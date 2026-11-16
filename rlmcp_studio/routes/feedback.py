"""The ledger, made reachable: feedback, closing a run out, and annotating a frame."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rlmcp_studio import briefs
from rlmcp_studio.api import validate
from rlmcp_studio.api.validate import BadRequest
from rlmcp_studio.context import Studio
from rlmcp_studio.sources import annotate
from rlmcp_studio.sources import closeout


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the feedback, close-out and annotation routes."""
  library = studio.library
  hub = studio.hub
  agents = studio.agents

  # ── feedback: the ledger, made reachable ──────────────────────────────
  @app.post("/api/run/{record_id}/feedback")
  async def api_add_feedback(record_id: str, body: Dict[str, Any],
                             task: Optional[str] = None) -> JSONResponse:
    """Write an observation to the record.

    This is the whole reason the ledger is worth having: `rlmcp feedback` is
    implemented and nobody types it mid-run. Here it is a box beside the run.
    """
    body = body or {}
    text = validate.text(body.get("text"), "text")
    kind = validate.kind(body.get("kind"))
    record, source = library.find(validate.identifier(record_id, "record id"), task)
    if record is None or source is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    from rlmcp.records import Feedback
    try:
      updated = source.store.add_feedback(record_id, Feedback(text=text, kind=kind))
    except Exception as exc:
      return JSONResponse({"error": str(exc)}, status_code=400)
    await hub.publish("records.changed", {"record": record_id}, record_id)
    return JSONResponse({"ok": True, "count": len(updated.feedback)})

  @app.post("/api/run/{record_id}/close")
  async def api_close_record(record_id: str, body: Dict[str, Any],
                             task: Optional[str] = None) -> JSONResponse:
    """Close the run out with a verdict, through rl-mcp's own close-out.

    Without this, every run launched here would stay `running` for ever,
    beside a status bar saying `trainer gone`, with no way to close it short
    of opening a terminal.

    The studio decides nothing about what a verdict means: it collects three
    sentences and a list of measurements and hands them to `rlmcp record
    close`, which gathers the evidence, folds in what was said to the live
    trainer, evaluates the falsifier and refuses the verdicts this record has
    not earned. A refusal is returned as rl-mcp wrote it -- those sentences
    are the most useful thing this control produces.
    """
    body = body or {}
    verdict = validate.text(body.get("verdict"), "verdict", limit=40)
    headline = validate.text(body.get("headline") or "", "headline",
                             required=False, limit=300)
    outcome = validate.text(body.get("outcome") or "", "outcome",
                            required=False, limit=8000)
    rows = body.get("metrics") or []
    if not isinstance(rows, list):
      return JSONResponse({"error": "`metrics` is a list of name=value"},
                          status_code=400)
    try:
      metrics = closeout.parse_metrics(rows[:40])
    except ValueError as exc:
      return JSONResponse({"error": str(exc)}, status_code=400)

    record, source = library.find(validate.identifier(record_id, "record id"), task)
    if record is None or source is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)

    answer = await closeout.close_record(
        source.root, record.id, verdict,
        headline=headline, outcome=outcome, metrics=metrics)
    if not answer.get("ok"):
      # 409, not 500: a refusal is the store disagreeing with the claim, which
      # is a conflict and not a failure. The words are rl-mcp's.
      return JSONResponse({"error": answer.get("error") or "it would not close"},
                          status_code=409)
    await hub.publish("records.changed", {"record": record.id}, record.id)
    return JSONResponse({"ok": True, "record": answer.get("record") or {},
                         "falsifier": answer.get("falsifier") or {},
                         "report": answer.get("report") or ""})

  @app.post("/api/run/{record_id}/annotate")
  async def api_annotate(record_id: str, body: Dict[str, Any],
                         task: Optional[str] = None) -> JSONResponse:
    """A box drawn on a frame, a remark about it, and both destinations.

    The ledger entry is the point -- it outlives the window --
    so it is written first and the agent is a second, optional destination. An
    agent that is not signed in must not lose the observation.
    """
    body = body or {}
    note = validate.text(body.get("note"), "note")
    kind = validate.kind(body.get("kind"))
    ask_agent = validate.flag(body.get("ask_agent"), "ask_agent")
    thread = validate.identifier(body.get("thread") or "main", "thread")
    frame_path = validate.path_like(body.get("path"), "path")
    box_given = body.get("box")
    if box_given is not None and not isinstance(box_given, dict):
      raise BadRequest("`box` must be an object with x, y, w and h")

    record, source = library.find(validate.identifier(record_id, "record id"), task)
    if record is None or source is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)

    frame = studio.artifact_path(frame_path)
    if frame is None:
      return JSONResponse(
          {"error": "that frame is not under any run this studio is watching"},
          status_code=404)
    # And it has to be *this* run's. A record's annotation is evidence about
    # the run it is filed on; a frame borrowed from a sibling run would be
    # filed as though this run had produced it.
    if not _belongs_to(record, frame, source):
      return JSONResponse(
          {"error": f"that frame was not produced by run {record.id}"},
          status_code=404)

    box = annotate.clamp_box(box_given or {})
    ref = annotate.frame_ref(frame)
    caption = annotate.caption_for(note, box, ref)

    try:
      image = await asyncio.to_thread(annotate.burn, frame, box, note)
      key, local = await asyncio.to_thread(
          annotate.file_on_record, source.store, record, image, frame, caption)
    except Exception as exc:
      return JSONResponse({"error": f"could not annotate the frame: {exc}"},
                          status_code=500)

    from rlmcp.records import Feedback
    index: Optional[int] = None
    try:
      updated = source.store.add_feedback(record_id, Feedback(
          text=note, kind=kind, iteration=ref.get("iteration"),
          artifacts=[key]))
      index = len(updated.feedback) - 1
    except Exception as exc:
      return JSONResponse({"error": str(exc)}, status_code=400)

    await hub.publish("records.changed", {"record": record_id}, record_id)

    asked, agent_error = False, ""
    if ask_agent:
      reason = agents.provider.unavailable_reason()
      if reason:
        agent_error = reason
      else:
        try:
          await briefs.tell_agent(
              studio, record,
              annotate.message_for_agent(record_id, note, kind, box, ref,
                                         local, index),
              thread,
              images=[await asyncio.to_thread(annotate.for_agent, image)],
              shows=local,
          )
          asked = True
        except Exception as exc:
          agent_error = str(exc)

    return JSONResponse({"ok": True, "key": key, "path": local, "box": box,
                         "frame": ref, "index": index, "kind": kind,
                         "asked_agent": asked, "agent_error": agent_error})

  def _belongs_to(record, frame: Path, source) -> bool:
    """Whether a file came from this run: its session, or its own media."""
    places: List[Path] = []
    if record.session:
      places.append(Path(record.session).resolve())
    if source is not None:
      try:
        places.append(Path(source.store.media_root).resolve() / record.id)
      except Exception:
        pass
    frame = Path(frame).resolve()
    return any(place == frame or place in frame.parents for place in places)
