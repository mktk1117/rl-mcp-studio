"""What a task is (the Overview tab) and its whole history as one report file."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from rlmcp_studio import briefs
from rlmcp_studio.api import validate
from rlmcp_studio.context import Studio
from rlmcp_studio.sources import library as source_library
from rlmcp_studio.sources import overview as source_overview
from rlmcp_studio.sources import report as source_report


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the overview and report routes."""
  library = studio.library
  registry = studio.registry
  gates = studio.gates
  agents = studio.agents

  # ── what the task is, as opposed to how it went ───────────────────────
  async def _overview(task: str) -> Dict[str, Any]:
    """The Overview tab's answer, assembled from what the app already holds.

    Nothing here reads a simulator or writes a word: the document is an
    agent's, the measurements are the last `rlmcp check`'s, and this composes
    them with the one thing neither can know on its own -- whether the code
    moved after the report was written.
    """
    # Asked rather than taken from the cache: `cached()` is empty until
    # something has listed, and a panel reading it on a fresh backend would say
    # "nothing here registers this task" about a task that is registered --
    # the difference between *no* and *not looked yet*, stated as the first.
    # The listing is the switcher's own, cached for a minute and a half and
    # locked, so this costs nothing after the first paint of the app.
    listing = await registry.tasks()
    scope = studio.task_scope(task)
    row = next((r for r in listing.get("tasks", []) if r.get("task") == task), {})
    return source_overview.describe(
        task, scope=scope, code=studio.code_clock(scope),
        gates=gates.describe(task), row=row,
        conversations=agents.conversations(f"task:{task}"),
        # The whole listing, not this task's row: one package can register
        # several tasks, and what the panel has to be able to say is which of
        # them share this one's package and its experiment directory.
        siblings=listing.get("tasks", []))

  @app.get("/api/overview")
  async def api_overview(task: str) -> JSONResponse:
    return JSONResponse(await _overview(validate.text(task, "task", limit=200)))

  @app.post("/api/overview/write")
  async def api_overview_write(body: Dict[str, Any]) -> JSONResponse:
    """Ask an agent to write this task's report, in a thread of its own.

    Its own thread is the whole point (`source_overview.THREAD`). Writing a
    definition is minutes of reading files and taking pictures, and the
    conversation you already have open is usually watching a run while you ask
    for it -- so this must not queue behind that one, and that one must not
    have its context filled with twenty file reads. A separate thread is a
    separate provider session, so the two run side by side.
    """
    body = body or {}
    task = validate.text(body.get("task"), "task", limit=200)
    note = validate.text(body.get("note") or "", "note", required=False,
                         limit=4000)
    described = await _overview(task)
    path = source_overview.report_path(studio.task_scope(task), task)
    if path is None:
      return JSONResponse({"error": described.get("why_not") or
                           f"`{task}` has no package here"}, status_code=409)
    # `briefs.tell_task_agent` already turns a busy conversation into a 409 with the
    # provider's own sentence in it, which for this thread reads as "a report is
    # already being written" -- so it is handed back rather than reworded.
    answer = await briefs.tell_task_agent(
        studio, task, source_overview.ask(task, path, described, note),
        thread=source_overview.THREAD)
    if getattr(answer, "status_code", 200) >= 400:
      return answer
    return JSONResponse({"ok": True, "path": str(path),
                         "thread": source_overview.THREAD,
                         "subject": f"task:{task}"})

  # ── the whole history, as one file ────────────────────────────────────
  def _report_records(task: str):
    """This task's records, oldest first, and the store the newest one is in."""
    records = source_library.collect(library.sources, task)
    source = None
    if records:
      _found, source = library.find(records[-1].id, task)
    return records, source

  def _media_root_for(task: str):
    def root_of(record):
      _found, source = library.find(record.id, task)
      return getattr(source.store, "media_root", None) if source else None
    return root_of

  @app.get("/api/report/plan")
  def api_report_plan(task: str) -> JSONResponse:
    """What a report of this task would embed, sized per media choice.

    Answered before anything is written: a task that filmed itself every
    hundred iterations for ten runs is a few hundred megabytes of video, and
    the person pressing the button is choosing between that file and a
    forty-megabyte one. Nothing is opened here -- files are counted.
    """
    name = validate.text(task, "task", limit=200)
    records, source = _report_records(name)
    planned = source_report.plan(records, _media_root_for(name))
    planned["task"] = name
    planned["out_dir"] = str(Path(source.root) / "reports") if source else ""
    return JSONResponse(planned)

  @app.post("/api/report/save")
  async def api_report_save(request: Request, task: str, media: str = "all") -> JSONResponse:
    """File the page the browser composed, under the task's own records root.

    The page is the studio's own panels, serialised by `web/js/report.js`
    with every clip and still written in -- so it arrives here whole, as
    HTML rather than JSON, and this only decides where it goes. `reports/`
    beside `runs/` and `media/`, dated, which `/api/artifact` already serves
    -- as a download: an HTML file served on this origin would run with the
    studio's API in reach, and that route refuses to render one.
    """
    name = validate.text(task, "task", limit=200)
    if media not in source_report.MEDIA_MODES:
      return JSONResponse({"error": f"media must be one of {list(source_report.MEDIA_MODES)}"},
                          status_code=400)
    records, source = _report_records(name)
    if not records or source is None:
      return JSONResponse({"error": f"no runs of `{name}` are recorded here"},
                          status_code=404)
    body = await request.body()
    if len(body) > source_report.MAX_BYTES:
      return JSONResponse({"error": "that page is larger than a report can be"},
                          status_code=413)
    if not source_report.looks_like_a_page(body):
      return JSONResponse({"error": "the body is not an HTML document"}, status_code=400)
    try:
      target = await asyncio.to_thread(
          source_report.write, body.decode("utf-8", errors="replace"),
          Path(source.root) / "reports", name)
    except OSError as exc:
      return JSONResponse({"error": f"could not write the report: {exc}"}, status_code=500)
    return JSONResponse({"ok": True, "task": name, "media": media, "runs": len(records),
                         "path": str(target), "name": target.name,
                         "size": target.stat().st_size,
                         "url": f"/api/artifact?path={quote(str(target))}"})
