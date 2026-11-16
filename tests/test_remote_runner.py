"""A run on another machine, launched from here (`runners/remote.py`).

`rlmcp hostd` on a thread is the other machine; a probe script standing in
for `rlmcp train` writes a session under the host's root the way a trainer
does. What is tested is the contract and the decisions: the host executes,
the session is discovered by task and time, the record here is filled from
the wire (the trainer there was given no record id), a cancel reaches the
process, and a host nobody wrote down does not exist.

Skipped on an rl-mcp without the wire; `PYTHONPATH=../rl-mcp-main` runs it.
"""

from __future__ import annotations

import asyncio
import sys
import time

import pytest
from fastapi.testclient import TestClient

wire = pytest.importorskip("rlmcp.wire")
hostd = pytest.importorskip("rlmcp.hostd")

from rlmcp.records import open_store  # noqa: E402

from rlmcp_studio.app import create_app  # noqa: E402
from rlmcp_studio.config import resolve  # noqa: E402
from rlmcp_studio.runners import remote  # noqa: E402
from rlmcp_studio.runners.base import JobState, RunSpec  # noqa: E402

PROBE = """
import json, os, sys, time
from pathlib import Path
from rlmcp.session import Session
# argv: ... train <task> --flags ; the log root is where a trainer would put it
args = sys.argv[1:]
task = args[args.index("train") + 1]
root = Path(args[args.index("--log-root") + 1]) if "--log-root" in args else Path("logs")
session = Session(root / time.strftime("%Y-%m-%d_%H-%M-%S") / "rlmcp").create({"task": task, "pid": os.getpid()})
session.publish_status({"iteration": 1, "state": "training"})
session.publish_params({"reward.weight": {"key": "reward.weight", "current": 2.5, "type": "float"},
                        "lr": {"key": "lr", "current": 0.001, "type": "float"}})
print("probe up", session.dir, flush=True)
assert "--record-run" not in args, "a remote trainer must not be given a record id"
for i in range(2, 400):
    for req in session.pop_requests():
        if req.cmd == "stop":
            from rlmcp.session import Response
            session.respond(Response(req_id=req.req_id, ok=True, result="stopping"))
            print("stopped cleanly", flush=True); sys.exit(0)
    session.publish_status({"iteration": i, "state": "training"})
    time.sleep(0.05)
"""


@pytest.fixture
def host(tmp_path):
  root = tmp_path / "hostlogs"
  root.mkdir()
  server = hostd.Server(hostd.Host(root, token="tok"), bind="127.0.0.1", port=0).start()
  yield server, root
  server.stop()


@pytest.fixture
def entry(host, tmp_path):
  server, root = host
  probe = tmp_path / "probe.py"
  probe.write_text(PROBE)
  return {"name": "box", "url": server.url, "token": "tok", "python": sys.executable,
          "cwd": str(tmp_path), "log_root": str(root), "_probe": str(probe)}


class ProbeRunner(remote.RemoteRunner):
  """`rlmcp train` replaced by the probe: same argv shape, no simulator."""

  def argv(self, spec, code_root=None):
    real = super().argv(spec, code_root=code_root)
    # [python, -c, ENTRY, train, task, --flags...] → [python, probe.py, train, task, --flags...]
    return [real[0], self.entry["_probe"], *real[3:]]


def _wait(runner, job_id, until, seconds=10.0):
  deadline = time.time() + seconds
  while time.time() < deadline:
    job = runner.poll(job_id)
    if until(job):
      return job
    time.sleep(0.1)
  return runner.poll(job_id)


def test_the_host_executes_and_the_session_is_discovered(entry, host):
  seen = {}
  runner = ProbeRunner(entry, on_session=lambda job, address: seen.update(job=job.id, address=address))
  spec = RunSpec(task="Demo-Task", entrypoint=["train"], record_id="004",
                 args={"num-envs": 8, "max-iterations": 3})
  job = asyncio.run(runner.submit(spec))
  assert job.state in (JobState.STARTING, JobState.RUNNING), job.error
  assert job.runner == "host:box" and job.workdir == entry["cwd"]

  job = _wait(runner, job.id, lambda j: bool(j.session_dir))
  assert job.session_dir.startswith(host[0].url + "/v1/sessions/"), job.session_dir
  assert seen == {"job": job.id, "address": job.session_dir}
  assert "probe up" in "\n".join(runner.logs(job.id))
  assert "--record-run" not in " ".join(runner.argv(spec)), "the record stays here"
  assert "--log-root" in runner.argv(spec)

  job = asyncio.run(runner.cancel(job.id))
  job = _wait(runner, job.id, lambda j: j.state.terminal)
  assert job.state in (JobState.SUCCEEDED, JobState.CANCELLED), (job.state, job.error)
  assert "stopped cleanly" in "\n".join(runner.logs(job.id)), "asked first, insisted second"


def test_capacity_is_the_hosts_and_an_unreachable_host_says_so(entry):
  slots = remote.RemoteRunner(entry).capacity()
  assert slots and all(s.id.startswith("box:") for s in slots)
  down = remote.RemoteRunner({**entry, "url": "http://127.0.0.1:9"}).capacity()
  assert down[0].busy_with == "unreachable"


def test_a_stamped_tree_is_sent_ahead_and_the_host_runs_from_it(entry, host, tmp_path):
  """D2: the worker gets the run's tree, not its own checkout."""
  if not hasattr(wire.WireHost, "push_tree"):
    pytest.skip("this rl-mcp's hostd has no trees face (rl-mcp #72)")
  import subprocess
  from rlmcp_studio.runners import trees
  repo = tmp_path / "tasks"
  (repo / "pkg").mkdir(parents=True)
  (repo / "pkg" / "task.py").write_text("weight = 7.0\n")
  for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"],
               ["add", "-A"], ["commit", "-qm", "task"]):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
  code = trees.stamp(repo, "006")
  (repo / "pkg" / "task.py").write_text("weight = 999.0\n")  # after the stamp: must not travel

  runner = ProbeRunner({**entry, "cwd": str(repo)})
  probe_reads = tmp_path / "probe_reads.py"
  probe_reads.write_text("import os, sys\nprint('cwd', os.getcwd())\n"
                         "print('task', open('pkg/task.py').read().strip())\n"
                         "print('code-root', repr(sys.argv[sys.argv.index('--code-root') + 1]))\n"
                         "print('pythonpath', os.environ.get('PYTHONPATH'))\n")
  runner.entry["_probe"] = str(probe_reads)
  spec = RunSpec(task="Demo-Task", entrypoint=["train"], record_id="006", code=code,
                 args={"max-iterations": 1})
  job = asyncio.run(runner.submit(spec))
  assert "runs the stamped tree" in job.note, job.note
  host_root = host[1]
  assert job.workdir.startswith(str(host_root / ".rlmcp-hostd" / "trees" / code["tree"]))
  assert job.describe()["materialized"] is True
  job = _wait(runner, job.id, lambda j: j.state.terminal)
  log = "\n".join(runner.logs(job.id))
  assert job.state == JobState.SUCCEEDED, log
  assert f"cwd {job.workdir}" in log
  assert "task weight = 7.0" in log, "the stamped content, not the later edit"
  assert "code-root ''" in log, "the record already carries the stamp"
  assert f"pythonpath {job.workdir}:{job.workdir}/src" in log

  # A second run of the same tree sends nothing: the host already has it.
  again = asyncio.run(runner.submit(spec))
  assert again.workdir == job.workdir
  asyncio.run(runner.cancel(again.id))


def test_a_host_without_the_trees_face_runs_its_own_checkout(entry, monkeypatch):
  runner = ProbeRunner(entry)
  monkeypatch.delattr(type(runner.host), "push_tree", raising=False)
  spec = RunSpec(task="Demo-Task", entrypoint=["train"], record_id="007",
                 code={"kind": "git", "tree": "abc123", "repo": "/nowhere"}, args={"max-iterations": 1})
  job = asyncio.run(runner.submit(spec))
  assert "runs its own checkout" in job.note and job.workdir == entry["cwd"]
  # It has a workdir and a stamp, and still did not run from the stamp: the
  # launch card reads this, so it must not say "a copy of the tree".
  assert job.describe()["materialized"] is False and job.describe()["tree"] == "abc123"
  asyncio.run(runner.cancel(job.id))


# Through the app: the launch button with a host named.


@pytest.fixture
def studio(entry, tmp_path, monkeypatch):
  records = tmp_path / "records"
  open_store(records)
  monkeypatch.setenv("RLMCP_STUDIO_HOSTS", "[]")
  cfg = resolve(records=[str(records)], settings=str(tmp_path / "settings.json"))
  cfg.hosts = [entry]
  app = create_app(cfg)
  # The probe stands in for `rlmcp train` on the host.
  for runner in app.state.hosts.runners.values():
    runner.__class__ = ProbeRunner
  with TestClient(app) as client:
    yield client, app, records


def test_launching_on_a_host_fills_the_record_from_the_wire(studio):
  client, app, records = studio
  answer = client.post("/api/build/launch", json={
      "task": "Demo-Task", "slug": "on-the-box", "hypothesis": "it trains there",
      "num_envs": 8, "max_iterations": 3, "host": "box"})
  assert answer.status_code == 200, answer.text
  body = answer.json()
  assert body["ok"] and body["job"]["runner"] == "host:box"
  assert body["command"].startswith("[box] ")
  record_id = body["record"]

  store = open_store(records)
  for _ in range(100):
    jobs = client.get("/api/build/launches").json()["jobs"]
    mine = next(j for j in jobs if j["id"] == body["job"]["id"])
    if mine["session_dir"]:
      break
    time.sleep(0.1)
  assert mine["session_dir"].startswith("http://"), "the session is a wire address"
  record = store.get_record(record_id)
  assert record.session == mine["session_dir"], "written here, from what the host reported"
  assert record.verdict == "running"
  for _ in range(50):
    record = store.get_record(record_id)
    if record.config:
      break
    client.get("/api/build/launches")  # a poll is what fills it
    time.sleep(0.1)
  assert record.config == {"reward.weight": 2.5, "lr": 0.001}, "the snapshot, from the wire"

  # And the session routes read it, because the record names it.
  snap = client.get("/api/session", params={"path": record.session}).json()
  assert snap["ok"] and snap["status"]["state"] == "training"

  cancelled = client.post("/api/build/launch/cancel", json={"job": mine["id"]}).json()["job"]
  assert cancelled["runner"] == "host:box"


def test_a_host_nobody_wrote_down_does_not_exist(studio):
  client, app, records = studio
  answer = client.post("/api/build/launch", json={
      "task": "Demo-Task", "slug": "nowhere", "hypothesis": "h", "host": "ghost"})
  assert answer.status_code == 404
  assert "ghost" in answer.json()["error"] and answer.json()["hosts"][0]["name"] == "box"
  assert open_store(records).list_records() == [], "no orphan record"


def test_a_launch_the_host_refused_still_lists_with_its_error(tmp_path, monkeypatch):
  """The listing route must never raise on a job that never reached the host."""
  records = tmp_path / "records"
  open_store(records)
  monkeypatch.setenv("RLMCP_STUDIO_HOSTS", "[]")
  cfg = resolve(records=[str(records)], settings=str(tmp_path / "settings.json"))
  cfg.hosts = [{"name": "down", "url": "http://127.0.0.1:9", "token": "x",
                "python": sys.executable, "cwd": str(tmp_path)}]
  app = create_app(cfg)
  with TestClient(app) as client:
    answer = client.post("/api/build/launch", json={
        "task": "Demo-Task", "slug": "nowhere", "hypothesis": "h", "host": "down"})
    assert answer.status_code == 200 and answer.json()["ok"] is False
    assert "did not take it" in answer.json()["job"]["error"]
    listed = client.get("/api/build/launches").json()
    mine = [j for j in listed["jobs"] if j["runner"] == "host:down"]
    assert mine and mine[0]["state"] == "failed" and mine[0]["log"] == []
    assert listed["capacity"][-1]["busy_with"] == "unreachable"


def test_a_run_the_studio_did_not_start_still_counts_as_training(client, session_dir):
  """A trainer launched from a terminal with `--record-run` holds a lease and
  writes a session exactly like one of ours; the capacity answer must not
  report its GPU as free, and the launches list is where it becomes visible."""
  listed = client.get("/api/build/launches").json()
  external = [j for j in listed["jobs"] if j["id"].startswith("external-")]
  assert external and external[0]["record_id"] == "002"
  assert external[0]["state"] == "running"
  assert external[0]["session_dir"] == str(session_dir)
  assert listed["capacity"][0]["busy_with"] == external[0]["id"]

  # And when the trainer dies, the slot frees itself: liveness is the
  # heartbeat's answer, not a job table's.
  from tests.conftest import kill_session
  kill_session(session_dir)
  listed = client.get("/api/build/launches").json()
  assert not [j for j in listed["jobs"] if j["id"].startswith("external-")]
  assert not listed["capacity"][0]["busy_with"]
