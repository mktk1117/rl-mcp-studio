"""Documents an agent wrote, and the task library."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rlmcp_studio.api import validate
from rlmcp_studio.context import Studio
from rlmcp_studio.sources import code as source_code
from rlmcp_studio.sources import docs as source_docs
from rlmcp_studio.sources import library as source_library
from rlmcp_studio.sources import registry as source_registry
from rlmcp_studio.sources import starting as source_starting


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the document and library routes."""
  cfg = studio.cfg
  library = studio.library
  code_roots = studio.code_roots
  registry = studio.registry
  starting = studio.starting

  # ── what an agent wrote about it ──────────────────────────────────────
  def _doc_roots() -> List[Path]:
    """Everywhere a document may legitimately be read from.

    The same shape as `Studio.artifact_path`, and separate from it on purpose: an
    artifact is confined to a session because that is where a run writes its
    frames, and prose is not. A record's own directory is under the records
    root, and the notes an agent writes are in the task repository -- which is
    where its working directory points, so it is where `Write NOTES.md` lands.
    """
    roots = [Path(r) for r in cfg.records]
    roots += [Path(r) for r in code_roots]
    roots += library.session_dirs()
    return roots

  @app.get("/api/docs")
  def api_docs(record_id: Optional[str] = None,
               task: Optional[str] = None) -> JSONResponse:
    """The markdown belonging to one run, and to the task it belongs to.

    `record_id` is optional: a task being written has no run, and its notes
    are the ones most worth reading before the first one exists.
    """
    record = source = None
    if record_id:
      record, source = library.find(record_id, task)
      if record is None:
        return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    store = source.store if source is not None else None
    return JSONResponse(source_docs.for_run(record, store, code_roots))

  @app.get("/api/doc")
  def api_doc(path: str) -> JSONResponse:
    """One document's text, as JSON.

    Not served through `/api/artifact`, which hands a `.md` back as an
    octet-stream download -- deliberately, since anything served as a file on
    this origin runs with the studio's own API in reach. Text in a JSON field
    is inert, and the browser renders it into nodes rather than into markup.
    """
    path = validate.path_like(path)
    try:
      target = source_code.resolve_under(path, _doc_roots())
    except source_code.OutsideRoot as exc:
      return JSONResponse({"error": str(exc)}, status_code=403)
    if target.suffix.lower() not in source_docs.SUFFIXES:
      return JSONResponse({"error": "that is not a markdown document"},
                          status_code=404)
    if not target.is_file():
      return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(source_docs.read(target))

  # ── the task library ──────────────────────────────────────────────────
  async def _task_rows() -> List[Dict[str, Any]]:
    """The switcher's own list, so the library cannot disagree with it.

    Registry ∪ records ∪ the ones begun here, exactly as `/api/tasks` builds
    it. The registry half is cached for a minute and a half, so asking for it
    again here costs nothing.
    """
    listing = await registry.tasks()
    rows, _ = source_registry.merge(library.tasks(), listing)
    return source_starting.merge(rows, starting.all())

  def _code_clock_for_tasks() -> Callable[[str], float]:
    """When a task's own files last changed, asked once per directory.

    The shelf is ordered by when a task was last worked on, and a task that has
    never trained has no record to date it -- which is precisely the task
    somebody is in the middle of writing. Its files are the only clock it has.

    Memoised on the directory rather than the task: one package registers many
    tasks, and walking `my_tasks` twenty times to get the same number
    twenty times is the whole cost of this feature paid over again.
    """
    seen: Dict[str, float] = {}

    def at(task: str) -> float:
      where = studio.task_scope(task).get("dir") or ""
      if not where:
        return 0.0
      if where not in seen:
        seen[where] = float(source_code.newest_change(Path(where)).get("at") or 0.0)
      return seen[where]

    return at

  @app.get("/api/library")
  async def api_library() -> JSONResponse:
    """Every task, with enough of its card to decide which one to open.

    Counts rather than entries: thirty tasks with their findings inlined is a
    payload nobody reads and a page nobody can scan. The card route is where
    the reading happens.
    """
    rows = await _task_rows()
    return JSONResponse({
        "tasks": source_library.index(rows, source_library.by_task(library.sources),
                                      code_at=_code_clock_for_tasks()),
    })

  @app.get("/api/library/card")
  async def api_library_card(task: str) -> JSONResponse:
    """One task's card: what it is, what its runs concluded, what to read.

    A task with no runs still has a card, because a task being built is known
    from the registry before it has any record. That is the state a task being built spends its first hour in, and a
    blank page there would say the task does not exist.
    """
    name = validate.text(task, "task", limit=200)
    rows = await _task_rows()
    row = next((r for r in rows if r.get("task") == name), None)
    records = source_library.collect(library.sources, name)
    # The documents, asked of the same module the Docs view uses. Anchored on
    # the newest record so the run write-ups come with the task's own notes;
    # with no runs at all it is the notes alone, which is the whole reading
    # list a task being built has.
    latest = records[-1] if records else None
    store = None
    if latest is not None:
      _found, source = library.find(latest.id, name)
      store = source.store if source is not None else None
    docs = source_docs.for_run(latest, store, code_roots)
    begun = next((b.get("what", "") for b in starting.all()
                  if b.get("task") == name), "")
    return JSONResponse(source_library.card(name, records, row, docs, begun,
                                           library_roots=cfg.library))
