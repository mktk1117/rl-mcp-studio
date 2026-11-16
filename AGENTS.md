# Working in this repo (agent guide)

rl-mcp-studio is a local web GUI over [rl-mcp](https://github.com/mktk1117/rl-mcp):
watch, steer and record RL training runs, with an AI agent in a panel beside
the run. This file is for an agent (or a person) **changing the studio**. To
*use* it, read the [README](README.md).

This file is the canonical guide. `CLAUDE.md` is a symlink to it, so every
assistant finds the same text; nothing here assumes a particular agent product.

## Rule zero: the studio never re-implements rl-mcp

The studio is a view and a set of buttons over rl-mcp. Metrics come from
`rlmcp.session`, commands go through `Session.call`, verdicts and headline
metrics from `rlmcp.records`, task listings from `rlmcp tasks`, gates from
`rlmcp check`. If the studio needs something rl-mcp cannot answer, the fix is a
pull request to rl-mcp, not a parser or a copy here.

The backend imports `rlmcp` and never imports torch or a simulator, so starting
or killing the studio cannot disturb a training run. Anything that must build a
task runs `rlmcp` in the *task repository's* interpreter (`sources/taskenv.py`).

## This is a public, generic codebase

Everything committed here is read by strangers and installed on machines we
have never seen. Before you commit, check your diff against this list:

* **No personal or project-specific content.** No names of people, no private
  project, repository, company or robot names, no task ids from anyone's own
  task packages, no quotes from a user. Examples, fixtures and docs use public
  tasks that ship with mjlab (`Mjlab-Velocity-Flat-Unitree-Go1`,
  `Mjlab-Velocity-Flat-Unitree-G1`, ...) or obviously made-up ones
  (`Example-Task-v0`, `my_tasks`).
* **No absolute paths or machine facts.** Never write a home directory, a
  username, a checkout layout of one machine, a GPU model "on this box", or a
  port "that is taken here". Paths in code come from configuration,
  `$XDG_*_HOME` (`rlmcp_studio/paths.py`) or the package itself
  (`Path(__file__)`). In tests use `tmp_path`; in docs use `~/tasks` or
  `/path/to/...`.
* **No diaries in the tree.** Code comments say *why the code is the way it
  is*, not when it changed, who asked, or what it used to be — that is what
  `git log` and pull requests are for. No dates, no commit hashes, no
  "previously", no "fixed on ...". Keep the reasoning, drop the story.
* **No secrets.** No tokens, keys or account ids, not even expired ones.
  Credentials come from the provider's own sign-in or environment variables.
* **No private links.** No links to chat transcripts, shared artifacts or
  private repositories. Link public docs only.

## Third-party code: depend on it, do not copy it

* Python libraries are **dependencies** in `pyproject.toml`. Add one only when
  it clearly earns its weight; prefer the standard library and what is already
  depended on. A heavy or niche dependency goes in an optional extra (see
  `antigravity`) and must be imported lazily.
* Browser libraries are **not committed**. `rlmcp_studio/vendor.py` pins each
  one (npm name, version, sha512 integrity) and fetches it into the wheel at
  build time, or into the user cache for a source checkout. To add or bump
  one, edit that manifest; never copy files into `rlmcp_studio/web/`.
* The client has **no build step and no Node toolchain**: plain ES modules
  under `rlmcp_studio/web/js/`, loaded by the browser as they are.
* Never load anything from a CDN at runtime. An installed studio must work
  offline.
* Anything added must be Apache-2.0 compatible (MIT, BSD, Apache).

## Layout

```
rlmcp_studio/
  __main__.py      the `rlmcp-studio` command
  app.py           create_app(): builds the context and mounts the routes in order
  context.py       Studio: the shared objects every route module is handed
  hub.py           the event hub behind the /ws feed
  briefs.py        what an agent is told about a task or a run
  config.py        where settings come from: flag, environment, settings file, default
  doctor.py        `rlmcp-studio --check`
  guard.py         the app's access token and origin check
  vendor.py        pinned browser libraries (see above)
  routes/          HTTP and websocket routes, one module per area
  sources/         read-only adapters over rl-mcp, records, tensorboard, git
  agents/          the agent panel: providers (Claude, Codex, Antigravity), host, policy
  runners/         launching runs, locally or on a remote `rlmcp hostd`
  terminals/       the terminal drawer's pty
  instructions/    the system prompt given to the studio's agents
  web/             the client: index.html, js/, css/, dev/ (test pages)
tests/             pytest; no network, no GPU, no simulator
```

Keep modules small enough to read in one sitting. When a file grows past
roughly 800 lines, split it by responsibility rather than adding to it.

## Developing

```bash
git clone https://github.com/mktk1117/rl-mcp
git clone https://github.com/mktk1117/rl-mcp-studio
cd rl-mcp-studio
uv sync --all-extras          # plain `uv sync` drops the dev extra
uv run rlmcp-studio           # http://127.0.0.1:8730
uv run pytest
uvx ruff@0.16.4 check rlmcp_studio tests setup.py
```

`pyproject.toml` resolves rl-mcp to a sibling checkout (`../rl-mcp`) for
development, so a git worktree must also sit beside `rl-mcp`. A built wheel
depends on rl-mcp from PyPI.

* Python indents by two spaces (ruff enforces the defects, not a format).
* The server sends the client with `Cache-Control: no-store`, so a JS or CSS
  change needs only a browser reload. A Python change needs a restart, and a
  tab open across a restart reloads itself once (the access token is per
  process). When a client change "does not work", check that the server
  process is newer than the Python it depends on.
* Tests must pass with no network, no GPU and no agent credentials. Never let
  a test write the real settings file: `tests/conftest.py` points
  `RLMCP_STUDIO_SETTINGS` and the XDG directories at a temporary directory.

## Verify by looking

Several real bugs here passed every DOM assertion and only showed in a
screenshot: a menu clipped by a scrolling parent, a drag handle under a
scrollbar, a layout that fit on one width only. After a visible change, start
a scratch studio with its own `XDG_STATE_HOME`/`XDG_CONFIG_HOME` and a port of
its own, open it in a browser, and look. `web/dev/goto.html?task=&view=&layout=`
seeds a fresh profile with a task, view and layout.

## Commits and pull requests

One logical change per commit, with a message that says what changed and why.
A defect gets an issue, and a regression test that fails before the fix.
