# Architecture

This document describes how rl-mcp-studio is put together: which processes
exist, where data comes from, how the browser stays live, how agents and runs
are hosted, and the rules that keep the codebase small. For installing and
using the studio, see the [README](../README.md); for changing it, see
[AGENTS.md](../AGENTS.md).

## Rule zero: never re-implement rl-mcp

The studio is a view and a set of buttons over
[rl-mcp](https://github.com/mktk1117/rl-mcp). Everything it shows or does maps
to something rl-mcp already answers:

| the studio needs to… | it calls |
| --- | --- |
| read a run's status, metrics or artifacts | `rlmcp.session` |
| send a command to a running trainer | `Session.call` |
| read or write run records, verdicts, feedback | `rlmcp.records` |
| draw the record graph or fold a recipe | `rlmcp.records.graph` / `rlmcp recipe build` |
| list parameters and commands a run offers | the trainer's own `list_parameters` / `list_commands` |
| list tasks, including ones that never ran | `rlmcp tasks` |
| check that a task builds and trains | `rlmcp check` |
| run training | `rlmcp train` (locally) or `rlmcp hostd` (remotely) |

If the studio needs something rl-mcp cannot answer, the fix is a pull request
to rl-mcp, not a parser or a copy here. The same rule keeps task knowledge out
of this tree: no metric name, parameter name, reward term or task id is
written into the studio. Panels render what the run reports.

## Processes

```
 ┌── the studio's machine ──────────────────────────────────────────────────┐
 │                                                                          │
 │  browser ──HTTP + /ws──▶ studio backend (FastAPI, uvicorn)               │
 │                           │  imports rlmcp, never torch or a simulator   │
 │                           │                                              │
 │          ┌────────────────┼──────────────────┬────────────────────┐      │
 │          ▼                ▼                  ▼                    ▼      │
 │   records roots     session dirs      task environment      agent processes
 │   (rlmcp.records)   (rlmcp.session)   (subprocess: rlmcp    (claude / codex /
 │                          ▲             tasks, check, play)   antigravity SDK)
 │                          │ writes                                        │
 │                   trainer process (rlmcp train, torch + simulator)       │
 └──────────────────────────────────────────────────────────────────────────┘
                 │ rl-mcp wire (HTTP, bearer token)
                 ▼
 ┌── a training machine ──────────────────┐
 │  rlmcp hostd  ──▶  trainer processes   │
 └────────────────────────────────────────┘
```

**The studio backend** (`rlmcp-studio`) is one Python process. It serves the
client, answers HTTP reads, pushes live changes over one WebSocket, hosts
agent conversations and launches runs. It imports `rlmcp` but never torch or a
simulator, so starting, restarting or killing it cannot disturb a training
run: it is only ever another reader and writer of the same directories, like
the rl-mcp CLI.

**Trainer processes** are ordinary rl-mcp training runs. They write a
*session directory* (status, metrics, events, artifacts) and read commands
from it between rollout batches. The studio never streams through the
trainer and cannot stall it. A run the studio launched and a run started by
hand in a terminal look the same once they are running; a run with no record
can be adopted into one from the GUI.

**The task environment** is the interpreter that can actually build a task
(`sources/taskenv.py`). Listing tasks, running rl-mcp's gates and holding a
preview all need the simulator, so they run as subprocesses in that
interpreter: a `.venv` in the directory that holds a records root when there
is one, otherwise the studio's own interpreter. A task that crashes takes a
subprocess with it, not the server.

**`rlmcp hostd`** runs on a training machine and exposes its sessions and a
launch endpoint over rl-mcp's HTTP wire. The studio reaches it as a
configured host (see *Runners*). The host keeps no records; the record stays
with the studio and is filled from what the host reports.

**Agent processes** are the vendors' own harnesses (the `claude` CLI driven by
the Claude Agent SDK, `codex app-server`, or the Antigravity SDK's harness),
started per conversation. Each is given rl-mcp's MCP server pinned to the
run's session, plus the studio's own fleet MCP server.

## Where data comes from

The studio has no database. Its sources are:

* **Records roots** — directories of rl-mcp run records: hypothesis,
  config, code stamp, verdict, feedback, media. Read and written through
  `rlmcp.records`. Several roots may be configured; every task found in any of
  them appears in the task switcher.
* **Session directories** — what a live (or finished) trainer writes. Read
  through `rlmcp.session`, either from a local path or from a wire address
  (`http://host/v1/sessions/<key>`) on a remote host.
* **The task registry** — `rlmcp tasks`, run in the task environment and
  cached. A task exists in the GUI because a package registers it *or* because
  runs of it were recorded; a task with no runs yet is a normal state.
* **TensorBoard event files** — the trainer's own logger output, read with a
  small standard-library reader (no TensorBoard process).
* **Git** — the task repository, for code stamps and diffs between runs.
* **Studio-owned state** — the settings file
  (`$XDG_CONFIG_HOME/rlmcp-studio/settings.json`) and
  `$XDG_STATE_HOME/rlmcp-studio/` (agent transcripts, the access token,
  verification reports, materialized code trees). Anything that belongs to a
  run goes into its record, not here.

The adapters over these live in `rlmcp_studio/sources/`. Each wraps an rl-mcp
API and reshapes the answer for the wire; none parses rl-mcp's files itself.

### Configuration resolution

Roots and options resolve in rl-mcp's order: **explicit argument, then
environment, then settings file, then default** (`config.py`). The GUI
changes a root by writing the settings file or passing an explicit value,
never by mutating the environment, so it can say honestly when a shell
variable overrides it. Writes to the settings file are atomic, unknown keys
are preserved, and an unreadable file is reported and ignored rather than
fatal.

## Live updates: the event hub

Everything the client receives live arrives on one WebSocket (`/ws`) as one
envelope (`events.py`):

```jsonc
{ "v": 1, "kind": "metrics.append", "seq": 4812, "scope": "...",
  "host": "...", "run": "...", "at": 1767225600.0, "data": { } }
```

* `kind` is a namespaced string (`status.tick`, `metrics.append`,
  `artifacts.changed`, `records.changed`, `liveness.changed`, `agent.*`, …).
  New kinds are additive; the client dispatches on `kind` and ignores what it
  does not know.
* `seq` is monotonic. The hub keeps a short replay buffer, and a client that
  reconnects sends the last `seq` it saw and receives the gap, so a dropped
  socket never leaves a stale page.
* `host` and `run` name the run as a `(machine, record)` pair, never as a
  path, so local and remote runs are addressed the same way.

Events come from three publishers:

* **The filesystem watcher** (`sources/watcher.py`) watches the records roots
  and the session directory of every open run, and coalesces bursts of writes
  into one event per quiet window.
* **The remote ticker** (`sources/remote_ticks.py`) polls sessions on remote
  hosts and publishes the same events the watcher would, so the client cannot
  tell a remote run from a local one.
* **The backend itself** — command results, liveness changes, launch progress,
  and every agent event.

Events carry little data; on most of them the client re-reads the relevant
HTTP endpoint. HTTP is for cold reads and actions, the socket is for "something
changed".

## The backend layout

```
rlmcp_studio/
  __main__.py      the `rlmcp-studio` command, `--check`, single-instance detection
  app.py           create_app(): builds the shared context and mounts the routes
  routes/          HTTP and WebSocket routes, one module per area
  config.py        resolution of roots, hosts, port, token
  settings.py      the settings file
  events.py        the event envelope
  guard.py         the app-wide credential check
  doctor.py        `--check` and /api/doctor
  api/validate.py  request-body validation
  sources/         read adapters over rl-mcp, records, sessions, tensorboard, git
  agents/          agent providers, the host, the permission policy, fleet MCP server
  instructions/    the agent's briefing, as markdown files
  runners/         launching runs: local, remote, and the code trees they run from
  terminals/       the terminal drawer's pty and its guard
  vendor.py        pinned browser libraries (Monaco, xterm.js)
  web/             the client
```

Routes stay thin: they validate the request (`api/validate.py`), call into
`sources/`, `agents/` or `runners/`, and shape the answer. A request body is
checked before it can reach a durable record, and a bad field is a 400 that
names it.

## Agents

The agent panel hosts a coding agent beside the run. The studio does not
implement an agent loop; it hosts the vendor's harness and gives it tools.

### Providers

A provider (`agents/base.py`) is small: start a session, send messages in,
stream events out, answer permission requests. The tool surface is MCP, so a
provider never needs to know anything about training.

| provider | harness | authentication |
| --- | --- | --- |
| Claude | Claude Agent SDK driving the installed `claude` CLI | the CLI's own sign-in (`claude auth login`, offered as a button), or `ANTHROPIC_API_KEY` |
| Codex | `codex app-server`, one process per conversation | `codex login`, or `OPENAI_API_KEY` |
| Antigravity | the `google-antigravity` Python SDK (optional extra) | `GEMINI_API_KEY`, or Vertex AI credentials |

A provider whose import or binary is missing is reported as unavailable with
the reason; it never prevents the others from working. The studio never
stores or forwards a credential: sign-in runs the vendor's own command and
completes in the vendor's own flow. See [providers.md](providers.md).

### The host

`agents/host.py` owns conversations. A conversation is keyed by run and
thread (`<record>#<thread>`), so one run can hold several independent
conversations, and a task-level conversation exists for work that is not
about one run (writing a new task, the task report). The host:

* builds the per-run context from the record (hypothesis, parent, what
  changed) and attaches rl-mcp's MCP server pinned to that run's session;
* keeps the transcript on disk (`agents/store.py`, one JSONL file per
  conversation, namespaced per set of records roots) so a conversation
  survives a browser reload and a server restart. After a restart the
  transcript is restored and a marker shows that the model's own context
  starts afresh;
* can continue a conversation begun outside the studio, from the Claude Code
  or Codex session files (`agents/external.py`);
* supports interrupting a turn: typing while the agent works interrupts it and
  sends the correction;
* runs a preflight (`agents/preflight.py`) before a session starts, checking
  what will actually be handed to the agent (tools, working directory,
  writable temp space) so the prompt never claims tools that are not there.

### Permissions

`agents/policy.py` is a data table from tool to verdict (`allow` or `ask`),
drawn by effect: reading is free, appending to the record is free, changing a
live run asks, editing files and running shell commands ask, and unknown
tools ask. A permission request becomes an approval card in the panel and the
agent waits for the answer.

The mode moves the line:

* **Manual** — the table as written.
* **Auto** — nothing asks; the person's control is watching and interrupting.
* **Plan** — the agent may read and plan but not change anything.

While a new task is being written, edits inside that task's own directory are
allowed without asking; edits anywhere else still follow the mode. For Claude,
tools that the user's own Claude Code settings already allow never reach the
studio's handler, so the table is a floor, not a ceiling; the panel says so.

### Instructions

`instructions/` holds the studio's briefing as numbered markdown files, read
in name order (`agents/harness.py`). The briefing points the agent at
rl-mcp's own agent documentation rather than restating it. The composer
offers three harness choices: **Studio** (the briefing, the run context and
the tools), **Context only** (the vendor's default prompt plus which run this
is and which tools it has) and **Plain** (only the tools and the working
directory). The task repository's own agent instructions load in all three,
because the vendor CLI reads those. A **Briefing** window shows the prompt as
sent, built from the same function that builds it.

### Fleet tools

Besides rl-mcp's per-run server, each conversation gets the studio's own MCP
server (`agents/fleet_mcp.py`), a small stdio process that turns each tool
into one HTTP call back to the studio. Its tools answer questions about the
whole fleet rather than one run, and launch new runs:

| tool | does |
| --- | --- |
| `studio_capacity` | where a run could start now |
| `studio_runs` | runs of a task, optionally under one parent |
| `studio_launches` | recent launches and their state |
| `studio_launch` | open a record with a hypothesis and launch it, optionally as a patched variant of a base run |
| `studio_cancel` | cancel a launch |
| `studio_compare` | what differs between two runs: code, config, interventions |

Because every tool goes through the same routes the GUI uses, the agent and
the person always see the same fleet.

## Runners

A runner (`runners/base.py`) executes a run and produces a session
directory; that directory is the only join between execution and
observation. Capabilities a runner lacks raise `NotSupported` rather than
returning nothing, the same convention as rl-mcp's adapters.

* **Local** (`runners/local.py`) — a subprocess on the studio's machine,
  running `rlmcp train` in the task environment. The record is opened before
  launch and passed to the trainer, so the run appears in the rail the moment
  it starts. It does not queue: `capacity()` says what is busy and the person
  decides.
* **Remote** (`runners/remote.py`) — a host running `rlmcp hostd`, written
  down in the settings file (`hosts`) or in `$RLMCP_STUDIO_HOSTS`. The record
  stays with the studio and is filled from what the host reports over the
  wire; the host needs no records and stays disposable.
* **Code trees** (`runners/trees.py`) — every launch runs from a stamped
  tree, not from the working directory. The task package is written as a git
  tree (without moving `HEAD` or creating a branch), kept alive under
  `refs/rlmcp/runs/<record>`, and materialized into a directory of its own
  with `git archive`; the trainer runs there with that directory first on
  `PYTHONPATH`. For a remote launch the tree is pushed to the host once and
  the run executes from the host's copy; if that fails, the host runs its own
  checkout and the launch says so. A *variant* is a base tree plus a patch,
  written through a throwaway index, so several variants of one run can be in
  flight without touching anyone's editor.

Because every run records its code, any two runs can be diffed, and the
Code view shows code, config and runtime changes separately.

## The client

The client lives in `rlmcp_studio/web/` and has **no build step**: plain ES
modules loaded by the browser as they are, one stylesheet of design tokens
with a light and a dark scheme, and no Node toolchain. Monaco (the code
editor) and xterm.js (the terminal) are pinned in `vendor.py` and fetched into
the wheel at build time, or into the user cache on a source checkout's first
start; nothing is loaded from a CDN at runtime.

* **State lives outside the layouts** (`js/state.js`). Selected task, run,
  view, open conversation and scroll positions belong to the store; a layout
  is a pure arrangement of components over it. Switching layout loses nothing.
* **A small reconciler** (`js/ui.js`: `el`, `patch`, `morph`) rebuilds the
  DOM from state and keeps the nodes already on screen, so live updates
  several times a second do not reset focus, scroll or a playing video.
* **Components never assume their container** (`js/components.js`): no fixed
  widths, no knowledge of what sits beside them.
* **Transport** (`js/api.js`) wraps fetch and the socket, with reconnect and
  replay from the last `seq`.

**Views** are what the main stage shows, switched like tabs: Build (the task
as it is being written: gates, preview, launch), Overview (what the task is),
Run (the live run: status, frames, clips, steering, annotation), Plots
(published metrics and TensorBoard scalars), Records (every run as cards, a
graph, or a folded recipe), Parameters (live values and their history), Code
(the task package, editing, and diffs between runs), Docs (markdown written
about a run) and Library (task cards).

**Layouts** are three arrangements of those views: **Console** (one column,
the run as a stream), **Focus** (one subject large, with headline numbers) and
**Workbench** (three columns, for comparison work).

Panels degrade visibly: a capability the run or backend lacks is shown in
place with the reason, not hidden and not crashed.

`web/dev/` holds pages for checks a unit test cannot make: an import of every
module, a walk of every view and layout pair looking for layout faults, and a
page that seeds a fresh profile with a task, view and layout.

## Security model

The studio edits code, launches training and opens a shell on the machine it
runs on, so every `/api` route and the live feed require a per-studio token,
delivered to the browser as an HttpOnly cookie and to other local tools as a
bearer token; state-changing browser requests must also come from the
studio's own origin. The terminal socket has a second guard of its own. The
studio binds `127.0.0.1` by default and has one implicit user; it is not a
multi-user service. Details: [security.md](security.md).
