"""The recipe fold: what a run *is*, counting everything it inherited.

The Diff view already answers "this run against that one"; the
fold answers the question a reader actually opens a record with, and the thing
it must not do is answer it with the run's whole config. Every test here is
about that distinction in one form or another -- a recipe is the short list of
things somebody decided, not the long list of values that resulted.

Records and sessions come from rl-mcp's own writers, as the rest of the suite
does, so these fail when the library changes shape rather than when a fixture
somebody froze goes stale.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rlmcp.records import open_store, snapshot

from rlmcp_studio.sources import recipe


def _config(store, record_id: str, values: dict) -> None:
  store.update_record(record_id, lambda r: setattr(r, "config", values))


def fold(store, record_id: str) -> dict:
  return recipe.fold(store.list_records(), record_id)


# ── what a recipe says ───────────────────────────────────────────────────

def test_a_recipe_carries_the_change_that_made_the_run_not_the_whole_config(store):
  """002's recipe is one weight, not the sixty-odd fields it also happens to
  hold. Folding a config against a root that recorded none is how a recipe
  turns into a wall of current values, which is exactly what a recipe must not
  be."""
  common = {"reward": {"track_lin_vel": {"weight": 2.0},
                       "action_rate_l2": {"weight": -0.1}},
            "env": {"num_envs": 4096}}
  _config(store, "001", common)
  _config(store, "002", {**common,
                         "reward": {**common["reward"],
                                    "action_rate_l2": {"weight": -0.25}}})

  answer = fold(store, "002")

  assert answer["ok"] is True
  moved = answer["config"]["moved"]
  assert [row["short"] for row in moved] == ["action_rate_l2.weight"]
  assert (moved[0]["key"], moved[0]["old"], moved[0]["new"]) == (
      "reward.action_rate_l2.weight", -0.1, -0.25)
  assert moved[0]["by"] == "002", "the recipe says which rung moved it"
  # The other three fields exist in both runs and nobody touched them, so they
  # are not part of the recipe at any level.
  assert answer["config"]["keys"] == 1
  assert "action_rate_l2" in answer["line"] and "num_envs" not in answer["line"]


def test_a_field_that_only_appeared_is_not_reported_as_a_decision(store):
  """The first run to record a config introduces every field it has at once.
  Sixty-one appearances are not sixty-one decisions, and counting them as such
  is how the fold becomes unreadable on any real ancestry."""
  _config(store, "002", {"reward": {"smoothness": {"weight": 3.0}},
                         "env": {"num_envs": 4096}})

  answer = fold(store, "002")

  assert {row["key"] for row in answer["config"]["moved"]} == {
      # only the knob somebody actually turned, mid-run, in 002's own session
      "reward.action_rate_l2.weight"}
  assert answer["config"]["introduced_total"] == 2
  assert {row["key"] for row in answer["config"]["introduced"]} == {
      "reward.smoothness.weight", "env.num_envs"}


def test_a_knob_turned_mid_run_folds_in_beside_the_config_edits(store):
  """The runtime layer -- after the launch config and the code -- and the one
  that most often explains a result.
  002's session raised `action_rate_l2` at iteration 20 with a reason typed at
  the time; a recipe that only reads config would never mention it."""
  answer = fold(store, "002")

  edits = answer["steps"][-1]["runtime"]["edits"]
  assert [e["short"] for e in edits] == ["action_rate_l2.weight"]
  assert (edits[0]["old"], edits[0]["new"], edits[0]["at"]) == (-0.1, -0.25, 20)
  assert edits[0]["why"] == "chatter", "the reason given at the time survives"
  assert "at 20" in answer["line"]

  moved = answer["config"]["moved"]
  assert moved[0]["layer"] == "runtime", "and the fold says which layer moved it"


def test_a_knob_moved_twice_counts_as_one_move_from_its_first_value_to_its_last(
    store, session_dir):
  """A parameter nudged three times was moved once. Transcribing every nudge is
  the wall of values again, one layer down."""
  from rlmcp.session import Session

  session = Session.open(session_dir)
  session.append_event("set_parameter", {
      "iteration": 60, "key": "reward.action_rate_l2.weight",
      "old": -0.25, "new": -0.4, "applied": True, "rationale": "still chattering",
  })

  edits = fold(store, "002")["steps"][-1]["runtime"]["edits"]

  assert len(edits) == 1
  assert (edits[0]["old"], edits[0]["new"]) == (-0.1, -0.4)
  assert edits[0]["moves"] == 2, "both moves are counted, only one is reported"


def test_a_knob_put_back_where_it_started_is_not_part_of_the_recipe(
    store, session_dir):
  """Reproducing the run does not require turning it and turning it back."""
  from rlmcp.session import Session

  Session.open(session_dir).append_event("set_parameter", {
      "iteration": 60, "key": "reward.action_rate_l2.weight",
      "old": -0.25, "new": -0.1, "applied": True, "rationale": "no better",
  })

  answer = fold(store, "002")

  assert answer["config"]["moved"] == []
  assert answer["steps"][-1]["runtime"]["edits"] == []


def test_the_fold_follows_the_ancestry_even_when_it_crosses_tasks(store):
  """A run's config parent is routinely a run of another task -- a variant task
  that starts from a run of the task it was derived from. Filtering by task
  first, the way the record graph does, cuts the chain at the last rename and
  calls the result a root."""
  store.new_record("variant_task", task="Third-Task-G1", parent="002",
                   hypothesis="the same recipe on a variant of the task")

  answer = fold(store, "004")

  assert [step["id"] for step in answer["steps"]] == ["001", "002", "004"]
  assert answer["root"] == "001" and answer["depth"] == 3


def test_a_record_that_is_not_in_this_store_is_refused_rather_than_folded(store):
  answer = fold(store, "999")
  assert answer["ok"] is False and "999" in answer["reason"]


def test_a_cycle_in_the_ancestry_is_reported_rather_than_walked_forever(store):
  """rl-mcp's own walk raises on a cycle. A view that let that escape would be
  a 500 on a record somebody can still repair."""
  store.update_record("001", lambda r: setattr(r, "parent", "002"))

  answer = fold(store, "002")

  assert answer["ok"] is False and "ycle" in answer["reason"]


# ── the code half ────────────────────────────────────────────────────────

@pytest.fixture
def task_repo(tmp_path) -> Path:
  """A task package under git, one commit deep."""
  root = tmp_path / "tasks"
  (root / "my_tasks").mkdir(parents=True)
  (root / "my_tasks" / "actions.py").write_text("filter = None\n")

  def git(*args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

  subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
  git("config", "user.email", "t@example.com")
  git("config", "user.name", "T")
  git("add", "-A")
  git("commit", "-qm", "the task")
  return root


def _stamp(store, record_id: str, repo: Path):
  code = snapshot.capture(repo / "my_tasks", record_id=record_id)
  store.update_record(record_id, lambda r: setattr(r, "code", code))


def test_the_code_half_folds_against_the_oldest_run_that_was_actually_stamped(
    store, task_repo):
  """Older rl-mcp releases did not stamp the package at launch, so the root of
  a long chain usually carries no snapshot. Comparing against it anyway would make the code
  half of every recipe read "no snapshot for 001" -- true, and useless. The
  honest base is the oldest run that can be compared, named in the payload."""
  _stamp(store, "002", task_repo)
  (task_repo / "my_tasks" / "actions.py").write_text("filter = 'ema'\n")
  store.new_record("ema_filter", task="Fake-Task-G1", parent="002",
                   hypothesis="an EMA filter on the actions")
  _stamp(store, "004", task_repo)

  code = fold(store, "004")["code"]

  assert code["ok"] is True
  assert code["from"] == "002", "001 has no stamp, so the fold starts at 002"
  assert [f["path"] for f in code["files"]] == ["my_tasks/actions.py"]
  assert code["added"] == 1 and code["removed"] == 1


def test_a_chain_with_no_stamps_at_all_says_so_instead_of_looking_empty(store):
  """An empty file list and a missing snapshot look identical on screen, and
  one of them means "this run changed no code"."""
  code = fold(store, "002")["code"]
  assert code["ok"] is False and "snapshot" in code["reason"]


# ── between phases ───────────────────────────────────────────────────────

def test_the_warm_start_chain_is_a_different_edge_from_the_ancestry(store):
  """A run takes its config from its predecessor and its weights from six runs
  back, routinely. Reproducing it means running the *weights* chain in order,
  which is why the phases are built from `weights.run` and not from `parent`."""
  from rlmcp.records.record import Weights

  store.new_record("finetune", task="Fake-Task-G1", parent="002",
                   weights=Weights(run="001", checkpoint="model_500.pt"),
                   hypothesis="warm start from the baseline")

  phases = fold(store, "004")["phases"]

  assert [p["run"] for p in phases] == ["001", "004"], "oldest segment first"
  assert phases[0]["scratch"] is True
  assert phases[1]["from"] == "001" and phases[1]["checkpoint"] == "model_500.pt"


def test_a_run_whose_weights_came_from_outside_this_store_says_so(store):
  """The chain stops there, and it has to look stopped rather than finished."""
  from rlmcp.records.record import Weights

  store.new_record("imported", task="Fake-Task-G1", parent="002",
                   weights=Weights(run="from-another-machine", checkpoint="x.pt"),
                   hypothesis="weights we were handed")

  phases = fold(store, "004")["phases"]

  assert phases[0]["run"] == "004" and phases[0]["dangling"] is True


def test_a_from_scratch_run_on_the_same_config_is_offered_as_a_shorter_path(store):
  """The first payoff of a recipe: the cheapest path is often not the
  historical one, and the evidence is already in the records as
  scratch-validation runs. It is an estimate and is labelled one -- the cost
  was paid from somewhere else."""
  from rlmcp.records.record import Weights

  config = {"reward": {"smoothness": {"weight": 8.0}}}
  _config(store, "002", config)          # 002 warm-started from 001
  store.update_record("002", lambda r: setattr(
      r, "weights", Weights(run="001", checkpoint="model_100.pt")))
  store.new_record("scratch_check", task="Fake-Task-G1", verdict="validated",
                   hypothesis="does this config create the behaviour from nothing")
  _config(store, "004", config)
  store.new_record("built_on_it", task="Fake-Task-G1", parent="002",
                   weights=Weights(run="002", checkpoint="model_900.pt"),
                   hypothesis="carry on from 002")

  cuts = fold(store, "005")["shortcuts"]

  assert [c["run"] for c in cuts] == ["004"]
  assert cuts[0]["instead_of"] == "002"
  assert cuts[0]["skips"] == ["001", "002"], "everything up to the state it reached"


# ── within a phase: the ladder ───────────────────────────────────────────

def test_the_ladder_is_loadable_by_rl_mcps_own_stage_schedule(store):
  """A recipe has to be something that *runs*, not a document about the run.
  So the ladder is built out of rl-mcp's `CurriculumStage` and serialised by
  `StageSchedule`, and the proof is that the library reads it back."""
  from rlmcp.core.curriculum import StageSchedule

  ladder = fold(store, "002")["curriculum"]
  assert ladder["ok"] is True

  restored = StageSchedule.from_dict(ladder["schedule"])

  # Two kinds of moment are rungs: a knob turned at 20, and the curriculum
  # stage the run entered at 30. A ladder that heard only the first would leave
  # the run's own ladder out of its recipe.
  assert [s.name for s in restored.stages] == ["launch", "at 20", "rough"]
  assert restored.stages[0].parameters == {"reward.action_rate_l2.weight": -0.1}
  assert restored.stages[1].parameters == {"reward.action_rate_l2.weight": -0.25}
  assert restored.stages[1].notes == "chatter", "the reason rides on the rung"


def test_a_ladder_with_no_recorded_trigger_does_not_claim_it_will_climb_itself(
    store):
  """Nothing in an event log records a metric trigger, so turning an
  intervention into a promotion condition would have nothing to read. A schedule that promised to promote on conditions it does not have
  would be a lie the data cannot support."""
  ladder = fold(store, "002")["curriculum"]

  assert ladder["schedule"]["auto_promote"] is False
  assert all(not stage["promote_when"] for stage in ladder["schedule"]["stages"])
  assert "metric trigger" in ladder["note"]
  # The rungs are ordered by when each happened instead, which is the honest
  # substitute and the only thing the events do say.
  assert [s["min_iterations"] for s in ladder["schedule"]["stages"]] == [0, 20, 10]


def test_a_run_nobody_touched_says_its_recipe_is_its_launch_config(store):
  """The empty case has to read as an answer, not as a panel that failed."""
  ladder = fold(store, "001")["curriculum"]
  assert ladder["ok"] is False and "launch config" in ladder["reason"]


# ── the route ────────────────────────────────────────────────────────────

def test_the_route_folds_a_run_the_client_can_name(client):
  answer = client.get("/api/recipe", params={"record_id": "002",
                                             "task": "Fake-Task-G1"}).json()

  assert answer["ok"] is True and answer["record"] == "002"
  assert [step["id"] for step in answer["steps"]] == ["001", "002"]


def test_the_route_refuses_a_record_that_does_not_exist(client):
  reply = client.get("/api/recipe", params={"record_id": "404"})
  assert reply.status_code == 404


def test_the_task_disambiguates_the_id_but_does_not_narrow_the_ancestry(
    tmp_path, records_root):
  """Two records roots number their runs from 001, so an id alone is ambiguous
  and the task is what resolves it. That is a different job from filtering the
  fold, and conflating them would truncate every chain that crosses a task."""
  from fastapi.testclient import TestClient

  from rlmcp_studio.app import create_app
  from rlmcp_studio.config import resolve

  second = open_store(tmp_path / "other-records")
  second.new_record("elsewhere", task="Second-Task-G1", hypothesis="another store")

  app = create_app(resolve(records=[str(records_root), str(tmp_path / "other-records")]))
  with TestClient(app) as running:
    mine = running.get("/api/recipe", params={"record_id": "001",
                                              "task": "Second-Task-G1"}).json()

  assert mine["record"] == "001" and mine["slug"] == "elsewhere"
  assert mine["depth"] == 1
