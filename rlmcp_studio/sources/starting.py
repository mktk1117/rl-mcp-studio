"""Tasks that are being written here, and are not yet a task anywhere else.

A task exists because a package registers it. That is true from the moment the
package imports — and false for the twenty minutes before it does, which is
exactly the twenty minutes somebody is writing it. Between "I want a quadruped
that walks on rough terrain" and the first successful import there is
nothing for the registry to list, nothing for the records to remember, and so
nothing in the switcher to select and build against.

This is that gap, written down: a small file per task started here, holding the
name, where its package is going, and what it is meant to do. It is the studio's
own state, not rl-mcp's, and it is deliberately thin — three fields and a
timestamp. The moment the registry can see the task, the registry's answer wins
and the entry becomes redundant; it is kept only so the switcher can show what
somebody is in the middle of.

**It is not a scaffold.** The studio does not hold a template for what a task
looks like: that is rl-mcp's, in `docs/your-task.md` and in whatever
`rlmcp scaffold task` becomes, and a copy here would silently stop producing
valid tasks the moment rl-mcp changed its mind. What the studio does is
make a directory and ask the agent to write the task into it, from rl-mcp's own
documentation, with that directory as the scope it may edit freely.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rlmcp_studio.paths import safe_name, state_dir

#: A task id: what `--task` takes, and what a record's `task` field holds.
TASK_ID = re.compile(r"^[A-Za-z][A-Za-z0-9-]{2,80}$")

#: A python package name for the task's own code.
PACKAGE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")


def state_home() -> Path:
  return state_dir("starting")


class Starting:
  """The tasks somebody has begun here, oldest first."""

  def __init__(self, root: Optional[Path] = None) -> None:
    self.root = Path(root or state_home())

  def _path(self, task: str) -> Path:
    return self.root / f"{safe_name(task)}.json"

  def add(self, task: str, package: str, directory: str, what: str,
          robot: str = "", shape: str = "") -> Dict[str, Any]:
    entry = {
        "task": task, "package": package, "dir": directory, "what": what,
        "robot": robot, "shape": shape, "at": time.time(),
    }
    self.root.mkdir(parents=True, exist_ok=True)
    self._path(task).write_text(json.dumps(entry, indent=2))
    return entry

  def get(self, task: str) -> Optional[Dict[str, Any]]:
    try:
      return json.loads(self._path(task).read_text())
    except (OSError, json.JSONDecodeError):
      return None

  def all(self) -> List[Dict[str, Any]]:
    if not self.root.is_dir():
      return []
    found: List[Dict[str, Any]] = []
    for path in sorted(self.root.glob("*.json")):
      try:
        found.append(json.loads(path.read_text()))
      except (OSError, json.JSONDecodeError):
        continue
    return sorted(found, key=lambda e: e.get("at", 0))

  def forget(self, task: str) -> bool:
    try:
      self._path(task).unlink()
      return True
    except OSError:
      return False


def check_names(task: str, package: str) -> str:
  """Why these names will not do, or "".

  Refused here rather than three layers down, because both of them end up in
  places that are hard to change afterwards: the task id is written into every
  record of every run, and the package name is a directory and an import.
  """
  if not TASK_ID.match(task or ""):
    return ("A task id is letters, digits and dashes — the shape `--task` takes, "
            "like `Mjlab-Velocity-Rough-Unitree-G1`.")
  if not PACKAGE.match(package or ""):
    return ("A package name is lowercase letters, digits and underscores: it is "
            "a directory and an import, like `go1_rough_walk`.")
  return ""


def merge(tasks: List[Dict[str, Any]], starting: List[Dict[str, Any]]
          ) -> List[Dict[str, Any]]:
  """Fold the ones being written into the switcher's list.

  A task the registry already knows is not "being written" any more, whatever
  this store still holds: the registry's answer wins, and the entry is left on
  disk to be cleaned up rather than argued with.
  """
  known = {row.get("task") for row in tasks}
  out = list(tasks)
  for entry in starting:
    if entry.get("task") in known:
      continue
    out.append({
        "task": entry["task"], "root": "", "runs": 0, "live": 0, "latest": None,
        "recorded": False, "registered": False, "starting": True,
        "package": entry.get("package", ""), "experiment": "", "backend": "",
        "dir": entry.get("dir", ""), "what": entry.get("what", ""),
    })
  return out


__all__ = ["PACKAGE", "Starting", "TASK_ID", "check_names", "merge", "state_home"]
