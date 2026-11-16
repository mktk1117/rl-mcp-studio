"""Closing a run out, which is rl-mcp's act and not the studio's.

Without a close-out, every run launched from this GUI would stay at
`verdict: running` for ever, with that word in the status bar next to `trainer
gone`. This is what gives the Record view a headline field and a verdict
control instead of a pointer to the CLI.

**Why this is a subprocess.** Closing a record is not "write `verdict` into a
JSON file". `rlmcp record close` gathers the run's evidence, folds the feedback
said to the live trainer into the record before its logs are deleted, evaluates
the pre-registered falsifier at the run's final iteration, refuses verdicts the
record has not earned -- `validated` on a warm start, a result verdict with no
measurement -- and writes `REPORT.md`. All of that is the *meaning* of a
verdict, and the meaning belongs to rl-mcp. A studio that reproduced any of it
would be a second opinion about the one thing this repository is not allowed to
have a second opinion about: what a run's result means.

There is no library call and no MCP tool for it: the whole close-out lives in
`rlmcp/cli.py`'s `record close`. That is the honest gap -- `records.close_out()`
belongs upstream, and would be a small PR -- and until it exists, running the
command rl-mcp already ships is closer to the rule than reimplementing it here.
`sources/gates.py` and `sources/registry.py` drive `rlmcp check` and
`rlmcp tasks` the same way, for the same reason.

It runs under the **studio's own** interpreter, unlike those two: closing a
record touches records and a session directory, never a simulator, so the
environment that can construct the task is irrelevant and the interpreter that
imports the same rl-mcp the studio reads records with is the right one.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from rlmcp_studio.sources.taskenv import _json_at_the_end

TIMEOUT = 90.0
"""A read, a falsifier evaluation and two file writes. Generous: the evidence
walk reads a finished run's metrics, which can be a large file."""


def verdict_vocabulary() -> Dict[str, Any]:
  """The words a verdict may be, from rl-mcp. Never a list written here.

  The studio already refuses to own the verdict *palette*
  (`records.VERDICT_COLORS`); the vocabulary is the same kind of thing. A
  control offering a word rl-mcp does not know would be a control that can
  only fail, and one missing a word it does know is a state the GUI cannot
  reach.
  """
  from rlmcp.records import OPEN_VERDICTS, TERMINAL_VERDICTS, VERDICTS
  from rlmcp.records.validate import RESULT_VERDICTS

  return {
      "all": list(VERDICTS),
      "open": list(OPEN_VERDICTS),
      "terminal": list(TERMINAL_VERDICTS),
      # The four that claim a result, and therefore owe an outcome and at
      # least one measurement. Said in the panel *before* the refusal.
      "result": list(RESULT_VERDICTS),
  }


def parse_metrics(rows: Optional[Sequence[Any]]) -> List[str]:
  """``name=value`` strings, checked here so the CLI never sees a bad one.

  `rlmcp record close` answers a malformed pair with `SystemExit`, which is a
  non-zero exit and no JSON at all -- a shape this side would have to report
  as "it did not answer", which is true and useless.
  """
  out: List[str] = []
  for row in rows or []:
    if isinstance(row, (list, tuple)) and len(row) == 2:
      name, value = str(row[0]).strip(), str(row[1]).strip()
    elif isinstance(row, str):
      if "=" not in row:
        raise ValueError(f"expected name=value, got {row!r}")
      name, value = (part.strip() for part in row.split("=", 1))
    else:
      raise ValueError("a measurement is `name=value`")
    if not name or not value:
      raise ValueError(f"a measurement needs a name and a value, got {row!r}")
    if "\n" in name or "\n" in value:
      raise ValueError("a measurement is one line")
    out.append(f"{name}={value}")
  return out


async def close_record(
    records_root: Path,
    record_id: str,
    verdict: str,
    headline: str = "",
    outcome: str = "",
    metrics: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
  """Run `rlmcp record close`, and answer with what it said.

  Refusals come back as `ok: False` with rl-mcp's own sentence, verbatim: they
  are the most useful thing this feature produces. "'validated' is not
  available to a run warm-started from 012" teaches the rule; a studio-written
  "could not close" teaches nothing.
  """
  argv = [
      sys.executable, "-m", "rlmcp.cli", "--json",
      "record", "--records-root", str(records_root),
      "close", record_id, verdict,
  ]
  if headline:
    argv += ["--headline", headline]
  if outcome:
    argv += ["--outcome", outcome]
  if metrics:
    argv += ["--metric", *metrics]

  try:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
  except OSError as exc:
    return {"ok": False, "error": f"could not run rlmcp: {exc}"}

  try:
    raw, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
  except asyncio.TimeoutError:
    proc.kill()
    return {"ok": False, "error": f"`rlmcp record close` did not answer in {TIMEOUT:.0f}s"}

  # The same reader `taskenv` uses, rather than a second one: rl-mcp's own
  # contract is that a pipe gets JSON, and anything a library prints at import
  # lands in front of it.
  answer = _json_at_the_end(raw.decode("utf-8", "replace"))
  if not isinstance(answer, dict):
    # No JSON: an argparse rejection, an import error, a traceback. The
    # command's own words are the best thing to show.
    text = (err.decode("utf-8", "replace").strip()
            or raw.decode("utf-8", "replace").strip()
            or f"exit {proc.returncode}")
    return {"ok": False, "error": text.splitlines()[-1][:500]}
  return answer


__all__ = ["close_record", "parse_metrics", "verdict_vocabulary"]
