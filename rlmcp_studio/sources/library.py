"""A task as a thing with a history: its card, and the index of every card.

The app can already switch tasks and list their runs, which answers *which*
task you are on and says nothing about *what it is*. The missing unit is a
**card** — what the task is, which package registers it, what its runs
concluded, what wasted time, and what to read first. It lives locally: a
directory beside the records, no cloud, no sync, no second data model.

**This module assembles a card; it does not publish one.** A published card is
immutable, versioned (`@1`, `@2`) and *written by an agent* from the whole
graph — and that instruction ships with rl-mcp as a skill, not as prose typed
into this app, so every agent that writes one follows the same rules. Writing
one here would be the studio inventing rl-mcp's card generator, which is
precisely what rule zero forbids. So what a card shows today is only what the
records, the registry and the notes already say, and it says so on its face:
`published.exists` is false until somebody promotes one.

Three rules of a card are load-bearing even for an assembled one, because they
are what separates a library from a pile of opinions:

* **every finding and every pitfall cites a run.** Nothing enters those lists
  that cannot name the record it came from -- which is why they are built from
  records and their ledgers rather than from anything derived or averaged.
* **it is written for two readers**: a person skimming "has anyone trained this
  robot to do this", and an agent reading the pitfalls *before* its first run.
  Both want the same short lists, which is why there is no prose summary here
  that nobody wrote.
* **the dead ends count.** A card built from the runs that worked throws away
  most of the knowledge, so `falsified` and `interrupted` runs are read the
  same way as the ones that concluded -- they just land in different lists.

What this deliberately does *not* do, and cannot honestly fake: cluster three
sibling failures into one conclusion, name which intervention actually moved a
metric, or decide what could not be concluded. Those are judgements, they are
the whole point of the generation skill, and a heuristic that guessed at them
would be folklore with a machine's confidence behind it.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from rlmcp.records import OPEN_VERDICTS, RunRecord

from rlmcp_studio.sources.records import UNKNOWN_TASK

CONCLUDED = ("validated", "best", "provisional")
"""Verdicts that say the run answered its question."""

RULED_OUT = ("falsified",)
"""A killed hypothesis is a finding, and often the most useful one -- it is
what saves the next person the week. rl-mcp's own vocabulary says as much:
``falsified`` is a good outcome, not a failure."""

WASTED = ("interrupted",)
"""Time spent with no result. `superseded` and `control` are deliberately in
neither list: one is bookkeeping about a better run existing, the other is a
baseline doing its job, and neither is something learned or something lost."""

PITFALL_KINDS = ("correct", "reject", "constrain")
"""Feedback kinds that are a warning in someone's own words.

``correct`` says something already done is wrong, ``reject`` says stop, and
``constrain`` is a standing rule that outlives the run it was said on -- which
is the definition of a thing the next run should be told before it starts.
``steer``, ``approve`` and ``observe`` are left out: they are direction and
commentary about one run, not a lesson about the task.
"""

READ_FIRST = 6
"""How many documents the card offers. A reading list nobody finishes is a list
of files, and the Docs view already exists for the rest of them."""

MEDIA = 3
"""How many kept clips the card carries. A card needs the final clip; a couple
of neighbours cost nothing and cover the case where the newest run is
not the one worth watching."""


def by_task(sources: Iterable[Any]) -> Dict[str, List[RunRecord]]:
  """Every record the studio can see, grouped by the task it names.

  One pass over the roots, because the index draws a row per task and asking
  each task separately would re-read every store once per task.
  """
  grouped: Dict[str, List[RunRecord]] = {}
  for source in sources:
    if not source.available():
      continue
    for record in source.records():
      grouped.setdefault(record.task or UNKNOWN_TASK, []).append(record)
  for records in grouped.values():
    records.sort(key=lambda r: r.seq)
  return grouped


def collect(sources: Iterable[Any], task: str) -> List[RunRecord]:
  """This task's records, oldest first, across every root."""
  return by_task(sources).get(task, [])


def _cite(record: RunRecord, text: str, **extra: Any) -> Dict[str, Any]:
  """One entry, with the run it came from attached to it.

  The citation is not decoration. A claim with no run behind it cannot be
  checked, and an agent reading this before its first run has no way to tell a
  measured conclusion from a remark somebody typed at four in the morning.
  """
  return {
      "run": record.id,
      "slug": record.slug,
      "seq": record.seq,
      "date": record.date,
      "verdict": record.verdict,
      "text": text,
      **extra,
  }


def _findings(records: Sequence[RunRecord]) -> List[Dict[str, Any]]:
  """What the runs concluded, newest first.

  Two stances, kept apart on the entry rather than in two lists: a run that
  confirmed something and a run that ruled something out are both findings,
  and a reader who cannot tell which is which has learned nothing.
  """
  out: List[Dict[str, Any]] = []
  for record in reversed(records):
    if record.verdict in CONCLUDED:
      stance = "confirmed"
    elif record.verdict in RULED_OUT:
      stance = "ruled out"
    else:
      continue
    line = record.one_line()
    if not line:
      # No headline, no outcome, no hypothesis: the record says nothing this
      # list could quote, and quoting its slug back would be inventing one.
      continue
    out.append(_cite(record, line, stance=stance, change=list(record.change),
                     metrics=[list(m) for m in record.metrics]))
  return out


def _pitfalls(records: Sequence[RunRecord]) -> List[Dict[str, Any]]:
  """What wasted time, from the two places it is actually written down.

  A run that was interrupted is time spent for no result. A remark of the kinds
  in ``PITFALL_KINDS`` is somebody saying, in their own words, that a direction
  was wrong or must not be taken again -- and the ledger is the only place in
  this system where that sentence survives at all.
  """
  out: List[Dict[str, Any]] = []
  for record in reversed(records):
    if record.verdict in WASTED:
      line = record.one_line() or f"{record.slug} was interrupted"
      out.append(_cite(record, line, source="verdict"))
    for index, entry in enumerate(record.feedback):
      if entry.kind in PITFALL_KINDS and entry.text.strip():
        out.append(_cite(record, entry.text.strip(), source="feedback",
                         kind=entry.kind, index=index,
                         # Whether anything was done about it. A standing rule
                         # nobody answered is still worth reading; a reader
                         # deserves to know which it is.
                         answered=bool(entry.response.strip())))
  return out


def _open_questions(records: Sequence[RunRecord]) -> List[Dict[str, Any]]:
  """What this task has not settled.

  A card flags what it could not conclude, because an open question is a
  result and hiding it is how a library rots. The two honest sources are
  runs that have not finished and instructions nobody answered -- both facts on
  the record, neither of them a judgement made here.
  """
  out: List[Dict[str, Any]] = []
  for record in reversed(records):
    if record.verdict in OPEN_VERDICTS:
      line = record.one_line() or f"{record.slug} is still {record.verdict}"
      out.append(_cite(record, line, source="verdict"))
    for index, entry in enumerate(record.feedback):
      if entry.outstanding and entry.text.strip():
        out.append(_cite(record, entry.text.strip(), source="feedback",
                         kind=entry.kind, index=index))
  return out


def _media(records: Sequence[RunRecord]) -> List[Dict[str, Any]]:
  """The clips the records kept, newest run first.

  Only what was promoted onto a record: a clip still sitting in ``logs/`` goes
  when the logs are cleaned, and a card that pointed at one would rot quietly.
  """
  out: List[Dict[str, Any]] = []
  for record in reversed(records):
    # Newest promotion first, within the run as well as across runs. Assets are
    # appended, so the *first* entry on a run that filmed its own progress is
    # `progress_env0_it000000.mp4` -- the untrained robot falling over, which is
    # the single worst clip to put at the top of a card about the task.
    for key, caption in reversed((record.assets or {}).get("videos", [])):
      out.append({"run": record.id, "key": key, "caption": caption})
      if len(out) >= MEDIA:
        return out
  return out


def _read_first(docs: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
  """What to read before doing anything, out of the documents that exist.

  The task's own notes come before the run write-ups, which is the one ordering
  judgement here and an easy one: a document about the task describes the
  problem, and a document about one run assumes you already know it.
  """
  if not docs:
    return []
  groups = sorted(docs.get("groups", []), key=lambda g: 0 if g["id"] == "task" else 1)
  out: List[Dict[str, Any]] = []
  for group in groups:
    for doc in group.get("docs", []):
      out.append({**doc, "group": group.get("label", "")})
      if len(out) >= READ_FIRST:
        return out
  return out


def _history(records: Sequence[RunRecord], row: Dict[str, Any]) -> Dict[str, Any]:
  """How many runs, how they ended, and when the task was last touched."""
  verdicts: Dict[str, int] = {}
  for record in records:
    verdicts[record.verdict] = verdicts.get(record.verdict, 0) + 1
  return {
      "runs": len(records),
      # Liveness is a fact about a process and the verdict is a claim about an
      # experiment; they disagree routinely. The count of live trainers comes
      # from the task list, which asked the sessions.
      "live": int(row.get("live") or 0),
      "verdicts": verdicts,
      "open": sum(n for v, n in verdicts.items() if v in OPEN_VERDICTS),
      "first": records[0].date if records else "",
      "last": records[-1].date if records else "",
      # The newest run, in the four fields a card needs to name it. Not the
      # full summary the rail is built from: that carries every asset and every
      # metric of one run, which is a payload about a *run* inside a card about
      # a *task*.
      "latest": ({"id": records[-1].id, "slug": records[-1].slug,
                  "date": records[-1].date, "verdict": records[-1].verdict,
                  "line": records[-1].one_line()} if records else None),
  }


def card(task: str, records: Sequence[RunRecord],
         row: Optional[Dict[str, Any]] = None,
         docs: Optional[Dict[str, Any]] = None,
         blurb: str = "",
         library_roots: Sequence[str] = ()) -> Dict[str, Any]:
  """One task's card, assembled from what is already known about it.

  ``row`` is the task list's own entry -- registered, recorded, which package
  registers it -- so the card and the switcher cannot disagree about what a
  task is. ``docs`` is ``sources.docs.for_run``'s answer, because the reading
  list is the documents that exist rather than a second search for them.
  """
  row = dict(row or {})
  return {
      "task": task,
      # What it is, when anybody said: the sentence given when the task was
      # begun here. Nothing invents one -- a card that guesses at the purpose
      # of a task from its id is putting words in the mouth of whoever wrote it.
      "blurb": blurb,
      "registered": bool(row.get("registered")),
      "recorded": bool(row.get("recorded")),
      "starting": bool(row.get("starting")),
      "backend": row.get("backend") or "",
      "package": row.get("package") or "",
      "experiment": row.get("experiment") or "",
      "root": row.get("root") or "",
      "history": _history(records, row),
      "findings": _findings(records),
      "pitfalls": _pitfalls(records),
      "open_questions": _open_questions(records),
      "read_first": _read_first(docs),
      "media": _media(records),
      # The published card: immutable, versioned, agent-written. Reported as
      # absent rather than left out, so the card does not read as though this
      # assembled view were the published one.
      "published": {
          "exists": False,
          # Where a published card would live: `$RLMCP_LIBRARY`, which the
          # config already resolves. Reported rather than scanned -- nothing
          # writes that directory yet, and a reader for a format nothing
          # produces is a guess with a filesystem behind it.
          "roots": [str(r) for r in library_roots],
          "reason": "nothing has been published for this task. A card is "
                    "written from the whole graph by rl-mcp's card skill and "
                    "promoted by a person; the studio shows what the "
                    "records and notes already say.",
      },
  }


def _day(at: float) -> str:
  """An epoch as the date the records themselves are written in."""
  if not at:
    return ""
  return time.strftime("%Y-%m-%d", time.localtime(at))


def index(rows: Sequence[Dict[str, Any]],
          grouped: Dict[str, List[RunRecord]],
          code_at: Optional[Callable[[str], float]] = None,
          ) -> List[Dict[str, Any]]:
  """One row per task, for the library browser, most recently worked first.

  Built from the same merged list the switcher uses -- registry ∪ records
  -- so a task that has never trained has a row here too. That is the
  case the library exists for as much as any: the first thing a new task should
  do is read the last one's pitfalls.

  **Ordered by when a run of it last changed.** The shelf is how you get back
  to what you were doing, and the registry's own order is alphabetical, which
  answers no question anybody has: the task with a trainer on it right now sat
  wherever its name put it. ``updated_at`` rather than ``date``, because a
  record's date is a *day* -- four runs of four tasks on one afternoon all
  compare equal, which is exactly the afternoon you want ordered.

  A task with no runs has no record to date it, and it is the one you are most
  likely to be in the middle of -- so ``code_at`` dates it by its own files
  instead, and the task somebody is writing this afternoon sits where the work
  put it rather than at the bottom of the shelf. Only as a *fallback*: a
  package like `my_tasks.tasks` can register twenty tasks out of one
  directory, so folding its mtime into tasks that do have runs would lift all
  twenty every time anybody touched any of them, which is the alphabet again in
  a worse disguise.

  Tasks that neither ran nor have files here keep the order they arrived in:
  there is nothing to sort them by, and inventing a key would shuffle them on
  every refresh.
  """
  out: List[Dict[str, Any]] = []
  for row in rows:
    task = row.get("task") or ""
    records = grouped.get(task, [])
    latest = records[-1] if records else None
    updated = max((r.updated_at for r in records), default=0.0)
    # When its own files were last edited, for a task with nothing else to date
    # it. Asked for only in that case: the walk is cheap but not free, and the
    # answer is only ever used here.
    edited = 0.0 if updated or code_at is None else float(code_at(task) or 0.0)
    out.append({
        "task": task,
        "registered": bool(row.get("registered")),
        "recorded": bool(row.get("recorded")),
        "starting": bool(row.get("starting")),
        "package": row.get("package") or "",
        "backend": row.get("backend") or "",
        "root": row.get("root") or "",
        "runs": len(records),
        "live": int(row.get("live") or 0),
        "last": latest.date if latest else "",
        # When any run of it last changed, as an epoch. The maximum across the
        # task rather than the newest record's own stamp: reopening an old run
        # to answer a remark on it is work on this task, and the shelf is about
        # the task. Falling back to the package's own files, which is all a
        # task being written has.
        "updated": updated or edited,
        # The day those files changed, said on the row -- so a task sitting
        # high on a shelf headed "most recently worked on" with no runs to
        # explain it says what put it there.
        "edited": _day(edited),
        # The counts, not the entries: a browser row is a reason to open the
        # card, and forty findings inlined into a list of thirty tasks is a
        # payload nobody reads and a page nobody can scan.
        "findings": len(_findings(records)),
        "pitfalls": len(_pitfalls(records)),
        "open": len(_open_questions(records)),
        "line": latest.one_line() if latest else "",
    })
  # Stable, so the never-trained half stays in the order the switcher gave it.
  out.sort(key=lambda r: r["updated"], reverse=True)
  return out


__all__ = ["by_task", "card", "collect", "index"]
