"""The live side: status, metrics and artifacts from a run's session directory.

Read through ``rlmcp.session``. The studio never opens ``metrics.jsonl``
itself -- that is the first thing a GUI is tempted to do and the reason this
module is thin.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from rlmcp.session import Session, iter_sessions


def is_address(value: Any) -> bool:
  """A wire address (`http://host/v1/sessions/<key>`) as opposed to a directory."""
  return isinstance(value, str) and value.startswith(("http://", "https://"))


HOST_TOKENS: Dict[str, str] = {}
"""Bearer tokens by host URL, registered by whoever configured the host
(`runners/remote.py`), so a session address on that host opens with the
right credential without the token ever appearing in the address."""


def register_host_token(url: str, token: str) -> None:
  if url and token:
    HOST_TOKENS[url.rstrip("/")] = token


def token_for(address: str) -> str:
  for url, token in HOST_TOKENS.items():
    if str(address).startswith(url + "/"):
      return token
  return os.environ.get("RLMCP_HOST_TOKEN", "")


def open_client(address: "str | Path"):
  """The right client for an address, and the only place this module picks one.

  A directory gives rlmcp's `Session`; a URL gives its `WireSession`, through
  `rlmcp.wire.connect`. Everything below is written against the seventeen
  names of `WIRE_SURFACE` and nothing else, which is what lets a run on
  another machine appear in the same panels.
  """
  if is_address(address):
    from rlmcp import wire
    return wire.connect(str(address), token=token_for(str(address)))
  return Session.open(Path(address))

def _liveness(session: Any) -> str:
  """rlmcp's own verdict: `running`, `stalled` or `dead`, from either transport.

  Asked of the session rather than derived here: a local one checks its pid,
  a remote one asks the host that can see the process. `stalled` is advisory
  -- one long iteration can look like it. A host that does not answer is a
  run this studio cannot see, and says so the way a gone pid does.
  """
  try:
    return str(session.liveness() or "dead")
  except Exception:  # noqa: BLE001 - an unreachable host, not a crash
    return "dead"


class LiveSession:
  """One rlmcp session, wherever it is: a directory here, or a URL on a host.

  Nothing in this class touches a path. ``address`` is what it was given and
  what it hands on -- the client below is the only thing that knows whether
  that string is a directory or a URL.
  """

  def __init__(self, address: "str | Path"):
    self.address = str(address)
    self._session: Any = None

  @property
  def dir(self) -> Path:
    """The directory, for a caller that still needs one -- local runs only.

    Kept so the terminal, which opens a shell *in* a session, keeps working;
    a remote run has no directory to give and this raises. New code reads
    through :attr:`session` instead.
    """
    if is_address(self.address):
      raise FileNotFoundError(f"{self.address} is on another machine; it has no directory here")
    return Path(self.address)

  @property
  def session(self):
    if self._session is None:
      self._session = open_client(self.address)
    return self._session

  def exists(self) -> bool:
    """Whether there is a session here at all.

    Asked of rlmcp rather than of the filesystem: ``Session.open`` is the
    definition of "this directory is a session", and it refuses one that is
    missing its ``session.json`` with exactly this message. A run that has
    started but not yet published a status is a session -- it just has no
    numbers yet -- and probing for the status file would call it absent. On
    the wire the same question is whether the host knows the key.
    """
    try:
      client = open_client(self.address)
      if is_address(self.address):
        client.info()
      return True
    except FileNotFoundError:
      return False
    except Exception:  # a host that does not answer, or does not know the key
      return False

  def snapshot(self, metrics_tail: int = 240) -> Dict[str, Any]:
    """Everything the status strip and the charts need, in one read."""
    if not self.exists():
      return {"ok": False, "reason": f"no session at {self.address}"}
    session = self.session
    status = session.status() or {}
    info = session.info() or {}
    rows = session.metrics(last_n=metrics_tail) or []
    return {
        "ok": True,
        # `dir` is the field name the client reads, and it carries an address
        # (directory or URL): the client composes `${dir}/artifacts/${name}`
        # and the artifact route resolves either form.
        "dir": self.address,
        "address": self.address,
        "state": _liveness(session),
        "task": info.get("task", ""),
        "started_at": info.get("started_at"),
        "status": status,
        "metrics": rows,
        "series": _series(rows),
        "artifacts": self.artifacts(),
    }

  def schema(self) -> Dict[str, Any]:
    """The parameter schema the trainer published to disk, by key.

    Read rather than asked for: callers that only need to know a parameter's
    *shape* -- what type it is, what it currently holds -- should not cost a
    live run a round trip to learn what it already wrote down.
    """
    try:
      published = self.session.params() or {}
    except Exception:
      return {}
    if isinstance(published, dict) and "parameters" in published:
      published = published["parameters"]
    if isinstance(published, list):
      return {p.get("key"): p for p in published if isinstance(p, dict)}
    return published if isinstance(published, dict) else {}

  def artifacts(self, limit: int = 24) -> List[Dict[str, Any]]:
    """What the run produced, newest first -- asked of the session, not a directory.

    `list_artifacts` is the method rl-mcp provides so a client does not join
    a path itself: the rows carry `name`, `bytes` and `modified_at`
    on either transport, and a `path` only on the local one, which nothing
    here reads.
    """
    try:
      rows = self.session.list_artifacts() or []
    except Exception:
      return []
    return [
        {"name": r["name"], "size": r.get("bytes", 0), "at": r.get("modified_at", 0.0),
         "kind": Path(r["name"]).suffix.lstrip(".").lower()}
        for r in rows[:limit] if r.get("name")
    ]

  def read_artifact(self, name: str) -> bytes:
    """One artifact's bytes, by the name :meth:`artifacts` gave. Either transport."""
    return self.session.read_artifact(name)



#: What ``Session.append_metrics`` puts on a row that is not a metric: the x
#: axis, the wall clock the row was written at, and the reader's cursor.
#: Charting ``t`` draws the time of day climbing steadily and labels it as
#: something the run measured; charting ``seq`` draws a straight line.
#:
#: rlmcp owns this list -- it writes the rows -- so the studio asks rather than
#: keeping its own copy.
from rlmcp.session import RESERVED_METRIC_KEYS  # noqa: E402

NOT_A_METRIC = set(RESERVED_METRIC_KEYS)


def _series(rows: List[Dict[str, Any]]) -> Dict[str, List[List[float]]]:
  """Metric rows to ``{name: [[iteration, value], ...]}``.

  Names are whatever the run published -- discovered, never listed here, so an
  extension's own metrics chart themselves without the studio learning them.
  The only two names this function knows are the ones rlmcp writes itself.
  """
  out: Dict[str, List[List[float]]] = {}
  for row in rows:
    iteration = row.get("iteration")
    if iteration is None:
      continue
    for key, value in row.items():
      if key in NOT_A_METRIC or not isinstance(value, (int, float)):
        continue
      out.setdefault(key, []).append([float(iteration), float(value)])
  return out


def find_sessions(root: Path) -> List[Path]:
  """Every training session under a logs root, newest first.

  ``rlmcp.session.iter_sessions`` already answers this, and answers it better
  than a glob does: it reads each session's own ``started_at`` rather than a
  file's mtime, and it leaves out *play* sessions -- which are newer than the
  run they replay, so a glob would happily offer a replay as the latest run.
  """
  return [session.dir for session in iter_sessions(Path(root))]


def events(address: "str | Path", last_n: Optional[int] = None) -> List[Dict[str, Any]]:
  """The audit trail, read through the session -- a directory or a URL."""
  try:
    client = open_client(address)
  except FileNotFoundError:
    return []
  try:
    return [e for e in (client.events(last_n=last_n) or []) if isinstance(e, dict)]
  except Exception:
    return []


def interventions(directory: "str | Path") -> Dict[str, Any]:
  """What somebody *did* to this run, read through rl-mcp's own vocabulary.

  The third layer of a run's history (after its code and its launch config;
  see ``sources/layers.py``), and the one the studio must
  not invent a list of kinds for: which events count as decisions is rlmcp's
  question, and a viewer with its own list goes stale the moment a command is
  added. An rl-mcp too old to answer says so rather than being second-guessed.
  """
  try:
    from rlmcp.records.interventions import from_events
  except ImportError:
    return {"ok": False, "items": [], "layers": {}, "changes": 0, "remarks": 0,
            "parameters": [],
            "reason": "the installed rl-mcp does not read interventions yet. "
                      "A newer rl-mcp does, and the panel fills in once it "
                      "is upgraded"}
  rows = events(directory)
  items = from_events(rows)
  tally: Dict[str, int] = {}
  for item in items:
    tally[item["layer"]] = tally.get(item["layer"], 0) + 1
  # rlmcp files a note or a piece of feedback under the `said` layer: somebody
  # wrote a judgement down rather than changing anything. Counted with the rest
  # it inflates the one number a reader trusts -- a run with one parameter
  # edit and one frame annotation would be summarised as "2 runtime edits".
  remarks = tally.get("said", 0)
  return {"ok": True, "items": items, "layers": tally,
          "changes": len(items) - remarks, "remarks": remarks,
          "parameters": _moved_parameters(rows)}


def _moved_parameters(rows: List[Dict[str, Any]]) -> List[str]:
  """The parameter keys this run moved after launch, first-touched order.

  ``from_events`` phrases each edit as a sentence for a reader, which is what a
  timeline wants; the key on its own is what lines the runtime layer up against
  the launch config, and taking it off rlmcp's own event field is more honest
  than parsing that sentence back apart. The three kinds are the ones rlmcp's
  own parameter history reads (``rlmcp/records/params.py``): a live edit, a
  reset to launch values, and a curriculum stage applying its own.
  """
  keys: List[str] = []

  def note(key: Any) -> None:
    if isinstance(key, str) and key and key not in keys:
      keys.append(key)

  for row in rows:
    kind = row.get("kind")
    if kind == "set_parameter":
      # A refused write moved nothing, so it is not a reason to caveat a
      # config comparison -- it is still in `items`, where it reads as refused.
      if row.get("applied", True):
        note(row.get("key"))
    elif kind == "reset_parameters":
      for key in row.get("keys") or []:
        note(key)
    elif kind == "curriculum_stage":
      for key in (row.get("applied") or {}).get("parameters") or {}:
        note(key)
  return keys


def parameter_traces(directory: "str | Path") -> Dict[str, Any]:
  """Parameter values as step functions over **iteration**.

  A parameter is flat until something changes it, so its history is a step
  function, not a curve -- and the x axis is the iteration, so a from-scratch
  run starts at 0 and an edit in the middle is a visible step.

  Everything here comes from the events rlmcp already writes:
  ``set_parameter`` carries ``{iteration, key, old, new, applied, rationale}``,
  which is the step and the reason for it. ``curriculum_stage`` gives the bands
  behind them, because entering a stage changes parameters too.
  """
  rows = events(directory)
  traces: Dict[str, Dict[str, Any]] = {}
  stages: List[Dict[str, Any]] = []
  last_iteration = 0

  for row in rows:
    kind = row.get("kind")
    iteration = int(row.get("iteration") or 0)
    last_iteration = max(last_iteration, iteration)

    if kind == "set_parameter":
      # A refused write changed nothing, so it is not part of the trace --
      # but it is still worth surfacing, so keep it flagged.
      key = row.get("key")
      if not key:
        continue
      trace = traces.setdefault(key, {"key": key, "steps": []})
      trace["steps"].append({
          "iteration": iteration,
          "old": row.get("old"),
          "new": row.get("new"),
          "applied": bool(row.get("applied", True)),
          "why": row.get("rationale") or "",
          "by": "agent",
      })

    elif kind == "curriculum_stage":
      stages.append({
          "iteration": iteration,
          "name": row.get("stage") or row.get("name") or "",
          "why": row.get("reason") or row.get("why") or "",
      })

    elif kind == "reset_parameters":
      for key in row.get("keys") or []:
        trace = traces.setdefault(key, {"key": key, "steps": []})
        trace["steps"].append({
            "iteration": iteration, "old": None, "new": None,
            "applied": True, "why": "reset to launch value", "by": "reset",
        })

  return {
      "traces": sorted(traces.values(), key=lambda t: -len(t["steps"])),
      "stages": stages,
      "last_iteration": last_iteration,
      "changed": len(traces),
  }
