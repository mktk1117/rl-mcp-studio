"""Where a conversation lives when the studio is not running.

A conversation about a run has to outlive the process hosting it. Kept only in
memory, every conversation about every run would be erased by a server restart
— which happens every time the studio's own code changes — and the reasoning
behind a run would go with it.

One JSONL file per conversation, appended as events happen. Deliberately not
the records store: a conversation is not a record. It is a working log, it is
large and chatty, and rl-mcp's ledger has its own contract about what a run's
history contains. Feedback the agent writes goes to the record; this does not.

**What is restored is the transcript, not the model's memory.** The provider's
session is gone with the process, so a message sent after a restart begins a
fresh context. That is why `hydrate` marks the point where the studio
restarted: showing an unbroken conversation whose second half cannot remember
its first would be a lie the pane tells by omission.

**One directory per set of records, not one per user.** A conversation key
begins with a run id -- which is numbered per records root and starts at 001 --
so the key alone cannot be the file name: two studios over two task folders
would both have a run 007, and both append to and read back the same
`007#main.jsonl`. The agent panel for one task would open showing another
task's transcript above the restart divider, under the heading of a run that
never mentioned it. `workspace_key` is what keeps them apart.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from rlmcp_studio.paths import state_dir

MAX_ENTRIES = 4_000
"""Kept per conversation. A long agent session is thousands of tool events, and
the pane renders all of them; past this the oldest are dropped on load."""

MAX_BYTES = 8 * 1024 * 1024
"""Ceiling per conversation file, after which it is rotated away rather than
grown without limit. A tool result can be large and there is no editor here."""


def state_home() -> Path:
  """Where a Linux desktop keeps state that is not configuration or cache."""
  return state_dir("conversations")


def _safe(name: str) -> str:
  """A file name from a conversation key, with nothing that walks a path."""
  return "".join(c if c.isalnum() or c in "-_.#" else "_" for c in name)[:180]


def workspace_key(records_root: str) -> str:
  """The directory that holds the conversations about one set of records.

  A run id means nothing on its own -- it counts from 001 inside the records
  root that issued it -- so the records root is what has to separate one
  studio's conversations from another's. The readable half of the name is for
  whoever opens this directory looking for a transcript; the digest is what
  actually keeps two roots apart, including two whose last components read the
  same.
  """
  path = str(records_root or "").strip()
  if not path:
    # Nothing to scope by. Still not the shared flat directory: one unnamed
    # workspace collides with itself only, which is a smaller wrong answer
    # than colliding with every other studio's.
    return "no-records"
  resolved = os.path.abspath(os.path.expanduser(path))
  digest = hashlib.sha1(resolved.encode("utf-8", "replace")).hexdigest()[:8]
  parts = [part for part in Path(resolved).parts if part not in ("/", "")][-2:]
  slug = _safe("-".join(parts)).strip("_-.#") or "records"
  return f"{slug[:60]}-{digest}"


def digest_of(path: Path) -> str:
  """Enough of a file's identity to park two same-named ones side by side."""
  try:
    stat = path.stat()
    seed = f"{stat.st_mtime_ns}:{stat.st_size}"
  except OSError:
    seed = path.name
  return hashlib.sha1(seed.encode()).hexdigest()[:8]


def _retire_unscoped(base: Path) -> List[str]:
  """Move the pre-workspace files aside, without reading or deleting them.

  A flat `007#main.jsonl` (the layout before workspaces) cannot be attributed
  to a records root: no entry says which studio wrote it, and it may hold two
  studios' turns interleaved. Handing them to a workspace would show
  the contamination one more time; deleting them would throw away real work.
  They go to `_unscoped/`, which nothing globs, and the studio says at boot
  where they went.
  """
  moved: List[str] = []
  try:
    stale = sorted(base.glob("*.jsonl")) + sorted(base.glob("*.jsonl.old"))
  except OSError:
    return moved
  if not stale:
    return moved
  attic = base / "_unscoped"
  try:
    attic.mkdir(parents=True, exist_ok=True)
  except OSError:
    return moved
  for path in stale:
    target = attic / path.name
    try:
      # Two studios can boot at once and glob the same file; the one that
      # loses finds it already gone, which is the outcome either way.
      if target.exists():
        target = attic / f"{path.name}.{digest_of(path)}"
      path.replace(target)
      moved.append(path.name)
    except OSError:
      continue
  return moved


def _read(path: Path) -> List[Dict[str, Any]]:
  """One JSONL file's entries; nothing for a file that is not there."""
  if not path.exists():
    return []
  entries: List[Dict[str, Any]] = []
  try:
    with path.open() as handle:
      for line in handle:
        line = line.strip()
        if not line:
          continue
        try:
          entries.append(json.loads(line))
        except json.JSONDecodeError:
          continue          # a half-written line from a killed process
  except OSError:
    return []
  return entries


class Transcripts:
  """Conversation logs on disk, one file per ``run#thread``, under one
  directory per records root."""

  def __init__(self, root: Optional[Path] = None, workspace: str = ""):
    base = Path(root) if root else state_home()
    self.workspace = workspace_key(workspace) if workspace else ""
    """Which set of records these belong to, ``""`` when nothing said."""
    self.root = base / self.workspace if self.workspace else base
    self.broken = ""
    """Why persistence is off, if it is. Empty when all is well."""
    self.retired: List[str] = []
    """Flat files moved to ``_unscoped/`` at boot, so the studio can say so."""
    try:
      self.root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
      # A studio that cannot write its logs still has to run: this is a
      # convenience, not the product.
      self.broken = f"conversations are not being saved: {exc}"
      return
    if self.workspace:
      self.retired = _retire_unscoped(base)

  def path(self, key: str) -> Path:
    return self.root / f"{_safe(key)}.jsonl"

  def append(self, key: str, entry: Dict[str, Any]) -> None:
    if self.broken:
      return
    path = self.path(key)
    try:
      if path.exists() and path.stat().st_size > MAX_BYTES:
        path.replace(path.with_suffix(".jsonl.old"))
      with path.open("a") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")
    except OSError as exc:
      self.broken = f"conversations are not being saved: {exc}"

  def load(self, key: str) -> List[Dict[str, Any]]:
    # `append` moves a file past MAX_BYTES to `.jsonl.old` and starts afresh.
    # The half before the cut is the same conversation: reading only the new
    # file would show a conversation that began mid-sentence, and until the
    # next word was said there would be no new file at all.
    path = self.path(key)
    entries = _read(path.with_suffix(".jsonl.old")) + _read(path)
    return entries[-MAX_ENTRIES:]

  def keys(self) -> List[str]:
    """Every conversation on disk, as the file spells ``run#thread``."""
    if self.broken or not self.root.is_dir():
      return []
    stems = {p.stem for p in self.root.glob("*.jsonl")}
    stems |= {p.name[:-len(".jsonl.old")] for p in self.root.glob("*.jsonl.old")}
    return sorted(stems)
