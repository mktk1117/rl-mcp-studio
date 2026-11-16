"""What the agent is actually handed, checked against what it is told.

The instruction harness says the rlmcp server is attached and pinned to this
run, and the prompt then lists two dozen tool names. That sentence is written
once and is true most of the time. The *other* half -- the MCP configuration --
is composed per conversation, and `AgentHost._mcp_for` returns `{}` whenever
there is no session directory to pin to. A run that has never been launched has
none. So an agent opened on one would come up with no tools at all, holding a
prompt that insists on two dozen, and no way to tell which half is wrong short
of reading its own process's command line and diffing it against a sibling
session's.

That is the failure this module is for. It is asked once, at the moment a
session is about to start, and it answers from **what will actually be passed
to the SDK** rather than from what the studio meant to pass. Two things use the
answer:

* the prompt, which stops claiming the tools when they are not there and says
  instead what is missing and why (`Report.prompt_block`);
* the transcript, which says the same thing on screen before the person types
  their first question (`Report.notice`).

The rule for what belongs here: a fact the studio already knows and the agent
can only discover by wasting a turn. Nothing in this module opens a socket or
starts a process -- it is `is_dir`, `access`, a look at the argv, and one
nine-byte write to a temporary file. A preflight that can hang is a preflight
somebody turns off, and none of those can.

That last one is the exception that proves the rule, and it is here because
`access` gave the wrong answer: see `_temp_dir_takes_a_write`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

BLOCKING = "blocking"
"""The agent will not have the tools. Saying anything else would be a lie."""

DEGRADED = "degraded"
"""The tools attach, but something about them is wrong and will bite later."""


@dataclass
class Finding:
  """One thing that is not the way the prompt would otherwise claim."""

  id: str
  """Stable, so a test and a stylesheet can name it: ``no-session``."""

  severity: str
  """:data:`BLOCKING` or :data:`DEGRADED`."""

  says: str
  """One line, for the person reading the pane.

  Plain prose, deliberately: these three strings are read twice, once by the
  agent in a markdown prompt and once by a person in the agent pane, which
  renders them as text. Emphasis written for the first shows up as literal
  asterisks in the second, and a note about honesty that looks like a bug is
  not a good note. The framing around them keeps the markdown.
  """

  because: str
  """The mechanism. Written for whoever has to fix it, agent or human."""

  fix: str = ""
  """What makes it go away, when there is a single answer."""

  def as_dict(self) -> Dict[str, Any]:
    return {"id": self.id, "severity": self.severity, "says": self.says,
            "because": self.because, "fix": self.fix}


@dataclass
class Report:
  """The answer, in the two shapes anything needs it."""

  attached: bool
  """Whether the agent will really have `mcp__rlmcp__*` tools.

  Not "was a server configured": a configured server whose command the studio's
  machine cannot run is the same absence with a longer explanation, and the prompt must
  not claim tools in either case.
  """

  findings: List[Finding] = field(default_factory=list)
  server: str = ""
  session_dir: str = ""
  records_root: str = ""
  studio: bool = False
  """Whether the studio's own fleet server (`agents/fleet_mcp.py`) is attached
  beside rlmcp's -- which it is whenever the studio knows its own URL, session
  or no session, because choosing the next run is what a conversation with no
  run yet is for."""

  @property
  def blocking(self) -> List[Finding]:
    return [f for f in self.findings if f.severity == BLOCKING]

  @property
  def severity(self) -> str:
    """The worst thing in it, or ``ok``."""
    if self.blocking:
      return BLOCKING
    return DEGRADED if self.findings else "ok"

  def notice(self) -> Dict[str, Any]:
    """The transcript entry, said before the conversation starts.

    On screen rather than only in a log, because the person watching is the one
    who can do something about it -- launching the run is a button they have
    and the agent does not.
    """
    return {
        "text": (self.blocking[0].says if self.blocking
                 else self.findings[0].says if self.findings
                 else "Tools attached."),
        "severity": self.severity,
        "attached": self.attached,
        "findings": [f.as_dict() for f in self.findings],
    }

  def prompt_block(self) -> str:
    """What goes in the instruction file's `{{tools_attached}}` placeholder.

    Assembled here rather than in `instructions/30-the-tools.md` because it is
    the one paragraph in the harness whose truth changes per conversation. The
    rest of that file stays markdown that anybody can edit after watching the
    agent get something wrong; this is the sentence that has to be computed.
    """
    if not self.attached:
      # Every finding, not only the blocking ones. Listing only
      # `self.blocking` would tell a conversation with no tools nothing about
      # anything *else* that was wrong with it -- and the two travel together
      # more often than not, because the machine that has just broken your
      # shell is the machine that could not start your run either. A
      # conversation missing both would get a careful paragraph about the tools
      # and silence about the shell, and spend the turn working the second half
      # out by hand. `findings` is already blocking-first; see `check`.
      reasons = "\n".join(_bullet(f) for f in self.findings)
      return (
          "**There are no `mcp__rlmcp__` tools in this conversation, and that "
          "is expected rather than broken.** The studio checked before starting "
          "you, and here is what it found:\n\n"
          + (reasons or "* No server was configured for this conversation.")
          + "\n\n"
          "So: do not go looking for them with a tool search \u2014 they are "
          "absent, not deferred. Do not substitute `Bash` for them and do not "
          "reason from memory about what a run is doing; either is how a "
          "confident wrong answer gets written into a record. Say plainly what "
          "you cannot see, answer what you can from files and from what you "
          "were told above, and tell the person what would attach the tools."
          + (("\n\n" + STUDIO_ATTACHED) if self.studio else ""))
    fleet = ("\n\n" + STUDIO_ATTACHED) if self.studio else ""
    if not self.findings:
      return ATTACHED + fleet
    caveats = "\n".join(_bullet(f) for f in self.findings)
    return (ATTACHED + "\n\nWhat the studio already knows about this "
            "attachment, so you do not have to find it out by being wrong:\n\n"
            + caveats + fleet)


def _bullet(finding: "Finding") -> str:
  """One finding, as the agent should read it: claim, mechanism, what to do."""
  return "* " + " ".join(p for p in (finding.says, finding.because, finding.fix) if p)


ATTACHED = (
    "The rlmcp MCP server **is** attached to this conversation and pinned to "
    "the run it is about — the studio verified that before starting you, and "
    "it is not a figure of speech. Its tools are named `mcp__rlmcp__*`.")
"""The true version of the claim, said once and used only when it is true."""


STUDIO_ATTACHED = (
    "The studio's fleet server **is** attached too, as `mcp__studio__*`: "
    "`studio_runs` for what has been tried, `studio_capacity` for what is "
    "training where, `studio_compare` for what differed, and `studio_launch` "
    "to open a record and train -- with `base` and `patch` for a variant of an "
    "existing run, from a copy of its stamped tree. These need no session; "
    "they are how the next run gets chosen and started.")
"""Said whenever the fleet server is in the spec, with or without rlmcp's."""


TOO_LATE = (
    "Launching it attaches the server to the next conversation opened on this "
    "run; this one keeps what it was started with, so say so rather than "
    "waiting for tools that will not arrive here.")
"""What to do, said to a conversation that has already started."""

NOT_YET = (
    "Launch it — or start the task's preview — and the tools are there when "
    "you open a conversation here.")
"""And to a pane previewing what a conversation *would* get, which is a
different sentence: there is no "this conversation" to keep anything yet, and
telling somebody looking at an empty pane that it is too late is nonsense."""


def check(spec: Any, mcp_command: str = "",
          prospective: bool = False) -> Report:
  """Everything knowable about this session's tools, without starting it.

  Takes the :class:`~rlmcp_studio.agents.base.SessionSpec` rather than the
  arguments it was built from, because the spec is what the provider actually
  passes to the SDK, and the point of a preflight is to check the thing that
  ships and not the intention behind it.

  Pure, so both callers can ask independently: the host asks in order to say
  something in the transcript, the provider asks in order to write the prompt.
  Duplicating the call is cheaper than plumbing the answer between them, and it
  keeps a provider used without a host honest.

  ``prospective`` means nobody has started this conversation: the studio is
  previewing what one *would* get, which changes only what there is to do about
  it. See :data:`NOT_YET`.
  """
  servers = dict(getattr(spec, "mcp_servers", None) or {})
  rlmcp = dict(servers.get("rlmcp") or {})
  studio = bool(servers.get("studio"))
  args = [str(a) for a in (rlmcp.get("args") or [])]
  command = str(rlmcp.get("command") or "") or (mcp_command if rlmcp else "")
  session_dir = _after(args, "--session")
  records_root = _after(args, "--records-root")

  findings: List[Finding] = []
  attached = bool(rlmcp)

  if not rlmcp:
    attached = False
    findings.append(Finding(
        "no-session", BLOCKING,
        "No rlmcp tools: this conversation was opened with no session to pin "
        "them to.",
        "The studio attaches rl-mcp's server to a session directory, and there "
        "is none — which means nothing is running yet for what this "
        "conversation is about: a run that has never been launched, or a task "
        "whose preview is not up.",
        NOT_YET if prospective else TOO_LATE))
  elif not _runnable(command):
    attached = False
    findings.append(Finding(
        "server-missing", BLOCKING,
        f"No rlmcp tools: {command or 'rlmcp-server'} is not something the "
        "studio's machine can run.",
        "The SDK starts that server as a subprocess and gives up with one "
        "line — ENOENT: Executable not found in $PATH. uv tool install "
        "--editable links only rlmcp-studio onto $PATH, so rlmcp-server can "
        "sit beside the interpreter and nowhere which will look.",
        "Somebody has to install rl-mcp into the environment the studio runs "
        "in; you cannot fix this from inside the conversation."))

  if rlmcp and session_dir and not Path(session_dir).is_dir():
    findings.append(Finding(
        "session-gone", DEGRADED,
        "The tools are pinned to a session directory that is not there.",
        f"The server was given --session {session_dir}, and nothing exists at "
        "that path, so it has nothing to read: expect the calls to attach and "
        "then fail.",
        "Check the run really is where its record says it is before reading "
        "anything into an empty answer."))

  if rlmcp and not records_root:
    findings.append(Finding(
        "no-records-root", DEGRADED,
        "The record tools are not rooted at a records store.",
        "A record tool takes a bare id — 002 — and an unrooted server resolves "
        "it the CLI's way: $RLMCP_RECORDS, then ./records under its own "
        "working directory. On a machine with two stores that can be somebody "
        "else's 002.",
        "Say the id you mean and check what comes back is the run you are "
        "looking at."))

  why = _temp_dir_takes_a_write()
  if why:
    temp = tempfile.gettempdir()
    findings.append(Finding(
        "tmp-unwritable", DEGRADED,
        "Shell commands do not work in this conversation: nothing can be "
        f"written to {temp}.",
        "That is where the CLI puts the files a Bash call captures its output "
        "into, so the capture fails before the output reaches you, and the "
        "call comes back as a bare exit 1 with nothing in it — whatever the "
        f"command was, including one that cannot fail. The system says: {why}.",
        "Nothing inside the conversation fixes it; somebody has to free space "
        f"in {temp}. Until then read files with the Read tool, and do not read "
        "an empty exit 1 as an answer about the run — it is this, and not the "
        "thing you asked about."))

  cwd = str(getattr(spec, "cwd", "") or "")
  if cwd and not Path(cwd).is_dir():
    findings.append(Finding(
        "cwd-missing", DEGRADED,
        f"The working directory this conversation was given does not exist: {cwd}.",
        "Your shell and your file tools will start somewhere else, so a "
        "relative path in anything you were told above will not resolve.",
        "Use absolute paths, and say that the directory is missing."))

  return Report(attached=attached, findings=findings, server=command,
                session_dir=session_dir, records_root=records_root,
                studio=studio)


def arrived(session_dir: str = "") -> Report:
  """The other half of `no-session`, and it needed saying separately.

  A conversation is started once and keeps the spec it was started with: the
  host rebuilds the spec only while `session.started` is false. So the run
  named in a toolless conversation can be launched, come up, and start
  training, and *that* conversation still has no tools -- and now the reason on
  screen is stale as well, which is worse than the original lie because the
  screen beside it shows a run that is plainly running.

  The studio is the only thing that can notice, since it holds both halves: the
  conversation's spec and the session directory that now exists. So it says so,
  once, and names the one thing that fixes it.
  """
  return Report(attached=False, session_dir=session_dir, findings=[Finding(
      "tools-arrived", DEGRADED,
      "This run is up now, and this conversation still has no rlmcp tools.",
      "It was started before there was a session to pin them to, and a "
      "conversation keeps the tools it was opened with — the studio attaches "
      "the server when the session is created and cannot add one to a "
      "conversation already running.",
      "Open a new conversation on this run and it will have them; this one is "
      "still the right place to read what was already said.")])


def _after(args: List[str], flag: str) -> str:
  """The value of ``--flag value`` in an argv, or empty."""
  try:
    return args[args.index(flag) + 1]
  except (ValueError, IndexError):
    return ""


def _runnable(command: str) -> bool:
  """Whether the studio's machine can start that command.

  A bare name goes through `$PATH`; anything with a separator in it is a path
  and is asked directly. `_rlmcp_server()` falls back to the literal string
  `rlmcp-server` when it finds nothing, so "the command is a name that resolves
  to nothing" is a real state the studio produces and not a hypothetical.
  """
  if not command:
    return False
  if os.sep in command or (os.altsep and os.altsep in command):
    path = Path(command)
    return path.is_file() and os.access(path, os.X_OK)
  return shutil.which(command) is not None


def _temp_dir_takes_a_write() -> str:
  """Empty when the temporary directory accepts a write, else why it does not.

  `os.access(temp, os.W_OK)` is the obvious way to ask and it is the wrong one,
  which is the entire reason this is a function with a paragraph attached. The
  directory's permissions need not be the problem: when `/tmp` is a tmpfs
  mounted with `usrquota` and the quota fills up, every write comes back
  `EDQUOT` while `access` goes on cheerfully answering yes.

  What it costs the agent is out of all proportion to how small it is. The CLI
  runs a shell command by capturing its output through a file in here, so with
  the directory unwritable *every* `Bash` call fails identically -- exit 1, no
  stdout, no stderr, no message -- and it fails that way for `ls`, for `true`,
  for anything. There is nothing in that result to read, so the agent reads it
  as being about whatever it asked: it says the run is not there, or the file
  is not there, or the training is not running, and none of that was ever
  checked. A whole turn of confident wrong answers, from a full disk.

  So this asks the only question with a reliable answer -- will it take a write
  -- by writing. Nine bytes to a file that is removed on close: no subprocess,
  no socket, and nothing that can block. The module's rule is that a preflight
  cannot hang, not that it cannot touch a disk.
  """
  try:
    with tempfile.NamedTemporaryFile(prefix="rlmcp-preflight-") as probe:
      probe.write(b"preflight")
      probe.flush()
  except OSError as exc:
    # `strerror` and not `str(exc)`: the latter carries the probe's own random
    # file name, which is neither stable enough to test nor any use to read.
    return exc.strerror or exc.__class__.__name__
  return ""


def defaults() -> Dict[str, str]:
  """What the harness substitutes when nobody has run a check.

  A prompt assembled outside a session — the fallback, a test, a preview of the
  instructions in the GUI — has no spec to check, and must therefore claim
  nothing. It says what is true in every case instead: that the tools may or
  may not be there, that their names are the way to tell, and what to do if
  they are not.
  """
  return {"tools_attached": UNCHECKED}


UNCHECKED = (
    "The rlmcp MCP server is normally attached to this conversation and pinned "
    "to the run it is about; its tools are named `mcp__rlmcp__*`.\n\n"
    "**If you have no `mcp__rlmcp__` tools at all**, say so plainly and stop "
    "guessing. It is not a missing capability and it is not something to work "
    "around: it means the studio started you without a server, which it does "
    "when there is no session to pin one to — a run that was never launched, "
    "most often. Do not substitute `Bash` for the tools and do not reason from "
    "memory about what the run is doing; either is how a confident wrong answer "
    "gets written into a record.")
"""The honest text for a prompt built with nothing to check against."""
