"""A run's clips, oldest first, ready to play.

Two places hold them and both matter. The **record** holds the durable copies:
the trainer files each progress clip into the media store as it is made, so a
finished run still has its trajectory after the logs are cleaned. The **session**
holds the ones that have not made it there yet -- a run whose record write is
lagging, or a run nobody registered, still writes its clips into
``<session>/artifacts``.

So this merges the two, record first, keyed by iteration: the durable copy of
iteration 400 wins over the session's copy of iteration 400, and a clip that
only exists in the session is shown rather than waited for. That is the whole
of the logic here -- what a clip *is*, which iteration it belongs to and what
still stands for it are all answered by ``rlmcp.records.clips`` and
``rlmcp.records.poster``, because the studio does not get to have its own idea
about the shape of a record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from rlmcp.records import RunRecord
from rlmcp.records import clips as _clips
from rlmcp.records.poster import record_posters

VIDEO_SUFFIXES = (".mp4", ".webm", ".gif")


def _artifact_url(path: Path | str) -> str:
  return f"/api/artifact?path={quote(str(path))}"


def from_record(record: RunRecord, media_root: Optional[Path]) -> List[Dict[str, Any]]:
  """Clips attached to the record, with a still each where one can be made."""
  if media_root is None:
    return []
  posters: Dict[str, str] = {}
  try:
    posters = record_posters(record, media_root)
  except Exception:  # noqa: BLE001 -- a thumbnail may never fail a listing.
    posters = {}
  out: List[Dict[str, Any]] = []
  for clip in _clips.from_record(record, posters=posters):
    key = clip["key"]
    out.append({
        **clip,
        "source": "record",
        "url": _artifact_url(Path(media_root) / key),
        "poster_url": (_artifact_url(Path(media_root) / clip["poster"])
                       if clip["poster"] else ""),
    })
  return out


def from_session(session_dir: Optional[str]) -> List[Dict[str, Any]]:
  """Clips sitting in a live run's artifacts directory."""
  if not session_dir:
    return []
  directory = Path(session_dir) / "artifacts"
  if not directory.is_dir():
    return []
  out: List[Dict[str, Any]] = []
  for path in directory.iterdir():
    if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
      continue
    out.append({
        "key": str(path),
        "caption": "",
        "iteration": _clips.iteration_of(path.name),
        "poster": "",
        "poster_url": "",
        "scheduled": _clips.is_progress_clip(path.name),
        "source": "session",
        "url": _artifact_url(path),
        "at": path.stat().st_mtime,
    })
  return out


def for_run(record: RunRecord, media_root: Optional[Path]) -> Dict[str, Any]:
  """Every clip of one run, oldest first, with the newest one named.

  ``latest`` is what a viewer opens with. It is the *newest* clip rather than
  the first, because the question a live run is being watched for is "what does
  it look like now" -- iteration 0 is one click away in the strip, which is
  where a comparison starts anyway.
  """
  merged: Dict[Any, Dict[str, Any]] = {}
  for clip in from_record(record, media_root) + from_session(record.session):
    # Iteration is identity when it is known: the record's durable copy and the
    # session's original are one clip, listed once. Anything unplaceable keys
    # on its own path so two of them cannot collide.
    key = ("it", clip["iteration"]) if clip["iteration"] is not None else ("k", clip["key"])
    merged.setdefault(key, clip)
  ordered = _clips.order(list(merged.values()))
  scheduled = [c for c in ordered if c["scheduled"]]
  # The newest *placed* clip: `order` keeps the unplaceable ones at the end, and
  # one of those standing in for "now" would be a guess.
  placed = [c for c in ordered if c["iteration"] is not None]
  newest = (placed or ordered or [None])[-1]
  return {
      "clips": ordered,
      "count": len(ordered),
      "scheduled": len(scheduled),
      "latest": newest["url"] if newest else "",
      "latest_iteration": newest["iteration"] if newest else None,
      "iterations": [c["iteration"] for c in scheduled if c["iteration"] is not None],
  }
