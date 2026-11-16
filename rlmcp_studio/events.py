"""The event envelope: the only shape the client ever receives.

One socket, one envelope, dispatched on ``kind``. Adding a kind is additive and
never breaks an older client, and ``seq`` is monotonic per scope so a client
that reconnects can say what it last saw and get the gap replayed -- which is
the failure a static page cannot recover from.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Dict

VERSION = 1

#: Every kind the server can send, in one place.
#:
#: Not an enum and not enforced -- publishing an unlisted kind still works,
#: because a new kind must stay additive. It is here to be *read*: by a person
#: adding a handler, and by the test that checks the client is not listening
#: for something this side stopped sending. That failure is otherwise silent,
#: with a connected socket and a UI that has quietly stopped updating.
KINDS = {
    "hello": "sent on connect, carries the replay window",
    "status.tick": "the run published a status; the heartbeat",
    "metrics.append": "new metric rows",
    "events.append": "new entries in the run's audit trail",
    "artifacts.changed": "a frame or clip appeared",
    "docs.changed": "a markdown document was written or changed",
    "records.changed": "a record was written -- by rlmcp, or by us",
    "sessions.rescanned": "the set of live sessions changed under the watcher",
    "liveness.changed": "a trainer started or stopped answering; the one event "
                        "no file change announces",
    "run.command": "a command was sent to a trainer, and how it went",
    "agent.auth": "the sign-in flow moved",
}
"""``agent.<kind>`` events are published from the agent host with the kinds in
:class:`~rlmcp_studio.agents.base.AgentEvent`; they are namespaced rather than
listed one by one because the provider decides that vocabulary."""

_seq = itertools.count(1)

HOST = ""
"""The id of the machine this studio is on, stamped on every event.

Stable per machine, not per process (`config.resolve` keeps it in the
settings file), and never a path: on a fleet a run is named by
``(host, run)`` and every event, artifact key and tool argument carries the
pair. On one desktop it is one constant, which is the degenerate case the
remote runner is designed as. Set once at startup with :func:`set_host`.
"""


def set_host(host_id: str) -> None:
  global HOST
  HOST = host_id or ""


@dataclass
class Event:
  kind: str
  """Namespaced: ``runs.changed``, ``status.tick``, ``metrics.append``, ``agent.token``."""

  data: Dict[str, Any] = field(default_factory=dict)
  scope: str = ""
  """What this is about -- a run id, a task, or empty for studio-wide."""

  run: str = ""
  """The record id this is about, when it is about one. Distinct from
  ``scope``, which the watcher fills with a directory and the agent host with
  a thread key: those are what the client dispatches on today, and this is
  the stable name a fleet dispatches on tomorrow. Empty for studio-wide."""

  host: str = field(default_factory=lambda: HOST)
  """Where it happened. See :data:`HOST`."""

  seq: int = field(default_factory=lambda: next(_seq))
  at: float = field(default_factory=time.time)

  def to_dict(self) -> Dict[str, Any]:
    return {
        "v": VERSION,
        "seq": self.seq,
        "kind": self.kind,
        "scope": self.scope,
        "run": self.run,
        "host": self.host,
        "at": self.at,
        "data": self.data,
    }


def event(kind: str, data: Dict[str, Any] | None = None, scope: str = "",
          run: str = "") -> Event:
  return Event(kind=kind, data=data or {}, scope=scope, run=run)
