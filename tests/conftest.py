"""Fixtures that build the studio's world the way rl-mcp builds it.

The rule the studio lives by -- never re-implement rlmcp -- applies to its
tests first. Nothing here hand-writes a `status.json`, a `metrics.jsonl` row or
a record on disk: sessions come from :class:`rlmcp.session.Session`, records
from ``rlmcp.records.open_store``. That is what makes this suite a *contract*
test against whatever rl-mcp is installed rather than a test of a fixture
somebody wrote once and froze.

There is exactly one exception, and it is marked where it happens: pretending a
trainer is dead needs a pid that is not ours, and no API writes one.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from rlmcp.records import open_store
from rlmcp.session import Response, Session

from rlmcp_studio.app import create_app
from rlmcp_studio.config import resolve


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch, tmp_path_factory):
  """Every test gets a private session registry.

  ``Session.create`` announces itself machine-wide; without this the suite
  would write the developer's real state directory and tests would see each
  other's runs -- and the developer's actual training runs.
  """
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("xdg-state")))
  # And a private settings file. `/api/agent/settings` writes the choice it
  # is given; without this it would land in the developer's own settings
  # file, and the suite would switch the real studio to another provider and
  # leave it there.
  monkeypatch.setenv("RLMCP_STUDIO_SETTINGS",
                     str(tmp_path_factory.mktemp("settings") / "settings.json"))


# ── a session that looks like a run in progress ──────────────────────────
FRAME_ITERATION = 120
FRAME_NAME = f"shot_env3_it{FRAME_ITERATION:06d}.png"


def write_frame(path: Path, size=(64, 48)) -> Path:
  """A real PNG, because the annotation path opens it with Pillow."""
  from PIL import Image

  path.parent.mkdir(parents=True, exist_ok=True)
  Image.new("RGB", size, (40, 80, 160)).save(path)
  return path


@pytest.fixture
def session_dir(tmp_path) -> Path:
  """A live-looking rlmcp session: status, metrics, events, params, a frame.

  The pid in ``session.json`` is this process, so rlmcp reads the trainer as
  alive -- which is what lets the steering tests exercise the real refusal
  logic instead of mocking it away.
  """
  directory = tmp_path / "logs" / "run-042" / "rlmcp"
  session = Session(directory).create({"task": "Fake-Task-G1", "kind": "trainer"})

  session.publish_params({
      "parameters": [
          {"key": "reward.track_lin_vel.weight", "value": 2.0, "type": "float",
           "category": "reward"},
          {"key": "reward.action_rate_l2.weight", "value": -0.1, "type": "float",
           "category": "reward"},
      ],
  })
  for iteration in range(0, 5):
    session.append_metrics(iteration * 10, {
        "Train/mean_reward": 1.0 + iteration,
        "Train/mean_episode_length": 80.0 + iteration,
        "rlmcp/terrain_level_mean": 0.5 + iteration * 0.1,
    })
  session.append_event("set_parameter", {
      "iteration": 20, "key": "reward.action_rate_l2.weight",
      "old": -0.1, "new": -0.25, "applied": True, "rationale": "chatter",
  })
  session.append_event("curriculum_stage", {
      "iteration": 30, "stage": "rough", "reason": "flat mastered",
  })
  session.publish_status({
      "iteration": 40,
      "at": time.time(),
      "headline_metrics": {"Train/mean_reward": 5.0},
      "progress_video": {"enabled": True, "every": 50},
  })
  write_frame(session.artifacts / FRAME_NAME)
  return directory


def kill_session(directory: Path) -> None:
  """Make rlmcp read this trainer as dead.

  The one place the suite writes an rlmcp file by hand: liveness is decided
  from the pid in ``session.json`` and no API can put someone else's pid there.
  Pid 1 is always alive but its heartbeat here is ancient, which is rlmcp's
  "presumed recycled" case -- so this exercises the same branch a real dead
  run does.
  """
  path = Path(directory) / "session.json"
  payload = json.loads(path.read_text())
  payload["pid"] = 999_999_999          # no such process
  path.write_text(json.dumps(payload))


@pytest.fixture
def dead_session(session_dir) -> Path:
  kill_session(session_dir)
  return session_dir


class Trainer:
  """A stand-in for the training process, answering through the real protocol.

  It pops requests and responds exactly as ``rlmcp.core.controller`` does, so
  the studio's steering path is exercised end to end -- inbox, outbox, timeouts
  and all -- without importing a simulator.
  """

  def __init__(self, directory: Path, answers: Optional[Dict[str, Any]] = None):
    self.session = Session.open(directory)
    self.answers: Dict[str, Any] = answers or {}
    self.seen: List[Dict[str, Any]] = []
    self._stop = threading.Event()
    self._thread: Optional[threading.Thread] = None

  def start(self) -> "Trainer":
    self._thread = threading.Thread(target=self._serve, daemon=True)
    self._thread.start()
    return self

  def stop(self) -> None:
    self._stop.set()
    if self._thread:
      self._thread.join(timeout=2.0)

  def _serve(self) -> None:
    while not self._stop.is_set():
      for request in self.session.pop_requests():
        self.seen.append({"cmd": request.cmd, "args": dict(request.args)})
        answer = self.answers.get(request.cmd)
        if callable(answer):
          answer = answer(**request.args)
        if answer is None:
          self.session.respond(Response(req_id=request.req_id, ok=False,
                                        error=f"no such command: {request.cmd}"))
        else:
          self.session.respond(Response(req_id=request.req_id, ok=True,
                                        result=answer))
      time.sleep(0.01)


@pytest.fixture
def trainer(session_dir):
  """A running trainer that answers the read-only verbs."""
  shot = session_dir / "artifacts" / FRAME_NAME

  def screenshot(**args):
    return {"image_path": str(shot), "env_id": args.get("env_id", 3),
            "where": args.get("where"), "size": [64, 48]}

  runner = Trainer(session_dir, {
      "screenshot": screenshot,
      "status": {"iteration": 40, "state": "running"},
      "list_parameters": {"parameters": [
          {"key": "reward.track_lin_vel.weight", "value": 2.0, "live": True}]},
      "set_parameter": lambda **a: {"applied": True, **a},
      "diagnose": {"verdict": "chatter", "hf_share": 0.31},
  }).start()
  yield runner
  runner.stop()


# ── records ──────────────────────────────────────────────────────────────
@pytest.fixture
def records_root(tmp_path, session_dir) -> Path:
  """A records store with a small but realistic ancestry.

  Built through rlmcp's store, so the shapes are whatever the installed rl-mcp
  writes -- including the fields the studio reads but does not set.
  """
  root = tmp_path / "records"
  store = open_store(root)

  store.new_record(
      "flat_baseline", task="Fake-Task-G1", verdict="validated",
      hypothesis="flat first", prediction="reward over 3",
      outcome="it did", change=["from scratch"])
  store.new_record(
      "rough_gate", task="Fake-Task-G1", verdict="running", parent="001",
      hypothesis="a gate on chatter costs nothing",
      prediction="hf share falls under 0.15",
      session=str(session_dir), change=["action_rate_l2 -0.1 -> -0.25"])
  store.new_record(
      "other_task", task="Second-Task-G1", verdict="planned",
      hypothesis="an unrelated problem")
  return root


@pytest.fixture
def store(records_root):
  return open_store(records_root)


@pytest.fixture
def app(records_root):
  return create_app(resolve(records=[str(records_root)]))


@pytest.fixture
def client(app) -> TestClient:
  with TestClient(app) as running:
    yield running


@pytest.fixture
def frame(session_dir) -> Path:
  return session_dir / "artifacts" / FRAME_NAME


@pytest.fixture
def make_frame():
  """A PNG on disk, for tests that need something Pillow can open."""
  return write_frame


@pytest.fixture(autouse=True)
def _test_clients_present_the_token(monkeypatch):
  """Every route under /api needs the studio's credential (guard.py).

  A browser gets it as a cookie from the page; a test client gets it here,
  as the bearer header the fleet server and the CLI use, so tests about
  other routes reach them without each one handling the credential. A test
  *about* the guard drops the header again.
  """
  original = TestClient.__init__

  def init(self, app, *args, **kwargs):
    original(self, app, *args, **kwargs)
    guard = getattr(getattr(app, "state", None), "guard", None)
    if guard is not None:
      self.headers["authorization"] = f"Bearer {guard.token}"

  monkeypatch.setattr(TestClient, "__init__", init)
