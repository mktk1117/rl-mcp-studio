"""What a task *is*, as one document and the facts underneath it.

Every other view in this app is about what happened: a run's curves, a record's
verdict, a card built from the graph. None of them answers the question asked
first and most often -- **what is this task**. What does the policy see, what do
its numbers mean, what is it paid for, what ends the episode, on which robot,
in what scene. Today that answer is spread across a package's `mdp/`, a
`config/<robot>/` registration and whatever `rlmcp check` last measured, and
reading it means opening six files.

So the answer is a document, `TASK.md`, beside the package that registers the
task, and this module is what finds it, says how old it is against the code it
describes, and states the facts the studio already holds beside it.

**The studio does not write it; an agent does.** The same split the task
library makes (``sources/library.py``): assembling what the records say is
mechanical and belongs here, and *reading a package and saying what the task is* is a
judgement that belongs to an agent with the code in front of it. What this
module contributes to that is :func:`ask` -- the sections, the file, and the
facts the studio can hand over so the agent does not go and re-measure them.

**Definition, never results.** The one rule that keeps this from becoming a
second report about a run: nothing here is about how training went. A learning
curve in this document is a bug in the instruction that produced it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from rlmcp_studio.sources import docs as source_docs

REPORT_NAME = "TASK.md"
"""The document's name, beside the package. Capitals because it is read the way
`README.md` is read -- the file you open first -- and beside the code because
that is the thing it describes and the thing that makes it go stale.

**One package can register several tasks** -- a flat variant and a rough one,
written in one directory because they share every line of code that is not the
terrain. They are different tasks and have different definitions, so one
`TASK.md` between them is not a shared document, it is one task's document
shown under the other's name. :func:`report_path` gives the second and later
ones a name of their own, :data:`OWN_REPORT`.
"""

OWN_REPORT = "TASK.{task}.md"
"""A task's own document, when `TASK.md` beside it belongs to a sibling.

Named from the task id rather than numbered, because the whole problem is
telling two tasks apart in one directory and `TASK.2.md` does not.
"""

THREAD = "report"
"""The conversation the report is written in.

Its own thread, deliberately. Writing a definition means reading twenty files
and taking pictures, which is minutes of tool calls -- and the conversation you
already have open is usually watching a run at the same time. A separate thread
is a separate provider session with its own context, so the report can be
written *while* the agent you were talking to is working, and neither waits for
the other.
"""

MEDIA_DIR = "media"
"""Where the report's pictures and clips go: one directory beside it, named in
the ask, so a document and its media move together.

A task with a document of its own gets a media directory of its own under it,
for the reason the document does: two tasks in one package both want a picture
called `scene-at-spawn.png`, and in one directory the second overwrites the
first.
"""

MEDIA_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm")

#: The sections a task report has, in the order they are read.
#:
#: Held here rather than in the instruction harness, and the harness says so:
#: the panel names which sections a document is missing, so the list has to be
#: something code can check against. Two copies of it would be a checklist that
#: drifts from the thing it checks.
SECTIONS: Sequence[Dict[str, str]] = (
    {"id": "task", "heading": "The task",
     "asks": "what success looks like, in two sentences, and on which robot"},
    {"id": "robot", "heading": "The robot",
     "asks": "the model and where it comes from, how many joints, which of "
             "them are driven, and what the actuators are"},
    {"id": "environment", "heading": "The environment",
     "asks": "the scene, physics step and decimation, control frequency, "
             "episode length, how many envs, and what is randomised"},
    {"id": "observations", "heading": "Observations",
     "asks": "one row per term, in manager order: group, term, dimension, the "
             "noise on it, clipping, scale, history. The rows in a group must "
             "add up to that group's dimension, and the groups to the "
             "observation the policy is handed; say whether the critic sees "
             "more, term by term"},
    {"id": "actions", "heading": "Actions",
     "asks": "one row per action term: which joints it drives, how many "
             "numbers, what one of them means physically and in what unit, "
             "scale, offset, clipping, and any filter between policy and "
             "simulator, with its coefficient"},
    {"id": "rewards", "heading": "Rewards",
     "asks": "one row per term, all of them: name, weight, every parameter it "
             "takes with its value, what it pays for, the file it is declared "
             "in, and what it actually paid under the zero policy"},
    {"id": "terminations", "heading": "Terminations",
     "asks": "one row per term, all of them: what triggers it, with the "
             "threshold and unit, and whether it is a failure or a truncation"},
    {"id": "commands", "heading": "Commands and curriculum",
     "asks": "every command term, what it samples, over what range and how "
             "often it resamples; every curriculum term, what it moves and on "
             "what condition"},
    {"id": "seeing", "heading": "How to see it",
     "asks": "a screenshot of the scene and a clip of an episode, from the "
             "preview under a zero policy"},
    {"id": "unknown", "heading": "What could not be determined",
     "asks": "anything above you could not read out of the code, as a question"},
)

HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", re.M)

#: What `rlmcp check` does not report, and therefore what the facts panel must
#: not appear to know. A missing capability is made visible, not left silent.
NOT_MEASURED = (
    "the observation dimension — `rlmcp check` reports the action count and "
    "not the observation one, so the report reads it out of the task's "
    "observation terms instead")


#: A task id as it appears in a heading: three or more dash-joined words, the
#: shape `Mjlab-Velocity-Flat-Unitree-Go1` and `Example-Task-v0` have. Used only
#: to read a document's own title, so a prose heading -- "The Go1 task" --
#: deliberately does not match: a document that names no task names no *other*
#: task either, and belongs to whoever is asking.
TASK_IN_TITLE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+){2,}\b")


# ── the document ─────────────────────────────────────────────────────────
def describes(path: Path, task: str) -> bool:
  """Whether the `TASK.md` at ``path`` is *this* task's, by its own title.

  Asked of the file rather than of the registry, and that is the point: which
  task a document describes is a property of the document. A package that
  registers one task has a `TASK.md` whose title is that task, or is prose --
  either way it is the asker's. A package that has grown a second task has a
  document whose title names the first, and the second must not be shown it.
  """
  try:
    head = path.read_text(errors="replace")[:2000]
  except OSError:
    return False
  found = HEADING.search(head)
  title = (found.group(1) if found else "").strip()
  if not title or task.lower() in title.lower():
    return True
  return not TASK_IN_TITLE.search(title)


def report_path(scope: Dict[str, Any], task: str = "") -> Optional[Path]:
  """Where this task's report lives, or ``None`` when the task has no package.

  ``scope`` is the app's own answer to "where does this task live" -- the
  directory an agent may edit freely. A task nothing in the task repository
  registers has no such directory, and inventing one would put a document
  describing a task next to a package that does not implement it.

  `TASK.md` is still the answer for the one-task package, which is nearly all
  of them, and it stays the answer for whichever task actually wrote it. It is
  only a *sibling* -- a second task registered from the same directory -- that
  gets :data:`OWN_REPORT`, and it gets it without anything moving on disk.
  """
  directory = (scope or {}).get("dir") or ""
  if not directory:
    return None
  where = Path(directory)
  if not task:
    return where / REPORT_NAME
  mine = where / OWN_REPORT.format(task=task)
  if mine.is_file():
    return mine
  shared = where / REPORT_NAME
  # Unwritten: `TASK.md` is free, and the first task to write one takes it.
  if not shared.is_file():
    return shared
  return shared if describes(shared, task) else mine


def media_dir(path: Optional[Path]) -> Optional[Path]:
  """The directory this report's pictures go in.

  Beside the document, and named after it: `media/` for the package's own
  `TASK.md`, `media/<task>/` for a sibling's. One rule, asked in the one place,
  so what :func:`ask` tells an agent to write and what :func:`media_beside`
  goes looking for cannot come apart.
  """
  if path is None:
    return None
  here = Path(path)
  if here.name == REPORT_NAME:
    return here.parent / MEDIA_DIR
  task = here.name[len("TASK."):-len(".md")] if here.name.startswith("TASK.") else here.stem
  return here.parent / MEDIA_DIR / task


def sections_in(text: str) -> List[str]:
  """Which of :data:`SECTIONS` this document actually has.

  Matched on the heading's words rather than on its exact spelling: an agent
  writing "Observations (what the policy sees)" has written the observations
  section, and a checklist that called that missing would be teaching people to
  write headings for the checker.
  """
  headings = [h.strip().lower() for h in HEADING.findall(text or "")]
  found = []
  for section in SECTIONS:
    first = section["heading"].split()[-1].lower()      # task, robot, rewards…
    if any(first in heading for heading in headings):
      found.append(section["id"])
  return found


def coverage(text: str, facts: Dict[str, Any]) -> Dict[str, Any]:
  """Which named terms the document actually contains, and which it left out.

  A report that lists nine of a task's fourteen reward terms is worse than no
  report: it reads complete, and the term somebody is looking for is the one
  that is not there. `rlmcp check` already names every reward and termination
  term the environment has -- so completeness is not an opinion here, and the
  panel states it.

  **Matched on the term's name appearing in the text**, which is deliberately
  crude: it can be fooled by a document that names a term in a sentence and
  never defines it, and it cannot be fooled the other way -- a term that is
  genuinely absent is always reported. The direction of the error is the one
  that keeps this honest, and the panel says how it matched.

  Observations and actions get no such check, because nothing upstream
  enumerates their terms (:data:`NOT_MEASURED`). The instruction demands one
  row each and a dimension that adds up; the studio cannot verify it.
  """
  body = (text or "").lower()
  out: Dict[str, Any] = {"checked": bool(facts.get("checked"))}
  for group, rows in (("rewards", facts.get("rewards") or []),
                      ("terminations", facts.get("terminations") or [])):
    names = [str(row.get("term") or "") for row in rows if row.get("term")]
    missing = [name for name in names if name.lower() not in body]
    out[group] = {"total": len(names), "named": len(names) - len(missing),
                  "missing": missing}
  return out


def media_beside(path: Optional[Path]) -> List[Dict[str, Any]]:
  """The pictures and clips in the report's own media directory."""
  directory = media_dir(path)
  if directory is None or not directory.is_dir():
    return []
  out = []
  try:
    entries = sorted(directory.iterdir())
  except OSError:
    return []
  for entry in entries:
    if entry.is_file() and entry.suffix.lower() in MEDIA_SUFFIXES:
      out.append({"path": str(entry), "name": entry.name,
                  "kind": "video" if entry.suffix.lower() in (".mp4", ".webm")
                          else "image"})
  return out


def freshness(written_at: float, code: Dict[str, Any]) -> Dict[str, Any]:
  """Whether the code moved after the report was written, and by how much.

  The same clock the Build view's gates are judged by (`_code_clock`), for the
  same reason: a document that describes a reward term somebody changed an hour
  ago is confidently wrong, and there is no other way to notice.
  """
  changed = float((code or {}).get("at") or 0.0)
  if not written_at or not changed or changed <= written_at:
    return {"stale": False, "by_s": 0.0, "file": ""}
  return {"stale": True, "by_s": round(changed - written_at, 1),
          "file": Path(str((code or {}).get("file") or "")).name}


# ── the facts under it ───────────────────────────────────────────────────
def sharing(task: str, row: Dict[str, Any],
            siblings: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
  """The tasks this one writes on top of, and where.

  `rlmcp train` puts a run under `<log-root>/<experiment_name>/<stamp>_<name>`,
  and `experiment_name` comes from the task's own RL config. Two tasks that
  share that config -- the ordinary way to write a rough variant of a flat task
  -- therefore share a directory: their checkpoints, their session and their
  progress videos land together, told apart only by a run name somebody typed.
  Nothing fails, which is why it goes unnoticed for a week.

  The studio does not rename anybody's experiment: `experiment_name` belongs to
  the task, one line of its RL config, and a studio that quietly overrode it
  would make the launch it shows you disagree with the launch you would run.
  What it does is say so, here, beside the rest of what the registry knows.
  """
  experiment = (row or {}).get("experiment") or ""
  package = (row or {}).get("package") or ""
  if not experiment:
    return {}
  others = sorted({str(other.get("task")) for other in siblings or ()
                   if other.get("task") and other.get("task") != task
                   and (other.get("experiment") or "") == experiment})
  return {"experiment": experiment, "package": package, "with": others}


def facts(task: str, scope: Dict[str, Any], gates: Dict[str, Any],
          row: Dict[str, Any],
          siblings: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
  """What the studio can state about this task without anybody writing prose.

  Two sources and no third: the registry row that says the task exists, and the
  last `rlmcp check`, which built the environment and stepped it. Everything
  here cites which of the two it came from, because the panel shows them side
  by side with an agent's prose and the reader has to be able to tell a
  measurement from a sentence.
  """
  answer = (gates or {}).get("answer") or {}
  env = answer.get("env") or {}
  rows: List[Dict[str, Any]] = [
      {"label": "task", "value": task, "from": "registry"},
      {"label": "backend", "value": row.get("backend") or "", "from": "registry"},
      {"label": "package", "value": scope.get("package") or "", "from": "registry"},
      {"label": "directory", "value": scope.get("dir") or "", "from": "registry"},
      {"label": "experiment", "value": (row or {}).get("experiment") or "",
       "from": "registry"},
  ]
  measured = [
      ("actions", env.get("actions"), ""),
      ("joints", env.get("joints"), ""),
      ("step dt", env.get("step_dt"), "s"),
      ("episode length", env.get("max_episode_length_s"), "s"),
  ]
  for label, value, unit in measured:
    if value is None:
      continue
    rows.append({"label": label, "value": f"{_number(value)} {unit}".strip(),
                 "from": "check"})
  return {
      "rows": [r for r in rows if r["value"] not in ("", None)],
      # The term breakdowns are rl-mcp's own and are handed over whole: the
      # Build view already draws them as bars, and a second shape for the same
      # numbers here is how two panels come to disagree about one measurement.
      "rewards": answer.get("rewards") or [],
      "terminations": answer.get("terminations") or [],
      "sharing": sharing(task, row, siblings),
      "checked": bool(answer),
      "checked_at": answer.get("at", 0.0),
      "check_ok": answer.get("ok"),
      "not_measured": [NOT_MEASURED],
  }


def _number(value: Any) -> str:
  """A measured number as a person reads it, without rounding it away.

  A task with no time limit answers `max_episode_length_s` with 5e10, and
  `50000000000.0 s` in a 200px rail is a wall of digits nobody parses. Exponent
  form above a million, trailing zeros dropped below it, integers left alone --
  and nothing is turned into a prettier number than it is.
  """
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return str(value)
  if isinstance(value, int) or float(value).is_integer() and abs(value) < 1e6:
    return str(int(value))
  if abs(value) >= 1e6:
    return f"{value:.3g}"
  return f"{value:g}"


def describe(task: str, scope: Dict[str, Any], code: Dict[str, Any],
             gates: Dict[str, Any], row: Dict[str, Any],
             conversations: Sequence[Dict[str, Any]] = (),
             siblings: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
  """Everything the Overview tab draws: the document, its age, and the facts."""
  path = report_path(scope, task)
  report: Dict[str, Any] = {"exists": False, "path": str(path) if path else ""}
  if path is not None and path.is_file():
    read = source_docs.read(path)
    if read.get("ok"):
      report = {
          "exists": True,
          **{k: read[k] for k in ("path", "name", "title", "text", "at",
                                  "size", "truncated")},
          "sections": sections_in(read.get("text", "")),
          "freshness": freshness(read.get("at") or 0.0, code),
      }
    else:
      report = {"exists": False, "path": str(path),
                "error": read.get("reason", "")}
  have = set(report.get("sections") or [])
  measured = facts(task, scope, gates, row, siblings)
  covered = coverage(report.get("text", "") if report.get("exists") else "",
                     measured)
  # A conversation row (`AgentSession.describe`) says what it is doing as
  # `state` and carries no `busy` flag, so `state` is what is read. Asking for
  # `busy` alone would leave the report thread twenty file reads in while the
  # tab still said "no report yet" over a live button: no chip, no poll, no
  # refresh when the file landed. `busy` is still honoured for a caller that
  # hands the flag straight in.
  writing = next((c for c in conversations or ()
                  if c.get("thread") == THREAD
                  and (c.get("busy") or c.get("state") in ("working", "needs_you"))),
                 None)
  return {
      "task": task,
      "report": report,
      "writable": bool(path),
      "why_not": _why_not(task, path, row),
      "sections": [{**s, "present": s["id"] in have} for s in SECTIONS],
      "missing": [s["heading"] for s in SECTIONS if s["id"] not in have],
      "media": media_beside(path),
      "facts": measured,
      # Only meaningful once there is a document: "0 of 14 named" about a task
      # with no report is a complaint about a file nobody has written.
      "coverage": covered if report.get("exists") else None,
      "code": code or {},
      "writing": bool(writing),
      # A turn blocked on a permission prompt is not making progress, and
      # only you can unblock it: the chip says so instead of "writing".
      "needs_you": bool(writing) and writing.get("state") == "needs_you",
      "thread": THREAD,
  }


def _why_not(task: str, path: Optional[Path], row: Dict[str, Any]) -> str:
  """Why there is nowhere to write, in the two ways that differ.

  A task the backend ships -- `Mjlab-Velocity-Rough-Unitree-G1` -- *is*
  registered, by a package that is not in this repository, and telling somebody
  nothing registers it is a lie they would go and check. A task nothing
  registers is the other case and is the one Build answers.
  """
  if path is not None:
    return ""
  package = (row or {}).get("package") or ""
  if package:
    return (f"`{task}` is registered by `{package}`, which is not a package in "
            "this task repository — so there is no directory here for a report "
            "to describe or an agent to edit.")
  return (f"nothing here registers `{task}`, so there is no package for a "
          "report to describe. Build it first.")


# ── what the agent is asked ──────────────────────────────────────────────
def ask(task: str, path: Path, description: Dict[str, Any],
        note: str = "") -> str:
  """The message the report conversation opens with.

  It carries what is *about this task* -- the file, the sections, the numbers
  the studio already measured, and whether a report is already there. How to
  write one at all is in the instruction harness (`60-the-task-report.md`),
  which every conversation already holds: repeating it here would fork the one
  piece of text somebody edits after watching a report come out wrong.
  """
  report = description.get("report") or {}
  fact = description.get("facts") or {}
  lines = [
      f"Write the task report for `{task}` into {path}.",
      "",
      "It is the task's *definition* — what the policy sees, what its actions "
      "mean, what it is paid for, what ends an episode — and not how any run "
      "of it went. The Overview tab in the studio renders this file.",
      "",
      "The sections, in this order, each as a `##` heading:",
  ]
  for section in SECTIONS:
    lines.append(f"* **{section['heading']}** — {section['asks']}")
  lines += [
      "",
      f"Pictures and clips go in {media_dir(path)}/ and are referenced by "
      "relative path from the section they belong to.",
  ]
  if Path(path).name != REPORT_NAME:
    lines += [
        f"`{Path(path).name}` rather than `{REPORT_NAME}`, and a media "
        "directory of its own, because another task is registered from this "
        "same package and has already written the one beside it. Describe "
        f"`{task}` only; where it differs from its sibling, say so, but do not "
        "document the sibling here.",
    ]

  if report.get("exists"):
    stale = (report.get("freshness") or {})
    lines += [
        "",
        f"**A report is already there.** Read {path} first and change what the "
        "code changed; keep the sentences that are still true.",
    ]
    if stale.get("stale"):
      lines.append(f"{stale.get('file') or 'A file in the package'} changed "
                   f"{stale.get('by_s')} s after it was written, which is why "
                   "you are being asked.")
    if description.get("missing"):
      lines.append("Sections it does not have yet: "
                   + ", ".join(description["missing"]) + ".")
    covered = description.get("coverage") or {}
    for group, label in (("rewards", "reward"), ("terminations", "termination")):
      absent = (covered.get(group) or {}).get("missing") or []
      if absent:
        lines.append(f"It names {(covered[group] or {}).get('named')} of "
                     f"{(covered[group] or {}).get('total')} {label} terms. "
                     "These are not in it at all: "
                     + ", ".join(f"`{name}`" for name in absent) + ".")

  lines += [
      "",
      "**Every term, not a selection of them.** One row per reward term, per "
      "termination term, per observation term, per action term, per command "
      "and per curriculum term — the whole manager, in its own order, "
      "including the terms whose weight is zero and the ones that never fire. "
      "No `…`, no \"and others\", no \"the main ones are\". A reader has to be "
      "able to hold this beside the config and find every line of it, and the "
      "term somebody is hunting is always the one a summary dropped.",
      "Exact values, with units: weights as written, every parameter a term "
      "takes, thresholds, ranges, scales, dimensions. Where a number is "
      "computed at construction, give the expression and the file it is in "
      "rather than a rounded guess.",
  ]

  if fact.get("checked"):
    rewards = fact.get("rewards") or []
    terminations = fact.get("terminations") or []
    lines += ["", "What the last `rlmcp check` measured, so you do not have to "
                  "re-measure it. It is also the *complete list of names* for "
                  "those two managers — the studio checks the report against "
                  "it and says which terms are missing:"]
    for row in fact.get("rows", []):
      if row.get("from") == "check":
        lines.append(f"* {row['label']}: {row['value']}")
    lines.append(f"* {len(rewards)} reward term(s), each of which needs a row:")
    for term in rewards:
      lines.append(f"    * `{term.get('term')}` — {term.get('mean')} per step "
                   "under a zero policy")
    lines.append(f"* {len(terminations)} termination term(s), each of which "
                 "needs a row:")
    for term in terminations:
      lines.append(f"    * `{term.get('term')}` — fired on "
                   f"{term.get('per_step')} of steps")
    lines.append("A live preview answers `list_parameters`, which is the "
                 "authority on the weights and action scales *as the "
                 "environment is running them* — prefer it to a default read "
                 "out of a config file.")
  else:
    lines += ["", "No `rlmcp check` has run on this task in this studio, so "
                  "there are no measured numbers to quote. Run one if you want "
                  "them; otherwise read the terms out of the package and say "
                  "that is where they came from."]

  if note.strip():
    lines += ["", "What the person asked for, in their words:", note.strip()]
  return "\n".join(lines)


__all__ = ["MEDIA_DIR", "OWN_REPORT", "REPORT_NAME", "SECTIONS", "THREAD",
           "ask", "coverage", "describes", "media_dir", "sharing",
           "describe", "facts", "freshness", "media_beside", "report_path",
           "sections_in"]
