"""The task package's source: browsing, searching, editing, and diffs between runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rlmcp_studio.api import validate
from rlmcp_studio.api.validate import BadRequest
from rlmcp_studio.context import Studio
from rlmcp_studio.sources import code as source_code
from rlmcp_studio.sources import layers
from rlmcp_studio.sources import recipe as source_recipe
from rlmcp_studio.sources import search as source_search
from rlmcp_studio.sources.session import interventions


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the source and diff routes."""
  library = studio.library
  code_roots = studio.code_roots

  # ── source and diffs ──────────────────────────────────────────────────
  @app.get("/api/source/tree")
  def api_source_tree(root: Optional[str] = None) -> JSONResponse:
    """The task package as a flat, depth-tagged listing."""
    roots = code_roots
    if not roots:
      return JSONResponse({"roots": [], "entries": [],
                           "reason": "no source root found beside the records"})
    try:
      chosen = source_code.resolve_under(validate.path_like(root, "root"),
                                         roots) if root else roots[0]
    except source_code.OutsideRoot as exc:
      return JSONResponse({"error": str(exc)}, status_code=403)
    return JSONResponse({"roots": [str(r) for r in roots], "root": str(chosen),
                         "entries": source_code.tree(chosen)})

  @app.get("/api/source/search")
  async def api_source_search(q: str, limit: int = source_search.MAX_MATCHES) -> JSONResponse:
    """Every line in the task package containing ``q``.

    The question a person actually arrives with is "where is the thing that
    computes the foot clearance", and a file tree cannot answer it. Names are
    searched in the browser, where the tree already is; this is the half that
    has to read the files.
    """
    query = validate.text(q, "q", limit=200)
    count = int(validate.number(limit, "limit", default=source_search.MAX_MATCHES,
                                low=1, high=2000))
    if not code_roots:
      return JSONResponse({"ok": False, "files": [], "count": 0,
                           "reason": "no source root found beside the records"})
    return JSONResponse(await source_search.in_files(query, code_roots, limit=count))

  @app.get("/api/source/file")
  def api_source_file(path: str) -> JSONResponse:
    path = validate.path_like(path)
    try:
      target = source_code.resolve_under(path, code_roots)
    except source_code.OutsideRoot as exc:
      return JSONResponse({"error": str(exc)}, status_code=403)
    return JSONResponse(source_code.read(target))

  @app.put("/api/source/file")
  def api_source_write(body: Dict[str, Any]) -> JSONResponse:
    """Write a file back.

    Guarded three ways, because this is the one endpoint that can damage
    something: the path must resolve under a configured root, the file must
    already exist (this saves edits, it does not create files), and the client
    must send the length it believes the file had. A mismatch means the file
    changed underneath the editor -- almost always the agent or a git checkout
    -- and silently overwriting that is how work disappears.
    """
    body = body or {}
    try:
      target = source_code.resolve_under(body.get("path", ""), code_roots)
    except source_code.OutsideRoot as exc:
      return JSONResponse({"error": str(exc)}, status_code=403)
    if not target.is_file():
      return JSONResponse({"error": "no such file"}, status_code=404)

    text = body.get("text")
    if not isinstance(text, str):
      raise BadRequest("`text` must be a string")

    # Required, not optional. A guard a client could skip by simply not
    # sending the field would allow exactly the silent overwrite it exists to
    # prevent, reachable by omission rather than by intent.
    expected = validate.number(body.get("expect_size"), "expect_size", low=0)
    actual = target.stat().st_size
    if int(expected) != actual:
      return JSONResponse(
          {"error": f"{target.name} changed on disk since you opened it "
                    f"({expected} → {actual} bytes). Reopen it before saving."},
          status_code=409)

    target.write_text(text)
    return JSONResponse({"ok": True, "path": str(target), "size": target.stat().st_size})

  @app.get("/api/diff")
  async def api_diff(record_id: str, against: Optional[str] = None,
                     task: Optional[str] = None) -> JSONResponse:
    """Config diff against another record, plus the repo's current worktree diff."""
    record, _ = library.find(record_id, task)
    if record is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    other_id = against or record.parent
    other, _ = library.find(other_id, task) if other_id else (None, None)
    if against and other is None:
      # You asked to compare against something that is not there. Answering
      # "this run has no parent" is a different statement, and a wrong one.
      return JSONResponse({"error": f"no record {against} to compare against"},
                          status_code=404)

    payload: Dict[str, Any] = {
        "record": record.id,
        "against": other.id if other else None,
        "config": source_code.config_diff(record.config, other.config if other else {})
                  if other else None,
        "no_parent": other is None,
        "change": list(record.change),
    }
    if code_roots:
      worktree = await source_code.worktree_diff(code_roots[0])
      if worktree.get("files"):
        # Mark relevance server-side, where the task name is known, and let the
        # client default to showing only those.
        hints = [h for h in (record.task or "").replace("-", " ").split() if len(h) > 3]
        for entry in worktree["files"]:
          entry["relevant"] = source_code.relevance(entry["path"], hints)
        worktree["relevant_count"] = sum(1 for e in worktree["files"] if e["relevant"])
      payload["worktree"] = worktree
      payload["head"] = await source_code.head_info(code_roots[0])
    # The code half of the recipe, from the snapshots rl-mcp stamps at launch.
    # A run that carries none says so; the worktree patch below stays either
    # way, because "what is uncommitted right now" is a different question.
    payload["code"] = source_code.snapshot_diff(record, other) if other else {
        "ok": False, "reason": "this run has no config parent to compare with"}
    # The third layer: what somebody did to each run while it trained. Config
    # says what it started from, code says what it ran; this says what happened
    # to it, which is the half of a result that usually explains the other.
    mine = (interventions(Path(record.session)) if record.session else
            {"ok": False, "items": [], "reason": "this run kept no session"})
    theirs = (interventions(Path(other.session))
              if other is not None and other.session else
              {"ok": False, "items": [], "reason": ""})
    payload["interventions"] = {"this": mine, "against": theirs}
    # Kept apart, and joined by exactly one fact: which of the fields the
    # config comparison covered did not stay at their launch value. Without it
    # the two layers sit on one screen contradicting each other: the config
    # comparison says nothing differs while the live edits show a field that
    # was changed mid-run.
    payload["config"] = layers.annotate_config(payload["config"], mine, theirs)
    payload["snapshot_note"] = (
        "" if payload["code"].get("ok") else
        "The patch below is this repository right now, not what that run used.")
    return JSONResponse(payload)

  @app.get("/api/recipe")
  async def api_recipe(record_id: str, task: Optional[str] = None) -> JSONResponse:
    """The recipe at a record: the fold of its whole ancestry.

    Deliberately *not* filtered to the task. `/api/diff` compares two runs and
    `/api/records/graph` draws one task's tree, but ancestry crosses tasks --
    a run of one task can have its config parent in another -- so the fold
    reads the store the record was found in, whole. The task is still
    passed, because it is what disambiguates an id across two records roots.
    """
    record, source = library.find(validate.identifier(record_id, "record id"), task)
    if record is None or source is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    return JSONResponse(source_recipe.fold(source.records(), record.id))
