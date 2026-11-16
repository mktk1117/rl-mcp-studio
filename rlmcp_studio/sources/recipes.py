"""Recipes on disk: what `rlmcp recipe build` wrote, ready to be trained again.

A recipe is a directory beside the records -- ``<root>/recipe-<id>/`` -- with
a ``recipe.json`` that says what it is (rl-mcp's manifest: task, packages,
seed, env count, length, the policy and the claimed numbers) and the files
`rlmcp train --recipe` reads. The studio does not interpret any of it; it
lists what is there and presses the button rl-mcp provides.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

MANIFEST = "recipe.json"


def find_recipes(roots: Sequence[Path], task: Optional[str] = None) -> List[Dict[str, Any]]:
  """Every recipe under the records roots, newest first, for one task or all.

  Looked for in ``<root>/recipe-*/`` (where `rlmcp recipe build` puts one by
  default) and ``<root>/recipes/*/`` (where somebody may have moved them).
  A directory whose manifest does not parse is listed with its error rather
  than skipped, so a half-written recipe is visible instead of invisible.
  """
  found: List[Dict[str, Any]] = []
  for root in roots:
    root = Path(root)
    candidates = list(root.glob("recipe-*")) + list((root / "recipes").glob("*"))
    for directory in candidates:
      manifest = directory / MANIFEST
      if not directory.is_dir() or not manifest.exists():
        continue
      entry = describe(directory)
      entry["records_root"] = str(root)
      if task and entry.get("task") not in (task, ""):
        continue
      found.append(entry)
  found.sort(key=lambda e: e.get("built", 0.0), reverse=True)
  return found


def describe(directory: Path) -> Dict[str, Any]:
  """One recipe, as the Build view lists it."""
  path = Path(directory)
  manifest = path / MANIFEST
  entry: Dict[str, Any] = {"path": str(path.resolve()), "name": path.name,
                           "from_run": "", "task": "", "expect": {},
                           "task_packages": []}
  try:
    entry["built"] = manifest.stat().st_mtime
    data = json.loads(manifest.read_text())
  except (OSError, ValueError) as exc:
    entry.update({"task": "", "error": f"{MANIFEST}: {exc}"})
    return entry
  if not isinstance(data, dict):
    entry.update({"task": "", "error": f"{MANIFEST} is not an object"})
    return entry
  entry.update({
      "from_run": str(data.get("from_run") or ""),
      "task": str(data.get("task") or ""),
      "headline": str(data.get("headline") or ""),
      "task_packages": list(data.get("task_packages") or []),
      "seed": data.get("seed"),
      "num_envs": data.get("num_envs"),
      "iterations": data.get("iterations"),
      "device": data.get("device") or "",
      "package": bool(data.get("package")) and (path / str(data.get("package"))).is_dir(),
      "policy": data.get("policy") or "",
      "expect": dict(data.get("expect") or {}),
      "verdict": str(data.get("verdict") or ""),
      "files": sorted(p.name for p in path.iterdir() if not p.name.startswith(".")),
  })
  return entry


def load_manifest(directory: Path) -> Dict[str, Any]:
  """The manifest itself, for a launch. Raises ValueError when unusable."""
  entry = describe(Path(directory))
  if entry.get("error"):
    raise ValueError(entry["error"])
  if not entry.get("task"):
    raise ValueError(f"{MANIFEST} in {directory} names no task")
  return entry


__all__ = ["MANIFEST", "describe", "find_recipes", "load_manifest"]
