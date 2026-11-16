"""Searching the task package: by name, and inside the files.

A file tree with a substring filter is fine when you remember the file's name.
The question a person actually arrives with is *"where is the thing that
computes the foot clearance"* — and answering that means reading the files, not
their names.

Two searches, because they answer different questions:

* **by name** — fuzzy, ranked, and done in the browser: the tree is already
  loaded there, so it costs no round trip and updates on every keystroke.
* **by content** — this module. `ripgrep` if it is installed, and a bounded
  Python scan if it is not, so the feature does not depend on a tool being
  present.

Both are confined to the configured source roots, like everything else that
touches a path here.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

MAX_MATCHES = 300
"""Enough to be useful, few enough to render. What is dropped is reported."""

MAX_LINE = 400
"""A match in a minified file should not send a megabyte to the browser."""

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "logs",
             "records", "wandb", "outputs", ".mypy_cache", ".pytest_cache"}

TEXT_SUFFIXES = {".py", ".pyi", ".md", ".txt", ".json", ".yaml", ".yml",
                 ".toml", ".cfg", ".ini", ".sh", ".js", ".ts", ".html", ".css",
                 ".xml", ".urdf", ".xacro", ".usda", ".rst"}


def _trim(line: str) -> str:
  line = line.rstrip("\n")
  return line if len(line) <= MAX_LINE else line[:MAX_LINE] + " …"


async def _ripgrep(query: str, root: Path, limit: int) -> Optional[List[Dict[str, Any]]]:
  """Ask ripgrep, if it is here. Returns None when it is not, or when it fails.

  ``--fixed-strings`` on purpose: a person typing ``action_rate_l2(`` into a
  search box means those characters, not a regular expression with a group in
  it. Nobody is surprised by a literal search; plenty of people are surprised
  by their brackets becoming syntax.
  """
  binary = shutil.which("rg")
  if not binary:
    return None
  args = [binary, "--json", "--fixed-strings", "--smart-case",
          "--max-count", "20", "--max-filesize", "2M",
          "--max-columns", str(MAX_LINE)]
  for directory in SKIP_DIRS:
    args += ["--glob", f"!{directory}/"]
  args += ["--", query, str(root)]

  process = await asyncio.create_subprocess_exec(
      *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
  try:
    out, _ = await asyncio.wait_for(process.communicate(), timeout=20)
  except asyncio.TimeoutError:
    process.kill()
    return None
  if process.returncode not in (0, 1):          # 1 is "no matches", not an error
    return None

  import json
  matches: List[Dict[str, Any]] = []
  for line in out.decode("utf-8", "replace").splitlines():
    if len(matches) >= limit:
      break
    try:
      payload = json.loads(line)
    except json.JSONDecodeError:
      continue
    if payload.get("type") != "match":
      continue
    data = payload["data"]
    text = data.get("lines", {}).get("text", "")
    submatch = (data.get("submatches") or [{}])[0]
    matches.append({
        "path": data["path"]["text"],
        "line": int(data["line_number"] or 0),
        "column": int(submatch.get("start", 0)),
        "text": _trim(text),
    })
  return matches


def _by_hand(query: str, root: Path, limit: int) -> List[Dict[str, Any]]:
  """The same search without ripgrep. Slower, and never absent.

  Bounded three ways -- suffix, directory and file size -- because this walks a
  task repository that also holds checkpoints and video.
  """
  needle = query.lower()
  matches: List[Dict[str, Any]] = []
  for path in sorted(root.rglob("*")):
    if len(matches) >= limit:
      break
    if any(part in SKIP_DIRS or part.startswith(".") for part in path.parts):
      continue
    if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
      continue
    try:
      if path.stat().st_size > 2_000_000:
        continue
      with path.open("r", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
          column = line.lower().find(needle)
          if column == -1:
            continue
          matches.append({"path": str(path), "line": number,
                          "column": column, "text": _trim(line)})
          if len(matches) >= limit:
            break
    except OSError:
      continue
  return matches


async def in_files(query: str, roots: Iterable[Path],
                   limit: int = MAX_MATCHES) -> Dict[str, Any]:
  """Every line in the package containing ``query``, grouped by file."""
  query = (query or "").strip()
  if len(query) < 2:
    return {"ok": False, "query": query, "files": [], "count": 0,
            "reason": "type at least two characters"}

  matches: List[Dict[str, Any]] = []
  engine = "ripgrep"
  for root in roots:
    root = Path(root)
    if not root.is_dir():
      continue
    found = await _ripgrep(query, root, limit - len(matches))
    if found is None:
      engine = "python"
      found = await asyncio.to_thread(_by_hand, query, root, limit - len(matches))
    matches.extend(found)
    if len(matches) >= limit:
      break

  files: Dict[str, Dict[str, Any]] = {}
  for match in matches:
    entry = files.setdefault(match["path"], {"path": match["path"], "lines": []})
    entry["lines"].append(match)
  ordered = sorted(files.values(), key=lambda f: (-len(f["lines"]), f["path"]))

  return {
      "ok": True,
      "query": query,
      "engine": engine,
      "files": ordered,
      "count": len(matches),
      "truncated": len(matches) >= limit,
      # Said plainly: a result list that silently stops is a result list that
      # lies about what is in the repository.
      "reason": (f"showing the first {limit} matches" if len(matches) >= limit
                 else ""),
  }
