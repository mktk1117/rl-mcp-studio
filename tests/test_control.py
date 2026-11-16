"""Steering: what happens when the studio speaks to a trainer, and when it cannot.

These run against the real file protocol -- a thread in `conftest.Trainer`
pops requests and responds exactly as the controller does -- so a change to
rlmcp's request or response shape fails here rather than in a browser.
"""

from __future__ import annotations

import pytest

from rlmcp.session import Session

from rlmcp_studio.sources import control


def run(coroutine):
  """Drive one coroutine to completion.

  The suite has no asyncio plugin on purpose: two of these tests need a
  *thread* answering while the coroutine waits, which is exactly what
  `asyncio.run` plus a daemon trainer gives, with nothing to configure.
  """
  import asyncio
  return asyncio.run(coroutine)


def test_a_command_reaches_the_trainer_and_the_answer_comes_back(session_dir, trainer):
  answer = run(control.call(session_dir, "screenshot", timeout=5, env_id=3))

  assert answer["ok"] and answer["cmd"] == "screenshot"
  assert answer["env_id"] == 3
  assert trainer.seen == [{"cmd": "screenshot", "args": {"env_id": 3}}]


def test_arguments_that_are_none_are_dropped_rather_than_sent(session_dir, trainer):
  """`where=None` must not become a criterion the extension has to understand."""
  run(control.call(session_dir, "screenshot", timeout=5, env_id=1, where=None))
  assert trainer.seen[0]["args"] == {"env_id": 1}


def test_a_command_the_trainer_refuses_is_reported_not_raised(session_dir, trainer):
  answer = run(control.call(session_dir, "no_such_command", timeout=5))
  assert answer["ok"] is False
  assert "no such command" in answer["error"]


def test_a_dead_trainer_is_refused_up_front_with_what_still_works(dead_session):
  with pytest.raises(control.TrainerGone) as raised:
    run(control.call(dead_session, "screenshot", timeout=1))

  message = str(raised.value)
  assert "dead" in message
  # The refusal has to say what is still possible, or the app looks broken
  # rather than the run looking finished.
  assert "records" in message and "code" in message


def test_read_only_names_every_verb_the_ui_offers():
  """Look, Clip and Diagnose must not need a confirmation; that is the point."""
  assert {"screenshot", "record_video", "diagnose"} <= control.READ_ONLY
  assert "set_parameter" not in control.READ_ONLY
  assert "stop_training" not in control.READ_ONLY


def test_parameters_come_from_the_trainer_when_it_answers(session_dir, trainer):
  answer = run(control.parameters(session_dir))

  assert answer["live"] is True
  # One shape out of this module whichever way the answer came: a map from key
  # to spec. The trainer replies with a list and the snapshot is already a map,
  # and letting both through meant every consumer had to know that.
  assert set(answer["parameters"]) == {"reward.track_lin_vel.weight"}
  assert answer["parameters"]["reward.track_lin_vel.weight"]["value"] == 2.0


def test_parameters_fall_back_to_the_published_snapshot(dead_session):
  """Most runs whose parameters you want to read have already finished."""
  answer = run(control.parameters(dead_session))

  assert answer["live"] is False
  assert answer["source"].endswith("params.json")
  assert sorted(answer["parameters"]) == sorted(
      ["reward.track_lin_vel.weight", "reward.action_rate_l2.weight"])


def test_liveness_is_reported_as_rlmcp_sees_it(session_dir, dead_session):
  assert Session.open(session_dir).liveness_info()["state"] == "dead"


def test_a_where_query_reaches_the_trainer_as_criteria(session_dir, trainer):
  """The vocabulary is the extension's, so it travels as a dict rather than
  as anything the studio parsed."""
  run(control.call(session_dir, "screenshot", timeout=5,
                   where={"terrain": "pyramid_stairs"}))
  assert trainer.seen[0]["args"] == {"where": {"terrain": "pyramid_stairs"}}


def test_a_finished_runs_parameters_arrive_where_the_client_looks(session_dir,
                                                                   dead_session):
  """rlmcp writes `params.json` as a flat map of key -> spec, and splatting it
  into the payload put every parameter at the *top level*, beside `ok` and
  `live`. The client looks for `parameters`/`schema`/`params` and found none of
  them, so every finished run's Parameters tab read "this run published no
  parameter schema" with the whole schema sitting in the response."""
  import asyncio
  import json

  (session_dir / "params.json").write_text(json.dumps({
      "reward.track_lin_vel.weight": {"key": "reward.track_lin_vel.weight",
                                      "current": 2.0, "type": "float"},
      "reward.action_rate_l2.weight": {"key": "reward.action_rate_l2.weight",
                                       "current": -0.25, "type": "float"},
  }))

  payload = asyncio.run(control.parameters(session_dir))

  assert payload["ok"] and payload["live"] is False
  assert "parameters" in payload, "the client looks for this key and no other"
  assert set(payload["parameters"]) == {"reward.track_lin_vel.weight",
                                        "reward.action_rate_l2.weight"}
  assert "reward.track_lin_vel.weight" not in payload, "not at the top level"
