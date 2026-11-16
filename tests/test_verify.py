"""The verification suite: the readings a gate strip does not do.

A task can pass all six gates in fifteen seconds and still be wrong in three
ways nobody is reading: most of its reward terms paying exactly zero across the
roll, nothing ever terminating, and an episode that can run for 5e10 seconds.
Every one of those is in the numbers `rlmcp check` already returns.

So what is tested here is not a measurement — none of these are the studio's
to make — but the *reading*: that two checks put side by side name a dead term
rather than counting it, that a cap of sixteen centuries is not a cap, and
that the suite's verdict is its own worst step rather than its last one.

The frames are covered by their consequences, not by rendering one: a suite
that took no frames must say so, and one whose frames are all identical must
say that too, because a renderer handing back the same picture forever looks
exactly like a preview that works.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path


from rlmcp_studio.sources import verify as source_verify
from tests.client_source import read_js

BUILD_JS = read_js("builder/verify.js")


def _check(rewards=(), terminations=(), env=None, ok=True, **extra) -> dict:
  """One `rlmcp check` answer, in the shape `Gates` files them in."""
  return {
      "at": 0.0, "ok": ok, "error": "",
      "gates": [{"gate": g, "ok": True, "detail": "", "fix": ""}
                for g in ("imports", "constructs", "steps", "rewards_finite",
                          "terminations", "trains")],
      "rewards": [{"term": t, "mean": m} for t, m in rewards],
      "terminations": [{"term": t, "per_step": p} for t, p in terminations],
      "env": env if env is not None else {"num_envs": 4, "step_dt": 0.02,
                                          "actions": 29, "joints": 29,
                                          "max_episode_length_s": 20.0},
      "steps": 120, "num_envs": 4, "device": "cpu", "seconds": 14.6,
      "passed": ok, "will_train": ok, **extra,
  }


def _row(rows: list, term: str) -> dict:
  return next(r for r in rows if r["term"] == term)


# ── reading two checks against each other ─────────────────────────────────

def test_a_term_that_pays_nothing_under_either_policy_is_named_not_counted():
  """A manipulation task's usual shape: a few terms priced, most declared.

  Under a zero policy alone this proves nothing — a shaping term that pays a
  robot doing nothing is *meant* to pay nothing. It is the second column that
  makes it a finding, which is why the suite runs the check twice.
  """
  rows = source_verify.components(
      _check(rewards=[("reach_object", 2.01), ("place_progress", 0.0)]),
      _check(rewards=[("reach_object", 1.44), ("place_progress", 0.0)]))

  assert _row(rows, "reach_object")["state"] == source_verify.PASS
  dead = _row(rows, "place_progress")
  assert dead["state"] == source_verify.WARN
  assert "paid nothing" in dead["says"]
  assert dead["zero"] == 0.0 and dead["random"] == 0.0, \
      "both columns travel, because the claim is about the pair"


def test_a_term_alive_only_under_a_zero_policy_is_not_the_same_finding():
  """It pays a robot standing still and stops the moment it moves. That is a
  different bug from a dead term and reads as one if they share a sentence."""
  rows = source_verify.components(
      _check(rewards=[("stay_upright", 0.8)]),
      _check(rewards=[("stay_upright", 0.0)]))

  found = _row(rows, "stay_upright")
  assert found["state"] == source_verify.WARN
  assert "stops paying" in found["says"]


def test_a_reward_that_is_not_finite_fails_rather_than_warns():
  rows = source_verify.components(
      _check(rewards=[("reach", float("nan"))]),
      _check(rewards=[("reach", 1.0)]))

  assert _row(rows, "reach")["state"] == source_verify.FAIL


def test_a_termination_firing_every_other_step_fails():
  """`terminations: ok` is rl-mcp's answer to "did the gate raise". An episode
  that ends on more than half its steps is a task nothing can learn, and the
  gate strip says nothing about it."""
  rows = source_verify.components(
      _check(terminations=[("fell_over", 0.7)]),
      _check(terminations=[("fell_over", 0.7)]))

  found = _row(rows, "fell_over")
  assert found["state"] == source_verify.FAIL
  assert found["kind"] == "termination"


def test_a_termination_that_never_fires_warns_and_does_not_fail():
  """`time_out` not firing inside 120 steps is the truth about a long episode.
  Worth saying, never worth failing on — the suite does not know the task."""
  rows = source_verify.components(
      _check(terminations=[("time_out", 0.0), ("dropped", 0.0)]),
      _check(terminations=[("time_out", 0.0), ("dropped", 0.0)]))

  assert {r["state"] for r in rows} == {source_verify.WARN}


def test_the_dominant_term_is_flagged_with_rl_mcps_own_ratio():
  rows = source_verify.components(
      _check(rewards=[("reach_object", 2.01), ("lift_object", 0.096)],
             dominance={"term": "reach_object", "over": "lift_object", "ratio": 20.9}),
      _check(rewards=[("reach_object", 1.9), ("lift_object", 0.09)]))

  found = _row(rows, "reach_object")
  assert found["state"] == source_verify.WARN
  assert "21×" in found["says"], "rl-mcp's own ratio, rounded, not recomputed"


# ── the scene ─────────────────────────────────────────────────────────────

def test_an_episode_cap_of_sixteen_centuries_is_not_a_cap():
  """A `max_episode_length_s` of 5e10. No gate looks at it, and it is what an
  unset episode length looks like by the time it reaches the wrapper — every
  episode then ends only if a termination fires, and in a task where none does
  it never ends."""
  found = source_verify.scene(_check(env={"actions": 29, "joints": 29,
                                          "step_dt": 0.02,
                                          "max_episode_length_s": 5e10}))

  assert found["state"] == source_verify.WARN
  assert any("not a cap" in note for note in found["notes"])


def test_an_empty_action_space_fails_the_scene():
  found = source_verify.scene(_check(env={"actions": 0, "joints": 29,
                                          "step_dt": 0.02,
                                          "max_episode_length_s": 20.0}))

  assert found["state"] == source_verify.FAIL
  assert any("action space" in note for note in found["notes"])


def test_a_scene_whose_numbers_are_ordinary_passes_quietly():
  found = source_verify.scene(_check())

  assert found["state"] == source_verify.PASS
  assert found["notes"] == []


# ── the verdict over the steps ────────────────────────────────────────────

def test_the_verdict_is_the_worst_step_and_not_the_last_one():
  """A suite that ends on a passing look, having found a failed gate three
  steps earlier, must not read as verified."""
  assert source_verify._worst(["pass", "fail", "pass"]) == source_verify.FAIL
  assert source_verify._worst(["pass", "warn", "pass"]) == source_verify.WARN
  assert source_verify._worst(["pass", "skip"]) == source_verify.SKIP
  assert source_verify._worst(["pass", "pass"]) == source_verify.PASS


# ── the frames, by their consequences ─────────────────────────────────────

def test_frames_that_never_change_are_reported_rather_than_shown_proudly(tmp_path):
  """A renderer handing back one frame forever looks exactly like a preview
  that works. Compared by bytes deliberately: this is not measuring motion,
  it is catching a stuck picture."""
  one, two = tmp_path / "1.png", tmp_path / "2.png"
  one.write_bytes(b"same")
  two.write_bytes(b"same")

  assert not source_verify._moved([{"policy": "zero", "path": str(one)},
                                   {"policy": "zero", "path": str(two)}])


def test_frames_that_differ_read_as_moving(tmp_path):
  one, two = tmp_path / "1.png", tmp_path / "2.png"
  one.write_bytes(b"aaaa")
  two.write_bytes(b"bbbb")

  assert source_verify._moved([{"policy": "zero", "path": str(one)},
                               {"policy": "zero", "path": str(two)}])


# ── what survives a restart ───────────────────────────────────────────────

def _env():
  class Nothing:
    async def json(self, *command, timeout: float = 0.0):
      return {"ok": False, "error": "no simulator in this suite", "argv": []}
  return Nothing()


def test_a_report_written_by_a_previous_studio_is_read_back(tmp_path):
  """A verdict that vanishes when the app restarts is a verdict nobody trusts
  enough to act on, and this suite takes minutes to produce one."""
  suite = source_verify.Verification(_env(), None, root=tmp_path)
  where = suite.dir_for("Example-Task-v0")
  where.mkdir(parents=True)
  (where / "report.json").write_text(json.dumps(
      {"task": "Example-Task-v0", "at": 1.0, "running": False,
       "verdict": "warn", "steps": [], "shots": []}))

  found = suite.describe("Example-Task-v0")

  assert found["report"]["verdict"] == "warn"
  assert found["running"] is False


def test_a_studio_killed_mid_suite_does_not_come_back_still_running(tmp_path):
  """`running: true` on disk with no job behind it is a spinner that never
  stops. Said as it ended instead."""
  suite = source_verify.Verification(_env(), None, root=tmp_path)
  where = suite.dir_for("T")
  where.mkdir(parents=True)
  (where / "report.json").write_text(json.dumps(
      {"task": "T", "at": 1.0, "running": True, "stage": "random",
       "verdict": "running", "steps": [], "shots": []}))

  report = suite.describe("T")["report"]

  assert report["running"] is False
  assert report["verdict"] == source_verify.FAIL
  assert "stopped while it ran" in report["error"]


def test_a_suite_whose_checks_cannot_run_still_answers_every_step(tmp_path):
  """The environment is missing, the simulator is not installed, the task does
  not import: the panel must still get five rows rather than a blank card, for
  the same reason a gate that did not run is drawn."""
  suite = source_verify.Verification(_env(), None, root=tmp_path)

  found = asyncio.run(suite.run("T", look=False))
  report = found["report"]

  assert [s["id"] for s in report["steps"]] == \
      ["zero", "random", "scene", "components", "look"]
  assert report["verdict"] == source_verify.FAIL
  assert report["steps"][0]["state"] == source_verify.FAIL
  assert report["steps"][2]["state"] == source_verify.SKIP, \
      "nothing was built, so there is no scene to read — skipped, not passed"
  assert report["running"] is False


def test_the_report_is_written_beside_its_frames(tmp_path):
  suite = source_verify.Verification(_env(), None, root=tmp_path)
  asyncio.run(suite.run("Example-Task-v0", look=False))

  written = json.loads(
      (suite.dir_for("Example-Task-v0") / "report.json").read_text())

  assert written["task"] == "Example-Task-v0"
  assert written["running"] is False


# ── the panel ─────────────────────────────────────────────────────────────

def test_the_strip_draws_a_step_nobody_asked_as_neither_passed_nor_failed():
  """The gates' own rule, which this strip has to follow for the same reason:
  five green ticks and one pale one reads as green, and that reading sends a
  task to training with a check that never ran."""
  assert 'const state = step ? step.state : "wait";' in BUILD_JS
  assert '"not asked yet"' in BUILD_JS


def test_the_strip_names_all_five_questions():
  for step in ("zero", "random", "scene", "components", "look"):
    assert f'"{step}"' in BUILD_JS or f"{step}:" in BUILD_JS


def test_a_frame_links_to_the_full_render():
  """A 100 px thumbnail is a prompt to look, not the looking."""
  assert "api.artifactUrl(shot.path)" in BUILD_JS
  assert 'rel: "noopener"' in BUILD_JS


def test_the_verify_route_answers_before_the_suite_finishes():
  """Two environment builds and two held simulators is minutes, and an HTTP
  request that has not returned is a spinner nobody can cancel."""
  app_py = (Path(__file__).resolve().parent.parent / "rlmcp_studio"
            / "routes" / "build.py").read_text()

  assert "verification.start(name" in app_py, "started, not awaited"
  assert "await verification.stop(name)" in app_py, "and it can be stopped"
