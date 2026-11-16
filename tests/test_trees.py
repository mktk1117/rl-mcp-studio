"""Launching from a tree: stamp, variant, materialize (`runners/trees.py`).

A real repository in a temporary directory, one commit deep, with an
uncommitted edit -- the state a launch usually finds. Every assertion is
about content that came back out of git, never about how it was stored.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rlmcp_studio.runners import trees


@pytest.fixture
def repo(tmp_path) -> Path:
  root = tmp_path / "tasks"
  (root / "pkg").mkdir(parents=True)
  (root / "pkg" / "task.py").write_text("weight = 1.0\n")
  (root / "pkg" / "other.py").write_text("x = 1\n")
  (root / ".gitignore").write_text("logs/\n.venv/\n")
  (root / "logs").mkdir()
  (root / "logs" / "junk.txt").write_text("not code\n")

  def git(*args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

  subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
  git("config", "user.email", "t@example.com")
  git("config", "user.name", "T")
  git("add", "-A")
  git("commit", "-qm", "the task")
  # An uncommitted edit: what a launch usually finds.
  (root / "pkg" / "task.py").write_text("weight = 2.0\n")
  return root


def _show(repo: Path, tree: str, path: str) -> str:
  return subprocess.run(["git", "-C", str(repo), "show", f"{tree}:{path}"],
                        check=True, capture_output=True, text=True).stdout


def test_stamp_is_the_working_tree_as_it_is_now_kept_alive_under_the_records_ref(repo):
  code = trees.stamp(repo, "007")
  assert code["kind"] == "git" and code["tree"]
  assert code["ref"] == "refs/rlmcp/runs/007"
  assert _show(repo, code["tree"], "pkg/task.py") == "weight = 2.0\n"
  assert code["clean"] is False and code["dirty"]["files"] == 1
  # Ignored files are not code and are not in the tree.
  listing = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", code["tree"]],
                           check=True, capture_output=True, text=True).stdout
  assert "logs/junk.txt" not in listing


def test_a_variant_is_the_base_plus_a_patch_and_the_working_tree_never_moves(repo):
  base = trees.stamp(repo, "010")
  patch = (
      "--- a/pkg/task.py\n+++ b/pkg/task.py\n@@ -1 +1 @@\n"
      "-weight = 2.0\n+weight = 3.5\n")
  before = (repo / "pkg" / "task.py").read_text()

  code = trees.variant(repo, base["tree"], patch, "011")

  assert code["kind"] == "git" and code["tree"] != base["tree"]
  assert code["base"] == base["tree"]
  assert code["variant"]["files"] == 1
  assert _show(repo, code["tree"], "pkg/task.py") == "weight = 3.5\n"
  assert _show(repo, code["tree"], "pkg/other.py") == "x = 1\n", "the rest of the base is kept"
  # The person's editor saw nothing.
  assert (repo / "pkg" / "task.py").read_text() == before
  status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          check=True, capture_output=True, text=True).stdout
  assert "pkg/task.py" in status and "pkg/other.py" not in status
  # And a ref keeps the variant alive, like any stamped run.
  ref = subprocess.run(["git", "-C", str(repo), "rev-parse", "refs/rlmcp/runs/011"],
                       check=True, capture_output=True, text=True).stdout.strip()
  assert ref == code["tree"]


def test_a_patch_that_no_longer_fits_its_base_is_a_real_answer(repo):
  base = trees.stamp(repo, "020")
  stale = ("--- a/pkg/task.py\n+++ b/pkg/task.py\n@@ -1 +1 @@\n"
           "-weight = 99.0\n+weight = 3.5\n")
  with pytest.raises(trees.TreeError) as err:
    trees.variant(repo, base["tree"], stale, "021")
  assert "apply" in str(err.value)
  # No half-written ref either.
  gone = subprocess.run(["git", "-C", str(repo), "rev-parse", "-q", "--verify",
                         "refs/rlmcp/runs/021"], capture_output=True, text=True)
  assert gone.returncode != 0


def test_an_empty_patch_is_just_the_base_again(repo):
  base = trees.stamp(repo, "030")
  same = trees.variant(repo, base["tree"], "", "031")
  assert same["tree"] == base["tree"] and same["variant"]["files"] == 0


def test_materialize_writes_exactly_the_tree_and_replaces_an_older_copy(repo, tmp_path):
  code = trees.stamp(repo, "040")
  dest = tmp_path / "runs" / "040"
  dest.mkdir(parents=True)
  (dest / "stale.py").write_text("from a previous materialization\n")

  out = trees.materialize(code, dest)

  assert out == dest.resolve()
  assert (dest / "pkg" / "task.py").read_text() == "weight = 2.0\n"
  assert not (dest / "stale.py").exists(), "an older copy's files must not survive"
  assert not (dest / "logs").exists(), "ignored files are not in the tree"
  assert not (dest / ".git").exists(), "a copy, not a checkout"


def test_materialize_refuses_a_record_with_no_tree(tmp_path):
  with pytest.raises(trees.TreeError):
    trees.materialize({"kind": "none", "reason": "outside git"}, tmp_path / "x")


def test_import_roots_put_the_copy_first_and_src_when_the_layout_has_one(tmp_path):
  flat = tmp_path / "flat"; flat.mkdir()
  assert trees.import_roots(flat) == [str(flat)]
  src = tmp_path / "srcl"; (src / "src").mkdir(parents=True)
  assert trees.import_roots(src) == [str(src), str(src / "src")]


def test_stamp_outside_git_is_a_reason_not_an_exception(tmp_path):
  loose = tmp_path / "loose"; loose.mkdir()
  code = trees.stamp(loose, "050")
  assert code["kind"] == "none" and "not inside a git repository" in code["reason"]


def test_the_launcher_asks_rlmcp_whether_it_keeps_the_stamp(monkeypatch):
  from rlmcp.records.link import RecordLink
  monkeypatch.setattr(RecordLink, "KEEPS_LAUNCHER_STAMP", True, raising=False)
  assert trees.trainer_keeps_launcher_stamp() is True
  monkeypatch.delattr(RecordLink, "KEEPS_LAUNCHER_STAMP", raising=False)
  assert trees.trainer_keeps_launcher_stamp() is False
