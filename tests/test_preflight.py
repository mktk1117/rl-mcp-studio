"""The preflight: what the agent is handed, checked against what it is told.

Every test here is a turn the agent would otherwise lose. The central case: a
conversation opened on run 003, which has a record and a plan and has never
been launched, has no session directory, so `AgentHost._mcp_for` returns `{}`
and the SDK is started with no MCP configuration at all. A prompt that says,
flatly, "The rlmcp MCP server is attached and pinned to the run this
conversation is about", and then lists every tool name, describes tools the
agent does not have. It spends a turn establishing that by inspecting its own
process tree -- finding out something the studio knew before it started.

So the rule these tests hold the code to: **the prompt may only claim what the
spec can be shown to contain.**
"""

from __future__ import annotations

import asyncio
import sys


from rlmcp_studio.agents import preflight
from rlmcp_studio.agents.base import SessionSpec
from rlmcp_studio.agents.host import AgentHost

from test_agents import FakeProvider, collected, spoken


def pinned(session_dir: str, records_root: str = "",
           command: str = "") -> SessionSpec:
  """A spec shaped the way `AgentHost._mcp_for` shapes one."""
  args = ["--session", session_dir]
  if records_root:
    args += ["--records-root", records_root]
  return SessionSpec(run_id="003#main", cwd="",
                     mcp_servers={"rlmcp": {"type": "stdio",
                                            "command": command or sys.executable,
                                            "args": args}})


# ── a run with no session ────────────────────────────────────────────────
def test_a_run_that_was_never_launched_is_told_so_rather_than_handed_a_list():
  """Run 003 exists as a record and a plan; nothing has ever been launched for
  it, so there is no session directory and there is no server. That is a
  perfectly consistent state, and the only thing that can be wrong with it is a
  paragraph insisting otherwise."""
  report = preflight.check(SessionSpec(run_id="003#main"), "rlmcp-server")

  assert report.attached is False
  assert [f.id for f in report.blocking] == ["no-session"]

  text = report.prompt_block()
  assert "no `mcp__rlmcp__` tools" in text
  assert "expected rather than broken" in text, "an absence with a reason"
  assert "never been launched" in text or "never launched" in text
  # The other half of the lesson: an agent told the tools are missing must not
  # go hunting for them, and must not reach for `Bash` instead.
  assert "not deferred" in text
  assert "`Bash`" in text


def test_the_prompt_stops_listing_tools_the_conversation_does_not_have():
  """The list is written from the permission table, so it is always complete
  and always right about *which* tools rlmcp has — and completely wrong when
  the conversation has none of them. A list of names beside an empty tool list
  is a contradiction that costs a turn."""
  from rlmcp_studio.agents.claude import _tool_list

  absent = _tool_list("auto-edit", attached=False)
  assert "mcp__rlmcp__set_parameter" not in absent
  assert "mcp__rlmcp__take_screenshot" not in absent
  assert "None of the" in absent and "`mcp__rlmcp__` tools" in absent

  # And when they are there, the names stay: they are the thing that makes
  # the tools findable behind a tool search.
  assert "mcp__rlmcp__set_parameter" in _tool_list("auto-edit", attached=True)


def test_a_healthy_session_still_gets_the_claim_it_always_got(tmp_path):
  """Honesty about absence must not make the prompt mealy-mouthed in the
  normal case. When
  the studio has checked and the tools really are there, it says so plainly —
  a prompt hedging about its own tools is its own kind of wrong."""
  report = preflight.check(pinned(str(tmp_path), str(tmp_path)))

  assert report.attached is True and report.findings == []
  text = report.prompt_block()
  assert "**is** attached" in text
  assert "mcp__rlmcp__" in text


# ── the ways it is wrong that are not "missing" ──────────────────────────
def test_a_server_that_cannot_be_started_is_the_same_absence(tmp_path):
  """`_rlmcp_server()` falls back to the bare string `rlmcp-server` when it
  finds nothing, and `uv tool install --editable` puts that script beside the
  interpreter and nowhere `$PATH` looks. The SDK's whole report is one line of
  ENOENT, after the conversation has started."""
  report = preflight.check(
      pinned(str(tmp_path), command="rlmcp-server-that-is-not-installed"))

  assert report.attached is False, "a server that cannot start is no tools"
  assert [f.id for f in report.blocking] == ["server-missing"]
  assert "ENOENT" in report.prompt_block()


def test_a_session_directory_that_is_gone_attaches_and_says_so(tmp_path):
  """Distinct from having no session at all, and the difference matters: the
  server does start, the tools do appear, and the calls come back empty. An
  agent that reads an empty answer as "the run is doing nothing" writes that
  into a record."""
  report = preflight.check(pinned(str(tmp_path / "not-there"), str(tmp_path)))

  assert report.attached is True, "the tools are really there"
  assert report.severity == preflight.DEGRADED
  assert [f.id for f in report.findings] == ["session-gone"]
  assert "**is** attached" in report.prompt_block()
  assert "not there" in report.prompt_block()


def test_an_unrooted_server_is_flagged_because_it_wrote_to_the_wrong_store(tmp_path):
  """A record tool takes a bare id. With no `--records-root` the server
  resolves `002` the CLI's way, and on a machine with two stores that is
  somebody else's run: a headline written from the studio would report
  success, never appear on the run it was about, and land in an unrelated
  experiment."""
  report = preflight.check(pinned(str(tmp_path)))

  assert report.attached is True
  assert [f.id for f in report.findings] == ["no-records-root"]


def test_a_working_directory_that_is_not_there_is_worth_saying(tmp_path):
  """The agent is started in the task repository, and everything it was told
  about where files are is relative to that. A cwd that does not exist means
  the SDK lands somewhere else and every relative path in the context above is
  quietly about a different directory."""
  spec = pinned(str(tmp_path), str(tmp_path))
  spec.cwd = str(tmp_path / "no-such-repo")
  report = preflight.check(spec)

  assert report.attached is True
  assert [f.id for f in report.findings] == ["cwd-missing"]
  assert "absolute paths" in report.prompt_block()


def test_a_shell_that_cannot_run_anything_is_said_before_a_turn_is_lost(
    tmp_path, monkeypatch):
  """When `/tmp` is full (a tmpfs with a user quota, say), every `Bash` call
  in every conversation returns exit 1 with no stdout, no stderr and no
  message — because the CLI captures a command's output through a file in
  there and the capture is what fails, not the command.

  An empty exit 1 says nothing, so the agent reads it as being about whatever
  it had asked: the run is not there, the training is not running, `ruff` will not
  start. It is none of those. The studio can see the real reason with one
  write, and the whole point of this module is that it should not cost a turn
  to find out."""
  monkeypatch.setattr(preflight, "_temp_dir_takes_a_write",
                      lambda: "Disk quota exceeded")
  report = preflight.check(pinned(str(tmp_path), str(tmp_path)))

  assert report.attached is True, "the rlmcp tools are unaffected by this"
  assert report.severity == preflight.DEGRADED
  assert [f.id for f in report.findings] == ["tmp-unwritable"]

  text = report.prompt_block()
  assert "Disk quota exceeded" in text, "the machine's own reason, verbatim"
  assert "exit 1" in text
  assert "free space" in text


def test_the_toolless_prompt_says_everything_wrong_and_not_just_the_tools(
    monkeypatch):
  """The two halves arrive together: a machine too full to launch the run is a
  machine too full to run a command about it. Listing the blocking findings
  alone would give a conversation with neither tools nor a shell a careful
  paragraph about the first and nothing at all about the second — which it
  would then work out by hand, in the turn."""
  monkeypatch.setattr(preflight, "_temp_dir_takes_a_write",
                      lambda: "Disk quota exceeded")
  text = preflight.check(SessionSpec(run_id="003#main"),
                         "rlmcp-server").prompt_block()

  assert "no `mcp__rlmcp__` tools" in text, "still the headline"
  assert "Disk quota exceeded" in text, "and the shell, in the same breath"


def test_a_writable_temp_directory_is_worth_no_words_at_all():
  """The normal case, which is every machine that is not out of space: the
  probe writes nine bytes, they land, and nobody is told anything."""
  assert preflight._temp_dir_takes_a_write() == ""


def test_two_things_wrong_are_both_said(tmp_path):
  """Findings are a list rather than a first-hit answer: the one that is
  easiest to detect is not the one most worth knowing."""
  spec = pinned(str(tmp_path / "gone"))
  spec.cwd = str(tmp_path / "also-gone")
  ids = [f.id for f in preflight.check(spec).findings]

  assert ids == ["session-gone", "no-records-root", "cwd-missing"]


# ── the harness, when nobody has checked ─────────────────────────────────
def test_the_harness_claims_nothing_when_there_is_no_spec_to_check():
  """`system_prompt()` is called in places that have no session — the
  fallback, a test, anything previewing the instructions. It has to be honest
  in that case too, and the honest thing is to describe both states rather than
  assert one."""
  from rlmcp_studio.agents.claude import system_prompt

  text = system_prompt()
  assert "mcp__rlmcp__" in text, "the prefix is still what makes them findable"
  assert "If you have no `mcp__rlmcp__` tools at all" in text
  # The sentence this whole module exists to delete.
  assert "server is attached and pinned" not in text


def test_the_placeholder_is_what_the_session_fills(tmp_path):
  """The claim lives in `instructions/30-the-tools.md` as a placeholder, so the
  rest of that file stays markdown anybody can edit after watching the agent
  get something wrong — only the one computed sentence lives in Python."""
  from rlmcp_studio.agents import harness

  body = (harness.HERE / "30-the-tools.md").read_text()
  assert "{{tools_attached}}" in body
  assert "is attached and pinned" not in body, "no flat claim"

  filled = harness.instructions({"tools_attached": "SENTINEL-FOR-THIS-SESSION"})
  assert "SENTINEL-FOR-THIS-SESSION" in filled
  assert "{{tools_attached}}" not in filled


# ── and the studio says it on screen ─────────────────────────────────────
def test_the_studio_says_it_in_the_transcript_before_the_first_answer():
  """The person watching is the one who can fix this — launching the run is a
  button they have and the agent does not. So it is said in the pane, above
  the first question, and not only in a log."""
  published = []

  async def publish(kind, data, scope=""):
    published.append(kind)

  host = AgentHost(publish, provider=FakeProvider())
  asyncio.run(collected(host, "003", "how is it going?", thread="main"))

  entries = host.transcript("003")
  assert entries[0]["kind"] == "preflight", "before anything was said"
  assert entries[0]["attached"] is False
  assert entries[0]["severity"] == "blocking"
  assert [f["id"] for f in entries[0]["findings"]] == ["no-session"]
  assert "agent.preflight" in published, "and it reaches the browser"

  # The conversation itself is unaffected: this is a note, not a refusal.
  assert [e["kind"] for e in spoken(entries)][:2] == ["user", "text"]


def test_a_conversation_with_nothing_wrong_gets_no_preamble(tmp_path):
  """A note that appears every time is a note nobody reads. Findings are the
  only reason to say anything at all."""
  session = tmp_path / "run003" / "rlmcp"
  session.mkdir(parents=True)

  async def publish(kind, data, scope=""):
    pass

  provider = FakeProvider()
  provider.mcp_command = sys.executable        # something that really runs
  host = AgentHost(publish, provider=provider)
  asyncio.run(collected(host, "003", "how is it going?", thread="main",
                        session_dir=str(session), cwd=str(tmp_path),
                        records_root=str(tmp_path)))

  assert "preflight" not in [e["kind"] for e in host.transcript("003")]
  assert host.conversations("003")[0]["tools"] is True


def test_a_run_that_comes_up_later_does_not_reach_the_conversation(tmp_path):
  """The half of the story the first note could not tell. A conversation keeps
  the spec it was started with, so run 003 can be launched, come up and start
  training while the conversation opened on it before the launch still has no
  tools — and the note it was given, which said nothing was running, is by then
  wrong as well. Only the studio holds both halves."""
  session = tmp_path / "run003" / "rlmcp"
  session.mkdir(parents=True)

  async def publish(kind, data, scope=""):
    pass

  provider = FakeProvider()
  provider.mcp_command = sys.executable
  host = AgentHost(publish, provider=provider)

  # Opened before the launch: no session, no tools, and it is told so.
  asyncio.run(collected(host, "003", "what is the plan?", thread="main"))
  assert host.transcript("003")[0]["findings"][0]["id"] == "no-session"

  # ...then somebody presses Launch, and asks this conversation about it.
  asyncio.run(collected(host, "003", "how is it going now?", thread="main",
                        session_dir=str(session), records_root=str(tmp_path)))

  notes = [e for e in host.transcript("003") if e["kind"] == "preflight"]
  assert [f["id"] for f in notes[-1]["findings"]] == ["tools-arrived"]
  assert notes[-1]["attached"] is False, "this conversation still has none"
  assert "Open a new conversation" in notes[-1]["findings"][0]["fix"]

  # Once. A note repeated on every message is a note nobody reads.
  asyncio.run(collected(host, "003", "and now?", thread="main",
                        session_dir=str(session), records_root=str(tmp_path)))
  assert len([e for e in host.transcript("003") if e["kind"] == "preflight"]) == 2

  # And the new conversation really does get them.
  asyncio.run(collected(host, "003", "picking this up", thread="second",
                        session_dir=str(session), records_root=str(tmp_path)))
  assert "preflight" not in [e["kind"] for e in host.transcript("003", "second")]


def test_the_first_note_does_not_promise_tools_that_will_not_arrive():
  """Saying the conversation "picks the server up the next time it is
  started" would be true of the *session* and not of this conversation — the
  same shape of confident-and-wrong the whole module exists to delete."""
  report = preflight.check(SessionSpec(run_id="003#main"), "rlmcp-server")

  assert "the next conversation opened on this run" in report.prompt_block()
  assert "keeps what it was started with" in report.prompt_block()


def test_an_empty_pane_is_told_what_to_do_rather_than_what_it_has_lost():
  """The same finding, read by somebody who has not started a conversation
  yet. Telling them this conversation will never get the tools is nonsense —
  there is no conversation. The studio's pane asks this way, and a live
  conversation asks the other."""
  preview = preflight.check(SessionSpec(run_id="003#main"), "rlmcp-server",
                            prospective=True)

  fix = preview.findings[0].fix
  assert "open a conversation here" in fix
  assert "keeps what it was started with" not in fix


def test_nothing_a_person_reads_is_written_in_markdown(monkeypatch):
  """These strings are read twice: by the agent, in a markdown prompt, and by
  a person, in a pane that renders them as text. Emphasis written for the
  first shows up as literal asterisks in the second — a `*session directory*`
  or a backticked `--session` sitting in the pane."""
  monkeypatch.setattr(preflight, "_temp_dir_takes_a_write",
                      lambda: "Disk quota exceeded")
  spec = pinned("/logs/gone/rlmcp")
  spec.cwd = "/no/such/repo"
  missing = pinned("/logs/gone/rlmcp", command="not-installed-anywhere")
  for finding in (preflight.check(spec).findings
                  + preflight.check(SessionSpec(run_id="003#main")).findings
                  + preflight.check(missing).findings
                  + preflight.arrived().findings):
    for part in (finding.says, finding.because, finding.fix):
      assert "*" not in part, f"{finding.id}: emphasis a person will read raw"
      assert "`" not in part, f"{finding.id}: a backtick a person will read raw"


def test_the_conversation_list_knows_which_ones_have_tools():
  """`None` until it has been started, which is not the same as `False`: a
  conversation nobody has spoken to yet has no tools *and* no fault."""
  async def publish(kind, data, scope=""):
    pass

  host = AgentHost(publish, provider=FakeProvider())
  asyncio.run(collected(host, "003", "hello", thread="main"))

  assert host.conversations("003")[0]["tools"] is False
