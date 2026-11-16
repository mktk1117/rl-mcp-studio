"""The agent host: the permission table, the conversations, and images.

No model is called here and none needs to be. What can go wrong in this layer
is a tool being allowed that should have asked, a transcript that a reconnecting
client cannot be caught up from, and an image message the CLI will not read --
all three are testable against a fake provider.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List

import pytest

from rlmcp_studio.agents import auth as cli_auth
from rlmcp_studio.agents import policy
from rlmcp_studio.agents.base import (AgentEvent, Decision, NotSupported,
                                      SessionSpec)
from rlmcp_studio.agents.host import AgentHost, build_context, key


# ── the permission table ─────────────────────────────────────────────────
@pytest.mark.parametrize("tool", [
    "mcp__rlmcp__take_screenshot", "mcp__rlmcp__get_metrics",
    "mcp__rlmcp__diagnose_motion", "mcp__rlmcp__record_video",
    "mcp__rlmcp__attach_feedback", "mcp__rlmcp__add_note",
    "Read", "Grep", "Glob",
])
def test_looking_and_writing_things_down_are_free(tool):
  """An agent that must ask before measuring is one nobody leaves running --
  and gating "write down what you saw" simply loses the observation."""
  assert policy.verdict(tool) == "allow"


@pytest.mark.parametrize("tool", [
    "mcp__rlmcp__set_parameter", "mcp__rlmcp__stop_training",
    "mcp__rlmcp__rollback_to_checkpoint", "mcp__rlmcp__curriculum_advance",
    "Edit", "Write", "Bash",
])
def test_changing_the_run_or_the_files_asks(tool):
  assert policy.verdict(tool) == "ask"


def test_a_tool_nobody_has_heard_of_asks():
  assert policy.verdict("mcp__rlmcp__launch_the_missiles") == "ask"
  assert policy.verdict("SomeNewBuiltin") == "ask"


# ── the mode moves the line ──────────────────────────────────────────────
#
# The studio hands the SDK a `can_use_tool` handler in every mode, so the
# handler is asked about every tool whatever the mode says. A handler that
# ignored the mode would make "Auto" behave exactly like "Manual": asking for
# permission for every command, in auto mode.


@pytest.mark.parametrize("tool,args", [
    ("Edit", {"file_path": "/anywhere.py"}),
    ("Bash", {"command": "ls -la /work/x"}),
    ("Bash", {"command": "python3 train.py"}),
    ("Bash", {"command": "rm -rf /tmp/x"}),
    ("Write", {"file_path": "/etc/hosts"}),
    # The half that is the point of this mode: the agent restarts runs and
    # moves parameters on its own judgement, and the person interrupts. See `instructions/30-the-tools.md`.
    ("mcp__rlmcp__set_parameter", {}),
    ("mcp__rlmcp__curriculum_goto", {}),
    ("mcp__rlmcp__stop_training", {}),
    ("mcp__rlmcp__rollback_to_checkpoint", {}),
])
def test_auto_frees_everything(tool, args):
  """Auto means what it says. A mode that still stopped for any command it
  could not prove was a read would describe a shape of work nobody does:
  approving `uv run` for the fourth time is not oversight."""
  assert policy.verdict(tool, args, mode="auto-edit") == "allow"


def test_the_old_no_prompts_id_still_means_auto():
  """`bypass` is the id older settings files use for what Auto now covers,
  and an id that stopped resolving would drop that studio back into `manual`
  -- the loudest possible way to lose a preference."""
  from rlmcp_studio.agents.claude import resolve_mode

  for tool, args in [("Bash", {"command": "rm -rf /"}),
                     ("mcp__rlmcp__stop_training", {})]:
    assert policy.verdict(tool, args, mode="bypass") == "allow"
  assert resolve_mode("bypass") == "auto-edit"
  assert resolve_mode("nonsense") == "manual"


def test_manual_keeps_its_prompt_for_every_command():
  """The conservative mode is the default and does not move: a person who
  picks "asks before anything" is asking to see the `ls` too."""
  assert policy.verdict("Bash", {"command": "ls -la"}) == "ask"
  assert policy.verdict("Bash", {"command": "ls -la"}, mode="manual") == "ask"
  assert policy.verdict("Edit", {"file_path": "/x.py"}, mode="manual") == "ask"


@pytest.mark.parametrize("command", [
    # Two easy misreadings: a `|` inside quotes is text, and the second `&` of
    # an `&&` is not a backgrounding `&`.
    'grep -rn "list_sessions\\|server_root\\|SERVER_ROOT" /work/x',
    'ls -la /work/x/rl-mcp && echo "---DOCS---"',
    "ls -R /work/x/rlmcp | head -100",
    "wc -l /work/x/session.py /work/x/other.py",
    "sed -n 1,50p file.py",
    "find . -name '*.py'",
    "git diff --stat",
    "nvidia-smi",
])
def test_a_command_that_only_reads_is_recognised(command):
  assert policy.reads_only(command) is True


@pytest.mark.parametrize("command", [
    "cat a.txt > b.txt",              # the effect is in the redirection
    "cat a >> b",
    "echo $(rm -rf /)",               # and here it is in the substitution
    "echo `rm -rf /`",
    "ls; rm -rf /",                   # ...and here in the second command
    "ls & rm -rf /",
    "ls && rm -rf /tmp/x",            # every link is judged, not just the first
    "sudo ls",                        # it runs something else
    "bash -c 'rm -rf /'",
    "xargs rm",
    "FOO=1 ls",                       # the program is the next word
    "python3 train.py",               # not on the list, so not proven
    "find . -name '*.py' -delete",    # a reading program in a writing mood
    "sed -i s/a/b/ file.py",
    "git commit -m x",
    "git branch -D main",
    "sort -o out.txt in.txt",
    "nvidia-smi -pl 200",
    "tail -f log.txt",                # writes nothing and never returns
    "ls 'unbalanced",                 # unparseable is not a promise
    "",
])
def test_a_command_that_might_write_is_not(command):
  """A false no costs one prompt; a false yes runs something unasked. The two
  are not the same mistake, and everything unparsed or unknown is a no."""
  assert policy.reads_only(command) is False


def test_the_table_says_which_mode_it_is_describing():
  """A panel listing `Edit` under "asks first" while Auto edit edits without
  asking is the broken promise the table's own caveat exists to avoid."""
  auto = policy.describe("auto-edit")
  assert "Edit" in auto["allow"] and auto["ask"] == []
  assert "mcp__rlmcp__set_parameter" in auto["allow"], "a live run too"
  assert "Interrupt" in auto["caveat"], "and it says what to do instead"

  free = policy.describe("bypass")
  assert free["ask"] == [] and free["default"] == "allow"

  assert policy.describe()["mode"] == "manual"


def test_the_handler_reads_the_mode_at_the_moment_it_is_asked():
  """Not at `start()`. The mode is changed from a button while a conversation
  is open, and a handler that closed over the old one would go on prompting in
  it until the next conversation -- and nobody restarts a conversation to stop
  being asked about `ls`."""
  from rlmcp_studio.agents.claude import ClaudeProvider

  provider = ClaudeProvider(mode="manual")
  handler = provider._make_permission_handler("048#main")
  asked: List[Any] = []
  provider.on_permission(lambda run, request: asked.append(request))

  provider.mode = "auto-edit"                       # the button, mid-turn
  answer = asyncio.run(handler("Bash", {"command": "ls -la /work/x"}, None))
  assert answer is not None and not asked, "it ran, and nobody was asked"


def test_a_prompt_nobody_is_listening_for_is_a_no_not_a_hang():
  """Every provider asks through one `Asking`. A copy that created the future,
  told nobody, and waited on it for ever would make a turn that could never
  end; with nobody listening, every provider says no."""
  from rlmcp_studio.agents.antigravity import AntigravityProvider
  from rlmcp_studio.agents.base import Decision, ToolCall
  from rlmcp_studio.agents.claude import ClaudeProvider
  from rlmcp_studio.agents.codex import CodexProvider

  handler = ClaudeProvider(mode="manual")._make_permission_handler("048#main")
  answer = asyncio.run(asyncio.wait_for(
      handler("mcp__rlmcp__set_parameter", {"key": "lr", "value": 1}, None), 5))
  assert type(answer).__name__ == "PermissionResultDeny"
  for provider in (AntigravityProvider(), CodexProvider()):
    decision = asyncio.run(asyncio.wait_for(
        provider._ask("048#main", ToolCall(name="Bash", args={})), 5))
    assert decision == Decision.DENY and not provider._pending


def test_a_new_mode_reaches_the_conversation_already_open():
  """The studio's own answer moves as soon as `self.mode` does, but the SDK
  holds a second copy given at construction, and it decides the things the
  handler never sees. Half an adoption is worse than none: the panel says the
  mode changed and half the prompts keep coming."""
  from rlmcp_studio.agents.claude import ClaudeProvider

  class FakeClient:
    def __init__(self):
      self.modes: List[str] = []
      self.models: List[str] = []

    async def set_permission_mode(self, mode):
      self.modes.append(mode)

    async def set_model(self, model):
      self.models.append(model)

  provider = ClaudeProvider(model="claude-opus-5")
  client = FakeClient()
  provider._clients["048#main"] = client
  provider.choose(mode="bypass")

  assert asyncio.run(provider.adopt_live()) == 1
  assert client.modes == ["bypassPermissions"], "in the SDK's vocabulary"
  assert client.models == ["claude-opus-5"]


def test_a_client_that_cannot_take_it_does_not_lose_the_preference():
  """A session that has died, or an SDK with no control channel. The choice is
  still the user's choice and still gets written down; the next conversation
  reads it from `_options` anyway."""
  from rlmcp_studio.agents.claude import ClaudeProvider

  class DeadClient:
    async def set_permission_mode(self, mode):
      raise RuntimeError("the transport is gone")

  provider = ClaudeProvider()
  provider._clients["048#main"] = DeadClient()
  provider.choose(mode="auto-edit")
  assert asyncio.run(provider.adopt_live()) == 0
  assert provider.mode == "auto-edit"


# ── the harness, and putting it down ─────────────────────────────────────
#
# The studio replaces Claude Code's system prompt with its own instructions,
# which is what makes the agent open a record before it launches a run. It is
# also the thing that has to be removable: while the prompt cannot be taken
# off, "is that the model or is that our prompt" has no answer, and a person
# whose own `CLAUDE.md` is being replaced has no way to run without it.


def _spec_for_a_run(tmp_path=None):
  return SessionSpec(run_id="048#main", cwd="/tmp",
                     system_context="Run 048 — go1_rough_walk on task X.")


def test_the_studio_harness_replaces_claude_codes_prompt():
  """The default, and the reason it is a string rather than an append: the
  instructions are the whole of how this agent is meant to behave."""
  from rlmcp_studio.agents.claude import ClaudeProvider, system_value

  provider = ClaudeProvider()
  brief = provider.briefing(_spec_for_a_run())
  assert brief["harness"] == "studio" and brief["preset"] is False
  assert [s["id"] for s in brief["sections"]] == ["instructions", "tools", "context"]

  system = system_value(brief)
  assert isinstance(system, str), "a string replaces Claude Code's own prompt"
  assert "mcp__rlmcp__" in system and "Run 048" in system


def test_the_harness_comes_off_and_claude_code_is_left():
  """`plain` is not "no prompt": it is the prompt a terminal in that directory
  would have had. The preset keeps Claude Code's own, and with nothing to add
  there is no append at all -- an empty one would be a flag with nothing
  behind it."""
  from rlmcp_studio.agents.claude import ClaudeProvider, system_value

  provider = ClaudeProvider(harness="plain")
  brief = provider.briefing(_spec_for_a_run())
  assert brief["sections"] == []
  assert system_value(brief) == {"type": "preset", "preset": "claude_code"}
  # And the task repository's own instructions still reach it, because the CLI
  # loads those and not us.
  assert brief["settings"] == ["user", "project", "local"]


def test_the_middle_choice_keeps_the_run_and_drops_the_instructions():
  """The useful half of the two: plain Claude Code, told which run it is
  looking at and which tools it has."""
  from rlmcp_studio.agents.claude import ClaudeProvider, system_value

  provider = ClaudeProvider(harness="context")
  brief = provider.briefing(_spec_for_a_run())
  assert [s["id"] for s in brief["sections"]] == ["tools", "context"]

  system = system_value(brief)
  assert system["preset"] == "claude_code", "Claude Code's prompt is underneath"
  assert "Run 048" in system["append"] and "mcp__rlmcp__" in system["append"]


def test_what_the_window_shows_is_what_is_sent():
  """The whole claim of the Briefing window. The sections are not a
  description of the prompt -- the prompt is assembled from them."""
  from rlmcp_studio.agents.claude import ClaudeProvider, system_value

  provider = ClaudeProvider()
  brief = provider.briefing(_spec_for_a_run())
  system = system_value(brief)
  for section in brief["sections"]:
    assert section["text"].strip() in system
    assert section["title"] and section["source"], "and where it came from"


def test_a_nonsense_harness_lands_on_the_full_one():
  """The quiet failure the other way -- an agent silently started with none of
  the studio's instructions -- is the one nobody would notice for a week."""
  from rlmcp_studio.agents.claude import ClaudeProvider, resolve_harness

  assert resolve_harness("hrness") == "studio"
  assert ClaudeProvider(harness="").harness == "studio"
  with pytest.raises(ValueError):
    ClaudeProvider().choose(harness="hrness")


def test_a_conversation_keeps_the_prompt_it_connected_with():
  """Unlike the mode and the model, this one cannot move under an open
  conversation: the system prompt is handed to the CLI once, at connect. A
  window that recomposed the briefing from the current setting would show a
  conversation a prompt it never had."""
  from rlmcp_studio.agents.claude import ClaudeProvider

  provider = ClaudeProvider()
  spec = _spec_for_a_run()
  provider._options(spec)                        # what `start()` does, without a CLI
  provider.choose(harness="plain")               # the button, afterwards

  given = provider.given("048#main")
  assert given["harness"] == "studio", "what it actually connected with"
  assert provider.briefing(spec)["harness"] == "plain", "what the next one gets"


def test_the_harness_is_offered_and_remembered():
  from rlmcp_studio.agents.claude import ClaudeProvider

  settings = ClaudeProvider().choose(harness="context")
  assert settings["harness"] == "context"
  assert [h["id"] for h in settings["harnesses"]] == ["studio", "context", "plain"]
  for offered in settings["harnesses"]:
    assert offered["label"] and offered["note"], "the menu has to say what it does"
    assert "preset" not in offered, "how it is assembled is not the client's business"


def test_the_table_can_be_shown_to_the_user():
  described = policy.describe()
  assert described["default"] == "ask"
  assert "mcp__rlmcp__set_parameter" in described["ask"]
  assert "mcp__rlmcp__take_screenshot" in described["allow"]


# ── conversations ────────────────────────────────────────────────────────
class FakeProvider:
  """A provider that answers instantly and remembers what it was handed."""

  name = "fake"
  mcp_command = "rlmcp-server"

  def __init__(self):
    self.auth_state: Dict[str, Any] = {"loggedIn": True}
    self.started: List[SessionSpec] = []
    self.clients: Dict[str, SessionSpec] = {}
    self.sent: List[Dict[str, Any]] = []
    self.reply = "looked at it"
    self.events = None
    """The host's ear. Set through `on_event`, exactly as the real one is."""

  def unavailable_reason(self) -> str:
    return ""

  def on_permission(self, callback) -> None:
    self.callback = callback

  def on_event(self, callback) -> None:
    self.events = callback

  async def start(self, spec: SessionSpec) -> str:
    self.started.append(spec)
    self.clients[spec.run_id] = spec
    return spec.run_id

  async def send(self, session_id, text, images=None) -> None:
    # The real provider keeps one client per session id and refuses anything
    # else. Mirroring that here is the whole point of the fake: addressing the
    # provider by the wrong id has to fail in a test the way it fails live.
    if session_id not in self.clients:
      raise NotSupported("no session; call start() first")
    self.sent.append({"session": session_id, "text": text,
                      "images": list(images or [])})
    await self.say(session_id, AgentEvent("text", {"text": self.reply}))
    await self.say(session_id, AgentEvent("done", {"reason": "end_turn"}))

  async def say(self, session_id, event) -> None:
    """What the provider's reader task does live: push, whoever asked."""
    if self.events is not None:
      await self.events(session_id, event)

  async def decide(self, session_id, request_id, decision, note="") -> None:
    pass

  async def stop(self, session_id) -> None:
    pass


async def collected(host, *args, **kwargs):
  await host.send(*args, **kwargs)
  for _ in range(200):                       # the turn runs as a task
    await asyncio.sleep(0.005)
    if not host.sessions[key(kwargs.get("run_id", args[0]),
                             kwargs.get("thread", "main"))].busy:
      return


def spoken(entries):
  """A transcript without the studio's own preamble.

  A conversation whose spec has no session directory opens with a
  `preflight` note saying so, because that is the state these fixtures are in
  and, on a real studio, the state of every run that has not been launched.
  These tests are about what was said after it.
  """
  return [e for e in entries if e["kind"] != "preflight"]


@pytest.fixture
def host():
  published: List[Dict[str, Any]] = []

  async def publish(kind, data, scope=""):
    published.append({"kind": kind, "data": data, "scope": scope})

  agent_host = AgentHost(publish, provider=FakeProvider())
  agent_host.published = published
  return agent_host


def test_a_turn_is_kept_so_a_reconnecting_client_is_caught_up(host):
  asyncio.run(collected(host, "002", "what is it doing?", thread="main"))

  kinds = [e["kind"] for e in spoken(host.transcript("002"))]
  assert kinds[0] == "user" and "text" in kinds
  assert spoken(host.transcript("002"))[0]["text"] == "what is it doing?"


def test_events_reach_the_browser_in_the_usual_envelope(host):
  asyncio.run(collected(host, "002", "hello", thread="main"))

  scopes = {e["scope"] for e in host.published}
  assert scopes == {"002#main"}, "an event has to name the conversation it is from"
  assert {e["kind"] for e in host.published} >= {"agent.user", "agent.text", "agent.idle"}


def test_one_run_can_hold_several_conversations(host):
  asyncio.run(collected(host, "002", "the long investigation", thread="main"))
  asyncio.run(collected(host, "002", "quick question", thread="t2200"))

  titles = {c["thread"]: c["title"] for c in host.conversations("002")}
  assert titles == {"main": "the long investigation", "t2200": "quick question"}
  # Separate provider sessions, so one context window is not two conversations.
  assert {s.run_id for s in host.provider.started} == {"002#main", "002#t2200"}


def test_a_conversation_names_itself_from_its_first_question(host):
  asyncio.run(collected(host, "002", "why did the reward collapse at 4k?"))
  assert host.conversations("002")[0]["title"] == "why did the reward collapse at 4k?"


def test_the_one_that_needs_you_sorts_first(host):
  asyncio.run(collected(host, "002", "one"))
  asyncio.run(collected(host, "002", "two", thread="second"))
  host.sessions["002#second"].pending["r1"] = {"tool": "set_parameter"}

  assert host.conversations("002")[0]["thread"] == "second"
  assert host.attention()["needs_you"] == 1


def test_the_most_recent_conversation_sorts_first(host):
  """Idle conversations in run order make the one from a minute ago as
  findable as one from last week."""
  asyncio.run(collected(host, "009", "old"))
  asyncio.run(collected(host, "002", "new"))

  rows = host.conversations()
  assert [r["run"] for r in rows] == ["002", "009"], "most recent first, not lowest id"
  assert all(r["last_at"] > 0 for r in rows), "every entry is stamped"
  assert all("at" in e for e in host.transcript("002"))
  assert rows[0]["since"] is None, "not running, so no clock"


def test_a_working_conversation_says_since_when(host):
  """"working" on its own cannot tell an agent that is thinking from one that
  hung an hour ago; the clock can."""
  asyncio.run(collected(host, "002", "one"))
  session = host.sessions["002#main"]

  session.busy = True
  since = host.conversations("002")[0]["since"]
  assert since and time.time() - since < 5
  session.busy = True
  assert host.sessions["002#main"].busy_since == since, "a rise, not every write"
  session.busy = False
  assert host.conversations("002")[0]["since"] is None


def test_a_restored_conversation_dates_from_its_file(tmp_path):
  """Entries from older files carry no `at`: the file's mtime stands in, so
  such a conversation still sorts by when it was."""
  from rlmcp_studio.agents.store import Transcripts

  async def publish(kind, data, scope=""):
    pass

  store = Transcripts(tmp_path / "conversations")
  store.append("046#main", {"kind": "user", "seq": 0, "text": "the long investigation"})
  os.utime(store.path("046#main"), (1_700_000_000, 1_700_000_000))

  host = AgentHost(publish, provider=FakeProvider(),
                   transcripts=Transcripts(tmp_path / "conversations"))
  host.restore()
  assert host.conversations("046")[0]["last_at"] == 1_700_000_000


def test_a_second_message_while_it_is_working_is_refused():
  """A turn ends when the provider says it ended, not when `send` returns --
  so this needs a provider whose turn is still running when it does."""
  host = AgentHost(_quiet, provider=SlowProvider())

  async def two_at_once():
    await _working(host)
    with pytest.raises(RuntimeError):
      await host.send("002", "second")

  asyncio.run(two_at_once())


def test_what_the_agent_is_shown_is_carried_in_the_transcript(host):
  """The image goes to the model; the path goes to the transcript, so the
  pane can render what the question was about instead of referring to a
  picture nobody can see."""
  asyncio.run(collected(host, "002", "what is in the box?",
                        images=[b"fake-png"], shows="/records/media/002/a.png"))

  entry = spoken(host.transcript("002"))[0]
  assert entry["shows"] == "/records/media/002/a.png"
  assert host.provider.sent[0]["images"] == [b"fake-png"]


# ── context and the image message ────────────────────────────────────────
def test_the_agent_starts_inside_the_experiment():
  context = build_context(
      {"id": "002", "slug": "rough_gate", "task": "Fake-Task-G1",
       "verdict": "running", "parent": "001", "change": ["a gate"],
       "weights": "001"},
      {"hypothesis": "a gate costs nothing", "prediction": "hf falls",
       "falsifier": {"prose": "hf climbs past 0.2"}})

  assert "002" in context and "Fake-Task-G1" in context
  # The falsifier is included on purpose: an agent that knows what would prove
  # the run wrong argues differently from one that knows only what it hopes.
  assert "hf climbs past 0.2" in context
  assert "cannot claim `validated`" in context, "a warm start limits the claim"


def test_the_image_message_is_the_shape_the_cli_reads():
  from rlmcp_studio.agents.claude import _with_images

  async def first():
    return [m async for m in _with_images("look here", [b"\x89PNG..."])][0]

  message = asyncio.run(first())
  assert message["type"] == "user" and message["message"]["role"] == "user"
  blocks = message["message"]["content"]
  assert [b["type"] for b in blocks] == ["text", "image"]
  assert blocks[1]["source"]["type"] == "base64"
  assert blocks[1]["source"]["media_type"] == "image/png"


def test_the_provider_is_addressed_by_the_id_it_was_started_with(host):
  """A session is started as `run#thread`; speaking to it as `run` makes every
  message come back "no session; call start() first" -- and only with
  credentials in place, which is how it goes unnoticed.

  One run holds several conversations, so the provider's key has to carry the
  thread. The run id alone identifies no conversation.
  """
  asyncio.run(collected(host, "046", "hello", thread="main"))

  assert [s.run_id for s in host.provider.started] == ["046#main"]
  assert [s["session"] for s in host.provider.sent] == ["046#main"]

  kinds = [e["kind"] for e in host.transcript("046")]
  assert "error" not in kinds, host.transcript("046")
  assert "text" in kinds


def test_a_second_thread_talks_to_its_own_provider_session(host):
  asyncio.run(collected(host, "046", "the long one", thread="main"))
  asyncio.run(collected(host, "046", "a quick one", thread="t99"))

  assert [s["session"] for s in host.provider.sent] == ["046#main", "046#t99"]
  assert all("error" not in [e["kind"] for e in host.transcript("046", t)]
             for t in ("main", "t99"))


def test_one_message_may_be_bigger_than_the_sdks_default():
  """A tool result carrying a frame, a clip or a metrics dump arrives as one
  JSON line. At the SDK's 1 MB default the turn dies with "JSON message
  exceeded maximum buffer size" -- which is how a turn ends once the agent
  records a clip and reads it back."""
  from rlmcp_studio.agents.claude import MAX_MESSAGE_BYTES, ClaudeProvider

  options = ClaudeProvider()._options(
      SessionSpec(run_id="046#main", cwd="", system_context="", mcp_servers={}))

  assert options.max_buffer_size == MAX_MESSAGE_BYTES
  assert MAX_MESSAGE_BYTES > 1024 * 1024, "the default is what broke"
  assert MAX_MESSAGE_BYTES <= 64 * 1024 * 1024, "still a cap, not an invitation"


# ── the permission prompt's promises ─────────────────────────────────────
def permission_handler(provider, run_id="046#main"):
  """The real provider's handler, without the SDK's client.

  It only touches the policy table, a future and the callback, so it can be
  driven directly -- which is the only way to test what the *buttons* do.
  """
  asked = []
  provider.on_permission(lambda run, request: asked.append(request))
  return provider._make_permission_handler(run_id), asked


def test_always_for_this_run_actually_remembers(host):
  """"Always for this run" has to be remembered, or the next call to the same
  tool asks again, and the one after, and the tenth."""
  from rlmcp_studio.agents.claude import ClaudeProvider

  provider = ClaudeProvider()
  handler, asked = permission_handler(provider)

  async def exercise():
    first = asyncio.create_task(handler("Bash", {"command": "ls"}, None))
    await asyncio.sleep(0.01)
    await provider.decide("046#main", asked[0].id, Decision.ALLOW_ALWAYS)
    await first
    # A second call to the same tool must not ask again.
    await handler("Bash", {"command": "pwd"}, None)

  asyncio.run(exercise())
  assert len(asked) == 1, "it asked a second time despite 'always'"
  assert provider.granted("046") == ["Bash"]


def test_a_grant_covers_the_run_not_every_tool(host):
  from rlmcp_studio.agents.claude import ClaudeProvider

  provider = ClaudeProvider()
  handler, asked = permission_handler(provider)

  async def exercise():
    first = asyncio.create_task(handler("Bash", {"command": "ls"}, None))
    await asyncio.sleep(0.01)
    await provider.decide("046#main", asked[0].id, Decision.ALLOW_ALWAYS)
    await first
    second = asyncio.create_task(handler("Edit", {"file_path": "x"}, None))
    await asyncio.sleep(0.01)
    await provider.decide("046#main", asked[1].id, Decision.DENY)
    await second

  asyncio.run(exercise())
  assert len(asked) == 2, "a grant for Bash must not cover Edit"


def test_two_prompts_in_flight_get_different_ids(host):
  """Ids numbered by the *size* of the pending map, which shrinks as requests
  are answered, would repeat -- so an approval could resolve a different
  request than the one on screen."""
  from rlmcp_studio.agents.claude import ClaudeProvider

  provider = ClaudeProvider()
  handler, asked = permission_handler(provider)

  async def exercise():
    one = asyncio.create_task(handler("Bash", {"command": "a"}, None))
    two = asyncio.create_task(handler("Edit", {"file_path": "b"}, None))
    await asyncio.sleep(0.02)
    ids = [request.id for request in asked]
    assert len(set(ids)) == 2, ids
    for request in asked:
      await provider.decide("046#main", request.id, Decision.DENY)
    await one
    await two

  asyncio.run(exercise())


# ── a conversation that outlives the process ─────────────────────────────
def test_a_transcript_is_written_to_disk(tmp_path):
  """The host promises a conversation "still knows what happened last night".
  Transcripts held only in memory would be erased by every restart -- which is
  every time this app's code changes -- along with every conversation about
  every run."""
  from rlmcp_studio.agents.store import Transcripts

  async def publish(kind, data, scope=""):
    pass

  store = Transcripts(tmp_path / "conversations")
  host = AgentHost(publish, provider=FakeProvider(), transcripts=store)
  asyncio.run(collected(host, "046", "why did the reward collapse?", thread="main"))

  saved = spoken(store.load("046#main"))
  assert [e["kind"] for e in saved][:2] == ["user", "text"]
  assert saved[0]["text"] == "why did the reward collapse?"


def test_a_new_process_reads_the_conversation_back(tmp_path):
  from rlmcp_studio.agents.store import Transcripts

  async def publish(kind, data, scope=""):
    pass

  store = Transcripts(tmp_path / "conversations")
  first = AgentHost(publish, provider=FakeProvider(), transcripts=store)
  asyncio.run(collected(first, "046", "the long investigation", thread="main"))

  # A new host, as after a restart: nothing in memory, but asking for the
  # transcript reads it off disk, so a client that reconnects is caught up
  # without waiting for anything.
  second = AgentHost(publish, provider=FakeProvider(),
                     transcripts=Transcripts(tmp_path / "conversations"))
  assert spoken(second.transcript("046"))[0]["text"] == "the long investigation"

  # `restore` is the other half: it puts the conversation back in the *list*,
  # so the rail shows it rather than only the run you happen to open.
  assert second.restore() == 1
  kept = spoken(second.transcript("046"))
  assert kept[0]["text"] == "the long investigation"
  assert second.conversations("046")[0]["title"] == "the long investigation"


def test_the_break_is_marked_when_a_restored_conversation_is_spoken_to(tmp_path):
  """The words come back; the model's memory of them does not. A pane showing
  an unbroken conversation whose second half cannot remember its first would
  be lying by omission."""
  from rlmcp_studio.agents.store import Transcripts

  async def publish(kind, data, scope=""):
    pass

  store = Transcripts(tmp_path / "conversations")
  first = AgentHost(publish, provider=FakeProvider(), transcripts=store)
  asyncio.run(collected(first, "046", "before the restart", thread="main"))

  second = AgentHost(publish, provider=FakeProvider(),
                     transcripts=Transcripts(tmp_path / "conversations"))
  second.restore()
  asyncio.run(collected(second, "046", "after the restart", thread="main"))

  kinds = [e["kind"] for e in second.transcript("046")]
  assert "restart" in kinds
  assert kinds.index("restart") < kinds.index("user", kinds.index("restart"))


def test_a_studio_that_cannot_write_its_logs_still_runs(tmp_path):
  """This is a convenience, not the product."""
  from rlmcp_studio.agents.store import Transcripts

  blocked = tmp_path / "not-a-dir"
  blocked.write_text("in the way")
  store = Transcripts(blocked / "conversations")
  assert store.broken

  async def publish(kind, data, scope=""):
    pass

  host = AgentHost(publish, provider=FakeProvider(), transcripts=store)
  asyncio.run(collected(host, "046", "still works", thread="main"))
  assert spoken(host.transcript("046"))[0]["text"] == "still works"


def test_a_tool_result_reaches_the_transcript():
  """Results come back as a *user* message -- that is the protocol's shape, not
  a person speaking. A `_translate` that handled only assistant messages would
  show every call and never its answer: calls sitting there looking unanswered
  under a reply that had clearly used them."""
  from claude_agent_sdk import ToolResultBlock, UserMessage

  from rlmcp_studio.agents.claude import _translate

  events = _translate(UserMessage(content=[
      ToolResultBlock(tool_use_id="toolu_1", content="iteration 3668"),
  ]))
  assert [e.kind for e in events] == ["tool_result"]
  assert events[0].data["id"] == "toolu_1"
  assert "3668" in events[0].data["content"]


def test_the_echo_of_what_you_typed_is_not_added_twice():
  """A user message carrying text is usually the CLI echoing the prompt back;
  it is already in the transcript, put there when it was sent."""
  from claude_agent_sdk import TextBlock, UserMessage

  from rlmcp_studio.agents.claude import _translate

  assert _translate(UserMessage(content=[TextBlock(text="hello")]), ["hello"]) == []
  assert _translate(UserMessage(content="hello"), ["hello"]) == []
  # With nothing to compare against, the default rule stands: text in a user
  # message is assumed to be ours. Mistaking the person's own words for the
  # CLI waking the agent is the worse of the two failures.
  assert _translate(UserMessage(content=[TextBlock(text="hello")])) == []


def test_a_turn_the_cli_started_by_itself_says_so():
  """The case this whole path exists for. A background `Bash` finishing makes
  the CLI inject a notification and answer it, with nobody in this studio
  having typed anything. Shown as its own thing, or the answer under it reads
  as a reply to the last question the user asked -- two messages in a row can
  answer the notifications before them while neither answers the person."""
  from claude_agent_sdk import TextBlock, UserMessage

  from rlmcp_studio.agents.claude import _translate

  events = _translate(UserMessage(content=[
      TextBlock(text="<task-notification>\n<task-id>ba7gmtng1</task-id>")]),
      ["reset the env so that it can learn again"])
  assert [e.kind for e in events] == ["wake"]
  assert "ba7gmtng1" in events[0].data["text"]
  # And what we said is still ours, in the same conversation.
  assert _translate(UserMessage(content=[
      TextBlock(text="reset the env so that it can learn again")]),
      ["reset the env so that it can learn again"]) == []


def test_an_unasked_turn_is_not_attributed_to_the_next_message():
  """The shape of the failure, at the host: events that arrive while the pane
  is idle belong to their own turn. Read only inside `send`, they would be
  drained by -- and shown under -- whatever was typed next."""
  host = AgentHost(_quiet, provider=FakeProvider())

  async def go():
    await collected(host, "002", "start training")
    # The CLI wakes up on its own: no `send`, and nobody waiting on it.
    await host.provider.say("002#main", AgentEvent(
        "wake", {"text": "<task-notification>ba7gmtng1"}))
    assert host.sessions["002#main"].busy, "a turn is running, whoever started it"
    await host.provider.say("002#main", AgentEvent("text", {"text": "it died at import"}))
    await host.provider.say("002#main", AgentEvent("done", {"reason": "end_turn"}))
    assert not host.sessions["002#main"].busy
    # Only now does the user type. What they get back is theirs.
    await collected(host, "002", "reset the env")

  asyncio.run(go())
  kinds = [e["kind"] for e in host.transcript("002")]
  said = [e.get("text") for e in host.transcript("002")]
  assert kinds.index("wake") < kinds.index("user", kinds.index("wake"))
  assert said[said.index("reset the env") + 1] == host.provider.reply, (
      "the answer under a question has to be the answer to it")


def test_the_prompt_names_the_tools_the_way_they_must_be_asked_for():
  """The tools are deferred behind ToolSearch, a keyword search for
  "screenshot" does not match `mcp__rlmcp__take_screenshot`, and an agent that
  finds nothing concludes -- reasonably, and wrongly -- that rlmcp is not
  attached. The names go in the prompt, written from the permission table so
  they cannot drift from it."""
  from rlmcp_studio.agents import policy
  from rlmcp_studio.agents.claude import system_prompt, _tool_list

  listing = _tool_list()
  # The prompt is assembled from `instructions/*.md`, so this asks the
  # function rather than a constant.
  assert "mcp__rlmcp__" in system_prompt(), "the prefix is what makes them findable"
  for tool in policy.describe()["allow"] + policy.describe()["ask"]:
    if tool.startswith("mcp__rlmcp__"):
      assert tool in listing, f"{tool} is in the policy table but not in the prompt"
  assert "the user is asked first" in listing, "which ones cost an approval"

  # ...and in the mode the studio actually runs in, nothing does. A prompt
  # saying "the user is asked first" over half the list while the handler
  # approves all of it makes the agent narrate approvals it does not need.
  auto = _tool_list("auto-edit")
  assert "asked first" not in auto
  assert "mcp__rlmcp__set_parameter" in auto and "mcp__rlmcp__stop_training" in auto


def test_a_conversation_that_outlived_a_restart_gets_its_tools_back(tmp_path):
  """A restored conversation is built from a transcript on disk, which knows
  nothing about a session directory -- so its spec has no cwd and no MCP
  servers. If `ensure` kept that spec, the conversation would come back
  permanently toolless: the agent would search for
  `mcp__rlmcp__take_screenshot`, find nothing and report, correctly for what it
  could see, that rlmcp was not attached.
  """
  from rlmcp_studio.agents.store import Transcripts

  async def publish(kind, data, scope=""):
    pass

  session_dir = tmp_path / "run" / "rlmcp"
  session_dir.mkdir(parents=True)

  first = AgentHost(publish, provider=FakeProvider(), transcripts=Transcripts(tmp_path / "c"))
  asyncio.run(collected(first, "046", "before", thread="main",
                        cwd=str(tmp_path), session_dir=str(session_dir)))

  second = AgentHost(publish, provider=FakeProvider(), transcripts=Transcripts(tmp_path / "c"))
  assert second.restore() == 1
  assert second.sessions[key("046", "main")].spec.mcp_servers == {}, "nothing to go on yet"

  asyncio.run(collected(second, "046", "after", thread="main",
                        cwd=str(tmp_path), session_dir=str(session_dir)))
  spec = second.sessions[key("046", "main")].spec
  assert spec.mcp_servers, "the rlmcp server has to be back"
  assert str(session_dir) in str(spec.mcp_servers), "and pinned to this run"
  assert spec.cwd == str(tmp_path)


def test_a_studio_only_restores_conversations_about_runs_it_has(tmp_path):
  """Conversations live in one directory per user, not one per records root, so
  a second studio over a different set of records would otherwise list run
  047's conversations in a rail whose only record is 001."""
  from rlmcp_studio.agents.store import Transcripts

  async def publish(kind, data, scope=""):
    pass

  store = Transcripts(tmp_path / "conversations")
  first = AgentHost(publish, provider=FakeProvider(), transcripts=store)
  asyncio.run(collected(first, "047", "about a run in another store", thread="main"))
  asyncio.run(collected(first, "001", "about this one", thread="main"))

  elsewhere = AgentHost(publish, provider=FakeProvider(),
                        transcripts=Transcripts(tmp_path / "conversations"))
  assert elsewhere.restore(known_runs=["001"]) == 1
  assert [s.run_id for s in elsewhere.sessions.values()] == ["001"]

  everything = AgentHost(publish, provider=FakeProvider(),
                         transcripts=Transcripts(tmp_path / "conversations"))
  assert everything.restore() == 2, "no list given means restore what is there"


def test_a_task_conversation_comes_back_after_a_restart(tmp_path):
  """Build's conversation and the report thread are about a task, not a run:
  `task:Foo#report`. A restore that kept only ids the rail can name would drop
  every task conversation at every boot, because the rail names runs. And the
  file is a *safe* spelling, `task_Foo#report.jsonl`, so even an unfiltered
  restore has to key it back under the name that is asked for."""
  from rlmcp_studio.agents.store import Transcripts

  async def publish(kind, data, scope=""):
    pass

  first = AgentHost(publish, provider=FakeProvider(),
                    transcripts=Transcripts(tmp_path / "c"))
  asyncio.run(collected(first, "task:Foo", "write the report", thread="report"))
  asyncio.run(collected(first, "001", "about a run", thread="main"))

  second = AgentHost(publish, provider=FakeProvider(),
                     transcripts=Transcripts(tmp_path / "c"))
  assert second.restore(known_runs=["001"]) == 2, "the rail's list does not name tasks"
  assert [c["title"] for c in second.conversations("task:Foo")] == ["write the report"]
  assert spoken(second.transcript("task:Foo", "report"))[0]["text"] == "write the report"
  assert key("task:Foo", "report") in second.sessions, "keyed as it is asked for"


def test_a_rotated_transcript_is_still_listed_and_read(tmp_path):
  """`append` moves a file past MAX_BYTES to `.jsonl.old` and starts afresh.
  A `keys()` that globbed only `*.jsonl` would, between the rotation and the
  next word said, leave the conversation off disk as far as a restart could
  tell; a `load` that read only the new file would lose the words before the
  cut."""
  from rlmcp_studio.agents.store import Transcripts

  store = Transcripts(tmp_path / "c")
  store.append("002#main", {"kind": "user", "seq": 0, "run": "002", "thread": "main",
                            "text": "before"})
  store.path("002#main").replace(store.path("002#main").with_suffix(".jsonl.old"))

  assert store.keys() == ["002#main"]
  assert [e["text"] for e in store.load("002#main")] == ["before"]
  store.append("002#main", {"kind": "user", "seq": 1, "run": "002", "thread": "main",
                            "text": "after"})
  assert [e["text"] for e in store.load("002#main")] == ["before", "after"]


def test_two_task_folders_do_not_share_a_run_007(tmp_path):
  """Run ids count from 001 inside a records root, so two studios over two
  task folders both have a 007. Keyed by id alone, both would append to and
  read back the same `007#main.jsonl`, and the agent panel for one task's 007
  would open showing the other task's conversation above the restart
  divider."""
  from rlmcp_studio.agents.store import Transcripts

  async def publish(kind, data, scope=""):
    pass

  state = tmp_path / "conversations"
  task_a = str(tmp_path / "task_a" / "records")
  task_b = str(tmp_path / "task_b" / "records")

  first = AgentHost(publish, provider=FakeProvider(),
                    transcripts=Transcripts(state, workspace=task_a))
  asyncio.run(collected(first, "007", "the gait drifts to the left",
                        thread="main"))

  second = AgentHost(publish, provider=FakeProvider(),
                     transcripts=Transcripts(state, workspace=task_b))
  assert second.restore(known_runs=["007"]) == 0, "another task's 007 is not this one"
  assert second.transcript("007", "main") == []

  asyncio.run(collected(second, "007", "port the gait to G1", thread="main"))
  again = AgentHost(publish, provider=FakeProvider(),
                    transcripts=Transcripts(state, workspace=task_b))
  assert again.restore(known_runs=["007"]) == 1
  texts = [e.get("text") for e in again.transcript("007", "main")]
  assert "port the gait to G1" in texts
  assert "the gait drifts to the left" not in texts

  # And the one that wrote it first still has it.
  back = AgentHost(publish, provider=FakeProvider(),
                   transcripts=Transcripts(state, workspace=task_a))
  assert back.restore(known_runs=["007"]) == 1
  assert "the gait drifts to the left" in [
      e.get("text") for e in back.transcript("007", "main")]


def test_conversations_written_before_scoping_are_kept_but_not_claimed(tmp_path):
  """A flat `007#main.jsonl` cannot be attributed to a records root, and the
  ones on disk may already hold two studios' turns. Adopting them would show
  the contamination once more; deleting them would throw away real work."""
  from rlmcp_studio.agents.store import Transcripts

  state = tmp_path / "conversations"
  state.mkdir(parents=True)
  legacy = state / "007#main.jsonl"
  legacy.write_text('{"kind": "user", "seq": 0, "thread": "main", '
                    '"run": "007", "text": "from before"}\n')

  store = Transcripts(state, workspace=str(tmp_path / "records"))
  assert store.keys() == [], "nothing unattributable comes back"
  assert store.retired == ["007#main.jsonl"]
  assert not legacy.exists()
  assert (state / "_unscoped" / "007#main.jsonl").exists(), "kept, not deleted"


def test_the_policy_says_what_it_actually_governs():
  """A panel listing `Bash` under "asks you first" can be wrong: the SDK runs
  the CLI with `--setting-sources=user,project,local`, on purpose, and a built-in the user's own settings already allow never reaches
  this handler. The rlmcp half is absolute; the built-in half is a floor, not a
  ceiling. Whatever the table claims, it must not imply otherwise."""
  from rlmcp_studio.agents import policy

  table = policy.describe()
  assert "mcp__rlmcp__set_parameter" in table["ask"]
  assert table["caveat"], "a promise the studio cannot keep needs a caveat beside it"
  assert "settings" in table["caveat"].lower()


def test_two_permission_requests_never_share_an_id_across_a_restart():
  """A transcript is read back from disk, so ids have to be unique across the
  life of the *transcript*, not of the process. Two `Bash` requests both
  carrying `048#main:1:Bash` — one from before a restart, one from after —
  would be indistinguishable, and a decision is matched to a request by that
  id."""
  from rlmcp_studio.agents.claude import ClaudeProvider

  first, second = ClaudeProvider(), ClaudeProvider()
  assert first._era != second._era, "each process needs its own era"

  first._asked += 1
  second._asked += 1
  assert (f"048#main:{first._era}{first._asked}:Bash"
          != f"048#main:{second._era}{second._asked}:Bash")


def test_the_mcp_server_is_pinned_to_the_records_root_as_well_as_the_session():
  """A record tool takes a bare id, and an unrooted server resolves it the
  CLI's way: `$RLMCP_RECORDS`, then `./records` under its own cwd. On a machine
  with two records roots that is somebody else's `002`: a headline written
  from the studio would report success, never appear on the run it was about,
  and land in an unrelated experiment in another store."""
  from rlmcp_studio.agents.host import AgentHost

  host = AgentHost(lambda *a, **k: None)
  servers = host._mcp_for("/logs/run/rlmcp", "/records/mine")
  args = servers["rlmcp"]["args"]

  assert "--session" in args and "/logs/run/rlmcp" in args
  assert "--records-root" in args
  assert args[args.index("--records-root") + 1] == "/records/mine"

  # No root configured is still a session-pinned server, not a broken one.
  assert "--records-root" not in host._mcp_for("/logs/run/rlmcp")["rlmcp"]["args"]
  # And no session at all means no server: there is nothing to pin it to.
  assert host._mcp_for("", "/records/mine") == {}


# ── stopping one ─────────────────────────────────────────────────────────
class SlowProvider(FakeProvider):
  """A turn that does not end until something ends it.

  The runaway it models: an agent that reads for minutes, writes no files, and
  has to be stopped from outside the turn.
  """

  def __init__(self):
    super().__init__()
    self.interrupted: List[str] = []
    self.closed: List[str] = []
    self._gate = asyncio.Event()

  async def send(self, session_id, text, images=None) -> None:
    if session_id not in self.clients:
      raise NotSupported("no session; call start() first")
    self.sent.append({"session": session_id, "text": text, "images": list(images or [])})
    await self.say(session_id, AgentEvent("text", {"text": "reading everything first"}))
    # The turn does not end when `send` returns -- it ends when the provider
    # says so, which is the distinction the host depends on.
    asyncio.create_task(self._finish(session_id))

  async def _finish(self, session_id) -> None:
    await self._gate.wait()
    await self.say(session_id, AgentEvent("done", {"reason": "interrupted"}))

  async def interrupt(self, session_id) -> None:
    self.interrupted.append(session_id)
    self._gate.set()

  async def stop(self, session_id) -> None:
    self.closed.append(session_id)
    self.clients.pop(session_id, None)
    self._gate.set()


class MidSentence(SlowProvider):
  """A provider interrupted with its mouth full, which is all of them.

  The turn does not stop at the word the request arrived on: what the model had
  already produced is still in flight, and the CLI answers the interrupt with a
  user-role message of its own -- "[Request interrupted by user]" -- before the
  turn ends. Both arrive *after* `stop` returns.
  """

  async def interrupt(self, session_id) -> None:
    self.interrupted.append(session_id)
    # In flight, therefore *after* `stop` returns: a fake that says the tail
    # inside `interrupt` proves nothing, because `stop` then tidies up behind
    # it and every ordering comes out right by accident.
    asyncio.create_task(self._tail(session_id))

  async def _tail(self, session_id) -> None:
    await asyncio.sleep(0.01)
    await self.say(session_id, AgentEvent("text", {"text": "…and forty-one, forty-t"}))
    await self.say(session_id, AgentEvent("wake", {"text": "[Request interrupted by user]"}))
    self._gate.set()


class CannotInterrupt(SlowProvider):
  """A provider with no interrupt at all -- `base.AgentProvider` says that is
  allowed, and the host has to stop the turn anyway."""

  async def interrupt(self, session_id) -> None:
    raise NotSupported("fake cannot interrupt")


async def _quiet(kind, data, scope=""):
  """A publish that goes nowhere, for the tests that read the transcript."""


async def _working(host, run="002", thread="main"):
  """Start a turn and wait until the host says it is running."""
  await host.send(run, "go", thread=thread)
  for _ in range(200):
    await asyncio.sleep(0.005)
    if host.sessions[key(run, thread)].busy:
      return host.sessions[key(run, thread)]
  raise AssertionError("the turn never started")


async def _settled(host, run="002", thread="main"):
  for _ in range(200):
    await asyncio.sleep(0.005)
    if not host.sessions[key(run, thread)].busy:
      return
  raise AssertionError("the turn never ended")


def test_a_working_agent_can_be_stopped():
  """`POST /api/agent/{id}/stop` and `AgentHost.stop` end a turn from the
  panel; without them the only way to end one is a terminal."""
  host = AgentHost(_quiet, provider=SlowProvider())

  async def go():
    await _working(host)
    stopped = await host.stop("002")
    await _settled(host)
    return stopped

  assert asyncio.run(go()) is True, "it says whether it stopped anything"
  assert host.provider.interrupted == ["002#main"], "the turn is ended, not the session"
  assert host.sessions["002#main"].busy is False


def test_stopping_a_turn_keeps_the_conversation_it_interrupted():
  """A runaway is stopped in order to be corrected -- "you have read for four
  minutes and written nothing; write these three files" -- and that sentence
  only means anything in the context it interrupts.

  It also has to keep the *transcript*. Popping the session would make the
  next message build a fresh one whose entries start again at ``seq`` 0 -- and
  the client de-duplicates on ``seq``, so the first thing said after a stop
  would be silently dropped from the screen while landing perfectly on disk.
  """
  host = AgentHost(_quiet, provider=SlowProvider())

  async def go():
    await _working(host)
    await host.stop("002")
    await _settled(host)
    before = list(host.transcript("002"))
    await host.send("002", "write the three files instead")
    await _settled(host)
    return before, list(host.transcript("002"))

  before, after = asyncio.run(go())
  assert "002#main" in host.sessions, "the conversation is still there to talk to"
  assert [e["kind"] for e in before].count("user") == 1
  assert [e["text"] for e in after if e["kind"] == "user"] == [
      "go", "write the three files instead"]
  seqs = [e["seq"] for e in after]
  assert seqs == sorted(set(seqs)), \
      "every entry keeps a seq of its own, or the client drops it as a duplicate"


def test_a_stop_is_said_in_the_transcript():
  """A turn that ended because it was ended looks exactly like one that
  finished, and the difference is the whole reason the control exists."""
  published: List[Dict[str, Any]] = []

  async def publish(kind, data, scope=""):
    published.append({"kind": kind, "data": data, "scope": scope})

  host = AgentHost(publish, provider=SlowProvider())

  async def go():
    await _working(host)
    await host.stop("002")
    await _settled(host)

  asyncio.run(go())
  assert "stopped" in [e["kind"] for e in host.transcript("002")]
  assert "agent.stopped" in [e["kind"] for e in published], \
      "the browser hears about it on the same socket as everything else"


def test_stopping_drops_the_questions_the_turn_was_waiting_on():
  """A permission card still asking about a tool call nobody will make now is
  a question whose answer changes nothing -- and it would hold the conversation
  at `needs_you` forever."""
  host = AgentHost(_quiet, provider=SlowProvider())

  async def go():
    session = await _working(host)
    session.pending["r1"] = {"tool": "Bash", "request_id": "r1"}
    await host.stop("002")
    await _settled(host)

  asyncio.run(go())
  assert host.sessions["002#main"].pending == {}
  assert host.sessions["002#main"].state == "idle"


def test_a_provider_that_cannot_interrupt_still_stops():
  """`interrupt` is optional in the contract. Closing the session is worse --
  the context goes with it -- but it is not nothing, and the transcript says
  so rather than claiming the studio restarted."""
  host = AgentHost(_quiet, provider=CannotInterrupt())

  async def go():
    await _working(host)
    await host.stop("002")
    await _settled(host)
    await host.send("002", "again")
    await _settled(host)

  asyncio.run(go())
  session = host.sessions["002#main"]
  assert host.provider.closed == ["002#main"], "it fell back to closing the session"
  notes = [e["text"] for e in host.transcript("002") if e["kind"] == "restart"]
  assert notes and "restart" not in notes[0].lower(), \
      "the studio did not restart, and must not say it did"
  assert session.busy is False


async def _said(host, kind, run="002", thread="main"):
  """Wait for an entry of `kind`, because the turn admits it ended in its own
  time and a test that reads the transcript one tick too early passes for a
  reason that has nothing to do with what it is testing."""
  for _ in range(400):
    await asyncio.sleep(0.005)
    if kind in [e["kind"] for e in host.transcript(run, thread)]:
      return
  raise AssertionError(f"nothing ever said {kind}")


def test_the_stop_note_comes_after_the_words_it_cut_off():
  """"You stopped it." said the moment the button is pressed lands *above* the
  three sentences the model had already produced -- so the pane reads as an
  agent that carried on talking after being stopped, which is the one thing
  the reader must not have to wonder about."""
  host = AgentHost(_quiet, provider=MidSentence())

  async def go():
    await _working(host)
    await host.stop("002")
    # Not "wait for the note" -- wait for the *turn*, or the tail it is meant
    # to sit under has not arrived yet and any order looks right.
    await _said(host, "idle")
    return [(e["kind"], e.get("text", "")) for e in host.transcript("002")]

  entries = asyncio.run(go())
  kinds = [k for k, _ in entries]
  assert kinds.count("stopped") == 1, "said once, whoever gets there first"
  assert kinds.index("stopped") > len(kinds) - 1 - kinds[::-1].index("text"), \
      "the note goes below the last thing the interrupted turn managed to say"


def test_the_words_arriving_after_a_stop_do_not_look_like_a_new_turn():
  """`_on_event` sets `busy` on anything that is not `done`, which is right for
  the CLI waking up on its own and wrong for the tail of a turn you just
  ended: the panel would say "working…" again moments after the stop, and the
  Send button would turn back into a Stop."""
  host = AgentHost(_quiet, provider=MidSentence())

  async def go():
    await _working(host)
    await host.stop("002")
    busy_after = []
    for _ in range(20):
      await asyncio.sleep(0.005)
      busy_after.append(host.sessions["002#main"].busy)
    await _said(host, "stopped")
    return busy_after

  assert not any(asyncio.run(go())), "it never looks like it is working again"


def test_the_cli_saying_it_was_interrupted_is_not_the_agent_waking_up():
  """The CLI answers an interrupt with "[Request interrupted by user]", which
  reaches the studio as `wake` -- the kind the panel labels WOKE ITSELF, for
  the agent doing something nobody asked for. About the one event in this app
  the person unambiguously caused, that is exactly backwards, and it would
  repeat what the studio's own note says."""
  host = AgentHost(_quiet, provider=MidSentence())

  async def go():
    await _working(host)
    await host.stop("002")
    await _said(host, "idle")
    return host.transcript("002")

  entries = asyncio.run(go())
  assert "wake" not in [e["kind"] for e in entries]
  assert [e["text"] for e in entries if e["kind"] == "stopped"] == ["You stopped it."]


def test_a_correction_typed_straight_after_a_stop_carries_the_note_with_it():
  """You stop it *in order to* say what it should have done, and that sentence
  usually follows within seconds -- before the interrupted turn has ended. The
  note has to go above the correction rather than arrive in the middle of the
  turn the correction starts."""
  host = AgentHost(_quiet, provider=SlowProvider())

  async def go():
    await _working(host)
    await host.stop("002")
    await host.send("002", "write the three files instead")
    await _said(host, "stopped")
    return [(e["kind"], e.get("text", "")) for e in host.transcript("002")]

  kinds = [k for k, _ in asyncio.run(go())]
  assert kinds.count("stopped") == 1, "the deadline does not say it a second time"
  assert kinds.index("stopped") < len(kinds) - 1 - kinds[::-1].index("user"), \
      "and it is above the correction, not after it"


def test_a_provider_that_never_accepts_the_interrupt_still_stops():
  """`interrupt` is a request to another process, and the button that makes it
  is the one you press when something is already not answering. Waiting on it
  forever would hang the request that the panel is waiting on."""

  class Deaf(SlowProvider):
    async def interrupt(self, session_id) -> None:
      await asyncio.sleep(30)

  host = AgentHost(_quiet, provider=Deaf())
  import rlmcp_studio.agents.host as host_module
  was = host_module.STOP_ACCEPTED
  host_module.STOP_ACCEPTED = 0.05

  async def go():
    await _working(host)
    return await host.stop("002")

  try:
    assert asyncio.run(go()) is True
  finally:
    host_module.STOP_ACCEPTED = was
  assert host.provider.closed == ["002#main"], \
      "an interrupt that is never accepted is a provider that cannot interrupt"
  assert host.sessions["002#main"].busy is False
  assert "stopped" in [e["kind"] for e in host.transcript("002")]


def test_a_turn_that_never_ends_still_says_it_was_stopped():
  """The note is said when the turn ends. A provider that accepts the
  interrupt and then goes quiet must not cost the user the one line that tells
  them the button did anything."""

  class Silent(SlowProvider):
    async def interrupt(self, session_id) -> None:
      self.interrupted.append(session_id)     # accepted, and then nothing

  host = AgentHost(_quiet, provider=Silent())
  import rlmcp_studio.agents.host as host_module
  was = host_module.STOP_ADMITTED
  host_module.STOP_ADMITTED = 0.05

  async def go():
    await _working(host)
    await host.stop("002")
    await _said(host, "stopped")
    return host.transcript("002")

  try:
    entries = asyncio.run(go())
  finally:
    host_module.STOP_ADMITTED = was
  assert [e["text"] for e in entries if e["kind"] == "stopped"] == ["You stopped it."]


def test_stopping_a_conversation_that_is_doing_nothing_says_nothing_was_running():
  host = AgentHost(_quiet, provider=FakeProvider())

  async def go():
    await collected(host, "002", "hello")
    return await host.stop("002")

  assert asyncio.run(go()) is False


def test_stopping_a_conversation_that_does_not_exist_is_not_an_error():
  host = AgentHost(_quiet, provider=FakeProvider())
  assert asyncio.run(host.stop("049")) is False


# ── auth: not being able to ask is not an answer ──────────────────────────
def test_a_timed_out_auth_check_does_not_sign_you_out(host, monkeypatch):
  """If `claude auth status` timing out reported the user as signed out, the
  panel would disable the composer, and the box being typed in would go dead
  and lose focus mid-sentence. A failed *question* must not overwrite a
  good *answer*."""
  host.provider.auth_state = {"loggedIn": True, "authMethod": "claudeai",
                              "answered": True}

  async def cannot_ask() -> Dict[str, Any]:
    return {"loggedIn": False, "answered": False, "reason": "timed out"}

  monkeypatch.setattr(cli_auth, "status", cannot_ask)
  state = asyncio.run(host.refresh_auth(force=True))

  assert state["loggedIn"] is True
  assert state["stale"] == "timed out"


def test_the_cli_actually_saying_no_does_sign_you_out(host, monkeypatch):
  """The other half: a real answer is believed, or signing out never lands."""
  host.provider.auth_state = {"loggedIn": True, "answered": True}

  async def signed_out() -> Dict[str, Any]:
    return {"loggedIn": False, "answered": True, "reason": "not signed in"}

  monkeypatch.setattr(cli_auth, "status", signed_out)
  state = asyncio.run(host.refresh_auth(force=True))

  assert state["loggedIn"] is False


def test_auth_is_not_a_subprocess_per_poll(host, monkeypatch):
  """The GUI polls /api/agent on a timer, and spawning `claude auth status` on
  each call is the load that makes the timeout above likely."""
  calls = []

  async def counted() -> Dict[str, Any]:
    calls.append(1)
    return {"loggedIn": True, "answered": True}

  monkeypatch.setattr(cli_auth, "status", counted)

  async def poll_three_times() -> None:
    for _ in range(3):
      await host.refresh_auth()

  asyncio.run(poll_three_times())
  assert len(calls) == 1


# ── auth: expiring mid-conversation ──────────────────────────────────────
EXPIRED = ("Failed to authenticate. API Error: 401 OAuth access token has "
           "expired. Re-authenticate to continue.")
"""What the CLI says when its sign-in has expired. The whole failure."""


def _never_asks(monkeypatch):
  """`_auth_expired` re-reads the CLI, which is a subprocess these tests do not
  want and, on a machine that *is* signed in, an answer that hides the bug."""
  async def signed_out():
    return {"loggedIn": False, "answered": True, "reason": "not signed in"}
  monkeypatch.setattr(cli_auth, "status", signed_out)


def test_an_expired_sign_in_puts_the_way_back_where_it_failed(host, monkeypatch):
  """When the token expires mid-conversation the provider says so in one
  sentence. Drawn as an ordinary reply, it tells the user to re-authenticate
  and offers nothing to do it with -- in an app that runs `claude auth login`
  from a button. The card is a transcript entry so it sits under the turn it
  killed."""
  _never_asks(monkeypatch)
  host.provider.reply = EXPIRED

  asyncio.run(collected(host, "053", "how did it go?", thread="main"))

  kinds = [e["kind"] for e in spoken(host.transcript("053"))]
  assert kinds == ["user", "text", "auth_expired", "done", "idle"], \
      "the card follows the sentence rather than replacing it"
  card = [e for e in host.transcript("053") if e["kind"] == "auth_expired"][0]
  assert card["says"] == EXPIRED, "in the provider's own words"
  # And the rest of the app has to stop believing the sign-in it had.
  assert any(e["kind"] == "agent.auth" for e in host.published)
  assert host.provider.auth_state["loggedIn"] is False


def test_an_agent_talking_about_the_error_is_not_the_error(host, monkeypatch):
  """An agent asked to read `auth.py` quotes that sentence, and a sign-in card
  in the middle of a working conversation is a bug of its own. The failure is a
  one-line replacement for a turn; a paragraph is somebody discussing it."""
  _never_asks(monkeypatch)
  host.provider.reply = (
      "I read `agents/auth.py`. It watches for the CLI saying "
      + EXPIRED + " and turns that into a card carrying the sign-in flow, "
      "which is the whole feature: a panel that printed the sentence and "
      "stopped would name the problem and point at nothing at all.")

  asyncio.run(collected(host, "053", "what does auth.py do?", thread="main"))

  kinds = [e["kind"] for e in spoken(host.transcript("053"))]
  assert "auth_expired" not in kinds


def test_the_sign_in_card_is_said_once_per_ask(host, monkeypatch):
  """The provider can repeat itself inside one failed turn, and a second card
  is not a second problem. The next failure after you ask again is news."""
  _never_asks(monkeypatch)
  host.provider.reply = EXPIRED

  async def twice() -> None:
    await collected(host, "053", "how did it go?", thread="main")
    # The same sentence again inside the same failed turn.
    session = host.sessions[key("053", "main")]
    await host.provider.say(session.handle, AgentEvent("text", {"text": EXPIRED}))
    await host.provider.say(session.handle, AgentEvent("done", {"reason": "end_turn"}))
    await collected(host, "053", "and now?", thread="main")

  asyncio.run(twice())

  cards = [e for e in host.transcript("053") if e["kind"] == "auth_expired"]
  assert len(cards) == 2, "one per ask, not one per sentence"



# ── the instruction harness ──────────────────────────────────────────────
def test_the_harness_points_at_rlmcp_rather_than_restating_it():
  """rl-mcp documents itself for agents, and those files are the authority.
  Restating their advice here forks it, and a fork goes stale in the direction
  of being confidently wrong -- which is the failure `AGENTS.md` opens by
  describing. So the harness resolves their real paths and says to read them."""
  from rlmcp_studio.agents import harness

  text = harness.instructions()
  assert text and len(text) > 1000, "the harness should not be nearly empty"

  root = harness.rlmcp_root()
  if root is None:
    pytest.skip("rl-mcp is installed without its documentation beside it")

  # Absolute paths, because the agent's working directory is the *task* repo:
  # a relative `docs/tuning.md` resolves to a file that is not there, and an
  # agent that tries once and fails does not try again.
  assert str(root / "AGENTS.md") in text
  assert str(root / "docs" / "tuning.md") in text


def test_every_instruction_file_is_read():
  """Adding a file is adding an instruction. They are read in name order, so
  the numbering is the running order -- and a file that never loads is an
  instruction nobody notices is missing."""
  from rlmcp_studio.agents import harness

  names = [p.name for p in harness.files()]
  assert names == sorted(names), "read in name order"
  assert len(names) >= 4, "the harness should have more than a paragraph in it"

  text = harness.instructions()
  for path in harness.files():
    first = next((l for l in path.read_text().splitlines()
                  if l.startswith("# ")), "")
    assert first.lstrip("# ") in text, f"{path.name} did not make it in"


def test_a_broken_instruction_file_is_not_fatal():
  """One unreadable instruction should cost a paragraph of guidance, not the
  ability to start a conversation at all."""
  from rlmcp_studio.agents.claude import system_prompt

  assert system_prompt(), "there is always a prompt"


def test_the_agent_is_told_where_the_session_is():
  """An agent nobody tells cannot find the session: the record names a
  directory and the studio knows it, so the context carries it."""
  from rlmcp_studio.agents.host import build_context

  text = build_context(
      {"id": "047", "slug": "gate", "task": "T", "session": "/logs/run/rlmcp"},
      {"hypothesis": "h", "records_root": "/records", "code_root": "/repo"})

  assert "/logs/run/rlmcp" in text
  assert "/records" in text and "/repo" in text
  assert "already pinned" in text, "and that it need not go looking"


def test_the_context_says_what_the_task_is_and_where_its_code_lives():
  """The agent is started in the task repository and could find the package by
  looking, but looking is slower than reading, and the intent -- what the task
  is *meant* to do -- is not in the code at all."""
  from rlmcp_studio.agents.host import build_context

  text = build_context(
      {"id": "046", "slug": "ema", "task": "my-tasks-walk"},
      {"task_brief": "walk forward at a commanded velocity",
       "backend": "mjlab", "package": "my_tasks", "code_root": "/repo/tasks"})

  assert "walk forward at a commanded velocity" in text
  assert "mjlab" in text and "my_tasks" in text
  assert "/repo/tasks" in text


def test_the_context_carries_the_numbers_and_says_they_are_a_snapshot():
  """So that orienting does not begin with three tool calls the studio has
  already made -- and so that the agent does not go on quoting a number that
  moved while it was reading it."""
  from rlmcp_studio.agents.host import build_context

  text = build_context(
      {"id": "046", "slug": "ema", "task": "T", "feedback_open": 2},
      {"progress": {"state": "running", "iteration": 4231,
                    "metrics": [("reward", "12.34"), ("success", "0.31")]}})

  assert "4,231" in text and "reward 12.34" in text
  assert "get_training_status" in text, "and where the live answer is"
  assert "2 questions" in text and "answer_feedback" in text


def test_the_context_says_how_to_launch_the_next_run():
  """Launching is the agent's to do, and it could not have worked out how: the
  interpreter is the task repository's rather than the one on its PATH, and a
  run with no `--record-run` writes its metrics onto no record at all."""
  from rlmcp_studio.agents.host import build_context

  text = build_context(
      {"id": "046", "slug": "ema", "task": "T"},
      {"launch": {"command": "/repo/.venv/bin/python -c ... train T "
                             "--record-run RECORD_ID",
                  "cwd": "/repo",
                  "record_command": "/repo/.venv/bin/python -c ... record new"}})

  assert "record new" in text, "the record is opened before the run, always"
  assert "RECORD_ID" in text and "/repo/.venv/bin/python" in text
  assert "detached" in text, "or it dies with the command that started it"
  # The mode change and this section are the same instruction from two ends.
  assert "interrupt" in text.lower()


def test_a_run_with_none_of_that_still_gets_a_context():
  """Every section is optional, and a record with no session, no brief and no
  launcher must not produce a context full of empty headings."""
  from rlmcp_studio.agents.host import build_context

  text = build_context({"id": "003", "slug": "x", "task": "T"}, {})
  assert text.strip() == "Run 003 — x on task T."


def test_the_harness_is_about_the_work_rather_than_the_gui():
  """Putting the studio at the centre -- "you are in a panel, here is what the
  user can see" -- describes the interface, not the job. The
  job is running and tuning RL through rl-mcp, with a person giving feedback,
  keeping the record. The GUI is one paragraph of context inside that."""
  from rlmcp_studio.agents import harness

  text = harness.instructions().lower()
  for idea in ("tune", "diagnose", "record", "feedback"):
    assert idea in text, f"the harness never mentions {idea}"

  # The human-in-the-loop half is the part with no terminal equivalent, so it
  # has to name the mechanism rather than gesture at collaboration.
  assert "get_feedback_timeline" in text and "answer_feedback" in text, \
      "feedback is an input to the work; the harness has to say how to read it"

  # And the loop has to put measuring before changing, which is the rule the
  # records in this system exist to make checkable.
  assert "diagnose" in text.split("change one thing")[0], \
      "diagnosing should come before intervening"


# ── a picture attached to a message ──────────────────────────────────────
TINY_PNG = ("data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def test_a_message_may_be_a_picture_with_no_words(client, records_root):
  """"Look at this" is a complete thing to say about a frame, and requiring a
  sentence with it would be the app being pedantic about a message that is
  already clear. The agent is not signed in here, so this asserts the request
  got *past* validation -- a 400 would mean it never did."""
  answer = client.post("/api/agent/002/message",
                       json={"text": "", "image": TINY_PNG})
  assert answer.status_code != 400, answer.json()


def test_a_message_with_neither_words_nor_a_picture_is_refused(client):
  assert client.post("/api/agent/002/message", json={"text": ""}).status_code == 400


def test_something_that_is_not_an_image_data_url_is_refused(client):
  for bad in ("hello", "data:text/html;base64,PHNjcmlwdD4=", "data:image/png;base64,!!!"):
    answer = client.post("/api/agent/002/message", json={"text": "hi", "image": bad})
    assert answer.status_code == 400, f"{bad!r} was accepted"


def test_an_enormous_image_is_refused_before_it_is_decoded(client):
  """The bound is on the *encoded* string, not the decoded bytes: checking
  after decoding would already have done the allocating, which is the thing
  the limit exists to prevent."""
  from rlmcp_studio.app import MAX_SHOT_CHARS
  huge = "data:image/png;base64," + ("A" * (MAX_SHOT_CHARS + 10))
  answer = client.post("/api/agent/002/message", json={"text": "hi", "image": huge})
  assert answer.status_code == 400
  assert "limit" in answer.json()["error"]


# ── typing over a running turn ───────────────────────────────────────────
def test_a_message_that_interrupts_goes_in_after_the_turn_it_ended():
  """The other half of stopping one: you type *because* it is going wrong.

  Refusing a message into a running turn with 409 -- "press Stop, then send"
  -- is two gestures and a wait for the one thing you wanted. With `interrupt=True` the host ends the turn, waits for it to
  admit it ended, and sends. The order is the whole point: the tail the model
  had already produced, then "You stopped it.", then your correction --
  because a correction *above* the paragraphs it interrupted reads as an
  agent that carried on after being told not to.
  """
  host = AgentHost(_quiet, provider=MidSentence())

  async def go():
    await _working(host)
    await host.send("002", "write the three files instead", interrupt=True)
    await _settled(host)

  asyncio.run(go())
  kinds = [e["kind"] for e in spoken(host.transcript("002"))]
  texts = [e.get("text") for e in spoken(host.transcript("002"))]
  assert host.provider.interrupted == ["002#main"], "the turn was ended, not the session"
  assert "stopped" in kinds, "and the transcript says so"
  assert kinds.index("stopped") < len(kinds) - 1 - kinds[::-1].index("user"), \
      "'You stopped it.' comes before the correction, not after it"
  tail = texts.index("…and forty-one, forty-t")
  assert tail < kinds.index("stopped"), "the tail the model had already produced comes first"
  assert "[Request interrupted by user]" not in texts, \
      "the provider's own interrupt notice is not drawn as the agent waking itself"
  assert [t for k, t in zip(kinds, texts) if k == "user"] == [
      "go", "write the three files instead"]
  assert host.sessions["002#main"].busy is False


def test_interrupting_with_nothing_running_is_an_ordinary_message():
  """`interrupt=True` on an idle conversation must not invent a stop: the flag
  says what to do *if* a turn is running, and the transcript of a quiet
  conversation should not carry a "You stopped it." nobody caused."""
  host = AgentHost(_quiet, provider=FakeProvider())

  asyncio.run(collected(host, "002", "hello", thread="main", interrupt=True))
  kinds = [e["kind"] for e in spoken(host.transcript("002"))]
  assert "stopped" not in kinds
  assert kinds[0] == "user"


def test_interrupting_a_provider_that_cannot_interrupt_still_sends():
  """A provider with no `interrupt` is stopped by closing its session. The
  correction still has to go in -- into a new context, and the transcript
  says so -- rather than failing because the old one is gone."""
  host = AgentHost(_quiet, provider=CannotInterrupt())

  async def go():
    await _working(host)
    await host.send("002", "again, properly", interrupt=True)
    await _settled(host)

  asyncio.run(go())
  entries = spoken(host.transcript("002"))
  kinds = [e["kind"] for e in entries]
  assert host.provider.closed == ["002#main"], "it fell back to closing the session"
  assert "restart" in kinds, "the break is marked"
  assert [e["text"] for e in entries if e["kind"] == "user"] == ["go", "again, properly"]
  assert kinds.index("restart") < kinds.index("user", 1), \
      "and marked above the message that starts the new context"
  assert host.sessions["002#main"].busy is False


def test_a_message_that_does_not_ask_to_interrupt_is_still_refused():
  """The flag is opt-in. A Look button, a frame annotation, a script -- none
  of them mean "and throw away what the agent is doing", so the 409 stays
  for every caller that did not say so."""
  host = AgentHost(_quiet, provider=SlowProvider())

  async def go():
    await _working(host)
    with pytest.raises(RuntimeError):
      await host.send("002", "second")

  asyncio.run(go())
  assert host.provider.interrupted == []


# ── more than one provider ───────────────────────────────────────────────
class OtherProvider(FakeProvider):
  name = "other"
  label = "The Other One"

  def __init__(self):
    super().__init__()
    self.reply = "the other one looked"


def _two():
  return AgentHost(_quiet, providers={"fake": FakeProvider(), "other": OtherProvider()},
                   current="fake")


def test_the_host_lists_every_provider_and_which_is_current():
  host = _two()
  rows = host.status()["providers"]
  assert [r["name"] for r in rows] == ["fake", "other"]
  assert [r["current"] for r in rows] == [True, False]
  assert rows[1]["label"] == "The Other One", "the label is the provider's own"
  assert host.status()["provider"] == "fake"


def test_switching_provider_applies_to_the_next_conversation_only():
  """An open conversation's context lives inside one provider's session, so
  it keeps that provider; the switch is for conversations started from now
  on. Silently answering an open conversation from a different model with
  none of its memory is the failure this guards."""
  host = _two()

  async def go():
    await collected(host, "002", "first", thread="main")
    host.choose_provider("other")
    await collected(host, "002", "second", thread="main")
    await collected(host, "003", "fresh", thread="main")

  asyncio.run(go())
  assert [s["text"] for s in host.providers["fake"].sent] == ["first", "second"], \
      "the open conversation stayed with the provider it started on"
  assert [s["text"] for s in host.providers["other"].sent] == ["fresh"], \
      "the new one went to the chosen provider"
  assert host.status()["provider"] == "other"


def test_a_provider_nobody_registered_is_refused():
  host = _two()
  with pytest.raises(ValueError):
    host.choose_provider("gpt")


def test_a_conversation_reopened_after_a_break_takes_the_current_provider():
  """A stop that had to close the session, or a restart, starts a new
  context -- and a new context is the current provider's, because nothing
  of the old one survives to be kept."""
  host = AgentHost(_quiet, providers={"cannot": CannotInterrupt(),
                                      "other": OtherProvider()}, current="cannot")

  async def go():
    await _working(host)
    await host.stop("002")
    await _settled(host)
    host.choose_provider("other")
    await collected(host, "002", "carry on", thread="main")

  asyncio.run(go())
  assert [s["text"] for s in host.providers["other"].sent] == ["carry on"]
  assert host.sessions["002#main"].provider is host.providers["other"]


# ── a conversation too large to send ─────────────────────────────────────
DROPPED = "API Error: Connection dropped (ECONNRESET)"
"""What the CLI says once its retries against a reset connection run out."""


def test_a_dropped_connection_says_why_and_offers_a_fresh_start(host):
  """A conversation with enough robot frames crosses the API's 32 MB request
  cap, every request is reset, and the CLI's one line gives no reason and
  nothing to do. The card follows that line, once."""
  host.provider.reply = DROPPED

  asyncio.run(collected(host, "053", "record the terrains", thread="main"))

  kinds = [e["kind"] for e in spoken(host.transcript("053"))]
  assert kinds == ["user", "text", "heavy", "done", "idle"]
  card = [e for e in host.transcript("053") if e["kind"] == "heavy"][0]
  assert card["dropped"] is True
  assert card["limit_bytes"] == 32 * 1024 * 1024


def test_images_that_add_up_warn_before_the_cap_and_only_once(host):
  """Every image a tool returns is resent with each later request. The studio
  counts them off the transcript and warns while there is still room -- not on
  every turn after, which would be a card nobody reads."""
  from rlmcp_studio.agents import host as host_module

  frame = host_module.HEAVY_BYTES // 3 + 1

  async def send(session_id, text, images=None):
    say = host.provider.say
    await say(session_id, AgentEvent("context", {"tokens": 120_000}))
    await say(session_id, AgentEvent("tool_result", {
        "id": "t", "content": "[image]", "images": 1, "image_bytes": frame}))
    await say(session_id, AgentEvent("text", {"text": "filmed one"}))
    await say(session_id, AgentEvent("done", {"reason": "end_turn"}))

  host.provider.send = send
  for _ in range(4):
    asyncio.run(collected(host, "053", "another", thread="main"))

  entries = host.transcript("053")
  cards = [e for e in entries if e["kind"] == "heavy"]
  assert len(cards) == 1, "once per conversation"
  assert cards[0]["dropped"] is False and cards[0]["images"] == 3
  assert not any(e["kind"] == "context" for e in entries), "a measurement, not a line"
  done = [e for e in entries if e["kind"] == "done"][-1]
  assert done["usage"]["context_tokens"] == 120_000, "the request's size rides on done"


def test_a_tool_result_carries_what_its_images_weigh():
  from claude_agent_sdk import ToolResultBlock, UserMessage

  from rlmcp_studio.agents.claude import _translate

  events = _translate(UserMessage(content=[ToolResultBlock(
      tool_use_id="toolu_1",
      content=[{"type": "text", "text": "frame"},
               {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": "A" * 1000}}])]))
  assert events[0].data["images"] == 1 and events[0].data["image_bytes"] == 1000
  plain = _translate(UserMessage(content=[ToolResultBlock(tool_use_id="t", content="ok")]))
  assert "images" not in plain[0].data, "nothing added when there is nothing"


def test_the_claude_models_say_how_much_room_they_have():
  from rlmcp_studio.agents.claude import MODELS

  rooms = {m["id"]: m["context"] for m in MODELS}
  assert rooms["claude-opus-5-5"] == 1_000_000
  assert rooms["claude-haiku-4-5-20251001"] == 200_000
