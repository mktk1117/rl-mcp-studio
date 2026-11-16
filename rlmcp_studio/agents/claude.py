"""The Claude provider: Claude Code as a library, in the studio's process.

One :class:`ClaudeSDKClient` per run, kept open, so the conversation about a
run survives across turns and across a browser reload. The SDK supplies the
agent loop, sessions, permissions, subagents and MCP attachment; this module
configures them and translates the SDK's message types into the studio's
:class:`~rlmcp_studio.agents.base.AgentEvent`.

Two things worth knowing about the shape:

**The tools come from MCP, not from here.** rl-mcp's server is attached pinned
to this run's session directory, so the agent gets the same surface an agent
gets over MCP today -- and this file never learns what a reward weight is.

**Permission decisions are answered by a human, asynchronously.** The SDK calls
``can_use_tool`` and waits; the studio turns that into an event, sends it to
the browser, and resolves a future when someone clicks. That is the whole
safety story, and it is why the callback is allowed to block.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from rlmcp_studio.agents import policy, preflight
from rlmcp_studio.agents.base import (
    AgentEvent, Asking, Decision, NotSupported, SessionSpec, ToolCall,
)

try:
  from claude_agent_sdk import (
      AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
      SystemMessage, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock,
      UserMessage,
  )
  from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
  SDK_IMPORT_ERROR = ""
except Exception as exc:                      # the studio must start without it
  SDK_IMPORT_ERROR = str(exc)
  ClaudeSDKClient = None                      # type: ignore[assignment]
  UserMessage = None                          # type: ignore[assignment]
  SystemMessage = None                        # type: ignore[assignment]


MAX_MESSAGE_BYTES = 32 * 1024 * 1024
"""Ceiling on one message from the CLI, over the SDK's 1 MB default.

A cap is still wanted -- an unbounded buffer is a memory leak waiting for a
runaway tool -- but 1 MB is below the size of the things this app's agent reads
every day: a frame, a clip, a metrics dump.
"""

SYSTEM_FALLBACK = """You are driving a reinforcement-learning training run from
the rlmcp studio, a GUI the user is looking at right now. The rlmcp MCP tools,
when they are attached, are pinned to this run and their names begin with
`mcp__rlmcp__`. Use them rather than reasoning from memory — and if you have
none, say so plainly rather than substituting `Bash` for them."""
"""Used only if the instruction files cannot be read at all.

Deliberately short: a fallback that restated the harness would be a second
copy to keep in step, and the failure it covers -- a missing or unreadable
`instructions/` directory -- is one worth noticing rather than papering over.
"""


def system_prompt(extra: Optional[Dict[str, str]] = None) -> str:
  """The studio's instruction harness, assembled from `instructions/*.md`.

  Read at call time rather than at import, so editing an instruction file
  takes effect on the next conversation rather than on the next restart. That
  matters more than the microsecond it costs: this is the text you edit while
  watching the agent get something wrong.

  ``extra`` fills the placeholders whose answer is different per conversation —
  today just `{{tools_attached}}`, written by `preflight`. Called with nothing,
  the harness falls back to text that claims nothing about the tools, which is
  the only honest thing to say when nobody has looked.
  """
  from rlmcp_studio.agents import harness
  return harness.instructions(extra) or SYSTEM_FALLBACK


def _tool_list(mode: str = "manual", attached: bool = True) -> str:
  """The rlmcp tools by their real names, in the prompt.

  Written from the permission table rather than by hand, so a tool that gains a
  policy gains a mention, and the two cannot drift.

  **For the mode the conversation starts in**, and that is not decoration: a
  prompt that says "the user is asked first" over half the list while the
  studio runs in a mode that asks about nothing makes the agent narrate
  approvals it does not need and treat `set_parameter` as something to
  propose. A prompt is fixed when the session opens, so a mode changed
  mid-conversation leaves this paragraph behind -- the handler and the SDK both
  move (`adopt_live`), and the agent is told the truth again on the next one.
  """
  table = policy.describe(mode)
  free = [t for t in table["allow"] if t.startswith("mcp__rlmcp__")]
  asks = [t for t in table["ask"] if t.startswith("mcp__rlmcp__")]
  if not attached:
    # Naming them here would be the same lie one paragraph later. The count is
    # worth saying because it is what the agent notices -- a prompt listing two
    # dozen tools beside a tool list holding none is a contradiction the agent
    # can spend a whole turn trying to resolve.
    return (f"\n\nNone of the {len(free) + len(asks)} `mcp__rlmcp__` tools are "
            "in this conversation, so nothing about which of them ask first "
            "applies. See above for why, and say so rather than working "
            "around it.\n")
  if not asks:
    return ("\n\nYours to call, none of them asking anybody first:\n  "
            + "\n  ".join(free) + "\n")
  return ("\n\nAvailable without asking:\n  " + "\n  ".join(free)
          + "\n\nAvailable, but the user is asked first:\n  " + "\n  ".join(asks) + "\n")


def _rlmcp_server() -> str:
  """Where `rlmcp-server` actually is, asked of this interpreter first.

  `shutil.which` alone is wrong here, and the way it is wrong is the way the
  studio is *installed*: `uv tool install rl-mcp-studio` puts every console
  script in the tool's own bin, and links only `rlmcp-studio` onto `$PATH`. So `rlmcp-server` sits right beside
  the interpreter running this code and is nowhere `which` will look, and the
  agent comes up with no rlmcp tools at all and one line of explanation:

      ENOENT: Executable not found in $PATH: rlmcp-server

  Beside `sys.executable` is the answer for a uv tool, a venv and a plain
  install alike, because in all three the scripts of an installed package live
  next to the python that imports it. `$PATH` stays as the fallback, for the
  case where the studio is run some way that separates the two.
  """
  beside = Path(sys.executable).parent / "rlmcp-server"
  if beside.is_file() and os.access(beside, os.X_OK):
    return str(beside)
  return shutil.which("rlmcp-server") or "rlmcp-server"


#: The models this provider can be asked for, newest first, with the empty id
#: meaning "whatever the CLI would pick on its own".
#:
#: A list rather than free text because a typo here is a turn that fails at the
#: provider with a message the panel cannot explain, and because the panel has
#: to *name* them: `claude-haiku-4-5-20251001` is an id, not a thing a person
#: chooses between. The shape is deliberately provider-agnostic -- id, label,
#: note -- so a second provider fills the same table.
#:
#: `context` is the model's window in tokens, for the composer's meter and the
#: "this conversation is getting large" warning. The SDK does not report it,
#: and a guessed 200k under a million-token model would read as a
#: conversation at several times its room.
MODELS = [
    {"id": "", "label": "Default", "note": "whatever the CLI is configured for",
     "context": 1_000_000},
    {"id": "claude-opus-5-5", "label": "Opus 5.5", "note": "most capable",
     "context": 1_000_000},
    {"id": "claude-opus-5", "label": "Opus 5", "note": "previous Opus",
     "context": 1_000_000},
    {"id": "claude-sonnet-5", "label": "Sonnet 5", "note": "balanced",
     "context": 1_000_000},
    {"id": "claude-fable-5", "label": "Fable 5", "note": "writing",
     "context": 1_000_000},
    {"id": "claude-haiku-4-5-20251001", "label": "Haiku 4.5", "note": "fastest",
     "context": 200_000},
]

#: How much the agent may do without asking.
#:
#: The studio's vocabulary, mapped onto the SDK's `permission_mode`. The names
#: are about *what happens to you* rather than about the mechanism: "Manual"
#: is a prompt per mutation.
#:
#: **Auto is the mode this studio is for, and it frees everything.** A middle
#: mode that still stopped for any command it could not prove was a read
#: would describe a shape of work the studio is not for: approving `uv run`
#: for the fourth time is not oversight. The person's lever is the
#: *interrupt*, and the feedback they leave on what they see; the agent restarts runs and moves parameters on its
#: own. Described in the words of what it permits, because it is the mode where
#: the studio's own policy table stops being consulted.
MODES = [
    {"id": "auto-edit", "label": "Auto", "sdk": "bypassPermissions",
     "note": "runs everything without asking — commands, edits, and changes "
             "to a live run. Interrupt it rather than gate it"},
    {"id": "manual", "label": "Manual", "sdk": "default",
     "note": "asks before anything that changes the run or the files"},
    {"id": "plan", "label": "Plan", "sdk": "plan",
     "note": "reads and proposes, changes nothing"},
]

#: How much of the studio's own text the agent is started with.
#:
#: The harness is the studio's opinion about how to drive a run, and there are
#: two reasons to be able to put it down. The first is diagnostic: when the
#: agent behaves oddly, the question "is that the model or is that our prompt"
#: has no answer while the prompt cannot be removed. The second is that a
#: person who lives in Claude Code has their own instructions -- `CLAUDE.md`,
#: their skills, their commands -- and the studio replacing the whole system
#: prompt with its own is the one thing that stops those reading normally.
#:
#: ``preset`` is the mechanism: ``False`` sends the studio's text *as* the
#: system prompt, which is what replaces Claude Code's own; ``True`` keeps
#: Claude Code's and appends whatever is left. So `plain` is not "no prompt",
#: it is the prompt a terminal in this directory would have had -- the task
#: repository's `CLAUDE.md`, skills and commands come with it either way,
#: because `setting_sources` is not part of this choice.
HARNESSES = [
    {"id": "studio", "label": "Studio", "preset": False,
     "instructions": True, "context": True,
     "note": "the studio's instructions, this run's context, and the truth "
             "about which tools are attached"},
    {"id": "context", "label": "Context only", "preset": True,
     "instructions": False, "context": True,
     "note": "Claude Code as it comes, told which run this is and which tools "
             "it has — no studio instructions"},
    {"id": "plain", "label": "Plain", "preset": True,
     "instructions": False, "context": False,
     "note": "nothing from the studio but the tools and the working directory "
             "— the agent a terminal here would give you"},
]

HARNESS_BY_ID = {h["id"]: h for h in HARNESSES}


def resolve_harness(harness: str) -> str:
  """The current id for whatever the caller said, or `studio` for nonsense.

  Falling back to the full harness rather than to `plain`: a settings file with
  a typo in it should cost the agent nothing, and the quiet failure the other
  way -- an agent silently started with none of the studio's instructions --
  is the one nobody would notice for a week.
  """
  return harness if harness in HARNESS_BY_ID else "studio"


#: Ids that are no longer offered, and what they became. An older settings
#: file can say `bypass`, a mode Auto now covers, and a mode id that no longer
#: resolves would silently drop that studio back into `manual` — the loudest
#: possible way to lose a preference.
ALIASES = {"bypass": "auto-edit"}

MODE_BY_ID = {m["id"]: m for m in MODES}

SETTING_SOURCES = ("user", "project", "local")
"""Where the CLI reads `CLAUDE.md`, skills and commands from.

A constant because the Briefing window says it: whichever harness is chosen,
the task repository's own instructions are loaded by Claude Code itself, and a
window that listed only the studio's sections would read as though they were
not there.
"""


def system_value(brief: Dict[str, Any]):
  """The `system_prompt` option, assembled from a briefing's own sections.

  From the sections rather than beside them, so what the window shows and what
  the CLI receives cannot be two different texts.

  A string *replaces* Claude Code's system prompt; the preset form keeps it and
  appends. Sending an empty append would be a flag with nothing after it, so a
  briefing with no sections resolves to the bare preset.
  """
  text = "\n\n".join(s["text"].strip() for s in brief["sections"]
                     if s.get("text", "").strip())
  if not brief.get("preset"):
    return text
  preset: Dict[str, Any] = {"type": "preset", "preset": "claude_code"}
  if text:
    preset["append"] = text
  return preset


def resolve_mode(mode: str) -> str:
  """The current id for whatever the caller said, or `manual` for nonsense."""
  mode = ALIASES.get(mode, mode)
  return mode if mode in MODE_BY_ID else "manual"


class ClaudeProvider(Asking):
  """The Claude Agent SDK, hosted in this process."""

  name = "claude"
  label = "Claude"
  """How the provider menu names it. The id is what settings and routes use."""

  def __init__(self, model: str = "", mcp_command: Optional[str] = None,
               mode: str = "manual", harness: str = "studio"):
    self.model = model
    self.mode = resolve_mode(mode)
    """How much runs without asking. Changed from the GUI, and it takes effect
    on the conversation already open: the permission handler reads this
    attribute per call, and `adopt_live()` tells the SDK as well."""
    self.harness = resolve_harness(harness)
    """How much of the studio's own text starts a conversation.

    Unlike the mode, this one cannot move under a conversation that is already
    open: a system prompt is given to the CLI once, at connect, and the SDK has
    no control message that replaces it. So this is read when a session starts
    and the panel says as much rather than implying otherwise."""
    self.mcp_command = mcp_command or _rlmcp_server()
    self._clients: Dict[str, ClaudeSDKClient] = {}
    self._init_asking()
    self._given: Dict[str, Dict[str, Any]] = {}
    """What each open session was actually started with, by session id.

    Kept because it is the honest answer to the question the Briefing window
    asks. The harness and the mode both move from a button while a
    conversation is open, and only the system prompt does *not* follow -- so a
    window that recomposed the briefing from the current settings would show a
    conversation a prompt it never had."""
    self._always: Dict[str, set] = {}
    """Tools granted for a whole run, by run id. The button says "Always for
    this run", so the grant has to be remembered or the next call asks again."""
    self._on_event: Optional[Callable[[str, AgentEvent], Awaitable[None]]] = None
    self._readers: Dict[str, asyncio.Task] = {}
    """One drain per session, running for as long as the session is open.

    Not one per turn. See `AgentProvider.on_event`: the CLI answers things the
    studio never sent it, and anything read only during our own turn is read
    at the wrong time and attributed to the wrong question."""
    self._echo: Dict[str, List[str]] = {}
    """What we last said to each session, to recognise the CLI's echo of it.

    The stream carries our own message back as a user message. It is already in
    the transcript -- the host wrote it before sending -- so the echo is
    dropped, and a user message that is *not* one of ours is the CLI waking the
    agent, which is a thing the pane has to show."""
    self.auth_state: Dict[str, Any] = {}
    """Last answer from `claude auth status`, refreshed by the host."""

  # ── availability ─────────────────────────────────────────────────────
  def available(self) -> bool:
    return not self.unavailable_reason()

  def unavailable_reason(self) -> str:
    """Why this provider cannot be used, in words a user can act on.

    Returned rather than raised because "not signed in" is a normal state --
    the studio is useful without an agent, and a panel that says what to do
    beats one that is simply missing.

    ``auth_state`` is filled in by the host from ``claude auth status``. A
    credentials file on disk outlives its session, so its mere presence proves
    nothing; only the CLI knows.
    """
    if ClaudeSDKClient is None:
      return f"claude-agent-sdk is not importable: {SDK_IMPORT_ERROR}"
    if not shutil.which("claude"):
      return "the `claude` CLI is not on PATH; the SDK runs it"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
      return ""
    if self.auth_state.get("loggedIn"):
      return ""
    return self.auth_state.get("reason") or "not signed in"

  # ── sessions ─────────────────────────────────────────────────────────
  def settings(self) -> Dict[str, Any]:
    """The choices this provider offers, and which are in force.

    `models` and `modes` come from the provider rather than the client, so a
    second provider offering a different set needs no change in the browser.
    """
    return {
        "provider": self.name,
        "models": list(MODELS),
        "model": self.model,
        "modes": [{k: v for k, v in m.items() if k != "sdk"} for m in MODES],
        "mode": self.mode,
        # Only the three fields the panel draws: `preset`, `instructions` and
        # `context` are how this file assembles a prompt, and a client that
        # knew them would be a second place where the assembly is described.
        "harnesses": [{k: h[k] for k in ("id", "label", "note")}
                      for h in HARNESSES],
        "harness": self.harness,
    }

  def choose(self, model: Optional[str] = None, mode: Optional[str] = None,
             harness: Optional[str] = None) -> Dict[str, Any]:
    """Adopt a model, a mode or a harness. Unknown values are refused."""
    if model is not None:
      if model not in {m["id"] for m in MODELS}:
        raise ValueError(f"no such model: {model!r}")
      self.model = model
    if mode is not None:
      # Through the alias table, so a `bypass` arriving from a stored setting
      # or an older client is adopted as the mode it became rather than
      # refused for having been renamed.
      if ALIASES.get(mode, mode) not in MODE_BY_ID:
        raise ValueError(f"no such mode: {mode!r}")
      self.mode = resolve_mode(mode)
    if harness is not None:
      if harness not in HARNESS_BY_ID:
        raise ValueError(f"no such harness: {harness!r}")
      self.harness = harness
    return self.settings()

  async def adopt_live(self) -> int:
    """Tell the conversations already open about a new mode or model.

    The studio's own answer moves the moment `self.mode` does -- the handler
    reads it per call -- but the SDK has a second copy, given at construction,
    and it is the copy that decides the things the handler never sees. Both
    halves have to move together or the mode is only half adopted.

    Returns how many live sessions took it, so the endpoint can say whether
    the choice applies to what is on screen or only to the next conversation.
    """
    moved = 0
    sdk_mode = MODE_BY_ID[self.mode]["sdk"]
    for client in list(self._clients.values()):
      try:
        await client.set_permission_mode(sdk_mode)
        if self.model:
          await client.set_model(self.model)
        moved += 1
      except Exception:
        # A client that has died, or an SDK without the control channel: the
        # next conversation gets it from `_options` either way, and failing
        # the whole settings write over it would lose the preference too.
        continue
    return moved

  def briefing(self, spec: SessionSpec) -> Dict[str, Any]:
    """Everything a conversation opened on this spec would be given.

    One function, and both halves are built from it: `_options` sends it and
    the Briefing window shows it. Two code paths would be a window describing a
    prompt the agent did not get, which is the exact failure this is for --
    the studio's text decides how the agent behaves, and without the window
    the only way to read it is to open `instructions/` in an editor and
    assemble it in your head.

    The sections are the pieces in the order they are sent, each with where it
    came from, so a paragraph that reads wrongly can be traced to the file that
    wrote it.
    """
    # Asked of the spec that is about to ship, not of the intention behind it.
    # The prompt's one claim that changes per conversation -- that the rlmcp
    # tools are here -- is written from this and from nothing else, so a
    # conversation opened on a run that was never launched is told the truth
    # instead of being handed a list of tools it does not have.
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
    return {
        "harness": shape["id"],
        "label": shape["label"],
        "note": shape["note"],
        # Whether Claude Code's own system prompt is underneath what follows.
        # The panel says this out loud: "Studio" *replaces* it, and somebody
        # reading a briefing that lists three sections deserves to know
        # whether that is the whole prompt or an addition to one.
        "preset": bool(shape["preset"]),
        "sections": sections,
        "cwd": spec.cwd,
        "model": spec.model or self.model or "",
        "mode": self.mode,
        "mcp": sorted(spec.mcp_servers or {}),
        "tools_attached": report.attached,
        # True whatever the harness says, and worth saying in the window: the
        # task repository's own `CLAUDE.md`, skills and commands are loaded by
        # the CLI, not by us, so "Plain" is a normal Claude Code and not a
        # naked model.
        "settings": list(SETTING_SOURCES),
    }

  def given(self, session_id: str) -> Optional[Dict[str, Any]]:
    """What an open conversation was started with, or `None` if none is."""
    return self._given.get(session_id)

  def _options(self, spec: SessionSpec) -> "ClaudeAgentOptions":
    mcp = dict(spec.mcp_servers or {})
    # Composed once and kept, because this is the only moment the prompt is
    # decided: `start()` is the caller, and after it the harness setting may
    # move while this conversation's prompt cannot.
    brief = self.briefing(spec)
    self._given[spec.run_id] = brief
    return ClaudeAgentOptions(
        system_prompt=system_value(brief),
        mcp_servers=mcp,
        can_use_tool=self._make_permission_handler(
            spec.run_id, spec.free_paths),
        permission_mode=MODE_BY_ID[self.mode]["sdk"],
        cwd=spec.cwd or None,
        model=spec.model or self.model or None,
        # Continue a Claude Code session rather than open a new one: the
        # conversation this studio had before it restarted, or one begun in a
        # terminal and attached here. The CLI's own `--resume`, so the memory
        # comes back with it and the terminal can take it back afterwards.
        resume=spec.resume or None,
        # One message from the CLI can be large, and in this app it reliably
        # is: a tool result carrying a rendered frame, a clip's bytes, or a
        # long metrics dump all arrive as a single JSON line. The SDK's 1 MB
        # default kills the turn with "JSON message exceeded maximum buffer
        # size" -- an agent that records a clip and reads it back is enough.
        max_buffer_size=MAX_MESSAGE_BYTES,
        # The task repo's own CLAUDE.md, skills and commands apply to this
        # agent exactly as they do in a terminal -- that is most of the reason
        # to use this SDK rather than the raw API.
        setting_sources=list(SETTING_SOURCES),
    )

  def _make_permission_handler(self, run_id: str, free_paths=()):
    run = run_id.split("#")[0]        # the grant is per run, as the button says
    scope = list(free_paths or [])

    async def handler(tool_name: str, input_data: dict, context: Any):
      # `self.mode`, read now rather than closed over: the mode is changed
      # from a button while a conversation is open, and a handler built at
      # `start()` would go on asking in the old mode until the next one.
      if policy.verdict(tool_name, input_data, scope, self.mode) == "allow":
        return PermissionResultAllow(updated_input=input_data)
      if tool_name in self._always.get(run, set()):
        return PermissionResultAllow(updated_input=input_data)

      decision = await self._ask(
          run_id, ToolCall(name=tool_name, args=dict(input_data)),
          str(input_data.get("why") or input_data.get("rationale") or ""))

      if decision == Decision.DENY:
        return PermissionResultDeny(message="The user declined this change.")
      if decision == Decision.ALLOW_ALWAYS:
        self._always.setdefault(run, set()).add(tool_name)
      return PermissionResultAllow(updated_input=input_data)

    return handler

  async def start(self, spec: SessionSpec) -> str:
    if ClaudeSDKClient is None:
      raise NotSupported(self.unavailable_reason())
    if spec.run_id in self._clients:
      return spec.run_id
    client = ClaudeSDKClient(options=self._options(spec))
    await client.connect()
    self._clients[spec.run_id] = client
    self._echo[spec.run_id] = []
    self._readers[spec.run_id] = asyncio.create_task(
        self._drain(spec.run_id, client), name=f"agent-read:{spec.run_id}")
    return spec.run_id

  async def _drain(self, session_id: str, client: ClaudeSDKClient) -> None:
    """Read this session's stream until the session closes.

    The whole conversation, not one turn of it. `receive_response`, called
    once per message sent, stops at the first result on a stream that is
    shared with turns the CLI runs on its own. A background `Bash` finishing
    injects a `<task-notification>` and the CLI answers it; with nobody
    reading, that answer waits on the stream, and the next thing the user
    types drains it and stops. From then on the pane is one turn behind for
    the rest of the session: every reply is the answer to the previous
    question, and the real answer to the last one never appears at all. Two
    messages in a row read as an agent ignoring the person typing.
    """
    try:
      async for message in client.receive_messages():
        for event in _translate(message, self._echo.get(session_id)):
          if self._on_event is not None:
            await self._on_event(session_id, event)
    except asyncio.CancelledError:
      raise
    except Exception as exc:                  # noqa: BLE001 - the pane says so
      # A stream that dies takes the conversation with it, and the alternative
      # to saying that is a panel that sits at "working" until it is reloaded.
      if self._on_event is not None:
        await self._on_event(session_id, AgentEvent(
            "error", {"message": f"the agent's stream ended: {exc}"}))
        await self._on_event(session_id, AgentEvent("done", {"reason": "error"}))

  def on_event(self, callback: Callable[[str, AgentEvent], Awaitable[None]]) -> None:
    self._on_event = callback

  async def send(self, session_id: str, text: str,
                 images: Optional[List[bytes]] = None) -> None:
    client = self._clients.get(session_id)
    if client is None:
      raise NotSupported("no session; call start() first")

    echo = self._echo.setdefault(session_id, [])
    echo.append(text.strip())
    del echo[:-8]
    if images:
      await client.query(_with_images(text, images))
    else:
      await client.query(text)

  def granted(self, run_id: str) -> List[str]:
    """Which tools this run has standing approval for. For the UI to show."""
    return sorted(self._always.get(run_id.split("#")[0], set()))

  async def stop(self, session_id: str) -> None:
    client = self._clients.pop(session_id, None)
    self._echo.pop(session_id, None)
    self._given.pop(session_id, None)
    reader = self._readers.pop(session_id, None)
    if reader is not None:
      reader.cancel()
    if client is not None:
      try:
        await client.disconnect()
      except Exception:
        pass

  async def interrupt(self, session_id: str) -> None:
    client = self._clients.get(session_id)
    if client is not None:
      await client.interrupt()


async def _with_images(text: str, images: List[bytes]):
  """One user message carrying text and pictures, in the SDK's streaming form.

  ``query`` takes either a string or an async iterable of message dicts, and
  only the second can hold content blocks -- which is the only way an image
  reaches the model without first being written somewhere it can be read from.
  The annotated frame is also named in the text, so an agent that never
  received the block can still open the file rather than answer about a picture
  it did not see.
  """
  blocks: List[Dict[str, Any]] = [{"type": "text", "text": text}]
  for raw in images:
    blocks.append({
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png",
                   "data": base64.b64encode(raw).decode("ascii")},
    })
  yield {"type": "user",
         "message": {"role": "user", "content": blocks},
         "parent_tool_use_id": None}


def _translate(message: Any, echo: Optional[List[str]] = None) -> List[AgentEvent]:
  """SDK message types to the studio's envelope.

  One place, so the rest of the app never imports the SDK and a second provider
  emits the same events without imitating Claude's types.

  ``echo`` is what the studio has recently said on this session, so that our own
  message coming back can be told apart from one the CLI wrote itself.
  """
  out: List[AgentEvent] = []
  if SystemMessage is not None and isinstance(message, SystemMessage):
    # The CLI's first word on a session is its `init`, and it carries the one
    # fact the studio cannot make up: the session id -- the name of the file
    # under `~/.claude/projects/` that `claude --resume` opens. Kept in the
    # transcript so the conversation can be continued after a restart, from
    # here or from a terminal, instead of being read back as words the agent
    # no longer remembers.
    if message.subtype == "init":
      data = message.data if isinstance(message.data, dict) else {}
      out.append(AgentEvent("session", {
          "provider": "claude",
          "session_id": str(data.get("session_id") or ""),
          "cwd": str(data.get("cwd") or ""),
          "model": str(data.get("model") or ""),
      }))
    return out
  if isinstance(message, AssistantMessage):
    # How big the request that produced this message was: everything the
    # model read, cached or not. The turn's `done` sums these over every call
    # in the turn, which is a cost, not a size -- a turn of fifteen tool calls
    # reads as fifteen conversations. The main agent's only: a subagent's
    # request is a separate, smaller context.
    usage = getattr(message, "usage", None)
    if isinstance(usage, dict) and not getattr(message, "parent_tool_use_id", None):
      read = sum(int(usage.get(k) or 0) for k in
                 ("input_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens"))
      if read:
        out.append(AgentEvent("context", {"tokens": read}))
    for block in message.content:
      if isinstance(block, TextBlock):
        out.append(AgentEvent("text", {"text": block.text}))
      elif isinstance(block, ThinkingBlock):
        out.append(AgentEvent("thinking", {"text": block.thinking}))
      elif isinstance(block, ToolUseBlock):
        out.append(AgentEvent("tool_call", {
            "id": block.id, "name": block.name, "args": block.input,
            "policy": policy.verdict(block.name),
        }))
      elif isinstance(block, ToolResultBlock):
        out.append(AgentEvent("tool_result", {
            "id": block.tool_use_id, "content": _flatten(block.content),
            **_image_weight(block.content),
        }))
  elif UserMessage is not None and isinstance(message, UserMessage):
    # Tool *results* come back as a user message -- that is the shape of the
    # protocol, not a person speaking. Without this the pane would show what
    # the agent asked for and never what it got, so every call would sit there
    # looking unanswered.
    blocks = message.content if isinstance(message.content, list) else []
    results = [b for b in blocks if isinstance(b, ToolResultBlock)]
    for block in results:
      out.append(AgentEvent("tool_result", {
          "id": block.tool_use_id, "content": _flatten(block.content),
          "error": bool(getattr(block, "is_error", False)),
          **_image_weight(block.content),
      }))
    # Text in a user message is usually the echo of what was typed here, which
    # the host wrote to the transcript before sending. What is left over when
    # it is not is the CLI *waking the agent*: a background shell finishing, a
    # hook, anything that starts a turn nobody in this studio asked for. That
    # is worth a line of its own, because otherwise the pane shows an answer
    # under a question it does not belong to.
    #
    # Only when nothing else is in the message: reminders ride along with tool
    # results all the time, and a note on every result is a note nobody reads.
    #
    # And only when there is an echo to check it against: without one there is
    # no way to tell our own message from the CLI's, and calling the user's own
    # words a wake-up is the worse of the two mistakes.
    if not results and echo is not None:
      woke = _woke(blocks if blocks else message.content, echo)
      if woke:
        out.append(AgentEvent("wake", {"text": woke}))
  elif isinstance(message, ResultMessage):
    # What the turn cost. Carried on `done` rather than as its own event kind
    # because it is a *property of the turn ending*, and a kind of its own
    # would need a handler on the client that did nothing but stash a number.
    #
    # Read defensively: `usage` is the SDK's shape, not ours, and a provider
    # that stops reporting it should cost the panel a number rather than the
    # end of the turn.
    usage = getattr(message, "usage", None) or {}
    if not isinstance(usage, dict):
      usage = getattr(usage, "__dict__", {}) or {}
    used = sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens", "output_tokens"))
    out.append(AgentEvent("done", {
        "reason": getattr(message, "terminal_reason", ""),
        "usage": {"used": used,
                  "input": int(usage.get("input_tokens") or 0),
                  "output": int(usage.get("output_tokens") or 0),
                  "cost_usd": getattr(message, "total_cost_usd", None)}
                 if used else None,
    }))
  return out


def _woke(content: Any, echo: Optional[List[str]]) -> str:
  """The part of a user message the studio did not put there, if any."""
  if isinstance(content, str):
    parts = [content]
  else:
    parts = [b.text for b in content
             if TextBlock is not None and isinstance(b, TextBlock)]
  said = []
  for text in parts:
    stripped = text.strip()
    if not stripped:
      continue
    if echo is not None and stripped in echo:
      echo.remove(stripped)
      continue
    said.append(stripped)
  return "\n".join(said)


def _image_weight(content: Any) -> Dict[str, int]:
  """How many images a tool result carried, and their base64 size in bytes.

  An image a tool returns stays in the conversation and is sent again with
  every later request. The API refuses a request over 32 MB by dropping the
  connection -- "Connection dropped (ECONNRESET)", retried ten times, then
  the turn dies -- and a conversation of a hundred-odd robot frames gets
  there without a word of warning. The transcript keeps the count so the
  studio can warn first. Empty when there are none, so the transcript stays
  as it was.
  """
  if not isinstance(content, list):
    return {}
  count = size = 0
  for item in content:
    if isinstance(item, dict) and item.get("type") == "image":
      count += 1
      source = item.get("source") or {}
      size += len(str(source.get("data") or "")) if isinstance(source, dict) else 0
  return {"images": count, "image_bytes": size} if count else {}


def _flatten(content: Any) -> str:
  if isinstance(content, str):
    return content
  if isinstance(content, list):
    parts = []
    for item in content:
      if isinstance(item, dict):
        parts.append(item.get("text") or f"[{item.get('type', 'block')}]")
      else:
        parts.append(str(item))
    return "\n".join(parts)
  return str(content)
