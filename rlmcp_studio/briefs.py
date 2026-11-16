"""What a conversation is opened with, and saying something into one.

A conversation is about a run (keyed by its record id) or about a task being
built (keyed `task:<id>`). Each kind has one brief -- the context text, the
working directory, the session and the paths an agent may edit freely -- and
every route that sends a message or shows the Briefing window asks here, so
what the agent is told cannot differ between them.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi.responses import JSONResponse

from rlmcp_studio.agents.run_context import build_context
from rlmcp_studio.context import Studio
from rlmcp_studio.runners import base as runners_base
from rlmcp_studio.runners import local as local_runner
from rlmcp_studio.sources import taskenv as source_taskenv
from rlmcp_studio.sources.records import UNKNOWN_TASK
from rlmcp_studio.sources.session import LiveSession, NOT_A_METRIC


def task_brief(studio: Studio, task: str) -> Dict[str, Any]:
  """What a conversation about a task being built is opened with.

  Two routes send one -- the Build view's composer and the Overview tab
  asking for a task report -- and a third, the Briefing window, only reads
  it. All three ask here: what the agent is told about *the task* has to be
  the same either way, and a second copy of this paragraph would drift
  within a week.
  """
  scope = studio.task_scope(task)
  preview = studio.previews.describe(task)
  answer = (studio.gates.cached(task) or {})
  lines = [f"You are working on the task `{task}`, which is being built "
           f"rather than watched: it may have no runs at all yet."]
  if scope["dir"]:
    lines.append(f"Its package is `{scope['package']}`, at {scope['dir']}. "
                 "Edits inside that directory do not need approval; edits "
                 "anywhere else do.")
  else:
    lines.append("Nothing here registers it yet, so it has no package on "
                 "disk. Ask before writing outside a directory the person "
                 "names.")
  lines.append(f"The preview is {preview.get('state', 'off')}"
               + (f" (session {preview['session']})" if preview.get("session") else "")
               + ". `rlmcp play --policy zero --mode hold` is what holds it, "
                 "and the studio restarts it when you ask for a rebuild.")
  if answer.get("gates"):
    failed = [g["gate"] for g in answer["gates"] if g.get("ok") is False]
    # A gate that did not run is named rather than folded into "all passed":
    # `trains` is skipped whenever an earlier gate broke, and telling an agent
    # every gate passed when the one that reads the RL config never ran is the
    # sentence this whole gate exists to stop being said.
    skipped = [g["gate"] for g in answer["gates"] if g.get("ok") is None]
    lines.append("Last `rlmcp check`: "
                 + (f"failed at {failed[0]}." if failed
                    else "every gate passed." if not skipped
                    else "nothing failed, but these did not run: "
                         + ", ".join(skipped) + "."))
    if answer.get("rewards"):
      top = answer["rewards"][0]
      lines.append(f"Largest reward term: {top['term']} at {top['mean']:.4g} "
                   "per step under a zero policy.")
  # Where the checks actually run. Without this the agent goes looking for
  # `rlmcp check` in whatever rl-mcp its own shell resolves -- which may be a
  # different install from the one the studio drives, so the command it was
  # told to verify with can appear not to exist. The panel shows this to the
  # person; the agent needs it just as much.
  where = studio.task_env.describe()
  lines.append(
      f"The studio builds and checks this task with {where['python']}"
      + (f", from {where['cwd']}" if where.get("cwd") else "")
      + ". That is the environment that has the simulator and this task's "
        "package; your own shell may resolve a different rl-mcp, so run the "
        "commands below through that interpreter, exactly as written:")
  package_args = "".join(f" --task-package {module}" for module in studio.task_env.packages)
  lines.append(
      f"  {where['python']} -c 'from rlmcp.cli import main; import sys; "
      f"sys.exit(main(sys.argv[1:]))' check --task {task}{package_args}")
  lines.append("Verify with that rather than by reasoning about the code, and "
               "prefer editing the task's own config over inventing new "
               "machinery. The studio's Build view runs the same command when "
               "somebody presses Run the checks, so its answer and yours "
               "cannot disagree.")

  return {"context": "\n".join(lines),
          "cwd": scope["dir"] or (studio.task_env.cwd or ""),
          "session_dir": preview.get("session") or "",
          "free_paths": scope["free"],
          "records_root": str(studio.cfg.primary_records or ""),
          "scope": scope}

async def tell_task_agent(studio: Studio, task: str, text: str,
                          thread: str = "main",
                          interrupt: bool = False) -> JSONResponse:
  """Say something to an agent about a task, in one of its threads."""
  # Asked here rather than by each caller: "not signed in" is what the
  # provider says when nobody has asked the CLI yet, and after a restart
  # nobody has -- so a route that skipped it would fail with the wrong
  # sentence.
  if not studio.agents.provider.auth_state:
    await studio.agents.refresh_auth()
  reason = studio.agents.provider.unavailable_reason()
  if reason:
    return JSONResponse({"error": reason}, status_code=503)

  brief = task_brief(studio, task)
  try:
    await studio.agents.send(
        f"task:{task}", text,
        context=brief["context"],
        cwd=brief["cwd"],
        session_dir=brief["session_dir"],
        thread=thread,
        free_paths=brief["free_paths"],
        records_root=brief["records_root"],
        interrupt=interrupt)
  except RuntimeError as exc:
    return JSONResponse({"error": str(exc)}, status_code=409)
  return JSONResponse({"ok": True, "scope": brief["scope"]})


def agent_cwd(studio: Studio, record) -> str:
  """Where the agent starts: the task package, not a logs directory.

  Not the session's grandparent, which for `logs/rsl_rl/<task>/<run>/rlmcp` is
  a *logs* folder -- an agent opened there sits in a directory of checkpoints
  and event files, with none of the code it is being asked about and none of
  the repository's own CLAUDE.md, skills or commands, which are most of the
  reason to host this SDK rather than call the API. That is only the fallback
  when no code root is known.
  """
  if studio.code_roots:
    return str(studio.code_roots[0])
  if record.session:
    return str(Path(record.session).parent.parent)
  return ""

def task_facts(studio: Studio, task: str) -> Dict[str, Any]:
  """What this task is for, and what registers it.

  The brief comes from `starting`, which is the studio's own note from when
  somebody began the task -- and the only prose in the system that says what
  a task is *meant* to do. The registry knows its id, its backend and its
  package, and nothing about its intent. An agent handed both stops opening
  the conversation by asking what it is looking at.

  Both are read from what is already in memory: `registry.cached()` rather
  than `registry.tasks()`, because listing tasks means importing the
  simulator in a subprocess and no message to an agent should cost that.
  """
  if not task:
    return {"task_brief": "", "backend": "", "package": ""}
  brief = next((b.get("what", "") for b in studio.starting.all()
                if b.get("task") == task), "")
  listing = studio.registry.cached() or {}
  row = next((t for t in listing.get("tasks", [])
              if t.get("task") == task), {})
  return {"task_brief": brief,
          "backend": row.get("backend", ""),
          "package": row.get("package", "")}

def run_progress(record) -> Dict[str, Any]:
  """The run's own numbers, read from its session directory.

  So that orienting on a conversation does not begin with three tool calls
  the studio has already made. A snapshot, and `build_context` says so: the
  live answer is `get_training_status`, and this is what was true when the
  message was composed.
  """
  if not record.session:
    return {}
  try:
    snap = LiveSession(Path(record.session)).snapshot(metrics_tail=1)
  except Exception:                       # noqa: BLE001 - a missing run is not an error
    return {}
  if not snap.get("ok"):
    return {}
  status = snap.get("status") or {}
  rows = snap.get("metrics") or []
  last = rows[-1] if rows else {}
  iteration = status.get("iteration") or last.get("iteration") or 0
  metrics = [(name, f"{value:.4g}" if isinstance(value, (int, float)) else str(value))
             for name, value in last.items() if name not in NOT_A_METRIC]
  return {"state": snap.get("state", ""), "iteration": int(iteration or 0),
          "metrics": metrics}

def launch_recipe(studio: Studio, record) -> Dict[str, Any]:
  """The command that starts the next run of this task, on the studio's machine.

  Written out rather than described. The interpreter is the task
  repository's and not the one on anybody's PATH, `--record-run` is what
  ties the metrics to a record, and `--records-root` is what stops a bare id
  resolving against somebody else's store -- three things an agent has no
  way to infer and easily gets wrong. This is the same spec the
  Launch button builds, so the two cannot drift apart.
  """
  if not record.task or record.task == UNKNOWN_TASK:
    return {}
  spec = runners_base.RunSpec(
      task=record.task, entrypoint=local_runner.default_entrypoint(),
      args={"num-envs": 4096, "device": "cuda:0"},
      # A placeholder rather than a real id: the command is a recipe for the
      # *next* run, and one that arrived with this run's id already in it
      # would be a way to overwrite a record by copying and pasting.
      record_id="RECORD_ID",
      workdir=studio.task_env.cwd or "", labels={})
  try:
    command = local_runner.command_line(studio.runner, spec)
  except Exception:                       # noqa: BLE001 - a recipe is optional
    return {}
  root = str(studio.cfg.primary_records or "")
  # Through the same interpreter, and *without* `--task-package`: that flag
  # exists on `tasks` and `check` and on no other subcommand, so borrowing
  # `TaskEnv.argv` here would hand the agent a `record new` that argparse
  # rejects. The Build view spells `check` out the same way.
  prefix = " ".join(shlex.quote(part) for part in
                    (studio.task_env.python or sys.executable, "-c",
                     source_taskenv.ENTRY))
  return {
      "command": command,
      "cwd": studio.task_env.cwd or "",
      "record_command": (
          f"{prefix} record new <slug> --task {record.task} "
          f"--hypothesis \"...\" --prediction \"...\" "
          f"--parent {record.id}"
          + (f" --records-root {root}" if root else "")),
  }

def run_brief(studio: Studio, record) -> Dict[str, Any]:
  """What a conversation about this run is opened with — text and paths.

  Split out of `tell_agent` so that the Briefing window can be answered
  with the same four values a message carries, rather than with a second
  copy of them. A window whose whole claim is "this is what the agent was
  given" has to be built from the thing that gives it.
  """
  from rlmcp_studio.sources.records import record_summary
  detail = {
      "hypothesis": record.hypothesis,
      "prediction": record.prediction,
      "falsifier": {"prose": record.falsifier.prose},
      # The paths, so "where is the session" is answered before it is asked.
      "records_root": str(studio.cfg.primary_records or ""),
      "code_root": str(studio.code_roots[0]) if studio.code_roots else "",
      "log_root": str(studio.runner.log_root or ""),
      **task_facts(studio, record.task),
      "progress": run_progress(record),
      "launch": launch_recipe(studio, record),
  }
  return {
      "context": build_context(record_summary(record), detail),
      "cwd": agent_cwd(studio, record),
      "session_dir": record.session or "",
      # Which store this record lives in. The record tools take a bare id,
      # and with two roots configured an unrooted server writes into whichever
      # one its working directory happens to find.
      "records_root": str(studio.cfg.primary_records or ""),
      "free_paths": [],
  }

async def tell_agent(studio: Studio, record, text: str, thread: str = "main",
                     images: Optional[List[bytes]] = None,
                     shows: str = "", records_root: str = "",
                     interrupt: bool = False) -> None:
  """Say something to a run's agent, with the run's own context attached.

  One place, so a message typed in the composer and one raised by pointing at
  a frame start the conversation knowing the same things.
  """
  brief = run_brief(studio, record)
  await studio.agents.send(
      record.id, text,
      context=brief["context"],
      cwd=brief["cwd"],
      session_dir=brief["session_dir"],
      thread=thread,
      images=images,
      shows=shows,
      records_root=records_root or brief["records_root"],
      interrupt=interrupt,
  )