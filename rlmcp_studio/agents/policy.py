"""Which tools run freely, and which need you.

A data table, per tool, never ``if tool_name == ...``. That matters for more
than tidiness: this is the file someone reads to answer "what can the agent do
to my training run without asking", and a table can be read in one sitting
where scattered conditionals cannot.

The line is drawn at **effect**, not at risk-sounding names:

* Reading anything is free. Looking at a run cannot damage it, and an agent
  that must ask before measuring is an agent nobody will leave running.
* Appending to the record is free. Feedback and notes are append-only, and
  capturing them is the point of the app -- an approval prompt in front of
  "write down what you observed" would simply stop it being written down.
* Changing the live run asks. Parameters, curriculum, checkpoints, stopping.
* Editing files asks, and running shell commands asks.

Unknown tools ask. A tool this table has never heard of is exactly the case
where a default of "allow" would be wrong.

**The mode moves the line.** The table above is what `manual` means. `auto`
frees everything -- shell commands, file edits, and changes to a live run --
and that is a deliberate choice about what this studio is for: the agent runs
the experiment, and the person watches it and interrupts. An agent that must
ask before restarting a run or nudging a weight is an agent doing the work at
the speed of somebody clicking, which is slower than doing it yourself.

The mode has to be read *here* rather than only handed to the SDK, because the
studio always passes a `can_use_tool` handler and a handler that ignores the
mode makes every mode behave like `manual`: "Auto edit" would ask before every
`ls`.

**What this table actually governs.** Every rlmcp tool: the studio's handler is
the only thing that can answer for them, so `ask` here means a prompt in the
panel, always. The built-ins are different, and the difference is not visible
from here: the SDK runs the `claude` CLI with `--setting-sources=user,project,
local` -- deliberately, because the task repo's CLAUDE.md, skills and commands
are most of the reason to use the SDK rather than the raw API -- and a tool the
*user's own* settings already allow never reaches this handler. If those
settings allow `Bash`, the table says ask and it still runs without a prompt.

So the built-in half is a **floor, not a ceiling**: it can add a prompt where
the CLI would not have asked, and it cannot take one away. `describe()` says so
to the UI rather than letting the panel imply a guarantee it does not have.
"""

from __future__ import annotations

import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Literal, Optional, Sequence

Verdict = Literal["allow", "ask"]

RLMCP = "mcp__rlmcp__"

#: Tools that only observe. Free.
OBSERVE = {
    "get_training_status", "list_metrics", "get_metrics", "plot_metrics",
    "get_events", "list_artifacts", "list_parameters", "list_commands",
    "list_sessions", "switch_session", "curriculum_status",
    "take_screenshot", "record_video", "diagnose_motion",
    "record_trace", "plot_joint_trace", "list_checkpoints",
    "get_feedback_timeline",
}

#: Append-only writes to the record. Free, because gating them loses the data.
RECORD_WRITES = {
    "record_feedback", "attach_feedback", "answer_feedback",
    "set_record_headline", "add_note",
}

#: Everything that changes a running experiment. Always asks.
MUTATES_RUN = {
    "set_parameter", "reset_parameters", "reset_environments", "run_command",
    "curriculum_advance", "curriculum_goto", "curriculum_auto",
    "pause_training", "resume_training",
    "save_checkpoint", "rollback_to_checkpoint", "stop_training",
}

#: Built-in tools the SDK provides.
BUILTIN: Dict[str, Verdict] = {
    "Read": "allow", "Glob": "allow", "Grep": "allow",
    "WebFetch": "allow", "WebSearch": "allow", "TodoWrite": "allow",
    "Edit": "ask", "Write": "ask", "NotebookEdit": "ask", "Bash": "ask",
}


#: Built-ins whose effect is confined to a path, so a *scope* can free them.
#: `Bash` is deliberately not here: a command line's effect is not bounded by
#: any argument this table can read.
PATH_SCOPED = {"Edit", "Write", "NotebookEdit"}



#: The modes that stop consulting the table. Two ids, one behaviour: `bypass`
#: is the id of the "No prompts" mode that Auto absorbed, and saved settings
#: can still carry it, so it is accepted as a synonym.
FREE_EVERYTHING = {"auto-edit", "bypass"}


#: Programs whose effect is confined to reading, whatever their arguments.
#:
#: **No mode consults this.** `auto` frees every command without reading
#: it, and `manual` means a prompt per command and keeps one, so the middle
#: answer -- "run it if it provably only reads" -- has nobody left to give it
#: to. It is kept whole rather than deleted because it is the one correct
#: implementation of that question in this codebase, and the mode that wants
#: it is a single line in `verdict`.
READS_ONLY = {
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg",
    "find", "file", "stat", "du", "df", "pwd", "echo", "which", "type",
    "whoami", "id", "date", "uname", "hostname", "uptime", "free", "nproc",
    "printenv", "basename", "dirname", "realpath", "readlink", "diff", "cmp",
    "sort", "uniq", "cut", "tr", "column", "jq", "sed", "git", "tree", "ps",
    "nvidia-smi", "md5sum", "sha1sum", "sha256sum",
}

#: Flags that turn one of the above into something else.
#:
#: `-f` on `tail` writes nothing and is here anyway: it never returns, and a
#: tool call that never returns holds the conversation open for good.
UNREADS: Dict[str, set] = {
    "find": {"-exec", "-execdir", "-ok", "-okdir", "-delete",
             "-fls", "-fprint", "-fprint0", "-fprintf"},
    "tail": {"-f", "--follow"},
    "sed": {"-i", "--in-place"},
    "sort": {"-o", "--output"},
    "nvidia-smi": {"-pm", "-pl", "-e", "-r", "-ac", "-rac", "-lgc", "-rgc",
                   "--gpu-reset", "--persistence-mode", "--applications-clocks"},
}

#: `git` reads in some of its moods and writes in others, and the mood is the
#: subcommand. Listed rather than excluded, because `git log` and `git diff`
#: are how an agent finds out what changed. `branch`, `remote`, `tag` and
#: `stash` are absent: each has a form that deletes something.
GIT_READS = {"status", "log", "diff", "show", "blame", "describe",
             "rev-parse", "rev-list", "ls-files", "ls-tree", "cat-file",
             "shortlog"}

#: Programs that run *another* program. Whatever they run is the thing to
#: judge, and this file cannot judge it from here.
RUNS_SOMETHING_ELSE = {"sudo", "env", "nohup", "xargs", "time", "command",
                       "watch", "timeout", "sh", "bash", "zsh", "ssh"}


def reads_only(command: str) -> bool:
  """Whether this command line can be proven to change nothing.

  Proven, not guessed: everything it cannot parse, and everything it has not
  been told about, is a no. A false no costs one prompt; a false yes runs
  something unasked, so the two are not the same mistake.

  Split by a *shell* lexer rather than by `re.split`, because a naive split
  gets ordinary commands wrong: it reads the `|` inside `grep -rn "a\\|b" .`
  as a pipe, and it reads the second `&` of an `&&` as a backgrounding `&`. A quoted pipe is text, and only a lexer that
  knows about quotes knows which one it is looking at.
  """
  text = (command or "").strip()
  if not text:
    return False
  # Substitution runs a second command inside this one, and in double quotes
  # it runs there too -- so this is checked on the raw line, before quoting is
  # resolved, and costs a prompt on the rare command that merely mentions it.
  if any(bad in text for bad in ("$(", "${", "`")):
    return False
  try:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)
  except ValueError:
    return False                    # an unbalanced quote is not a promise
  if not tokens:
    return False

  segment: list = []
  for token in tokens:
    if token in CHAINS:             # only chains; every link is judged alone
      if not _segment_reads_only(segment):
        return False
      segment = []
      continue
    # Any other operator -- a redirection, a `;`, a `&`, a subshell -- puts
    # the effect somewhere no allowlist of program names can see.
    if token and set(token) <= set("();<>|&"):
      return False
    segment.append(token)
  return _segment_reads_only(segment)


#: Operators that only sequence what is already being judged.
CHAINS = {"&&", "||", "|"}


def _segment_reads_only(tokens: Sequence[str]) -> bool:
  if not tokens:
    return False
  if "=" in tokens[0]:
    return False                    # `VAR=x cmd` -- the program is the next word
  program = PurePosixPath(tokens[0]).name
  if program in RUNS_SOMETHING_ELSE or program not in READS_ONLY:
    return False
  rest = list(tokens[1:])
  # `--output` is the one flag that writes a file from inside a reading
  # program, and enough of them spell it the same way to check it once.
  if any(t == "-o" or t.startswith("--output") for t in rest):
    return False
  banned = UNREADS.get(program, set())
  if any(t in banned or t.split("=")[0] in banned for t in rest):
    return False
  if program == "git":
    return bool(rest) and rest[0] in GIT_READS
  if program == "sed":
    # `w` is sed's write command and it hides inside the script rather than in
    # a flag: `sed -n 'w out.txt'`. Anything carrying one is asked about.
    return not any("w" in t for t in rest)
  return True


def verdict(tool_name: str, args: Optional[Dict[str, Any]] = None,
            free_paths: Sequence[str] = (), mode: str = "manual") -> Verdict:
  """What to do about a tool the agent wants to call.

  ``free_paths`` is how a *task being built* differs from a run being watched.
  Approving every edit to a package that does not exist yet would kill the
  loop the env builder is for -- you would be clicking Allow once a minute at
  the exact moment you want to be reading a reward term. So edits inside the
  task under construction are free, and edits anywhere else still ask.

  Scoped by **path, not by tool**: it is the same `Edit` either way, and a
  table that freed the tool would have freed it everywhere. A scope is only
  ever handed in by whoever knows what is being built.
  """
  # Auto means what it says, and it is described to the user in the words of
  # what it permits rather than as a convenience. This is where it stops
  # consulting the table -- including for a live run, deliberately: driving the
  # experiment is the agent's job here and the person's lever is interruption,
  # not a queue of approvals: the studio is for watching and giving feedback.
  if mode in FREE_EVERYTHING:
    return "allow"
  if tool_name.startswith(RLMCP):
    bare = tool_name[len(RLMCP):]
    if bare in OBSERVE or bare in RECORD_WRITES:
      return "allow"
    if bare in MUTATES_RUN:
      return "ask"                    # a live run asks in every mode but auto
    return "ask"                      # an rlmcp tool we do not recognise
  if free_paths and tool_name in PATH_SCOPED and _inside(args or {}, free_paths):
    return "allow"
  return BUILTIN.get(tool_name, "ask")


def _inside(args: Dict[str, Any], free_paths: Sequence[str]) -> bool:
  """Whether every path this call names is under one of the free roots.

  Every one, not any: a call that touches one file inside the scope and one
  outside is a call that leaves the scope, and the strict reading is the only
  one that keeps "inside the task is free" true.
  """
  named = [str(args.get(key)) for key in ("file_path", "path", "notebook_path")
           if args.get(key)]
  if not named:
    return False
  try:
    roots = [Path(root).expanduser().resolve() for root in free_paths if root]
  except OSError:
    return False
  for value in named:
    try:
      target = Path(value).expanduser().resolve()
    except OSError:
      return False
    if not any(target == root or root in target.parents for root in roots):
      return False
  return True


def describe(mode: str = "manual") -> Dict[str, Any]:
  """The table, for the UI to show. A policy nobody can read is not a policy.

  Answered *for the mode in force*: a panel listing `Edit` under "asks first"
  while Auto edit is editing without asking is the same broken promise the
  caveat below exists to avoid.
  """
  if mode in FREE_EVERYTHING:
    return {
        "allow": sorted([f"{RLMCP}{t}" for t in
                         OBSERVE | RECORD_WRITES | MUTATES_RUN] + list(BUILTIN)),
        "ask": [],
        "default": "allow",
        "scoped": [],
        "mode": mode,
        "caveat": ("Auto does not consult this table at all. Everything the "
                   "agent asks for runs: shell commands, file edits, and "
                   "changes to a live run — parameters, curriculum, "
                   "checkpoints, stopping. Interrupt it if you disagree; it "
                   "will not wait to be let through. Pick Manual to be asked "
                   "first."),
    }
  return {
      "allow": sorted([f"{RLMCP}{t}" for t in OBSERVE | RECORD_WRITES]
                      + [t for t, v in BUILTIN.items() if v == "allow"]),
      "ask": sorted([f"{RLMCP}{t}" for t in MUTATES_RUN]
                    + [t for t, v in BUILTIN.items()
                       if v == "ask"]),
      "default": "ask",
      "mode": mode,
      # The one exception, and it is a scope rather than a tool: see `verdict`.
      "scoped": sorted(PATH_SCOPED),
      # Said out loud, because a panel listing `Bash` under "asks first" while
      # Bash runs without asking is a promise the studio cannot keep.
      "caveat": ("The rlmcp tools always prompt here. Built-in tools are "
                 "governed by your own claude settings first: one your "
                 "settings already allow will not prompt."),
  }
