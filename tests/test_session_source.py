"""The live side: the snapshot the charts, the strip and the status bar read."""

from __future__ import annotations

import time

from rlmcp.session import Session

from rlmcp_studio.sources import session as source_session
from rlmcp_studio.sources.session import (LiveSession, events, find_sessions,
                                          parameter_traces)
from tests.conftest import FRAME_NAME


def test_snapshot_carries_status_metrics_series_and_artifacts(session_dir):
  snapshot = LiveSession(session_dir).snapshot()

  assert snapshot["ok"] and snapshot["state"] == "running"
  assert snapshot["task"] == "Fake-Task-G1"
  assert snapshot["status"]["iteration"] == 40
  assert len(snapshot["metrics"]) == 5
  assert [name for name in snapshot["artifacts"]][0]["name"] == FRAME_NAME


def test_series_are_whatever_the_run_published(session_dir):
  """Nothing in the studio names a metric: an extension's own metric charts
  itself without a change here."""
  series = LiveSession(session_dir).snapshot()["series"]

  assert set(series) == {"Train/mean_reward", "Train/mean_episode_length",
                         "rlmcp/terrain_level_mean"}
  assert series["Train/mean_reward"][0] == [0.0, 1.0]
  assert series["Train/mean_reward"][-1] == [40.0, 5.0]


def test_a_missing_session_says_so_rather_than_pretending(tmp_path):
  snapshot = LiveSession(tmp_path / "nowhere").snapshot()
  assert snapshot["ok"] is False and "no session" in snapshot["reason"]


def test_a_session_with_no_status_yet_is_still_a_session(tmp_path):
  """A run that has started but published nothing is not an absent run."""
  directory = tmp_path / "starting" / "rlmcp"
  Session(directory).create({"task": "Fresh"})
  live = LiveSession(directory)

  assert live.exists()
  assert live.snapshot()["ok"] and live.snapshot()["metrics"] == []


def test_liveness_is_rlmcps_verdict_and_follows_the_heartbeat(session_dir):
  """The snapshot's `state` is rl-mcp's own `liveness()`, not a second copy of
  its thresholds: a copy checked a remote run's pid on *this* machine."""
  session = Session.open(session_dir)
  live = LiveSession(session_dir)
  assert live.snapshot()["state"] == "running"

  session.publish_status({"iteration": 41, "updated_at": time.time() - 3600})
  assert live.snapshot()["state"] == "stalled"

  session.publish_status({"iteration": 42, "updated_at": time.time() - 5 * 24 * 3600})
  assert live.snapshot()["state"] == "dead"


def test_a_session_that_cannot_answer_reads_as_dead():
  class Unreachable:
    def liveness(self):
      raise ConnectionError("host gone")
  assert source_session._liveness(Unreachable()) == "dead"


def test_artifacts_are_newest_first_and_typed(session_dir, make_frame):
  make_frame(session_dir / "artifacts" / "shot_env0_it000200.png")
  (session_dir / "artifacts" / "clip_it000200.mp4").write_bytes(b"not really a video")

  artifacts = LiveSession(session_dir).artifacts()
  assert {a["kind"] for a in artifacts} == {"png", "mp4"}
  assert artifacts == sorted(artifacts, key=lambda a: -a["at"])


def test_events_are_read_through_rlmcps_reader(session_dir):
  kinds = [e["kind"] for e in events(session_dir)]
  assert kinds == ["set_parameter", "curriculum_stage"]


def test_parameter_traces_are_step_functions_over_iteration(session_dir):
  traces = parameter_traces(session_dir)

  assert traces["changed"] == 1
  step = traces["traces"][0]
  assert step["key"] == "reward.action_rate_l2.weight"
  assert step["steps"][0] == {"iteration": 20, "old": -0.1, "new": -0.25,
                              "applied": True, "why": "chatter", "by": "agent"}
  assert traces["stages"] == [{"iteration": 30, "name": "rough",
                               "why": "flat mastered"}]
  assert traces["last_iteration"] == 30


def test_a_refused_parameter_write_is_flagged_not_hidden(session_dir):
  Session.open(session_dir).append_event("set_parameter", {
      "iteration": 35, "key": "reward.track_lin_vel.weight",
      "old": 2.0, "new": 99.0, "applied": False, "rationale": "out of bounds",
  })
  traces = parameter_traces(session_dir)
  refused = [s for t in traces["traces"] for s in t["steps"] if not s["applied"]]
  assert len(refused) == 1 and refused[0]["new"] == 99.0


def test_find_sessions_skips_replays(tmp_path, session_dir):
  """A play session is newer than the run it replays. Offering it as the
  latest run is rlmcp's own trap, and rlmcp's own iterator avoids it."""
  from rlmcp.session import PLAY_SESSION_KIND

  Session(tmp_path / "logs" / "replay" / "rlmcp").create(
      {"task": "Fake-Task-G1", "kind": PLAY_SESSION_KIND})

  assert find_sessions(tmp_path / "logs") == [session_dir]


def test_the_wall_clock_is_not_charted_as_a_metric(session_dir):
  """rlmcp stamps `t` on every metrics row. Charting it draws the time of day
  climbing steadily and calls it a measurement the run published."""
  series = LiveSession(session_dir).snapshot()["series"]
  assert "t" not in series and "iteration" not in series


def test_the_where_vocabulary_reaches_the_client_untouched(session_dir):
  """`status.selectors` is what the env picker renders: which criteria this
  run accepts and which values are live. The studio must carry it verbatim --
  a task with a `holding` selector gets a menu entry with no commit here."""
  Session.open(session_dir).publish_status({
      "iteration": 41, "at": time.time(),
      "selectors": {"holding": {"label": "holding the object",
                                "values": [True, False], "extension": "objects"}},
  })

  status = LiveSession(session_dir).snapshot()["status"]
  assert status["selectors"]["holding"]["values"] == [True, False]
  assert status["selectors"]["holding"]["extension"] == "objects"
