"""The three layers of a comparison, each counted for what it actually is.

The bug these pin down is a screen that says "0 of 67 differ" directly above
"reward_scale 3.0 → 8.0". Both halves can be honest -- the weight was equal in
the two *launch* snapshots and was raised by hand mid-run -- and together they
read as the app arguing with itself, because no count said what it had counted.

So: a count states its layer, a layer nobody could read says so instead of
reporting zero, and the one place the layers touch -- a launch field that did
not stay at its launch value -- is said out loud rather than left for the
reader to spot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlmcp.session import Session

from rlmcp_studio.sources import layers
from tests.client_source import read_js

WEB = Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web" / "js"


def code_of(module: str, function: str) -> str:
  """One client function with its comments stripped.

  A comment may quote the very wording the code must avoid, to explain why, so
  a naive substring search would find it there.
  """
  text = (WEB / module).read_text().split(f"function {function}(", 1)[1]
  text = text.split("\nfunction ", 1)[0].split("\n}\n", 1)[0]
  return "\n".join(line for line in text.splitlines()
                    if not line.strip().startswith("//"))


# The knob `session_dir` moves at iteration 20. Putting it in both launch
# snapshots is exactly the shape that produced the contradiction: identical
# before either run started, different by the time either one finished.
MOVED_KEY = "reward.action_rate_l2.weight"
LAUNCH = {"reward": {"action_rate_l2": {"weight": -0.1}, "track_lin_vel": {"weight": 2.0}}}


def _set_config(store, record_id: str, config) -> None:
  store.update_record(record_id, lambda r: setattr(r, "config", config))


def _diff(client, record_id: str = "002"):
  return client.get("/api/diff", params={"record_id": record_id}).json()


# ── the contradiction itself ─────────────────────────────────────────────
def test_a_run_whose_knob_moved_mid_run_is_not_reported_as_launching_unchanged(
    client, store, session_dir):
  """Two identical launch snapshots is a true statement about two snapshots.
  It is not a statement about the runs, and the screen has to carry the
  difference rather than leaving the reader to referee it."""
  _set_config(store, "001", LAUNCH)
  _set_config(store, "002", LAUNCH)

  payload = _diff(client)
  config = payload["config"]

  assert config["changed"] == [] and config["added"] == [] and config["removed"] == []
  assert config["moved_after_launch"] == [MOVED_KEY], \
      "the field both runs launched with, and this run did not keep"


def test_a_knob_that_only_exists_at_runtime_does_not_caveat_the_launch_comparison(
    client, store):
  """The caveat is about the fields this card compared. A parameter the config
  snapshots never covered is a fact about the run, not a hole in the card."""
  other = {"reward": {"track_lin_vel": {"weight": 2.0}}}
  _set_config(store, "001", other)
  _set_config(store, "002", other)

  assert _diff(client)["config"]["moved_after_launch"] == []


def test_a_refused_write_moved_nothing_and_so_caveats_nothing(
    client, store, session_dir):
  """A refusal is worth showing -- it reads as refused in the timeline -- but
  it did not change the value, so it cannot make a launch comparison stale."""
  Session.open(session_dir).append_event("set_parameter", {
      "iteration": 33, "key": "reward.track_lin_vel.weight",
      "old": 2.0, "new": 9.0, "applied": False, "rationale": "out of range",
  })
  _set_config(store, "001", LAUNCH)
  _set_config(store, "002", LAUNCH)

  payload = _diff(client)

  assert payload["config"]["moved_after_launch"] == [MOVED_KEY]
  assert any("refused" in item["what"]
             for item in payload["interventions"]["this"]["items"])


# ── counts that say what they counted ────────────────────────────────────
def test_a_comparison_neither_run_recorded_reports_no_denominator_at_all(client):
  """Neither record here snapshotted a config. "0 of 0 differ" would read as
  agreement; the count has to be absent instead."""
  config = _diff(client)["config"]

  assert config["compared"] == 0
  assert config["fields_here"] == 0 and config["fields_there"] == 0


def test_the_denominator_is_the_fields_the_two_snapshots_cover_between_them(
    client, store):
  """`total` is the *other* run's field count, which would render as "64 of 0
  differ" for a run whose parent published no config at all."""
  _set_config(store, "002", LAUNCH)

  config = _diff(client)["config"]

  assert config["compared"] == 2 and len(config["added"]) == 2
  assert config["fields_here"] == 2, "this run snapshotted both fields"
  assert config["fields_there"] == 0, "and its parent snapshotted neither"


def test_a_note_written_during_a_run_is_not_counted_as_a_change_to_it(
    client, session_dir):
  """rlmcp files a note or a frame annotation under its `said` layer: somebody
  wrote a judgement down. Counted with the edits it would inflate the one
  number a reader trusts -- a run with one weight change and one note would
  report two changes."""
  Session.open(session_dir).append_event(
      "note", {"iteration": 35, "text": "the gait looks steadier"})

  side = _diff(client)["interventions"]["this"]

  assert side["remarks"] == 1
  assert side["changes"] == len(side["items"]) - 1
  assert side["layers"]["said"] == 1


def test_a_run_with_no_event_log_is_not_reported_as_a_run_nobody_touched(client):
  """001 kept no session, so whether anything was done to it is unknown. A
  layer that was not read must not be published as a layer where nothing
  happened."""
  payload = _diff(client)

  assert payload["interventions"]["against"]["ok"] is False
  assert payload["config"]["runtime_known"] is False, \
      "one unreadable side is enough to make the joint claim unknown"


# ── the reconciliation on its own ────────────────────────────────────────
def test_only_a_field_the_comparison_covered_can_qualify_it():
  config = {"changed": [], "added": [], "removed": [], "total": 2,
            "keys": ["a", "b"]}
  mine = {"ok": True, "parameters": ["b", "c"]}
  theirs = {"ok": True, "parameters": ["a"]}

  answer = layers.annotate_config(config, mine, theirs)

  assert answer["moved_after_launch"] == ["a", "b"], "c was never compared"
  assert answer["runtime_known"] is True


def test_there_is_nothing_to_annotate_when_there_is_no_parent():
  assert layers.annotate_config(None, {}, {}) is None


# ── what the screen says ─────────────────────────────────────────────────
#
# Static reads over the client, in the manner of tests/test_client_modules.py:
# there is no JavaScript engine here, and the sentences are the feature.
def test_the_config_count_on_screen_says_that_it_counted_launch_fields():
  card = code_of("views/source.js", "configDiff")

  assert "launch field" in card, "the count names its own scope"
  assert "identical to" not in card, \
      "a launch comparison cannot call two runs identical"
  assert "moved_after_launch" in card, "and it carries the caveat when it is stale"


def test_the_summary_line_never_calls_a_layer_it_could_not_read_empty():
  strip = code_of("views/source.js", "changedStrip")

  assert "launch config not recorded" in strip
  assert "runtime changes not recorded" in strip
  assert "at launch" in strip, "the config count is scoped on the strip too"


def test_the_run_brief_scopes_its_claim_to_the_launch_snapshots():
  brief = code_of("components/record.js", "keyDiff")

  assert "identical to" not in brief
  assert "at launch" in brief and "moved_after_launch" in brief


@pytest.mark.parametrize("module", ["views.js", "views/*.js", "components.js",
                                    "components/*.js"])
def test_no_dead_view_id_is_offered_as_a_way_to_reach_the_comparison(module):
  """`setView` drops an id it does not know, so `setView("diff")` would be a
  button that does nothing at all -- the comparison lives in Code, and there is
  no Diff view."""
  assert 'setView("diff")' not in read_js(module)
