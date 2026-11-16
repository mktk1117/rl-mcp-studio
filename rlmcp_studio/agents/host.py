"""Agent sessions as addressable resources -- one per run.

Not a chat widget. A run's conversation is a thing with a lifetime: it is
created lazily, it survives a browser reload, and it still knows what happened
last night when you come back in the morning. That is why the transcript lives
here and not in the client.

The host owns three jobs the provider deliberately does not:

* **Context.** What this run claims, what would falsify it, what changed since
  its parent -- built from the record, so the agent starts knowing the
  experiment rather than being told each time.
* **Transcript.** Every event, kept, so a reconnecting client is caught up
  rather than shown an empty pane.
* **Fan-out.** Events reach the browser through the same envelope everything
  else uses; nothing about the agent needs its own transport.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from rlmcp_studio.agents.base import (
    AgentEvent, Decision, NotSupported, PermissionRequest, SessionSpec,
)
from rlmcp_studio.agents.claude import ClaudeProvider
from rlmcp_studio.agents import auth as cli_auth, preflight
from rlmcp_studio.agents.store import Transcripts
# Re-exported: callers import these from the host.
from rlmcp_studio.agents.run_context import build_context  # noqa: F401
from rlmcp_studio.agents.session import DEFAULT_THREAD, AgentSession, _title, key

Publish = Callable[[str, Dict[str, Any], str], Awaitable[None]]

#: How long the provider is given to *accept* an interrupt before the studio
#: decides it cannot interrupt at all and closes the session instead.
STOP_ACCEPTED = 10.0

#: How long the interrupted turn is given to end before the studio says it was
#: stopped anyway. Longer than a turn takes to stop and shorter than a person
#: waits before pressing the button a second time.
STOP_ADMITTED = 8.0

#: What a stopped turn is called in the transcript. One spelling, because it is
#: said from two places -- the end of the turn, and the deadline for a turn
#: that does not end.
_STOPPED_WORKING = {"text": "You stopped it.", "was_working": True}

#: How long an answer from `claude auth status` is trusted without asking
#: again. Sign-in state does not change on the timescale the GUI polls at.
AUTH_TTL = 15.0


def _who(entries: List[Dict[str, Any]], stem: str) -> "tuple[str, str]":
  """Whose transcript this is: what its entries say, else what its file says.

  Every entry names its run and thread; the split on the file name is for a
  file that somehow has none.
  """
  for entry in entries:
    run, thread = entry.get("run"), entry.get("thread")
    if run and thread:
      return str(run), str(thread)
  run_id, thread = stem.split("#", 1)
  return run_id, thread


#: The Messages API's cap on one request body. Over it, the connection is
#: dropped rather than answered.
REQUEST_LIMIT_BYTES = 32 * 1024 * 1024
#: Warn well before the cap: a turn adds screenshots as it goes, and the
#: request that crosses the line is the one that fails.
HEAVY_BYTES = 20 * 1024 * 1024
#: Or when the context is this full.
HEAVY_FRACTION = 0.7


class AgentHost:
  """Every live agent session, keyed by run."""

  def __init__(self, publish: Publish, provider: Optional[Any] = None,
               transcripts: Optional[Transcripts] = None,
               records_root: str = "",
               providers: Optional[Dict[str, Any]] = None,
               current: str = "",
               studio_url: str = "",
               host_id: str = "",
               studio_token: str = ""):
    self.publish = publish
    # Where this studio answers HTTP, for the fleet server the agent gets
    # beside rlmcp's (`agents/fleet_mcp.py`); empty means no fleet tools.
    # The token is what that server presents (guard.py); it travels in the
    # subprocess's environment and nowhere a page could read it.
    self.studio_url = studio_url
    self.studio_token = studio_token
    self.host_id = host_id
    # Every provider this studio can drive, by name, and the one in force.
    # `provider` alone is accepted too: one provider, which is then the
    # current one.
    self.providers: Dict[str, Any] = dict(providers or {})
    if provider is not None:
      self.providers.setdefault(getattr(provider, "name", "claude"), provider)
    if not self.providers:
      self.providers["claude"] = ClaudeProvider()
    chosen = current if current in self.providers else next(iter(self.providers))
    self.provider = self.providers[chosen]
    """The provider new conversations get. Open ones keep their own."""
    # The records root is passed down, not kept here: it is not this host's
    # business where a transcript file sits, only that two studios over two
    # task folders do not share one. See `store.workspace_key`.
    self.transcripts = (transcripts if transcripts is not None
                        else Transcripts(workspace=records_root))
    self.sessions: Dict[str, AgentSession] = {}
    self._loop: Optional[asyncio.AbstractEventLoop] = None
    for each in self.providers.values():
      each.on_permission(self._on_permission)
      if hasattr(each, "on_event"):
        each.on_event(self._on_event)
    self.login = cli_auth.LoginFlow()
    self._auth_at: Dict[str, float] = {}
    """When each provider's `auth_state` was last asked for, monotonic."""

  # ── which provider ───────────────────────────────────────────────────
  def choose_provider(self, name: str) -> str:
    """Make `name` the provider for conversations started from now on."""
    if name not in self.providers:
      raise ValueError(f"no such provider: {name!r}")
    self.provider = self.providers[name]
    return name

  def describe_providers(self) -> List[Dict[str, Any]]:
    """Every provider, whether it could answer, and why not -- for the menu.

    Availability is read from each provider's *cached* auth state, not
    re-asked here: this is drawn on every status poll, and one of them
    spawns a subprocess to answer.
    """
    rows = []
    for name, each in self.providers.items():
      try:
        reason = each.unavailable_reason()
      except Exception as exc:              # noqa: BLE001 - listed, not fatal
        reason = str(exc)
      rows.append({"name": name, "label": getattr(each, "label", name),
                   "available": not reason, "reason": reason,
                   "current": each is self.provider})
    return rows

  def _of(self, session: AgentSession) -> Any:
    """The provider this conversation talks to."""
    return session.provider or self.provider

  # ── status ───────────────────────────────────────────────────────────
  async def refresh_auth(self, force: bool = False) -> Dict[str, Any]:
    """Re-read the CLI's auth state.

    Not as cheap as it looks: every call spawns `claude auth status`, and the
    GUI asks for `/api/agent` on a timer. Two things follow.

    **Answers are cached for a few seconds.** Sign-in state does not change
    between two polls a second apart, and a subprocess per poll is load that
    makes the next problem more likely. `force` is for the moments it really
    can have changed -- the end of a sign-in flow.

    **A failure to ask is not an answer.** When the CLI times out or will not
    spawn, `cli_auth.status` says so with `answered: False`; overwriting a
    known-good state with that would report the user as signed out, which
    disables the composer *while they are typing in it* -- the box goes dead
    and loses focus mid-sentence. Keep the last real answer and mark it stale
    instead.
    """
    await self.login.reap_if_stale()
    now = time.monotonic()
    if (not force and self.provider.auth_state.get("answered")
        and now - self._auth_at.get(self.provider.name, 0.0) < AUTH_TTL):
      return self.provider.auth_state

    # A provider that knows how to answer for itself does; the Claude CLI's
    # `auth status` is the answer for a provider that does not.
    answer = await self._ask_auth(self.provider, force)
    self._auth_at[self.provider.name] = now
    # The others, once: the provider menu lists every provider with whether
    # it could answer, and a provider nobody has picked yet has never been
    # asked -- so without this the menu would call a signed-in provider "not
    # signed in" until it was chosen, which is the moment the answer stops
    # mattering.
    for other in self.providers.values():
      if other is not self.provider and not other.auth_state:
        other.auth_state = await self._ask_auth(other, False)
        self._auth_at[other.name] = now
    previous = self.provider.auth_state
    if not answer.get("answered") and previous.get("loggedIn"):
      self.provider.auth_state = {
          **previous, "stale": answer.get("reason") or "could not ask the CLI"}
    else:
      self.provider.auth_state = answer
    return self.provider.auth_state

  async def _ask_auth(self, provider: Any, force: bool) -> Dict[str, Any]:
    """One provider's sign-in state, asked the way that provider answers."""
    ask = getattr(provider, "auth_status", None)
    if ask is None:
      return await cli_auth.status()
    try:
      return await ask(force=force)
    except TypeError:
      return await ask()

  def status(self) -> Dict[str, Any]:
    reason = self.provider.unavailable_reason()
    return {
        "provider": self.provider.name,
        "available": not reason,
        "reason": reason,
        "sessions": sorted(self.sessions),
        "conversations": self.conversations(),
        "attention": self.attention(),
        "auth": dict(self.provider.auth_state),
        "cli": cli_auth.cli_present(),
        "login": self.login.describe(),
        "providers": self.describe_providers(),
        # What the provider offers and what is in force. Read from the
        # provider rather than assembled here, so a second one answering the
        # same shape needs no change in this file or in the browser. A
        # provider with no menus to offer simply offers none.
        **(self.provider.settings() if hasattr(self.provider, "settings") else {}),
    }

  def transcript(self, run_id: str, thread: str = DEFAULT_THREAD) -> List[Dict[str, Any]]:
    handle = key(run_id, thread)
    session = self.sessions.get(handle)
    if session is not None:
      return list(session.transcript)
    # Not in memory: it may still be on disk from before a restart. Reading it
    # here is what makes "come back in the morning" true.
    return self.transcripts.load(handle)

  def restore(self, known_runs: Optional[Iterable[str]] = None) -> int:
    """Bring every saved conversation back as a readable transcript.

    The provider's session is *not* restored -- it went with the process -- so
    these come back able to be read and not to be continued from. `ensure`
    marks the break when one is spoken to again.
    """
    # Conversations are scoped to a records root, so run 007 here is not run
    # 007 in another task folder. This filter is still needed: a workspace
    # outlives the records it was named for, and listing a conversation about
    # a run this rail cannot show is still the wrong answer.
    allowed = set(known_runs) if known_runs is not None else None
    found = 0
    for stem in self.transcripts.keys():
      if "#" not in stem:
        continue
      entries = self.transcripts.load(stem)
      if not entries:
        continue
      # The file name is a *safe* spelling of the key -- `task:Foo#report`
      # is written as `task_Foo#report.jsonl` -- so it cannot be read back
      # as the key: a session keyed by the stem answered to a name nothing
      # asks for. The entries can be: every one names its run and thread.
      run_id, thread = _who(entries, stem)
      handle = key(run_id, thread)
      if handle in self.sessions:
        continue
      # A conversation about a task -- Build's, or the report thread -- is
      # scoped by the registry, not by the rail, and the rail's list never
      # names a task. Filtering them by it would silently drop every one of
      # them at every boot.
      if allowed is not None and run_id not in allowed and not run_id.startswith("task:"):
        continue
      spec = SessionSpec(run_id=handle, cwd="", system_context="", mcp_servers={})
      session = AgentSession(run_id, spec, thread)
      session.transcript = entries
      session.restored = True
      try:
        session.file_at = self.transcripts.path(handle).stat().st_mtime
      except (OSError, AttributeError):
        session.file_at = 0.0
      for entry in entries:
        if entry.get("kind") == "user" and not session.title:
          session.title = _title(entry.get("text"))
        if entry.get("kind") == "session" and entry.get("session_id"):
          # The newest wins: a conversation resumed twice has said `session`
          # twice, and the last one names the file that holds all of it.
          session.session_id = str(entry.get("session_id") or "")
          session.session_provider = str(entry.get("provider") or "")
          session.session_cwd = str(entry.get("cwd") or "")
      self.sessions[handle] = session
      found += 1
    return found

  def attach(self, run_id: str, session_id: str, provider: str = "claude",
             thread: str = DEFAULT_THREAD, cwd: str = "") -> AgentSession:
    """Point a conversation at a provider session that already exists.

    The session id of a Claude Code conversation begun somewhere else -- a
    terminal, the desktop app, this studio before a reinstall -- becomes what
    this conversation resumes from on its next message. Nothing is started
    here: `ensure` does that when there is something to say, with the run's
    own context and tools, exactly as after a restart. A provider session
    already open on this conversation is closed first, because the point of
    attaching is to continue *that* one.
    """
    handle = key(run_id, thread)
    session = self.sessions.get(handle)
    if session is None:
      spec = SessionSpec(run_id=handle, cwd=cwd, system_context="", mcp_servers={})
      session = AgentSession(run_id, spec, thread)
      self.sessions[handle] = session
    if session.started:
      asyncio.create_task(self._of(session).stop(handle))
      session.started = False
    session.session_id = session_id
    session.session_provider = provider
    session.session_cwd = cwd
    session.restored = True
    session.break_note = (
        f"Attached {provider} session {session_id[:8]}…: the next message "
        f"continues that conversation here, with its memory.")
    entry = session.append("session", {"provider": provider, "session_id": session_id,
                                       "cwd": cwd, "attached": True})
    self.transcripts.append(handle, entry)
    return session

  def conversations(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every conversation, or one run's.

    Sorted so the ones that want something come first: a permission prompt
    nobody sees is the same as no permission prompt. Within a state, the one
    spoken to most recently comes first -- a dozen idle conversations in run
    order would make the one from a minute ago as findable as one from last
    week.
    """
    order = {"needs_you": 0, "working": 1, "idle": 2, "empty": 3}
    rows = [s.describe() for s in self.sessions.values()
            if run_id is None or s.run_id == run_id]
    return sorted(rows, key=lambda r: (order.get(r["state"], 9), -r["last_at"],
                                       r["run"], r["thread"]))

  def attention(self) -> Dict[str, int]:
    """A count for the chrome, so a blocked run is visible from any view."""
    rows = self.conversations()
    return {
        "needs_you": sum(1 for r in rows if r["state"] == "needs_you"),
        "working": sum(1 for r in rows if r["state"] == "working"),
        "total": len(rows),
    }

  # ── lifecycle ────────────────────────────────────────────────────────
  async def ensure(self, run_id: str, context: str, cwd: str,
                   session_dir: str, thread: str = DEFAULT_THREAD,
                   free_paths: Optional[List[str]] = None,
                   records_root: str = "") -> AgentSession:
    handle = key(run_id, thread)
    session = self.sessions.get(handle)
    spec = self._spec(handle, context, cwd, session_dir, free_paths, records_root)
    if session is None:
      session = AgentSession(run_id, spec, thread)
      self.sessions[handle] = session
    elif session.started and spec.mcp_servers and not session.spec.mcp_servers:
      # The run was launched *after* this conversation was opened. The spec is
      # fixed once the provider has been started with it, so this conversation
      # is permanently toolless while the screen beside it shows a run that is
      # plainly training -- and the note it was given when it opened, which
      # said there was nothing running, is now wrong as well. Only the studio
      # holds both halves, so only the studio can say it. Once, hence the flag.
      if not session.told_arrived:
        session.told_arrived = True
        await self._emit(session, "preflight",
                         preflight.arrived(session_dir).notice())
    elif not session.started:
      # A restored conversation was built from a transcript on disk, which
      # knows nothing about a session directory: its spec had no cwd and no
      # MCP servers. Kept as is, every conversation that outlived a studio
      # restart would come back permanently toolless -- the agent would search
      # for `mcp__rlmcp__take_screenshot`, find nothing, and report,
      # reasonably, that rlmcp was not attached. So the spec is rebuilt while
      # the provider has not been started with it yet.
      session.spec = spec
    if not session.started:
      # The provider in force *now*, decided before the note below, because
      # the note depends on whether this provider can pick the conversation
      # back up: only the one that issued the session id can resume it.
      session.provider = self.provider
      resumable = bool(session.session_id) and (
          getattr(self.provider, "name", "") == session.session_provider)
      if session.restored and resumable:
        session.spec.resume = session.session_id
        session.resuming = True
        await self._emit(session, "restart", {
            "text": session.break_note
                    or (f"The studio restarted. This conversation continues "
                        f"{_provider_label(session.session_provider)} session "
                        f"{session.session_id[:8]}…, so the agent's memory of "
                        f"what is above is kept."),
            "resumed": session.session_id,
        })
        session.restored = False
        session.break_note = ""
      elif session.restored:
        # Said out loud, once, in the transcript itself: everything above this
        # line was a different context. A pane that showed an unbroken
        # conversation whose second half cannot remember its first would be
        # lying by omission.
        await self._emit(session, "restart", {
            "text": session.break_note
                    or "The studio restarted. What is above is kept, but the "
                       "agent's memory of it is not: this message starts a new "
                       "context.",
        })
        session.restored = False
        session.break_note = ""
      # What this conversation is actually being given, checked against what
      # its prompt is about to claim. Said here, once, before the provider is
      # started: the person watching is the one who can do something about a
      # run that was never launched, and the alternative is the agent working
      # it out mid-turn by reading its own `/proc` entry.
      session.preflight = preflight.check(
          session.spec, getattr(session.provider, "mcp_command", ""))
      if session.preflight.findings:
        await self._emit(session, "preflight", session.preflight.notice())
      await session.provider.start(session.spec)
      session.started = True
    return session

  def _spec(self, handle: str, context: str, cwd: str, session_dir: str,
            free_paths: Optional[List[str]] = None,
            records_root: str = "") -> SessionSpec:
    """What a conversation on this run is about.

    One builder, because the Briefing window's whole claim is that it shows
    what a conversation *would* be started with -- and a second copy of this
    is how that claim quietly stops being true.
    """
    # A task-scoped conversation (`task:<id>`) tells the fleet server which
    # task its tools default to; a run's conversation says the task in its
    # briefing and names it per call.
    task = handle[len("task:"):] if handle.startswith("task:") else ""
    return SessionSpec(
        run_id=handle,            # the provider keys on this, so threads differ
        cwd=cwd,
        system_context=context,
        mcp_servers=self._mcp_for(session_dir, records_root, task=task),
        free_paths=list(free_paths or []),
    )

  def briefing(self, run_id: str, context: str, cwd: str, session_dir: str,
               thread: str = DEFAULT_THREAD,
               free_paths: Optional[List[str]] = None,
               records_root: str = "") -> Dict[str, Any]:
    """What this conversation was given, or what the next one would be.

    Both answers, and which one it is matters enough to be a field: the harness
    is fixed when a session connects, so a conversation open since before the
    setting moved is running on the older text. `live` is how the window says
    "this is the prompt above you" rather than "this is what you would get".

    A provider with no opinion about prompts answers nothing rather than
    raising: the panel's Briefing button is a nicety, and a provider that
    cannot describe itself should cost that button and not the conversation.
    """
    given = self.given(run_id, thread)
    if given is not None:
      return {**given, "live": True, "run": run_id, "thread": thread}
    build = getattr(self.provider, "briefing", None)
    if build is None:
      return {"live": False, "run": run_id, "thread": thread, "sections": [],
              "unsupported": f"{self.provider.name} does not describe its prompt"}
    spec = self._spec(key(run_id, thread), context, cwd, session_dir,
                      free_paths, records_root)
    return {**build(spec), "live": False, "run": run_id, "thread": thread}

  def given(self, run_id: str, thread: str = DEFAULT_THREAD) -> Optional[Dict[str, Any]]:
    """What an open conversation was started with, if one is open.

    Asked before the prospective answer is composed, and that is not only
    tidiness: composing one means reading the run's own numbers off disk, and a
    window that shows what is already on screen should not cost that.
    """
    session = self.sessions.get(key(run_id, thread))
    who = self._of(session) if session is not None else self.provider
    ask = getattr(who, "given", None)
    return ask(key(run_id, thread)) if ask is not None else None

  def _mcp_for(self, session_dir: str, records_root: str = "",
               task: str = "") -> Dict[str, Any]:
    """rl-mcp's own server, pinned to this run *and* to this run's records.

    Pinned rather than pointed at a root: the server would otherwise choose the
    newest session on startup and never retarget, which is the right default
    for a terminal and the wrong one for a GUI where the user picks the run.

    ``--records-root`` is the other half of that, and leaving it out is a
    silent data bug. The record tools take a *bare id* -- `002` -- and with no
    root the server resolves it the CLI's way: `$RLMCP_RECORDS`, then
    `./records` under its own working directory. On a machine with two records
    roots that is somebody else's `002`: a headline written from the studio
    would report success, not appear on the run it was about, and land in an
    unrelated experiment in another store.
    """
    servers: Dict[str, Any] = {}
    if session_dir:
      args = ["--session", str(Path(session_dir))]
      if records_root:
        args += ["--records-root", str(Path(records_root))]
      servers["rlmcp"] = {
          "type": "stdio",
          "command": self.provider.mcp_command,
          "args": args,
      }
    # The fleet, beside the run: what is training where, what
    # has been tried, and launching -- the one act a server pinned inside an
    # existing run cannot do. Attached whether or not there is a session,
    # because choosing the next run is exactly what a conversation with no
    # run yet is for.
    if self.studio_url:
      from rlmcp_studio.agents import fleet_mcp
      command, *rest = fleet_mcp.argv()
      servers[fleet_mcp.NAME] = {
          "type": "stdio",
          "command": command,
          "args": rest,
          "env": {"RLMCP_STUDIO_URL": self.studio_url,
                  "RLMCP_STUDIO_TASK": task,
                  "RLMCP_STUDIO_HOST": self.host_id,
                  "RLMCP_STUDIO_TOKEN": self.studio_token},
      }
    return servers

  # ── the turn ─────────────────────────────────────────────────────────
  async def send(self, run_id: str, text: str, context: str = "",
                 cwd: str = "", session_dir: str = "",
                 thread: str = DEFAULT_THREAD,
                 images: Optional[List[bytes]] = None,
                 shows: str = "",
                 free_paths: Optional[List[str]] = None,
                 records_root: str = "",
                 interrupt: bool = False) -> None:
    """Say something to this run's agent, optionally holding up a picture.

    ``shows`` is the path of what the images are, carried into the transcript
    so a reconnecting client can render the thumbnail rather than a message
    that refers to an image nobody can see.

    ``interrupt`` is the composer typing over a turn that is still running.
    You type *because* it is going wrong, and the message is the correction:
    so the turn is ended first, the studio waits for it to admit it ended,
    and only then does the correction go in -- under "You stopped it." and
    after whatever the model had already produced, which is the only order
    a reader can follow. Not a queue: nothing is held for later, and a
    message that could not be sent is reported rather than kept.
    """
    session = await self.ensure(run_id, context, cwd, session_dir, thread,
                                free_paths=free_paths, records_root=records_root)
    if session.busy:
      if not interrupt:
        raise RuntimeError("this run's agent is still working on the last message")
      await self.stop(run_id, thread)
      await self._turn_over(session)
      # A provider that could only stop by closing the session left this one
      # unstarted; `ensure` opens it again and marks the break.
      session = await self.ensure(run_id, context, cwd, session_dir, thread,
                                  free_paths=free_paths, records_root=records_root)

    if session.stopping:
      # You stopped it and said what it should have done instead, which is the
      # whole point of the button. The note goes above your correction rather
      # than arriving in the middle of the turn your correction starts.
      session.stopping = False
      await self._emit(session, "stopped", _STOPPED_WORKING)

    await self._emit(session, "user", {"text": text, "shows": shows,
                                       **_attached_weight(images)})
    session.auth_flagged = False
    session.drop_flagged = False
    session.busy = True
    try:
      await self._of(session).send(session.handle, text, images=images)
    except Exception:
      # Nothing was said, so nothing is coming: the turn that would have
      # cleared this flag never starts. Leaving it set is a conversation the
      # composer refuses for the rest of the session.
      session.busy = False
      await self._emit(session, "idle", {})
      raise

  async def _turn_over(self, session: AgentSession) -> None:
    """Wait for a stopped turn to admit it ended, or for the deadline.

    `stop` returns as soon as the provider *accepts* the interrupt; the turn's
    tail and its `done` come after. `_on_event` clears `stopping` on the
    `done`, and `_stopped_anyway` clears it at `STOP_ADMITTED` if none comes,
    so either way this returns. Polled rather than signalled: the flag is the
    one source of truth and two places already write it.
    """
    deadline = time.monotonic() + STOP_ADMITTED + 2.0
    while session.stopping and time.monotonic() < deadline:
      await asyncio.sleep(0.02)

  async def _on_event(self, handle: str, event: AgentEvent) -> None:
    """Everything the provider says, whether or not this host asked for it.

    The provider reads its stream for the whole life of a session, so events
    arrive here from turns nobody in the studio started -- the CLI answering a
    background shell that finished, most often. They are the same events and
    they go to the same place; the only difference is that one of them arrives
    while this conversation believes it is idle, and that is exactly what
    `busy` should have been saying.

    Turn-shaped state lives here rather than in `send` for that reason: a turn
    ends when the provider says a turn ended, not when the call that started
    one returns.

    A turn being stopped is the exception, and it is the whole of `stopping`.
    The words the model had already produced arrive after the interrupt does,
    so left alone they would make the panel say "working…" again two seconds
    after you pressed the button that was supposed to end it -- and the CLI's
    own "[Request interrupted by user]" would come through as `wake`, a card
    that means *the agent did something nobody asked for*, about the one
    event in this app that the person themselves caused.
    """
    session = self.sessions.get(handle)
    if session is None:
      return
    if event.kind == "context":
      # A measurement, not something said: kept on the session and carried
      # on `done`, never written to the transcript.
      session.context_tokens = int(event.data.get("tokens") or 0)
      return
    if event.kind == "session":
      if event.data.get("session_id") and event.data.get("session_id") != session.session_id:
        session.heavy_warned = False           # a new context starts light
      session.session_id = str(event.data.get("session_id") or session.session_id)
      session.session_provider = str(event.data.get("provider") or session.session_provider)
      session.session_cwd = str(event.data.get("cwd") or session.session_cwd)
      session.resuming = False
      await self._emit(session, event.kind, event.data)
      return
    if session.resuming and event.kind == "error":
      # The first thing back from a resumed start was an error: the session
      # file is gone, or the CLI would not open it. Say so, forget the id, and
      # let the next message start fresh -- rather than resuming into the same
      # error on every message from now on.
      session.resuming = False
      await self._emit(session, "error", event.data)
      await self._of(session).stop(handle)
      session.started = False
      session.restored = True
      # A new spec, not the old one edited: the provider was handed that
      # object, and what it was started with should stay what it says.
      session.spec = dataclasses.replace(session.spec, resume="")
      session.session_id = ""
      session.break_note = (
          f"Resuming the previous {_provider_label(session.session_provider)} "
          "session failed (see above), so the next message starts a new "
          "context. What is above is kept.")
      session.busy = False
      await self._emit(session, "idle", {})
      return
    if session.stopping and _is_interrupt_notice(event):
      # Said once, in our words, below the text it cut off. See `stop`.
      return
    if event.kind != "done" and not session.busy and not session.stopping:
      session.busy = True
    if event.kind == "done":
      event = AgentEvent("done", {**event.data, "usage": {
          **(event.data.get("usage") or {}), **self.weight(session)}})
    await self._emit(session, event.kind, event.data)
    if _is_expiry_notice(event) and not session.auth_flagged:
      session.auth_flagged = True
      await self._auth_expired(session, event)
    if _is_connection_drop(event) and not session.drop_flagged:
      session.drop_flagged = True
      await self._emit(session, "heavy", {**self.weight(session), "dropped": True})
    elif event.kind == "done" and not session.heavy_warned:
      weight = self.weight(session)
      if (weight["request_bytes"] >= HEAVY_BYTES
          or (weight["window"] and weight["context_tokens"] >= HEAVY_FRACTION * weight["window"])):
        session.heavy_warned = True
        await self._emit(session, "heavy", {**weight, "dropped": False})
    if event.kind == "done":
      session.busy = False
      if session.stopping:
        session.stopping = False
        await self._emit(session, "stopped", _STOPPED_WORKING)
      await self._emit(session, "idle", {})

  async def decide(self, run_id: str, request_id: str, decision: str,
                   note: str = "", thread: str = DEFAULT_THREAD) -> None:
    session = self.sessions.get(key(run_id, thread))
    if session is None:
      raise NotSupported(f"no agent session for {run_id}#{thread}")
    session.pending.pop(request_id, None)
    await self._emit(session, "decision",
                     {"request_id": request_id, "decision": decision, "note": note})
    await self._of(session).decide(session.handle, request_id, Decision(decision), note)

  async def stop(self, run_id: str, thread: str = DEFAULT_THREAD) -> bool:
    """Stop what the agent is doing now. Returns whether it was doing anything.

    The conversation survives, and that is the difference between this and
    closing it. A runaway is stopped in order to be *corrected* -- "you have
    read for four minutes and written nothing; write these three files" -- and
    a sentence like that only means something in the context it interrupts.
    So the provider is asked to end the turn and the session stays open.

    Popping the session would cost more than the context. The transcript in
    memory would go with it, `ensure` would build a fresh one on the next
    message, and its entries would start again from ``seq`` 0 -- which the
    client de-duplicates against the entries it already has, so the first
    thing said after a stop would be silently dropped from the screen. The
    words would stay on disk, and the pane would stop showing them.

    A provider that cannot interrupt falls back to closing its session: the
    conversation is still readable and still writable, it simply starts a new
    context, which is exactly the restart case and is marked the same way.

    **"You stopped it." is not said here.** An interrupt is a request to a
    process that is mid-sentence, and what it had already produced arrives
    after it -- so the note said at this moment ends up above three paragraphs
    the agent appears to have written after being stopped. It is said where it
    is true instead: when the turn ends, when the user's correction goes in, or
    at a deadline if neither happens. The only note said here is the one for a
    stop that stopped nothing, which is complete already.
    """
    handle = key(run_id, thread)
    session = self.sessions.get(handle)
    if session is None:
      return False
    was_working = session.busy
    session.stopping = was_working
    try:
      # Bounded, because this is a request *to another process* and the button
      # that made it is the one the user presses when something is already not
      # answering. A provider that never accepts the interrupt is a provider
      # that cannot interrupt, and that case has an answer below.
      await asyncio.wait_for(self._of(session).interrupt(handle), STOP_ACCEPTED)
    except Exception:                         # noqa: BLE001 - the fallback is the point
      session.stopping = False
      await self._of(session).stop(handle)
      # The provider forgot the conversation; `ensure` has to start a new one,
      # and say so in the transcript rather than let the agent answer the next
      # message as though it remembered the last four minutes.
      session.started = False
      session.restored = True
      session.break_note = (
          "Stopping it closed the conversation, because this provider cannot "
          "end a turn any other way. What is above is kept; the agent's memory "
          "of it is not, so say what you want again in full.")
    # Whatever the turn does next, nobody is waiting on it: a permission
    # request left pending would keep the conversation showing "needs you"
    # about a tool call that has been abandoned.
    session.pending.clear()
    session.busy = False
    if session.stopping:
      # Nothing to say yet. The turn is still emptying its mouth, and "You
      # stopped it." above the last three sentences it produced reads as an
      # agent that carried on after being stopped. `_on_event` says it when
      # the turn ends; this is the deadline for a turn that never does.
      asyncio.create_task(self._stopped_anyway(session))
    else:
      await self._emit(session, "stopped", {
          "text": "You stopped it." if was_working
                  else "Nothing was running when you pressed stop.",
          "was_working": was_working,
      })
    return was_working

  async def _stopped_anyway(self, session: AgentSession) -> None:
    """Say a stop happened even if the turn never admits it ended.

    The provider is asked to end a turn and normally answers with `done` a
    moment later, which is where the note belongs. A provider that answers
    with nothing must not cost the user the one line that tells them the
    button worked.
    """
    await asyncio.sleep(STOP_ADMITTED)
    if not session.stopping:
      return
    session.stopping = False
    session.busy = False
    await self._emit(session, "stopped", _STOPPED_WORKING)

  # ── plumbing ─────────────────────────────────────────────────────────
  async def _auth_expired(self, session: AgentSession,
                          event: AgentEvent) -> None:
    """The credential died mid-conversation. Say what to do about it.

    The provider reports this as an ordinary sentence -- "Failed to
    authenticate. API Error: 401 OAuth access token has expired.
    Re-authenticate to continue." -- so drawn as it comes, it tells the user
    to re-authenticate without saying where. The studio's sign-in flow sits
    behind a screen you only see when the app boots signed out, so the card
    brings it to the conversation.

    Two things go out, and they are different: `auth_expired` is a *transcript*
    entry, so the card sits under the turn it killed and is still there
    tomorrow; `agent.auth` carries the freshly-asked auth state, so the rest of
    the app stops believing a sign-in that has expired.
    """
    # `says` is not painted -- the sentence is the entry directly above, and
    # the card is the answer to it rather than a second copy. It is in the
    # record because the transcript file outlives the panel, and "what did the
    # provider actually say the day this happened" is the first question asked
    # of it.
    said = str(event.data.get("text") or event.data.get("message") or "").strip()
    await self._emit(session, "auth_expired",
                     {"says": said[:cli_auth.EXPIRY_LIMIT],
                      "cli": cli_auth.cli_present()})
    # Forced: the cached answer is from before the token died, and this is
    # exactly the moment it can have changed.
    await self.refresh_auth(force=True)
    await self.publish("agent.auth", self.auth_summary())

  def auth_summary(self) -> Dict[str, Any]:
    """The sign-in half of `status()`, on its own.

    `agent.auth` is merged into whatever the client already holds, so it need
    carry only what a sign-in changes -- and the conversation list and the
    provider's menus, which is the rest of `status()`, are not that.
    """
    reason = self.provider.unavailable_reason()
    return {"available": not reason, "reason": reason,
            "auth": dict(self.provider.auth_state),
            "cli": cli_auth.cli_present(),
            "login": self.login.describe()}

  def weight(self, session: AgentSession) -> Dict[str, Any]:
    """How heavy this conversation is to send, as far as the studio can tell.

    Images since the context began, read off the transcript so a restart does
    not forget them; the last request's size in tokens; the model's window;
    and an estimate of the request body, which is what the API actually caps.
    """
    images = image_bytes = 0
    current = ""
    for entry in session.transcript:
      if entry.get("kind") == "session" and entry.get("session_id"):
        if current and entry["session_id"] != current:
          images = image_bytes = 0             # a new context: nothing carried
        current = entry["session_id"]
      images += int(entry.get("images") or 0)
      image_bytes += int(entry.get("image_bytes") or 0)
    window = 0
    provider = self._of(session)
    settings = provider.settings() if hasattr(provider, "settings") else {}
    model = session.spec.model if session.spec else ""
    model = model or str(settings.get("model") or "")
    for row in settings.get("models") or []:
      if row.get("id") == model:
        window = int(row.get("context") or 0)
    tokens = session.context_tokens
    # Text is roughly four bytes a token on the wire; the images' own tokens
    # are small beside their base64, so counting both double-counts little.
    return {"images": images, "image_bytes": image_bytes,
            "context_tokens": tokens, "window": window,
            "request_bytes": image_bytes + 4 * tokens,
            "limit_bytes": REQUEST_LIMIT_BYTES}

  async def _emit(self, session: AgentSession, kind: str, data: Dict[str, Any]) -> None:
    entry = session.append(kind, data)
    self.transcripts.append(key(session.run_id, session.thread), entry)
    await self.publish(f"agent.{kind}", entry, key(session.run_id, session.thread))

  def _on_permission(self, run_id: str, request: PermissionRequest) -> None:
    """Called from the provider's callback, which is inside the event loop.

    ``run_id`` here is the provider's key, which is already ``run#thread``.
    """
    session = self.sessions.get(run_id)
    if session is None:
      return
    payload = {
        "request_id": request.id,
        "tool": request.call.name,
        "args": request.call.args,
        "reason": request.reason,
    }
    session.pending[request.id] = payload
    asyncio.create_task(self._emit(session, "permission", payload))


def _provider_label(provider: str) -> str:
  """What a person calls the app whose session a conversation continues."""
  return {"claude": "Claude Code", "codex": "Codex"}.get(provider, provider or "the agent's")


def _attached_weight(images: Optional[List[bytes]]) -> Dict[str, int]:
  """What a picture sent with a message weighs once it is base64 in a request."""
  if not images:
    return {}
  return {"images": len(images),
          "image_bytes": sum(4 * ((len(b) + 2) // 3) for b in images)}


def _is_connection_drop(event: AgentEvent) -> bool:
  """Did the provider give up because the connection kept being dropped?

  What the API does with a request over its size cap: no status, no reason,
  a reset -- which the CLI retries ten times and then reports as the turn's
  last words. It can also be the network; the card says both.
  """
  if event.kind not in ("text", "error"):
    return False
  said = str(event.data.get("text") or event.data.get("message") or "")
  return ("ECONNRESET" in said or "Connection dropped" in said
          or "request_too_large" in said or "Request too large" in said)


def _is_expiry_notice(event: AgentEvent) -> bool:
  """Is the provider telling us the credential is gone rather than answering?

  Both kinds, because the CLI has sent it as either: an assistant `text` when
  the turn is replaced by the failure, an `error` when the SDK raised instead.
  """
  if event.kind not in ("text", "error"):
    return False
  return cli_auth.looks_expired(
      str(event.data.get("text") or event.data.get("message") or ""))


def _is_interrupt_notice(event: AgentEvent) -> bool:
  """Is this the provider telling us what we just told it?

  The CLI answers an interrupt with a user-role message reading "[Request
  interrupted by user]", which reaches the studio as `wake` -- the kind that
  means *the agent said something nobody here asked for*, and which the panel
  labels "WOKE ITSELF". It is the exact opposite of true about the one event in
  this app the person unambiguously caused, and the studio already has its own
  sentence for it.
  """
  if event.kind != "wake":
    return False
  return "interrupted by user" in str(event.data.get("text") or "").lower()
