"""The Antigravity provider: Google's agent runtime, in the studio's process.

A second implementation of the provider interface in `base.py`. It is built
on the Antigravity Python SDK (`google-antigravity`), which ships the
same Go harness that drives Antigravity 2.0 and the `agy` CLI: one `Agent` per
conversation, kept open, with rl-mcp's MCP server attached exactly as it is for
Claude. Nothing here knows what a reward weight is either.

Three things about the shape, because they are where it differs from Claude:

**Steps, not messages.** The harness streams *steps* -- a text response, a
thought, a tool call -- each updated from ACTIVE to DONE. The studio's events
are whole blocks, so a step is translated once, when it is done, and the
deltas on the way are ignored. A pane that drew every delta as a bubble would
show a sentence as forty entries.

**Tool results arrive through a hook, not a step.** The SDK's `Step` carries a
tool call's arguments and nothing of its answer, so the pane would show every
call unanswered -- the same gap the Claude provider closes by reading tool
results out of user messages. `post_tool_call` is the SDK's own seam for it, and
it fires for the harness's built-in tools and for MCP tools alike.

**Permissions are a policy, and the policy is ours.** One `ask_user("*")`
whose handler consults the studio's table (`policy.py`) per call, so the mode
moves the line here the way it does for Claude, and a prompt in the panel is
the same prompt whichever provider raised it.

**One way in.** The SDK authenticates with `GEMINI_API_KEY` or Vertex
credentials and nothing else, read from the environment; no button, and
`Check again` looks again. Signing in through the `agy` CLI with a Google
account (the way a Google AI Pro or Ultra subscription is used) is left out
on purpose: Google's Antigravity terms call third-party tools on the Service a
breach and Google disables accounts for it (docs/providers.md). The `auth`
setting and its `signins` list are one entry long, and keep the shape the
panel and the routes expect.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Set

from rlmcp_studio.agents import policy, preflight
from rlmcp_studio.agents.base import (
    AgentEvent, Asking, Decision, NotSupported, SessionSpec, ToolCall,
)
# The vocabulary is shared on purpose: a mode is a studio preference, not a
# provider's, and the panel draws both providers' menus from one shape.
from rlmcp_studio.agents.claude import (
    ALIASES, HARNESSES, HARNESS_BY_ID, MODES, MODE_BY_ID, _rlmcp_server,
    _tool_list, resolve_harness, resolve_mode, system_prompt,
)

try:
  from google.antigravity import (
      Agent, AgentBehavior, BuiltinTools, CapabilitiesConfig, Image as AgImage,
      LocalAgentConfig, SystemInstructionSection, TemplatedSystemInstructions,
  )
  from google.antigravity import types as ag_types
  from google.antigravity.hooks import hooks as ag_hooks
  from google.antigravity.hooks import policy as ag_policy
  SDK_IMPORT_ERROR = ""
except Exception as exc:                      # the studio must start without it
  SDK_IMPORT_ERROR = str(exc)
  Agent = None                                # type: ignore[assignment]
  ag_types = None                             # type: ignore[assignment]


NAME = "antigravity"
LABEL = "Antigravity"

INSTALL_HINT = ("the google-antigravity package is not installed in this "
                "studio's environment; it is the `antigravity` extra: "
                "`uv tool install \"rl-mcp-studio[antigravity]\"` (or "
                "`uv sync --extra antigravity` in a checkout), then restart")

NO_KEY = ("no Gemini API key: set GEMINI_API_KEY (or GOOGLE_GENAI_USE_VERTEXAI=1 "
          "with `gcloud auth application-default login`) in the environment "
          "the studio starts in, restart it, then press Check again")

#: How the provider signs in. The id is what settings and routes use; the
#: label and note are what the panel and Settings draw. One entry: the
#: Google-account way in is not in this tree (see the module docstring).
AUTHS = [
    {"id": "key", "label": "API key",
     "note": "the Python SDK with GEMINI_API_KEY or Vertex credentials -- "
             "billed per token"},
]
AUTH_BY_ID = {a["id"]: a for a in AUTHS}

#: The models this provider can be asked for. Same shape as Claude's table --
#: id, label, note -- so the panel needs no second renderer. The empty id is
#: the SDK's own default, which is the one entry guaranteed to exist.
MODELS = [
    {"id": "", "label": "Default", "note": "whatever the SDK is configured for "
                                          "(gemini-3.8-flash at 0.1.16)"},
    {"id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash", "note": "fast"},
    {"id": "gemini-3.1-pro", "label": "Gemini 3.1 Pro", "note": "most capable"},
]

#: How the harness's built-in tools are called in the studio's permission
#: table, which is written in Claude Code's names. Judged by *effect*, which is
#: what the table is about: `run_command` is `Bash` because a command line is
#: a command line whoever runs it.
BUILTIN_TO_STUDIO = {
    "run_command": "Bash",
    "view_file": "Read",
    "list_directory": "Glob",
    "find_file": "Glob",
    "search_directory": "Grep",
    "edit_file": "Edit",
    "create_file": "Write",
    "search_web": "WebSearch",
    "read_url_content": "WebFetch",
    "invoke_subagent": "Task",
    "generate_image": "GenerateImage",
    "finish": "finish",
}

#: The harness ends every turn by calling `finish`. It is not a tool in any
#: sense the table cares about, and asking about it would be a prompt at the
#: end of every turn.
ALWAYS = {"finish"}

#: How the studio names the MCP server it attaches, on both providers.
MCP_NAME = "rlmcp"


def _v(value: Any) -> str:
  """An enum's value, or the string it already is."""
  return str(getattr(value, "value", value) or "")


def studio_name(call: Any) -> str:
  """The name the studio's table and pane use for this tool call.

  MCP tools arrive as `(server_name, name)` and become `mcp__<server>__<name>`,
  which is what `policy.verdict` matches and what the pane strips back off.
  Built-ins go through the table above; anything unknown keeps its name and
  therefore asks, which is the table's rule for a tool it has not heard of.
  """
  name = _v(getattr(call, "name", ""))
  server = getattr(call, "server_name", None)
  if server:
    return f"mcp__{server}__{name}"
  return BUILTIN_TO_STUDIO.get(name, name)


def _args_of(call: Any) -> Dict[str, Any]:
  """The call's arguments, with the path the SDK normalised put where the
  studio's scope check looks for it."""
  args = dict(getattr(call, "args", None) or {})
  canonical = getattr(call, "canonical_path", None)
  if canonical and not args.get("file_path"):
    args["file_path"] = canonical
  return args


def _flatten(value: Any) -> str:
  """A tool's answer as text the pane can show."""
  if value is None:
    return ""
  if isinstance(value, str):
    return value
  dump = getattr(value, "model_dump", None)
  if callable(dump):
    try:
      value = dump(mode="json")
    except Exception:                         # noqa: BLE001 - show what we have
      value = str(value)
  if isinstance(value, dict):
    # The harness wraps most answers in one field, and the pane reads a
    # bare string better than `{"output": ...}` around it.
    for key in ("output", "text", "content", "result", "summary"):
      if isinstance(value.get(key), str) and len(value) == 1:
        return value[key]
    import json
    try:
      return json.dumps(value, ensure_ascii=False, indent=1)
    except (TypeError, ValueError):
      return str(value)
  if isinstance(value, list):
    return "\n".join(_flatten(v) for v in value)
  return str(value)


def translate(step: Any, seen: Set[str]) -> List[AgentEvent]:
  """One SDK step, as the studio's events. Pure, and duck-typed on purpose.

  ``seen`` is per session: the harness reports a step several times on its
  way from ACTIVE to DONE, and a tool call is announced on the first sighting
  and its text on the last. Without the set, every sentence would be two
  bubbles.
  """
  out: List[AgentEvent] = []
  kind = _v(getattr(step, "type", ""))
  status = _v(getattr(step, "status", ""))
  target = _v(getattr(step, "target", ""))
  source = _v(getattr(step, "source", ""))
  step_id = str(getattr(step, "id", "") or "")

  # The harness plays the person's own message back as a step of its own.
  # The host wrote it to the transcript before sending, so drawn again here
  # it would read as the agent repeating the question -- the same echo the Claude
  # provider drops, arriving in a different shape.
  if source == "USER":
    return out

  for call in getattr(step, "tool_calls", None) or []:
    call_id = str(getattr(call, "step_id", None) or getattr(call, "id", None)
                  or step_id)
    if call_id in seen:
      continue
    seen.add(call_id)
    name = studio_name(call)
    out.append(AgentEvent("tool_call", {
        "id": call_id, "name": name, "args": _args_of(call),
        "policy": policy.verdict(name),
    }))

  error = str(getattr(step, "error", "") or "")
  if status == "ERROR" and error and ("err:" + step_id) not in seen:
    seen.add("err:" + step_id)
    out.append(AgentEvent("error", {"message": error}))

  if status != "DONE" or ("done:" + step_id) in seen:
    return out
  seen.add("done:" + step_id)

  thinking = str(getattr(step, "thinking", "") or "")
  content = str(getattr(step, "content", "") or "")
  if kind == "THINKING" and thinking.strip():
    out.append(AgentEvent("thinking", {"text": thinking}))
  elif kind == "TEXT_RESPONSE" and content.strip():
    # What the model says to the environment -- a plan it writes for itself
    # between tool calls -- is a thought as far as a reader is concerned; what
    # it says to the user is the reply.
    if target == "TARGET_ENVIRONMENT":
      out.append(AgentEvent("thinking", {"text": content}))
    else:
      out.append(AgentEvent("text", {"text": content}))
  return out


def usage_of(usage: Any) -> Optional[Dict[str, Any]]:
  """The SDK's `UsageMetadata`, in the shape `done` carries."""
  if usage is None:
    return None
  prompt = int(getattr(usage, "prompt_token_count", None) or 0)
  answer = int(getattr(usage, "candidates_token_count", None) or 0)
  thought = int(getattr(usage, "thoughts_token_count", None) or 0)
  total = int(getattr(usage, "total_token_count", None) or 0) or (prompt + answer + thought)
  if not total:
    return None
  return {"used": total, "input": prompt, "output": answer + thought,
          "cost_usd": None}


def workspaces_for(spec: SessionSpec, extra: Iterable[str] = ()) -> List[str]:
  """Where the harness's file tools may reach.

  The SDK confines file tools to its workspaces, and a conversation about a
  run has to read outside the task repository: the session directory, the
  records, and rl-mcp's own documentation that the harness points at. Every
  existing directory named by the spec is a workspace; the cwd is first.
  """
  from rlmcp_studio.agents import harness
  roots: List[str] = []
  candidates = [spec.cwd or os.getcwd()]
  rlmcp = dict((spec.mcp_servers or {}).get(MCP_NAME) or {})
  args = [str(a) for a in (rlmcp.get("args") or [])]
  for flag in ("--session", "--records-root"):
    if flag in args and args.index(flag) + 1 < len(args):
      candidates.append(args[args.index(flag) + 1])
  candidates += list(spec.free_paths or [])
  candidates += list(extra)
  docs = harness.rlmcp_root()
  if docs is not None:
    candidates.append(str(docs))
  for raw in candidates:
    try:
      path = Path(raw).expanduser().resolve()
    except OSError:
      continue
    if path.is_dir() and str(path) not in roots:
      roots.append(str(path))
  return roots or [os.getcwd()]


class _Open:
  """One conversation the SDK holds open."""

  def __init__(self, agent: Any, stack: Any, spec: SessionSpec):
    self.agent = agent
    self.stack = stack
    self.spec = spec
    self.reader: Optional[asyncio.Task] = None
    self.cancelled = False
    """An interrupt was asked for on the turn in flight, so the error the
    harness answers a halt with is the answer we wanted and not a failure."""
    self.seen: Set[str] = set()


class AntigravityProvider(Asking):
  """The Antigravity SDK, hosted in this process."""

  name = NAME
  label = LABEL

  def __init__(self, model: str = "", mcp_command: Optional[str] = None,
               mode: str = "manual", harness: str = "studio",
               auth: str = "key"):
    self.model = model
    self.mode = resolve_mode(mode)
    self.harness = resolve_harness(harness)
    self.auth = auth if auth in AUTH_BY_ID else "key"
    """Which way in. Only `key` (the SDK) is offered; an unknown value, such
    as `account` in an older settings file, falls back to it rather than
    failing the settings file."""
    self.mcp_command = mcp_command or _rlmcp_server()
    self.auth_state: Dict[str, Any] = {}
    self._open: Dict[str, Any] = {}
    self._init_asking()
    self._always: Dict[str, set] = {}
    self._given: Dict[str, Dict[str, Any]] = {}
    self._on_event: Optional[Callable[[str, AgentEvent], Awaitable[None]]] = None

  # ── availability ─────────────────────────────────────────────────────
  async def auth_status(self, force: bool = False) -> Dict[str, Any]:
    """Whether the way in would work, and why not.

    Reads the environment, which is the exact check the SDK makes, in a
    microsecond; `force` is accepted for the protocol's sake and changes
    nothing here.
    """
    if Agent is None:
      return {"loggedIn": False, "authMethod": "none", "answered": True,
              "reason": INSTALL_HINT}
    if os.environ.get("GEMINI_API_KEY"):
      return {"loggedIn": True, "authMethod": "api_key", "answered": True}
    vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true")
    if vertex and (os.environ.get("GOOGLE_CLOUD_PROJECT")
                   or os.environ.get("GOOGLE_API_KEY")):
      return {"loggedIn": True, "authMethod": "vertex", "answered": True}
    return {"loggedIn": False, "authMethod": "none", "answered": True,
            "reason": NO_KEY}

  def available(self) -> bool:
    return not self.unavailable_reason()

  def unavailable_reason(self) -> str:
    if Agent is None:
      return INSTALL_HINT
    if self.auth_state.get("loggedIn"):
      return ""
    return self.auth_state.get("reason") or NO_KEY

  def models(self) -> List[Dict[str, str]]:
    """The models the SDK offers: the table above."""
    return list(MODELS)

  def modes(self) -> List[Dict[str, str]]:
    """The modes, in the shared vocabulary; the SDK asks through the studio's
    own permission handler, so every note means here what it means for Claude."""
    return [{k: v for k, v in m.items() if k != "sdk"} for m in MODES]

  # ── settings ─────────────────────────────────────────────────────────
  def settings(self) -> Dict[str, Any]:
    return {
        "provider": self.name,
        "models": self.models(),
        "model": self.model,
        "modes": self.modes(),
        "mode": self.mode,
        "harnesses": [{k: h[k] for k in ("id", "label", "note")} for h in HARNESSES],
        "harness": self.harness,
        # `signin`, not `auth`: `auth` in the status payload is the sign-in
        # *state* (`auth_state`), and the setting must not paint over it.
        "signin": self.auth,
        "signins": list(AUTHS),
    }

  def choose(self, model: Optional[str] = None, mode: Optional[str] = None,
             harness: Optional[str] = None,
             auth: Optional[str] = None) -> Dict[str, Any]:
    if auth is not None:
      if auth not in AUTH_BY_ID:
        raise ValueError(f"no such sign-in: {auth!r}")
    if model is not None:
      if model not in {m["id"] for m in self.models()}:
        raise ValueError(f"no such model: {model!r}")
      self.model = model
    if mode is not None:
      if ALIASES.get(mode, mode) not in MODE_BY_ID:
        raise ValueError(f"no such mode: {mode!r}")
      self.mode = resolve_mode(mode)
    if harness is not None:
      if harness not in HARNESS_BY_ID:
        raise ValueError(f"no such harness: {harness!r}")
      self.harness = harness
    return self.settings()

  async def adopt_live(self) -> int:
    """The mode reaches open conversations by itself: the permission handler
    reads it per call. The model does not -- an `Agent` is built with one --
    so what is reported here is the number of conversations the *mode* moved."""
    return len(self._open)

  def on_event(self, callback: Callable[[str, AgentEvent], Awaitable[None]]) -> None:
    self._on_event = callback

  # ── the prompt ───────────────────────────────────────────────────────
  def briefing(self, spec: SessionSpec) -> Dict[str, Any]:
    """Everything a conversation opened on this spec would be given.

    The same sections as Claude's, from the same files, plus one this runtime
    needs: the instructions name the rlmcp tools `mcp__rlmcp__<tool>`, which
    is how Claude Code spells an MCP tool and not how this harness does.
    """
    report = preflight.check(spec, self.mcp_command)
    shape = HARNESS_BY_ID[resolve_harness(self.harness)]
    sections: List[Dict[str, str]] = []
    if shape["instructions"]:
      from rlmcp_studio.agents import harness as harness_files
      sections.append({
          "id": "instructions",
          "title": "The studio's instructions",
          "source": ", ".join(p.name for p in harness_files.files())
                    or "instructions/*.md",
          "text": system_prompt({"tools_attached": report.prompt_block()}),
      })
    if shape["context"]:
      sections.append({
          "id": "tools",
          "title": "The rlmcp tools, by name",
          "source": f"written from the permission table, for {self.mode} mode",
          "text": _tool_list(self.mode, report.attached).strip(),
      })
      if spec.system_context:
        sections.append({
            "id": "context",
            "title": "This run",
            "source": "built from the record by `build_context`",
            "text": spec.system_context,
        })
    if sections and report.attached:
      spelled = "call `<tool>` from that server"
      sections.append({
          "id": "runtime",
          "title": "How this runtime spells the tools",
          "source": "agents/antigravity.py",
          "text": ("You are running on Antigravity. The rlmcp tools are the "
                   f"tools of the MCP server named `{MCP_NAME}`: where the text "
                   f"above says `mcp__rlmcp__<tool>`, {spelled}. Your built-in "
                   "tools are Antigravity's own (`run_command`, `view_file`, "
                   "...), not Claude Code's."),
      })
    return {
        "harness": shape["id"],
        "label": shape["label"],
        "note": shape["note"],
        # Always appended, never replacing: the SDK's own instructions carry
        # its safety mandates and tool protocol, and a studio that replaced
        # them would be handing the harness a model that does not know how to
        # call its tools. Said to the window rather than implied.
        "preset": True,
        "sections": sections,
        "cwd": spec.cwd,
        "model": spec.model or self.model or "",
        "mode": self.mode,
        "mcp": sorted(spec.mcp_servers or {}),
        "tools_attached": report.attached,
        "settings": [],
        "signin": self.auth,
        "runtime": ("Antigravity's own system instructions stay underneath; "
                    "the studio's text is appended as sections."),
    }

  def given(self, session_id: str) -> Optional[Dict[str, Any]]:
    return self._given.get(session_id)

  def granted(self, run_id: str) -> List[str]:
    return sorted(self._always.get(run_id.split("#")[0], set()))

  # ── sessions ─────────────────────────────────────────────────────────
  def _config(self, spec: SessionSpec) -> Any:
    brief = self.briefing(spec)
    self._given[spec.run_id] = brief
    sections = [SystemInstructionSection(content=s["text"].strip(), title=s["id"])
                for s in brief["sections"] if s.get("text", "").strip()]
    instructions = TemplatedSystemInstructions(sections=sections) if sections else None

    servers = []
    for name, server in (spec.mcp_servers or {}).items():
      servers.append(ag_types.McpStdioServer(
          name=name, command=str(server.get("command") or self.mcp_command),
          args=[str(a) for a in (server.get("args") or [])],
          env=dict(server.get("env") or {}) or None))

    capabilities = CapabilitiesConfig(
        agent_behavior=AgentBehavior.INTERACTIVE,
        # Plan changes nothing. The mode can move while a conversation is open
        # and the handler follows it; this is the half that cannot move, and
        # it is set from the mode the conversation starts in.
        enabled_tools=(list(BuiltinTools.read_only()) + [BuiltinTools.FINISH]
                       if self.mode == "plan" else None),
    )
    handler = self._make_permission_handler(spec.run_id,
                                            spec.free_paths)
    return LocalAgentConfig(
        system_instructions=instructions,
        capabilities=capabilities,
        policies=[ag_policy.ask_user("*", handler=handler, name="studio")],
        hooks=[ag_hooks.post_tool_call(self._make_result_hook(spec.run_id))],
        mcp_servers=servers or None,
        workspaces=workspaces_for(spec),
        model=spec.model or self.model or None,
    )

  def _make_permission_handler(self, run_id: str, free_paths=()):
    run = run_id.split("#")[0]
    scope = list(free_paths or [])

    async def handler(call: Any) -> bool:
      name = studio_name(call)
      if name in ALWAYS:
        return True
      args = _args_of(call)
      # `self.mode`, read now: the mode moves from a button while this
      # conversation is open, and the SDK was handed a policy that only ever
      # says "ask us", so this is the one place the answer is decided.
      if policy.verdict(name, args, scope, self.mode) == "allow":
        return True
      if self.mode == "plan":
        return False                     # reads and proposes; asks nobody
      if name in self._always.get(run, set()):
        return True

      decision = await self._ask(
          run_id, ToolCall(name=name, args=args, id=str(getattr(call, "id", "") or "")),
          str(args.get("why") or args.get("rationale") or ""))
      if decision == Decision.ALLOW_ALWAYS:
        self._always.setdefault(run, set()).add(name)
      return decision != Decision.DENY

    return handler

  def _make_result_hook(self, run_id: str):
    async def hook(result: Any) -> None:
      if self._on_event is None:
        return
      error = getattr(result, "error", None)
      await self._on_event(run_id, AgentEvent("tool_result", {
          "id": str(getattr(result, "step_id", None) or getattr(result, "id", None) or ""),
          "content": _flatten(error if error else getattr(result, "result", None)),
          "error": bool(error),
      }))
    return hook

  async def start(self, spec: SessionSpec) -> str:
    if spec.run_id in self._open:
      return spec.run_id
    if Agent is None:
      raise NotSupported(self.unavailable_reason())
    stack = contextlib.AsyncExitStack()
    try:
      agent = await stack.enter_async_context(Agent(self._config(spec)))
    except Exception as exc:
      await stack.aclose()
      self._given.pop(spec.run_id, None)
      # The SDK's own sentence -- "A Gemini API key is required..." -- is the
      # one worth showing; a wrapper around it would only be longer.
      raise NotSupported(f"Antigravity could not start: {exc}") from exc
    self._open[spec.run_id] = _Open(agent, stack, spec)
    return spec.run_id

  async def send(self, session_id: str, text: str,
                 images: Optional[List[bytes]] = None) -> None:
    open_ = self._open.get(session_id)
    if open_ is None:
      raise NotSupported("no session; call start() first")
    if open_.reader is not None and not open_.reader.done():
      # The host does not send into a running turn, so this is the tail of
      # one that has been ended: give it a moment to finish emptying.
      with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(open_.reader), 10.0)
    open_.cancelled = False
    content: Any = text
    if images:
      content = [text] + [AgImage(data=raw, mime_type="image/png") for raw in images]
    await open_.agent.conversation.send(content)
    open_.reader = asyncio.create_task(self._drain(session_id, open_),
                                       name=f"agent-read:{session_id}")

  async def _drain(self, session_id: str, open_: _Open) -> None:
    """Read one turn to its end, and say so.

    A turn is the unit this SDK reads in: `receive_steps` ends when the
    harness goes idle, and raises when the turn was halted. A halt we asked
    for is the turn ending the way we wanted; any other exception is the
    provider failing, and the pane has to be told rather than left at
    "working…".
    """
    conv = open_.agent.conversation
    reason = "end_turn"
    said_error = False
    try:
      async for step in conv.receive_steps():
        for event in translate(step, open_.seen):
          said_error = said_error or event.kind == "error"
          if self._on_event is not None:
            await self._on_event(session_id, event)
    except asyncio.CancelledError:
      raise
    except Exception as exc:                  # noqa: BLE001 - the pane says so
      if open_.cancelled:
        reason = "interrupted"
      else:
        reason = "error"
        # The harness reports a failed turn twice: as an ERROR step, and then
        # by raising the same sentence out of the stream. One card per fault.
        if self._on_event is not None and not said_error:
          await self._on_event(session_id, AgentEvent(
              "error", {"message": f"the agent's turn failed: {exc}"}))
    finally:
      usage = None
      try:
        usage = usage_of(conv.last_turn_usage)
      except Exception:                     # noqa: BLE001 - a number, not the turn
        usage = None
      if self._on_event is not None:
        await self._on_event(session_id, AgentEvent(
            "done", {"reason": reason, "usage": usage}))

  async def interrupt(self, session_id: str) -> None:
    open_ = self._open.get(session_id)
    if open_ is None:
      return
    open_.cancelled = True
    await open_.agent.conversation.cancel()

  async def stop(self, session_id: str) -> None:
    open_ = self._open.pop(session_id, None)
    self._given.pop(session_id, None)
    if open_ is None:
      return
    if open_.reader is not None and not open_.reader.done():
      open_.reader.cancel()
    try:
      await open_.stack.aclose()
    except Exception:                         # noqa: BLE001 - it is gone either way
      pass
