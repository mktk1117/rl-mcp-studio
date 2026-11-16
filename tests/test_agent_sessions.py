"""A conversation outlives the process that held it (host.py, external.py).

The studio's agent is Claude Code, and Claude Code keeps every session as a
file it can resume. Without that id, a restart could only read the words back
and start the agent from nothing. These tests are about the id that makes the
difference -- learned from the provider, kept in the
transcript, resumed from after a restart, and taken from a terminal's session
when the person asks.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

from rlmcp_studio.agents import external
from rlmcp_studio.agents.base import AgentEvent, NotSupported, SessionSpec
from rlmcp_studio.agents.host import AgentHost, key
from rlmcp_studio.agents.store import Transcripts
from tests.client_source import read_js


class ClaudeLike:
  """A provider that, like the real one, announces its session id on start
  and can be told to fail a resumed start."""

  name = "claude"
  mcp_command = "rlmcp-server"

  def __init__(self, session_id: str = "abc12345-0000-4000-8000-000000000001"):
    self.auth_state: Dict[str, Any] = {"loggedIn": True}
    self.session_id = session_id
    self.started: List[SessionSpec] = []
    self.stopped: List[str] = []
    self.clients: Dict[str, SessionSpec] = {}
    self.fail_resume = False
    self.events = None

  def unavailable_reason(self) -> str:
    return ""

  def on_permission(self, callback) -> None:
    pass

  def on_event(self, callback) -> None:
    self.events = callback

  async def start(self, spec: SessionSpec) -> str:
    self.started.append(spec)
    self.clients[spec.run_id] = spec
    return spec.run_id

  async def send(self, session_id, text, images=None) -> None:
    if session_id not in self.clients:
      raise NotSupported("no session; call start() first")
    spec = self.clients[session_id]
    if spec.resume and self.fail_resume:
      await self.events(session_id, AgentEvent("error", {"message": "No conversation found with session ID"}))
      await self.events(session_id, AgentEvent("done", {"reason": "error"}))
      return
    # The CLI's init comes first on a fresh start and on a resume alike.
    await self.events(session_id, AgentEvent("session", {
        "provider": "claude", "session_id": spec.resume or self.session_id,
        "cwd": spec.cwd, "model": "m"}))
    await self.events(session_id, AgentEvent("text", {"text": "ok"}))
    await self.events(session_id, AgentEvent("done", {"reason": "end_turn"}))

  async def decide(self, *a, **k) -> None:
    pass

  async def stop(self, session_id) -> None:
    self.stopped.append(session_id)
    self.clients.pop(session_id, None)


async def settled(host, run, thread="main"):
  for _ in range(400):
    await asyncio.sleep(0.005)
    if not host.sessions[key(run, thread)].busy:
      return


async def quiet(kind, data, scope=""):
  pass


def test_the_session_id_is_learned_and_kept_in_the_transcript(tmp_path):
  host = AgentHost(quiet, provider=ClaudeLike(), transcripts=Transcripts(tmp_path))

  async def go():
    await host.send("041", "hello", cwd="/work")
    await settled(host, "041")
  asyncio.run(go())
  session = host.sessions[key("041")]
  assert session.session_id.startswith("abc12345")
  assert session.session_provider == "claude"
  assert host.conversations("041")[0]["session_id"] == session.session_id
  kinds = [e["kind"] for e in host.transcripts.load("041#main")]
  assert "session" in kinds


def test_after_a_restart_the_conversation_is_resumed_not_restarted(tmp_path):
  """The whole point: the next process continues the same Claude Code
  session, and says so instead of saying the memory is gone."""
  first = AgentHost(quiet, provider=ClaudeLike(), transcripts=Transcripts(tmp_path))

  async def before():
    await first.send("041", "hello", cwd="/work")
    await settled(first, "041")
  asyncio.run(before())

  provider = ClaudeLike()
  second = AgentHost(quiet, provider=provider, transcripts=Transcripts(tmp_path))
  assert second.restore() == 1
  restored = second.sessions[key("041")]
  assert restored.restored and restored.session_id.startswith("abc12345")

  async def after():
    await second.send("041", "and now?", cwd="/work")
    await settled(second, "041")
  asyncio.run(after())
  assert provider.started[0].resume == restored.session_id
  notes = [e for e in restored.transcript if e["kind"] == "restart"]
  assert notes and "continues Claude Code session" in notes[-1]["text"]
  assert notes[-1]["resumed"] == restored.session_id


def test_a_resume_that_fails_falls_back_to_a_fresh_start(tmp_path):
  """The session file can be gone. One error, then a new context -- not the
  same error on every message from now on."""
  first = AgentHost(quiet, provider=ClaudeLike(), transcripts=Transcripts(tmp_path))

  async def before():
    await first.send("041", "hello", cwd="/work")
    await settled(first, "041")
  asyncio.run(before())

  provider = ClaudeLike()
  provider.fail_resume = True
  second = AgentHost(quiet, provider=provider, transcripts=Transcripts(tmp_path))
  second.restore()

  async def after():
    await second.send("041", "still there?", cwd="/work")
    await settled(second, "041")
    provider.fail_resume = False
    await second.send("041", "again", cwd="/work")
    await settled(second, "041")
  asyncio.run(after())
  session = second.sessions[key("041")]
  assert provider.started[0].resume and not provider.started[1].resume
  restarts = [e["text"] for e in session.transcript if e["kind"] == "restart"]
  assert any("failed" in t for t in restarts)
  assert [e["text"] for e in session.transcript if e["kind"] == "text"][-1] == "ok"


def test_a_terminal_session_can_be_attached_and_is_resumed_on_the_next_message(tmp_path):
  provider = ClaudeLike()
  host = AgentHost(quiet, provider=provider, transcripts=Transcripts(tmp_path))
  session = host.attach("041", "deadbeef-1111-4222-8333-444455556666", cwd="/work")
  assert session.restored and session.session_id.startswith("deadbeef")
  assert host.conversations("041")[0]["session_id"].startswith("deadbeef")

  async def go():
    await host.send("041", "carry on", cwd="/work")
    await settled(host, "041")
  asyncio.run(go())
  assert provider.started[0].resume == "deadbeef-1111-4222-8333-444455556666"
  restart = [e for e in session.transcript if e["kind"] == "restart"][-1]
  assert "Attached claude session deadbeef" in restart["text"]


def test_attaching_over_an_open_conversation_closes_it_first(tmp_path):
  provider = ClaudeLike()
  host = AgentHost(quiet, provider=provider, transcripts=Transcripts(tmp_path))

  async def go():
    await host.send("041", "hello", cwd="/work")
    await settled(host, "041")
    host.attach("041", "deadbeef-1111-4222-8333-444455556666", cwd="/work")
    await asyncio.sleep(0.02)
    await host.send("041", "carry on", cwd="/work")
    await settled(host, "041")
  asyncio.run(go())
  assert provider.stopped == ["041#main"]
  assert [s.resume for s in provider.started] == ["", "deadbeef-1111-4222-8333-444455556666"]


def test_another_providers_session_is_not_resumed_through_claude(tmp_path):
  """A Codex thread id means nothing to `claude --resume`."""
  first = AgentHost(quiet, provider=ClaudeLike(), transcripts=Transcripts(tmp_path))
  first.transcripts.append("041#main", {"kind": "user", "seq": 0, "run": "041", "thread": "main", "text": "hi"})
  first.transcripts.append("041#main", {"kind": "session", "seq": 1, "run": "041", "thread": "main",
                                        "provider": "codex", "session_id": "thread-1"})
  provider = ClaudeLike()
  host = AgentHost(quiet, provider=provider, transcripts=Transcripts(tmp_path))
  host.restore()

  async def go():
    await host.send("041", "hello", cwd="/work")
    await settled(host, "041")
  asyncio.run(go())
  assert provider.started[0].resume == ""
  restart = [e for e in host.sessions[key("041")].transcript if e["kind"] == "restart"][-1]
  assert "memory of it is not" in restart["text"]


# ── the apps' own files ──────────────────────────────────────────────────

def _claude_file(folder, session_id, text, entrypoint="", mtime=None):
  folder.mkdir(parents=True, exist_ok=True)
  path = folder / f"{session_id}.jsonl"
  lines = [
      {"type": "queue-operation", "sessionId": session_id},
      {"type": "user", "sessionId": session_id, "entrypoint": entrypoint,
       "cwd": "/work/task", "message": {"role": "user", "content": text}},
      {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "sure"}]}},
  ]
  path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
  if mtime:
    os.utime(path, (mtime, mtime))
  return path


def test_claude_code_sessions_are_listed_from_the_directorys_slug(tmp_path, monkeypatch):
  monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
  cwd = tmp_path / "work" / "task_one"
  cwd.mkdir(parents=True)
  slug = external.project_slug(str(cwd))
  assert slug == str(cwd.resolve()).replace("/", "-").replace("_", "-")
  folder = tmp_path / ".claude" / "projects" / slug
  _claude_file(folder, "11111111-aaaa-4bbb-8ccc-000000000001", "older question", "cli", mtime=1_700_000_000)
  _claude_file(folder, "22222222-aaaa-4bbb-8ccc-000000000002",
               "what about training from scratch again?", "sdk-py", mtime=1_700_000_100)
  rows = external.claude_sessions(str(cwd))
  assert [r["id"][:8] for r in rows] == ["22222222", "11111111"], "newest first"
  assert rows[0]["via"] == "studio" and rows[1]["via"] == "terminal"
  assert rows[0]["title"].startswith("what about training")
  assert external.claude_sessions(str(tmp_path / "elsewhere")) == []


def test_codex_sessions_are_matched_by_the_cwd_in_their_header(tmp_path, monkeypatch):
  monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
  day = tmp_path / ".codex" / "sessions" / "2025" / "01" / "15"
  day.mkdir(parents=True)
  for name, cwd, text in [("rollout-2025-01-15T01-00-00-aaa.jsonl", "/work/task", "fix the gait"),
                          ("rollout-2025-01-15T02-00-00-bbb.jsonl", "/other", "unrelated")]:
    lines = [{"type": "session_meta", "payload": {"id": name[-9:-6], "cwd": cwd, "originator": "Codex Desktop"}},
             {"type": "event_msg", "payload": {"type": "user_message", "message": text}}]
    (day / name).write_text("\n".join(json.dumps(l) for l in lines) + "\n")
  monkeypatch.setattr(external.Path, "resolve", lambda self, strict=False: self)
  rows = external.codex_sessions("/work/task")
  assert [r["id"] for r in rows] == ["aaa"]
  assert rows[0]["title"] == "fix the gait" and rows[0]["via"] == "Codex Desktop"


# ── finding the recent one ───────────────────────────────────────────────

def test_every_conversation_row_names_its_task():
  """`071` is a rough G1 run in one records root and a flat Go1 run in another;
  the menu groups by task, so each row has to say which."""
  from rlmcp_studio.app import annotate_tasks
  asked: List[str] = []

  def lookup(run):
    asked.append(run)
    return {"071": "Mjlab-Velocity-Rough-Unitree-G1", "002": "Mjlab-Velocity-Flat-Unitree-Go1"}.get(run, "")

  rows = annotate_tasks([{"run": "071", "thread": "main"}, {"run": "071", "thread": "b"},
                         {"run": "task:Example-Task-v0", "thread": "report"},
                         {"run": "002", "thread": "main"}, {"run": "999", "thread": "main"}], lookup)
  assert [r["task"] for r in rows] == ["Mjlab-Velocity-Rough-Unitree-G1", "Mjlab-Velocity-Rough-Unitree-G1",
                                       "Example-Task-v0", "Mjlab-Velocity-Flat-Unitree-Go1", ""]
  assert asked == ["071", "002", "999"], "one lookup per distinct run; task ids need none"


def test_the_menu_puts_this_task_first_folds_the_others_and_can_be_filtered():
  """Dozens of idle rows from several tasks in one flat list make the recent
  conversation hard to find."""
  js = read_js("agent/conversations.js")
  menu = js.split("function conversations(", 1)[1].split("\n}", 1)[0]
  assert "taskOf(c) === here" in menu and "this task ·" in menu, "the current task's rows come first"
  assert "dayBucket(" in menu and "conv-day" in menu, "under day headings"
  assert "conv-task" in menu and "convTaskOpen" in menu, "other tasks are one folded line each"
  assert "convFilter(" in menu and "state.convFilter" in menu, "and there is a filter box"
  assert 'if (id === "idle") continue;' in menu, "needs-you and working still lead, from every task"
  assert "SECTIONS" in menu and "conv-sec" in menu and "latest" in menu
  bucket = js.split("function dayBucket(", 1)[1].split("\n}", 1)[0]
  for word in ("today", "yesterday", "this week", "earlier"):
    assert f'"{word}"' in bucket


def test_every_helper_the_menu_calls_is_defined():
  """A paint that throws leaves the menu closed with no sign why. The names
  the menu body calls must exist in the module -- if one goes missing in a
  refactor, the whole menu stops opening."""
  import re
  js = read_js("agent/conversations.js")
  menu = js.split("function conversations(", 1)[1].split("\n}", 1)[0]
  defined = set(re.findall(r"^(?:export )?(?:async )?function (\w+)\(", js, re.M))
  imported = set(re.findall(r"import \{([^}]*)\}", js))
  names = set()
  for group in imported:
    names |= {n.strip() for n in group.split(",") if n.strip()}
  for call in ("externalSessions", "conversationRow", "convFilter", "taskOf", "dayBucket",
               "retirementNotice", "actionsOf"):
    assert call in menu, f"{call} is expected in the menu body"
    assert call in defined or call in names, f"{call} is called but not defined"
