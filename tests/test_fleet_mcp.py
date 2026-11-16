"""The fleet server (`agents/fleet_mcp.py`): one HTTP call per tool, the GUI's routes.

The server process itself is the SDK's business; what is tested is the
`Studio` client every tool is a thin wrapper over, driven through the app's
own test client so the agent and the GUI are reading the same routes -- and
the way it is attached beside rlmcp's server, which is what the preflight
tells the agent about.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pytest

from rlmcp_studio.agents import fleet_mcp, preflight
from rlmcp_studio.agents.base import SessionSpec
from rlmcp_studio.agents.host import AgentHost


@pytest.fixture
def studio(client, monkeypatch):
  """A `Studio` whose HTTP goes through the FastAPI test client."""
  s = fleet_mcp.Studio(url="http://studio.test", task="Fake-Task-G1")

  def request(method: str, path: str, body: Optional[Dict[str, Any]] = None,
              params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    answer = (client.get(path, params=params) if method == "GET"
              else client.post(path, params=params, json=body or {}))
    if answer.status_code >= 400:
      raise fleet_mcp.StudioError(str(answer.json().get("error") or answer.text))
    return answer.json()

  monkeypatch.setattr(s, "request", request)
  monkeypatch.setenv("RLMCP_STUDIO_HOST", "h-test")
  return s


def test_runs_is_the_record_tree_trimmed_for_a_model(studio):
  answer = studio.runs()
  assert answer["task"] == "Fake-Task-G1" and answer["host"] == "h-test"
  assert answer["count"] == 2
  ids = [r["id"] for r in answer["runs"]]
  assert ids == sorted(ids), "newest last"
  for run in answer["runs"]:
    assert set(run) <= set(fleet_mcp.SUMMARY_KEYS), "nothing the GUI draws leaks in"
    assert "line" in run and "verdict" in run, "the claim and how it ended"


def test_runs_can_be_narrowed_to_one_parents_children(studio):
  children = studio.runs(parent="001")["runs"]
  assert [r["id"] for r in children] == ["002"]
  assert studio.runs(parent="999")["runs"] == []


def test_capacity_says_what_is_free_and_what_is_training(studio):
  answer = studio.capacity()
  assert answer["host"] == "h-test"
  assert isinstance(answer["slots"], list) and isinstance(answer["free"], int)
  # The fixture's record 002 points at a live session the studio never
  # launched. That is training too: the record heartbeat is the census, not
  # the launcher's job table -- a terminal-launched `--record-run` trainer
  # must not read as a free GPU.
  assert [j["record_id"] for j in answer["training"]] == ["002"]
  assert answer["training"][0]["state"] == "running"


def test_compare_is_counts_and_file_names_not_patches(studio):
  answer = studio.compare("002")
  assert answer["to"] == "002" and answer["from"] == "001", "against defaults to the parent"
  assert "config" in answer
  assert "code" in answer and "files" in answer["code"]
  assert json.dumps(answer).count("\n+") == 0, "no patch text"


def test_a_variant_needs_a_base_that_carries_a_tree(studio):
  with pytest.raises(fleet_mcp.StudioError) as err:
    studio.launch(slug="v1", hypothesis="a tweak", base="001", patch="--- a\n+++ b\n")
  assert "carries no code tree" in str(err.value)
  with pytest.raises(fleet_mcp.StudioError) as err:
    studio.launch(slug="v1", hypothesis="a tweak", patch="--- a\n+++ b\n")
  assert "needs a `base`" in str(err.value)


def test_launch_sends_the_build_views_own_request(monkeypatch):
  s = fleet_mcp.Studio(url="http://studio.test", task="T")
  seen = {}

  def request(method, path, body=None, params=None):
    seen.update(method=method, path=path, body=body)
    return {"ok": True, "record": "009", "slug": "v1",
            "job": {"id": "local-1", "state": "running", "log": ["x"]}, "command": "rlmcp train T"}

  monkeypatch.setattr(s, "request", request)
  answer = s.launch(slug="v1", hypothesis="wider stance helps", base="004",
                    patch="--- a/x\n+++ b/x\n", max_iterations=50, num_envs=512)
  assert seen["method"] == "POST" and seen["path"] == "/api/build/launch"
  body = seen["body"]
  assert body["task"] == "T" and body["variant"] == {"base": "004", "patch": "--- a/x\n+++ b/x\n"}
  assert body["parent"] == "004", "a variant lands under its base"
  assert body["max_iterations"] == 50 and body["num_envs"] == 512
  assert answer["record"] == "009" and answer["variant_of"] == "004"
  assert "log" not in answer["job"], "a launch answer is not a log dump"


def test_the_tool_list_matches_what_the_server_defines():
  names = fleet_mcp.describe()
  assert names == ["studio_capacity", "studio_runs", "studio_launches", "studio_launch",
                   "studio_cancel", "studio_compare"]
  source = open(fleet_mcp.__file__).read()
  for name in names:
    assert f"def {name}(" in source


# How it is attached.


def test_the_fleet_server_sits_beside_rlmcps_and_needs_no_session():
  host = AgentHost(publish=lambda *a, **k: None, studio_url="http://127.0.0.1:8730",
                   host_id="h-1", studio_token="tok")
  both = host._mcp_for("/tmp/session", "/tmp/records", task="G1")
  assert set(both) == {"rlmcp", "studio"}
  fleet = both["studio"]
  assert fleet["args"][-1] == "rlmcp_studio.agents.fleet_mcp"
  assert fleet["env"] == {"RLMCP_STUDIO_URL": "http://127.0.0.1:8730",
                          "RLMCP_STUDIO_TASK": "G1", "RLMCP_STUDIO_HOST": "h-1",
                          "RLMCP_STUDIO_TOKEN": "tok"}, "the token travels in the environment"
  alone = host._mcp_for("", "", task="G1")
  assert set(alone) == {"studio"}, "no session, still a fleet"
  none = AgentHost(publish=lambda *a, **k: None)._mcp_for("", "")
  assert none == {}, "a host with no url attaches nothing"


def test_a_task_conversation_tells_the_server_its_task():
  host = AgentHost(publish=lambda *a, **k: None, studio_url="http://x", host_id="h")
  spec = host._spec("task:Mjlab-Cartpole-Balance", "", "", "")
  assert spec.mcp_servers["studio"]["env"]["RLMCP_STUDIO_TASK"] == "Mjlab-Cartpole-Balance"
  spec = host._spec("007", "", "", "/tmp/s")
  assert spec.mcp_servers["studio"]["env"]["RLMCP_STUDIO_TASK"] == ""


def test_preflight_tells_the_agent_about_the_fleet_with_or_without_a_session():
  with_session = SessionSpec(run_id="007", cwd="", system_context="", mcp_servers={
      "rlmcp": {"command": "python", "args": ["--session", "/tmp/s", "--records-root", "/tmp/r"]},
      "studio": {"command": "python", "args": ["-m", "rlmcp_studio.agents.fleet_mcp"]},
  })
  report = preflight.check(with_session)
  assert report.studio is True
  assert "mcp__studio__" in report.prompt_block() and "studio_launch" in report.prompt_block()

  without = SessionSpec(run_id="task:T", cwd="", system_context="", mcp_servers={
      "studio": {"command": "python", "args": ["-m", "rlmcp_studio.agents.fleet_mcp"]},
  })
  report = preflight.check(without, prospective=True)
  assert report.attached is False and report.studio is True
  block = report.prompt_block()
  assert "no `mcp__rlmcp__` tools" in block, "still honest about the run tools"
  assert "mcp__studio__" in block, "and honest about the fleet"

  bare = preflight.check(SessionSpec(run_id="x", cwd="", system_context="", mcp_servers={}))
  assert bare.studio is False and "mcp__studio__" not in bare.prompt_block()
