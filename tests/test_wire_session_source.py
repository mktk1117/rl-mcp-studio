"""A run on another machine, in the studio's own panels.

`sources/session.py` reads through rl-mcp's session client and never joins
a path, so the same `LiveSession` answers for a directory here and a URL on
a host. These tests run `rlmcp hostd` on a thread over a real session
directory and ask the studio's questions of both addresses, requiring the
same answer -- and they ask the artifact route for a picture by its wire
address, which is what the client composes from `snapshot.dir`.

Skipped on an rl-mcp without the wire (before rl-mcp #71); run with
`PYTHONPATH=../rl-mcp-main` to exercise it against current main.
"""

from __future__ import annotations


import pytest

wire = pytest.importorskip("rlmcp.wire")
hostd = pytest.importorskip("rlmcp.hostd")

from rlmcp.session import Session  # noqa: E402

from rlmcp_studio.sources import session as source_session  # noqa: E402


@pytest.fixture
def local(tmp_path) -> Session:
  session = Session(tmp_path / "logs" / "run-x" / "rlmcp").create({"task": "Fake-Task-G1", "num_envs": 4})
  session.publish_status({"iteration": 5, "state": "training"})
  session.append_metrics(1, {"Train/mean_reward": 0.1})
  session.append_metrics(2, {"Train/mean_reward": 0.4})
  session.append_event("set_parameter", {"iteration": 2, "key": "lr", "old": 1e-3, "new": 5e-4,
                                         "applied": True, "rationale": "too hot"})
  session.artifacts.mkdir(parents=True, exist_ok=True)
  (session.artifacts / "frame_000010.png").write_bytes(b"\x89PNG\r\n" + b"x" * 40)
  return session


@pytest.fixture
def host(tmp_path, local):
  server = hostd.Server(hostd.Host(tmp_path / "logs", token="tok"), bind="127.0.0.1", port=0).start()
  yield server
  server.stop()


@pytest.fixture
def address(host, local, monkeypatch) -> str:
  monkeypatch.setenv("RLMCP_HOST_TOKEN", "tok")
  return host.url + "/v1/sessions/" + local.key


def test_open_client_picks_the_transport(local, address):
  assert isinstance(source_session.open_client(local.dir), Session)
  assert isinstance(source_session.open_client(address), wire.WireSession)
  assert source_session.is_address(address) and not source_session.is_address(str(local.dir))


def test_a_remote_snapshot_is_the_local_one_with_a_different_address(local, address):
  here = source_session.LiveSession(local.dir).snapshot()
  there = source_session.LiveSession(address).snapshot()
  assert there["ok"] and here["ok"]
  assert there["dir"] == there["address"] == address
  for key in ("state", "task", "started_at", "status", "metrics", "series"):
    assert there[key] == here[key], key
  assert [a["name"] for a in there["artifacts"]] == ["frame_000010.png"]
  assert there["artifacts"] == here["artifacts"]


def test_events_and_their_readings_cross_the_wire(local, address):
  assert source_session.events(address) == source_session.events(local.dir)
  assert source_session.interventions(address) == source_session.interventions(local.dir)
  assert source_session.parameter_traces(address) == source_session.parameter_traces(local.dir)
  assert source_session.parameter_traces(address)["traces"], "the edit is a step"


def test_a_remote_session_has_no_directory_and_says_so(address):
  live = source_session.LiveSession(address)
  assert live.exists()
  with pytest.raises(FileNotFoundError):
    _ = live.dir
  assert live.read_artifact("frame_000010.png").startswith(b"\x89PNG")
  with pytest.raises(ValueError):
    live.read_artifact("../session.json")


def test_a_host_that_does_not_know_the_key_is_a_missing_session(host, monkeypatch):
  monkeypatch.setenv("RLMCP_HOST_TOKEN", "tok")
  gone = source_session.LiveSession(host.url + "/v1/sessions/run-x/nope")
  assert not gone.exists()
  assert gone.snapshot()["ok"] is False


# The artifact route, by wire address.


def test_the_artifact_route_serves_a_remote_picture_the_studio_knows(client, store, address,
                                                                     local, monkeypatch):
  # The client composes `${dir}/artifacts/${name}` with `dir` the address.
  record = store.new_record("remote-run", task="Fake-Task-G1", session=address)
  answer = client.get("/api/artifact", params={"path": f"{address}/artifacts/frame_000010.png"})
  assert answer.status_code == 200, answer.text
  assert answer.headers["content-type"].startswith("image/png")
  assert answer.content.startswith(b"\x89PNG")
  assert record.session == address


def test_the_artifact_route_refuses_a_url_the_studio_does_not_know(client, address):
  stranger = address.replace("run-x", "run-y")
  answer = client.get("/api/artifact", params={"path": f"{stranger}/artifacts/frame_000010.png"})
  assert answer.status_code == 403
  answer = client.get("/api/artifact", params={"path": "http://evil.example/v1/sessions/a/b/artifacts/x.png"})
  assert answer.status_code == 403
  answer = client.get("/api/artifact", params={"path": address})
  assert answer.status_code == 400, "an address with no artifact name is not an artifact"


# The three session routes, by wire address.


def test_the_session_routes_answer_for_a_remote_run_the_studio_knows(client, store, address, local):
  store.new_record("remote-run", task="Fake-Task-G1", session=address)
  snap = client.get("/api/session", params={"path": address}).json()
  assert snap["ok"] and snap["address"] == address and snap["status"]["iteration"] == 5
  events = client.get("/api/events", params={"path": address}).json()["events"]
  assert [e["kind"] for e in events] == ["set_parameter"]
  traces = client.get("/api/traces", params={"path": address}).json()
  assert [t["key"] for t in traces["traces"]] == ["lr"]


def test_the_session_routes_refuse_an_address_nobody_named(client, address):
  answer = client.get("/api/session", params={"path": address.replace("run-x", "run-z")})
  assert answer.status_code == 400
  assert "not a session" in answer.json()["error"]
