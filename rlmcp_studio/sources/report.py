"""A task's whole history as one file: what it would weigh, and where it goes.

The page itself is composed in the browser, out of the studio's own panels
(`web/js/report.js`): whatever the studio shows is what the file shows, with
every clip and still written in as a data URI so the file stands alone. That
leaves two things for this side. Before anything is written, :func:`plan`
prices the three media choices -- a task that filmed itself every hundred
iterations for ten runs is a few hundred megabytes of video, and the person
pressing the button is choosing between that file and a forty-megabyte one.
Afterwards, :func:`write` files the page under the task's own records root,
dated, beside ``runs/`` and ``media/``.

What is counted is counted through the studio's own sources: the clips a run
has are ``sources.clips.for_run``'s answer -- the same merge of the record's
kept copies and the session's originals the clips panel plays -- so the price
and the panel cannot disagree about which clips exist.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from rlmcp.records import RunRecord

from rlmcp_studio.sources import clips as source_clips

MEDIA_MODES = ("all", "latest", "none")
"""How much video goes in. ``all`` is every clip on every run; ``latest`` is
the newest clip of each run, which is what the story card leads with; ``none``
keeps the stills and the posters and lists the clips by caption."""

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")

MAX_BYTES = 2_000_000_000
"""How large a page the save route accepts. A bound rather than a guess: the
body is held in memory while it is written, and two gigabytes is more video
than a task's report plausibly needs."""


def plan(records: Sequence[RunRecord],
         media_root_of: Callable[[RunRecord], Optional[Path]]) -> Dict[str, Any]:
  """What a report of these records would embed, per media mode.

  Sizes rather than a guess: the file is the sum of its media. Counted, not
  read -- nothing is opened here.
  """
  totals = {mode: {"files": 0, "bytes": 0} for mode in MEDIA_MODES}
  clips = 0
  for record in records:
    found = _media_of(record, media_root_of(record))
    videos = [v for v in found["videos"] if v["path"] is not None]
    stills = [s for s in found["images"] if s["path"] is not None]
    clips += len(videos)
    for mode in MEDIA_MODES:
      chosen = _choose(videos, mode)
      for item in chosen + stills:
        totals[mode]["files"] += 1
        totals[mode]["bytes"] += item["bytes"]
      # Posters stay in every mode: a strip of stills is how the clips that
      # were left out are still listed.
      for item in videos:
        totals[mode]["bytes"] += item["poster_bytes"]
  return {
      "runs": len(records),
      "clips": clips,
      "first": records[0].date if records else "",
      "last": records[-1].date if records else "",
      "modes": totals,
  }


def write(page: str, out_dir: Path, task: str,
          at: Optional[float] = None) -> Path:
  """Save the page under the task's own records root, dated to the second.

  ``reports/`` beside ``runs/`` and ``media/``: the file belongs to the task
  it describes, and a dated name means a report written after another run
  lands beside the last one rather than over it.
  """
  out_dir = Path(out_dir)
  out_dir.mkdir(parents=True, exist_ok=True)
  stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(at or time.time()))
  safe = re.sub(r"[^A-Za-z0-9._-]+", "_", task).strip("_") or "task"
  target = out_dir / f"{safe}-{stamp}.html"
  target.write_text(page, encoding="utf-8")
  return target


def looks_like_a_page(body: bytes) -> bool:
  """Whether what arrived is an HTML document rather than something else
  posted at the route. The head of it is enough to tell."""
  head = body[:4000].lower()
  return b"<html" in head and b"<body" in body.lower()


# ── what a run has ───────────────────────────────────────────────────────

def _media_of(record: RunRecord, media_root: Optional[Path]) -> Dict[str, List[Dict[str, Any]]]:
  """Every clip and still the run has, with the path each resolves to."""
  root = Path(media_root) if media_root else None
  videos: List[Dict[str, Any]] = []
  try:
    listed = source_clips.for_run(record, root)["clips"]
  except Exception:  # noqa: BLE001 -- a listing that fails prices as no clips
    listed = []
  for clip in listed:
    if clip.get("source") == "record":
      path = root / clip["key"] if root else None
      poster = root / clip["poster"] if (root and clip.get("poster")) else None
    else:
      path = Path(clip["key"])
      poster = None
    path = path if path is not None and path.is_file() else None
    poster = poster if poster is not None and poster.is_file() else None
    videos.append({
        "key": clip["key"], "caption": clip.get("caption") or "",
        "iteration": clip.get("iteration"),
        "path": path, "bytes": path.stat().st_size if path else 0,
        "poster_bytes": poster.stat().st_size if poster else 0,
    })
  images: List[Dict[str, Any]] = []
  for kind, entries in (record.assets or {}).items():
    if kind == "videos":
      continue
    for entry in entries or []:
      if not entry:
        continue
      key = str(entry[0])
      path = root / key if root else None
      if path is not None and (not path.is_file()
                               or path.suffix.lower() not in IMAGE_SUFFIXES):
        path = None
      images.append({"kind": kind, "key": key,
                     "path": path, "bytes": path.stat().st_size if path else 0})
  return {"videos": videos, "images": images}


def _choose(videos: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
  if mode == "none":
    return []
  if mode == "latest":
    placed = [v for v in videos if v.get("iteration") is not None]
    return (placed or videos)[-1:]
  return list(videos)
