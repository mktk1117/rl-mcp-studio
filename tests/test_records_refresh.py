"""A run that starts while you look at Records appears there without a reload.

The rail reloaded on `records.changed`; the story and the tree drew the
graph `ensureGraph` had cached for the task and never asked again. Read
from the source the way the other client tests are.
"""

from __future__ import annotations

from pathlib import Path
from tests.client_source import read_js

JS = Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web" / "js"


def test_a_record_change_re_reads_the_graph_on_screen():
  main = read_js("app/feed.js", "app/load.js")
  handler = main.split('"records.changed":', 1)[1].split("\n", 2)[0:2]
  assert "refreshGraph" in "".join(handler), "the same event the rail reloads on"
  body = main.split("async function refreshGraph(", 1)[1].split("\n}", 1)[0]
  assert "state.graphTask !== task || !state.graph" in body, "only a graph already on screen"
  assert "api.recordGraph(task)" in body
  assert "if (state.task !== task) return;" in body, "a task switched mid-flight is not painted over"
  assert "set({ graph: payload.nodes || [], graphTask: task })" in body


def test_ensure_graph_still_caches_for_a_view_switch():
  views = read_js("views.js")
  body = views.split("export async function ensureGraph(", 1)[1].split("\n}", 1)[0]
  assert "if (state.graphTask === state.task && state.graph) return state.graph;" in body
