"""Closing a run out from the GUI.

Every run launched from the studio stayed at `verdict: running` for ever --
the status bar said so next to `trainer gone` -- because no view had a headline
field or a verdict control, and the Record view told you to open a terminal.

What is checked here is mostly that the studio does *not* decide anything: the
words come from rl-mcp, the judgement comes from `rlmcp record close`, and a
refusal reaches the browser in rl-mcp's own sentence. These are slow-ish tests
by this suite's standards -- each close is a subprocess -- and that is the
price of the close-out being rl-mcp's rather than a copy of it.
"""

from __future__ import annotations

import asyncio

import pytest

from rlmcp_studio.sources import closeout


# ── the vocabulary ───────────────────────────────────────────────────────
def test_the_verdict_words_are_rl_mcps():
  """A GUI list would drift from the store's, and every difference is either a
  verdict the app cannot reach or one that can only be refused."""
  from rlmcp.records import OPEN_VERDICTS, TERMINAL_VERDICTS, VERDICTS

  words = closeout.verdict_vocabulary()
  assert words["all"] == list(VERDICTS)
  assert words["open"] == list(OPEN_VERDICTS)
  assert words["terminal"] == list(TERMINAL_VERDICTS)
  assert "running" not in words["terminal"], "an open state is not a close-out"


def test_the_browser_is_told_the_vocabulary(client):
  """The control is built from this, so it has to arrive with the config
  rather than being typed into the client."""
  words = client.get("/api/config").json()["verdicts"]
  assert "falsified" in words["terminal"]
  assert "validated" in words["result"], \
      "the four that owe an outcome and a measurement are named separately"


# ── the measurements ─────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["no-equals-sign", "=2", "name=", 17, ["one"]])
def test_a_measurement_that_is_not_name_equals_value_is_refused_here(bad):
  """`rlmcp record close` answers a malformed pair with `SystemExit`: no JSON
  at all, which this side could only report as "it did not answer"."""
  with pytest.raises(ValueError):
    closeout.parse_metrics([bad])


def test_a_measurement_may_be_a_pair_or_a_string():
  assert closeout.parse_metrics(["reward=2.3", ["height_m", "0.02"]]) == [
      "reward=2.3", "height_m=0.02"]


def test_the_endpoint_refuses_a_bad_measurement_before_running_anything(client):
  answer = client.post("/api/run/002/close",
                       json={"verdict": "falsified", "metrics": ["oops"]})
  assert answer.status_code == 400
  assert "name=value" in answer.json()["error"]


# ── closing ──────────────────────────────────────────────────────────────
def test_a_running_record_can_be_closed_from_the_gui(client, store):
  answer = client.post("/api/run/002/close", json={
      "verdict": "falsified",
      "headline": "the gate cost more than the chatter did",
      "outcome": "hf share fell to 0.11, and reward fell with it",
      "metrics": ["hf_share=0.11", "Train/mean_reward=1.9"],
  })
  assert answer.status_code == 200, answer.json()
  assert answer.json()["report"], "rl-mcp writes a REPORT.md; the path comes back"

  closed = store.get_record("002")
  assert closed.verdict == "falsified"
  assert closed.headline == "the gate cost more than the chatter did"
  assert closed.outcome.startswith("hf share fell")
  assert ["hf_share", "0.11"] in [list(m) for m in closed.metrics]


def test_the_rail_shows_the_new_verdict_without_being_told_twice(client):
  """The close is a write to the records, so the rail's own read is what
  proves it landed -- not the answer the button got."""
  client.post("/api/run/002/close", json={
      "verdict": "superseded", "outcome": "replaced by 004"})
  rows = {r["id"]: r for r in client.get("/api/runs").json()["runs"]}
  assert rows["002"]["verdict"] == "superseded"


def test_a_refusal_arrives_in_rl_mcps_own_words(client):
  """The refusals are the most useful thing this control produces.

  "'validated' claims a result, and this record has no metrics" teaches the
  rule the store is enforcing; a studio-written "could not close" teaches
  nothing and invites a second guess about what a verdict means.
  """
  answer = client.post("/api/run/002/close", json={"verdict": "validated"})
  assert answer.status_code == 409, "a refusal is a conflict, not a failure"
  error = answer.json()["error"]
  assert "validated" in error and "measurement" in error


def test_an_open_verdict_is_not_a_close_out(client):
  answer = client.post("/api/run/002/close", json={"verdict": "running"})
  assert answer.status_code == 409
  assert "open state" in answer.json()["error"]


def test_closing_one_that_is_already_closed_is_refused_not_duplicated(client, store):
  first = client.post("/api/run/002/close", json={
      "verdict": "falsified", "outcome": "it did not hold",
      "metrics": ["hf_share=0.11"]})
  assert first.status_code == 200
  again = client.post("/api/run/002/close", json={
      "verdict": "best", "outcome": "actually it was great",
      "metrics": ["hf_share=0.99"]})
  assert again.status_code == 409
  assert "already closed" in again.json()["error"]
  assert store.get_record("002").verdict == "falsified"
  assert len(store.get_record("002").metrics) == 1, \
      "a second close-out would duplicate the evidence rows"


def test_closing_a_record_that_does_not_exist_is_a_404(client):
  assert client.post("/api/run/404/close",
                     json={"verdict": "superseded"}).status_code == 404


def test_a_verdict_must_be_said(client):
  assert client.post("/api/run/002/close", json={}).status_code == 400


def test_it_closes_the_record_in_the_store_that_record_came_from(tmp_path, session_dir):
  """Ids are per store: two roots both number from 001. Finding 4 in the
  tutorial was exactly this bug one layer up -- a headline written from the
  studio landed in an unrelated experiment in another records root -- so the
  close-out is rooted at the store the record was *found* in, never at a
  default.
  """
  from fastapi.testclient import TestClient
  from rlmcp.records import open_store

  from rlmcp_studio.app import create_app
  from rlmcp_studio.config import resolve

  mine, theirs = tmp_path / "mine", tmp_path / "theirs"
  open_store(mine).new_record("here", task="Fake-Task-G1", verdict="running",
                              hypothesis="the one being closed")
  open_store(theirs).new_record("elsewhere", task="Other-Task", verdict="running",
                                hypothesis="somebody else's 001")

  app = create_app(resolve(records=[str(mine), str(theirs)]))
  with TestClient(app) as client:
    answer = client.post("/api/run/001/close", json={
        "verdict": "superseded", "outcome": "closed from the studio",
        "task": "Fake-Task-G1"})
    assert answer.status_code == 200, answer.json()

  assert open_store(mine).get_record("001").verdict == "superseded"
  assert open_store(theirs).get_record("001").verdict == "running", \
      "the other store's 001 was not touched"


def test_a_close_out_that_will_not_run_says_so_rather_than_looking_like_a_refusal(
    tmp_path, monkeypatch):
  """No rlmcp on the path, a broken interpreter, a timeout. The answer has to
  be a sentence, not an exception in the log and a spinner on screen."""
  monkeypatch.setattr(closeout.sys, "executable", str(tmp_path / "no-such-python"))
  answer = asyncio.run(closeout.close_record(tmp_path, "001", "superseded"))
  assert answer["ok"] is False and answer["error"]
