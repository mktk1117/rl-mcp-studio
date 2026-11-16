"""The code pane: what may be read, what may be written, and the diff.

The save endpoint is the one place in the studio that can destroy work, so its
three guards get a test each.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlmcp_studio.sources import code


@pytest.fixture
def package(records_root) -> Path:
  """A task package beside the records, which is where the studio looks."""
  repo = records_root.parent
  (repo / "task" / "rewards").mkdir(parents=True, exist_ok=True)
  (repo / "task" / "__init__.py").write_text("VERSION = 1\n")
  (repo / "task" / "rewards" / "gait.py").write_text(
      "def action_rate_l2(env):\n  return 0.0\n")
  (repo / "task" / "notes.md").write_text("# notes\n")
  (repo / "task" / "weights.pt").write_bytes(b"\x00\x01\x02")
  return repo


def test_the_source_root_is_the_repository_holding_the_records(records_root):
  assert code.source_roots([records_root]) == [records_root.parent]


def test_the_tree_is_flat_depth_tagged_and_only_text(package):
  entries = code.tree(package)
  names = {e["name"] for e in entries}

  assert {"task", "rewards", "gait.py", "notes.md"} <= names
  assert "weights.pt" not in names, "a checkpoint is not source"
  assert {e["depth"] for e in entries if e["name"] == "gait.py"} == {2}


def test_reading_a_file_returns_its_lines_and_size(package):
  """Lines, not one blob: the pane numbers them and jumps to one."""
  answer = code.read(package / "task" / "rewards" / "gait.py")
  assert answer["ok"] and answer["name"] == "gait.py"
  assert answer["lines"][0].startswith("def action_rate_l2")
  assert answer["size"] > 0


def test_reading_something_that_is_not_a_file_says_so(package):
  assert code.read(package / "task")["ok"] is False


def test_a_path_outside_every_root_is_refused(package):
  with pytest.raises(code.OutsideRoot):
    code.resolve_under("/etc/passwd", [package])


def test_dot_dot_is_collapsed_before_the_check_not_after(package):
  """Checking the unresolved string is the classic way to get this wrong."""
  with pytest.raises(code.OutsideRoot):
    code.resolve_under(str(package / "task" / ".." / ".." / ".." / "etc"), [package])


# ── the write path ───────────────────────────────────────────────────────
def source_path(client, package) -> str:
  return str(package / "task" / "rewards" / "gait.py")


def test_a_save_writes_the_file(client, package):
  path = source_path(client, package)
  size = Path(path).stat().st_size
  answer = client.put("/api/source/file", json={
      "path": path, "text": "def action_rate_l2(env):\n  return 1.0\n",
      "expect_size": size})

  assert answer.status_code == 200
  assert "1.0" in Path(path).read_text()


def test_a_save_is_refused_when_the_file_changed_underneath_you(client, package):
  """Almost always the agent or a git checkout. Overwriting is how work
  disappears."""
  path = source_path(client, package)
  answer = client.put("/api/source/file", json={
      "path": path, "text": "whatever", "expect_size": 999_999})

  assert answer.status_code == 409
  assert "changed on disk" in answer.json()["error"]
  assert "whatever" not in Path(path).read_text()


def test_a_save_outside_the_root_is_refused(client, package, tmp_path_factory):
  outside = tmp_path_factory.mktemp("elsewhere") / "elsewhere.py"
  outside.write_text("x = 1\n")
  answer = client.put("/api/source/file",
                      json={"path": str(outside), "text": "x = 2\n"})

  assert answer.status_code == 403
  assert outside.read_text() == "x = 1\n", "the file must be untouched"


def test_saving_creates_nothing(client, package):
  """This endpoint saves edits; a typo in a path must not become a new file."""
  missing = package / "task" / "not_yet.py"
  answer = client.put("/api/source/file",
                      json={"path": str(missing), "text": "hello"})

  assert answer.status_code == 404
  assert not missing.exists()


# ── the diff ─────────────────────────────────────────────────────────────
def test_config_diff_is_exact_and_keeps_the_three_kinds_apart():
  diff = code.config_diff(
      child={"rl": {"lr": 0.001}, "gamma": 0.99, "new_knob": 1},
      parent={"rl": {"lr": 0.0005}, "gamma": 0.99, "dropped": True})

  assert diff["changed"] == [{"key": "rl.lr", "old": 0.0005, "new": 0.001}]
  assert diff["added"] == [{"key": "new_knob", "new": 1}]
  assert diff["removed"] == [{"key": "dropped", "old": True}]
  # An unchanged value is not a change; it is counted, not listed.
  assert diff["same"] == 1


def test_a_range_pair_is_one_value_not_two():
  """`[-1.0, 1.0]` is a command range. Flattened per element it would read as
  two unrelated knobs that both changed."""
  diff = code.config_diff({"cmd": {"vx": [-1.0, 2.0]}}, {"cmd": {"vx": [-1.0, 1.0]}})
  assert diff["changed"] == [{"key": "cmd.vx", "old": [-1.0, 1.0], "new": [-1.0, 2.0]}]


def test_relevance_prefers_the_task_and_never_counts_writing_as_code():
  assert code.relevance("my_locomotion/tasks/steering/rewards.py",
                        ["steering", "locomotion"])
  # Notes, logs and records are about what you wrote, not what the policy did.
  assert not code.relevance("docs/NEXT-walking-style.md", ["steering"])
  assert not code.relevance("records/runs/001/meta.json", ["steering"])
  assert not code.relevance("logs/rsl_rl/run/events.jsonl", ["steering"])
  # With no hint matching, source is still plausibly relevant and a lockfile
  # never is.
  assert code.relevance("tools/helper.py", ["nothing"])
  assert not code.relevance("uv.lock", ["nothing"])


def test_the_diff_endpoint_admits_when_the_patch_is_only_the_tree_now(client):
  """A run launched without a code snapshot has none, so the worktree
  patch is all there is -- and the payload says so rather than implying the
  patch is what that run used. See test_snapshot_diff.py for the stamped case."""
  payload = client.get("/api/diff", params={"record_id": "002"}).json()

  assert payload["against"] == "001"
  assert payload["config"] is not None
  assert payload["code"]["ok"] is False
  assert "this repository right now" in payload["snapshot_note"]


def test_a_root_run_has_no_parent_to_diff_against(client):
  payload = client.get("/api/diff", params={"record_id": "001"}).json()
  assert payload["no_parent"] is True and payload["config"] is None


def test_the_diff_says_which_keys_it_covers():
  """"Identical" and "not in the snapshot" look the same on screen otherwise —
  which is how a Diff view can say "0 of 67 differ" directly above the
  record's own "reward_scale 3.0 -> 8.0"."""
  diff = code.config_diff({"rl": {"lr": 0.001}}, {"rl": {"lr": 0.001}})
  assert diff["keys"] == ["rl.lr"]
  assert diff["changed"] == []


def test_an_unreadably_long_diff_line_is_cut_and_says_so():
  """A minified file is one diff line hundreds of thousands of characters long.
  Rendered whole it lays out to millions of pixels: nothing looks broken,
  because the pane scrolls, but the browser measures every pixel of it and no
  human is ever going to scroll there."""
  from rlmcp_studio.sources.code import MAX_DIFF_LINE, clip_lines

  long_line = "-const RUNS=" + "x" * 300_000
  out = clip_lines(["-def f():", long_line, "+    return 1"])

  assert out[0] == "-def f():" and out[2] == "+    return 1", "short lines untouched"
  assert len(out[1]) < MAX_DIFF_LINE + 60
  assert out[1].startswith("-const RUNS=")
  assert "more characters" in out[1], "what was cut has to be said"


# ── the third clock ──────────────────────────────────────────────────────
def test_the_newest_change_in_a_package_is_readable(tmp_path):
  """Two Build panels hold a *moment*: the preview holds the environment it
  constructed when it started, and the gate strip holds the answer `rlmcp
  check` gave when it ran. An edit invalidates both and neither notices, so
  the green gates and the running preview end up describing different versions
  of the same task with nothing on screen to say so."""
  import os

  from rlmcp_studio.sources.code import newest_change

  package = tmp_path / "task_pkg"
  (package / "mdp").mkdir(parents=True)
  (package / "__init__.py").write_text("x = 1\n")
  rewards = package / "mdp" / "rewards.py"
  rewards.write_text("def upright(): ...\n")
  os.utime(package / "__init__.py", (1_700_000_000, 1_700_000_000))
  os.utime(rewards, (1_700_000_500, 1_700_000_500))

  found = newest_change(package)
  assert found["at"] == 1_700_000_500
  assert found["file"].endswith("rewards.py"), "and which file it was"
  assert found["files"] == 2


def test_reading_a_package_does_not_look_like_editing_it(tmp_path):
  """`__pycache__` is rewritten by *importing* the package -- which the gates
  and the preview both do -- so counting it would report an edit nobody made,
  every time, and the note would be permanent and therefore ignored. Same skip
  list as the file tree, so "the task's files" means one thing."""
  import os

  from rlmcp_studio.sources.code import newest_change

  package = tmp_path / "task_pkg"
  (package / "__pycache__").mkdir(parents=True)
  (package / "__init__.py").write_text("x = 1\n")
  cached = package / "__pycache__" / "__init__.cpython-312.pyc"
  cached.write_bytes(b"\x00")
  os.utime(package / "__init__.py", (1_700_000_000, 1_700_000_000))
  os.utime(cached, (1_800_000_000, 1_800_000_000))

  assert newest_change(package)["at"] == 1_700_000_000


def test_a_package_that_is_not_there_yet_has_no_clock(tmp_path):
  """A task begun in the GUI has no directory for a moment. Zero, so nothing
  is ever reported as stale against a package that does not exist."""
  from rlmcp_studio.sources.code import newest_change

  assert newest_change(tmp_path / "nothing-here") == {"at": 0.0, "file": "", "files": 0}
