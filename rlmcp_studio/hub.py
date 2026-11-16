"""Fan-out of events to every connected client."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import WebSocket

from rlmcp_studio.events import Event, event

REPLAY = 256
"""How many events a reconnecting client can ask to have replayed."""


class Hub:
  """Fan-out to every connected client, with a small replay buffer.

  The buffer is what makes a dropped socket recoverable: a client reconnects
  with the last ``seq`` it saw and gets the gap, rather than silently showing
  stale numbers.
  """

  def __init__(self) -> None:
    self.clients: Set[WebSocket] = set()
    self.backlog: List[Event] = []
    self.resolve_run: Optional[Callable[[str], str]] = None
    """Names the run a directory belongs to (`Library.record_for_path`), for
    the publishers that only know a path: the watcher, mostly."""

  async def publish(self, kind: str, data: Dict[str, Any], scope: str = "",
                    run: str = "") -> None:
    # Every event names its run when it has one (a run is `(host, run)` on
    # the wire, never a path). Publishers that know the
    # record say so in `data`; the watcher knows a directory, and the
    # library knows whose it is.
    if not run:
      run = str(data.get("record") or data.get("run") or "")
    if not run and scope and self.resolve_run is not None:
      try:
        run = self.resolve_run(scope) or ""
      except Exception:
        run = ""
    evt = event(kind, data, scope, run=run)
    self.backlog.append(evt)
    del self.backlog[:-REPLAY]
    payload = json.dumps(evt.to_dict())
    for client in list(self.clients):
      try:
        await client.send_text(payload)
      except Exception:
        self.clients.discard(client)

  def since(self, seq: int) -> List[Dict[str, Any]]:
    return [e.to_dict() for e in self.backlog if e.seq > seq]
