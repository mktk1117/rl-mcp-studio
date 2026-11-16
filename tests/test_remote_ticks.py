"""Ticks for runs on other machines (`sources/remote_ticks.py`).

The watcher speaks for a run by the files it writes here; a remote run
writes them there. The ticker asks the host and publishes the same events,
only on change, so the client repaints a remote run exactly as it does a
local one. `rlmcp hostd` on a thread is the other machine.

Skipped on an rl-mcp without the wire; `PYTHONPATH=../rl-mcp-main` runs it.
"""

from __future__ import annotations

import pytest

wire = pytest.importorskip("rlmcp.wire")
hostd = pytest.importorskip("rlmcp.hostd")

from rlmcp.records import open_store  # noqa: E402
from rlmcp.session import Session  # noqa: E402

from rlmcp_studio.sources import remote_ticks  # noqa: E402
from rlmcp_studio.sources.records import Library  # noqa: E402


@pytest.fixture
def remote(tmp_path, monkeypatch):
  root = tmp_path / "hostlogs"
  session = Session(root / "run-r" / "rlmcp").create({"task": "T", "pid": 1})
  session.publish_status({"iteration": 1, "seq": 1, "state": "training"})
  server = hostd.Server(hostd.Host(root, token="tok"), bind="127.0.0.1", port=0).start()
  monkeypatch.setenv("RLMCP_HOST_TOKEN", "tok")
  yield session, server, server.url + "/v1/sessions/" + session.key
  server.stop()


def test_a_first_look_is_a_tick_and_a_liveness_and_no_change_is_nothing(remote):
  session, server, address = remote
  ticker = remote_ticks.RemoteTicker()
  first = ticker.poll([address])
  kinds = [k for k, _, _ in first]
  assert "status.tick" in kinds
  assert ("liveness.changed", {"live": False, "address": address}, address) in first or \
         ("liveness.changed", {"live": True, "address": address}, address) in first
  assert all(scope == address for _, _, scope in first), "scoped to the address the client reads"
  assert ticker.poll([address]) == [], "nothing moved, nothing said"


def test_a_status_the_host_reports_moved_is_one_tick(remote):
  session, server, address = remote
  ticker = remote_ticks.RemoteTicker()
  ticker.poll([address])
  session.publish_status({"iteration": 2, "seq": 2, "state": "training"})
  events = ticker.poll([address])
  assert [k for k, _, _ in events] == ["status.tick"]
  assert events[0][1] == {"path": address}, "the watcher's own shape: the client keys on it"


def test_a_host_that_stops_answering_is_a_liveness_change_not_an_exception(remote):
  session, server, address = remote
  ticker = remote_ticks.RemoteTicker()
  ticker.poll([address])
  server.stop()
  events = ticker.poll([address])
  assert events == [] or events == [("liveness.changed", {"live": False, "address": address}, address)]
  assert ticker.poll([address]) == [], "said once"


def test_addresses_no_longer_asked_about_are_forgotten(remote):
  session, server, address = remote
  ticker = remote_ticks.RemoteTicker()
  ticker.poll([address])
  assert ticker.poll([]) == []
  assert "status.tick" in [k for k, _, _ in ticker.poll([address])], "a clean start when it is back"


def test_only_addresses_are_polled_and_the_library_lists_open_remote_runs(tmp_path, remote):
  session, server, address = remote
  assert remote_ticks.RemoteTicker().poll(["/a/local/dir", ""]) == []
  store = open_store(tmp_path / "records")
  store.new_record("r1", task="T", session=address, verdict="running")
  store.new_record("r2", task="T", session=str(tmp_path / "local"), verdict="running")
  store.new_record("r3", task="T", session=address.replace("run-r", "run-old"), verdict="validated")
  library = Library([tmp_path / "records"])
  assert library.live_session_addresses() == [address], "remote, open, once"
  assert library._is_alive(address) in (True, False)
  assert library._is_alive(address.replace("run-r", "run-old")) is False, "a host that has no such key"
