"""Launch from a tree, not from the working tree.

Left alone, a run executes from the task repository's working directory:
whatever the files say at the moment the trainer imports them. rl-mcp stamps that
moment on the record (`rlmcp.records.snapshot`), so provenance is exact --
but the run and the person editing share one directory. An agent that edits
the package while a launch is still importing changes what that launch
trains; two variants of one file cannot be in flight at once; and a fleet of
machines has no working tree to share at all.

So a launch materializes. Three acts, each a function here:

* **stamp** -- write the tree of the code root as it is now, keep it alive
  under ``refs/rlmcp/runs/<record>``, and return the ``code`` payload a
  record stores. This is rl-mcp's own `capture`, called by the launcher
  rather than by the trainer, so it happens *before* the process exists.
* **variant** -- the same, but the tree is a base tree plus
  a patch, written through a throwaway index and never touching the
  working tree. Five variants are five refs from one base; the person's
  editor never sees any of them until one wins and is merged back.
* **materialize** -- write a stamped tree out into a directory of its own,
  the one the trainer will run from. ``git archive`` of a tree, so the copy
  is exactly the stamped content and nothing else: no ``.venv``, no logs, no
  checkpoint that was lying beside the package.

The trainer then runs in that directory with it first on ``PYTHONPATH``, so
the package it imports is the stamped one even when the task repository is
installed editable in the interpreter. The record carries the payload from
here; the trainer is told ``--code-root ''`` so it does not re-stamp from
its own cwd, which is a plain directory and would stamp ``kind: none`` over
the truth. (rl-mcp keeps an existing stamp in that case -- see
``RecordLink.KEEPS_LAUNCHER_STAMP``; an older rl-mcp overwrites it, and the
launcher falls back to letting the trainer stamp the live tree, and says so
on the job.)

None of this depends on the remote tier, and all of it is what the remote
tier is made of: ``RemoteRunner`` is *materialize on another
machine* -- a tree id pushed, a checkout there, the same command.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from rlmcp.records import snapshot

from rlmcp_studio.paths import state_dir

REF_PREFIX = snapshot.REF_PREFIX


class TreeError(RuntimeError):
  """A launch that cannot be materialized, with the git message that says why."""


def runs_home() -> Path:
  """Where materialized trees live: state, not config and not cache.

  One directory per record id, deleted when the run ends (`LocalRunner`
  does that) -- the tree itself lives on in the repository under its ref,
  so nothing here is the copy of record.
  """
  return state_dir("runs")


def stamp(code_root: Path | str, record_id: str) -> Dict[str, Any]:
  """The code root as it is now, as a record's ``code`` payload. Never raises.

  ``kind: git`` with a ``tree`` when the root is inside a repository;
  ``kind: none`` with a reason when it is not -- and a launch goes ahead
  either way, from the working tree, because provenance is worth having and
  never worth a failed launch (rl-mcp's rule, kept).
  """
  return snapshot.capture(code_root, record_id=record_id)


def variant(code_root: Path | str, base_tree: str, patch: str,
            record_id: str) -> Dict[str, Any]:
  """``base_tree`` plus ``patch``, as a new tree kept alive under the record's ref.

  Through a throwaway index: ``read-tree`` the base into it, ``apply
  --cached`` the patch onto it, ``write-tree``. The working tree, the real
  index and HEAD are untouched, which is the whole point -- a person mid-edit
  never notices five variants being written beside them.

  Raises :class:`TreeError` when the patch does not apply to the base, with
  git's own message: that is a real answer ("this change no longer fits
  that code"), not a launch to fall back from.
  """
  root = Path(code_root).expanduser().resolve()
  repo = snapshot.repo_of(root)
  if repo is None:
    raise TreeError(f"{root} is not inside a git repository, so there is no base tree")
  scope = str(root.relative_to(repo)) if root != repo else "."
  index = repo / ".git" / f"rlmcp-studio-index-{uuid.uuid4().hex[:8]}"
  env = {**os.environ, "GIT_INDEX_FILE": str(index)}
  try:
    _git(repo, env, "read-tree", base_tree)
    if patch.strip():
      _git(repo, env, "apply", "--cached", "--whitespace=nowarn", "-", stdin=patch)
    tree = _git(repo, env, "write-tree")
  finally:
    index.unlink(missing_ok=True)
  ref = f"{REF_PREFIX}/{record_id}"
  _git(repo, None, "update-ref", ref, tree)
  head = snapshot.head_of(repo)
  against_base = snapshot.diff(repo, base_tree, tree, scope=scope)
  files = [p for p in _git(repo, None, "ls-tree", "-r", "--name-only", tree).split("\n") if p]
  return {
      "kind": "git",
      "repo": str(repo),
      "root": scope,
      "files": len(files),
      "head": head,
      "tree": tree,
      # Relative to HEAD, the same meaning `capture` gives these two fields,
      # so a reader of the record does not need to know which path wrote it.
      "clean": False if against_base["changed"] else _clean_against_head(repo, tree, head, scope),
      "dirty": {"files": against_base["changed"], "added": against_base["added"],
                "removed": against_base["removed"]},
      "ref": ref,
      # What makes it a variant: where it came from, and by how much.
      "base": base_tree,
      "variant": {"files": against_base["changed"], "added": against_base["added"],
                  "removed": against_base["removed"]},
  }


def materialize(code: Dict[str, Any], destination: Path | str) -> Path:
  """Write a stamped tree out; the directory the run will execute from.

  Empty first: a destination that already holds an older materialization of
  the same record (a relaunch) would otherwise keep files the new tree
  deleted, and the run would import them.
  """
  if code.get("kind") != "git" or not code.get("tree"):
    raise TreeError("nothing to materialize: the record carries no git tree")
  destination = Path(destination).expanduser().resolve()
  if destination.exists():
    shutil.rmtree(destination)
  try:
    return snapshot.restore(code["repo"], code["tree"], destination)
  except (snapshot.SnapshotError, subprocess.CalledProcessError, OSError) as exc:
    raise TreeError(f"could not materialize {code['tree'][:12]}: {exc}") from exc


def archive(code: Dict[str, Any]) -> bytes:
  """`git archive` of a stamped tree: what a host is sent to run it.

  Bytes, not a path -- the host has no repository, only the token holder's
  word that these bytes are that tree, which is the same trust it extends
  to argv. Ignored files are not in the tree, so a checkpoint lying beside
  the package never travels.
  """
  if code.get("kind") != "git" or not code.get("tree"):
    raise TreeError("nothing to archive: the record carries no git tree")
  done = subprocess.run(["git", "-C", str(code["repo"]), "archive", code["tree"]],
                        capture_output=True, check=False)
  if done.returncode != 0:
    raise TreeError(f"git archive {code['tree'][:12]} failed: {done.stderr.decode(errors='replace').strip()}")
  return done.stdout


def import_roots(materialized: Path) -> list:
  """What goes first on ``PYTHONPATH`` so the stamped package is the one imported.

  The root itself, and ``src/`` when the repository uses that layout: the
  interpreter is the task repository's own, where the package is usually
  installed editable and would otherwise resolve to the working tree.
  """
  roots = [materialized]
  if (materialized / "src").is_dir():
    roots.append(materialized / "src")
  return [str(r) for r in roots]


def trainer_keeps_launcher_stamp() -> bool:
  """Whether this rl-mcp leaves a record's ``code`` alone when told ``--code-root ''``.

  Older rl-mcp versions overwrite it with ``{}`` at wrap time, which would erase
  the stamp made here; with those the launcher lets the trainer stamp the
  live tree instead and says so.
  """
  try:
    from rlmcp.records.link import RecordLink
  except Exception:
    return False
  return bool(getattr(RecordLink, "KEEPS_LAUNCHER_STAMP", False))


def _clean_against_head(repo: Path, tree: str, head: Optional[Dict[str, Any]],
                        scope: str) -> bool:
  if not head:
    return False
  try:
    return snapshot.diff(repo, f"{head['commit']}^{{tree}}", tree, scope=scope)["changed"] == 0
  except snapshot.SnapshotError:
    return False


def _git(repo: Path, env: Optional[Dict[str, str]], *args: str,
         stdin: Optional[str] = None) -> str:
  result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, input=stdin, env=env, check=False)
  if result.returncode != 0:
    raise TreeError(f"git {' '.join(args[:2])} failed in {repo}: {result.stderr.strip()}")
  return result.stdout.strip()
