# Security

The studio **edits code, launches training and opens a terminal** on the
machine it runs on. Treat access to it like access to a shell on that
machine. The reasoning behind each check lives beside the code:
`rlmcp_studio/guard.py` (the app), `rlmcp_studio/terminals/guard.py` (the
terminal) and `rlmcp_studio/api/validate.py` (request bodies).

## Bind to localhost

The studio binds `127.0.0.1` by default. Keep it that way, and reach it from
another machine over an SSH tunnel or a VPN rather than by binding `0.0.0.0`.
The guard below stops a drive-by web page and a stranger on the network who
does not have the token; it is one implicit user with one token, not a
multi-user system with accounts.

## The access token

Every route under `/api` and the live feed (`/ws`) require the studio's token
(`guard.py`). The page itself (`/`) and its static files (`/static`) are open;
they are how a browser gets in.

* **Where the token comes from.** `--token` or `$RLMCP_STUDIO_TOKEN` when
  given (useful in a container or a test). Otherwise the studio reuses the
  token the previous studio on the same port left in
  `$XDG_STATE_HOME/rlmcp-studio/token` (`token-<port>` for a port other than
  8730), and mints a new one only when there is none, so an open tab survives
  a restart. Either way the token is written to that file with mode `0600`,
  which is how local tools present it: a second `rlmcp-studio` checking
  whether one is already running, the fleet MCP server the agent runs, and
  `scripts/fresh-install.sh`.
* **Browsers.** `GET /` sets the token as an HttpOnly, `SameSite=Strict`
  cookie named per port (`rlmcp_studio_<port>`, because browsers ignore the
  port when scoping cookies and two studios on one host would otherwise
  overwrite each other's). Same-origin fetches and the WebSocket handshake
  carry it; the page's JavaScript never sees it. A page in another tab cannot
  read it and a cross-site page cannot send it.
* **Origin.** A request that carries an `Origin` header must come from this
  studio's own origin; browsers send one on state-changing requests and on
  WebSocket handshakes, and a page cannot forge it. A state-changing request
  or a WebSocket handshake with no `Origin` is accepted only with a bearer
  token, never on the cookie alone.
* **Everything else** presents `Authorization: Bearer <token>`.
* A refused request is a `401` with one sentence and no hint about which
  check failed. Token comparison is constant-time.

## The terminal

The terminal drawer's socket (`/ws/terminal`) grants a shell, so it has a
second guard of its own (`terminals/guard.py`): the handshake must carry an
`Origin` that is this studio (a missing `Origin` is refused) and a separate,
per-process terminal token, which the client obtains from a guarded `/api`
route. Restarting the studio invalidates it.

## Request bodies

Request bodies are validated before they can reach a record
(`api/validate.py`): types, lengths and paths are checked, and a bad field is
a `400` that names it.

## Agents

The agent in the panel runs with your user's permissions. In **Manual** mode
the studio asks before any tool that changes a live run, edits files or runs
a shell command; in **Auto** mode it asks for nothing, and your control is to
watch and interrupt. For Claude, tools that your own Claude Code settings
already allow run without a prompt regardless of mode. See
[architecture.md](architecture.md#permissions).

Agent credentials are handled only by each vendor's own CLI or SDK; the
studio does not store them. See [providers.md](providers.md).

## Remote hosts

A remote host (`rlmcp hostd`) is reached with the bearer token written in the
studio's settings file (or `$RLMCP_STUDIO_HOSTS`). `rlmcp hostd` refuses to
bind beyond localhost without a token. The settings file therefore contains
secrets; keep it readable only by you.

## Reporting a vulnerability

Please report security issues privately through GitHub's
[security advisory form](https://github.com/mktk1117/rl-mcp-studio/security/advisories/new)
rather than in a public issue.
