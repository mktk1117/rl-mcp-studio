"""Records and sessions: the run rail, adopting a session, the record graph, one run's record."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rlmcp_studio.api import validate
from rlmcp_studio.api.validate import BadRequest
from rlmcp_studio.context import Studio
from rlmcp_studio.sources import code as source_code
from rlmcp_studio.sources import session as source_session
from rlmcp_studio.sources.session import (LiveSession, events as session_events,
                                          parameter_traces)


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the records and session routes."""
  cfg = studio.cfg
  library = studio.library
  code_roots = studio.code_roots
  previews = studio.previews
  hub = studio.hub

  @app.get("/api/runs")
  def api_runs(task: Optional[str] = None) -> JSONResponse:
    # With liveness: the rail draws a live run differently, and it must not
    # decide that from `verdict == "running"` -- which is the record's claim
    # about the experiment and stays true long after the trainer is gone.
    return JSONResponse({"task": task,
                         "runs": library.summaries(task, liveness=True)})

  def _logs_roots() -> List[Path]:
    """Where trainers write. Beside the task package, by the same convention
    that puts the records there."""
    roots: List[Path] = []
    for root in code_roots:
      candidate = Path(root) / "logs"
      if candidate.is_dir():
        roots.append(candidate)
    return roots

  @app.get("/api/sessions/unrecorded")
  def api_unrecorded() -> JSONResponse:
    """Runs that are training, or have trained, with no record to their name."""
    return JSONResponse({"sessions": library.unrecorded(_logs_roots())})

  @app.post("/api/sessions/adopt")
  async def api_adopt(body: Dict[str, Any]) -> JSONResponse:
    """Write a record for a session that has none.

    The one action that turns an invisible run into a run this app can show.
    Deliberately a *button*, not something that happens on its own: a record is
    a claim, and inventing claims about somebody's runs is not the studio's
    business.
    """
    body = body or {}
    session_dir = validate.path_like(body.get("session"), "session")
    slug = validate.text(body.get("slug"), "slug", limit=80)
    hypothesis = validate.text(body.get("hypothesis"), "hypothesis",
                               required=False, limit=2000)

    # Only a session this studio is actually offering. That keeps the write
    # confined to the logs beside the records, and stops a record being
    # attached to a directory that already has one.
    offered = {str(Path(s["session"]).resolve())
               for s in library.unrecorded(_logs_roots())}
    if str(Path(session_dir).expanduser().resolve()) not in offered:
      return JSONResponse(
          {"error": "that is not a session waiting for a record; refresh and "
                    "look again"}, status_code=404)

    source = next((s for s in library.sources if s.available()), None)
    if source is None:
      return JSONResponse({"error": "no writable records root"}, status_code=409)

    live = LiveSession(Path(session_dir)).snapshot(metrics_tail=1)
    record = source.store.new_record(
        slug,
        task=live.get("task") or "",
        session=str(session_dir),
        hypothesis=hypothesis,
        # `running` only when the trainer actually answers. A stopped session
        # is not evidence that the run *failed* -- a short smoke test finishes
        # all its iterations and exits clean, and calling it `interrupted`
        # because nothing is listening any more would put a false claim in the
        # ledger. Anything else is the human's
        # to close out, so the store's default stands.
        **({"verdict": "running"} if live.get("state") == "running" else {}),
    )
    await hub.publish("records.changed", {"record": record.id}, record.id)
    return JSONResponse({"ok": True, "record": record.id, "slug": record.slug})

  @app.post("/api/records/next")
  async def api_next_record(body: Dict[str, Any]) -> JSONResponse:
    """Open the record the edit in the working tree belongs to.

    The other half of the `next_run` banner: you edit code, and the app
    offers you the record it belongs to. The banner says the edit
    cannot reach the running process; this is what to do about it -- a `planned`
    record, parented on the run you were looking at, carrying the changed files
    as its stated change.

    A button, never automatic, for the same reason as `api_adopt`: a record is
    a claim, and the studio does not invent claims. What it fills in is what it
    can *see* -- the parent, the task, the file list -- and the hypothesis, the
    part only a person can write, is required.
    """
    body = body or {}
    parent_id = validate.identifier(body.get("parent"), "parent")
    slug = validate.text(body.get("slug"), "slug", limit=80)
    hypothesis = validate.text(body.get("hypothesis"), "hypothesis", limit=2000)

    parent, source = library.find(parent_id, validate.text(
        body.get("task"), "task", required=False, limit=200) or None)
    if parent is None or source is None:
      return JSONResponse({"error": f"no record {parent_id}"}, status_code=404)

    roots = source_code.source_roots(cfg.records)
    patch = await source_code.worktree_diff(roots[0]) if roots else {"ok": False}
    # The same relevance rule the Diff view uses: a change to `records/` or to
    # a doc is not what the next run is about, and listing it as the record's
    # stated change would be noise in the ledger forever. Everything, if the
    # rule finds nothing -- better a broad claim than an empty one.
    hints = [h for h in (parent.task or "").replace("-", " ").split() if len(h) > 3]
    files = patch.get("files", []) if patch.get("ok") else []
    relevant = [f["path"] for f in files if source_code.relevance(f["path"], hints)]
    changed = relevant or [f["path"] for f in files]
    if not changed:
      return JSONResponse(
          {"error": "nothing is changed in the working tree, so there is no "
                    "edit for a record to be about"}, status_code=409)

    record = source.store.new_record(
        slug,
        task=parent.task,
        parent=parent.id,
        hypothesis=hypothesis,
        # Said as files, not as a diff: the diff is in the working tree and the
        # record should not carry a copy that can drift from it. The run's own
        # code stamp, written when it launches, is what makes it exact.
        change=[f"uncommitted edits to {name}" for name in changed[:20]],
    )
    await hub.publish("records.changed", {"record": record.id}, record.id)
    return JSONResponse({"ok": True, "record": record.id, "slug": record.slug,
                         "files": changed})

  @app.get("/api/params/history")
  def api_param_history(task: Optional[str] = None) -> JSONResponse:
    """How every parameter moved, across this task's whole ancestry.

    rl-mcp builds it; the studio only carries it. Each point knows where it
    came from -- a launch value, a live edit, the end of a run -- and a live
    edit carries the reason that was given for it, which is the half of "what
    changed" that a chart cannot draw.
    """
    return JSONResponse(library.param_history(task))

  @app.get("/api/records/graph")
  def api_record_graph(task: Optional[str] = None) -> JSONResponse:
    """The record graph, built by rlmcp.records.graph -- so this tree and the
    one `rlmcp record graph` draws cannot disagree."""
    return JSONResponse({"task": task, "nodes": library.record_graph(task)})

  @app.get("/api/run/{record_id}")
  def api_run(record_id: str, task: Optional[str] = None) -> JSONResponse:
    # `task` disambiguates: two records roots both number from 001, so an id
    # alone can name two different runs. The client always knows which task it
    # is looking at, because the rail is filtered by one.
    record, source = library.find(record_id, task)
    if record is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    from rlmcp_studio.sources.records import record_summary
    payload: Dict[str, Any] = {
        "record": record_summary(record),
        "hypothesis": record.hypothesis,
        "prediction": record.prediction,
        "outcome": record.outcome,
        "falsifier": {
            "prose": record.falsifier.prose,
            "check_after": record.falsifier.check_after,
        },
        # ``artifacts`` are media keys; a key is meaningless to a browser, so
        # the ones that still resolve are carried as paths it can fetch. A
        # remark made by pointing at a frame is unreadable without its frame.
        "feedback": [
            {"text": f.text, "kind": f.kind, "author": f.author,
             "response": f.response, "changed": f.changed, "at": f.at,
             "iteration": f.iteration, "artifacts": list(f.artifacts),
             "images": _feedback_images(source, f)}
            for f in record.feedback
        ],
        "links": dict(record.links),
        "dir": str(source.store.record_dir(record)) if source else "",
    }
    payload["live"] = _live_for(record)
    return JSONResponse(payload)

  def _session_path(path: str) -> Path:
    """A session directory the client may read.

    These three routes take a path from the query string, and every path is
    checked against the configured roots before it is opened -- the rule
    `code.py` writes down. Without the check, any page in any tab could read
    another project's telemetry and list its artifacts directory.

    The allowed set is deliberately narrow: the session directories records
    actually name, the roots themselves, and the previews this studio started.
    Not "anywhere under a root".
    """
    path = validate.path_like(path, "path")
    # A run on another machine is a URL, and the same rule holds: only a
    # session a record of this studio names, or one of its own jobs started.
    if source_session.is_address(path):
      if path.rstrip("/") in studio.known_addresses():
        return path.rstrip("/")
      raise BadRequest("that address is not a session of any run this studio is watching")
    wanted = Path(path).expanduser().resolve()
    allowed = [Path(p).resolve() for p in library.session_dirs()]
    allowed += [Path(r).resolve() for r in cfg.records]
    # Plus the previews, which are the one kind of session this app creates
    # rather than finds -- so no record names them, and the studio picked the
    # path itself rather than taking it from a browser.
    allowed += [Path(p).resolve() for p in previews.session_dirs()]
    for root in allowed:
      if wanted == root or root in wanted.parents:
        return wanted
    raise BadRequest(
        "that directory is not a session of any run this studio is watching")

  @app.get("/api/events")
  def api_events(path: str, tail: int = 200) -> JSONResponse:
    return JSONResponse({"events": session_events(_session_path(path), last_n=tail)})

  @app.get("/api/traces")
  def api_traces(path: str) -> JSONResponse:
    """Parameter step functions over iteration, plus the curriculum bands."""
    return JSONResponse(parameter_traces(_session_path(path)))

  @app.get("/api/session")
  def api_session(path: str, tail: int = 240) -> JSONResponse:
    return JSONResponse(LiveSession(_session_path(path)).snapshot(metrics_tail=tail))

  def _feedback_images(source, feedback) -> List[str]:
    """The pictures attached to one remark, as paths the client can ask for.

    Keys that no longer resolve are dropped rather than reported: a media file
    someone deleted is a missing image, not an error worth a red box.
    """
    if source is None:
      return []
    out: List[str] = []
    for key in feedback.artifacts or []:
      local = source.store.media.get(key)
      if local and Path(local).suffix.lower() in (".png", ".jpg", ".jpeg", ".gif"):
        out.append(local)
    return out

  def _live_for(record) -> Dict[str, Any]:
    """A record points at its session directory; follow it if it still exists."""
    if not record.session:
      return {"ok": False, "reason": "record has no session directory"}
    return LiveSession(Path(record.session)).snapshot(metrics_tail=120)
