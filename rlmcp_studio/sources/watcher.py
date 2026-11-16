"""Filesystem watch, debounced into events.

A run writing status at every service tick will out-pace any client, so changes
are coalesced here rather than at the socket: one event per quiet window, not
one per write.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Iterable, List, Set

from watchfiles import awatch

from rlmcp_studio.events import KINDS

Emit = Callable[[str, dict, str], Awaitable[None]]


#: Which of rlmcp's files means which event. A table rather than a chain of
#: ifs, because this is the list a reader wants when asking "what makes the
#: client update"; every value is a key of :data:`rlmcp_studio.events.KINDS`.
MEANS = {
    "status.json": "status.tick",
    "metrics.jsonl": "metrics.append",
    "events.jsonl": "events.append",
    "meta.json": "records.changed",
}


#: What a markdown file under any watched root means. Checked before the
#: artifacts rule, because a `.md` beside the frames is prose about the run and
#: not another frame -- classifying it as an artifact would refresh the strip
#: and leave the document nobody asked to see.
DOCUMENTS = (".md", ".markdown")


def _classify(path: Path) -> str | None:
  """Which event kind a touched file means."""
  kind = MEANS.get(path.name)
  if kind is None and path.suffix.lower() in DOCUMENTS:
    kind = "docs.changed"
  if kind is None and path.parent.name == "artifacts":
    kind = "artifacts.changed"
  assert kind is None or kind in KINDS, f"unlisted event kind {kind}"
  return kind


class Watcher:
  """Watches a set of roots and emits coalesced events."""

  def __init__(self, roots: Iterable[Path], emit: Emit, debounce_ms: int = 400):
    self.roots: List[Path] = [Path(r) for r in roots if Path(r).exists()]
    self.emit = emit
    self.debounce_ms = debounce_ms
    self._task: asyncio.Task | None = None

  def start(self) -> None:
    if self._task is None and self.roots:
      self._task = asyncio.create_task(self._run(), name="rlmcp-studio-watcher")

  async def stop(self) -> None:
    if self._task:
      self._task.cancel()
      try:
        await self._task
      except asyncio.CancelledError:
        pass
      self._task = None

  async def _run(self) -> None:
    async for changes in awatch(*self.roots, debounce=self.debounce_ms,
                                recursive=True, ignore_permission_denied=True):
      kinds: Set[tuple[str, str]] = set()
      for _change, raw in changes:
        path = Path(raw)
        kind = _classify(path)
        if kind:
          kinds.add((kind, str(path.parent)))
      for kind, scope in kinds:
        await self.emit(kind, {"path": scope}, scope)
