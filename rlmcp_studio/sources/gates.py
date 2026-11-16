"""The gates, run against a task, cached per task.

`rlmcp check` is rl-mcp's own: it builds the task with no policy, rolls it, and
answers imports / constructs / steps / rewards finite / terminations sane /
trains, plus what each reward term paid. The studio does not repeat any of that
-- it runs the command and renders the answer, which is rule zero applied to
the one feature most tempted to break it.

The sixth of those, `trains`, is the one this module has to be careful about.
The first five never construct a PPO actor -- a zero policy is a callable
returning zeros -- so a task with a hole in its `rl_cfg` passes all five and
dies at iteration 0, while a strip of five green ticks reads, to an agent and
to a person, as *this will train*. `trains` builds what `rlmcp train` builds
and takes one iteration on it, on CPU, which is why the answer to "will it
train" is upstream's to give and not the studio's to compute.

What is here is what a *panel* needs and a command line does not: which task
was asked about, whether an answer is in flight, how old the one on screen is,
whether the runner gate was answered *at all* by the rl-mcp installed here,
and the fact that a check takes seconds so nothing may block a paint waiting
for it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from rlmcp_studio.sources.taskenv import TaskEnv

TIMEOUT = 300.0
"""A cold simulator import plus a construct plus 150 steps. Generous, because
the failure this bounds is a task package whose import hangs."""

DEFAULT_STEPS = 150
"""rl-mcp's own default, and the number its tuning guide (`docs/tuning.md`)
uses."""

RUNNER_GATE = "trains"
"""The gate that reads the task's RL config, and the only one that does."""

NO_RUNNER_GATE = (
    "not run: the rl-mcp installed here answers five gates and not this one, "
    "so nothing has built this task's RL runner. A full strip means the "
    "environment is sound, not that training starts.")
"""Said in rl-mcp's own words for a gate that did not run -- `not run: <why>`."""


def _with_runner_gate(rows: list) -> list:
  """rl-mcp's rows, plus the sixth one written down as absent when it is.

  An older rl-mcp answers only the first five gates and does not emit the
  `trains` row at all. A strip that
  simply drew what arrived would then show five ticks and nothing about the
  sixth question -- which is precisely the reading the gate exists to stop
  being wrong. So the absence is written down, in rl-mcp's own vocabulary for
  a gate that did not run (`ok: None`), rather than left out for the panel to
  interpret. Nothing here decides whether the task trains; it decides only that
  nobody asked.
  """
  if any(row.get("gate") == RUNNER_GATE for row in rows):
    return rows
  return [*rows, {"gate": RUNNER_GATE, "ok": None, "detail": NO_RUNNER_GATE,
                  "fix": ""}]


def _will_train(rows: list):
  """Yes, no, or nobody asked -- which is not the same question as `passed`.

  `passed` is rl-mcp's answer to "did any gate fail", and a gate that did not
  run fails nothing, so a task whose runner was never built passes. That is
  right for a command whose exit code gates `rlmcp check && rlmcp train`, and
  wrong for a panel somebody reads as *green means it will train*. The
  difference between proven and not asked is the whole of this.
  """
  states = [row.get("ok") for row in rows]
  if any(ok is False for ok in states):
    return False
  return next((row.get("ok") for row in rows
               if row.get("gate") == RUNNER_GATE), None)


class Gates:
  """The last check for each task, and at most one check in flight."""

  def __init__(self, env: TaskEnv) -> None:
    self.env = env
    self._answers: Dict[str, Dict[str, Any]] = {}
    self._running: Dict[str, asyncio.Task] = {}

  def cached(self, task: str) -> Optional[Dict[str, Any]]:
    return self._answers.get(task)

  def describe(self, task: str) -> Dict[str, Any]:
    """What the panel draws: an answer, or the honest absence of one.

    ``running`` is a state of its own rather than a stale answer with a
    spinner over it: a check that is re-running after an edit is showing the
    *previous* task's verdict, and saying so is the difference between reading
    it and being misled by it.
    """
    answer = self._answers.get(task)
    return {
        "task": task,
        "running": task in self._running,
        "answer": answer,
        "age_s": round(time.time() - answer["at"], 1) if answer else -1.0,
    }

  async def run(self, task: str, steps: int = DEFAULT_STEPS,
                num_envs: int = 4, device: str = "cpu",
                policy: str = "zero") -> Dict[str, Any]:
    """Check this task, or wait for the check already checking it.

    Two panels asking at once must not build two environments, and a second
    request while one is in flight is nearly always the same question -- so it
    waits for the answer rather than starting a second simulator.
    """
    existing = self._running.get(task)
    if existing is not None:
      await asyncio.shield(existing)
      return self.describe(task)

    job = asyncio.ensure_future(
        self._ask(task, steps=steps, num_envs=num_envs, device=device, policy=policy))
    self._running[task] = job
    try:
      await job
    finally:
      self._running.pop(task, None)
    return self.describe(task)

  async def _ask(self, task: str, steps: int, num_envs: int, device: str,
                 policy: str) -> None:
    answer = await self.env.json(
        "--json", "check", "--task", task, "--policy", policy,
        "--steps", str(int(steps)), "--num-envs", str(int(num_envs)),
        "--device", device, timeout=TIMEOUT)

    if not answer["ok"]:
      self._answers[task] = {
          "at": time.time(), "ok": False, "error": answer["error"],
          "gates": [], "rewards": [], "terminations": [], "passed": None,
          "will_train": None,
      }
      return

    payload = answer["payload"] or {}
    # `check` speaks the envelope every offline command speaks: {ok, result}.
    # A refusal -- an unknown task, a policy this command does not take -- is
    # `ok: false` with the sentence in `error`, and is not a failed gate.
    if not payload.get("ok", False):
      self._answers[task] = {
          "at": time.time(), "ok": False,
          "error": payload.get("error") or "the check refused to run",
          "gates": [], "rewards": [], "terminations": [], "passed": None,
          "will_train": None,
      }
      return

    result = payload.get("result") or {}
    rows = _with_runner_gate(list(result.get("gates") or []))
    self._answers[task] = {
        "at": time.time(),
        "ok": True,
        "error": "",
        "passed": result.get("passed"),
        # `passed` is rl-mcp's; this is the panel's, and they differ by exactly
        # one case -- the runner gate that did not run.
        "will_train": _will_train(rows),
        "first_problem": result.get("first_problem", ""),
        "gates": rows,
        "rewards": result.get("rewards", []),
        "dominance": result.get("dominance", {}),
        "terminations": result.get("terminations", []),
        "env": result.get("env", {}),
        "seconds": result.get("seconds", 0.0),
        "steps": result.get("steps", 0),
        "num_envs": result.get("num_envs", 0),
        "device": result.get("device", ""),
        "policy": result.get("policy", ""),
    }


__all__ = ["DEFAULT_STEPS", "Gates", "NO_RUNNER_GATE", "RUNNER_GATE",
           "TIMEOUT"]
