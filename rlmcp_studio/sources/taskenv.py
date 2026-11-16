"""Where a task is built, and how the studio asks it anything.

Three questions in this app are answered by *constructing an environment*:
which tasks exist, does this one still work, and what does it look like right
now. All three have the same two problems, so they share this module.

**They cannot run in the studio's process.** Building a task imports the
simulator, which imports torch, and this backend importing rlmcp and never
torch is what makes it safe to restart while a run is training.
So each of them is a subprocess, and a task that segfaults takes a subprocess
with it rather than the app.

**They cannot run in the studio's *environment* either.** The studio's
virtualenv has rl-mcp in it and nothing else — no mjlab, no torch, and
certainly not your task package, which lives in the records repository next
door with its own environment. Asked from here, "which tasks exist" has the
honest and useless answer "no backend installed". So the interpreter is the
task repository's when there is one.

That second point is the seam this module exists to name. It is the same
question for a listing, a check and a preview, and for a run on a remote host
it is the same question again with a different answer: *which environment can
construct this task.*
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ENTRY = "from rlmcp.cli import main; import sys; sys.exit(main(sys.argv[1:]))"
"""rl-mcp's CLI as a ``-c`` program rather than an ``rlmcp`` found on the PATH,
so the interpreter chosen below is the one that answers."""


def task_environment(code_roots: Sequence[Path]) -> Tuple[str, str]:
  """The interpreter that can build these tasks, and where to run it.

  ``<records-root>/../.venv/bin/python`` when it exists, which is the layout
  rl-mcp's own convention produces: a records directory inside the task
  repository. Returns ``("", "")`` when there is nothing better than the
  studio's own interpreter, which is a real answer -- a studio installed
  alongside the tasks needs no second environment.
  """
  for root in code_roots:
    candidate = Path(root) / ".venv" / "bin" / "python"
    if candidate.exists():
      return str(candidate), str(root)
  return "", ""


class TaskEnv:
  """One interpreter, one working directory, and the packages to import."""

  def __init__(self, python: str = "", cwd: str = "",
               packages: Sequence[str] = ()) -> None:
    self.python = python or sys.executable
    self.cwd = cwd or None
    self.packages = [p for p in packages if p]

  def describe(self) -> Dict[str, Any]:
    return {"python": self.python, "cwd": self.cwd or "",
            "packages": list(self.packages),
            "own": self.python == sys.executable}

  def learn(self, package: str) -> bool:
    """Import this package too, from the next command on.

    A task started in this GUI is a package nothing imports yet: the studio was
    launched with whatever `$RLMCP_TASK_PACKAGES` said at the time, and the
    package it has just made this minute is by construction not in it. Without
    this, `+ New task` produces a task the same studio then cannot check,
    preview or launch -- the first gate answers "Unknown task ... nothing
    imported here registers it", which is true, and which no button in the app
    could fix. The package name is the one thing the dialog definitely knows,
    so it is the studio's job to remember it rather than the user's job to
    restart with a longer command line.
    """
    package = (package or "").strip()
    if not package or package in self.packages:
      return False
    self.packages.append(package)
    return True

  def argv(self, *command: str) -> List[str]:
    """A full rlmcp command line, with the task packages appended.

    ``--task-package`` goes on every command that has to import them, and
    ``$RLMCP_TASK_PACKAGES`` reaches the subprocess through the environment on
    its own -- so a studio started with neither still works wherever the shell
    was already set up for it.
    """
    argv = [self.python, "-c", ENTRY, *command]
    for module in self.packages:
      argv += ["--task-package", module]
    return argv

  async def json(self, *command: str, timeout: float = 180.0) -> Dict[str, Any]:
    """Run one rlmcp command and parse its JSON, or say why there is none.

    Never raises for a command that failed: the caller is a panel, and a panel
    that throws shows nothing at all. Everything comes back as ``ok`` plus a
    sentence.
    """
    argv = self.argv(*command)
    try:
      proc = await asyncio.create_subprocess_exec(
          *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
          cwd=self.cwd)
      out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
      return {"ok": False, "error": f"no answer within {timeout:.0f}s",
              "argv": argv}
    except Exception as exc:                      # noqa: BLE001 - reported, not raised
      return {"ok": False, "error": f"could not run it: {exc}", "argv": argv}

    text = (out or b"").decode("utf-8", "replace").strip()
    trouble = (err or b"").decode("utf-8", "replace").strip()
    if not text:
      return {"ok": False, "argv": argv,
              "error": trouble or f"it said nothing (exit {proc.returncode})"}
    payload = _json_at_the_end(text)
    if payload is None:
      return {"ok": False, "argv": argv,
              "error": f"that was not JSON: {text[:200]}"}
    return {"ok": True, "payload": payload, "note": trouble, "argv": argv}

  def spawn_argv(self, *command: str) -> List[str]:
    """The same line, for a process the caller will manage itself."""
    return self.argv(*command)

  def environ(self) -> Dict[str, str]:
    """The environment a subprocess inherits.

    Unbuffered, because the one thing a preview's output is for is the
    traceback that appears when the env will not build, and a buffered pipe
    holds that until the process exits -- which is exactly when it is too late.
    """
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _json_at_the_end(text: str) -> Optional[Any]:
  """The JSON document at the end of a noisy stdout, or None.

  Simulators announce themselves on stdout. Warp prints a banner naming the
  CUDA toolkit and every device; mujoco_warp prints a line per compiled kernel.
  All of that lands *in front of* the answer, so `json.loads` on the whole
  stream fails on output that is perfectly good.

  rl-mcp's own contract is that a pipe gets JSON, and the honest fix is
  upstream -- a command in JSON mode should be the only thing writing to
  stdout. Until that lands everywhere, this reads the last document rather
  than refusing a good answer because a library said hello.
  """
  text = text.strip()
  if not text:
    return None
  try:
    return json.loads(text)
  except json.JSONDecodeError:
    pass
  lines = text.splitlines()
  for index in range(len(lines) - 1, -1, -1):
    if lines[index][:1] not in ("{", "["):
      continue
    try:
      return json.loads("\n".join(lines[index:]))
    except json.JSONDecodeError:
      continue
  return None


def from_roots(code_roots: Sequence[Path], packages: Sequence[str] = ()) -> TaskEnv:
  python, cwd = task_environment(code_roots)
  return TaskEnv(python, cwd, packages)


__all__ = ["ENTRY", "TaskEnv", "from_roots", "task_environment"]
