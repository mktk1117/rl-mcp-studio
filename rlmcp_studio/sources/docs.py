"""Markdown documents, gathered from the places an agent actually writes them.

The app already has a word for what a *run* produces -- ``artifacts``, the
frames and clips in ``<session>/artifacts`` -- and this is deliberately not
that. This is the other thing that gets written about a run: prose. An agent
asked to plan an experiment, or to write it up afterwards, produces a markdown
document, and reading one should not mean leaving the studio.

Three places, because those are the three an agent is standing in when it
writes one:

* **the record's own directory**, which is where rl-mcp itself puts ``PLAN.md``
  and ``REPORT.md`` (``rlmcp.records.report``). Anything else dropped beside
  them belongs to the same run and is listed with them.
* **the session's ``artifacts`` directory**, for a run whose record is lagging
  or which nobody registered -- the same argument ``clips.py`` makes for
  clips, for the same reason.
* **the task repository**, at its top level and one folder down, which is
  where the agent's working directory points (``app._agent_cwd``) and
  therefore where ``Write NOTES.md`` lands.

Nothing here decides what a document *means*. It finds files, reads their first
heading for a title, and hands back paths the reading route will serve.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from rlmcp.records import RunRecord

SUFFIXES = (".md", ".markdown")

MAX_BYTES = 512_000
"""How much of a document is served.

A bound rather than a guess: these are read into a browser tab on one fetch,
and a stray log renamed `.md` should not be the thing that decides how much
memory the page uses. What is left out is reported, never silently dropped.
"""

TITLE_SCAN = 4096
"""How far into a file to look for its heading. A title is at the top or it is
not a title."""

#: Directories under the task repository that are worth walking for prose.
#: Everything else there is code, and the Source view is what reads code.
TASK_FOLDERS = ("docs", "notes")


def _title(path: Path) -> str:
  """The document's first heading, or "" -- never a guess from the filename.

  A file whose first line is not a heading has no title, and inventing one
  from `training-notes.md` would put words in the writer's mouth
  in a list that is read as what the documents say about themselves.
  """
  try:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
      head = handle.read(TITLE_SCAN)
  except OSError:
    return ""
  for line in head.splitlines():
    stripped = line.strip()
    if stripped.startswith("#"):
      return stripped.lstrip("#").strip()
    if stripped:
      return ""            # prose before any heading: this file has no title
  return ""


def describe(path: Path, source: str) -> Dict[str, Any]:
  """One document, as the client lists it."""
  try:
    stat = path.stat()
  except OSError:
    return {}
  return {
      "path": str(path),
      "name": path.name,
      "title": _title(path),
      "source": source,
      "at": stat.st_mtime,
      "size": stat.st_size,
  }


def _in(directory: Optional[Path], source: str, *, depth: int = 0,
        folders: Iterable[str] = ()) -> List[Dict[str, Any]]:
  """Markdown directly in ``directory``, plus the named folders below it."""
  if directory is None:
    return []
  directory = Path(directory)
  if not directory.is_dir():
    return []
  found: List[Dict[str, Any]] = []
  places = [directory]
  if depth:
    places += [directory / name for name in folders]
  for place in places:
    if not place.is_dir():
      continue
    try:
      entries = sorted(place.iterdir())
    except OSError:
      continue
    for path in entries:
      if path.is_file() and path.suffix.lower() in SUFFIXES:
        described = describe(path, source)
        if described:
          found.append(described)
  return found


def _record_dir(record: RunRecord, store: Any) -> Optional[Path]:
  """Where this record lives, asked of the store rather than rebuilt here.

  ``FileStore.record_dir`` composes `<id>-<slug>`, which is a naming rule the
  studio must not hold a second copy of -- a record renamed upstream would
  otherwise leave this view reading an empty folder and saying "nothing yet".
  """
  try:
    return Path(store.record_dir(record))
  except Exception:               # noqa: BLE001 -- another store shape, or none
    return None


def order(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
  """Newest first. What an agent just wrote is what you came to read."""
  return sorted(docs, key=lambda d: -(d.get("at") or 0))


def for_run(record: Optional[RunRecord], store: Any = None,
            code_roots: Iterable[Path] = ()) -> Dict[str, Any]:
  """Every document one run and its task have, grouped by where it was found.

  Grouped rather than merged, unlike ``clips.for_run``: two clips of iteration
  400 are one clip, but ``README.md`` in the task repo and ``REPORT.md`` in the
  record are not two copies of anything, and a flat list would put the task's
  standing notes among this run's write-up with nothing to tell them apart.
  """
  groups: List[Dict[str, Any]] = []

  if record is not None:
    directory = _record_dir(record, store) if store is not None else None
    docs = _in(directory, "record")
    docs += _in(Path(record.session) / "artifacts" if record.session else None,
                "session")
    groups.append({
        "id": "run",
        "label": f"{record.id} · {record.slug}" if record.slug else record.id,
        "blurb": "written about this run",
        "dir": str(directory) if directory else "",
        "docs": order(docs),
    })

  for root in code_roots:
    root = Path(root)
    docs = _in(root, "task", depth=1, folders=TASK_FOLDERS)
    groups.append({
        "id": "task",
        "label": root.name or str(root),
        "blurb": "written about the task",
        "dir": str(root),
        "docs": order(docs),
    })

  return {
      "groups": groups,
      "count": sum(len(g["docs"]) for g in groups),
      # Where a document goes to be listed here, said once so the view can
      # show it: an agent has to be told a directory, not a convention.
      "write_to": next((g["dir"] for g in groups if g["id"] == "run" and g["dir"]),
                       next((g["dir"] for g in groups), "")),
  }


def read(path: Path) -> Dict[str, Any]:
  """One document's text, bounded, with whatever was left out declared."""
  path = Path(path)
  try:
    stat = path.stat()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
      text = handle.read(MAX_BYTES)
  except OSError as exc:
    return {"ok": False, "path": str(path), "reason": str(exc)}
  return {
      "ok": True,
      "path": str(path),
      "name": path.name,
      "title": _title(path),
      "text": text,
      "at": stat.st_mtime,
      "size": stat.st_size,
      "truncated": stat.st_size > len(text.encode("utf-8", errors="replace")),
  }
