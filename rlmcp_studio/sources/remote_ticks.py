"""Ticks for runs on other machines, which the file watcher cannot see.

Every live update in this app is a file changing under the watcher: a
trainer writes `status.json`, the watcher says `status.tick`, the client
re-reads. A run on another machine writes its files *there*, so nothing
here changes and the watcher is silent -- the panel would paint the run
once and never again. This is the other half: a poller that asks each
remote session what its status says and publishes the same events the
watcher would have, so the client cannot tell the two apart.

Two events, both only on change. `status.tick` when the status the host
reports moved (its `seq`, else its `updated_at`, else its iteration) -- the
client re-reads everything on it, so one event is enough. And
`liveness.changed` when a remote run starts or stops answering, which is
the one fact no tick carries.

Pure at the core: `RemoteTicker.poll(addresses)` returns the events to
publish and keeps only what it needs to notice a change. The loop that
calls it on a timer lives in app.py beside the heartbeat.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

Event = Tuple[str, Dict[str, Any], str]
"""``(kind, data, scope)`` -- what `Hub.publish` takes."""

REMOTE_POLL_S = 2.0
"""How often a remote session is asked. A trainer's heartbeat is slower
than this, so a change is seen within one of its beats; a host answers a
status read in a millisecond, so forty sessions cost nothing."""


class RemoteTicker:
  """What changed on the wire since last time, as events."""

  def __init__(self) -> None:
    self._marks: Dict[str, Any] = {}
    self._alive: Dict[str, bool] = {}

  def poll(self, addresses: Iterable[str]) -> List[Event]:
    from rlmcp_studio.sources.session import LiveSession, is_address

    events: List[Event] = []
    seen = set()
    for address in addresses:
      if not is_address(address) or address in seen:
        continue
      seen.add(address)
      live = LiveSession(address)
      try:
        status = live.session.status() or {}
        alive = live.session.liveness() == "running"
      except Exception:  # noqa: BLE001 - a host that does not answer is a run that stopped
        status, alive = {}, False
      mark = status.get("seq", status.get("updated_at", status.get("iteration")))
      if mark is not None and self._marks.get(address) != mark:
        self._marks[address] = mark
        events.append(("status.tick", {"path": address}, address))
      if self._alive.get(address) != alive:
        was_known = address in self._alive
        self._alive[address] = alive
        if was_known or alive:
          events.append(("liveness.changed", {"live": alive, "address": address}, address))
    # Addresses that are no longer asked about are forgotten, so a record
    # closed and reopened later starts clean.
    for gone in [a for a in self._marks if a not in seen]:
      self._marks.pop(gone, None)
      self._alive.pop(gone, None)
    return events
