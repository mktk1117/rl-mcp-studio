"""The task's own source, and what changed in it.

Two different questions the GUI asks, and they have different answers:

**Source** -- what does this env actually do? The task package is a directory
on disk next to the records.

**Diff** -- what changed? A record carries a resolved *config* snapshot, so
config-vs-parent is real and exact. A *code* diff between two runs needs the
package snapshot each run stamped at launch; for runs that carry none, the
honest thing to show is the working tree's own uncommitted diff, clearly
labelled as "now", not "that run".

Every path is checked against the configured roots before it is opened. The
studio serves files over HTTP, so a traversal here would hand out the whole
filesystem.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", "dist", "build", "logs", "wandb",
             # Records and their media are a different question with their own
             # views. Left in, they bury the source: a task's eval media alone
             # can be thousands of small JSON files.
             "records", "media", "outputs", "checkpoints", "videos", "work"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt", ".cfg",
                 ".ini", ".sh", ".xml", ".jsonl", ".html", ".css", ".js"}
MAX_BYTES = 400_000


class OutsideRoot(PermissionError):
  """A requested path is not under any configured root."""


def resolve_under(path: str | Path, roots: Iterable[Path]) -> Path:
  """Resolve ``path`` and prove it lives under one of ``roots``.

  ``Path.resolve`` first, so ``..`` and symlinks are collapsed before the
  comparison rather than after -- checking the unresolved string is the classic
  way to get this wrong.
  """
  try:
    target = Path(path).expanduser().resolve()
  except (ValueError, OSError) as exc:
    # A NUL byte, a name too long, a path the OS refuses to stat. It is
    # certainly not under a root, and must not escape as a bare 500.
    raise OutsideRoot(f"that path cannot be read ({exc})") from exc
  for root in roots:
    root = Path(root).expanduser().resolve()
    if target == root or root in target.parents:
      return target
  # The *resolved* path is not echoed back: it discloses where the server runs
  # and where the caller's home is, to a caller who by definition asked for
  # something outside. What they sent is enough to identify their mistake.
  raise OutsideRoot(f"'{path}' is not under a configured root")


def source_roots(records_roots: Iterable[Path]) -> List[Path]:
  """Where a task's code is likely to be.

  A records root usually sits inside the task repository (``<repo>/records``),
  so the repository is its parent. That is a convention rather than a rule, so
  it is one function, easy to replace when the record starts carrying its own
  package path.
  """
  out: List[Path] = []
  for root in records_roots:
    root = Path(root).expanduser().resolve()
    parent = root.parent
    if parent.exists() and parent not in out:
      out.append(parent)
  return out


def tree(root: Path, max_entries: int = 3000) -> List[Dict[str, Any]]:
  """A flat, depth-tagged listing. Flat because the client renders it flat."""
  root = Path(root)
  entries: List[Dict[str, Any]] = []

  def walk(directory: Path, depth: int) -> None:
    if len(entries) >= max_entries or depth > 6:
      return
    try:
      children = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except (PermissionError, OSError):
      return
    for child in children:
      if (child.name.startswith(".") or child.name in SKIP_DIRS
          or child.name.endswith((".egg-info", ".dist-info"))):
        continue
      if child.is_dir():
        entries.append({"name": child.name, "path": str(child), "depth": depth, "dir": True})
        walk(child, depth + 1)
      elif child.suffix.lower() in TEXT_SUFFIXES:
        entries.append({"name": child.name, "path": str(child), "depth": depth,
                        "dir": False, "size": child.stat().st_size})
      if len(entries) >= max_entries:
        return

  walk(root, 0)
  return entries


def newest_change(root: Path, max_files: int = 4000) -> Dict[str, Any]:
  """When this package was last edited, and which file it was.

  Two panels in the Build view are answers about a *moment*: the preview holds
  the environment it constructed when it started, and the gate strip holds the
  answer `rlmcp check` gave when it ran. An edit invalidates both, silently --
  the gates come back green on the new code while the preview is still stepping
  the old one, and nothing on screen says the two are about different files.

  So the third clock. Same walk and the same `SKIP_DIRS` as `tree`, because
  "the task's files" has to mean the same set in both places -- notably not
  `__pycache__`, which is rewritten by *reading* the package and would report
  an edit nobody made.
  """
  root = Path(root)
  newest = 0.0
  where = ""
  seen = 0

  def walk(directory: Path, depth: int) -> None:
    nonlocal newest, where, seen
    if depth > 6 or seen >= max_files:
      return
    try:
      children = list(directory.iterdir())
    except (PermissionError, OSError):
      return
    for child in children:
      if seen >= max_files:
        return
      if (child.name.startswith(".") or child.name in SKIP_DIRS
          or child.name.endswith((".egg-info", ".dist-info"))):
        continue
      if child.is_dir():
        walk(child, depth + 1)
        continue
      if child.suffix.lower() not in TEXT_SUFFIXES:
        continue
      seen += 1
      try:
        at = child.stat().st_mtime
      except OSError:
        continue
      if at > newest:
        newest, where = at, str(child)

  if not root or not root.is_dir():
    return {"at": 0.0, "file": "", "files": 0}
  walk(root, 0)
  return {"at": newest, "file": where, "files": seen}


def read(path: Path) -> Dict[str, Any]:
  path = Path(path)
  if not path.is_file():
    return {"ok": False, "reason": "not a file"}
  size = path.stat().st_size
  if size > MAX_BYTES:
    return {"ok": False, "reason": f"too large to show ({size // 1024} KB)"}
  try:
    text = path.read_text(errors="replace")
  except Exception as exc:
    return {"ok": False, "reason": str(exc)}
  return {"ok": True, "path": str(path), "name": path.name,
          "lines": text.splitlines(), "size": size}


# ── diffs ───────────────────────────────────────────────────────────────
def _flatten(value: Any, prefix: str = "") -> Dict[str, Any]:
  """Nested config to ``dotted.key -> leaf``, which is how rlmcp names them."""
  flat: Dict[str, Any] = {}
  if isinstance(value, dict):
    for key, item in value.items():
      flat.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
  elif isinstance(value, list) and value and all(
      isinstance(v, (int, float)) for v in value):
    flat[prefix] = list(value)          # a [min, max] pair is one value
  elif isinstance(value, list):
    for i, item in enumerate(value):
      flat.update(_flatten(item, f"{prefix}[{i}]"))
  else:
    flat[prefix] = value
  return flat


def config_diff(child: Dict[str, Any], parent: Dict[str, Any]) -> Dict[str, Any]:
  """Exact, because both sides are resolved snapshots rather than source."""
  a, b = _flatten(parent or {}), _flatten(child or {})
  changed = [{"key": k, "old": a[k], "new": b[k]}
             for k in sorted(set(a) & set(b)) if a[k] != b[k]]
  added = [{"key": k, "new": b[k]} for k in sorted(set(b) - set(a))]
  removed = [{"key": k, "old": a[k]} for k in sorted(set(a) - set(b))]
  return {"changed": changed, "added": added, "removed": removed,
          "total": len(a), "same": len(set(a) & set(b)) - len(changed),
          # The keys the snapshots actually cover. Without this a reader cannot
          # tell "identical" from "not recorded here", and the two look the
          # same on screen: a record saying a reward weight changed, above a
          # panel saying every parameter is identical.
          "keys": sorted(set(a) | set(b))}


def split_patch(lines: List[str]) -> List[Dict[str, Any]]:
  """A unified diff, split per file, so the client can show one and hide the rest.

  A repo-wide patch is mostly noise when you are asking about one run: notes,
  logs and unrelated packages all land in it. Splitting is what lets the
  default be "the files this task owns" rather than everything.
  """
  files: List[Dict[str, Any]] = []
  current: Optional[Dict[str, Any]] = None
  for line in lines:
    if line.startswith("diff --git "):
      parts = line.split(" b/")
      path = parts[-1] if len(parts) > 1 else line
      current = {"path": path, "lines": [line], "added": 0, "removed": 0}
      files.append(current)
      continue
    if current is None:
      continue
    current["lines"].append(line)
    if line.startswith("+") and not line.startswith("+++"):
      current["added"] += 1
    elif line.startswith("-") and not line.startswith("---"):
      current["removed"] += 1
  return files


def relevance(path: str, task_hints: Iterable[str]) -> bool:
  """Is this file plausibly part of the task the run trained?

  Matched on name shape rather than a configured path list, so a repo laid out
  differently still gets a sensible default. Docs, logs and records are never
  relevant to *what the policy did* -- they are relevant to what you wrote
  about it, which is a different question and a different tab.
  """
  lowered = path.lower()
  for skip in ("records/", "logs/", "docs/", ".md", "media/", "uv.lock", "wandb/"):
    if skip in lowered:
      return False
  for hint in task_hints:
    if hint and hint.lower() in lowered:
      return True
  return lowered.endswith((".py", ".yaml", ".yml", ".toml"))


MAX_DIFF_LINE = 500
"""Characters of one diff line that reach the browser.

A minified file or a one-line JSON constant is a single diff line hundreds of
thousands of characters long, which lays out to millions of pixels: the pane
scrolls, so nothing looks broken, but the browser has to measure it and no
human is going to scroll there. What is cut is said.
"""


def clip_lines(lines: List[str]) -> List[str]:
  """Diff lines, with the unreadably long ones cut and marked."""
  out = []
  for line in lines:
    out.append(line if len(line) <= MAX_DIFF_LINE
               else f"{line[:MAX_DIFF_LINE]} … +{len(line) - MAX_DIFF_LINE} more characters")
  return out


async def worktree_diff(root: Path, max_bytes: int = 200_000) -> Dict[str, Any]:
  """What is uncommitted in the task repo right now.

  Explicitly *now*, not "what that run used": without a per-run code snapshot
  there is nothing to diff a past run against, and implying otherwise would be
  worse than saying so.
  """
  root = Path(root)
  if not (root / ".git").exists():
    return {"ok": False, "reason": f"{root} is not a git repository"}
  try:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(root), "diff", "--stat", "--patch", "--no-color",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
  except Exception as exc:
    return {"ok": False, "reason": str(exc)}
  if proc.returncode not in (0, 1):
    return {"ok": False, "reason": err.decode(errors="replace")[:400]}
  text = out.decode(errors="replace")
  truncated = len(text) > max_bytes
  lines = clip_lines(text[:max_bytes].splitlines())
  return {"ok": True, "root": str(root), "truncated": truncated,
          "lines": lines, "files": split_patch(lines), "empty": not text.strip()}


def snapshot_diff(record: Any, other: Any) -> Dict[str, Any]:
  """The code diff between two runs, from the snapshots they stamped at launch.

  This is the question the Diff view exists to answer, and it needs rl-mcp's
  launch-time stamp: not "what is uncommitted right now" but "what was
  different about the code when *that* run went". Both trees are content, so it
  works whether or not either run was committed, and it keeps working after the
  commit is rebased away.

  All the git work belongs to ``rlmcp.records.snapshot``; this reshapes its
  answer for the wire and reuses :func:`split_patch` so the client renders it
  with the same component as the worktree patch.
  """
  from rlmcp.records import snapshot as rlmcp_snapshot

  mine, theirs = record.code or {}, (other.code or {}) if other else {}
  if mine.get("kind") != "git" or theirs.get("kind") != "git":
    missing = [r.id for r, c in ((record, mine), (other, theirs))
               if r is not None and c.get("kind") != "git"]
    return {"ok": False, "missing": missing,
            "reason": ("no code snapshot for " + ", ".join(missing) +
                       " — runs launched before `--code-root`, or outside a "
                       "git repository, carry no package stamp")}
  if mine.get("repo") != theirs.get("repo"):
    return {"ok": False, "reason": "the two runs were stamped in different repositories"}

  try:
    counts = rlmcp_snapshot.diff(mine["repo"], theirs["tree"], mine["tree"])
    text = rlmcp_snapshot.patch(mine["repo"], theirs["tree"], mine["tree"])
  except Exception as exc:  # noqa: BLE001 -- a lost object must not 500 the view.
    return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

  lines = clip_lines(text.splitlines())
  files = split_patch(lines)
  trivial = {f["path"] for f in counts["files"] if f["trivial"]}
  for entry in files:
    # Greyed, never filtered: deciding whether an edit changes behaviour is
    # undecidable, and dropping the quiet ones is how a real change vanishes.
    entry["trivial"] = entry["path"] in trivial
  return {"ok": True, "from": other.id, "to": record.id, "files": files,
          "changed": counts["changed"], "added": counts["added"],
          "removed": counts["removed"], "empty": not lines,
          "base": mine.get("head"), "clean": mine.get("clean", True)}


async def head_info(root: Path) -> Dict[str, Any]:
  """Commit, branch and dirtiness -- the anchor a snapshot would record."""
  root = Path(root)
  if not (root / ".git").exists():
    return {"ok": False}
  async def git(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(root), *args,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return out.decode(errors="replace").strip()
  commit = await git("rev-parse", "--short", "HEAD")
  branch = await git("rev-parse", "--abbrev-ref", "HEAD")
  dirty = await git("status", "--porcelain")
  return {"ok": True, "commit": commit, "branch": branch,
          "dirty_files": len([l for l in dirty.splitlines() if l.strip()])}
