"""The text a conversation about a run is opened with.

Composed from plain dicts (the record's summary and the studio's detail about
it) so its shape can be tested without a store behind it.
"""

from __future__ import annotations

from typing import Any, Dict, List


def build_context(record: Dict[str, Any], detail: Dict[str, Any]) -> str:
  """What the agent should already know before it is asked anything.

  Everything here is a question an agent would otherwise ask, or a thing it
  would get wrong for want of being told. The rule for adding to it is that:
  not "would this be nice to know", but "does an agent waste a turn
  discovering it". Four sections, in the order somebody picking up the run
  would want them.

  * **The claim** — hypothesis, prediction, falsifier. An agent that knows what
    would prove the run wrong argues differently from one that only knows what
    it hopes for.
  * **The task** — what it is meant to do, and where its code is. The agent is
    started in the task repository and could find the package by looking, but
    told where it is, it reads instead of searching.
  * **How it has gone** — the numbers, so orientation does not begin with three
    tool calls that the studio has already made.
  * **How to launch another** — because the agent may, and the studio's own
    Launch button is not reachable from a conversation.

  Built from a plain dict rather than from the record object so the shape of
  this text is testable without a store behind it.
  """
  lines = [f"Run {record.get('id')} — {record.get('slug')} on task {record.get('task')}."]
  if record.get("verdict"):
    lines.append(f"Verdict so far: {record['verdict']}.")
  if detail.get("hypothesis"):
    lines.append(f"Hypothesis: {detail['hypothesis']}")
  if detail.get("prediction"):
    lines.append(f"Prediction: {detail['prediction']}")
  falsifier = (detail.get("falsifier") or {}).get("prose")
  if falsifier:
    lines.append(f"Falsifier: {falsifier}")
  if record.get("parent"):
    lines.append(f"Config parent: {record['parent']}.")
  if record.get("weights"):
    lines.append(f"Warm started from {record['weights']}, so it cannot claim `validated`.")
  if record.get("change"):
    lines.append("Changed from parent: " + "; ".join(record["change"][:6]))

  lines += _the_task(record, detail)
  lines += _how_it_went(record, detail)
  lines += _where_things_are(record, detail)
  lines += _how_to_launch(record, detail)
  return "\n".join(lines)


def _the_task(record: Dict[str, Any], detail: Dict[str, Any]) -> List[str]:
  """What this task is for, and where the code that implements it lives.

  The brief is the studio's own note from when the task was started, which is
  the only prose anywhere that says what the task is *meant* to do — the
  registry knows its id and its package and nothing about its intent.
  """
  out: List[str] = []
  brief = (detail.get("task_brief") or "").strip()
  facts = []
  if detail.get("backend"):
    facts.append(f"It is a {detail['backend']} task.")
  if detail.get("package"):
    facts.append(f"The `{detail['package']}` package is what registers it.")
  if not (brief or facts or detail.get("code_root")):
    return out
  out += ["", "## The task"]
  if brief:
    out.append(f"What it is meant to do: {brief}")
  if facts:
    out.append(" ".join(facts))
  if detail.get("code_root"):
    out.append(
        f"Its implementation is under {detail['code_root']} — the environment "
        "config, the reward terms, the curriculum and the launcher are files "
        "there. Read them before you reason about what the run is doing; they "
        "are the ground truth that the metrics are only evidence about.")
  return out


def _how_it_went(record: Dict[str, Any], detail: Dict[str, Any]) -> List[str]:
  """The run's own numbers, so orienting does not start with three tool calls.

  A summary, deliberately, and it says so: it is a snapshot taken when this
  message was composed, and `get_training_status` is the live answer. An agent
  that trusts a stale number in a system whose whole subject is a number that
  moves is worse off than one that was told nothing.
  """
  progress = detail.get("progress") or {}
  out: List[str] = []
  state = progress.get("state") or ""
  iteration = progress.get("iteration")
  metrics = progress.get("metrics") or []
  feedback_open = record.get("feedback_open") or 0
  if not (state or iteration or metrics or feedback_open):
    return out

  out += ["", "## How this run has gone"]
  if state == "running":
    where = f" at iteration {iteration:,}" if iteration else ""
    out.append(f"It is **training right now**{where}.")
  elif state:
    out.append(f"Its trainer is {state}"
               + (f"; it reached iteration {iteration:,}." if iteration else "."))
  if metrics:
    out.append("Last seen: "
               + ", ".join(f"{name} {value}" for name, value in metrics[:8]))
  if feedback_open:
    out.append(f"{feedback_open} question{'s' if feedback_open != 1 else ''} in "
               "the feedback timeline still unanswered — `get_feedback_timeline` "
               "to read them, `answer_feedback` to close one. They are counted "
               "on screen, so leaving one is visible.")
  if state or metrics:
    out.append("Those numbers were read when this message was composed. "
               "`get_training_status` is the live answer and costs nothing.")
  return out


def _where_things_are(record: Dict[str, Any], detail: Dict[str, Any]) -> List[str]:
  """Paths the Read tool can open.

  An agent that is not told where the session is cannot find it: the record
  names a directory and the studio knows it, so the context carries it. A
  path it can read is worth more than any amount of prose about how to look.
  """
  where = []
  if record.get("session"):
    where.append(f"Session directory: {record['session']}")
  for name in ("records_root", "code_root", "log_root"):
    if detail.get(name):
      where.append(f"{name.replace('_', ' ').capitalize()}: {detail[name]}")
  if not where:
    return []
  return ["", "## Where things are", "\n".join(where),
          "The rlmcp tools are already pinned to that session; you do not need "
          "to pass it, and you should not go looking for it with a shell "
          "command."]


def _how_to_launch(record: Dict[str, Any], detail: Dict[str, Any]) -> List[str]:
  """How to start the next run, since starting one is yours to do.

  This section exists because the agent could not have worked it out. The
  studio launches through an interpreter that is not the one on its own PATH
  (the task repository's `.venv`, which is where the simulator is), and a run
  with no `--record-run` writes its metrics into a record that does not exist.
  Both mistakes are easy to make, and neither is visible until afterwards.
  """
  launch = detail.get("launch") or {}
  command = (launch.get("command") or "").strip()
  if not command:
    return []
  out = ["", "## Starting another run",
         "Launching, restarting and stopping runs is yours to do — the person "
         "watches and interrupts, they do not authorise. Open a record first, "
         "always, because the prediction is only worth something before the "
         "result:", "",
         f"    {launch.get('record_command') or 'rlmcp record new <slug> --hypothesis ... --parent ' + str(record.get('id') or '')}",
         "",
         "then launch it, putting the id that comes back where this says "
         "`RECORD_ID`:", "",
         f"    {command}", ""]
  if launch.get("cwd"):
    out.append(f"Run it from {launch['cwd']}.")
  out.append(
      "That interpreter is the point of the line: it is the environment with "
      "the simulator and this task's package in it, and the `rlmcp` on your own "
      "PATH may be a different install. Start it detached — `nohup ... &`, or a "
      "background shell — so that it outlives the command and you can go on "
      "watching it here. The studio's Launch button runs exactly this.")
  out.append(
      "Once it is up, rlmcp finds it: the tools in this conversation stay "
      "pinned to the run they were given, so open a conversation on the new "
      "run to drive it, or use the CLI above from a shell.")
  return out
