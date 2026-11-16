"""One conversation about one run: its transcript, its state, its name.

The host (`agents/host.py`) owns every conversation and drives the provider;
this is the record of a single one, which the host keeps, persists and
describes to the panel.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from rlmcp_studio.agents.base import SessionSpec

DEFAULT_THREAD = "main"


def key(run_id: str, thread: str = DEFAULT_THREAD) -> str:
  """One run can hold several conversations, so a session is keyed by both.

  A long investigation and a quick "what is this metric" should not share a
  context window, and neither should be interrupted to ask the other.
  """
  return f"{run_id}#{thread or DEFAULT_THREAD}"


def _title(text: Optional[str]) -> str:
  """A conversation's name: its first message, on one line and cut short."""
  text = (text or "").strip().replace("\n", " ")
  return text[:52] + ("…" if len(text) > 52 else "")


class AgentSession:
  """One conversation about one run."""

  def __init__(self, run_id: str, spec: SessionSpec, thread: str = DEFAULT_THREAD):
    self.run_id = run_id
    """The record this conversation is about. Not how the provider is addressed."""
    self.thread = thread
    self.title = ""
    """First thing asked, trimmed -- a conversation names itself."""
    self.spec = spec
    self.transcript: List[Dict[str, Any]] = []
    self.pending: Dict[str, Dict[str, Any]] = {}
    self._busy = False
    self.busy_since = 0.0
    """When the turn now running began, as `time.time()`; 0 while idle.

    Set by the `busy` setter on every rise, so the six places that start a
    turn need not each remember the clock. The panel says "working for 4m"
    from it -- the difference between an agent that is thinking and one that
    hung an hour ago is exactly this number.
    """
    self.file_at = 0.0
    """When the transcript on disk was last written, for a conversation read
    back after a restart whose entries predate the `at` stamp."""
    self.started = False
    self.restored = False
    """Read back from disk after a restart: the words are here, the model's
    memory of them is not."""
    self.told_arrived = False
    """Whether this conversation has been told its run came up without it.

    Once: a note repeated on every message is a note nobody reads, and the
    thing it is about does not change until somebody opens a new conversation.
    """
    self.preflight = None
    """What the last start of this session found wrong with it, if anything.

    A :class:`~rlmcp_studio.agents.preflight.Report`, filled by `ensure` at the
    moment the provider is started, and kept because the answer is about the
    conversation rather than about the turn: a pane reopened an hour later
    should still be able to say that this one has no tools.
    """
    self.provider: Any = None
    """Who is driving this conversation: the provider it was *started* with.

    A conversation keeps its provider the way it keeps its prompt -- the
    context lives inside one SDK session, and switching the studio's provider
    from a menu must not silently start answering an open conversation from a
    different model with none of its memory. The switch applies to the next
    conversation, and to this one only after a break closes it.
    """
    self.stopping = False
    """A stop has been asked for and the turn has not finished admitting it.

    An interrupt is not instant: the provider is mid-sentence, and what it has
    already produced arrives *after* the request. Three things depend on
    knowing that this window is open -- the turn does not get to look busy
    again, the provider's own "[Request interrupted by user]" is not repeated
    back as though the agent had woken by itself, and "You stopped it." is said
    at the end of what it interrupted rather than above it.
    """
    self.context_tokens = 0
    """How much the model read on this conversation's last request, in tokens.

    Not the turn's `usage.used`, which sums every request in a turn -- a turn
    of fifteen tool calls read as fifteen conversations. Zero until a request
    has reported; it is not stored, so a restarted studio learns it again on
    the next turn."""
    self.heavy_warned = False
    """The "this conversation is getting large" card has been shown. Once per
    conversation context: a second copy on every turn is noise."""
    self.drop_flagged = False
    """A dropped-connection card is already under this turn. Cleared on send,
    like `auth_flagged`."""
    self.auth_flagged = False
    """A sign-in card has already been put under this turn.

    The provider can say the same thing twice in one failed turn, and a second
    card is not a second problem. Cleared when you send again, because the next
    failure after a sign-in is news.
    """
    self.session_id = ""
    """The provider's own name for this conversation -- for Claude, the Claude
    Code session id. Learned from the provider's `session` event, kept in the
    transcript, and what `ensure` resumes from after a restart."""
    self.session_provider = ""
    """Which provider issued `session_id`; a Codex thread is not resumable
    through Claude's `--resume` and the other way round."""
    self.session_cwd = ""
    self.resuming = False
    """A start with `resume` is in flight and nothing has come back yet. If the
    first thing back is an error, the resume is what failed, and the next
    message starts fresh rather than erroring the same way forever."""
    self.break_note = ""
    """Why the context above this point is gone, when it is not a restart.

    A restart is the usual reason and has its own sentence. A stop that had to
    close the provider's session -- because that provider cannot interrupt a
    turn -- is the other, and telling the user their studio restarted when it
    did not is the kind of small lie that costs an hour later.
    """

  @property
  def busy(self) -> bool:
    return self._busy

  @busy.setter
  def busy(self, value: bool) -> None:
    if value and not self._busy:
      self.busy_since = time.time()
    elif not value:
      self.busy_since = 0.0
    self._busy = bool(value)

  @property
  def last_at(self) -> float:
    """When anything last happened here, as `time.time()`.

    The list of conversations is sorted by this, because "which one was I
    just talking to" is the question that list exists to answer and a sort
    by run id would answer "which one has the smallest number".
    """
    for entry in reversed(self.transcript):
      at = entry.get("at")
      if at:
        return float(at)
    return self.file_at

  def append(self, kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
    entry = {"kind": kind, "seq": len(self.transcript), "at": time.time(),
             "thread": self.thread, "run": self.run_id, **data}
    self.transcript.append(entry)
    if kind == "user" and not self.title:
      self.title = _title(data.get("text"))
    return entry

  @property
  def handle(self) -> str:
    """How the **provider** is addressed for this conversation: ``run#thread``.

    Deliberately not ``run_id``. One run holds several conversations and each
    is its own provider session, so the bare run id identifies no conversation
    at all -- passing it makes every message come back "no session; call
    start() first", a mistake that is easy to miss because it needs
    credentials to reproduce. There is one spelling of this and everything
    that talks to the provider uses it.
    """
    return self.spec.run_id

  @property
  def state(self) -> str:
    """The one thing you must be able to see without opening it.

    ``needs_you`` outranks ``working`` because a blocked conversation is not
    making progress and only you can unblock it.
    """
    if self.pending:
      return "needs_you"
    if self.busy:
      return "working"
    return "idle" if self.transcript else "empty"

  def describe(self) -> Dict[str, Any]:
    return {
        "run": self.run_id,
        "thread": self.thread,
        "title": self.title or "new conversation",
        "state": self.state,
        "messages": sum(1 for e in self.transcript if e["kind"] in ("user", "text")),
        "pending": len(self.pending),
        "last_at": self.last_at,
        # When the running turn started; absent unless it is running.
        "since": self.busy_since if self.busy else None,
        # `None` until it has been started, which is not the same as `False`:
        # a conversation nobody has spoken to yet has no tools *and* no fault.
        "tools": None if self.preflight is None else self.preflight.attached,
        # The provider's session behind this conversation, when known: the id
        # `claude --resume` takes, so the menu can offer the terminal a way in.
        "session_id": self.session_id,
        "session_provider": self.session_provider,
        "session_cwd": self.session_cwd or self.spec.cwd,
    }
