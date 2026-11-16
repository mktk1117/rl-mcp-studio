"""Pointing at a frame, and saying what is wrong with it.

An observation has one path and two destinations. Drawing a box on a frame
and typing "the wrist snaps here" must not be a chat message that dies with the
window, so the same call writes it to rl-mcp's feedback ledger *and* hands it
to the agent as an image with the box burned in.

Three things this module is careful about:

**The box is drawn, and also written out.** A model reads a drawn rectangle far
more reliably than four numbers, but the numbers survive re-encoding, cropping
and a model that ignores the image -- so both go, and the text is the fallback.

**The annotated frame is copied, not referenced.** It goes into the record's
media store through ``store.media.put``, which is rlmcp's own home for evidence
that must outlive a session directory being cleaned up.

**The remark stays verbatim.** ``Feedback.text`` is what the user typed and
nothing else; where they pointed lives in the asset caption and in the burned-in
image. rlmcp's ledger separates what was *said* from what it was *taken to
mean*, and a helpful annotation appended to the quote would erase that line.
"""

from __future__ import annotations

import hashlib
import io
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

MEDIA_KIND = "annotations"
"""Its own kind under the record's media root, beside plots and videos."""

AGENT_MAX_EDGE = 1024
"""What the agent is sent. Full size is kept on the record; the copy in the
message is downscaled because a 4K frame buys no accuracy and costs latency."""

_ITERATION = re.compile(r"_it(\d+)")
_ENV = re.compile(r"_env(\d+)")


def frame_ref(path: Path | str) -> Dict[str, Any]:
  """What is known about a frame, read off the name rlmcp stamped on it.

  ``Controller._artifact`` writes ``{stem}_it{iteration:06d}{suffix}`` and the
  screenshot's stem carries the env index, so the file name is a record of
  which environment at which iteration. Unstamped names are reported as
  unknown rather than guessed at -- an invented iteration on a durable record
  is worse than a missing one.

  (An ``artifact_info`` on ``rlmcp.session.Session`` would make this a lookup
  rather than a read of a convention; that belongs upstream in rl-mcp.)
  """
  name = Path(path).name
  iteration = _ITERATION.search(name)
  env = _ENV.search(name)
  return {
      "name": name,
      "path": str(path),
      "iteration": int(iteration.group(1)) if iteration else None,
      "env": int(env.group(1)) if env else None,
  }


def clamp_box(box: Dict[str, Any]) -> Dict[str, float]:
  """A normalized box, forced into the frame and given a usable minimum size.

  Coordinates are fractions of the frame rather than pixels so the record
  survives the same run being re-rendered at another resolution.
  """
  def frac(value: Any) -> float:
    try:
      return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
      return 0.0

  x, y = frac(box.get("x")), frac(box.get("y"))
  w = min(max(frac(box.get("w")), 0.004), 1.0 - x)
  h = min(max(frac(box.get("h")), 0.004), 1.0 - y)
  return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def describe_box(box: Dict[str, float], ref: Dict[str, Any]) -> str:
  """The box in words: the fallback for a model that did not see the image."""
  x, y, w, h = box["x"], box["y"], box["w"], box["h"]
  where = (f"x {x:.2f}–{x + w:.2f}, y {y:.2f}–{y + h:.2f} "
           f"(fractions of the frame, origin top-left)")
  parts = [f"the boxed region at {where}"]
  if ref.get("iteration") is not None:
    parts.append(f"iteration {ref['iteration']:,}")
  if ref.get("env") is not None:
    parts.append(f"env {ref['env']}")
  return " · ".join(parts)


def _font(size: int):
  from PIL import ImageFont
  try:
    return ImageFont.load_default(size=size)
  except Exception:
    return ImageFont.load_default()


def burn(source: Path | str, box: Dict[str, float], label: str = ""):
  """The frame with the box drawn on it, as a PIL image.

  Two rectangles, dark under bright: a single-colour outline disappears against
  a robot, a floor or a sky depending on the frame, and this is the one part
  that has to be legible on all three.
  """
  from PIL import Image, ImageDraw

  image = Image.open(source).convert("RGB")
  width, height = image.size
  draw = ImageDraw.Draw(image)

  left = box["x"] * width
  top = box["y"] * height
  right = left + box["w"] * width
  bottom = top + box["h"] * height

  stroke = max(2, round(min(width, height) / 240))
  draw.rectangle([left - stroke, top - stroke, right + stroke, bottom + stroke],
                 outline=(12, 12, 16), width=stroke)
  draw.rectangle([left, top, right, bottom], outline=(255, 214, 64), width=stroke)

  text = (label or "").strip().replace("\n", " ")
  if text:
    if len(text) > 64:
      text = text[:63] + "…"
    size = max(12, round(min(width, height) / 34))
    font = _font(size)
    pad = max(3, stroke * 2)
    try:
      x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    except Exception:                      # a bitmap fallback font
      x0, y0, x1, y1 = 0, 0, len(text) * size // 2, size
    tag_w, tag_h = (x1 - x0) + pad * 2, (y1 - y0) + pad * 2
    tag_x = min(max(left, 0), max(width - tag_w, 0))
    tag_y = top - tag_h - stroke
    if tag_y < 0:                          # no room above; sit inside the box
      tag_y = top + stroke
    draw.rectangle([tag_x, tag_y, tag_x + tag_w, tag_y + tag_h], fill=(255, 214, 64))
    draw.text((tag_x + pad - x0, tag_y + pad - y0), text, fill=(20, 18, 8), font=font)

  return image


def for_agent(image, max_edge: int = AGENT_MAX_EDGE) -> bytes:
  """PNG bytes, downscaled to something worth sending over a pipe."""
  from PIL import Image

  copy = image.copy()
  copy.thumbnail((max_edge, max_edge), Image.LANCZOS)
  buffer = io.BytesIO()
  copy.save(buffer, format="PNG", optimize=True)
  return buffer.getvalue()


def annotated_name(frame: Path | str, image_bytes: bytes) -> str:
  """A name that cannot collide, derived from the picture itself.

  A name that counted the annotations already on the record would race: two
  annotations arriving together would both read "there are three" and both
  write `-note03.png`, and the media store copies by file name, so one image
  would overwrite the other and several remarks would point at one picture
  whose burned-in caption names only one of them. A record that misattributes
  evidence is worse than one that lost it.

  A content hash also makes the write idempotent: the same box, note and frame
  twice is one file, which is what a retried request should produce.
  """
  digest = hashlib.sha256(image_bytes).hexdigest()[:12]
  return f"{Path(frame).stem}-note-{digest}.png"


def file_on_record(store: Any, record: Any, image, frame: Path | str,
                   caption: str) -> Tuple[str, str]:
  """Copy the annotated frame into the record's media store and list it.

  Returns ``(key, local_path)``. The listing goes on ``record.assets`` through
  ``update_record``, which is the retry helper -- a heartbeat landing between
  the read and the write must not lose the annotation.
  """
  buffer = io.BytesIO()
  image.save(buffer, format="PNG")
  payload = buffer.getvalue()

  with tempfile.TemporaryDirectory(prefix="rlmcp-studio-annot-") as tmp:
    staged = Path(tmp) / annotated_name(frame, payload)
    staged.write_bytes(payload)
    key = store.media.put(record.id, str(staged), caption, MEDIA_KIND)

  def add_asset(fresh: Any) -> Any:
    entries = fresh.assets.setdefault(MEDIA_KIND, [])
    if any(entry and entry[0] == key for entry in entries):
      return False
    entries.append([key, caption])

  # More retries than the store's default: this is a small append that races
  # every other annotation *and* the trainer's heartbeat, and a double-click in
  # the GUI is enough to make several of them land together. Losing the listing
  # would leave the image on disk with nothing pointing at it.
  store.update_record(record.id, add_asset, retries=8)
  return key, store.media.get(key) or ""


def caption_for(note: str, box: Dict[str, float], ref: Dict[str, Any]) -> str:
  """What the asset is, in one line: the remark, the frame, and the region.

  The source frame is named here rather than listed as an artifact of the
  remark: ``Feedback.artifacts`` is for paths that exist *because* of what was
  said, and the frame existed before anyone pointed at it.
  """
  return f"{note.strip()} — on {ref['name']}, {describe_box(box, ref)}"


def message_for_agent(run_id: str, note: str, kind: str, box: Dict[str, float],
                      ref: Dict[str, Any], local_path: str,
                      feedback_index: Optional[int]) -> str:
  """The text half of what the agent gets. The image half is the same frame.

  It names the file on disk as well, so an agent whose transport dropped the
  image block can still read it with the Read tool rather than answering about
  a picture it never saw.
  """
  lines = [
      f"I have drawn a box on a frame from run {run_id} and I am asking about "
      f"what is inside it.",
      "",
      f"In {describe_box(box, ref)}:",
      "",
      f"  “{note.strip()}”",
      "",
      f"The frame is {ref['name']}; the copy with the box drawn on it is at "
      f"{local_path}.",
  ]
  if feedback_index is not None:
    lines.append(
        f"This is on the run record as feedback #{feedback_index} ({kind}), so "
        "answer it there with answer_feedback when you have looked."
    )
  lines.append(
      "Look at the region, say what you think is happening, and measure it "
      "rather than guessing -- diagnose_motion, a trace, or a longer clip."
  )
  return "\n".join(lines)
