"""The sixth gate: whether the task's runner builds, and how the strip says so.

The failure this file exists for: five gates can pass on a task that cannot
train -- say its `rl_cfg` is missing a field the PPO actor needs -- because
every one of those five steps the environment with a zero policy, which is a
callable returning zeros and never constructs an actor at all. The Build view
reads as *green means it will train*, and five gates alone do not mean that.

The check itself is rl-mcp's (`rlmcp check` has a `trains` gate that builds
what `rlmcp train` builds and takes one iteration on it, on CPU). What is
tested here is the studio's half, which is the half that can still be wrong:
rendering six gates rather than five, and -- the case that matters more --
saying *unanswered* when the rl-mcp installed here does not answer the sixth,
instead of showing five ticks and a silence.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from rlmcp_studio.sources import gates as source_gates
from rlmcp_studio.sources.taskenv import TaskEnv
from tests.client_source import read_js

FIRST_FIVE = ["imports", "constructs", "steps", "rewards_finite", "terminations"]

BUILD_JS = read_js("builder/gates.js")


# ── the shape `rlmcp check` answers in ────────────────────────────────────
#
# Rows are written here because a check needs a simulator and this suite has
# none, but the *verdict* over them is rl-mcp's own `summarise`: a hand-written
# `passed` would be testing the fixture rather than the studio.

def _passed(name: str, detail: str = "") -> dict:
  return {"gate": name, "ok": True, "detail": detail, "fix": ""}


def _failed(name: str, detail: str, fix: str = "", **extra) -> dict:
  return {"gate": name, "ok": False, "detail": detail, "fix": fix, **extra}


def _did_not_run(name: str, why: str) -> dict:
  return {"gate": name, "ok": None, "detail": f"not run: {why}", "fix": ""}


def _result(rows: list) -> dict:
  from rlmcp.check import summarise

  return {"task": "T", "policy": "zero", "steps": 150, "num_envs": 4,
          "device": "cpu", "gates": rows, "rewards": [], "terminations": [],
          "env": {}, "seconds": 3.4, **summarise(rows)}


class FakeCheck:
  """A `TaskEnv` that answers one canned `rlmcp check` instead of running one."""

  def __init__(self, payload: dict) -> None:
    self.payload = payload
    self.command: list = []

  async def json(self, *command, timeout: float = 0.0) -> dict:
    self.command = list(command)
    return {"ok": True, "payload": self.payload, "note": "", "argv": []}


def _kept(rows: list) -> dict:
  """What the panel would be handed, for a check that answered these rows."""
  gates = source_gates.Gates(FakeCheck({"ok": True, "result": _result(rows)}))
  asyncio.run(gates.run("T"))
  return gates.cached("T")


def _row(kept: dict, name: str) -> dict:
  return next(r for r in kept["gates"] if r["gate"] == name)


# ── the gate itself ───────────────────────────────────────────────────────

def test_a_task_whose_rl_config_is_incomplete_does_not_pass_the_gates():
  """The case this gate exists for: five ticks and a dead launch.

  Nothing here decides that -- `trains` failing is rl-mcp's answer -- but the
  studio has to carry it whole, including the traceback, because the panel's
  next move is handing that text to the agent in the pane beside it.
  """
  kept = _kept([_passed(n) for n in FIRST_FIVE]
               + [_failed("trains",
                          "AttributeError: 'PPOCfg' object has no attribute "
                          "'distribution_cfg'",
                          fix="The runner is built from the task's own RL config.",
                          traceback="Traceback (most recent call last): ...")])

  assert kept["passed"] is False, "one gate failed, so the check failed"
  assert kept["will_train"] is False
  assert kept["first_problem"] == "trains"
  assert _row(kept, "trains")["traceback"], \
      "the traceback survives the studio, because the agent is sent it verbatim"


def test_a_check_that_never_answered_the_runner_gate_does_not_read_as_green():
  """An rl-mcp older than the gate emits five rows and says nothing about the
  sixth. Passing that through unchanged is the five-gate blind spot again:
  the strip would be green end to end, and the one question that would have
  caught the broken config would not even be on screen.
  """
  kept = _kept([_passed(n) for n in FIRST_FIVE])

  assert kept["passed"] is True, "rl-mcp's own verdict: nothing failed"
  assert kept["will_train"] is None, "and 'nothing failed' is not 'it will train'"
  row = _row(kept, "trains")
  assert row["ok"] is None, "a gate nobody answered is neither passed nor failed"
  assert row["detail"].startswith("not run:"), \
      "said in rl-mcp's own words for a gate that did not run"


def test_a_runner_gate_the_check_skipped_keeps_the_reason_the_check_gave():
  """`trains` is skipped for reasons of its own -- an earlier gate failed, the
  task registers no RL config, no runner is importable here -- and each of
  those sentences is more useful than anything the studio could write over it.
  """
  kept = _kept([_passed(n) for n in FIRST_FIVE]
               + [_did_not_run("trains",
                               "this task registers no RL config to build from")])

  assert kept["will_train"] is None
  detail = _row(kept, "trains")["detail"]
  assert "registers no RL config" in detail
  assert source_gates.NO_RUNNER_GATE not in detail, \
      "the studio only speaks for a gate that is absent, never over one that spoke"


def test_a_task_whose_runner_builds_is_the_only_one_that_says_it_will_train():
  kept = _kept([_passed(n) for n in FIRST_FIVE]
               + [_passed("trains", "1 iteration of 24 steps/env")])

  assert kept["passed"] is True
  assert kept["will_train"] is True


def test_a_check_that_could_not_run_at_all_claims_nothing_about_training():
  """An unknown task, or a check that refused: no gates, and no verdict either.
  The strip draws six pale cells, which is the truth."""
  gates = source_gates.Gates(FakeCheck({"ok": False, "error": "Unknown task 'T'"}))
  asyncio.run(gates.run("T"))
  kept = gates.cached("T")

  assert kept["ok"] is False and kept["gates"] == []
  assert kept["passed"] is None and kept["will_train"] is None


def test_the_check_the_studio_runs_is_the_one_that_builds_the_runner(monkeypatch):
  """Two things about the command line, and both are the feature.

  `--no-runner` turns the sixth gate off upstream, and a studio that sent it
  would be back to five green ticks meaning nothing about training. `--device
  cpu` is why it is allowed to run at all: one iteration of a handful of envs
  on the CPU, so a check never queues behind -- or evicts -- the run that may
  be on the card.
  """
  sent = {}

  async def fake(*argv, **kwargs):
    sent["argv"] = list(argv)

    class Answered:
      returncode = 0

      async def communicate(self):
        rows = [_passed(n) for n in FIRST_FIVE] + [_passed("trains")]
        return json.dumps({"ok": True, "result": _result(rows)}).encode(), b""

    return Answered()

  monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
  gates = source_gates.Gates(TaskEnv())
  asyncio.run(gates.run("Some-Task-G1"))

  argv = sent["argv"]
  assert "check" in argv
  assert "--no-runner" not in argv, "that would switch the sixth gate off"
  assert argv[argv.index("--device") + 1] == "cpu"
  assert gates.cached("Some-Task-G1")["will_train"] is True


def test_the_studio_never_builds_the_runner_itself():
  """Rule zero, in the feature most tempted to break it.

  "Does this task train" is answerable only by constructing what `rlmcp train`
  constructs, from the same registry, and a second construction here would be
  a second opinion about the thing that matters most -- and would import torch
  into a backend whose never importing it is what makes it safe to restart
  mid-run.
  """
  source = (Path(__file__).resolve().parent.parent
            / "rlmcp_studio" / "sources" / "gates.py").read_text()

  for forbidden in ("import torch", "OnPolicyRunner", "load_runner",
                    "train_once", "runner_cls"):
    assert forbidden not in source, (
        f"{forbidden} is part of what building a runner *means*, and that "
        "belongs to rl-mcp")
  assert '"check"' in source, "the answer comes from `rlmcp check`"


# ── the vocabulary it borrows ─────────────────────────────────────────────

def test_a_gate_that_did_not_run_is_rlmcps_own_shape():
  """The studio writes one such row itself, for an rl-mcp that has no `trains`
  gate, so the shape has to be the one every other skipped gate arrives in --
  otherwise the strip has two ways of spelling "nobody asked" and renders one
  of them as something else."""
  check = pytest.importorskip("rlmcp.check")
  skipped = getattr(check, "_skipped", None)
  if skipped is None:
    pytest.skip("this rl-mcp has no skipped-gate vocabulary to borrow")

  theirs = skipped("trains", "an earlier gate failed")
  ours = source_gates._with_runner_gate([])[0]
  assert theirs["ok"] is None and ours["ok"] is None
  assert set(theirs) <= set(ours), "same fields, so the panel reads them the same way"
  assert ours["detail"].startswith("not run:")


# ── the strip that draws it ───────────────────────────────────────────────

def test_the_gate_strip_draws_the_runner_gate():
  """Six cells, not five: a gate the server answers and the client does not
  draw is invisible in exactly the way this whole feature is about."""
  order = BUILD_JS.split("const GATE_ORDER = ", 1)[1].split("]", 1)[0]
  labels = BUILD_JS.split("const GATE_LABEL = ", 1)[1].split("};", 1)[0]

  assert '"trains"' in order, "the sixth gate is in the order the strip walks"
  assert "trains:" in labels, "and it has a word on it"


def test_the_strip_says_out_loud_when_the_runner_gate_did_not_run():
  """One pale cell at the end of five green ones is read as green. The reading
  is the bug, so the strip writes the qualification underneath rather than
  leaving it to a tick and a tooltip."""
  strip = BUILD_JS.split("function gateStrip(", 1)[1].split("\n}", 1)[0]

  assert "will_train === null" in strip, \
      "the tri-state the server sends, read strictly -- false is a failure, "\
      "null is a question nobody asked"
  assert "does not yet mean it will train" in strip
