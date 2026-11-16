"""The raw trace behind a diagnosis, so it can be drawn in this app's own style.

`diagnose` returns measurements, a verdict, and a rendered PNG. The PNG is
matplotlib's, which means the one chart in the studio that arrives as a picture
is also the one that does not match anything around it -- different colours,
different type, and no way to smooth it, zoom it, or read a value off it.

It also returns ``trace_path``: the same signals as an ``.npz``, written by
:class:`~rlmcp.core.telemetry.trace.TraceRecorder`. Reading that gives the
numbers, and the numbers can go through the same renderer as every other curve.
`rlmcp.core.telemetry.trace.load_npz` is what reads it -- this module only
reshapes the result for the wire.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

MAX_POINTS = 900
"""A four-second trace at 50 Hz is 200 samples; a long one at 500 Hz is 2000.

Thinned to keep a modal chart honest without shipping every sample of every
joint -- the same reason the TensorBoard reader thins.
"""


def _thin(values: List[float], limit: int = MAX_POINTS) -> List[float]:
  if len(values) <= limit:
    return values
  stride = len(values) // limit + 1
  thinned = values[::stride]
  if thinned[-1] != values[-1]:
    thinned.append(values[-1])
  return thinned


def read(path: Path | str) -> Dict[str, Any]:
  """One trace as ``{channel: {component: [[t, value], ...]}}``.

  Channels are what the recorder captured -- joint positions, velocities,
  actions, whatever the adapter offered. Components are the columns within a
  channel, labelled by the simulator's own names when it supplied them, so a
  curve says `left_knee` rather than `7`.
  """
  path = Path(path)
  if not path.is_file():
    return {"ok": False, "reason": f"no trace at {path}"}

  try:
    from rlmcp.core.telemetry.trace import load_npz
  except Exception as exc:                       # numpy missing, or an old rlmcp
    return {"ok": False, "reason": f"cannot read traces here: {exc}"}

  try:
    # One dict, not a tuple -- unpacking it would silently give the key strings.
    loaded = load_npz(path)
  except Exception as exc:
    return {"ok": False, "reason": f"could not read {path.name}: {exc}"}
  data = loaded.get("data", {})
  labels = loaded.get("labels", {})
  meta = loaded.get("meta", {})

  time = [float(t) for t in list(data.get("time", []))]
  channels: Dict[str, Any] = {}
  for name, array in data.items():
    if name == "time":
      continue
    values = array.tolist() if hasattr(array, "tolist") else list(array)
    if not values:
      continue
    # A channel is (steps,) or (steps, components).
    columns = (list(zip(*values)) if isinstance(values[0], (list, tuple))
               else [values])
    names = list(labels.get(name) or [])
    series: Dict[str, List[List[float]]] = {}
    for index, column in enumerate(columns):
      label = names[index] if index < len(names) else str(index)
      pairs = [[time[i] if i < len(time) else float(i), float(v)]
               for i, v in enumerate(column)]
      series[str(label)] = _thin(pairs)
    channels[name] = series

  return {
      "ok": True,
      "path": str(path),
      "meta": dict(meta or {}),
      "duration_s": round(time[-1] - time[0], 3) if len(time) > 1 else 0.0,
      "samples": len(time),
      "channels": channels,
  }
