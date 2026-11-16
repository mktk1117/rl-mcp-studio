"""The Codex provider against a fake App Server that speaks the real wire.

The fake below is a Python script driven over stdin/stdout the way the CLI
is, and its message shapes are those of `codex app-server
generate-json-schema` (0.153): `thread/started`, `turn/started`,
`item/started` + `item/completed`, `thread/tokenUsage/updated`, the `error`
notification, `turn/completed`, and the server requests
`item/commandExecution/requestApproval`, `item/fileChange/requestApproval`,
`mcpServer/elicitation/request`. What the provider answers is written to a
file the test reads back, so the tests are about the bytes on the wire, not
about a method that was monkeypatched away.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from rlmcp_studio.agents import codex as codex_module
from rlmcp_studio.agents.base import AgentEvent, Decision, SessionSpec
from rlmcp_studio.agents.codex import CodexProvider


FAKE = r'''#!/usr/bin/env python3
"""A fake `codex app-server`. Argv is recorded; every client reply is recorded.

Scenario comes from $FAKE_SCENARIO: "plain" (one text answer), "tools" (an
MCP call with an approval elicitation, a command with an approval, a file
change with an approval, token usage, then done), "error" (an `error`
notification then a failed turn), "resume-fails" (thread/resume answers an
error), "hang" (never exits on EOF: only a signal ends it).
"""
import json, os, signal, sys, time

log = open(os.environ["FAKE_LOG"], "a")
def record(kind, payload):
  log.write(json.dumps({"kind": kind, "payload": payload}) + "\n"); log.flush()
def send(obj):
  sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
record("argv", sys.argv[1:])
record("env", {k: v for k, v in os.environ.items() if k.startswith("RLMCP_")})
scenario = os.environ.get("FAKE_SCENARIO", "plain")
thread_id = "01a09785-4bf6-7bc1-b5d1-19798e48b1e5"
next_server_id = 100

def approval(method, params):
  global next_server_id
  next_server_id += 1
  send({"id": next_server_id, "method": method, "params": params})
  # Read until the reply to that id arrives; pass anything else back to main.
  while True:
    line = sys.stdin.readline()
    if not line:
      sys.exit(0)
    m = json.loads(line)
    if m.get("id") == next_server_id and "method" not in m:
      record("reply:" + method, m)
      return m
    handle(m)

def handle(m):
  method = m.get("method")
  if "id" not in m:
    return
  rid = m["id"]
  if method == "initialize":
    send({"id": rid, "result": {"userAgent": "fake"}})
  elif method == "model/list":
    send({"id": rid, "result": {"data": [
        {"id": "gpt-5.4", "displayName": "gpt-5.4", "description": "best", "isDefault": True, "hidden": False},
        {"id": "gpt-hidden", "displayName": "hidden", "hidden": True}]}})
  elif method == "thread/start":
    record("thread/start", m["params"])
    send({"id": rid, "result": {"thread": {"id": thread_id, "cwd": m["params"].get("cwd")}, "model": "gpt-5.4"}})
    send({"method": "thread/started", "params": {"thread": {"id": thread_id}}})
  elif method == "thread/resume":
    record("thread/resume", m["params"])
    if scenario == "resume-fails":
      send({"id": rid, "error": {"code": -32000, "message": "no rollout for that thread"}})
    else:
      send({"id": rid, "result": {"thread": {"id": m["params"]["threadId"], "cwd": m["params"].get("cwd")}, "model": "gpt-5.4"}})
  elif method == "turn/start":
    record("turn/start", m["params"])
    send({"id": rid, "result": {"turn": {"id": "turn-1", "status": "inProgress", "items": []}}})
    send({"method": "turn/started", "params": {"threadId": thread_id, "turn": {"id": "turn-1", "status": "inProgress", "items": []}}})
    run_turn()
  elif method == "turn/interrupt":
    record("turn/interrupt", m["params"])
    send({"id": rid, "result": {}})
  else:
    send({"id": rid, "result": {}})

def run_turn():
  if scenario == "error":
    send({"method": "error", "params": {"threadId": thread_id, "turnId": "turn-1", "willRetry": True,
                                        "error": {"message": "transient, retrying"}}})
    send({"method": "error", "params": {"threadId": thread_id, "turnId": "turn-1", "willRetry": False,
                                        "error": {"message": "401 Unauthorized: token expired"}}})
    send({"method": "turn/completed", "params": {"threadId": thread_id,
          "turn": {"id": "turn-1", "status": "failed", "items": [], "error": {"message": "the turn failed"}}}})
    return
  if scenario == "tools":
    item = {"id": "m1", "type": "mcpToolCall", "server": "rlmcp", "tool": "set_parameter",
            "arguments": {"name": "reward.x.weight", "value": -0.2}, "status": "inProgress"}
    send({"method": "item/started", "params": {"threadId": thread_id, "turnId": "turn-1", "item": item, "startedAtMs": 1}})
    approval("mcpServer/elicitation/request", {
        "threadId": thread_id, "turnId": "turn-1", "serverName": "rlmcp", "mode": "form",
        "message": "Allow the rlmcp MCP server to run tool \"set_parameter\"?",
        "requestedSchema": {"type": "object", "properties": {}},
        "_meta": {"codex_approval_kind": "mcp_tool_call", "persist": ["session", "always"]}})
    done = dict(item, status="completed",
                result={"content": [{"type": "text", "text": "set"}, {"type": "image", "data": "AAAA", "mimeType": "image/png"}]})
    send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": "turn-1", "item": done}})
    cmd = {"id": "c1", "type": "commandExecution", "command": "python train.py", "cwd": "/task",
           "commandActions": [], "status": "inProgress"}
    send({"method": "item/started", "params": {"threadId": thread_id, "turnId": "turn-1", "item": cmd, "startedAtMs": 2}})
    approval("item/commandExecution/requestApproval", {
        "threadId": thread_id, "turnId": "turn-1", "itemId": "c1", "command": "python train.py", "cwd": "/task"})
    send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": "turn-1",
          "item": dict(cmd, status="completed", aggregatedOutput="ok", exitCode=0)}})
    fc = {"id": "f1", "type": "fileChange", "status": "inProgress",
          "changes": [{"path": "/task/env.py", "kind": "update", "diff": "-a\n+b"}]}
    send({"method": "item/started", "params": {"threadId": thread_id, "turnId": "turn-1", "item": fc, "startedAtMs": 3}})
    approval("item/fileChange/requestApproval", {"threadId": thread_id, "turnId": "turn-1", "itemId": "f1"})
    send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": "turn-1",
          "item": {"id": "f1", "type": "fileChange", "status": "completed", "changes": fc["changes"]}}})
    send({"method": "thread/tokenUsage/updated", "params": {"threadId": thread_id, "turnId": "turn-1",
          "tokenUsage": {"last": {"inputTokens": 120, "outputTokens": 30, "totalTokens": 150,
                                  "cachedInputTokens": 0, "reasoningOutputTokens": 0},
                         "total": {"inputTokens": 120, "outputTokens": 30, "totalTokens": 150,
                                   "cachedInputTokens": 0, "reasoningOutputTokens": 0}}}})
  send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": "turn-1",
        "item": {"id": "a1", "type": "agentMessage", "text": "x" * int(os.environ.get("FAKE_TEXT_LEN", "12"))}}})
  send({"method": "turn/completed", "params": {"threadId": thread_id,
        "turn": {"id": "turn-1", "status": "completed", "items": []}}})

if scenario == "hang":
  signal.signal(signal.SIGTERM, lambda *_: sys.exit(3))
for line in sys.stdin:
  handle(json.loads(line))
record("eof", True)
if scenario == "hang":
  time.sleep(30)
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
  """A fake codex on disk, an empty CODEX_HOME with a config that declares
  two global MCP servers, and the log the fake writes."""
  binary = tmp_path / "codex"
  binary.write_text(FAKE)
  binary.chmod(0o755)
  home = tmp_path / "codex-home"
  home.mkdir()
  (home / "config.toml").write_text(
      '[mcp_servers.rlmcp]\ncommand = "/elsewhere/rlmcp-server"\n'
      '[mcp_servers.node_repl]\ncommand = "/usr/bin/node"\n'
      '[projects."/x"]\ntrust_level = "trusted"\n')
  monkeypatch.setenv("CODEX_HOME", str(home))
  log = tmp_path / "fake.log"
  monkeypatch.setenv("FAKE_LOG", str(log))
  monkeypatch.setenv("FAKE_SCENARIO", "plain")
  monkeypatch.delenv("OPENAI_API_KEY", raising=False)
  monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)

  class Fake:
    def __init__(self):
      self.binary, self.home, self.log, self.tmp = binary, home, log, tmp_path

    def records(self, kind=None):
      rows = [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]
      return [r["payload"] for r in rows if kind is None or r["kind"] == kind]

  return Fake()


def _provider(fake, mode="manual", **kw):
  provider = CodexProvider(mode=mode, codex_command=str(fake.binary),
                           mcp_command="/usr/bin/rlmcp-server", **kw)
  provider.auth_state = {"loggedIn": True}
  return provider


def _spec(fake, run="002#main", resume="", free_paths=()):
  return SessionSpec(
      run_id=run, cwd=str(fake.tmp), resume=resume, free_paths=list(free_paths),
      mcp_servers={
          "rlmcp": {"command": "/usr/bin/rlmcp-server", "args": ["--session", "/runs/002/rlmcp"]},
          "studio": {"command": "/usr/bin/python3", "args": ["-m", "fleet"],
                     "env": {"RLMCP_STUDIO_TOKEN": "secret-token", "RLMCP_STUDIO_URL": "http://x"}},
      })


class Collector:
  def __init__(self):
    self.events = []
    self.asked = []
    self.done = asyncio.Event()

  async def on_event(self, _session, event):
    self.events.append(event)
    if event.kind == "done":
      self.done.set()

  def on_permission(self, run, request):
    self.asked.append((run, request))

  def kinds(self):
    return [e.kind for e in self.events]


def _wire(provider):
  c = Collector()
  provider.on_event(c.on_event)
  provider.on_permission(c.on_permission)
  return c


# ── settings ─────────────────────────────────────────────────────────────
def test_the_provider_offers_codex_and_the_shared_studio_choices(fake):
  settings = _provider(fake).settings()
  assert settings["provider"] == "codex"
  assert {row["id"] for row in settings["modes"]} == {"auto-edit", "manual", "plan"}
  assert {row["id"] for row in settings["harnesses"]} == {"studio", "context", "plain"}
  assert settings["models"][0]["id"] == "" and settings["command"] == str(fake.binary)


def test_a_snap_codex_is_named_as_the_reason_it_cannot_be_used(tmp_path, monkeypatch):
  snap = tmp_path / "snap" / "bin" / "codex"
  snap.parent.mkdir(parents=True)
  snap.write_text("#!/bin/sh\n")
  snap.chmod(0o755)
  monkeypatch.setattr(codex_module, "is_snap", lambda command: True)
  provider = CodexProvider(codex_command=str(snap))
  assert "snap" in provider.unavailable_reason() and "confined" in provider.unavailable_reason()


# ── the command line ─────────────────────────────────────────────────────
def test_the_spec_servers_are_added_and_the_users_own_servers_are_switched_off(fake):
  command, env = _provider(fake)._command(_spec(fake))
  assert command[:2] == [str(fake.binary), "app-server"]
  assert 'mcp_servers.rlmcp.command="/usr/bin/rlmcp-server"' in command
  assert 'mcp_servers.rlmcp.args=["--session", "/runs/002/rlmcp"]' in command
  assert "mcp_servers.rlmcp.default_tools_approval_mode=prompt" in command
  assert "mcp_servers.node_repl.enabled=false" in command, "a global server the spec lacks is off"
  assert "mcp_servers.rlmcp.enabled=false" not in command, "the spec's rlmcp replaces the global one"
  assert "mcp_servers.rlmcp.enabled=true" in command
  auto, _ = _provider(fake, mode="auto-edit")._command(_spec(fake))
  assert "mcp_servers.rlmcp.default_tools_approval_mode=approve" in auto


def test_the_studio_token_rides_in_the_environment_never_in_argv(fake):
  command, env = _provider(fake)._command(_spec(fake))
  joined = " ".join(command)
  assert "secret-token" not in joined and "http://x" not in joined
  assert 'mcp_servers.studio.env_vars=["RLMCP_STUDIO_TOKEN", "RLMCP_STUDIO_URL"]' in command
  assert env == {"RLMCP_STUDIO_TOKEN": "secret-token", "RLMCP_STUDIO_URL": "http://x"}


def test_a_server_name_that_is_not_an_identifier_is_refused(fake):
  spec = SessionSpec(run_id="x", mcp_servers={"evil.name": {"command": "/bin/true"}})
  with pytest.raises(codex_module.NotSupported):
    _provider(fake)._command(spec)


def test_configured_servers_reads_the_users_config_without_writing_it(fake):
  before = (fake.home / "config.toml").read_text()
  assert codex_module.configured_servers() == ["node_repl", "rlmcp"]
  assert (fake.home / "config.toml").read_text() == before
  (fake.home / "config.toml").write_text("this is not toml = = =")
  assert codex_module.configured_servers() == []


# ── a conversation over the wire ─────────────────────────────────────────
def test_a_conversation_starts_says_its_session_and_ends_on_eof(fake):
  provider = _provider(fake, mode="plan", harness="plain")
  c = _wire(provider)

  async def go():
    spec = _spec(fake)
    await provider.start(spec)
    await provider.send(spec.run_id, "hello")
    await asyncio.wait_for(c.done.wait(), timeout=5)
    conv = provider._open[spec.run_id]
    await provider.stop(spec.run_id)
    return conv

  conv = asyncio.run(go())
  assert conv.proc.returncode == 0, "EOF on stdin is the shutdown; no signal was needed"
  assert fake.records("eof") == [True]
  session = [e for e in c.events if e.kind == "session"][0]
  assert session.data["provider"] == "codex" and session.data["session_id"].startswith("01a09785")
  assert [e.data["text"] for e in c.events if e.kind == "text"] == ["x" * 12]
  assert c.events[-1].kind == "done" and c.events[-1].data["reason"] == "end_turn"
  assert not [e for e in c.events if e.kind == "error"]
  # The fake's model list replaced the static one, hidden models left out.
  assert [m["id"] for m in provider.models()] == ["", "gpt-5.4"]
  # Every turn carries the mode's policy, so a later switch reaches this thread.
  turn = fake.records("turn/start")[0]
  assert turn["approvalPolicy"] == "never" and turn["sandboxPolicy"] == {"type": "readOnly"}
  start = fake.records("thread/start")[0]
  assert start["sandbox"] == "read-only" and start["approvalPolicy"] == "never"
  assert "developerInstructions" not in start, "the plain harness gives Codex no prompt"
  assert fake.records("env")[0]["RLMCP_STUDIO_TOKEN"] == "secret-token"


def test_a_large_app_server_jsonl_event_is_not_rejected(fake, monkeypatch):
  monkeypatch.setenv("FAKE_TEXT_LEN", "100000")
  provider = _provider(fake, mode="plan", harness="plain")
  c = _wire(provider)

  async def go():
    spec = _spec(fake, run="large")
    await provider.start(spec)
    await provider.send(spec.run_id, "large event")
    await asyncio.wait_for(c.done.wait(), timeout=5)
    await provider.stop(spec.run_id)

  asyncio.run(go())
  assert len("".join(e.data["text"] for e in c.events if e.kind == "text")) == 100_000


def test_manual_mode_answers_every_approval_on_the_wire_in_codexs_own_shape(fake, monkeypatch):
  monkeypatch.setenv("FAKE_SCENARIO", "tools")
  provider = _provider(fake, mode="manual", harness="plain")
  c = _wire(provider)

  async def answer(decision_for):
    seen = 0
    while not c.done.is_set():
      if len(c.asked) > seen:
        run, request = c.asked[seen]
        seen += 1
        await provider.decide(run, request.id, decision_for(request.call.name))
      await asyncio.sleep(0.01)

  def decisions(name):
    return {"mcp__rlmcp__set_parameter": Decision.ALLOW_ALWAYS, "Bash": Decision.ALLOW,
            "Edit": Decision.DENY}[name]

  async def go():
    spec = _spec(fake)
    await provider.start(spec)
    await provider.send(spec.run_id, "tune it")
    await asyncio.wait_for(asyncio.gather(c.done.wait(), answer(decisions)), timeout=8)
    await provider.stop(spec.run_id)

  asyncio.run(go())
  # The MCP call named itself from the started item (the fake's _meta has no
  # tool_name, like a 0.114 CLI), and *allow always* is accept + persist.
  assert [r.call.name for _, r in c.asked] == ["mcp__rlmcp__set_parameter", "Bash", "Edit"]
  assert c.asked[0][1].call.args == {"name": "reward.x.weight", "value": -0.2}
  elicit = fake.records("reply:mcpServer/elicitation/request")[0]["result"]
  assert elicit == {"action": "accept", "_meta": {"persist": "session"}}
  assert fake.records("reply:item/commandExecution/requestApproval")[0]["result"] == {"decision": "accept"}
  assert fake.records("reply:item/fileChange/requestApproval")[0]["result"] == {"decision": "decline"}
  assert c.asked[2][1].call.args["path"] == "/task/env.py", "the edit's paths come from the started item"
  assert provider.granted("002#main") == ["mcp__rlmcp__set_parameter"]
  # Calls are announced when they start and answered when they complete,
  # and a screenshot in a result is named, not dumped.
  kinds = c.kinds()
  assert kinds.index("tool_call") < kinds.index("tool_result")
  results = [e for e in c.events if e.kind == "tool_result"]
  assert results[0].data["content"] == "set\n[image]"
  assert results[2].data["content"].startswith("update /task/env.py")
  done = c.events[-1]
  assert done.data["usage"] == {"used": 150, "input": 120, "output": 30, "cost_usd": None}


def test_a_read_only_rlmcp_call_never_reaches_the_panel_and_plan_declines_the_rest(fake, monkeypatch):
  monkeypatch.setenv("FAKE_SCENARIO", "tools")
  provider = _provider(fake, mode="plan", harness="plain")
  c = _wire(provider)

  async def go():
    spec = _spec(fake)
    await provider.start(spec)
    await provider.send(spec.run_id, "look")
    await asyncio.wait_for(c.done.wait(), timeout=8)
    await provider.stop(spec.run_id)

  asyncio.run(go())
  assert c.asked == []
  assert fake.records("reply:mcpServer/elicitation/request")[0]["result"] == {"action": "decline"}
  assert fake.records("reply:item/commandExecution/requestApproval")[0]["result"] == {"decision": "decline"}


def test_auto_mode_accepts_and_the_mode_switch_reaches_the_next_turn(fake, monkeypatch):
  monkeypatch.setenv("FAKE_SCENARIO", "tools")
  provider = _provider(fake, mode="auto-edit", harness="plain")
  c = _wire(provider)

  async def go():
    spec = _spec(fake, free_paths=[str(fake.tmp / "pkg")])
    await provider.start(spec)
    await provider.send(spec.run_id, "go")
    await asyncio.wait_for(c.done.wait(), timeout=8)
    c.done.clear()
    provider.choose(mode="plan")
    assert await provider.adopt_live() == 1
    await provider.send(spec.run_id, "and now only look")
    await asyncio.wait_for(c.done.wait(), timeout=8)
    await provider.stop(spec.run_id)

  asyncio.run(go())
  assert c.asked == []
  first, second = fake.records("turn/start")
  assert first["approvalPolicy"] == "on-request"
  assert first["sandboxPolicy"]["type"] == "workspaceWrite"
  assert first["sandboxPolicy"]["writableRoots"] == [str(fake.tmp / "pkg")]
  assert second["approvalPolicy"] == "never" and second["sandboxPolicy"] == {"type": "readOnly"}
  replies = fake.records("reply:item/commandExecution/requestApproval")
  assert [r["result"] for r in replies] == [{"decision": "accept"}, {"decision": "decline"}]


def test_an_error_notification_is_surfaced_and_a_retry_is_not(fake, monkeypatch):
  monkeypatch.setenv("FAKE_SCENARIO", "error")
  provider = _provider(fake, mode="plan", harness="plain")
  c = _wire(provider)

  async def go():
    spec = _spec(fake)
    await provider.start(spec)
    await provider.send(spec.run_id, "hello")
    await asyncio.wait_for(c.done.wait(), timeout=5)
    await provider.stop(spec.run_id)

  asyncio.run(go())
  errors = [e.data["message"] for e in c.events if e.kind == "error"]
  assert errors == ["401 Unauthorized: token expired", "the turn failed"]
  assert c.events[-1].data["reason"] == "error"


def test_resume_is_thread_resume_and_a_failed_resume_is_said_then_raised(fake, monkeypatch):
  provider = _provider(fake, mode="plan", harness="plain")
  c = _wire(provider)

  async def go():
    spec = _spec(fake, resume="0f0f0f0f-aaaa-bbbb-cccc-dddddddddddd")
    await provider.start(spec)
    await provider.stop(spec.run_id)

  asyncio.run(go())
  assert fake.records("thread/resume")[0]["threadId"] == "0f0f0f0f-aaaa-bbbb-cccc-dddddddddddd"
  assert fake.records("thread/start") == []
  assert [e.data["session_id"] for e in c.events if e.kind == "session"] == [
      "0f0f0f0f-aaaa-bbbb-cccc-dddddddddddd"]

  monkeypatch.setenv("FAKE_SCENARIO", "resume-fails")
  provider2 = _provider(fake, mode="plan", harness="plain")
  c2 = _wire(provider2)

  async def go2():
    with pytest.raises(codex_module.NotSupported):
      await provider2.start(_spec(fake, run="003#main", resume="0f0f0f0f-aaaa-bbbb-cccc-dddddddddddd"))
    assert "003#main" not in provider2._open

  asyncio.run(go2())
  assert [e.kind for e in c2.events] == ["error"]
  assert "could not resume" in c2.events[0].data["message"]


def test_stop_falls_back_to_a_signal_when_eof_is_ignored(fake, monkeypatch):
  monkeypatch.setenv("FAKE_SCENARIO", "hang")
  monkeypatch.setattr(codex_module, "STOP_TIMEOUT_S", 0.5)
  provider = _provider(fake, mode="plan", harness="plain")
  _wire(provider)

  async def go():
    spec = _spec(fake)
    await provider.start(spec)
    conv = provider._open[spec.run_id]
    await provider.stop(spec.run_id)
    return conv

  conv = asyncio.run(go())
  assert conv.proc.returncode == 3, "SIGTERM ended it after EOF did not"


def test_stop_survives_a_process_that_cannot_be_signalled(fake, monkeypatch):
  """A snap-confined codex answers `terminate()` with EPERM; a stop that
  raised there would leave the process alive."""
  monkeypatch.setenv("FAKE_SCENARIO", "hang")
  monkeypatch.setattr(codex_module, "STOP_TIMEOUT_S", 0.3)
  provider = _provider(fake, mode="plan", harness="plain")
  _wire(provider)

  async def go():
    spec = _spec(fake)
    await provider.start(spec)
    conv = provider._open[spec.run_id]

    def refuse():
      raise PermissionError(13, "Operation not permitted")
    monkeypatch.setattr(conv.proc, "terminate", refuse)
    monkeypatch.setattr(conv.proc, "kill", refuse)
    await provider.stop(spec.run_id)       # must not raise
    assert spec.run_id not in provider._open
    conv.proc.send_signal(9)               # clean up the fake ourselves
    await conv.proc.wait()

  asyncio.run(go())


# ── small pieces ─────────────────────────────────────────────────────────
def test_text_names_images_and_reads_mcp_content_blocks():
  assert codex_module._text({"content": [{"type": "text", "text": "a"},
                                         {"type": "image", "data": "x", "mimeType": "image/png"}]}) == "a\n[image]"
  assert codex_module._text({"message": "boom"}) == "boom"
  assert codex_module._text(None) == ""


def test_usage_is_this_turns_share_of_the_thread():
  assert codex_module._usage({"last": {"totalTokens": 5, "inputTokens": 3, "outputTokens": 2},
                              "total": {"totalTokens": 50}}) == {
      "used": 5, "input": 3, "output": 2, "cost_usd": None}
  assert codex_module._usage({}) is None


def test_the_briefing_tells_codex_how_the_pinned_tools_are_spelled(fake):
  server = fake.tmp / "rlmcp-server"
  server.write_text("#!/bin/sh\n")
  server.chmod(0o755)
  provider = CodexProvider(mode="auto-edit", codex_command=str(fake.binary), mcp_command=str(server))
  spec = SessionSpec(run_id="002#main", cwd=str(fake.tmp), system_context="Run 002.",
                     mcp_servers={"rlmcp": {"command": str(server), "args": ["--session", str(fake.tmp)]}})
  briefing = provider.briefing(spec)
  assert [section["id"] for section in briefing["sections"]][-1] == "runtime"
  assert "mcp__rlmcp__" in briefing["sections"][-1]["text"]
  assert provider._thread_params(spec, "run context")["approvalPolicy"] == "on-request"
  provider.choose(mode="plan")
  plan = provider._thread_params(spec, "run context")
  assert plan["sandbox"] == "read-only" and plan["approvalPolicy"] == "never"


def test_find_codex_prefers_the_named_binary_then_the_desktop_app(tmp_path, monkeypatch):
  named = tmp_path / "mine"
  named.write_text("#!/bin/sh\n")
  named.chmod(0o755)
  monkeypatch.setenv(codex_module.CODEX_ENV, str(named))
  assert codex_module.find_codex() == str(named)
  monkeypatch.delenv(codex_module.CODEX_ENV)
  desktop = tmp_path / "desktop-codex"
  desktop.write_text("#!/bin/sh\n")
  desktop.chmod(0o755)
  monkeypatch.setattr(codex_module, "DESKTOP_CANDIDATES", (str(desktop),))
  assert codex_module.find_codex() == str(desktop)
  monkeypatch.setattr(codex_module, "DESKTOP_CANDIDATES", (str(tmp_path / "absent"),))
  monkeypatch.setattr(codex_module.shutil, "which", lambda name: "/usr/local/bin/codex")
  assert codex_module.find_codex() == "/usr/local/bin/codex"
  assert AgentEvent("x", {}).kind == "x"
  assert os.path.isabs(str(tmp_path))
