"""What tasks *exist*, which is not the same question as what has *run*.

The task switcher is built from records, so a task appears in this app because
a run of it was recorded. That is the right spine for watching runs and exactly
backwards for building one: the task you are writing has no runs by definition,
so there is nothing to select and nowhere to build.

The other half of the answer is the registry — the ids rl-mcp can drive right
now — and it arrives through ``rlmcp tasks``. Not through
``mjlab.tasks.registry``: that would break rule zero and the adapter contract in
one step, and this GUI is meant to reach IsaacLab too, whose registry is
somewhere else entirely.

**It runs as a subprocess, and that is the whole design of this module.**
Listing tasks means importing the packages that register them, which imports
the simulator, which imports torch. The studio backend imports rlmcp and never
torch — that is what makes it safe to restart while a run is training — so
answering this question in-process would trade a load-bearing property of the
app for one list. A subprocess costs a couple of seconds, keeps torch on the
other side of a process boundary, and cannot take the server down with it.

Which makes it slow, so it is cached. The registry changes when somebody edits
a task package, which is minutes apart, not seconds — and the one moment it has
to be fresh is right after a new task is scaffolded, which is a refresh the
caller asks for.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rlmcp_studio.sources.taskenv import TaskEnv
from rlmcp_studio.sources.taskenv import task_environment  # noqa: F401 -- re-exported: the seam this module names, reached through it by the tests

CACHE_SECONDS = 90.0
"""How long a listing is reused. Long enough that opening the switcher is free,
short enough that a task added by hand shows up without a restart."""

TIMEOUT = 180.0
"""A cold import of a simulator and torch is tens of seconds on a busy machine,
and the failure this guards against is a package whose import hangs -- not one
that is merely slow."""

def capability() -> Dict[str, Any]:
  """Whether the installed rl-mcp can list tasks at all.

  Importing ``rlmcp.tasks`` is cheap -- it pulls in no simulator, which is the
  point of its lazy backend table -- so the probe costs nothing and is
  definitive, unlike a version number on a checkout that lives on a branch.
  """
  try:
    import rlmcp.tasks  # noqa: F401
  except Exception as exc:                        # noqa: BLE001 - reported, not raised
    return {
        "available": False,
        "reason": f"this rl-mcp has no `rlmcp tasks` ({type(exc).__name__}: {exc})",
    }
  return {"available": True, "reason": ""}


class Registry:
  """One cached view of the task registry, per studio process."""

  def __init__(self, packages: Sequence[str] = (), python: str = "",
               cwd: str = "") -> None:
    self.env = TaskEnv(python, cwd, packages)
    self.packages = list(packages)
    self.python = self.env.python
    self.cwd = self.env.cwd
    self._answer: Optional[Dict[str, Any]] = None
    self._at = 0.0
    self._lock = asyncio.Lock()

  @property
  def age(self) -> float:
    return time.time() - self._at if self._answer is not None else -1.0

  def learn(self, package: str) -> bool:
    """Import this package too, and drop the listing that did not know it."""
    if not self.env.learn(package):
      return False
    self.packages = list(self.env.packages)
    self._answer, self._at = None, 0.0
    return True

  async def tasks(self, refresh: bool = False) -> Dict[str, Any]:
    """The registry's answer, from cache unless it is stale or refused.

    The lock is not an optimisation. Three panels ask on the same paint, and
    without it the studio starts three simulator imports at once on a machine
    whose GPU is training.
    """
    async with self._lock:
      fresh = self._answer is not None and self.age < CACHE_SECONDS
      if fresh and not refresh:
        return self._answer
      self._answer = await self._ask()
      self._at = time.time()
      return self._answer

  def cached(self) -> Optional[Dict[str, Any]]:
    """Whatever is in hand, without asking. For a path that must not block."""
    return self._answer

  async def _ask(self) -> Dict[str, Any]:
    # No capability gate here on purpose. `capability()` probes the rl-mcp the
    # *studio* imports, and the interpreter below is usually a different one --
    # the task repository's. The subprocess is the authority on what it can do;
    # the probe is for the panel that explains an absence before anybody asks.
    answer = await self.env.json("--json", "tasks", timeout=TIMEOUT)
    if not answer["ok"]:
      return {"ok": False, "tasks": [], "packages": [], "backends": [],
              "error": answer["error"], "python": self.env.python}

    payload = answer["payload"]
    payload.setdefault("tasks", [])
    payload.setdefault("packages", [])
    payload.setdefault("backends", [])
    # A non-zero exit means "nothing to show", not "broken": the note on stderr
    # is the useful half and the CLI writes it precisely for this case.
    payload["ok"] = True
    payload["note"] = answer.get("note", "")
    payload["python"] = self.env.python
    return payload


def merge(recorded: List[Dict[str, Any]],
          listing: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
  """Records ∪ registry, keyed by the task id both sides already use.

  Four combinations, and three of them are real (the fourth, neither, is
  nothing to list):

  * **registered and recorded** — a task being worked on.
  * **registered, never recorded** — a task being *built*. It has no runs, so
    the records alone would never show it.
  * **recorded, not registered** — history whose package is not importable
    here: an archive, or somebody else's task. It keeps everything the records
    can show and cannot be previewed.

  The key is the same string on both sides because it always was:
  ``RunRecord.task`` is the registered task id, not a second vocabulary.
  """
  rows: Dict[str, Dict[str, Any]] = {}
  for entry in recorded:
    rows[entry["task"]] = {**entry, "recorded": True, "registered": False,
                           "backend": "", "package": "", "experiment": ""}

  for entry in (listing or {}).get("tasks", []):
    task = entry.get("task") or ""
    if not task:
      continue
    row = rows.get(task)
    if row is None:
      row = {"task": task, "root": "", "runs": 0, "live": 0, "latest": None,
             "recorded": False}
      rows[task] = row
    row.update({
        "registered": True,
        "backend": entry.get("backend") or "",
        "package": entry.get("package") or "",
        "experiment": entry.get("experiment") or "",
    })

  ordered = sorted(rows.values(),
                   key=lambda t: (-t.get("live", 0), -t.get("runs", 0), t["task"]))
  status = {
      "ok": bool(listing and listing.get("ok")),
      "error": (listing or {}).get("error", ""),
      "note": (listing or {}).get("note", ""),
      "packages": (listing or {}).get("packages", []),
      "backends": (listing or {}).get("backends", []),
      "python": (listing or {}).get("python", ""),
  }
  return ordered, status
