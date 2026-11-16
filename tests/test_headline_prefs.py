"""Headline metrics are a choice, per task: which series, how many points.

The server half is one rule in `settings.patch`: `ui.headline` merges per
task and a null clears one, so two tabs on two tasks never overwrite each
other. The client half is read from the source the way the other client
tests do: the ranking honours the choice, the cards carry the control, and
the live refresh asks the session route for exactly the window.
"""

from __future__ import annotations

import json
from pathlib import Path

from rlmcp_studio import settings as settings_file
from tests.client_source import read_js

JS = Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web" / "js"


def test_headline_merges_per_task_and_null_clears_one(tmp_path):
  where = tmp_path / "settings.json"
  settings_file.patch({"ui": {"headline": {"G1": {"keys": ["Train/mean_reward"], "window": 1000}}}}, where)
  settings_file.patch({"ui": {"headline": {"Cartpole": {"window": 500}}}}, where)
  saved = json.loads(where.read_text())["ui"]["headline"]
  assert saved == {"G1": {"keys": ["Train/mean_reward"], "window": 1000}, "Cartpole": {"window": 500}}, \
      "a second task's entry did not replace the first's"
  settings_file.patch({"ui": {"headline": {"G1": None}}}, where)
  saved = json.loads(where.read_text())["ui"]["headline"]
  assert saved == {"Cartpole": {"window": 500}}, "null clears one task and keeps the rest"


def test_other_ui_settings_still_replace_whole(tmp_path):
  where = tmp_path / "settings.json"
  settings_file.patch({"ui": {"panels": {"agent": 300, "rail": 200}}}, where)
  settings_file.patch({"ui": {"panels": {"agent": 340}}}, where)
  assert json.loads(where.read_text())["ui"]["panels"] == {"agent": 340}, \
      "the one-level rule for everything else is unchanged"


def test_the_client_sends_one_tasks_entry_and_the_ranking_honours_it():
  state = (JS / "state.js").read_text()
  assert "export function setHeadline(" in state and "export function headlinePrefs(" in state
  body = state.split("export function setHeadline(", 1)[1].split("\n}", 1)[0]
  assert 'settingChanged("headline", { [task]:' in body, "a delta for this task, not the whole map"
  assert "null" in body, "and null to clear it"
  assert "headline: (v) =>" in state, "mirrored from the settings file at boot"

  components = read_js("components/metrics.js")
  rank = components.split("export function rankMetrics(", 1)[1].split("\n}", 1)[0]
  assert "headlinePrefs(state.task).keys" in rank, "a chosen list wins over the heuristics"
  cards = components.split("export function metricCards(", 1)[1].split("\n}", 1)[0]
  assert "headlineConfig(series, live)" in cards, "the cards carry the control"
  config = components.split("function headlineConfig(", 1)[1].split("\n}", 1)[0]
  assert "WINDOWS.map(" in config and "setHeadline(task, { window:" in config
  assert "setHeadline(task, { keys: null })" in config, "a way back to auto"


def test_the_live_refresh_asks_for_exactly_the_window():
  main = read_js("main.js", "app/*.js")
  body = main.split("async function refreshLive(", 1)[1].split("\n}", 1)[0]
  assert "tailFor(state.task)" in body
  assert "api.session(detail.record.session, tail)" in body
  assert "|${tail}" in body, "a new window with the same beat still repaints"
  assert "api.session(detail.record.session, 240)" not in body


def test_the_windows_are_a_short_honest_list():
  state = (JS / "state.js").read_text()
  line = state.split("export const WINDOWS = ", 1)[1].split(";", 1)[0]
  windows = json.loads(line)
  assert windows[0] == 240, "the first is today's default"
  assert windows == sorted(windows) and windows[-1] <= 5000, "bounded: a longer window is a longer read"
