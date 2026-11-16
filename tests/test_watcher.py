"""What a touched file means, and that the meaning stays in one vocabulary."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rlmcp_studio.events import KINDS
from rlmcp_studio.sources.watcher import MEANS, Watcher, _classify


def test_each_of_rlmcps_files_means_one_event():
  assert _classify(Path("/logs/run/rlmcp/status.json")) == "status.tick"
  assert _classify(Path("/logs/run/rlmcp/metrics.jsonl")) == "metrics.append"
  assert _classify(Path("/logs/run/rlmcp/events.jsonl")) == "events.append"
  assert _classify(Path("/records/runs/001-x/meta.json")) == "records.changed"


def test_anything_in_an_artifacts_directory_is_a_new_frame_or_clip():
  assert _classify(Path("/logs/run/rlmcp/artifacts/shot_env0_it000010.png")) \
      == "artifacts.changed"


def test_a_file_nobody_cares_about_produces_nothing():
  assert _classify(Path("/logs/run/rlmcp/inbox/req-1.json")) is None
  assert _classify(Path("/records/index.sqlite")) is None


def test_every_meaning_is_in_the_published_vocabulary():
  """The client dispatches on these strings; an unlisted one reaches nobody."""
  assert set(MEANS.values()) <= set(KINDS)
  assert "artifacts.changed" in KINDS


def test_a_write_becomes_one_event_not_one_per_write(tmp_path):
  """A run publishing status every service tick out-paces any client."""
  seen = []

  async def emit(kind, data, scope=""):
    seen.append(kind)

  async def exercise():
    watcher = Watcher([tmp_path], emit, debounce_ms=50)
    watcher.start()
    await asyncio.sleep(0.2)
    for i in range(10):
      (tmp_path / "metrics.jsonl").write_text(f"row {i}\n")
    await asyncio.sleep(0.6)
    await watcher.stop()

  asyncio.run(exercise())
  assert seen, "ten writes produced no event at all"
  assert len(seen) <= 3, f"ten writes produced {len(seen)} events: {seen}"
