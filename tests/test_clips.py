"""Progress clips: two sources, one list, in the order the run happened.

The panel's value is entirely in the ordering and the merge -- a pile of mp4s
is not a trajectory -- so that is what these pin.
"""

from __future__ import annotations

from pathlib import Path

from rlmcp_studio.sources import clips as source_clips


def fake_clip(path: Path) -> Path:
  """A file that is a video as far as every path under test is concerned.

  No encoder is involved on purpose: poster extraction is allowed to fail and
  must degrade to "no still", which is exactly what an unreadable file proves.
  """
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_bytes(b"\x00\x00\x00\x18ftypmp42not-a-real-video")
  return path


def test_session_clips_are_found_and_placed(session_dir):
  fake_clip(session_dir / "artifacts" / "progress_it000000.mp4")
  fake_clip(session_dir / "artifacts" / "progress_it000400.mp4")

  found = source_clips.from_session(str(session_dir))
  assert sorted(c["iteration"] for c in found) == [0, 400]
  assert all(c["source"] == "session" for c in found)
  assert all(c["url"].startswith("/api/artifact?path=") for c in found)


def test_a_frame_is_not_a_clip(session_dir):
  """The artifacts directory holds screenshots too."""
  assert source_clips.from_session(str(session_dir)) == []


def test_clips_are_ordered_by_iteration_not_by_when_they_were_copied(session_dir, store):
  for iteration in (400, 0, 100):
    fake_clip(session_dir / "artifacts" / f"progress_it{iteration:06d}.mp4")

  payload = source_clips.for_run(store.get_record("002"), None)
  assert [c["iteration"] for c in payload["clips"]] == [0, 100, 400]
  assert payload["latest_iteration"] == 400
  assert payload["count"] == 3


def test_the_record_copy_and_the_session_copy_are_one_clip(session_dir, store,
                                                           records_root):
  """Otherwise every filed clip appears twice the moment it is copied."""
  clip = fake_clip(session_dir / "artifacts" / "progress_it000200.mp4")
  key = store.media.put("002", str(clip), "progress clip at iteration 200", "videos")
  store.update_record("002", lambda r: r.assets.setdefault("videos", []).append(
      [key, "progress clip at iteration 200"]))

  payload = source_clips.for_run(store.get_record("002"), records_root / "media")
  assert payload["count"] == 1, payload["clips"]
  assert payload["clips"][0]["source"] == "record", "the durable copy wins"


def test_an_unplaceable_clip_is_listed_but_never_called_the_latest(session_dir, store):
  """A clip nobody stamped is still worth showing; guessing it is 'now' is not."""
  fake_clip(session_dir / "artifacts" / "progress_it000100.mp4")
  fake_clip(session_dir / "artifacts" / "handmade.mp4")

  payload = source_clips.for_run(store.get_record("002"), None)
  assert payload["count"] == 2
  assert payload["latest_iteration"] == 100


def test_a_run_with_no_session_and_no_assets_is_empty_not_broken(store):
  payload = source_clips.for_run(store.get_record("001"), None)
  assert payload["clips"] == [] and payload["latest"] == ""


def test_the_endpoint_serves_the_same_answer(client, session_dir):
  fake_clip(session_dir / "artifacts" / "progress_it000050.mp4")

  payload = client.get("/api/run/002/clips").json()
  assert payload["count"] == 1
  assert payload["latest_iteration"] == 50

  # And the url it hands out is one the artifact route will actually serve.
  url = payload["clips"][0]["url"]
  assert client.get(url).status_code == 200


def test_the_endpoint_refuses_a_run_that_does_not_exist(client):
  assert client.get("/api/run/999/clips").status_code == 404
