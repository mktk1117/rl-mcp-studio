# Contributing

Thanks for helping with rl-mcp-studio. [AGENTS.md](AGENTS.md) is the full
guide to the codebase (for people and coding agents alike); this page is the
short version.

## Set up

The studio is developed against a sibling checkout of rl-mcp:

```bash
git clone https://github.com/mktk1117/rl-mcp
git clone https://github.com/mktk1117/rl-mcp-studio
cd rl-mcp-studio
uv sync --all-extras          # plain `uv sync` leaves out the dev tools
uv run rlmcp-studio           # http://127.0.0.1:8730
```

On first start a checkout downloads the pinned editor and terminal libraries
into your user cache.

## Test and lint

```bash
uv run pytest
uvx ruff@0.16.4 check rlmcp_studio tests setup.py
```

Tests must pass with no network, no GPU and no agent credentials. For a
visible change, also start a scratch studio and look at it in a browser
(see *Verify by looking* in AGENTS.md). `scripts/fresh-install.sh` builds the
wheel and checks it inside a clean container.

## Rules for what gets committed

This is a public, generic codebase; see AGENTS.md for the details.

- **Rule zero:** never re-implement rl-mcp. If the studio needs something
  rl-mcp cannot answer, open a pull request against rl-mcp.
- **Nothing personal or machine-specific:** no names, private project or task
  ids, absolute paths, secrets or private links. Use public mjlab tasks or
  made-up names in examples and tests.
- **Comments explain why, not history:** no dates, commit hashes or change
  stories in the code.
- **Depend on third-party code, do not copy it:** Python libraries go in
  `pyproject.toml`; browser libraries are pinned in `rlmcp_studio/vendor.py`.
  Everything must be Apache-2.0 compatible.

## Issues and pull requests

- Report a bug or propose a feature in an
  [issue](https://github.com/mktk1117/rl-mcp-studio/issues). For a bug, include
  `rlmcp-studio --check` output and steps to reproduce.
- Report security problems privately; see [docs/security.md](docs/security.md).
- Keep a pull request to one logical change, with a message that says what
  changed and why. A bug fix comes with a regression test that fails without
  the fix.

By contributing you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
