"""Run records, read through rlmcp -- never parsed here.

The studio does not know what a record looks like on disk. It asks
``rlmcp.records`` and reshapes the answer for the wire. If something is missing
from this module the fix is usually a PR to rl-mcp, not a helper here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from rlmcp.records import RunRecord, open_store
from rlmcp.records import graph as _graph

# rl-mcp's own verdict palette. The studio must not invent a second one.
from rlmcp.records.views import VERDICT_COLORS

UNKNOWN_TASK = "(unknown)"
"""``RunRecord.task`` is empty on records written before the field existed.

Those runs are grouped under one heading rather than dropped -- the same choice
the field's own docstring describes.
"""


def _one_line(record: RunRecord) -> str:
  """The sentence a reader gets who will not open the record."""
  try:
    return record.one_line()
  except Exception:
    return record.headline or record.outcome or record.hypothesis or ""


def record_summary(record: RunRecord) -> Dict[str, Any]:
  """The shape every list, card and tree node is built from."""
  weights = getattr(record, "weights", None)
  return {
      "id": record.id,
      "slug": record.slug,
      "seq": record.seq,
      "task": record.task or UNKNOWN_TASK,
      "stage": record.stage,
      "date": record.date,
      "verdict": record.verdict,
      "line": _one_line(record),
      "parent": record.parent,
      "weights": getattr(weights, "run", None) if weights else None,
      "session": record.session,
      "change": list(record.change),
      "metrics": [list(m) for m in record.metrics],
      # What the record kept, as opposed to what its logs happen to still
      # hold. A clip in logs/ is deleted with the logs; one promoted here is
      # part of the record, which is the deliverable.
      "assets": {kind: [list(entry) for entry in entries]
                 for kind, entries in (record.assets or {}).items()},
      "feedback_open": sum(1 for f in record.feedback if _unanswered(f)),
      "feedback_total": len(record.feedback),
  }


def _unanswered(feedback: Any) -> bool:
  """rlmcp's rule: the four kinds that ask for something and got no response."""
  try:
    from rlmcp.records import OWED_A_RESPONSE
    return feedback.kind in OWED_A_RESPONSE and not feedback.response
  except Exception:
    return False


class RecordsSource:
  """One records root, opened through rlmcp's store."""

  def __init__(self, root: Path):
    self.root = Path(root)
    self._store = None

  @property
  def store(self):
    if self._store is None:
      self._store = open_store(self.root)
    return self._store

  def available(self) -> bool:
    return self.root.exists()

  def records(self) -> List[RunRecord]:
    if not self.available():
      return []
    return self.store.list_records()

  def summaries(self) -> List[Dict[str, Any]]:
    return [record_summary(r) for r in self.records()]

  def get(self, record_id: str) -> Optional[RunRecord]:
    return self.store.get_record(record_id)

  def param_history(self, task: Optional[str] = None) -> Dict[str, Any]:
    """How every parameter moved, across the whole ancestry.

    `rlmcp.records.params.build_history` does all of it, and its own docstring
    says the payload is "deliberately viewer-shaped" -- runs in draw order with
    their x offsets, a series of points per key, and an index of the keys that
    ever moved. Deriving any of that here would be a second answer to a
    question rl-mcp already answers, which rule zero forbids; it would also
    lose the `why` each point carries, which is the half a chart cannot
    show and a reader most wants.
    """
    from rlmcp.records import params as _params
    records = self.records()
    if task:
      records = [r for r in records if (r.task or UNKNOWN_TASK) == task]
    if not records:
      return {"runs": [], "index": [], "span": 0}
    return _params.build_history(_graph.build(records))

  def record_graph(self, task: Optional[str] = None) -> List[Dict[str, Any]]:
    """The record graph, with the recipe derived by rlmcp rather than by us.

    Split by task: runs from two problems share no ancestry, so drawing them in
    one tree would invent a relationship that does not exist.
    """
    records = self.records()
    if task:
      records = [r for r in records if (r.task or UNKNOWN_TASK) == task]
    if not records:
      return []
    graph = _graph.build(records)
    return _graph.to_payload(graph)


class Library:
  """Every records root the studio was pointed at, indexed by task."""

  def __init__(self, roots: List[Path]):
    self.sources = [RecordsSource(r) for r in roots]

  def _is_alive(self, session: Optional[str]) -> bool:
    """Whether a trainer is actually running, which the verdict does not say.

    A record's verdict is a *claim about the experiment*; liveness is a fact
    about a process. They disagree routinely -- a run closed as `interrupted`
    whose process is still finishing, or a `running` record whose machine
    rebooted -- and using the verdict to decide what to watch means watching
    the wrong things.
    """
    if not session:
      return False
    # A run on another machine: ask the host, through the same client the
    # panels read it with. A host that does not answer is a run that stopped.
    from rlmcp_studio.sources.session import is_address, open_client
    if is_address(session):
      try:
        return open_client(session).liveness() == "running"
      except Exception:  # noqa: BLE001
        return False
    try:
      from rlmcp.session import Session
      return Session.open(Path(session)).liveness_info().get("state") != "dead"
    except Exception:
      # No session there any more, or one rlmcp will not open. Either way the
      # record points at nothing to watch.
      return False

  def tasks(self) -> List[Dict[str, Any]]:
    """One entry per task, which is what the task switcher lists.

    ``RunRecord.task`` is the field the library itself nominates as the one a
    view splits on, so the studio splits on it rather than inventing a notion
    of "project".
    """
    seen: Dict[str, Dict[str, Any]] = {}
    for source in self.sources:
      for summary in source.summaries():
        entry = seen.setdefault(summary["task"], {
            "task": summary["task"],
            "root": str(source.root),
            "runs": 0,
            "live": 0,
            "latest": None,
        })
        entry["runs"] += 1
        if self._is_alive(summary.get("session")):
          entry["live"] += 1
        if entry["latest"] is None or summary["seq"] > entry["latest"]["seq"]:
          entry["latest"] = summary
    return sorted(seen.values(), key=lambda t: (-t["live"], -t["runs"], t["task"]))

  def summaries(self, task: Optional[str] = None,
                liveness: bool = False) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for source in self.sources:
      for summary in source.summaries():
        if task is None or summary["task"] == task:
          if liveness:
            # A *fact about a process*, carried beside the record's claim so
            # that nothing downstream has to read `verdict == "running"` as
            # "training now". Those two disagree for as long as a finished run
            # stays open, which is most of a run's life. Asking is cheap, well
            # under a millisecond per session, because `liveness_info` reads
            # a heartbeat rather than walking a log.
            summary["live"] = self._is_alive(summary.get("session"))
          out.append(summary)
    return sorted(out, key=lambda s: s["seq"])

  def live_signature(self) -> str:
    """What is training right now, as one string to compare against.

    Liveness is the one thing about a run that changes with *no file changing*:
    a trainer that dies stops writing, so a studio driven only by a file
    watcher can learn that a run is alive and never that it stopped. This is
    what the heartbeat loop diffs, and it is deliberately a whole-store answer
    -- a run dying is interesting on every screen, not only the one it is
    open on.
    """
    parts: List[str] = []
    for source in self.sources:
      for summary in source.summaries():
        if summary.get("session") and self._is_alive(summary["session"]):
          parts.append(f"{source.root}/{summary['id']}")
    return "|".join(sorted(parts))

  def record_graph(self, task: Optional[str] = None) -> List[Dict[str, Any]]:
    for source in self.sources:
      payload = source.record_graph(task)
      if payload:
        return payload
    return []

  def param_history(self, task: Optional[str] = None) -> Dict[str, Any]:
    for source in self.sources:
      if not source.available():
        continue
      payload = source.param_history(task)
      if payload.get("runs"):
        return payload
    return {"runs": [], "index": [], "span": 0}

  def find(self, record_id: str, task: Optional[str] = None):
    """The record with this id, and the store it came from.

    Ids are per-store: two roots both number their records from 001, so
    `find("002")` on a two-root studio is genuinely ambiguous. Returning
    whichever root came first would let a click on a run in the rail show a
    *different* run's claim, metrics and session under the right id -- wrong
    in the one way this app must never be wrong.

    The caller almost always knows the task, because the rail is filtered by
    one, so a task narrows it to the intended store. Without one, the first
    match stands.
    """
    matches = []
    for source in self.sources:
      if not source.available():
        continue
      record = source.get(record_id)
      if record is not None:
        matches.append((record, source))
    if not matches:
      return None, None
    if task:
      for record, source in matches:
        if (record.task or "") == task:
          return record, source
    return matches[0]

  def ambiguous_ids(self) -> Dict[str, int]:
    """Ids that exist in more than one root, for the studio to warn about."""
    seen: Dict[str, int] = {}
    for source in self.sources:
      if not source.available():
        continue
      for record in source.records():
        seen[record.id] = seen.get(record.id, 0) + 1
    return {rid: n for rid, n in seen.items() if n > 1}

  def palette(self) -> Dict[str, str]:
    return dict(VERDICT_COLORS)

  def unrecorded(self, logs_roots: List[Path]) -> List[Dict[str, Any]]:
    """Training sessions on disk that no record names.

    A run with no record is invisible in this app -- the rail is built from
    records, which is the right spine and the wrong silence. An agent can
    launch a short cartpole run from the panel that trains for three minutes
    and exits without a record, and nothing in the GUI would ever show it.

    So they are listed, separately and honestly, with the one thing that would
    fix them: a record.
    """
    from rlmcp_studio.sources.session import LiveSession, find_sessions

    known = {str(Path(p).resolve()) for p in self.session_dirs()}
    out: List[Dict[str, Any]] = []
    for root in logs_roots:
      for directory in find_sessions(Path(root)):
        if str(Path(directory).resolve()) in known:
          continue
        snapshot = LiveSession(directory).snapshot(metrics_tail=1)
        status = snapshot.get("status") or {}
        out.append({
            "session": str(directory),
            "name": Path(directory).parent.name,
            "task": snapshot.get("task") or "",
            "state": snapshot.get("state") or "unknown",
            "iteration": status.get("iteration"),
            "started_at": snapshot.get("started_at"),
        })
    out.sort(key=lambda s: -(s.get("started_at") or 0))
    return out

  def session_dirs(self) -> List[Path]:
    """Every session directory any record names, live or finished.

    Distinct from :meth:`live_session_dirs`, which answers "what is worth
    watching". This one answers "what may be read", and a finished run's
    events and metrics are still worth reading -- so the two must not be the
    same list, or looking at yesterday's run would be refused.
    """
    out: List[Path] = []
    for source in self.sources:
      for record in source.records():
        if not record.session:
          continue
        directory = Path(record.session)
        if directory.exists() and directory not in out:
          out.append(directory)
    return out

  def record_for_path(self, path: str | Path) -> str:
    """Which run a touched path belongs to, as a record id, or "".

    The watcher names what changed by directory: a session directory (the
    trainer's files) or a record's own directory (its meta.json). Both are
    facts a record already states -- ``record.session`` and where the store
    keeps it -- so the answer is a lookup, never a guess from a path's shape.
    Empty when nothing claims the path, which a studio-wide event is
    entitled to be.
    """
    try:
      touched = Path(path).resolve()
    except OSError:
      return ""
    for source in self.sources:
      root = Path(source.root).resolve() if getattr(source, "root", None) else None
      for record in source.records():
        if record.session:
          try:
            session = Path(record.session).resolve()
          except OSError:
            session = None
          if session and (touched == session or session in touched.parents):
            return record.id
        if root is not None and (root / record.id) in (touched, *touched.parents):
          return record.id
    return ""

  def live_session_addresses(self) -> List[str]:
    """Wire addresses of runs on other machines that may still be writing.

    The remote counterpart of :meth:`live_session_dirs`: nothing here can
    be watched as a file, so `sources/remote_ticks.py` polls these instead.
    Open verdicts only; a closed remote run is asked nothing.
    """
    from rlmcp.records import OPEN_VERDICTS
    from rlmcp_studio.sources.session import is_address
    out: List[str] = []
    for source in self.sources:
      for record in source.records():
        if record.session and is_address(record.session) and record.verdict in OPEN_VERDICTS:
          if record.session not in out:
            out.append(record.session)
    return out

  def live_session_dirs(self) -> List[Path]:
    """Session directories worth watching.

    A record's session lives wherever the trainer wrote its logs, which is not
    under the records root -- so watching the records alone means the status
    heartbeat never reaches the client. Only records that could still be
    writing are included; a closed run's directory is static.
    """
    from rlmcp.records import OPEN_VERDICTS
    dirs: List[Path] = []
    for source in self.sources:
      for record in source.records():
        if not record.session:
          continue
        directory = Path(record.session)
        if not directory.exists():
          continue
        # Watch anything still writing, whatever its record claims -- see
        # _is_alive. The open-verdict case is kept because a run that has just
        # been created has no heartbeat yet.
        if record.verdict in OPEN_VERDICTS or self._is_alive(record.session):
          dirs.append(directory)
    return dirs
