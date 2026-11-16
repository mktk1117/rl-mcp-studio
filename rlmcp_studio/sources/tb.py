"""Reading the scalars a trainer wrote to TensorBoard, without TensorBoard.

rlmcp publishes the numbers it was asked to publish; the trainer's own logger
writes *everything* — dozens of scalar series on a locomotion run, where the
session's metrics carry a handful. Termination reasons, per-terrain curriculum
levels, every loss term. They are already on disk beside the session, in event
files nothing in this stack reads.

**Why not import tensorboard.** The package is ~50 MB of server, protobuf
runtime and web assets, and the only thing wanted here is `(tag, step, value)`.
The studio renders the curves itself, in the same style as every other chart,
so the UI half of that dependency would be dead weight and the data half is the
hundred lines below.

**What this is.** A TFRecord frame reader and just enough protobuf to walk an
`Event` message. Two field numbers and two wire types; nothing about the format
here is guessed — it is checked against the real `tensorboard` package in
tests/test_tb.py.

Anything that is not a scalar — histograms, images, graphs, the newer tensor
summaries carrying more than one value — is skipped rather than half-read.
"""

from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

EVENT_GLOB = "events.out.tfevents.*"

MAX_POINTS = 600
"""Points per series on the wire.

A run publishes thousands, a chart 400 px wide can show a few hundred, and the
difference is a megabyte of JSON per tag. Downsampling happens on this side.
"""


# ── TFRecord framing ─────────────────────────────────────────────────────
def _records(data: bytes, start: int = 0) -> Iterator[Tuple[bytes, int]]:
  """Yield ``(payload, offset_after)`` for each complete TFRecord.

  Framing is ``length:u64, crc:u32, payload, crc:u32``. The checksums are
  masked CRC-32C, which needs a table this module would otherwise have no use
  for; they are skipped, and the length is trusted the way every reader that
  tails a growing file must. A truncated final record — the normal state of a
  file being written *right now* — ends the iteration rather than raising.
  """
  at = start
  size = len(data)
  while at + 12 <= size:
    (length,) = struct.unpack_from("<Q", data, at)
    end = at + 12 + length + 4
    if length > size or end > size:
      return                       # the writer is mid-record; come back later
    yield data[at + 12:at + 12 + length], end
    at = end


# ── just enough protobuf ─────────────────────────────────────────────────
def _varint(buf: bytes, at: int) -> Tuple[int, int]:
  result = shift = 0
  while at < len(buf):
    byte = buf[at]
    at += 1
    result |= (byte & 0x7F) << shift
    if not byte & 0x80:
      return result, at
    shift += 7
  raise ValueError("truncated varint")


def _fields(buf: bytes) -> Iterator[Tuple[int, int, Any]]:
  """``(field_number, wire_type, value)`` for one message.

  value is an int for varint and 64/32-bit fields, and a ``bytes`` slice for
  length-delimited ones — which is all a submessage or a string is.
  """
  at = 0
  while at < len(buf):
    key, at = _varint(buf, at)
    field, wire = key >> 3, key & 7
    if wire == 0:
      value, at = _varint(buf, at)
    elif wire == 1:
      value = struct.unpack_from("<Q", buf, at)[0]
      at += 8
    elif wire == 2:
      length, at = _varint(buf, at)
      value = buf[at:at + length]
      at += length
    elif wire == 5:
      value = struct.unpack_from("<I", buf, at)[0]
      at += 4
    else:                          # groups: gone from proto3, not written here
      raise ValueError(f"unsupported wire type {wire}")
    yield field, wire, value


def _scalar_of_tensor(payload: bytes) -> Optional[float]:
  """A ``TensorProto`` that holds exactly one float, or None.

  Newer writers put ``add_scalar`` into a tensor rather than ``simple_value``.
  A tensor with more than one element is a histogram or an image by another
  name and is not a curve this can draw.
  """
  dtype = 0
  float_vals: List[float] = []
  content = b""
  shape_dims = 0
  for field, _wire, value in _fields(payload):
    if field == 1:
      dtype = value
    elif field == 2 and isinstance(value, bytes):        # tensor_shape
      shape_dims = sum(1 for f, _, _ in _fields(value) if f == 2)
    elif field == 4 and isinstance(value, bytes):        # tensor_content
      content = value
    elif field == 5:                                     # float_val, packed
      if isinstance(value, bytes):
        float_vals += list(struct.unpack(f"<{len(value) // 4}f", value))
      else:
        float_vals.append(struct.unpack("<f", struct.pack("<I", value))[0])
  if shape_dims > 0:
    return None                    # a shaped tensor is not one number
  if len(float_vals) == 1:
    return float(float_vals[0])
  if dtype == 1 and len(content) == 4:                   # DT_FLOAT
    return float(struct.unpack("<f", content)[0])
  if dtype == 2 and len(content) == 8:                   # DT_DOUBLE
    return float(struct.unpack("<d", content)[0])
  return None


def _event_scalars(payload: bytes) -> Tuple[int, List[Tuple[str, float]]]:
  """``(step, [(tag, value), ...])`` from one Event message.

  ``step`` defaults to 0 because protobuf does not encode a field holding its
  type's default: an event logged at step 0 carries no step field at all.
  Treating "absent" as "unknown, skip it" silently drops the first point of
  every run that starts counting at zero -- which is every run from scratch.
  """
  step = 0
  summaries: List[bytes] = []
  for field, _wire, value in _fields(payload):
    if field == 2:
      step = int(value)
    elif field == 5 and isinstance(value, bytes):        # summary
      summaries.append(value)

  out: List[Tuple[str, float]] = []
  for summary in summaries:
    for field, _wire, value in _fields(summary):
      if field != 1 or not isinstance(value, bytes):     # repeated Value
        continue
      tag, number = "", None
      for vfield, vwire, vvalue in _fields(value):
        if vfield == 1 and isinstance(vvalue, bytes):
          tag = vvalue.decode("utf-8", "replace")
        elif vfield == 2 and vwire == 5:                 # simple_value
          number = struct.unpack("<f", struct.pack("<I", vvalue))[0]
        elif vfield == 8 and isinstance(vvalue, bytes):  # tensor
          number = _scalar_of_tensor(vvalue)
      if tag and number is not None:
        out.append((tag, float(number)))
  return step, out


# ── the reader ───────────────────────────────────────────────────────────
class EventFile:
  """One event file, read incrementally.

  A live run appends to this file for hours. Re-reading it from the top on
  every poll would cost more each time the run gets longer, which is exactly
  backwards, so the offset of the last complete record is kept and each read
  continues from there.
  """

  def __init__(self, path: Path):
    self.path = Path(path)
    self.offset = 0
    self.series: Dict[str, List[List[float]]] = {}

  def refresh(self) -> "EventFile":
    try:
      size = self.path.stat().st_size
    except OSError:
      return self
    if size < self.offset:         # truncated or replaced: start again
      self.offset, self.series = 0, {}
    if size == self.offset:
      return self
    with self.path.open("rb") as handle:
      handle.seek(self.offset)
      data = handle.read()
    base = self.offset
    for payload, end in _records(data):
      self.offset = base + end
      try:
        step, values = _event_scalars(payload)
      except (ValueError, struct.error):
        continue                   # one unreadable event is not a broken file
      if not values:
        continue                   # a file_version or graph event, not a curve
      for tag, number in values:
        self.series.setdefault(tag, []).append([float(step), number])
    return self


def _thin(points: List[List[float]], limit: int = MAX_POINTS) -> List[List[float]]:
  """Every nth point, with the last one kept.

  The last point is the one being watched on a live run, so it survives the
  thinning even when the stride would have skipped it.
  """
  if len(points) <= limit:
    return points
  stride = len(points) // limit + 1
  thinned = points[::stride]
  if thinned[-1] is not points[-1]:
    thinned.append(points[-1])
  return thinned


class Reader:
  """Every event file under one run directory, cached between polls."""

  def __init__(self) -> None:
    self._files: Dict[str, EventFile] = {}

  def files(self, directory: Path | str) -> List[Path]:
    root = Path(directory)
    if not root.is_dir():
      return []
    # rsl_rl writes into the run directory itself; other loggers use a
    # subdirectory. One level of nesting covers both without walking a tree
    # that may hold checkpoints and video.
    found = sorted(root.glob(EVENT_GLOB)) + sorted(root.glob(f"*/{EVENT_GLOB}"))
    return found

  def read(self, directory: Path | str,
           limit: int = MAX_POINTS) -> Dict[str, Any]:
    paths = self.files(directory)
    merged: Dict[str, List[List[float]]] = {}
    for path in paths:
      cached = self._files.get(str(path))
      if cached is None:
        cached = self._files[str(path)] = EventFile(path)
      cached.refresh()
      for tag, points in cached.series.items():
        merged.setdefault(tag, []).extend(points)

    series = {}
    for tag, points in merged.items():
      points.sort(key=lambda p: p[0])
      series[tag] = _thin(points, limit)

    # The furthest step any series reached, and when this was read. The client
    # uses the second to say "these files were read four minutes ago" on a run
    # that is still training -- and deliberately *not* the first: the trainer's
    # global step and rlmcp's iteration are not always the same quantity, so a
    # banner comparing them would be confidently wrong wherever they diverge.
    last = max((points[-1][0] for points in series.values() if points), default=None)

    return {
        "ok": bool(paths),
        "dir": str(directory),
        "files": [str(p) for p in paths],
        "tags": sorted(series),
        "series": series,
        "points": sum(len(p) for p in series.values()),
        "last_iteration": last,
        "read_at": time.time(),
        "reason": "" if paths else "no TensorBoard event files beside this run",
    }


def run_directory(session_dir: str | Path) -> Optional[Path]:
  """Where the trainer's own logger writes, given rlmcp's session directory.

  rlmcp lives in ``<run>/rlmcp`` by convention, so the run directory is its
  parent -- the same directory rsl_rl points its SummaryWriter at.
  """
  if not session_dir:
    return None
  parent = Path(session_dir).parent
  return parent if parent.is_dir() else None
