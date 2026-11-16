"""The recipe: one run, folded along the ancestry that produced it.

The Diff view answers *this run against that one*; this answers
*what is this run, counting everything it inherited* -- which is a different
question and the one a reader asks when they open a record they did not launch.

Three things make it a fold rather than a listing:

* **the walk is rl-mcp's.** ``graph.recipe`` is ``fold_recipe`` on the config
  edges, and it is the one place the ancestry is defined. Walking ``parent``
  here would be a second answer to a question the library already answers, and
  the two would drift the first time a record gained an edge kind.
* **each rung carries all three layers.** Code, config and interventions
  are computed *per step*, so "what did this run add" is answered on that
  run's row rather than by subtracting two totals in your head.
* **the interventions are distilled, not transcribed.** A run that moved one
  knob three times moved it once, from its first value to its last, and that
  is what the line says. A wall of current values is what the record already
  is; the recipe is the diff of it against where the campaign started.

What is deliberately *not* here, because the records cannot support it yet, is
a cheapest-path search to a run's policy. A node in that search is
``(run, iteration)`` -- a policy state you could load -- and a record names one
checkpoint, not one per iteration, so Dijkstra over it would have exactly one path per target and would
be arithmetic dressed as a search. The half that *is* supported is
:func:`_shortcuts`: a from-scratch run whose config already matches a rung is
evidence that the rung is reachable without the warm start, and those runs are
sitting in the records already.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from rlmcp.records import RunRecord
from rlmcp.records import graph as _graph

from rlmcp_studio.sources import code as _code
from rlmcp_studio.sources import session as _session

MAX_TERMS = 4
"""Deltas that reach the one-line summary of a step.

The line exists to be read at a glance -- "012 = reward_scale 3 → 8 + a change
to actions.py". Past four terms it is a list again, and the overflow is counted
rather than printed.
"""

MAX_ROWS = 24
"""Config rows carried per step. A step that re-prices forty reward terms is a
real thing; sending all of them for dozens of ancestors is a payload nobody
reads."""

MAX_FILES = 40


def fold(records: Sequence[RunRecord], record_id: str) -> Dict[str, Any]:
  """The recipe at ``record_id``: every rung from the root, with its three layers.

  ``records`` is the whole store, never the task's slice. Ancestry crosses
  tasks routinely -- a run of one task often takes its config from a run of a
  sibling task -- so filtering by task first would silently cut the chain at
  the last rename and call the result a root.
  """
  graph = _graph.build(records)
  if record_id not in graph.nodes:
    return {"ok": False, "record": record_id,
            "reason": f"no record {record_id} in this store"}
  try:
    chain = graph.recipe(record_id)
  except ValueError as exc:      # a cycle in the parent chain; rl-mcp names it
    return {"ok": False, "record": record_id, "reason": str(exc)}

  by_id = {rid: node.record for rid, node in graph.nodes.items()}
  node = by_id[record_id]
  root = by_id[chain[0][0]]

  steps: List[Dict[str, Any]] = []
  parent: Optional[RunRecord] = None
  for rid, change in chain:
    record = by_id[rid]
    steps.append(_step(record, parent, list(change)))
    parent = record

  net = _net_config(steps)
  for step in steps:                 # the wire copy is trimmed; the fold is not
    _trim(step["config"])

  phases = _phases(by_id, node)
  runtime = steps[-1]["runtime"]
  return {
      "ok": True,
      "record": node.id,
      "slug": node.slug,
      "task": node.task or "",
      "verdict": node.verdict,
      "root": root.id,
      "depth": len(steps),
      "line": steps[-1]["line"],
      "steps": steps,
      # The accumulated halves, root to node, which is what "recipe" means
      # here: not what this run changed, but what it *is* by the time it ran.
      "config": net,
      "code": _tree_delta(
          node, _earliest_stamped(chain, by_id, node),
          "no ancestor of this run carries a code snapshot from the same "
          "repository, so there is nothing to fold the code against"),
      "phases": phases,
      "shortcuts": _shortcuts(records, phases, by_id),
      "curriculum": _curriculum(runtime),
  }


# ── one rung ─────────────────────────────────────────────────────────────

def _step(record: RunRecord, parent: Optional[RunRecord],
          said: List[str]) -> Dict[str, Any]:
  """A record against the one it took its config from, in all three layers."""
  config = _config_delta(record, parent)
  code = _tree_delta(record, parent)
  runtime = _runtime(record)
  terms = _terms(config, code, runtime, said)
  return {
      "id": record.id,
      "slug": record.slug,
      "seq": record.seq,
      "verdict": record.verdict,
      "task": record.task or "",
      "root": parent is None,
      "parent": parent.id if parent is not None else None,
      # The record's own words for the change, kept beside the derived line
      # rather than folded into it: one is a claim and the other is a
      # measurement, and a reader is owed the difference.
      "said": said,
      "config": config,
      "code": code,
      "runtime": runtime,
      "terms": terms,
      "line": _line(terms, said, record),
  }


def _config_delta(record: RunRecord, parent: Optional[RunRecord]) -> Dict[str, Any]:
  """Config fields this record moved, exact, because both sides are snapshots.

  ``code.config_diff`` is the same comparison the Diff view runs, reused rather
  than repeated so a recipe row and a diff row cannot disagree about what a
  run changed. Its ``keys`` list is dropped here: it exists so the Diff view
  can tell "identical" from "not recorded", and one copy of every key name per
  ancestor is most of this payload for no reader.
  """
  if parent is None:
    return {"changed": [], "added": [], "removed": [], "total": 0, "same": 0,
            "more": 0, "root": True}
  full = _code.config_diff(record.config or {}, parent.config or {})
  full.pop("keys", None)
  full["more"] = 0
  full["root"] = False
  return full


def _trim(delta: Dict[str, Any]) -> None:
  """Cap a step's config rows for the wire, counting what was left behind.

  Done after the fold rather than inside it: the net below is built from these
  lists, and a net assembled from truncated steps would quietly lose whichever
  key came twenty-fifth alphabetically.
  """
  more = 0
  for field in ("changed", "added", "removed"):
    rows = delta[field]
    if len(rows) > MAX_ROWS:
      more += len(rows) - MAX_ROWS
      delta[field] = rows[:MAX_ROWS]
  delta["more"] = more


def _net_config(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
  """The fold itself: every key the chain moved, from its first value to its last.

  Not ``config_diff(node, root)``, which is exactly the wrong answer. A root
  often recorded no config at all, so that comparison returns the node's
  entire config as "added" and calls it a recipe --
  a wall of current values, which is exactly what a reader cannot use. What
  they can use is the short list of knobs somebody actually turned on the way
  down, each with where it started, where it ended, and who moved it last.

  Runtime edits fold in beside the config fields, in the order they happened,
  because a weight raised mid-run and a weight raised at launch are the same
  fact about the recipe -- the per-step view keeps the layers apart to *read*,
  not to pretend a knob has two independent histories.
  """
  net: Dict[str, Dict[str, Any]] = {}

  def touch(key: str, old: Any, new: Any, rid: str, layer: str) -> None:
    row = net.get(key)
    if row is None:
      # `first` is the earliest value the chain ever recorded for this key,
      # which for a key that *appeared* is the value it appeared with. Keeping
      # it separate from `old` is what lets a key that was introduced and then
      # tuned read as "0 → 8" rather than as "it appeared, once".
      row = net[key] = {"key": key, "short": _short(key), "old": old,
                        "first": new if old is None else old,
                        "appeared": old is None, "first_by": rid, "moves": 0}
    row.update({"new": new, "by": rid, "layer": layer})
    row["moves"] += 1

  for step in steps:
    rid, config = step["id"], step["config"]
    for row in config["changed"]:
      touch(row["key"], row["old"], row["new"], rid, "config")
    for row in config["added"]:
      touch(row["key"], None, row["new"], rid, "config")
    for row in config["removed"]:
      touch(row["key"], row["old"], None, rid, "config")
    for edit in step["runtime"]["edits"]:
      touch(edit["key"], edit["old"], edit["new"], rid, "runtime")

  rank = {step["id"]: i for i, step in enumerate(steps)}
  moved: List[Dict[str, Any]] = []
  introduced: List[Dict[str, Any]] = []
  for row in net.values():
    if row["new"] is None:
      # What it was worth before it vanished, not the `None` the last delta
      # reported as its "old" -- a row reading "— → —" says nothing was lost.
      row["old"] = row["first"]
      row["kind"] = "gone"
      moved.append(row)
      continue
    # A key that appeared once and was never touched again did not *change*:
    # it appeared, because the run that introduced it was the first in the
    # chain to record a config at all. Counting those as changes is how a fold
    # of dozens of appearances gets presented as dozens of decisions.
    if row["appeared"] and row["moves"] == 1:
      row["kind"] = "introduced"
      introduced.append(row)
      continue
    if row["first"] == row["new"]:
      continue                       # turned and turned back: it never moved
    row["old"] = row["first"]
    row["kind"] = "changed"
    moved.append(row)

  moved.sort(key=lambda r: (rank.get(r["by"], 0), r["key"]))
  introduced.sort(key=lambda r: (rank.get(r["by"], 0), r["key"]))
  return {"moved": moved,
          "introduced": introduced[:MAX_ROWS],
          "introduced_total": len(introduced),
          "keys": len(net), "steps": len(steps)}


def _earliest_stamped(chain: List[Any], by_id: Dict[str, RunRecord],
                      node: RunRecord) -> Optional[RunRecord]:
  """The oldest ancestor whose code was stamped in the same repository.

  The root of a long chain often has no snapshot -- older rl-mcp releases did
  not stamp packages -- and comparing against it would make the code half of
  every recipe read "no code snapshot for <root>". The honest base is the oldest
  run that *can* be compared, and the payload names it so nobody mistakes it
  for the root.
  """
  repo = (node.code or {}).get("repo")
  for rid, _ in chain:
    record = by_id[rid]
    code = record.code or {}
    if record.id != node.id and code.get("kind") == "git" and code.get("repo") == repo:
      return record
  return None


def _tree_delta(record: RunRecord, parent: Optional[RunRecord],
                nothing_before: str = "the root of the chain: nothing before it"
                ) -> Dict[str, Any]:
  """Line counts between two runs' code snapshots -- names and totals, no patch.

  The patch itself belongs to the Diff view, which already renders one and can
  afford to: a recipe asks *which files* moved on each rung, and dozens of
  unified diffs in one response is a download rather than a summary.

  All the git work is ``rlmcp.records.snapshot``'s: the snapshot is a git tree,
  addressed by content, so this answers whether or not either run was
  committed and keeps answering after the commit is rebased away.
  """
  if parent is None:
    return {"ok": False, "reason": nothing_before}

  from rlmcp.records import snapshot as rlmcp_snapshot

  mine, theirs = record.code or {}, parent.code or {}
  if mine.get("kind") != "git" or theirs.get("kind") != "git":
    missing = [r.id for r, c in ((record, mine), (parent, theirs))
               if c.get("kind") != "git"]
    return {"ok": False, "missing": missing,
            "reason": "no code snapshot for " + ", ".join(missing)
                      + " — runs launched before the package was stamped"}
  if mine.get("repo") != theirs.get("repo"):
    return {"ok": False,
            "reason": "the two runs were stamped in different repositories"}
  try:
    counts = rlmcp_snapshot.diff(mine["repo"], theirs["tree"], mine["tree"])
  except Exception as exc:  # noqa: BLE001 -- a lost object must not 500 a view
    return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

  # Biggest first, and it is not cosmetic: the list is truncated, and git's own
  # order is alphabetical, so a snapshot that also swept up the records
  # directory would leave `meta.json` naming the change and the file that
  # actually moved cut off the end.
  files = sorted(counts["files"], key=lambda f: -(f["added"] + f["removed"]))
  return {"ok": True, "from": parent.id, "to": record.id,
          "files": files[:MAX_FILES],
          "changed": counts["changed"], "added": counts["added"],
          "removed": counts["removed"], "head": mine.get("head") or {},
          "clean": mine.get("clean", True)}


def _runtime(record: RunRecord) -> Dict[str, Any]:
  """What was done to this run while it trained, read and then distilled.

  Two readers, because they answer different halves. ``interventions`` is
  rl-mcp's own vocabulary of what counts as a decision -- the studio must not
  keep a second list of interesting event kinds -- and gives the readable
  timeline. ``parameter_traces`` gives the *structure* the ladder needs, since
  an intervention's phrase has already collapsed key, old and new into a
  sentence.
  """
  if not record.session:
    return {"ok": False, "items": [], "edits": [], "stages": [],
            "reason": "this run kept no session"}
  directory = Path(record.session)
  if not directory.exists():
    return {"ok": False, "items": [], "edits": [], "stages": [],
            "reason": "its session directory is gone"}
  read = _session.interventions(directory)
  traces = _session.parameter_traces(directory)
  return {"ok": bool(read.get("ok")),
          "items": read.get("items", []),
          "edits": _net_edits(traces),
          "stages": traces.get("stages", []),
          "last_iteration": traces.get("last_iteration", 0),
          "reason": read.get("reason", "")}


def _net_edits(traces: Dict[str, Any]) -> List[Dict[str, Any]]:
  """Each knob's whole history as one move, first value to last.

  A parameter nudged three times was moved once. Transcribing all three is a
  wall of current values that no reader can use, and it hides the one fact
  that matters -- where the knob ended up relative to where the run launched.
  A knob put back exactly where it started moved nowhere and is dropped.
  """
  out: List[Dict[str, Any]] = []
  for trace in traces.get("traces", []):
    steps = [s for s in trace.get("steps", []) if s.get("applied", True)]
    if not steps:
      continue
    first, last = steps[0], steps[-1]
    if first.get("old") == last.get("new"):
      continue
    why = next((s.get("why") for s in steps if s.get("why")), "")
    out.append({"key": trace["key"], "short": _short(trace["key"]),
                "old": first.get("old"), "new": last.get("new"),
                "at": first.get("iteration", 0), "moves": len(steps),
                "why": why})
  out.sort(key=lambda e: (e["at"], e["key"]))
  return out


# ── the readable line ────────────────────────────────────────────────────

def _terms(config: Dict[str, Any], code: Dict[str, Any],
           runtime: Dict[str, Any], said: List[str]) -> List[Dict[str, Any]]:
  """The rung, as the handful of phrases somebody would say out loud.

  "012 = reward_scale 0 → 8" rather than every value in the config, which is
  the whole point of a recipe. Ordered config, then runtime, then code,
  because that is the order of increasing effort to read: a number, a number
  with an iteration on it, and a file you have to open.
  """
  terms: List[Dict[str, Any]] = []
  overflow = 0

  for row in config["changed"]:
    if len(terms) < MAX_TERMS:
      terms.append({"layer": "config", "why": "",
                    "text": f"{_short(row['key'])} {_fmt(row['old'])} → {_fmt(row['new'])}"})
    else:
      overflow += 1
  for row in config["added"]:
    if len(terms) < MAX_TERMS:
      terms.append({"layer": "config", "why": "",
                    "text": f"{_short(row['key'])} = {_fmt(row['new'])}, new"})
    else:
      overflow += 1
  for row in config["removed"]:
    if len(terms) < MAX_TERMS:
      terms.append({"layer": "config", "why": "",
                    "text": f"{_short(row['key'])} gone (was {_fmt(row['old'])})"})
    else:
      overflow += 1

  for edit in runtime.get("edits", []):
    if len(terms) < MAX_TERMS:
      terms.append({"layer": "runtime", "why": edit["why"],
                    "text": f"{edit['short']} {_fmt(edit['old'])} → {_fmt(edit['new'])}"
                            f" at {edit['at']}"})
    else:
      overflow += 1

  if code.get("ok") and code.get("changed"):
    names = [f["path"].rsplit("/", 1)[-1] for f in code["files"][:2]]
    rest = code["changed"] - len(names)
    label = ", ".join(names) + (f" and {rest} more" if rest > 0 else "")
    terms.append({
        "layer": "code", "why": _clip(said[0]) if said else "",
        "text": f"{label} +{code['added']}/−{code['removed']}"})

  if overflow:
    terms.append({"layer": "more", "why": "",
                  "text": f"and {overflow} more field{'s' if overflow > 1 else ''}"})
  return terms


def _line(terms: List[Dict[str, Any]], said: List[str],
          record: RunRecord) -> str:
  """One sentence for the rung: the derived terms, or the record's own words."""
  derived = [t["text"] for t in terms if t["layer"] != "more"]
  if derived:
    return " + ".join(derived)
  if said:
    return _clip(said[0])
  if record.parent:
    return "nothing recorded changed"
  return "the root of this chain"


def _short(key: str) -> str:
  """``reward.action_rate_l2.weight`` → ``action_rate_l2.weight``.

  The tail, two segments of it, which is the rule the Parameters view already
  uses for the same reason -- reward configs nest four deep, and the leaf alone
  is ``weight`` for every term in the file. Borrowed rather than improved on:
  a key abbreviated one way in the recipe and another in the table beside it is
  two names for one knob.
  """
  parts = str(key).split(".")
  return key if len(parts) <= 2 else ".".join(parts[-2:])


def _fmt(value: Any) -> str:
  if value is None:
    return "—"
  if isinstance(value, bool):
    return "true" if value else "false"
  if isinstance(value, float):
    if value == int(value) and abs(value) < 1e6:
      return str(int(value))
    # -2.5e-7 rendered to three decimals is "-0.000", which is every digit
    # gone; the client's `fmt` draws the same line and this matches it.
    if value and abs(value) < 1e-3:
      return f"{value:.2e}"
    return f"{value:.4g}"
  if isinstance(value, (list, tuple)):
    return "[" + ", ".join(_fmt(v) for v in value) + "]"
  return _clip(str(value), 40)


def _clip(text: str, limit: int = 110) -> str:
  text = " ".join(str(text).split())
  return text if len(text) <= limit else text[:limit - 1] + "…"


# ── between phases: the warm-start chain ─────────────────────────────────

def _phases(by_id: Dict[str, RunRecord], record: RunRecord) -> List[Dict[str, Any]]:
  """The training segments, oldest first: follow the weights edge to scratch.

  A different edge from the ancestry above, and deliberately so -- the recipe
  has two levels, between phases and within one. ``parent`` says where the
  config came from; ``weights.run`` says which policy was loaded, and a run
  routinely takes its config from its predecessor and its weights from six
  runs back. Reproducing the node means
  running these segments in this order, which is what makes them phases.
  """
  phases: List[Dict[str, Any]] = []
  seen: set = set()
  current: Optional[RunRecord] = record
  while current is not None and current.id not in seen:
    seen.add(current.id)
    weights = getattr(current, "weights", None)
    source = getattr(weights, "run", None) if weights else None
    phases.append({
        "run": current.id,
        "slug": current.slug,
        "verdict": current.verdict,
        "from": source or "",
        "scratch": not source,
        "checkpoint": (getattr(weights, "checkpoint", "") if weights else "") or "",
        # Missing, not zero: a run whose session is gone trained an unknown
        # number of iterations, and calling that 0 would make a phase look free.
        "iterations": _iterations(current),
        # The warm-start source may be outside this store, in which case the
        # chain stops here and has to say why rather than looking finished.
        "dangling": bool(source and source not in by_id),
    })
    current = by_id.get(source) if source else None
  phases.reverse()
  return phases


def _iterations(record: RunRecord) -> Optional[int]:
  """How long this segment trained, from the session rlmcp wrote."""
  if not record.session or not Path(record.session).exists():
    return None
  try:
    status = _session.LiveSession(Path(record.session)).snapshot(metrics_tail=1)
    value = (status.get("status") or {}).get("iteration")
    return int(value) if isinstance(value, (int, float)) else None
  except Exception:  # noqa: BLE001 -- a phase with no cost is still a phase
    return None


def _shortcuts(records: Sequence[RunRecord], phases: List[Dict[str, Any]],
               by_id: Dict[str, RunRecord]) -> List[Dict[str, Any]]:
  """Runs that reached a rung's config from random init, so the chain could start there.

  The first payoff of a recipe, in the form the records can actually support:
  the cheapest path is often not the historical one, and the evidence is already
  written down as scratch-validation runs -- a run launched from nothing on a
  config a warm-started run also used. If one exists and it was not falsified,
  every phase before it is skippable.

  Reported as an estimate and nothing stronger, because a segment's cost is the
  iterations it took *that time* from *that* checkpoint. Replayed from another
  it is a guess, and it is shown as one.
  """
  scratch = [r for r in records
             if not (getattr(r, "weights", None) and r.weights.run)
             and r.verdict != "falsified" and r.config]
  out: List[Dict[str, Any]] = []
  for index in range(1, len(phases)):
    # The state this segment had to start from is the segment before it. If
    # something reached that state from nothing, everything up to and including
    # it is skippable.
    target = phases[index - 1]
    if index == 1 and target["scratch"]:
      continue                       # swapping one from-scratch run for another
    reached = by_id.get(target["run"])
    if reached is None or not reached.config:
      continue
    for candidate in scratch:
      if candidate.id == reached.id:
        continue
      delta = _code.config_diff(candidate.config, reached.config)
      if delta["changed"] or delta["added"] or delta["removed"]:
        continue
      out.append({
          "instead_of": target["run"],
          "run": candidate.id,
          "slug": candidate.slug,
          "verdict": candidate.verdict,
          "skips": [p["run"] for p in phases[:index]],
          "iterations": _iterations(candidate),
      })
      break                          # one witness is the claim; a list is noise
  return out


# ── within a phase: the ladder ───────────────────────────────────────────

def _curriculum(runtime: Dict[str, Any]) -> Dict[str, Any]:
  """The interventions of this run, as the ladder that would have produced them.

  The curriculum ladder is the target abstraction, built out of rl-mcp's own
  ``CurriculumStage`` and ``StageSchedule`` and serialised by them, so what
  comes out of here is loadable by ``StageSchedule.from_dict`` unchanged
  rather than being a document *about* the run.

  One honest limit, and it decides the shape: **nothing records a metric
  trigger.** A ``set_parameter`` event carries key, old, new and the reason
  typed at the time, and no condition -- so the natural mapping, "an
  intervention with a metric trigger becomes a promotion condition", has
  nothing to read. Every rung
  here is therefore a fixed value with its reason attached, ordered by the
  iteration it happened at, and the schedule is built with ``auto_promote``
  off: a ladder with no conditions that claimed it would climb itself would be
  a promise this data cannot keep. Supplying the conditions is the human's job,
  and the note says so.
  """
  from rlmcp.core.curriculum import CurriculumStage, StageSchedule

  edits = runtime.get("edits", [])
  named = {int(s.get("iteration") or 0): s.get("name") or ""
           for s in runtime.get("stages", [])}
  if not edits and not named:
    why = runtime.get("reason") or "nothing was changed while this run trained"
    return {"ok": False, "stages": [],
            "reason": f"{why}, so this run's recipe is its launch config"}

  # One rung per iteration at which something was set: two knobs turned in the
  # same breath are one decision, and splitting them invents an order nobody
  # chose.
  launch = CurriculumStage(
      name="launch", min_iterations=0,
      parameters={e["key"]: e["old"] for e in edits},
      notes="the values this run started from, for the knobs that later moved")
  stages = [launch]
  previous = 0
  # Both kinds of moment are a rung: a knob turned, and a stage entered.
  # Entering a stage changes parameters too, so a curriculum that only listened
  # to `set_parameter` would leave the run's own ladder out of its recipe.
  for iteration in sorted({e["at"] for e in edits} | set(named)):
    here = [e for e in edits if e["at"] == iteration]
    reasons = [e["why"] for e in here if e["why"]]
    stages.append(CurriculumStage(
        name=named.get(iteration) or f"at {iteration}",
        parameters={e["key"]: e["new"] for e in here},
        min_iterations=max(0, iteration - previous),
        notes="; ".join(reasons)))
    previous = iteration

  schedule = StageSchedule(stages, auto_promote=False)
  return {
      "ok": True,
      "schedule": schedule.to_dict(),
      "stages": [{"name": s.name, "min_iterations": s.min_iterations,
                  "notes": s.notes,
                  "parameters": [{"key": k, "short": _short(k), "value": _fmt(v)}
                                 for k, v in s.parameters.items()]}
                 for s in stages],
      "note": "no intervention recorded a metric trigger, so these rungs are "
              "ordered by the iteration each happened at and the schedule does "
              "not promote itself. Add promote_when conditions to make it climb.",
  }
