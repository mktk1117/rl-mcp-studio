"""The studio's instruction harness: markdown files, assembled at start.

Why files rather than a string in `claude.py`: this is the text that decides
how the agent behaves, and it is the thing most likely to need changing after
watching it get something wrong. A prompt buried in a Python literal is edited
by a programmer; a directory of markdown is edited by whoever just watched the
mistake. Adding a file is adding an instruction -- they are read in name order,
so the numbering is the running order.

**It points; it does not copy.** rl-mcp documents itself for agents in
`AGENTS.md` and `docs/`, and those files are the authority on how to drive a
run. Restating their advice here would fork it, and a fork goes stale in the
direction of being confidently wrong -- which is the failure `AGENTS.md` itself
opens by describing. So the harness resolves their real paths and tells the
agent to read them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from rlmcp_studio.agents import preflight

HERE = Path(__file__).resolve().parent.parent / "instructions"

#: The rl-mcp documents worth naming, and what each is for. Named rather than
#: globbed: `docs/` also holds design notes and a style guide, which are about
#: working *on* rlmcp rather than *with* it, and pointing an agent at every
#: file in a directory is how a reading list stops being read.
RLMCP_DOCS = [
    ("AGENTS.md", "the canonical guide: read this before deciding how to drive a run"),
    ("docs/tools.md", "every command and MCP tool, one entry each"),
    ("docs/tuning.md", "the workflow for driving a run well — verify before "
                       "training, watch the right numbers, diagnose before "
                       "touching a weight"),
    ("docs/your-task.md", "putting rlmcp on a task, in five steps"),
    ("docs/curriculum.md", "stages, promotion and demotion"),
    ("docs/extensions.md", "adding a metric, a verb or a view"),
    ("docs/records.md", "what a record is and what closing one means"),
]


def rlmcp_root() -> Optional[Path]:
  """Where rl-mcp is checked out, asked of the installed package.

  Not configured and not guessed: the studio imports rlmcp already, so the
  package's own file location is the answer, and it stays right when the
  checkout moves.
  """
  try:
    import rlmcp
  except Exception:                       # noqa: BLE001 -- no rlmcp, no list
    return None
  root = Path(rlmcp.__file__).resolve().parent.parent
  return root if (root / "AGENTS.md").is_file() else None


def _doc_list() -> str:
  """The reading list, as absolute paths the agent's Read tool can open.

  Absolute on purpose. The agent's working directory is the *task* repository,
  so a relative `docs/tuning.md` resolves to a file that is not there, and an
  agent that tries once and fails does not try again.
  """
  root = rlmcp_root()
  if root is None:
    return ("rl-mcp's own documentation could not be located from this studio, "
            "which is unusual and worth mentioning to the user. Ask before "
            "relying on anything you remember about how rlmcp works.")
  lines = []
  for relative, what in RLMCP_DOCS:
    path = root / relative
    if path.is_file():
      lines.append(f"* `{path}` — {what}")
  if not lines:
    return "rl-mcp is installed but its documentation is not beside it."
  return "\n".join(lines)


def files() -> List[Path]:
  """The instruction files, in reading order."""
  if not HERE.is_dir():
    return []
  return sorted(p for p in HERE.glob("*.md") if p.is_file())


def instructions(extra: Optional[Dict[str, str]] = None) -> str:
  """Every instruction file, in order, with its placeholders filled.

  A file that cannot be read is skipped rather than fatal: a broken instruction
  should cost the agent one paragraph of guidance, not the ability to start.

  ``extra`` is where a *session's own truth* enters the text. Most of the
  harness is the same for every conversation, but one paragraph is not: whether
  the rlmcp tools are actually attached. As flat markdown that claim would be
  wrong for every conversation opened on a run that has never been launched —
  see `preflight.py`. Anything not supplied falls back to
  `preflight.defaults()`, which claims nothing.
  """
  values = {"rlmcp_docs": _doc_list(), **preflight.defaults(), **(extra or {})}
  out: List[str] = []
  for path in files():
    try:
      text = path.read_text(encoding="utf-8")
    except OSError:
      continue
    for name, value in values.items():
      text = text.replace("{{" + name + "}}", value)
    out.append(text.strip())
  return "\n\n---\n\n".join(out)
