"""The Antigravity provider, without the Antigravity SDK.

The SDK is an optional extra and is not in the test environment, which is the
point: everything that can go wrong in the *translation* -- a step drawn
twice, a tool call with no result, a built-in judged by the wrong name, a
verdict that ignores the mode -- is testable against plain objects shaped like
the SDK's, and the provider is written to read attributes rather than types
so that it is.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from typing import Any, List

import pytest

from rlmcp_studio.agents import antigravity
from rlmcp_studio.agents.antigravity import (
    AntigravityProvider, studio_name, translate, usage_of, workspaces_for,
)
from rlmcp_studio.agents.base import Decision, SessionSpec


def _step(**kw) -> NS:
  base = dict(id="t:1", type="TEXT_RESPONSE", status="DONE", source="MODEL",
              target="TARGET_USER", content="", thinking="", tool_calls=[],
              error="", depth=0)
  base.update(kw)
  return NS(**base)


def _call(name, args=None, server=None, step_id="t:1", path=None) -> NS:
  return NS(name=name, args=args or {}, id=None, step_id=step_id,
            server_name=server, canonical_path=path)


# ── names ────────────────────────────────────────────────────────────────
def test_mcp_tools_get_the_name_the_studio_uses_everywhere():
  """The pane strips `mcp__rlmcp__` and the policy table matches on it, so
  an MCP tool has to arrive spelled the way Claude spells one."""
  assert studio_name(_call("take_screenshot", server="rlmcp")) == "mcp__rlmcp__take_screenshot"


@pytest.mark.parametrize("theirs, ours", [
    ("run_command", "Bash"), ("view_file", "Read"), ("edit_file", "Edit"),
    ("create_file", "Write"), ("search_directory", "Grep"),
])
def test_builtins_are_judged_by_effect(theirs, ours):
  assert studio_name(_call(theirs)) == ours


def test_an_enum_name_is_read_as_its_value():
  assert studio_name(_call(NS(value="run_command"))) == "Bash"


def test_a_tool_nobody_mapped_keeps_its_name_and_therefore_asks():
  from rlmcp_studio.agents import policy
  assert studio_name(_call("teleport")) == "teleport"
  assert policy.verdict("teleport") == "ask"


# ── translation ──────────────────────────────────────────────────────────
def test_a_step_is_said_once_when_it_is_done():
  """The harness reports a step on every update from ACTIVE to DONE. A pane
  that drew each one showed a sentence as a stack of partial sentences."""
  seen: set = set()
  active = _step(status="ACTIVE", content="I will look at")
  done = _step(status="DONE", content="I will look at the metrics first.")
  assert translate(active, seen) == []
  out = translate(done, seen)
  assert [(e.kind, e.data["text"]) for e in out] == [
      ("text", "I will look at the metrics first.")]
  assert translate(done, seen) == [], "and not again on a repeated DONE"


def test_the_echo_of_what_you_typed_is_not_drawn_as_the_agent():
  """The harness plays the person's message back as a step from USER. The
  host wrote that message before sending it, so drawn again it read as the
  agent repeating the question -- found on the first live smoke test."""
  seen: set = set()
  echo = _step(id="u", source="USER", content="Say hi in three words.")
  assert translate(echo, seen) == []


def test_thinking_is_thinking_and_planning_at_the_environment_is_too():
  seen: set = set()
  out = translate(_step(id="a", type="THINKING", thinking="hmm"), seen)
  assert [e.kind for e in out] == ["thinking"]
  out = translate(_step(id="b", target="TARGET_ENVIRONMENT", content="plan: read"), seen)
  assert [e.kind for e in out] == ["thinking"], \
      "what the model says to the environment is not a reply to the person"


def test_a_tool_call_is_announced_on_first_sight_with_its_policy():
  seen: set = set()
  call = _call("set_parameter", {"name": "lr", "value": 0.1}, server="rlmcp", step_id="t:7")
  out = translate(_step(id="t:7", type="TOOL_CALL", status="ACTIVE", tool_calls=[call]), seen)
  assert len(out) == 1 and out[0].kind == "tool_call"
  assert out[0].data == {"id": "t:7", "name": "mcp__rlmcp__set_parameter",
                         "args": {"name": "lr", "value": 0.1}, "policy": "ask"}
  again = translate(_step(id="t:7", type="TOOL_CALL", status="DONE", tool_calls=[call]), seen)
  assert [e.kind for e in again] == [], "the DONE update of a tool step is not a second call"


def test_a_failed_step_is_an_error_once():
  seen: set = set()
  bad = _step(id="x", status="ERROR", error="quota exhausted")
  assert [e.kind for e in translate(bad, seen)] == ["error"]
  assert translate(bad, seen) == []


def test_usage_is_carried_in_the_shape_done_expects():
  usage = NS(prompt_token_count=1000, candidates_token_count=200,
             thoughts_token_count=50, total_token_count=1250)
  assert usage_of(usage) == {"used": 1250, "input": 1000, "output": 250, "cost_usd": None}
  assert usage_of(None) is None
  assert usage_of(NS(prompt_token_count=None, candidates_token_count=None,
                     thoughts_token_count=None, total_token_count=None)) is None


# ── the permission handler ───────────────────────────────────────────────
def _provider(mode="manual") -> AntigravityProvider:
  return AntigravityProvider(mode=mode, mcp_command="rlmcp-server", auth="key")


def test_auto_answers_yes_without_asking_anybody():
  provider = _provider("auto-edit")
  asked: List[Any] = []
  provider.on_permission(lambda run, req: asked.append(req))
  handler = provider._make_permission_handler("002#main")
  assert asyncio.run(handler(_call("set_parameter", server="rlmcp"))) is True
  assert asyncio.run(handler(_call("run_command", {"command_line": "rm -rf /"}))) is True
  assert asked == []


def test_manual_asks_the_panel_and_takes_its_answer():
  """The same prompt, whichever provider raised it: the request reaches the
  host's callback in the studio's own shape, and the decision comes back
  through `decide` the way it does for Claude."""
  provider = _provider("manual")
  asked: List[Any] = []
  provider.on_permission(lambda run, req: asked.append((run, req)))
  handler = provider._make_permission_handler("002#main")

  async def go():
    pending = asyncio.ensure_future(handler(_call("set_parameter", {"name": "lr"}, server="rlmcp")))
    for _ in range(50):
      await asyncio.sleep(0.001)
      if asked:
        break
    run, req = asked[0]
    assert run == "002#main"
    assert req.call.name == "mcp__rlmcp__set_parameter"
    await provider.decide("002#main", req.id, Decision.DENY)
    return await pending

  assert asyncio.run(go()) is False


def test_reads_are_free_in_manual_and_finish_is_never_asked_about():
  provider = _provider("manual")
  provider.on_permission(lambda run, req: pytest.fail("asked about a read"))
  handler = provider._make_permission_handler("002#main")
  assert asyncio.run(handler(_call("get_metrics", server="rlmcp"))) is True
  assert asyncio.run(handler(_call("view_file", path="/etc/hosts"))) is True
  assert asyncio.run(handler(_call("finish"))) is True


def test_plan_denies_a_change_without_asking():
  """Plan reads and proposes. A prompt would be the wrong answer -- it is the
  mode you pick to *not* be asked -- and an allow would not be plan."""
  provider = _provider("plan")
  provider.on_permission(lambda run, req: pytest.fail("plan asked"))
  handler = provider._make_permission_handler("002#main")
  assert asyncio.run(handler(_call("edit_file", path="/tmp/x.py"))) is False
  assert asyncio.run(handler(_call("set_parameter", server="rlmcp"))) is False


def test_always_for_this_run_is_remembered_here_too():
  provider = _provider("manual")
  asked: List[Any] = []
  provider.on_permission(lambda run, req: asked.append(req))
  handler = provider._make_permission_handler("002#main")

  async def go():
    first = asyncio.ensure_future(handler(_call("run_command", {"command_line": "ls"})))
    while not asked:
      await asyncio.sleep(0.001)
    await provider.decide("002#main", asked[0].id, Decision.ALLOW_ALWAYS)
    assert await first is True
    return await handler(_call("run_command", {"command_line": "ls -a"}))

  assert asyncio.run(go()) is True
  assert len(asked) == 1, "the second call did not ask"
  assert provider.granted("002") == ["Bash"]


def test_edits_inside_the_task_being_built_are_free(tmp_path):
  provider = _provider("manual")
  provider.on_permission(lambda run, req: pytest.fail("asked inside the scope"))
  handler = provider._make_permission_handler("task:x#main", free_paths=[str(tmp_path)])
  inside = str(tmp_path / "env.py")
  assert asyncio.run(handler(_call("edit_file", {"TargetFile": inside}, path=inside))) is True


# ── availability ─────────────────────────────────────────────────────────
def test_without_the_sdk_the_reason_says_how_to_install_it(monkeypatch):
  monkeypatch.setattr(antigravity, "Agent", None)
  provider = _provider()
  assert "google-antigravity" in provider.unavailable_reason()
  state = asyncio.run(provider.auth_status())
  assert state["loggedIn"] is False and state["answered"] is True


def test_with_the_sdk_but_no_key_the_reason_names_the_variable(monkeypatch):
  monkeypatch.setattr(antigravity, "Agent", object())
  for name in ("GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT",
               "GOOGLE_API_KEY"):
    monkeypatch.delenv(name, raising=False)
  provider = _provider()
  state = asyncio.run(provider.auth_status())
  provider.auth_state = state
  assert state["loggedIn"] is False
  assert "GEMINI_API_KEY" in provider.unavailable_reason()


def test_a_key_in_the_environment_is_enough(monkeypatch):
  monkeypatch.setattr(antigravity, "Agent", object())
  monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
  provider = _provider()
  provider.auth_state = asyncio.run(provider.auth_status())
  assert provider.available()
  assert provider.auth_state["authMethod"] == "api_key"


def test_starting_without_the_sdk_is_a_not_supported_not_a_crash(monkeypatch):
  from rlmcp_studio.agents.base import NotSupported
  monkeypatch.setattr(antigravity, "Agent", None)
  with pytest.raises(NotSupported):
    asyncio.run(_provider().start(SessionSpec(run_id="002#main")))


# ── the prompt and the workspaces ────────────────────────────────────────
def test_the_briefing_says_the_studio_text_is_appended_and_how_tools_are_spelled(tmp_path):
  provider = _provider("auto-edit")
  spec = SessionSpec(run_id="002#main", cwd=str(tmp_path), system_context="Run 002.",
                     mcp_servers={"rlmcp": {"command": "rlmcp-server",
                                            "args": ["--session", str(tmp_path)]}})
  brief = provider.briefing(spec)
  assert brief["preset"] is True, "Antigravity's own instructions stay underneath"
  ids = [s["id"] for s in brief["sections"]]
  assert ids[:3] == ["instructions", "tools", "context"]
  assert ids[-1] == "runtime"
  assert "mcp__rlmcp__" in brief["sections"][-1]["text"]


def test_the_workspaces_reach_the_session_and_the_records(tmp_path):
  """The SDK confines file tools to its workspaces, and a conversation about
  a run has to read outside the task repository."""
  cwd = tmp_path / "task"; cwd.mkdir()
  session = tmp_path / "logs" / "run"; session.mkdir(parents=True)
  records = tmp_path / "records"; records.mkdir()
  spec = SessionSpec(run_id="002#main", cwd=str(cwd),
                     mcp_servers={"rlmcp": {"command": "x", "args": [
                         "--session", str(session), "--records-root", str(records)]}})
  roots = workspaces_for(spec)
  assert roots[0] == str(cwd.resolve())
  assert str(session.resolve()) in roots and str(records.resolve()) in roots
  assert str(tmp_path / "nowhere") not in roots, "only directories that exist"


def test_the_settings_are_the_shape_the_panel_draws():
  provider = _provider()
  settings = provider.settings()
  assert settings["provider"] == "antigravity"
  assert {m["id"] for m in settings["modes"]} == {"auto-edit", "manual", "plan"}
  assert settings["models"][0]["id"] == ""
  with pytest.raises(ValueError):
    provider.choose(model="gpt-9")
  assert provider.choose(mode="bypass")["mode"] == "auto-edit", "the old id still means auto"
