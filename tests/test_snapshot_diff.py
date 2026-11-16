"""The code half of a recipe: what was different about the *code* of a run.

A recipe built from config edits alone cannot explain a run whose decisive
change was in the code -- a new action interface, say -- and that is often the
run that matters most. rl-mcp stamps the package at launch; this is the studio
reading that stamp, and none of the git work happens here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rlmcp.records import snapshot

from rlmcp_studio.sources import code as source_code


@pytest.fixture
def task_repo(tmp_path) -> Path:
  """A task package under git, one commit deep."""
  root = tmp_path / "tasks"
  (root / "my_tasks").mkdir(parents=True)
  (root / "my_tasks" / "task.py").write_text("weight = 1.0\n")

  def git(*args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

  subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
  git("config", "user.email", "t@example.com")
  git("config", "user.name", "T")
  git("add", "-A")
  git("commit", "-qm", "the task")
  return root


def _stamp(store, record_id: str, repo: Path):
  """Stamp a record the way a launch does, and hand back the fresh record."""
  code = snapshot.capture(repo / "my_tasks", record_id=record_id)
  store.update_record(record_id, lambda r: setattr(r, "code", code))
  return store.get_record(record_id)


def test_two_runs_are_compared_by_what_their_code_actually_was(store, task_repo):
  """Neither run committed anything. The comparison still works, which is the
  whole reason the snapshot stores a tree and not just a commit."""
  parent = _stamp(store, "001", task_repo)
  (task_repo / "my_tasks" / "task.py").write_text("weight = 1.0\nfilter = 'ema'\n")
  child = _stamp(store, "002", task_repo)

  answer = source_code.snapshot_diff(child, parent)

  assert answer["ok"] is True
  assert answer["from"] == "001" and answer["to"] == "002"
  assert answer["changed"] == 1 and answer["added"] == 1
  assert [f["path"] for f in answer["files"]] == ["my_tasks/task.py"]
  assert any("filter = 'ema'" in line for line in answer["files"][0]["lines"])


def test_a_whitespace_only_edit_is_greyed_rather_than_hidden(store, task_repo):
  parent = _stamp(store, "001", task_repo)
  (task_repo / "my_tasks" / "task.py").write_text("weight   =   1.0\n")
  child = _stamp(store, "002", task_repo)

  answer = source_code.snapshot_diff(child, parent)

  assert answer["files"][0]["trivial"] is True   # shown, marked, not dropped


def test_identical_code_reads_as_no_change_not_as_a_failure(store, task_repo):
  parent = _stamp(store, "001", task_repo)
  child = _stamp(store, "002", task_repo)

  answer = source_code.snapshot_diff(child, parent)

  assert answer["ok"] is True and answer["changed"] == 0 and answer["files"] == []


def test_a_run_with_no_snapshot_says_which_run_is_missing_one(store, task_repo):
  """A record written by an rl-mcp that does not stamp code carries none. That
  is a sentence to show, not an error to raise."""
  child = _stamp(store, "002", task_repo)

  answer = source_code.snapshot_diff(child, store.get_record("001"))

  assert answer["ok"] is False
  assert answer["missing"] == ["001"] and "no code snapshot" in answer["reason"]


def test_snapshots_from_two_different_repositories_are_refused(store, task_repo, tmp_path):
  parent = _stamp(store, "001", task_repo)
  child = _stamp(store, "002", task_repo)
  child.code["repo"] = str(tmp_path / "elsewhere")

  answer = source_code.snapshot_diff(child, parent)

  assert answer["ok"] is False and "different repositories" in answer["reason"]


def test_the_diff_endpoint_serves_the_code_diff_beside_the_config_one(
    client, store, task_repo):
  _stamp(store, "001", task_repo)
  (task_repo / "my_tasks" / "task.py").write_text("weight = 2.0\n")
  _stamp(store, "002", task_repo)

  payload = client.get("/api/diff", params={"record_id": "002"}).json()

  assert payload["code"]["ok"] is True
  assert payload["code"]["changed"] == 1
  assert payload["snapshot_note"] == ""    # nothing left to apologise for
