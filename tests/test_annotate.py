"""Frame annotation: the box, the burned image, and both destinations.

The ledger half is the one that must never silently fail -- it is the durable
record -- so it is tested through the HTTP endpoint against a real store, not
through the helper functions alone.
"""

from __future__ import annotations

from pathlib import Path


from rlmcp_studio.sources import annotate

BOX = {"x": 0.38, "y": 0.22, "w": 0.24, "h": 0.30}


# ── the pieces ───────────────────────────────────────────────────────────
def test_a_box_is_forced_inside_the_frame():
  box = annotate.clamp_box({"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5})
  assert box["x"] + box["w"] <= 1.0
  assert box["y"] + box["h"] <= 1.0


def test_a_box_from_nonsense_does_not_explode():
  box = annotate.clamp_box({"x": None, "y": "left", "w": [], "h": 2})
  assert box == {"x": 0.0, "y": 0.0, "w": 0.004, "h": 1.0}


def test_the_frame_reference_reads_rlmcps_own_stamp():
  ref = annotate.frame_ref("/logs/rlmcp/artifacts/shot_env7_it026014.png")
  assert ref["iteration"] == 26014 and ref["env"] == 7


def test_an_unstamped_frame_is_unknown_rather_than_guessed():
  """An invented iteration on a durable record is worse than a missing one."""
  ref = annotate.frame_ref("/tmp/someone_elses.png")
  assert ref["iteration"] is None and ref["env"] is None


def test_the_box_is_also_written_out_in_words():
  said = annotate.describe_box(annotate.clamp_box(BOX),
                               annotate.frame_ref("shot_env3_it000120.png"))
  assert "0.38" in said and "0.62" in said
  assert "iteration 120" in said and "env 3" in said


def test_burning_the_box_keeps_the_frame_and_changes_it(frame):
  from PIL import Image

  before = Image.open(frame).convert("RGB")
  after = annotate.burn(frame, annotate.clamp_box(BOX), "the wrist snaps here")

  assert after.size == before.size
  assert after.tobytes() != before.tobytes(), "nothing was drawn"


def test_the_agent_copy_is_a_png_and_is_bounded(frame):
  image = annotate.burn(frame, annotate.clamp_box(BOX), "note")
  data = annotate.for_agent(image, max_edge=32)
  assert data[:8] == b"\x89PNG\r\n\x1a\n"
  assert len(data) < 100_000


def test_the_message_names_the_file_so_a_dropped_image_is_recoverable():
  message = annotate.message_for_agent(
      "045", "the wrist snaps here", "observe", annotate.clamp_box(BOX),
      annotate.frame_ref("shot_env3_it000120.png"),
      "/records/media/045/annotations/shot-note00.png", 3)

  assert "the wrist snaps here" in message
  assert "/records/media/045/annotations/shot-note00.png" in message
  assert "feedback #3" in message


# ── the endpoint: both destinations ──────────────────────────────────────
def annotate_request(client, frame, **overrides):
  body = {"path": str(frame), "box": BOX, "note": "the wrist snaps here",
          "kind": "observe", "ask_agent": False}
  body.update(overrides)
  return client.post("/api/run/002/annotate", json=body)


def test_an_annotation_lands_on_the_record_with_its_image(client, frame, store):
  answer = annotate_request(client, frame)
  assert answer.status_code == 200
  payload = answer.json()

  # The file exists, in rlmcp's media store, listed on the record.
  assert Path(payload["path"]).is_file()
  record = store.get_record("002")
  assert record.assets["annotations"][0][0] == payload["key"]
  assert "the wrist snaps here" in record.assets["annotations"][0][1]

  # And the remark is on the ledger, verbatim, stamped with the iteration the
  # frame belongs to.
  entry = record.feedback[0]
  assert entry.text == "the wrist snaps here"
  assert entry.kind == "observe"
  assert entry.iteration == 120
  assert entry.artifacts == [payload["key"]]


def test_the_annotated_frame_comes_back_with_the_run(client, frame):
  annotate_request(client, frame)
  detail = client.get("/api/run/002").json()

  entry = detail["feedback"][0]
  assert entry["iteration"] == 120
  assert entry["images"], "the ledger entry has to carry a path the browser can fetch"
  assert client.get("/api/artifact", params={"path": entry["images"][0]}).status_code == 200


def test_two_annotations_on_one_frame_do_not_collide(client, frame, store):
  annotate_request(client, frame, note="first")
  annotate_request(client, frame, note="second")

  keys = [entry[0] for entry in store.get_record("002").assets["annotations"]]
  assert len(set(keys)) == 2, f"the second annotation overwrote the first: {keys}"
  assert len(store.get_record("002").feedback) == 2


def test_a_kind_that_asks_for_something_is_owed_an_answer(client, frame, store):
  annotate_request(client, frame, kind="steer")
  assert store.get_record("002").feedback[0].outstanding


def test_an_annotation_with_no_remark_is_refused(client, frame):
  answer = annotate_request(client, frame, note="   ")
  assert answer.status_code == 400


def test_a_frame_outside_the_run_is_refused(client, tmp_path, make_frame):
  elsewhere = make_frame(tmp_path / "not-a-run" / "photo.png")
  answer = annotate_request(client, elsewhere)
  assert answer.status_code == 404


def test_annotating_an_unknown_run_is_refused(client, frame):
  answer = client.post("/api/run/999/annotate",
                       json={"path": str(frame), "box": BOX, "note": "hello"})
  assert answer.status_code == 404


def test_the_record_is_written_even_when_the_agent_cannot_be_reached(client, frame, store):
  """The ledger is the point; the agent is the optional second destination."""
  answer = annotate_request(client, frame, ask_agent=True)

  assert answer.status_code == 200
  payload = answer.json()
  assert payload["asked_agent"] is False
  assert payload["agent_error"], "a silent failure to ask is worse than a message"
  assert len(store.get_record("002").feedback) == 1


def test_a_frame_from_another_run_is_refused(client, frame, store):
  """An annotation is evidence about the run it is filed on. `frame` came out
  of 002's session; filing it on 001 would record it as something 001
  produced."""
  answer = client.post("/api/run/001/annotate",
                       json={"path": str(frame), "box": BOX, "note": "look here"})

  assert answer.status_code == 404
  assert "not produced by run 001" in answer.json()["error"]
  assert store.get_record("001").feedback == []


def test_a_finished_run_can_be_annotated_from_its_own_media(client, store, make_frame,
                                                            records_root):
  """Most runs worth annotating have already stopped. What they still have is
  their media -- a clip's poster, an earlier annotation."""
  filed = make_frame(records_root / "media" / "001" / "plots" / "eval.png")

  answer = client.post("/api/run/001/annotate",
                       json={"path": str(filed), "box": BOX, "note": "look here"})

  assert answer.status_code == 200, answer.text
  assert store.get_record("001").feedback[0].text == "look here"
