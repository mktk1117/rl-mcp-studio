# Agent providers

The studio's agent panel hosts one of three coding agents. Each is the
vendor's own harness, started by the studio as a subprocess or loaded as the
vendor's SDK, and signed in through the vendor's own flow. The studio never
sees, stores or forwards a credential: it either runs the vendor's login
command and shows you the URL it prints, or it relies on an API key you have
set in the environment.

You are responsible for using each provider in line with that provider's
terms of service and usage policies, including any rules about which account
types may be used from third-party tools and how usage is billed.

| provider | how the studio drives it | ways to authenticate | install |
| --- | --- | --- | --- |
| **Claude** | the Claude Agent SDK, which runs the installed `claude` CLI | sign in from the panel (runs `claude auth login`), or `ANTHROPIC_API_KEY` | Claude Code's `claude` CLI on `PATH`; the SDK is a dependency of the studio |
| **Codex** | `codex app-server`, one process per conversation | `codex login`, or `OPENAI_API_KEY` | the `codex` CLI |
| **Antigravity** | Google's `google-antigravity` Python SDK | `GEMINI_API_KEY`, or Vertex AI credentials | the studio's `antigravity` extra |

Any subset works. A provider that is not installed or not signed in is shown
as unavailable with the reason, and the others keep working. `rlmcp-studio
--check` reports which credentials it can see.

## Claude

The studio uses the Claude Agent SDK, which starts the `claude` CLI as
installed; the CLI must be on `PATH`.

* **Sign in from the panel.** The *Sign in* button runs `claude auth login`,
  shows the URL it prints, and passes the one-time code you get back to that
  same local process. The sign-in completes in Anthropic's own browser flow;
  the studio keeps no token. Running `claude auth login` yourself in a
  terminal works too.
* **API key.** Set `ANTHROPIC_API_KEY` in the environment the studio runs in.

The panel offers Claude's models and three permission modes (Manual, Auto,
Plan); see [architecture.md](architecture.md#permissions).

Anthropic's documentation for Claude Code, including its legal and
compliance page: <https://code.claude.com/docs/en/legal-and-compliance>.

## Codex

The studio starts `codex app-server` for each conversation and talks to it
over its JSON-RPC interface. It supplies the run's rl-mcp MCP server as
command-line configuration overrides and disables, for that process only, any
MCP servers declared in your own `~/.codex/config.toml`; nothing is written to
that file. Secrets for the studio's servers are passed through the process
environment, never on the command line.

* **Which `codex`.** `$RLMCP_STUDIO_CODEX` if set; otherwise the binary
  bundled with the ChatGPT desktop app, when installed; otherwise `codex` on
  `PATH` (for example from `npm install -g @openai/codex`). A snap-packaged
  `codex` is confined and cannot start the MCP server, so the panel reports
  it as unusable.
* **Sign in.** Run `codex login` once, or sign in through the ChatGPT desktop
  app, which shares its sign-in with the bundled binary. The studio asks
  `codex login status` rather than reading credential files.
* **API key.** Set `OPENAI_API_KEY` (or `CODEX_ACCESS_TOKEN`) in the
  studio's environment.

Codex documentation: <https://github.com/openai/codex>.

## Antigravity

The Antigravity provider uses Google's
[`google-antigravity`](https://pypi.org/project/google-antigravity/) Python
SDK. Its wheel is large, so it is an optional extra:

```bash
uv tool install "rl-mcp-studio[antigravity]"     # once released; or from a wheel/checkout
```

The SDK reads its credentials from the environment:

* `GEMINI_API_KEY`, or
* Vertex AI: `GOOGLE_GENAI_USE_VERTEXAI=1` with `GOOGLE_CLOUD_PROJECT` (after
  `gcloud auth application-default login`) or `GOOGLE_API_KEY`.

There is no sign-in button; after setting the variables, restart the studio
and use *Check again* in the panel.

Signing in to Antigravity with a Google account (through the `agy` CLI or the
Antigravity app) is **not supported**. Only the SDK with an API key or Vertex
credentials is.

Gemini API terms: <https://ai.google.dev/gemini-api/terms>.
