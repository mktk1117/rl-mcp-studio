"""Every event names its host and its run.

On a fleet a run is ``(host, run)`` on the wire and never a path. On one
desktop that is one constant host and a record id looked up from whatever
directory the watcher touched -- the degenerate case the remote tier is
designed as, exercised here so the envelope does not have to change later.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from rlmcp_studio import events
from rlmcp_studio.config import resolve
from rlmcp_studio.app import Hub


@pytest.fixture(autouse=True)
def _host(monkeypatch):
  monkeypatch.setattr(events, "HOST", "h-test")


def test_the_envelope_carries_host_and_run():
  evt = events.event("records.changed", {"record": "007"}, "007", run="007")
  payload = evt.to_dict()
  assert payload["host"] == "h-test" and payload["run"] == "007"
  assert set(payload) == {"v", "seq", "kind", "scope", "run", "host", "at", "data"}


def test_set_host_names_every_event_from_then_on():
  events.set_host("h-other")
  assert events.event("hello").to_dict()["host"] == "h-other"


class _Client:
  def __init__(self):
    self.sent = []

  async def send_text(self, text):
    self.sent.append(json.loads(text))


def _publish(hub, *args, **kwargs):
  asyncio.run(hub.publish(*args, **kwargs))


def test_a_publisher_that_names_its_record_is_believed():
  hub = Hub(); client = _Client(); hub.clients.add(client)
  _publish(hub, "records.changed", {"record": "012"}, "012")
  assert client.sent[-1]["run"] == "012" and client.sent[-1]["host"] == "h-test"


def test_a_publisher_that_only_knows_a_directory_is_answered_by_the_library(tmp_path):
  hub = Hub(); client = _Client(); hub.clients.add(client)
  session = tmp_path / "logs" / "run-a" / "rlmcp"
  hub.resolve_run = lambda scope: "003" if Path(scope) == session else ""
  _publish(hub, "status.tick", {"path": str(session)}, str(session))
  assert client.sent[-1]["run"] == "003"
  _publish(hub, "sessions.rescanned", {"count": 2})
  assert client.sent[-1]["run"] == "", "studio-wide events are entitled to no run"


def test_a_resolver_that_fails_never_stops_the_event():
  hub = Hub(); client = _Client(); hub.clients.add(client)

  def boom(scope):
    raise RuntimeError("index unreadable")
  hub.resolve_run = boom
  _publish(hub, "docs.changed", {"path": "/x"}, "/x")
  assert client.sent[-1]["kind"] == "docs.changed" and client.sent[-1]["run"] == ""


def test_the_replay_buffer_carries_the_same_fields():
  hub = Hub()
  _publish(hub, "records.changed", {"record": "020"}, "020")
  replayed = hub.since(0)[-1]
  assert replayed["run"] == "020" and replayed["host"] == "h-test"


# The host id itself.


def test_a_host_id_is_minted_once_and_kept_in_the_settings_file(tmp_path, monkeypatch):
  monkeypatch.delenv("RLMCP_RECORDS", raising=False)
  where = tmp_path / "settings.json"
  first = resolve(records=[str(tmp_path / "r")], settings=str(where))
  assert first.host_id and len(first.host_id) == 12
  assert json.loads(where.read_text())["hostId"] == first.host_id
  second = resolve(records=[str(tmp_path / "r")], settings=str(where))
  assert second.host_id == first.host_id, "stable across restarts"


def test_an_unreadable_settings_file_gets_a_derived_id_and_is_not_overwritten(tmp_path, monkeypatch):
  monkeypatch.delenv("RLMCP_RECORDS", raising=False)
  where = tmp_path / "settings.json"
  where.write_text("{not json")
  cfg = resolve(records=[str(tmp_path / "r")], settings=str(where))
  assert cfg.settings_error, "the broken file is reported"
  assert cfg.host_id.startswith("h-") and len(cfg.host_id) == 14
  assert where.read_text() == "{not json", "nothing written over what the person wrote"
  again = resolve(records=[str(tmp_path / "r")], settings=str(where))
  assert again.host_id == cfg.host_id, "derived is stable too"


def test_the_library_names_the_record_a_session_directory_belongs_to(tmp_path):
  from rlmcp.records import open_store
  from rlmcp_studio.sources.records import Library

  root = tmp_path / "records"
  store = open_store(root)
  session = tmp_path / "logs" / "run-b" / "rlmcp"
  session.mkdir(parents=True)
  record = store.new_record("with-session", task="T", session=str(session))
  other = store.new_record("without", task="T")
  library = Library([root])

  assert library.record_for_path(session) == record.id
  assert library.record_for_path(session / "artifacts") == record.id, "a file below it too"
  assert library.record_for_path(root / other.id / "meta.json") == other.id, "a record's own dir"
  assert library.record_for_path(tmp_path / "elsewhere") == ""
