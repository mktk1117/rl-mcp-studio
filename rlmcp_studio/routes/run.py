"""One run: playing its policy, steering its trainer, and what it produced."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response

from rlmcp_studio.api import validate
from rlmcp_studio.context import Studio
from rlmcp_studio.sources import clips as source_clips
from rlmcp_studio.sources import code as source_code
from rlmcp_studio.sources import control
from rlmcp_studio.sources import preview as source_preview
from rlmcp_studio.sources import session as source_session
from rlmcp_studio.sources import tb as source_tb
from rlmcp_studio.sources import trace as source_trace
from rlmcp_studio.sources.session import LiveSession

RENDERABLE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4",
              ".webm", ".mov", ".ogg", ".wav", ".mp3"}
"""What an artifact route may render inline. Everything else is a download."""

_live_schema: Dict[str, Dict[str, Any]] = {}
"""The last parameter schema a live trainer gave us, by session directory.

So the shape check in front of `set_parameter` judges a value against exactly
what the browser was shown, without asking the trainer a second time.
"""


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the per-run routes."""
  cfg = studio.cfg
  library = studio.library
  code_roots = studio.code_roots
  previews = studio.previews
  hub = studio.hub
  tensorboard = studio.tensorboard

  # ── watching a policy that has finished training ──────────────────────
  def _play_key(record_id: str, task: str) -> str:
    """Which played policy this is. Keyed by task *and* id for the same reason
    `find` takes both: `012` names a different run in each records root, and a
    key that ignored the task would have them share one viewer."""
    return f"play:{task or '?'}:{record_id}"

  @app.post("/api/run/{record_id}/play")
  async def api_play_policy(record_id: str, body: Dict[str, Any],
                            task: Optional[str] = None) -> JSONResponse:
    """Open the run's own policy in a viewer.

    `Live 3D` is a window onto a *process*, so it goes when the trainer does --
    which is exactly when you want to look at what it learned. This is the
    other half: `rlmcp play` rebuilds the environment from the checkpoint,
    restores the conditions it trained under, and holds it open as an rlmcp
    session. The same `live_view` command then frames it in the same panel, so
    nothing downstream can tell the two apart.
    """
    body = body or {}
    action = (body.get("action") or "start").strip()
    # Ids are per-store, so the task is what makes `012` mean one run: without
    # it a two-root studio can play a different run's policy under the right id.
    record, _ = library.find(record_id, task)
    if record is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)

    play = previews.of(record.task or "", key=_play_key(record_id, record.task))
    if action == "stop":
      return JSONResponse({"play": await play.stop()})

    if action == "view":
      # The viewer is a command to the *play* session, not to the run's -- the
      # run's trainer is gone, which is why this process exists. Same command
      # the trainer answers, sent down a different session directory.
      if not play.running:
        return JSONResponse({"error": "nothing is playing this run's policy"},
                            status_code=409)
      try:
        answer = await control.call(play.session_dir, "live_view", timeout=30,
                                    enabled=bool(body.get("enabled", True)))
      except control.TrainerGone as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
      if not answer.get("ok"):
        return JSONResponse({"error": answer.get("error") or "the viewer refused"},
                            status_code=502)
      return JSONResponse({"play": play.describe(), "view": answer})

    checkpoint = (body.get("checkpoint") or "").strip()
    if not checkpoint:
      # Whatever `rlmcp play` would have picked on its own, resolved here so
      # the panel can say which one it is before spending a minute on it.
      checkpoint = source_preview.latest_checkpoint(record)
    if not checkpoint:
      return JSONResponse(
          {"error": "this run kept no checkpoint, so there is no policy to "
                    "play. Its frames and clips are still on the record."},
          status_code=409)

    described = await play.start(checkpoint=checkpoint,
                                 # 0 = let the task's play config decide. See
                                 # `Preview.start`: forcing one env can overflow
                                 # the contact budget on rough terrain.
                                 num_envs=int(body.get("num_envs") or 0),
                                 device=validate.text(body.get("device") or "cpu",
                                                      "device", limit=20))
    await hub.publish("run.play", {"record": record_id, "state": described["state"]},
                      record_id)
    return JSONResponse({"play": described})

  @app.get("/api/run/{record_id}/play")
  def api_play_state(record_id: str, task: Optional[str] = None) -> JSONResponse:
    record, _ = library.find(record_id, task)
    if record is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    played = previews.describe_key(_play_key(record_id, record.task))
    # Which checkpoint this *would* play, resolved before anything is spawned:
    # the panel offers "play the policy" or explains that there is no policy to
    # play, and it cannot tell the difference from a description of a process
    # that is not running.
    ckpt = source_preview.latest_checkpoint(record)
    return JSONResponse({"play": {**played, "can_play": bool(ckpt), "would_play": ckpt}})

  # ── steering ──────────────────────────────────────────────────────────
  def _session_of(record_id: str, task: Optional[str] = None):
    record, _ = library.find(record_id, task)
    if record is None:
      return None, JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    if not record.session:
      return None, JSONResponse(
          {"error": "this record does not name a session directory, so there is "
                    "no trainer to talk to."}, status_code=409)
    return record, None

  @app.post("/api/run/{record_id}/command")
  async def api_command(record_id: str, body: Dict[str, Any],
                        task: Optional[str] = None) -> JSONResponse:
    """Send one rlmcp command to the trainer.

    Read-only commands go straight through. Anything that changes the run is
    refused unless the caller says it means it -- the same line the agent's
    permission table draws, drawn again here because this endpoint is
    reachable without an agent.
    """
    body = body or {}
    cmd = validate.text(body.get("cmd"), "cmd", limit=120)
    args = validate.mapping(body.get("args"), "args",
                            reserved=("cmd", "timeout", "session_dir"))
    timeout = validate.number(body.get("timeout"), "timeout",
                              default=120, low=0.1, high=3600)
    confirmed = validate.flag(body.get("confirmed"), "confirmed")
    record, problem = _session_of(record_id, task)
    if problem is not None:
      return problem
    if cmd not in control.READ_ONLY and not confirmed:
      return JSONResponse(
          {"error": f"`{cmd}` changes the live run; resend with confirmed=true",
           "needs_confirmation": True}, status_code=428)

    # A parameter's *shape* is checked here as well as in the browser. The
    # client's check is the one that helps -- it disables the button and says
    # why -- but this endpoint is reachable without it, and the damage is not
    # recoverable: a wrong-typed value (text for a parameter that is a list,
    # say) would be stored as a string with a type changed underneath it, and
    # nothing in the GUI could put it back.
    if cmd == "set_parameter":
      wrong = _wrong_shape(record.session, args)
      if wrong:
        return JSONResponse({"error": wrong}, status_code=400)

    try:
      answer = await control.call(record.session, cmd, timeout=timeout, **args)
    except control.TrainerGone as exc:
      return JSONResponse({"error": str(exc), "trainer_gone": True}, status_code=409)
    except Exception as exc:
      return JSONResponse({"error": str(exc)}, status_code=500)
    await hub.publish("run.command", {"record": record_id, "cmd": cmd,
                                      "ok": answer.get("ok")}, record_id)
    return JSONResponse(answer)

  def _wrong_shape(session: str, args: Dict[str, Any]) -> str:
    """Why this value does not fit the parameter, or "" if it does.

    **The same schema the browser was shown**, which is the live one when the
    trainer answered and the snapshot otherwise. Reading only the snapshot here
    would be worse than not checking at all: after a bad edit the live type can
    differ from the on-disk one, so the browser would offer one shape, this
    would demand another, and the parameter could not be repaired through the
    GUI at all.

    The live answer is remembered when the Parameters view fetches it, so this
    still costs a running trainer nothing.
    """
    key, value = args.get("key"), args.get("value")
    if not isinstance(key, str):
      return "set_parameter needs a `key`"

    # Never storable, whatever the parameter is: a `NaN` sent through here
    # would be written as `null`.
    for number in ([value] if not isinstance(value, list) else value):
      if isinstance(number, float) and (number != number or number in (float("inf"), float("-inf"))):
        return f"{key} cannot be set to {number}"

    published = _live_schema.get(str(session), {})
    if not published:
      try:
        published = LiveSession(Path(session)).schema()
      except Exception:                   # no snapshot yet: not this call's
        return ""                         # problem to solve
    spec = published.get(key) if isinstance(published, dict) else None
    if not isinstance(spec, dict):
      return ""                           # unknown to the schema: let it through
    current = spec.get("current", spec.get("value"))
    if isinstance(current, bool) and not isinstance(value, bool):
      return f"{key} is true/false"
    if isinstance(current, (int, float)) and not isinstance(current, bool):
      if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"{key} is a number; {value!r} is not"
    if isinstance(current, list):
      if not isinstance(value, list) or len(value) != len(current):
        return f"{key} is a list of {len(current)}; {value!r} is not"
      if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        return f"every entry of {key} has to be a number"
    if isinstance(current, str) and not isinstance(value, str):
      return f"{key} is text; {value!r} is not"
    return ""

  @app.get("/api/run/{record_id}/parameters")
  async def api_parameters(record_id: str, contains: str = "",
                           task: Optional[str] = None) -> JSONResponse:
    """The run's knobs: live if the trainer answers, from its snapshot if not.

    A run with no session at all is an *answer*, not an error: most records you
    open have finished, and some never had a trainer. An error status here
    would reach the user as a raw status code, so every archived run's
    Parameters tab would read as a failure instead of saying why it is empty.
    """
    record, _ = library.find(validate.identifier(record_id, "record id"), task)
    if record is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    if not record.session:
      return JSONResponse({
          "ok": False, "live": False, "parameters": [],
          "reason": "this record does not name a session, so there are no "
                    "parameters to read -- not even a snapshot",
      })
    answer = await control.parameters(record.session, contains)
    # Remembered for `_wrong_shape`, which must judge a value against exactly
    # what the browser was shown -- and only when it came from the trainer,
    # since the snapshot is readable there anyway.
    if answer.get("live") and isinstance(answer.get("parameters"), dict):
      _live_schema[str(record.session)] = answer["parameters"]
    return JSONResponse(answer)

  @app.get("/api/run/{record_id}/tensorboard")
  def api_tensorboard(record_id: str, limit: int = source_tb.MAX_POINTS,
                      task: Optional[str] = None) -> JSONResponse:
    """Every scalar the trainer's own logger wrote, beside the session.

    rlmcp publishes what it was asked to publish -- a handful of series. The
    trainer writes everything: dozens on a real locomotion run, including
    per-terrain curriculum levels, every termination reason and every loss
    term. They are already on disk, and nothing else in this stack reads them.

    Read incrementally and cached per file, so a live run costs the same on the
    hundredth poll as on the first.
    """
    record, _ = library.find(validate.identifier(record_id, "record id"), task)
    if record is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    directory = source_tb.run_directory(record.session)
    if directory is None:
      return JSONResponse({
          "ok": False, "series": {}, "tags": [], "files": [],
          "reason": "this record does not name a session, so there is no run "
                    "directory to look beside for event files",
      })
    points = int(validate.number(limit, "limit", default=source_tb.MAX_POINTS,
                                 low=10, high=20_000))
    return JSONResponse(tensorboard.read(directory, limit=points))

  @app.get("/api/run/{record_id}/clips")
  def api_clips(record_id: str, task: Optional[str] = None) -> JSONResponse:
    """The run's clips, oldest first: the trajectory, not the attachments.

    Merged from the record's media store and the live session's artifacts, so
    a clip is watchable the moment it is encoded and still there once the logs
    are gone.
    """
    record, source = library.find(record_id, task)
    if record is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    media_root = getattr(source.store, "media_root", None) if source else None
    return JSONResponse(source_clips.for_run(record, media_root))

  # ── what the record keeps ─────────────────────────────────────────────
  @app.post("/api/run/{record_id}/asset")
  async def api_add_asset(record_id: str, body: Dict[str, Any],
                          task: Optional[str] = None) -> JSONResponse:
    """Promote an artifact into the record's own media store.

    rl-mcp's argument, and it is the right one: a clip that stays in `logs/`
    is not a deliverable -- it goes when the logs are cleaned, and the record
    is left describing a video nobody can watch. `record asset` copies it in.
    """
    body = body or {}
    path = body.get("path", "")
    kind = body.get("kind", "videos")
    caption = body.get("caption", "")
    if not path:
      return JSONResponse({"error": "no path"}, status_code=400)

    record, source = library.find(record_id, task)
    if record is None or source is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)

    store = source.store
    try:
      key = store.media.put(record.id, path, caption, kind)

      def add_asset(fresh) -> None:
        entries = fresh.assets.setdefault(kind, [])
        # Promoting the same file twice would leave the record describing one
        # clip under two keys, and the second is the one the view would show.
        if any(entry and entry[0] == key for entry in entries):
          return False
        entries.append([key, caption])

      store.update_record(record.id, add_asset)
    except Exception as exc:
      return JSONResponse({"error": str(exc)}, status_code=400)

    await hub.publish("records.changed", {"record": record_id}, record_id)
    return JSONResponse({"ok": True, "key": key, "kind": kind})

  @app.get("/api/record-asset")
  def api_record_asset(key: str, task: Optional[str] = None) -> Any:
    """Serve one asset out of a records media store, by its key.

    The store resolves the key itself and refuses anything that would land
    outside it, so path containment is rl-mcp's answer here rather than a
    second one invented in the studio.

    **`task` is what stops it serving the wrong run's video.** A media key
    begins with the record id -- `012/videos/....mp4` -- and record ids are
    per *store*: with several roots configured, `001/videos/...` resolves in
    more than one of them. Scanning the sources and returning the first hit
    then plays another task's clip under this run's name, which is the same
    failure `Library.find` documents for records themselves, in a place where
    it is even harder to notice: the video simply shows the wrong robot.

    So the id is read off the key, the record is resolved *within the task*,
    and that record's own store serves the file. Without a task the plain scan
    stands, because a single-root studio has no ambiguity to resolve.
    """
    record_id = str(key).split("/", 1)[0].strip()
    if task and record_id:
      record, source = library.find(record_id, task)
      if record is not None and source is not None:
        path = source.store.media.get(key)
        if path:
          return FileResponse(path)
        return JSONResponse({"error": "no such asset in this task"},
                            status_code=404)
    for source in library.sources:
      if not source.available():
        continue
      path = source.store.media.get(key)
      if path:
        return FileResponse(path)
    return JSONResponse({"error": "no such asset"}, status_code=404)

  @app.get("/api/trace")
  def api_trace(path: str) -> JSONResponse:
    """The signals behind a diagnosis, so they can be drawn like every other curve.

    `diagnose` hands back a matplotlib PNG and the numbers that produced it.
    The PNG is the only chart in the app that arrives as a picture, which makes
    it the only one that cannot be smoothed, zoomed or read a value off. The
    npz beside it holds the same signals.
    """
    try:
      target = source_trace_path(path)
    except source_code.OutsideRoot as exc:
      return JSONResponse({"error": str(exc)}, status_code=403)
    return JSONResponse(source_trace.read(target))

  def source_trace_path(path: str) -> Path:
    roots = [Path(p) for p in library.live_session_dirs()] + list(cfg.records)
    for summary in library.summaries():
      if summary.get("session"):
        roots.append(Path(summary["session"]))
    return source_code.resolve_under(path, roots)

  @app.get("/api/artifact")
  def api_artifact(path: str) -> Any:
    """Serve one artifact the run produced.

    Confined to session directories, resolved before the check. Artifacts are
    images and video, so they are served as files rather than JSON.
    """
    path = validate.path_like(path)
    # The task repository too: a document's pictures are written beside the
    # document, and the task report's are in the package. Everything under
    # there is already readable through `/api/source/file`, and what is not
    # renderable still downloads with `nosniff`.
    # A run on another machine: the client composed `${dir}/artifacts/${name}`
    # with `dir` a wire address, so the "path" is a URL. The bytes come
    # through the session client, never a filesystem; the
    # session is one the library knows, so an arbitrary URL is not fetched.
    if source_session.is_address(path):
      address, _, name = path.rpartition("/artifacts/")
      if not address or not name or "/" in name:
        return JSONResponse({"error": "not an artifact address"}, status_code=400)
      if address not in studio.known_addresses():
        return JSONResponse({"error": "not a session this studio knows"}, status_code=403)
      try:
        data = source_session.LiveSession(address).read_artifact(name)
      except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
      except Exception as exc:  # the host did not answer, or refused
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=502)
      suffix = Path(name).suffix.lower()
      media = ("application/octet-stream" if suffix not in RENDERABLE
               else {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".gif": "image/gif", ".webp": "image/webp", ".mp4": "video/mp4",
                     ".webm": "video/webm", ".svg": "image/svg+xml"}.get(suffix, "application/octet-stream"))
      return Response(content=data, media_type=media,
                      headers={"X-Content-Type-Options": "nosniff"})
    target = studio.artifact_path(path, code_roots)
    if target is None:
      return JSONResponse({"error": "not found"}, status_code=404)
    # rlmcp writes `records.html` into the records root, and a run or an agent
    # can drop anything else there. Served as text/html on this origin, such a
    # file would run as script with the studio's own API in reach -- same
    # origin, so the cookie that guards the API is no obstacle to it. Pictures
    # and video render; the rest downloads.
    if target.suffix.lower() not in RENDERABLE:
      return FileResponse(target, media_type="application/octet-stream",
                          filename=target.name,
                          headers={"X-Content-Type-Options": "nosniff"})
    return FileResponse(target, headers={"X-Content-Type-Options": "nosniff"})
