"""Codex as an rlmcp Studio agent, through its local App Server.

Codex's App Server is the interface OpenAI uses for rich clients -- the VS
Code extension and the ChatGPT desktop app -- and offers for embedding Codex
in other products (docs/providers.md). It keeps a thread alive, streams items
as they start and complete, requests approvals over JSON-RPC, and reads MCP
servers from the normal Codex configuration. The studio gives each
conversation its own App Server process and supplies that process with the
run-pinned rlmcp server as command-line config overrides. Nothing is written
to the user's ``~/.codex/config.toml``; the servers *in* it are switched off
for the process (``enabled=false``), because Codex merges overrides onto the
file and a global ``rlmcp`` entry pointed at some other session would
otherwise ride along into every conversation, unannounced.

The protocol client below uses only JSONL and the standard library. The
shapes it speaks are those of the CLI's generated schema (``codex
app-server generate-json-schema``; written against codex 0.153) -- the
``item/*`` lifecycle, ``turn/*``, the ``error`` notification,
``thread/tokenUsage/updated``, and the five server requests
(``item/commandExecution/requestApproval``, ``item/fileChange/requestApproval``,
``item/permissions/requestApproval``, ``item/tool/requestUserInput``,
``mcpServer/elicitation/request``). An MCP tool approval arrives as an
elicitation whose ``_meta`` carries ``codex_approval_kind: mcp_tool_call`` and
(newer CLIs) ``tool_name``/``tool_params``; the answer is ``{"action":
"accept"}``, with ``_meta.persist = "session"`` for *allow always*, exactly
what Codex's own TUI sends.

Which ``codex``: the ChatGPT desktop app bundles a full binary
(``/usr/lib/chatgpt/resources/codex`` on Linux) that shares ``~/.codex`` with
the app's sign-in; a snap install on PATH is confined -- it cannot execute an
MCP server outside its own tree and its process cannot be signalled -- so the
desktop binary is preferred when both exist. ``$RLMCP_STUDIO_CODEX`` names one
explicitly.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from rlmcp_studio.agents import policy, preflight
from rlmcp_studio.agents.base import (
    AgentEvent, Asking, Decision, NotSupported, SessionSpec, ToolCall,
)
from rlmcp_studio.agents.claude import _rlmcp_server, _tool_list, system_prompt
from rlmcp_studio.agents.external import codex_home


NAME = "codex"
LABEL = "Codex"
REQUEST_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 5.0
CODEX_ENV = "RLMCP_STUDIO_CODEX"
"""Names the `codex` binary to drive, overriding the search below."""

#: Where the ChatGPT desktop app keeps its bundled CLI. Tried before PATH,
#: because the PATH one is often a confined snap (see the module docstring).
DESKTOP_CANDIDATES = (
    "/usr/lib/chatgpt/resources/codex",
    "/Applications/ChatGPT.app/Contents/Resources/codex",
    "~/Applications/ChatGPT.app/Contents/Resources/codex",
)

# App Server sends one JSON object per line. A tool result or a message with
# rich context can easily exceed asyncio's 64 KiB StreamReader default; the
# limit is a framing ceiling, not a desired event size.
APP_SERVER_LINE_LIMIT = 16 * 1024 * 1024

SERVER_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
"""An MCP server name goes into a `-c mcp_servers.<name>...` path; a name
with a dot or a quote in it would be a different override."""

MODELS = [
    {"id": "", "label": "Default", "note": "the model Codex is configured for"},
    {"id": "gpt-6-astra", "label": "GPT-6 Astra", "note": "most capable agent"},
    {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol", "note": "reasoning"},
    {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra", "note": "balanced agent"},
    {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna", "note": "fast agent"},
    {"id": "gpt-5.5", "label": "GPT-5.5", "note": "previous generation"},
]
"""A starting list (what codex 0.153's `model/list` answered); `model/list`
from the running App Server replaces it the first time a conversation starts
(`_live_models`), so the picker shows what this account can actually use."""

#: The studio's modes, in Codex's terms. `approval` is the thread's
#: AskForApproval, `sandbox` its mode at start and the policy sent with every
#: turn. Auto is `on-request` + workspace writes, with the studio accepting
#: whatever escalation the model asks for (a launch that writes outside the
#: working tree, a network fetch); Manual is `untrusted`, which asks before
#: any command Codex does not know to be safe; Plan runs in a read-only
#: sandbox and asks nobody, so a prompt is refused rather than raised.
MODES = [
    {"id": "auto-edit", "label": "Auto", "approval": "on-request", "sandbox": "workspace-write",
     "note": "runs in the working tree without asking, and gets any escalation it "
             "asks for; interrupt it to redirect"},
    {"id": "manual", "label": "Manual", "approval": "untrusted", "sandbox": "workspace-write",
     "note": "Codex asks before commands and file changes; rlmcp calls go through "
             "the studio's own table"},
    {"id": "plan", "label": "Plan", "approval": "never", "sandbox": "read-only",
     "note": "read-only; it can inspect and propose but cannot change files or a run"},
]
MODE_BY_ID = {mode["id"]: mode for mode in MODES}

HARNESSES = [
    {"id": "studio", "label": "Studio",
     "note": "the studio instructions, run context, and attached-tool status"},
    {"id": "context", "label": "Context only",
     "note": "Codex with this run's context and tool status, without studio instructions"},
    {"id": "plain", "label": "Plain",
     "note": "Codex in the working directory, with no Studio prompt"},
]
HARNESS_BY_ID = {harness["id"]: harness for harness in HARNESSES}

TURN_REASONS = {"completed": "end_turn", "interrupted": "interrupted",
                "failed": "error", "inProgress": "end_turn"}

#: `_meta` keys of a Codex tool approval, from codex-rs/protocol/src/mcp_approval_meta.rs.
META_KIND, META_KIND_TOOL_CALL = "codex_approval_kind", "mcp_tool_call"
META_TOOL_NAME, META_TOOL_PARAMS, META_PERSIST = "tool_name", "tool_params", "persist"


def _resolve_mode(mode: str) -> str:
  return mode if mode in MODE_BY_ID else "manual"


def _resolve_harness(harness: str) -> str:
  return harness if harness in HARNESS_BY_ID else "studio"


# ── which codex, and what it is configured with ─────────────────────────
def find_codex() -> str:
  """The `codex` to drive: `$RLMCP_STUDIO_CODEX`, the desktop app's bundled
  binary, then PATH. "" when none is found."""
  named = os.environ.get(CODEX_ENV, "").strip()
  if named:
    return named
  for candidate in DESKTOP_CANDIDATES:
    path = Path(candidate).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
      return str(path)
  return shutil.which("codex") or ""


def is_snap(command: str) -> bool:
  """A snap-confined codex: signals to it fail with EPERM and it cannot run
  a program outside its own tree, so the rlmcp server never starts."""
  try:
    real = os.path.realpath(command)
  except OSError:
    return False
  return real == "/usr/bin/snap" or real.startswith("/snap/")


def configured_servers(home: Optional[Path] = None) -> List[str]:
  """The MCP servers the user's own Codex config declares, read-only.

  Codex merges `-c` overrides onto `config.toml`, so anything in here starts
  with every thread unless the studio switches it off for its own processes.
  A file that will not parse yields [] -- Codex will complain about it in its
  own words when it starts."""
  path = (home or codex_home()) / "config.toml"
  try:
    with path.open("rb") as fh:
      data = tomllib.load(fh)
  except (OSError, tomllib.TOMLDecodeError):
    return []
  servers = data.get("mcp_servers")
  return sorted(str(name) for name in servers) if isinstance(servers, dict) else []


# ── payload helpers ──────────────────────────────────────────────────────
def _text(value: Any) -> str:
  """A JSON-RPC payload's human-readable text.

  Content blocks are the MCP shape: `{"type": "text", "text": ...}` is its
  text, and any other typed block -- an image, a resource -- is named rather
  than dumped, so a screenshot does not land in the transcript as base64."""
  if value is None:
    return ""
  if isinstance(value, str):
    return value
  if isinstance(value, list):
    return "\n".join(part for item in value if (part := _text(item)))
  if isinstance(value, dict):
    if isinstance(value.get("text"), str):
      return value["text"]
    kind = value.get("type")
    if isinstance(kind, str) and kind not in ("text",) and (
        "data" in value or "url" in value or "image_url" in value or kind == "image"):
      return f"[{kind}]"
    if "content" in value:
      return _text(value["content"])
    if "output" in value:
      return _text(value["output"])
    if "message" in value and isinstance(value["message"], str):
      return value["message"]
    return json.dumps(value, default=str, ensure_ascii=False)
  return str(value)


def _arguments(value: Any) -> Dict[str, Any]:
  return dict(value) if isinstance(value, dict) else ({} if value is None else {"value": value})


def _toml_list(values: List[str]) -> str:
  return "[" + ", ".join(json.dumps(str(v)) for v in values) + "]"


@dataclass
class _Conversation:
  spec: SessionSpec
  proc: asyncio.subprocess.Process
  thread_id: str = ""
  turn_id: str = ""
  pending: Dict[int, asyncio.Future] = field(default_factory=dict)
  items: Dict[str, Dict[str, Any]] = field(default_factory=dict)
  """Every item Codex has started and not completed, by id: the approval
  requests name an item id and the elicitation names only a server."""
  usage: Optional[Dict[str, Any]] = None
  reader: Optional[asyncio.Task] = None
  stderr_task: Optional[asyncio.Task] = None
  stderr_tail: str = ""
  tasks: Set[asyncio.Task] = field(default_factory=set)
  write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
  closed: bool = False


class CodexProvider(Asking):
  """One local Codex App Server process per Studio conversation."""

  name = NAME
  label = LABEL

  def __init__(self, model: str = "", mode: str = "manual", harness: str = "studio",
               codex_command: Optional[str] = None, mcp_command: Optional[str] = None):
    self.model = model if _model_ok(model) else ""
    self.mode = _resolve_mode(mode)
    self.harness = _resolve_harness(harness)
    self.codex_command = codex_command or find_codex() or "codex"
    self.mcp_command = mcp_command or _rlmcp_server()
    self.auth_state: Dict[str, Any] = {}
    self._live_models: List[Dict[str, str]] = []
    self._open: Dict[str, _Conversation] = {}
    self._given: Dict[str, Dict[str, Any]] = {}
    self._always: Dict[str, Set[str]] = {}
    self._on_event: Optional[Callable[[str, AgentEvent], Awaitable[None]]] = None
    self._init_asking()
    self._request_id = 0

  # ── availability and settings ───────────────────────────────────────
  def _binary(self) -> str:
    """The resolved command, or "" when there is nothing to run."""
    if Path(self.codex_command).is_file():
      return self.codex_command
    return shutil.which(self.codex_command) or ""

  def available(self) -> bool:
    return not self.unavailable_reason()

  def unavailable_reason(self) -> str:
    binary = self._binary()
    if not binary:
      return (f"the `codex` CLI was not found (looked at ${CODEX_ENV}, the ChatGPT "
              "desktop app's bundled binary, and PATH)")
    if is_snap(binary):
      return ("the only `codex` found is the snap, which is confined: it cannot run "
              "the rlmcp server or be stopped. Install the ChatGPT desktop app, or "
              f"`npm install -g @openai/codex`, or point ${CODEX_ENV} at a binary")
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_ACCESS_TOKEN"):
      return ""
    if self.auth_state.get("loggedIn"):
      return ""
    return self.auth_state.get("reason") or "not signed in to Codex"

  async def auth_status(self, force: bool = False) -> Dict[str, Any]:
    """Ask Codex rather than inferring authentication from a local file."""
    binary = self._binary()
    if not binary or is_snap(binary):
      return {"loggedIn": False, "authMethod": "none", "answered": True,
              "reason": self.unavailable_reason()}
    if os.environ.get("OPENAI_API_KEY"):
      return {"loggedIn": True, "authMethod": "api_key", "answered": True}
    if os.environ.get("CODEX_ACCESS_TOKEN"):
      return {"loggedIn": True, "authMethod": "access_token", "answered": True}
    try:
      proc = await asyncio.create_subprocess_exec(
          binary, "login", "status", stdout=asyncio.subprocess.PIPE,
          stderr=asyncio.subprocess.PIPE)
      out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
    except asyncio.TimeoutError:
      return {"loggedIn": False, "authMethod": "none", "answered": False,
              "reason": "`codex login status` timed out"}
    except Exception as exc:  # noqa: BLE001 - this is an availability message
      return {"loggedIn": False, "authMethod": "none", "answered": False,
              "reason": str(exc)}
    message = (out + err).decode(errors="replace").strip()
    logged_in = proc.returncode == 0 and "logged in" in message.lower() \
        and "not logged in" not in message.lower()
    return {
        "loggedIn": logged_in,
        "authMethod": "chatgpt" if logged_in else "none",
        "answered": True,
        "reason": "" if logged_in else (
            (message or "not signed in to Codex")
            + " -- run `codex login` in a terminal (Terminal in the top bar), "
              "then press Check again"),
    }

  def models(self) -> List[Dict[str, str]]:
    return list(self._live_models or MODELS)

  def settings(self) -> Dict[str, Any]:
    return {
        "provider": self.name,
        "models": self.models(),
        "model": self.model,
        "modes": [{k: v for k, v in m.items() if k not in ("approval", "sandbox")}
                  for m in MODES],
        "mode": self.mode,
        "harnesses": list(HARNESSES),
        "harness": self.harness,
        "command": self._binary() or self.codex_command,
    }

  def choose(self, model: Optional[str] = None, mode: Optional[str] = None,
             harness: Optional[str] = None) -> Dict[str, Any]:
    if model is not None:
      # Only what the picker offers: the static list until a conversation has
      # asked `model/list`, then what this account can actually use. (The
      # constructor is looser -- a settings file may name a model learned by
      # the previous process, and that id should survive the restart.)
      if model not in {row["id"] for row in self.models()}:
        raise ValueError(f"no such model: {model!r}")
      self.model = model
    if mode is not None:
      if mode not in MODE_BY_ID:
        raise ValueError(f"no such mode: {mode!r}")
      self.mode = mode
    if harness is not None:
      if harness not in HARNESS_BY_ID:
        raise ValueError(f"no such harness: {harness!r}")
      self.harness = harness
    return self.settings()

  async def adopt_live(self) -> int:
    """The mode reaches open conversations on their next turn: `turn/start`
    carries the approval policy and the sandbox policy for that turn and the
    ones after, and the approval handlers read `self.mode` per request."""
    return len(self._open)

  def on_event(self, callback: Callable[[str, AgentEvent], Awaitable[None]]) -> None:
    self._on_event = callback

  # ── the prompt ──────────────────────────────────────────────────────
  def briefing(self, spec: SessionSpec) -> Dict[str, Any]:
    report = preflight.check(spec, self.mcp_command)
    shape = HARNESS_BY_ID[self.harness]
    sections: List[Dict[str, str]] = []
    if self.harness == "studio":
      sections.append({
          "id": "instructions", "title": "The studio's instructions",
          "source": "instructions/*.md",
          "text": system_prompt({"tools_attached": report.prompt_block()}),
      })
    if self.harness in ("studio", "context"):
      sections.append({
          "id": "tools", "title": "The rlmcp tools, by name",
          "source": f"written from the permission table, for {self.mode} mode",
          "text": _tool_list(self.mode, report.attached).strip(),
      })
      if spec.system_context:
        sections.append({
            "id": "context", "title": "This run",
            "source": "built from the record by build_context",
            "text": spec.system_context,
        })
      if report.attached:
        sections.append({
            "id": "runtime", "title": "How Codex spells the tools",
            "source": "agents/codex.py",
            "text": ("You are running in Codex. The rlmcp tools are the tools of the "
                     "MCP server named `rlmcp`: where the text above says "
                     "`mcp__rlmcp__<tool>`, call `<tool>` on that server. Use them to "
                     "inspect or steer the run rather than guessing from files or memory."),
        })
    return {
        "harness": shape["id"], "label": shape["label"], "note": shape["note"],
        "preset": True, "sections": sections, "cwd": spec.cwd,
        "model": spec.model or self.model or "", "mode": self.mode,
        "mcp": sorted(spec.mcp_servers or {}), "tools_attached": report.attached,
        "settings": [],
        "runtime": ("Codex receives these sections as developer instructions "
                    "when the thread starts."),
    }

  def given(self, session_id: str) -> Optional[Dict[str, Any]]:
    return self._given.get(session_id)

  def granted(self, run_id: str) -> List[str]:
    return sorted(self._always.get(run_id, ()))

  # ── App Server lifecycle ────────────────────────────────────────────
  def _command(self, spec: SessionSpec) -> Tuple[List[str], Dict[str, str]]:
    """The argv and the *extra environment* of one conversation's process.

    Every spec server is added with `-c` overrides; every server the user's
    own config declares and the spec does not is switched off, so a thread
    sees exactly the spec's servers. A server's env goes into the App Server
    process's environment and is forwarded by name (`env_vars`) -- never as
    argv, where `/proc/*/cmdline` would show the studio's token to every
    process on the machine."""
    binary = self._binary() or self.codex_command
    command = [binary, "app-server"]
    extra_env: Dict[str, str] = {}
    wanted = dict(spec.mcp_servers or {})
    for name, server in wanted.items():
      if not SERVER_NAME.fullmatch(str(name)):
        raise NotSupported(f"MCP server name {name!r} is not a plain identifier")
      server = dict(server or {})
      server_command = str(server.get("command") or self.mcp_command)
      args = [str(arg) for arg in (server.get("args") or [])]
      command.extend(["-c", f"mcp_servers.{name}.command={json.dumps(server_command)}"])
      command.extend(["-c", f"mcp_servers.{name}.args={_toml_list(args)}"])
      command.extend(["-c", f"mcp_servers.{name}.enabled=true"])
      # An MCP approval prompt (`mcpServer/elicitation/request`) is where the
      # studio applies its own table in Manual and Plan; Auto never asks.
      approval = "approve" if self.mode == "auto-edit" else "prompt"
      command.extend(["-c", f"mcp_servers.{name}.default_tools_approval_mode={approval}"])
      env = {str(k): str(v) for k, v in dict(server.get("env") or {}).items()}
      if env:
        names = sorted(env)
        for key in names:
          if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise NotSupported(f"MCP server env name {key!r} is not a variable name")
        command.extend(["-c", f"mcp_servers.{name}.env_vars={_toml_list(names)}"])
        extra_env.update(env)
    for name in configured_servers():
      if name not in wanted and SERVER_NAME.fullmatch(name):
        command.extend(["-c", f"mcp_servers.{name}.enabled=false"])
    return command, extra_env

  def _thread_params(self, spec: SessionSpec, developer_instructions: str) -> Dict[str, Any]:
    mode = MODE_BY_ID[self.mode]
    params: Dict[str, Any] = {
        "cwd": spec.cwd or None,
        "model": spec.model or self.model or None,
        "sandbox": mode["sandbox"],
        "approvalPolicy": mode["approval"],
    }
    if developer_instructions:
      params["developerInstructions"] = developer_instructions
    return params

  def _turn_policy(self, spec: SessionSpec) -> Dict[str, Any]:
    """What every `turn/start` carries, so a mode changed in Settings reaches
    a conversation that is already open."""
    mode = MODE_BY_ID[self.mode]
    if mode["sandbox"] == "read-only":
      sandbox: Dict[str, Any] = {"type": "readOnly"}
    else:
      roots = [str(Path(p).expanduser()) for p in (spec.free_paths or []) if p]
      sandbox = {"type": "workspaceWrite", "writableRoots": roots, "networkAccess": True}
    return {"approvalPolicy": mode["approval"], "sandboxPolicy": sandbox}

  async def start(self, spec: SessionSpec) -> str:
    if spec.run_id in self._open:
      return spec.run_id
    if not self.available():
      raise NotSupported(self.unavailable_reason())

    brief = self.briefing(spec)
    developer_instructions = "\n\n".join(
        section["text"].strip() for section in brief["sections"]
        if section.get("text", "").strip())
    command, extra_env = self._command(spec)
    env = {**os.environ, **extra_env}
    try:
      proc = await asyncio.create_subprocess_exec(
          *command, cwd=spec.cwd or None, env=env, stdin=asyncio.subprocess.PIPE,
          stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
          limit=APP_SERVER_LINE_LIMIT)
    except Exception as exc:  # noqa: BLE001 - presented in the agent panel
      raise NotSupported(f"Codex App Server could not start: {exc}") from exc

    conv = _Conversation(spec=spec, proc=proc)
    self._open[spec.run_id] = conv
    conv.reader = asyncio.create_task(self._read(spec.run_id, conv),
                                      name=f"codex-app-server:{spec.run_id}")
    conv.stderr_task = asyncio.create_task(self._drain_stderr(conv),
                                           name=f"codex-stderr:{spec.run_id}")
    try:
      await self._request(conv, "initialize", {
          "clientInfo": {"name": "rlmcp-studio", "title": "rlmcp Studio", "version": "0.1"},
      })
      await self._notify(conv, "initialized", {})
      if not self._live_models:
        await self._learn_models(conv)
      params = self._thread_params(spec, developer_instructions)
      if spec.resume:
        try:
          result = await self._request(conv, "thread/resume",
                                       {"threadId": spec.resume, **params})
        except NotSupported as exc:
          # Said as an event first: the host's resume fallback listens for
          # the first error out of a resumed start and turns the next message
          # into a fresh thread. Then raised, so this start is not a success.
          await self._emit(spec.run_id, AgentEvent(
              "error", {"message": f"could not resume Codex thread "
                                   f"{spec.resume[:8]}…: {exc}"}))
          raise
      else:
        result = await self._request(conv, "thread/start", params)
      thread = dict(result.get("thread") or {})
      conv.thread_id = str(thread.get("id") or "")
      if not conv.thread_id:
        raise NotSupported("Codex App Server started a thread without an id")
    except Exception:
      await self.stop(spec.run_id)
      raise
    self._given[spec.run_id] = brief
    # The thread id is what `thread/resume` takes, and what the terminal's
    # `codex resume <id>` takes: the transcript keeps it for both.
    await self._emit(spec.run_id, AgentEvent("session", {
        "provider": self.name, "session_id": conv.thread_id,
        "cwd": str(thread.get("cwd") or spec.cwd or ""),
        "model": str(result.get("model") or ""),
    }))
    return spec.run_id

  async def _learn_models(self, conv: _Conversation) -> None:
    """`model/list` once per studio process: the ids this account can use,
    in the picker's shape. A failure keeps the static list."""
    try:
      answer = await self._request(conv, "model/list", {})
    except NotSupported:
      return
    rows: List[Dict[str, str]] = [dict(MODELS[0])]
    for model in answer.get("data") or []:
      if not isinstance(model, dict) or model.get("hidden"):
        continue
      ident = str(model.get("id") or model.get("model") or "")
      if not ident or not _model_ok(ident):
        continue
      rows.append({"id": ident, "label": str(model.get("displayName") or ident),
                   "note": str(model.get("description") or "")
                           + (" (default)" if model.get("isDefault") else "")})
    if len(rows) > 1:
      self._live_models = rows

  async def send(self, session_id: str, text: str,
                 images: Optional[List[bytes]] = None) -> None:
    conv = self._open.get(session_id)
    if conv is None or not conv.thread_id:
      raise NotSupported("no Codex session; call start() first")
    input_items: List[Dict[str, Any]] = [{"type": "text", "text": text}]
    for image in images or []:
      encoded = base64.b64encode(image).decode("ascii")
      input_items.append({"type": "image", "url": f"data:image/png;base64,{encoded}"})
    params = {"threadId": conv.thread_id, "input": input_items,
              **self._turn_policy(conv.spec)}
    if self.model or conv.spec.model:
      params["model"] = conv.spec.model or self.model
    result = await self._request(conv, "turn/start", params)
    # `turn/started` usually lands first; whichever is later wins the same id.
    conv.turn_id = str((result.get("turn") or {}).get("id") or conv.turn_id)

  async def interrupt(self, session_id: str) -> None:
    conv = self._open.get(session_id)
    if conv is None or not conv.turn_id:
      return
    await self._request(conv, "turn/interrupt", {
        "threadId": conv.thread_id, "turnId": conv.turn_id,
    })

  async def stop(self, session_id: str) -> None:
    conv = self._open.pop(session_id, None)
    self._given.pop(session_id, None)
    if conv is None:
      return
    conv.closed = True
    for request_id, future in list(conv.pending.items()):
      if not future.done():
        future.set_exception(NotSupported("Codex App Server stopped"))
      conv.pending.pop(request_id, None)
    for local_id, future in list(self._pending.items()):
      if local_id.startswith(session_id + ":") and not future.done():
        future.set_result(Decision.DENY)
    await _end_process(conv.proc)
    for task in (conv.reader, conv.stderr_task, *conv.tasks):
      if task is not None and not task.done():
        task.cancel()

  # ── JSON-RPC ─────────────────────────────────────────────────────────
  async def _write(self, conv: _Conversation, payload: Dict[str, Any]) -> None:
    if conv.proc.stdin is None or conv.proc.returncode is not None:
      raise NotSupported("Codex App Server is no longer running")
    async with conv.write_lock:
      conv.proc.stdin.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
      await conv.proc.stdin.drain()

  async def _notify(self, conv: _Conversation, method: str, params: Dict[str, Any]) -> None:
    await self._write(conv, {"method": method, "params": params})

  async def _request(self, conv: _Conversation, method: str,
                     params: Dict[str, Any]) -> Dict[str, Any]:
    self._request_id += 1
    request_id = self._request_id
    future = asyncio.get_running_loop().create_future()
    conv.pending[request_id] = future
    try:
      await self._write(conv, {"id": request_id, "method": method, "params": params})
      return await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
      raise NotSupported(f"Codex App Server did not answer {method} in time"
                         + (f" ({conv.stderr_tail.strip()[-200:]})" if conv.stderr_tail else "")
                         ) from exc
    finally:
      conv.pending.pop(request_id, None)

  async def _reply(self, conv: _Conversation, request_id: Any,
                   result: Optional[Dict[str, Any]] = None,
                   error: str = "") -> None:
    payload: Dict[str, Any] = {"id": request_id}
    if error:
      payload["error"] = {"code": -32000, "message": error}
    else:
      payload["result"] = result if result is not None else {}
    try:
      await self._write(conv, payload)
    except NotSupported:
      pass  # the process is gone; there is nobody to answer

  async def _drain_stderr(self, conv: _Conversation) -> None:
    """Keep the last bit of stderr: a pipe nobody reads eventually blocks the
    process, and its last sentence is the best explanation of a silent exit."""
    if conv.proc.stderr is None:
      return
    try:
      while True:
        chunk = await conv.proc.stderr.read(4096)
        if not chunk:
          return
        conv.stderr_tail = (conv.stderr_tail + chunk.decode(errors="replace"))[-2000:]
    except asyncio.CancelledError:
      raise
    except Exception:  # noqa: BLE001 - a diagnostic, not the conversation
      return

  async def _read(self, session_id: str, conv: _Conversation) -> None:
    """Dispatch every response, notification and server request from Codex."""
    reason = "Codex App Server stopped"
    try:
      if conv.proc.stdout is None:
        raise NotSupported("Codex App Server has no stdout stream")
      while True:
        raw = await conv.proc.stdout.readline()
        if not raw:
          break
        try:
          message = json.loads(raw)
        except json.JSONDecodeError:
          continue
        if not isinstance(message, dict):
          continue
        if "method" not in message and "id" in message:
          future = conv.pending.get(message["id"])
          if future is None or future.done():
            continue
          if "error" in message:
            error = message.get("error") or {}
            future.set_exception(NotSupported(str(error.get("message") or error)))
          else:
            result = message.get("result")
            future.set_result(dict(result) if isinstance(result, dict) else {"value": result})
          continue
        method = str(message.get("method") or "")
        if not method:
          continue
        if "id" in message:
          task = asyncio.create_task(self._server_request(session_id, conv, message))
          conv.tasks.add(task)
          task.add_done_callback(conv.tasks.discard)
        else:
          await self._notification(session_id, conv, method,
                                   dict(message.get("params") or {}))
    except asyncio.CancelledError:
      raise
    except Exception as exc:  # noqa: BLE001 - surfaced in the conversation
      reason = str(exc)
    finally:
      for future in list(conv.pending.values()):
        if not future.done():
          future.set_exception(NotSupported(reason))
      if not conv.closed and self._open.get(session_id) is conv:
        tail = conv.stderr_tail.strip()
        if conv.proc.returncode is not None:
          reason = f"Codex App Server exited ({conv.proc.returncode})"
        if tail:
          reason += f": {tail[-300:]}"
        await self._emit(session_id, AgentEvent("error", {"message": reason}))
        await self._emit(session_id, AgentEvent("done", {"reason": "error"}))

  # ── notifications ────────────────────────────────────────────────────
  async def _notification(self, session_id: str, conv: _Conversation, method: str,
                          params: Dict[str, Any]) -> None:
    if method == "item/started":
      item = dict(params.get("item") or {})
      if item.get("id"):
        conv.items[str(item["id"])] = item
      call = self._call_event(item)
      if call is not None:
        await self._emit(session_id, call)
      return
    if method == "item/completed":
      item = dict(params.get("item") or {})
      started = conv.items.pop(str(item.get("id") or ""), None)
      if started is not None:
        # The completed item is authoritative; fields the start had and the
        # completion lacks (a fileChange's `changes`) are kept.
        item = {**started, **item}
      for event in self._completed_events(item):
        await self._emit(session_id, event)
      return
    if method == "turn/started":
      conv.turn_id = str((params.get("turn") or {}).get("id") or conv.turn_id)
      return
    if method == "turn/completed":
      turn = dict(params.get("turn") or {})
      conv.turn_id = ""
      conv.items.clear()
      status = str(turn.get("status") or "completed")
      if turn.get("error"):
        await self._emit(session_id, AgentEvent("error", {"message": _text(turn["error"])}))
      usage, conv.usage = conv.usage, None
      await self._emit(session_id, AgentEvent("done", {
          "reason": TURN_REASONS.get(status, "end_turn"), "usage": usage,
      }))
      return
    if method == "thread/tokenUsage/updated":
      conv.usage = _usage(dict(params.get("tokenUsage") or {}))
      return
    if method == "error":
      if params.get("willRetry"):
        return
      await self._emit(session_id, AgentEvent("error", {"message": _text(params.get("error"))}))
      return
    if method == "mcpServer/startupStatus/updated":
      if str(params.get("status") or "") == "failed":
        await self._emit(session_id, AgentEvent("error", {
            "message": f"MCP server `{params.get('name')}` failed to start"
                       + (f": {params.get('error')}" if params.get("error") else "")}))
      return
    if method == "codex/event/mcp_startup_update":       # older CLIs
      msg = dict(params.get("msg") or {})
      status = dict(msg.get("status") or {})
      if status.get("state") == "failed":
        await self._emit(session_id, AgentEvent("error", {
            "message": f"MCP server `{msg.get('server')}` failed to start: "
                       f"{status.get('error') or 'no reason given'}"}))
      return
    if method == "thread/closed" and not conv.closed:
      await self._emit(session_id, AgentEvent("error", {"message": "Codex closed the thread"}))
      await self._emit(session_id, AgentEvent("done", {"reason": "error"}))

  async def _emit(self, session_id: str, event: AgentEvent) -> None:
    if self._on_event is not None:
      await self._on_event(session_id, event)

  def _call_event(self, item: Dict[str, Any]) -> Optional[AgentEvent]:
    """The `tool_call` for an item Codex has started, if it is a tool."""
    kind = str(item.get("type") or "")
    item_id = str(item.get("id") or "")
    if kind == "mcpToolCall":
      name, args = _mcp_name(item), _arguments(item.get("arguments"))
      return AgentEvent("tool_call", {"id": item_id, "name": name, "args": args,
                                      "policy": policy.verdict(name, args, mode=self.mode)})
    if kind == "commandExecution":
      args = {"command": item.get("command", ""), "cwd": item.get("cwd", "")}
      return AgentEvent("tool_call", {"id": item_id, "name": "Bash", "args": args,
                                      "policy": policy.verdict("Bash", args, mode=self.mode)})
    if kind == "fileChange":
      args = _change_args(item)
      return AgentEvent("tool_call", {"id": item_id, "name": "Edit", "args": args,
                                      "policy": policy.verdict("Edit", args, mode=self.mode)})
    return None

  def _completed_events(self, item: Dict[str, Any]) -> List[AgentEvent]:
    kind = str(item.get("type") or "")
    item_id = str(item.get("id") or "")
    if kind == "agentMessage":
      return [AgentEvent("text", {"text": str(item.get("text") or "")})]
    if kind == "reasoning":
      text = _text(item.get("summary") or item.get("content"))
      return [AgentEvent("thinking", {"text": text})] if text else []
    if kind == "mcpToolCall":
      error = item.get("error")
      return [AgentEvent("tool_result", {
          "id": item_id, "content": _text(item.get("result")),
          "error": bool(error) or str(item.get("status") or "") == "failed",
          "message": _text(error)})]
    if kind == "commandExecution":
      failed = str(item.get("status") or "") in ("failed", "declined")
      return [AgentEvent("tool_result", {
          "id": item_id, "content": _text(item.get("aggregatedOutput")), "error": failed,
          "message": "declined" if item.get("status") == "declined" else ""})]
    if kind == "fileChange":
      failed = str(item.get("status") or "") in ("failed", "declined")
      return [AgentEvent("tool_result", {
          "id": item_id, "content": _diffs(item), "error": failed,
          "message": "declined" if item.get("status") == "declined" else ""})]
    return []

  # ── approvals ────────────────────────────────────────────────────────
  async def _server_request(self, session_id: str, conv: _Conversation,
                            message: Dict[str, Any]) -> None:
    method = str(message.get("method") or "")
    params = dict(message.get("params") or {})
    request_id = message.get("id")
    try:
      if method == "mcpServer/elicitation/request":
        await self._mcp_elicitation(session_id, conv, request_id, params)
      elif method in ("item/commandExecution/requestApproval", "execCommandApproval"):
        args = {"command": params.get("command") or "", "cwd": params.get("cwd") or "",
                "reason": params.get("reason") or ""}
        decision = await self._decide_tool(session_id, conv, "Bash", args,
                                           str(params.get("itemId") or ""),
                                           str(params.get("reason") or ""))
        await self._reply(conv, request_id, _decision_payload(decision, method))
      elif method in ("item/fileChange/requestApproval", "applyPatchApproval"):
        started = conv.items.get(str(params.get("itemId") or ""), {})
        args = _change_args(started)
        if params.get("grantRoot"):
          args["grant_root"] = params["grantRoot"]
        if params.get("reason"):
          args["reason"] = params["reason"]
        decision = await self._decide_tool(session_id, conv, "Edit", args,
                                           str(params.get("itemId") or ""),
                                           str(params.get("reason") or ""))
        await self._reply(conv, request_id, _decision_payload(decision, method))
      elif method == "item/permissions/requestApproval":
        await self._permissions(session_id, conv, request_id, params)
      elif method == "item/tool/requestUserInput":
        # The panel has no form for a tool's questions yet; the questions are
        # shown as text so the person can answer in the next message, and
        # the tool gets no answers rather than a failed turn.
        questions = [str(q.get("question") or "") for q in params.get("questions") or []
                     if isinstance(q, dict)]
        await self._emit(session_id, AgentEvent("text", {
            "text": "Codex asked, and the studio cannot answer a form yet -- reply in "
                    "your next message:\n" + "\n".join(f"- {q}" for q in questions)}))
        await self._reply(conv, request_id, {"answers": {}})
      else:
        await self._reply(conv, request_id, error=f"rlmcp Studio does not answer {method}")
    except asyncio.CancelledError:
      raise
    except Exception as exc:  # noqa: BLE001 - one request, not the conversation
      await self._reply(conv, request_id, error=f"rlmcp Studio could not answer {method}: {exc}")

  async def _decide_tool(self, session_id: str, conv: _Conversation, name: str,
                         args: Dict[str, Any], item_id: str, reason: str) -> Decision:
    """The studio's answer to Codex asking about a command or an edit.

    Auto accepts (that is what the mode means, and Codex only asks in Auto
    for an escalation the model requested); Plan declines; Manual consults
    the same table Claude's conversations do, so an edit inside a task being
    built is free and everything else reaches the panel."""
    if self.mode == "auto-edit":
      return Decision.ALLOW
    if self.mode == "plan":
      return Decision.DENY
    if name in self._always.get(session_id, ()):
      return Decision.ALLOW
    verdict = policy.verdict(name, args, free_paths=conv.spec.free_paths, mode=self.mode)
    if verdict == "allow":
      return Decision.ALLOW
    decision = await self._ask(session_id, ToolCall(name=name, args=args, id=item_id), reason)
    if decision == Decision.ALLOW_ALWAYS:
      self._always.setdefault(session_id, set()).add(name)
    return decision

  async def _mcp_elicitation(self, session_id: str, conv: _Conversation,
                             request_id: Any, params: Dict[str, Any]) -> None:
    """Codex asking about an MCP tool call, answered from the studio's table.

    A tool approval is a form elicitation whose `_meta` says
    `codex_approval_kind: mcp_tool_call`; newer CLIs name the tool and its
    arguments there, older ones only the server, so the newest in-progress
    call on that server is the fallback (Codex runs a thread's tool calls one
    at a time). Anything else -- a URL sign-in, a real form -- is declined
    with a note, because the panel cannot render it yet."""
    meta = dict(params.get("_meta") or {})
    server = str(params.get("serverName") or "")
    if str(params.get("mode") or "form") != "form" or meta.get(META_KIND) != META_KIND_TOOL_CALL:
      what = params.get("url") or params.get("message") or "an elicitation"
      await self._emit(session_id, AgentEvent("text", {
          "text": f"MCP server `{server}` asked for something the studio cannot relay "
                  f"({what}); declined."}))
      await self._reply(conv, request_id, {"action": "decline"})
      return
    tool = str(meta.get(META_TOOL_NAME) or "")
    args = _arguments(meta.get(META_TOOL_PARAMS)) if META_TOOL_PARAMS in meta else {}
    if not tool:
      started = next((item for item in reversed(list(conv.items.values()))
                      if item.get("type") == "mcpToolCall"
                      and str(item.get("server") or "") == server
                      and str(item.get("status") or "inProgress") == "inProgress"), {})
      tool = str(started.get("tool") or "")
      if not args:
        args = _arguments(started.get("arguments"))
    if not tool:
      match = re.search(r'tool ["“]([^"”]+)', str(params.get("message") or ""))
      tool = match.group(1) if match else "unknown"
    name = _mcp_name({"server": server, "tool": tool})
    if self.mode == "plan":
      decision = Decision.DENY
    elif self.mode == "auto-edit" or name in self._always.get(session_id, ()):
      decision = Decision.ALLOW
    elif policy.verdict(name, args, free_paths=conv.spec.free_paths, mode=self.mode) == "allow":
      decision = Decision.ALLOW
    else:
      decision = await self._ask(session_id, ToolCall(name=name, args=args),
                                 str(params.get("message") or ""))
      if decision == Decision.ALLOW_ALWAYS:
        self._always.setdefault(session_id, set()).add(name)
    if decision == Decision.DENY:
      await self._reply(conv, request_id, {"action": "decline"})
      return
    answer: Dict[str, Any] = {"action": "accept"}
    if decision == Decision.ALLOW_ALWAYS and _persist_supported(meta, "session"):
      answer["_meta"] = {META_PERSIST: "session"}
    await self._reply(conv, request_id, answer)

  async def _permissions(self, session_id: str, conv: _Conversation,
                         request_id: Any, params: Dict[str, Any]) -> None:
    """Codex asking for more sandbox room (paths, network) for a turn."""
    asked = dict(params.get("permissions") or {})
    if self.mode == "auto-edit":
      granted = asked
    elif self.mode == "plan":
      granted = {}
    else:
      args = {"permissions": asked, "reason": params.get("reason") or ""}
      decision = await self._ask(session_id, ToolCall(name="Permissions", args=args,
                                                      id=str(params.get("itemId") or "")),
                                 str(params.get("reason") or ""))
      granted = asked if decision in (Decision.ALLOW, Decision.ALLOW_ALWAYS) else {}
    await self._reply(conv, request_id, {"permissions": granted, "scope": "turn"})


# ── helpers ───────────────────────────────────────────────────────────
def _model_ok(model: str) -> bool:
  return bool(model) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", model) is not None


def _mcp_name(item: Dict[str, Any]) -> str:
  server, tool = str(item.get("server") or ""), str(item.get("tool") or "")
  return f"mcp__{server}__{tool}" if server else tool


def _change_args(item: Dict[str, Any]) -> Dict[str, Any]:
  changes = [c for c in (item.get("changes") or []) if isinstance(c, dict)]
  paths = [str(c.get("path") or "") for c in changes if c.get("path")]
  return {"path": paths[0] if paths else "", "paths": paths,
          "changes": [{"path": c.get("path", ""), "kind": c.get("kind", "")} for c in changes]}


def _diffs(item: Dict[str, Any]) -> str:
  out = []
  for change in item.get("changes") or []:
    if not isinstance(change, dict):
      continue
    kind = change.get("kind")
    kind = kind if isinstance(kind, str) else json.dumps(kind, default=str)
    out.append(f"{kind} {change.get('path', '')}\n{change.get('diff', '')}".rstrip())
  return "\n".join(out)


def _usage(token_usage: Dict[str, Any]) -> Optional[Dict[str, Any]]:
  """`thread/tokenUsage/updated` carries the last turn and the total; the
  panel's number is this turn's."""
  last = dict(token_usage.get("last") or {})
  if not last:
    return None
  return {"used": last.get("totalTokens"), "input": last.get("inputTokens"),
          "output": last.get("outputTokens"), "cost_usd": None}


def _persist_supported(meta: Dict[str, Any], mode: str) -> bool:
  persist = meta.get(META_PERSIST)
  if isinstance(persist, str):
    return persist == mode
  if isinstance(persist, list):
    return mode in [str(p) for p in persist]
  return False


def _decision_payload(decision: Decision, method: str) -> Dict[str, Any]:
  if method in ("execCommandApproval", "applyPatchApproval"):       # v1 spellings
    legacy = {Decision.ALLOW: "approved", Decision.ALLOW_ALWAYS: "approved_for_session",
              Decision.DENY: "denied"}
    return {"decision": legacy[decision]}
  current = {Decision.ALLOW: "accept", Decision.ALLOW_ALWAYS: "acceptForSession",
             Decision.DENY: "decline"}
  return {"decision": current[decision]}


async def _end_process(proc: asyncio.subprocess.Process) -> None:
  """Stop an App Server: EOF on stdin is its own shutdown, and it is the only
  one a confined binary allows -- `terminate()` on a snap raises EPERM, and a
  stop that raised would leave the process running. Signals are the
  fallback."""
  if proc.returncode is not None:
    return
  try:
    if proc.stdin is not None and not proc.stdin.is_closing():
      proc.stdin.close()
  except Exception:  # noqa: BLE001 - closing what may already be closed
    pass
  try:
    await asyncio.wait_for(proc.wait(), timeout=STOP_TIMEOUT_S)
    return
  except asyncio.TimeoutError:
    pass
  for signal_it in (proc.terminate, proc.kill):
    try:
      signal_it()
    except (ProcessLookupError, PermissionError):
      return
    try:
      await asyncio.wait_for(proc.wait(), timeout=STOP_TIMEOUT_S)
      return
    except asyncio.TimeoutError:
      continue
