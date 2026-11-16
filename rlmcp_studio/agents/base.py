"""Which AI drives the run.

Several providers can drive a run, and the reason is structural: **the tool
surface is MCP**.
rl-mcp already exposes the whole control surface as an MCP server, so a
provider's job is to run a loop, call tools, and stream -- not to know anything
about training. Any model that can drive MCP tools can drive a run.

So this contract is small on purpose. It carries a session, messages in,
events out, and a permission decision. Everything task-specific is already on
the other side of MCP.

The providers in this package:

``ClaudeProvider``       (`agents/claude.py`) Claude via the Claude Agent SDK.
                         Sessions, permissions, subagents and MCP attachment
                         come with it.
``CodexProvider``        (`agents/codex.py`) Codex, driven through its
                         app-server.
``AntigravityProvider``  (`agents/antigravity.py`) Antigravity via its SDK, an
                         optional extra imported only when chosen.

This module is the seam between them, kept independent of any one provider so
that one provider's assumptions do not become the interface.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any, Awaitable, Callable, Dict, List, Optional, Protocol,
)
from uuid import uuid4


class NotSupported(RuntimeError):
  """This provider cannot do that. Never a falsy stand-in."""


class Decision(str, Enum):
  ALLOW = "allow"
  DENY = "deny"
  ALLOW_ALWAYS = "allow_always"
  """For this run only. Scope is per-run because trust is per-run."""


@dataclass
class ToolCall:
  name: str
  args: Dict[str, Any] = field(default_factory=dict)
  id: str = ""


@dataclass
class PermissionRequest:
  """A tool the provider wants to run that needs a human.

  Which tools land here is a data table, per tool and per run -- never
  ``if name == ...``. Read-only tools (screenshots, metrics, diagnosis) run
  freely; anything that mutates a live run asks.
  """

  id: str
  call: ToolCall
  reason: str = ""
  at: float = field(default_factory=time.time)


@dataclass
class AgentEvent:
  """One thing that happened, carried to the client in the usual envelope."""

  kind: str
  """``token``, ``tool_call``, ``tool_result``, ``permission``, ``done``, ``error``."""

  data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionSpec:
  """What a session is about. One per run record."""

  run_id: str
  cwd: str = ""
  mcp_servers: Dict[str, Any] = field(default_factory=dict)
  """rlmcp's server, pinned to this run's session directory."""

  system_context: str = ""
  """Built from the record: hypothesis, falsifier, stage, diff from parent."""

  model: str = ""
  """Provider-specific. Empty means the provider's default."""

  resume: str = ""
  """A provider session to continue rather than a new one to start.

  For Claude this is a Claude Code session id: the same file `claude --resume`
  opens, so a conversation the studio had before a restart -- or one begun in
  a terminal -- carries its memory into this session. Empty starts fresh."""

  free_paths: List[str] = field(default_factory=list)
  """Directories this conversation may edit without asking.

  Empty for a run: watching an experiment is not a reason to be given write
  access to it. Filled for a *task being built*, where the package under
  construction is the whole point and a prompt per edit would end the loop.
  See `agents/policy.py`."""


class AgentProvider(Protocol):
  """The contract every AI backend implements."""

  name: str
  """``claude``, ``codex``, ``antigravity``. Shown in the UI so the user knows who is driving."""

  def available(self) -> bool:
    """Whether this provider is usable right now -- credentials present, etc.

    A provider that is configured but unusable must say so here rather than
    failing at first message, so the UI can offer the ones that work.
    """

  async def start(self, spec: SessionSpec) -> str:
    """Create or resume a session. Returns a session id."""

  def on_event(self, callback: Callable[[str, AgentEvent], Awaitable[None]]) -> None:
    """Where everything the backend says goes, for as long as it is open.

    Deliberately a push, and it is the difference between a pane that is
    correct and one that lies. A turn is not the unit the backend works in:
    Claude Code starts turns nobody asked for -- a background shell finishing
    injects a notification and the CLI answers it -- and a provider that only
    reads while it is answering *our* message leaves those buffered on the
    stream. The next thing the user types then drains the wrong turn and stops
    at its result, so every reply from that point on is the answer to the
    previous question, and the pane looks like an agent ignoring the person
    typing at it.

    So the host is told everything as it arrives, whether or not it asked, and
    :meth:`send` is a write rather than a read.
    """

  async def send(self, session_id: str, text: str,
                 images: Optional[List[bytes]] = None) -> None:
    """Say something. What comes back arrives through :meth:`on_event`.

    ``images`` is how a frame annotation reaches the model: the box burned into
    the image, with its coordinates repeated in ``text`` as a fallback.
    """

  async def decide(self, session_id: str, request_id: str, decision: Decision,
                   note: str = "") -> None:
    """Answer a permission request."""

  async def stop(self, session_id: str) -> None:
    ...

  # ── optional ─────────────────────────────────────────────────────────
  async def interrupt(self, session_id: str) -> None:
    """End the turn in flight, keeping the conversation and its context.

    Distinct from :meth:`stop`, and the distinction is the whole point: an
    agent that has gone the wrong way has to be *stopped and then told why*,
    and closing the session throws away the context in which that sentence
    makes sense. A provider that cannot do this says so, and the host closes
    the session instead -- worse, but not nothing.
    """
    raise NotSupported(f"{self.name} cannot interrupt a turn")

  async def fork(self, session_id: str) -> str:
    """Branch a conversation, for trying two directions from one point."""
    raise NotSupported(f"{self.name} cannot fork a session")


class Asking:
  """Putting a tool call in front of a person, and matching their answer to it.

  The same in every provider: a request gets an id, the host is told, and the
  turn waits on a future until `decide` resolves it. Written once, because
  per-provider copies drift apart -- one denying a prompt nobody is listening
  for while another waits on it for ever.
  """

  def _init_asking(self) -> None:
    self._pending: Dict[str, asyncio.Future] = {}
    self._asked = 0
    """A monotonic counter for request ids.

    Not `len(self._pending)`, which *shrinks* as requests are answered -- two
    prompts in flight could then share an id, and an approval could resolve a
    different request than the one on screen.
    """
    self._era = uuid4().hex[:6]
    """Per-process, because the counter above is not.

    A transcript is read back from disk after a restart, and the counter starts
    again at one: without this, a request from before a restart and one from
    after could both be `012#main:1:Bash`. The id is what a decision is matched
    to, so ids have to be unique across the life of the *transcript*, not of the
    process.
    """
    self._on_permission: Optional[Callable[[str, PermissionRequest], None]] = None

  def on_permission(self, callback: Callable[[str, PermissionRequest], None]) -> None:
    """Called when a tool needs a human. The host forwards it to the browser."""
    self._on_permission = callback

  async def _ask(self, session_id: str, call: ToolCall, reason: str = "") -> Decision:
    """Ask, and wait for the answer. With nobody listening, the answer is no:
    a prompt that cannot be shown cannot be answered, and waiting on it would
    hang the turn."""
    self._asked += 1
    request_id = f"{session_id}:{self._era}{self._asked}:{call.name}"
    if self._on_permission is None:
      return Decision.DENY
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    self._pending[request_id] = future
    self._on_permission(session_id, PermissionRequest(id=request_id, call=call, reason=reason))
    try:
      return await future
    finally:
      self._pending.pop(request_id, None)

  async def decide(self, session_id: str, request_id: str, decision: Decision,
                   note: str = "") -> None:
    future = self._pending.get(request_id)
    if future is not None and not future.done():
      future.set_result(decision)
