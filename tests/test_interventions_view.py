"""The third layer of a diff: what somebody did to the run while it trained."""

from __future__ import annotations

import pytest

from rlmcp_studio.sources import session as source_session

pytest.importorskip("rlmcp.records.interventions",
                    reason="needs rl-mcp with the interventions reader (#29)")


def test_the_decisions_are_read_and_the_bookkeeping_is_not(session_dir):
  """An event log is mostly things that *happened* -- a clip rendered, a job
  finished. The view wants the handful that were *decided*, and it must not
  keep its own list of which those are; rl-mcp owns that vocabulary."""
  from rlmcp.session import Session

  Session.open(session_dir).append_event(
      "progress_clip", {"iteration": 40, "video_path": "x.mp4"})

  answer = source_session.interventions(session_dir)

  kinds = [i["kind"] for i in answer["items"]]
  assert answer["ok"] is True
  assert "set_parameter" in kinds and "curriculum_stage" in kinds
  assert "progress_clip" not in kinds


def test_every_row_carries_the_reason_that_was_given_at_the_time(session_dir):
  """Without the reason this card is a diff of trivia."""
  items = source_session.interventions(session_dir)["items"]
  edit = next(i for i in items if i["kind"] == "set_parameter")

  assert edit["what"] == "reward.action_rate_l2.weight: -0.1 → -0.25"
  assert edit["why"] == "chatter"
  assert edit["iteration"] == 20 and edit["layer"] == "parameter"


def test_the_diff_endpoint_carries_both_runs(client):
  payload = client.get("/api/diff", params={"record_id": "002"}).json()

  mine = payload["interventions"]["this"]
  assert mine["ok"] is True and mine["items"]
  assert [i["iteration"] for i in mine["items"]] == sorted(
      i["iteration"] for i in mine["items"])
  # 001 never ran, so its side is empty rather than missing.
  assert payload["interventions"]["against"]["items"] == []


def test_a_run_with_no_session_says_so_instead_of_failing(client):
  payload = client.get("/api/diff", params={"record_id": "001"}).json()

  side = payload["interventions"]["this"]
  assert side["ok"] is False and side["items"] == []
  assert "session" in side["reason"]
