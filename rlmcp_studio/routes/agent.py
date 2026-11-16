"""The agent panel: status and settings, sign-in, conversations, and messages."""

from __future__ import annotations

import base64
import binascii
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rlmcp_studio import briefs
from rlmcp_studio import settings as settings_store
from rlmcp_studio.agents import external as external_sessions
from rlmcp_studio.agents import policy as agent_policy
from rlmcp_studio.api import validate
from rlmcp_studio.context import Studio

MAX_SHOT_CHARS = 12_000_000
"""How large an attached image may be, base64-encoded.

About 9 MB of pixels -- comfortably a 4K frame, and a bound rather than a
guess: this is a data url arriving on one request, and without
a limit a page can make this process allocate whatever it likes.
"""


def annotate_tasks(rows: List[Dict[str, Any]], task_of_run) -> List[Dict[str, Any]]:
  """Give every conversation row a `task`: a task-scoped one says it in its
  id; a run's is looked up once per distinct run through ``task_of_run``."""
  seen: Dict[str, str] = {}
  for row in rows:
    run = str(row.get("run") or "")
    if run.startswith("task:"):
      row["task"] = run[len("task:"):]
      continue
    if run not in seen:
      seen[run] = str(task_of_run(run) or "")
    row["task"] = seen[run]
  return rows


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the agent routes."""
  cfg = studio.cfg
  library = studio.library
  hub = studio.hub
  agents = studio.agents

  # ── the agent ─────────────────────────────────────────────────────────
  @app.get("/api/agent")
  async def api_agent_status(force: bool = False) -> JSONResponse:
    """Whether an agent can run at all, and the tool policy it would use.

    ``force`` is the sign-in panel's "check again". A sign-in done outside this
    app -- in the studio's own terminal, most often -- lands while the cached
    answer still says signed out, and a button that reports what the CLI said
    fifteen seconds ago is a button that looks broken.
    """
    await agents.refresh_auth(force=force)
    mode = getattr(getattr(agents, "provider", None), "mode", "manual")
    return JSONResponse({**agents.status(),
                         "policy": agent_policy.describe(mode)})

  @app.post("/api/agent/settings")
  async def api_agent_settings(body: Dict[str, Any]) -> JSONResponse:
    """Choose the model, or how much runs without asking.

    Saved to the settings file as well, because this is a preference and the
    file is the half that follows *you* rather than one browser -- the same
    argument the theme and the layout make.

    It takes effect on the conversation already open as well: the permission
    handler reads the mode per call, and `adopt_live()` hands
    the same choice to the SDK sessions already connected. The reply says
    which of the two happened rather than letting the button imply either.
    """
    body = body or {}
    provider = body.get("provider")
    model = body.get("model")
    mode = body.get("mode")
    harness = body.get("harness")
    signin = body.get("signin")
    if provider is not None:
      provider = validate.text(provider, "provider", limit=40)
    if signin is not None:
      signin = validate.text(signin, "signin", limit=20)
    if model is not None:
      model = validate.text(model, "model", required=False, limit=80)
    if mode is not None:
      mode = validate.text(mode, "mode", limit=40)
    if harness is not None:
      harness = validate.text(harness, "harness", limit=40)
    try:
      if provider is not None:
        agents.choose_provider(provider)
        # Its cached answer may be from before a key was set, or from
        # never; the switch is the moment somebody is looking.
        await agents.refresh_auth(force=True)
      # The mode and the harness are studio preferences and reach every
      # provider; the model is the one choice that belongs to a provider.
      for each in agents.providers.values():
        if each is not agents.provider and (mode is not None or harness is not None):
          each.choose(mode=mode, harness=harness)
      # The way in is a choice only a provider with more than one has.
      if signin is not None:
        if not getattr(agents.provider, "auth", None):
          return JSONResponse({"error": f"{agents.provider.name} has one way to sign in"},
                              status_code=400)
        agents.provider.choose(auth=signin)
        await agents.refresh_auth(force=True)
      chosen = agents.provider.choose(model=model, mode=mode, harness=harness)
    except ValueError as exc:
      return JSONResponse({"error": str(exc)}, status_code=400)

    live = 0
    adopt = getattr(agents.provider, "adopt_live", None)
    if adopt is not None:
      live = await adopt()

    ui = dict(cfg.ui or {})
    ui["agentProvider"] = agents.provider.name
    # `agentModel` is Claude's key, named when it was the only provider, and
    # renaming it would drop a saved preference on the first start.
    ui["agentModel" if agents.provider.name == "claude"
       else f"{agents.provider.name}Model"] = chosen["model"]
    if "signin" in chosen:
      ui[f"{agents.provider.name}Auth"] = chosen["signin"]
    ui["agentMode"] = chosen["mode"]
    if "harness" in chosen:
      ui["agentHarness"] = chosen["harness"]
    merged, problem = settings_store.patch({settings_store.UI_KEY: ui},
                                           cfg.settings_path)
    if not problem:
      cfg.ui = dict(merged.get(settings_store.UI_KEY) or {})
    return JSONResponse({"ok": True, **chosen, "saved": not problem,
                         "live": live,
                         "available": not agents.provider.unavailable_reason(),
                         "reason": agents.provider.unavailable_reason(),
                         "auth": dict(agents.provider.auth_state),
                         "providers": agents.describe_providers(),
                         "policy": agent_policy.describe(chosen["mode"]),
                         "applies": (f"now, including {live} open "
                                     f"conversation{'' if live == 1 else 's'}")
                                    if live else
                                    "to conversations started from now on",
                         # The harness is the one choice that cannot reach a
                         # conversation already open: a system prompt is given
                         # to the CLI once, at connect, and there is no control
                         # message that replaces it. Said here rather than left
                         # for the user to infer from an agent that goes on
                         # behaving the way it did.
                         "harness_applies": "to conversations started from now "
                                            "on — an open one keeps the prompt "
                                            "it connected with"})

  @app.post("/api/agent/auth/login")
  async def api_agent_login(body: Dict[str, Any]) -> JSONResponse:
    """Begin sign-in. Returns the URL for the user to open; no credential here."""
    flow = await agents.login.start((body or {}).get("mode", "claudeai"))
    await hub.publish("agent.auth", flow)
    return JSONResponse(flow)

  @app.post("/api/agent/auth/code")
  async def api_agent_code(body: Dict[str, Any]) -> JSONResponse:
    """Hand the user's one-time code to the local CLI process, and only to it."""
    flow = await agents.login.submit_code((body or {}).get("code", ""))
    # Sign-in just changed, which is the one moment the cache must not answer.
    state = await agents.refresh_auth(force=True)
    payload = {**flow, "auth": state, **agents.status()}
    await hub.publish("agent.auth", payload)
    return JSONResponse(payload)

  @app.post("/api/agent/auth/cancel")
  async def api_agent_cancel_login() -> JSONResponse:
    flow = await agents.login.cancel()
    await hub.publish("agent.auth", flow)
    return JSONResponse(flow)

  def _retirement() -> Dict[str, Any]:
    """What the pre-scoping migration moved aside, so the panel can say so.

    The move is announced at boot with `print()`, and a studio started detached
    under `uv tool` has no tty -- so the one sentence explaining where somebody's
    whole conversation list went was written to a stdout nobody has. The list
    vanishing with no trace inside the app is what made a working panel look
    broken, so the notice belongs where the list is.
    """
    retired = agents.transcripts.retired
    if not retired:
      return {}
    return {"count": len(retired),
            "path": str(agents.transcripts.root.parent / "_unscoped"),
            "names": retired[:12]}

  _task_of_run: Dict[str, tuple] = {}

  def _run_task(run_id: str) -> str:
    """The task a run id belongs to, remembered for a minute.

    The menu asks on every poll and the answer is a directory read per run;
    a run does not change task, so a minute is generous and still lets a
    record that appears mid-session be named on the next refresh.
    """
    hit = _task_of_run.get(run_id)
    if hit and hit[1] > time.time():
      return hit[0]
    task = ""
    try:
      # Conversations are filed under the primary records root (the host's
      # `records_root`), so when two roots both have a `002`, the primary's
      # is the one these conversations are about; `find` alone would take
      # whichever root came first.
      wanted = validate.identifier(run_id, "record id")
      record = None
      for source in getattr(library, "sources", []):
        if cfg.primary_records and Path(source.root) == Path(cfg.primary_records):
          record = source.get(wanted) if source.available() else None
          break
      if record is None:
        record, _ = library.find(wanted)
      task = str(getattr(record, "task", "") or "") if record is not None else ""
    except Exception:  # noqa: BLE001 -- an unnamed task is a hint missing, not an error
      task = ""
    _task_of_run[run_id] = (task, time.time() + 60)
    return task

  @app.get("/api/agent/conversations")
  def api_conversations(record_id: Optional[str] = None) -> JSONResponse:
    """Every live conversation, or one run's. Ones needing you sort first.

    Each row names its task, because a run id does not: `012` can be a G1
    run in one records root and a Go1 run in another, and a flat list of
    rows from several tasks is one where nobody can find the conversation
    they want. The panel groups by it.
    """
    rows = annotate_tasks(agents.conversations(record_id), _run_task)
    return JSONResponse({"conversations": rows,
                         "attention": agents.attention(),
                         "retired": _retirement()})

  @app.get("/api/agent/{record_id}")
  def api_agent_transcript(record_id: str, thread: str = "main") -> JSONResponse:
    """The conversation so far. A reconnecting client is caught up, not blanked."""
    from rlmcp_studio.agents.host import key as session_key
    session = agents.sessions.get(session_key(record_id, thread))
    return JSONResponse({
        "run": record_id,
        "thread": thread,
        "transcript": agents.transcript(record_id, thread),
        "pending": list(session.pending.values()) if session else [],
        "busy": bool(session and session.busy),
        "conversations": agents.conversations(record_id),
        "tools": _tools_for(record_id, thread, session),
        # Who answers in this conversation: the provider it was started on,
        # or the one it would start on. The pane labels turns with it.
        "provider": (session.provider.name if session is not None and session.provider
                     else agents.provider.name),
    })

  def _tools_for(record_id: str, thread: str, session) -> Dict[str, Any]:
    """What this conversation has, or would be given if it were opened now.

    Asked rather than assumed: a pane that says "the rlmcp tools are attached
    and pinned to this run" over every run in the store would say it about
    runs that were never launched and so have no session for anything to be
    pinned to -- the same check the agent's own prompt relies on, see
    `agents/preflight.py`.

    The prospective spec is built through `_mcp_for`, deliberately: a preview
    assembled any other way could disagree with what a real conversation gets,
    and a second answer that is *nearly* right is worse than none.
    """
    from rlmcp_studio.agents.base import SessionSpec
    from rlmcp_studio.agents import preflight as agent_preflight
    if session is not None and session.preflight is not None:
      return session.preflight.notice()
    record = None
    if record_id and not record_id.startswith("task:"):
      try:
        record, _ = library.find(record_id)
      except Exception:                     # noqa: BLE001 - a preview, not a load
        record = None
    spec = SessionSpec(
        run_id=f"{record_id}#{thread}",
        cwd=briefs.agent_cwd(studio, record) if record is not None else "",
        mcp_servers=agents._mcp_for(getattr(record, "session", "") or "",
                                    str(cfg.primary_records or "")))
    # Prospective: nobody has started this conversation, so what to do about a
    # missing session is "launch it and open one", not "this one will never
    # get them" — the sentence a live conversation needs.
    return agent_preflight.check(
        spec, getattr(agents.provider, "mcp_command", ""),
        prospective=True).notice()

  @app.get("/api/agent/{record_id}/briefing")
  def api_agent_briefing(record_id: str, thread: str = "main",
                         task: Optional[str] = None) -> JSONResponse:
    """What this conversation was started with, section by section.

    The studio's harness decides how the agent behaves, and without this route
    the only way to read it would be to open `instructions/` in an editor and
    assemble the pieces in your head -- with the run's own context, which is
    composed per message and written down nowhere else. A prompt that cannot
    be read is one nobody can say is wrong.

    It answers about the conversation that is *open* when there is one, and
    about the one that would open otherwise. The difference matters: the
    harness is fixed when a session connects, so a conversation older than the
    setting is running on the older text, and `live` is how the window says
    which of the two it is showing.
    """
    # Only when nothing is open: composing the prospective answer reads the
    # run's numbers off disk, and the live one is already in memory.
    if agents.given(record_id, thread) is not None:
      brief: Dict[str, Any] = {}
    elif record_id.startswith("task:"):
      brief = briefs.task_brief(studio, record_id.split(":", 1)[1])
    else:
      record, _ = library.find(validate.identifier(record_id, "record id"), task)
      if record is None:
        return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
      brief = briefs.run_brief(studio, record)
    return JSONResponse(agents.briefing(
        record_id,
        context=brief.get("context", ""),
        cwd=brief.get("cwd", ""),
        session_dir=brief.get("session_dir", ""),
        thread=validate.identifier(thread or "main", "thread"),
        free_paths=brief.get("free_paths") or [],
        records_root=brief.get("records_root", "")))

  def _cwd_of(record_id: str, task: Optional[str]) -> Optional[str]:
    """Where a conversation about this subject runs -- the same answer the
    briefing gives, because that is the directory whose sessions it shares."""
    if record_id.startswith("task:"):
      return str(briefs.task_brief(studio, record_id.split(":", 1)[1]).get("cwd") or "")
    record, _ = library.find(validate.identifier(record_id, "record id"), task)
    if record is None:
      return None
    return str(briefs.run_brief(studio, record).get("cwd") or "")

  @app.get("/api/agent/{record_id}/sessions")
  def api_agent_sessions(record_id: str, task: Optional[str] = None) -> JSONResponse:
    """The apps' own conversations about this subject's working directory.

    Claude Code and Codex each file every conversation under the directory it
    was begun in (`agents/external.py`), and a conversation about this run's
    code is one begun in this run's cwd -- from a terminal, from the desktop
    app, or from this studio, whose SDK sessions are the same files. Listed so
    the panel can offer to continue one here (`attach` below).
    """
    cwd = _cwd_of(record_id, task)
    if cwd is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    return JSONResponse(external_sessions.sessions(cwd))

  @app.post("/api/agent/{record_id}/attach")
  async def api_agent_attach(record_id: str, body: Dict[str, Any],
                             task: Optional[str] = None) -> JSONResponse:
    """Continue a Claude Code session in this conversation.

    The next message resumes it -- `claude --resume`, through the SDK -- with
    the run's context and tools, and its memory of everything said before,
    wherever that was said. Not a mirror: one client drives a session at a
    time, and after this it is the studio. The terminal can take it back with
    the same id afterwards.
    """
    body = body or {}
    session_id = str(body.get("session_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", session_id):
      return JSONResponse({"error": "`session_id` must be the app's session id"},
                          status_code=400)
    provider = str(body.get("provider") or "claude")
    if provider not in ("claude", "codex"):
      return JSONResponse({"error": f"continuing a {provider} session here is not "
                                    "built; Claude Code and Codex sessions are"},
                          status_code=501)
    thread = validate.identifier(body.get("thread") or "main", "thread")
    cwd = _cwd_of(record_id, task)
    if cwd is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    session = agents.attach(record_id, session_id, provider=provider,
                            thread=thread, cwd=cwd)
    entry = session.transcript[-1]
    await hub.publish(f"agent.{entry['kind']}", entry, f"{record_id}#{thread}")
    return JSONResponse({"ok": True, "conversation": session.describe()})

  @app.post("/api/agent/{record_id}/message")
  async def api_agent_message(record_id: str, body: Dict[str, Any],
                              task: Optional[str] = None) -> JSONResponse:
    body = body or {}
    text = body.get("text", "").strip()
    thread = body.get("thread") or "main"
    # Validated here, with the rest of the body, not at the send: a machine
    # with no agent CLI answers 503 before it gets that far, and a malformed
    # flag is the client's mistake whether or not a provider is ready.
    interrupt = validate.flag(body.get("interrupt"), "interrupt")
    # A picture the user attached: a frame of a clip, a chart, a 3-D view, with
    # or without a box drawn on it. It arrives as a data URL because most of
    # those are not files -- a video frame and an SVG chart exist only in the
    # page until something draws them to a canvas.
    shot, problem = _decode_shot(body.get("image"))
    if problem:
      return JSONResponse({"error": problem}, status_code=400)
    # A picture on its own is a message: "look at this" is a complete thing to
    # say, and requiring words to go with it would be the app being pedantic.
    if not text and not shot:
      return JSONResponse({"error": "empty message"}, status_code=400)
    # "Not signed in" is what the provider says when nobody has *asked* the
    # CLI yet, and after a restart nobody has. The GUI happens to call
    # /api/agent on boot, so this only bit a client that sent a message first
    # -- which is exactly what a script, or a reconnecting page, does.
    if not agents.provider.auth_state:
      await agents.refresh_auth()
    reason = agents.provider.unavailable_reason()
    if reason:
      return JSONResponse({"error": reason}, status_code=503)

    record, source = library.find(record_id, task)
    if record is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)

    try:
      await briefs.tell_agent(studio, record, text or "Look at this.", thread,
                        images=[shot] if shot else None,
                        shows="attached image" if shot else "",
                        interrupt=interrupt)
    except RuntimeError as exc:
      return JSONResponse({"error": str(exc)}, status_code=409)
    return JSONResponse({"ok": True})

  def _decode_shot(value: Any) -> tuple:
    """One `data:image/...;base64,...` from the browser, as bytes.

    Bounded before decoding, not after: the limit exists so a page cannot make
    this process allocate whatever it likes, and checking the decoded size
    would already have done the allocating. Base64 is 4/3 of the bytes, so the
    encoded bound is the real one.
    """
    if value in (None, ""):
      return None, ""
    if not isinstance(value, str):
      return None, "`image` must be a data url"
    if len(value) > MAX_SHOT_CHARS:
      return None, (f"that image is {len(value):,} characters encoded; the "
                    f"limit is {MAX_SHOT_CHARS:,}")
    match = re.match(r"^data:image/(png|jpeg|webp);base64,(.+)$", value, re.S)
    if not match:
      return None, "`image` must be a png, jpeg or webp data url"
    try:
      return base64.b64decode(match.group(2), validate=True), ""
    except (binascii.Error, ValueError):
      return None, "that image is not valid base64"

  @app.post("/api/agent/{record_id}/decide")
  async def api_agent_decide(record_id: str, body: Dict[str, Any]) -> JSONResponse:
    body = body or {}
    try:
      await agents.decide(record_id, body.get("request_id", ""),
                          body.get("decision", "deny"), body.get("note", ""),
                          body.get("thread") or "main")
    except Exception as exc:
      return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True})

  @app.post("/api/agent/{record_id}/stop")
  async def api_agent_stop(record_id: str, body: Dict[str, Any] = None) -> JSONResponse:
    """Stop the turn this conversation is in the middle of.

    ``record_id`` is the conversation's subject, which in the Build view is
    `task:<id>` rather than a record -- the same string the message route
    takes, because it is the same conversation.
    """
    thread = validate.identifier((body or {}).get("thread") or "main", "thread")
    record_id = validate.identifier(record_id, "record id")
    from rlmcp_studio.agents.host import key as session_key
    existed = session_key(record_id, thread) in agents.sessions
    # Whether it was *working*, which is not the same as whether a conversation
    # exists: 200 for something that did not happen is how a UI comes to
    # believe it stopped an agent that had already finished.
    stopped = await agents.stop(record_id, thread)
    return JSONResponse({"ok": existed, "stopped": stopped,
                         "reason": "" if existed else "no conversation to stop"})
