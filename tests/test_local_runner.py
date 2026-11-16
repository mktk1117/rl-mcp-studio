"""`LocalRunner` runs from the stamped tree, not from the working tree.

The runner is exercised with a stand-in for the task environment whose argv
is a Python one-liner that reports where it ran and what it imported, so no
simulator, no rl-mcp CLI and no GPU are involved -- only the contract:
the copy is where the process executes, the copy is first on PYTHONPATH,
an edit to the source after the stamp does not reach the run, the trainer
is told not to re-stamp, and the copy is gone when the run succeeds.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

from rlmcp_studio.runners import trees
from rlmcp_studio.runners.base import JobState, RunSpec
from rlmcp_studio.runners.local import LocalRunner

PROBE = (
    "import json, os, sys; "
    "print(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:], "
    "'pythonpath': os.environ.get('PYTHONPATH', ''), "
    "'task': open('pkg/task.py').read()}))"
)


class ProbeEnv:
  """A TaskEnv whose 'rlmcp' is a script that reports its surroundings."""

  def __init__(self, cwd: str):
    self.python = sys.executable
    self.cwd = cwd
    self.packages: List[str] = []

  def argv(self, *command: str) -> List[str]:
    return [self.python, "-c", PROBE, *command]

  def environ(self):
    return {**os.environ, "PYTHONPATH": "/already/there"}

  def describe(self):
    return {"python": self.python, "cwd": self.cwd, "own": True}


@pytest.fixture
def repo(tmp_path) -> Path:
  root = tmp_path / "tasks"
  (root / "pkg").mkdir(parents=True)
  (root / "pkg" / "task.py").write_text("weight = 1.0\n")

  def git(*args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

  subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
  git("config", "user.email", "t@example.com")
  git("config", "user.name", "T")
  git("add", "-A")
  git("commit", "-qm", "the task")
  return root


def _run(runner: LocalRunner, spec: RunSpec):
  async def go():
    job = await runner.submit(spec)
    for _ in range(200):
      if runner.poll(job.id).state.terminal:
        break
      await asyncio.sleep(0.05)
    return runner.poll(job.id)
  return asyncio.run(go())


def _report(runner: LocalRunner, job_id: str) -> dict:
  lines = [line for line in runner.logs(job_id) if line.startswith("{")]
  assert lines, list(runner.logs(job_id))
  return json.loads(lines[-1])


def test_a_stamped_run_executes_from_its_own_copy_of_the_tree(repo, tmp_path, monkeypatch):
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
  from rlmcp.records.link import RecordLink
  monkeypatch.setattr(RecordLink, "KEEPS_LAUNCHER_STAMP", True, raising=False)

  code = trees.stamp(repo, "001")
  # The edit that must not reach the run: made after the stamp, before launch.
  (repo / "pkg" / "task.py").write_text("weight = 999.0\n")

  runner = LocalRunner(ProbeEnv(str(repo)), log_root=str(tmp_path / "logs"))
  spec = RunSpec(task="Fake-Task", entrypoint=["train"], record_id="001",
                 workdir=str(repo), code=code)
  job = _run(runner, spec)

  assert job.state == JobState.SUCCEEDED, (job.error, list(runner.logs(job.id)))
  seen = _report(runner, job.id)
  copy = Path(seen["cwd"])
  assert copy != repo.resolve() and copy.name == "001", "ran from a copy named after the record"
  assert seen["task"] == "weight = 1.0\n", "the stamped content, not the later edit"
  assert seen["pythonpath"].split(os.pathsep)[0] == str(copy), "the copy imports first"
  assert "/already/there" in seen["pythonpath"], "and the environment's own path survives"
  assert "--code-root" in seen["argv"] and seen["argv"][seen["argv"].index("--code-root") + 1] == "", \
      "the trainer is told the record already carries the stamp"
  assert job.describe()["materialized"] is True and job.describe()["tree"] == code["tree"]
  assert not copy.exists(), "a finished run's copy is removed; the tree lives on under its ref"
  # Logs stayed beside the source, where the rail looks.
  assert list((tmp_path / "logs").rglob("*.log"))


def test_an_older_rlmcp_that_would_erase_the_stamp_gets_the_source_tree_instead(
    repo, tmp_path, monkeypatch):
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
  from rlmcp.records.link import RecordLink
  monkeypatch.delattr(RecordLink, "KEEPS_LAUNCHER_STAMP", raising=False)

  code = trees.stamp(repo, "002")
  runner = LocalRunner(ProbeEnv(str(repo)), log_root=str(tmp_path / "logs"))
  job = _run(runner, RunSpec(task="Fake-Task", entrypoint=["train"], record_id="002",
                             workdir=str(repo), code=code))

  assert job.state == JobState.SUCCEEDED, job.error
  seen = _report(runner, job.id)
  assert seen["argv"][seen["argv"].index("--code-root") + 1] == str(repo.resolve()) or \
         seen["argv"][seen["argv"].index("--code-root") + 1] == str(repo)
  assert "cannot keep a launcher's stamp" in job.note


def test_no_stamp_means_the_working_tree_as_before(repo, tmp_path):
  runner = LocalRunner(ProbeEnv(str(repo)), log_root=str(tmp_path / "logs"))
  job = _run(runner, RunSpec(task="Fake-Task", entrypoint=["train"], record_id="003",
                             workdir=str(repo), code={"kind": "none", "reason": "outside git"}))
  assert job.state == JobState.SUCCEEDED, job.error
  seen = _report(runner, job.id)
  assert Path(seen["cwd"]) == repo.resolve()
  assert "--code-root" not in seen["argv"], "the trainer stamps its cwd, as it always did"
  assert job.describe()["materialized"] is False


def test_materialize_off_is_the_escape_hatch(repo, tmp_path, monkeypatch):
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
  code = trees.stamp(repo, "004")
  runner = LocalRunner(ProbeEnv(str(repo)), log_root=str(tmp_path / "logs"))
  job = _run(runner, RunSpec(task="Fake-Task", entrypoint=["train"], record_id="004",
                             workdir=str(repo), code=code, materialize=False))
  assert job.state == JobState.SUCCEEDED, job.error
  assert Path(_report(runner, job.id)["cwd"]) == repo.resolve()


def test_a_failed_run_keeps_its_copy_for_a_look(repo, tmp_path, monkeypatch):
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
  from rlmcp.records.link import RecordLink
  monkeypatch.setattr(RecordLink, "KEEPS_LAUNCHER_STAMP", True, raising=False)

  class FailingEnv(ProbeEnv):
    def argv(self, *command):
      return [self.python, "-c", "import sys; print('boom'); sys.exit(3)", *command]

  code = trees.stamp(repo, "005")
  runner = LocalRunner(FailingEnv(str(repo)), log_root=str(tmp_path / "logs"))
  job = _run(runner, RunSpec(task="Fake-Task", entrypoint=["train"], record_id="005",
                             workdir=str(repo), code=code))
  assert job.state == JobState.FAILED and job.exit_code == 3
  assert Path(job.workdir).exists(), "what it ran from is still there to inspect"


# Through the app: what the Build view's launch card is drawn from.


def _studio(repo, tmp_path, monkeypatch):
  """A studio whose task environment is `repo` and whose trainer is the probe."""
  from fastapi.testclient import TestClient
  from rlmcp.records import open_store
  from rlmcp_studio.app import create_app
  from rlmcp_studio.config import resolve
  from rlmcp_studio.sources import taskenv

  records = tmp_path / "records"
  open_store(records)
  monkeypatch.setattr(taskenv, "from_roots",
                      lambda roots, packages=(): taskenv.TaskEnv(sys.executable, str(repo), []))
  monkeypatch.setattr(taskenv.TaskEnv, "argv",
                      lambda self, *command: [sys.executable, "-c", PROBE, *command])
  app = create_app(resolve(records=[str(records)], settings=str(tmp_path / "settings.json")))
  return TestClient(app), records


def _settled(client, record_id: str) -> dict:
  import time
  job: dict = {}
  for _ in range(200):
    jobs = client.get("/api/build/launches", params={"tail": 0}).json()["jobs"]
    job = next(j for j in jobs if j.get("record_id") == record_id)
    if job["state"] in ("succeeded", "failed", "cancelled"):
      return job
    time.sleep(0.05)
  raise AssertionError(f"{record_id} never finished: {job}")


def test_the_launch_list_says_which_tree_a_run_executed_from_and_where(
    repo, tmp_path, monkeypatch):
  """The Build view's launch card shows what a run actually ran: the tree the
  record was stamped with, whether the process ran from a copy of it or from
  the working tree, and where. All of it has to arrive in
  `/api/build/launches`, the list the card is drawn from -- the record alone
  says what was stamped, not whether the run used it."""
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
  from rlmcp.records import open_store
  from rlmcp.records.link import RecordLink
  monkeypatch.setattr(RecordLink, "KEEPS_LAUNCHER_STAMP", True, raising=False)
  (repo / "pkg" / "task.py").write_text("weight = 2.0\n")   # not committed at launch

  client, records = _studio(repo, tmp_path, monkeypatch)
  with client:
    copied = client.post("/api/build/launch", json={
        "task": "Fake-Task", "slug": "copied", "hypothesis": "it runs the stamp",
        "num_envs": 1, "device": "cpu"}).json()
    job = _settled(client, copied["record"])
    stamped = open_store(records).get_record(copied["record"]).code

    assert job["state"] == "succeeded", job
    assert job["tree"] and job["tree"] == stamped["tree"], "the tree the record carries"
    assert job["clean"] is False, "the stamp took the edit nobody committed, and says so"
    assert job["materialized"] is True
    assert job["workdir"] == str(trees.runs_home() / copied["record"])
    assert copied["job"]["materialized"] is True, "the launch answer says so too"

    # The escape hatch: the same kind of stamp, but the run did not use it.
    in_place = client.post("/api/build/launch", json={
        "task": "Fake-Task", "slug": "in-place", "hypothesis": "it runs the checkout",
        "num_envs": 1, "device": "cpu", "in_place": True, "anyway": True}).json()
    job = _settled(client, in_place["record"])
    assert job["tree"] and job["materialized"] is False and job["workdir"] == ""


def test_the_launch_list_carries_the_runners_note(repo, tmp_path, monkeypatch):
  """An rl-mcp that would erase the launcher's stamp gets the source tree
  instead, and the runner says so in `note` -- which only helps if the list
  the launch card is drawn from carries it."""
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
  from rlmcp.records.link import RecordLink
  monkeypatch.delattr(RecordLink, "KEEPS_LAUNCHER_STAMP", raising=False)

  client, _ = _studio(repo, tmp_path, monkeypatch)
  with client:
    launched = client.post("/api/build/launch", json={
        "task": "Fake-Task", "slug": "old-rlmcp", "hypothesis": "the note survives",
        "num_envs": 1, "device": "cpu"}).json()
    job = _settled(client, launched["record"])
  assert "cannot keep a launcher's stamp" in job["note"], job
