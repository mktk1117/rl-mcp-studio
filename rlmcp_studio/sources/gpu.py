"""What the GPUs are doing, and which run is doing it.

Two questions that look like one. `nvidia-smi` answers the first — memory,
utilisation, temperature, per device — and answers the second only as a list of
*pids*, which is not a thing anyone recognises. The studio already knows which
pid belongs to which run, because a session records the trainer's, so the join
is the useful part of this module and the reason it is not a two-line wrapper.

**A subprocess, and never a Python binding.** `pynvml` would be a dependency
and, worse, importing anything CUDA-shaped into this process is the one thing
the studio's design forbids: it imports rlmcp and never torch, which is what
makes restarting it safe while a run is training. `nvidia-smi` is a separate
process that cannot take this one down.

Written for more than one device from the start, and for devices that are not
here: a studio watching a trainer on another machine fills the same shape from
what that machine reports rather than from a local binary, and a machine with
four cards is the ordinary case for the work this app is for.

Two queries per device rather than one. The first asks for the handful of
fields every driver has answered for a decade, and it is what the bar's chip
is drawn from; the second asks for the *detail* -- power, clocks, fan, PCIe,
why the clocks are being held down -- which a driver old enough not to know
one of the names refuses as a whole. Asked separately, an old driver costs the
popup its detail rows and nothing else.
"""

from __future__ import annotations

import asyncio
import os
import pwd
import shutil
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

#: What to ask for, in order. Kept as one list so the parser cannot drift from
#: the query: written out twice, a column added to one would shift every field
#: in the other.
FIELDS = ["index", "name", "memory.used", "memory.total",
          "utilization.gpu", "utilization.memory", "temperature.gpu"]

#: The detail. Each entry is (nvidia-smi field, key in the answer, unit); the
#: unit is what the popup prints after the number, and "" means a bare label.
DETAIL = [
    ("index", "index", ""),
    ("uuid", "uuid", ""),
    ("driver_version", "driver", ""),
    ("pstate", "pstate", ""),
    ("power.draw", "power_w", "W"),
    ("power.limit", "power_limit_w", "W"),
    ("clocks.sm", "clock_sm_mhz", "MHz"),
    ("clocks.max.sm", "clock_sm_max_mhz", "MHz"),
    ("clocks.mem", "clock_mem_mhz", "MHz"),
    ("clocks.max.mem", "clock_mem_max_mhz", "MHz"),
    ("fan.speed", "fan_pct", "%"),
    ("memory.reserved", "memory_reserved_mb", "MiB"),
    ("pcie.link.gen.current", "pcie_gen", ""),
    ("pcie.link.gen.max", "pcie_gen_max", ""),
    ("pcie.link.width.current", "pcie_width", ""),
    ("pcie.link.width.max", "pcie_width_max", ""),
    ("clocks_throttle_reasons.active", "throttle_mask", ""),
]

#: The bits of `clocks_throttle_reasons.active`, from nvml.h, named the way a
#: person would say them. `gpu_idle` is not a problem and is left out.
THROTTLE = [
    (0x0000000000000002, "application clock setting"),
    (0x0000000000000004, "software power cap"),
    (0x0000000000000008, "hardware slowdown"),
    (0x0000000000000010, "sync boost"),
    (0x0000000000000020, "software thermal slowdown"),
    (0x0000000000000040, "hardware thermal slowdown"),
    (0x0000000000000080, "hardware power brake"),
    (0x0000000000000100, "display clock setting"),
]

TIMEOUT = 4.0
"""`nvidia-smi` blocks on a wedged driver, and this is polled. A studio that
hangs because a GPU is unhappy would be the tool making the problem worse."""


def present() -> bool:
  return shutil.which("nvidia-smi") is not None


async def _smi(*args: str) -> Optional[str]:
  """Run nvidia-smi, or None if it will not answer."""
  if not present():
    return None
  try:
    proc = await asyncio.create_subprocess_exec(
        "nvidia-smi", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
  except OSError:
    return None
  try:
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
  except asyncio.TimeoutError:
    try:
      proc.kill()
    except ProcessLookupError:
      pass
    return None
  if proc.returncode != 0:
    return None
  return out.decode("utf-8", errors="replace")


def _number(text: str) -> Optional[float]:
  text = (text or "").strip()
  if not text or text in ("[N/A]", "[Not Supported]", "N/A"):
    return None
  try:
    return float(text)
  except ValueError:
    return None


def _rows(text: Optional[str], width: int) -> List[List[str]]:
  out = []
  for line in (text or "").splitlines():
    parts = [p.strip() for p in line.split(",")]
    if len(parts) >= width:
      out.append(parts)
  return out


async def devices() -> List[Dict[str, Any]]:
  """One entry per GPU: what it is, and how full and busy it is."""
  text = await _smi(f"--query-gpu={','.join(FIELDS)}",
                    "--format=csv,noheader,nounits")
  if text is None:
    return []
  out: List[Dict[str, Any]] = []
  for parts in _rows(text, len(FIELDS)):
    row = dict(zip(FIELDS, parts))
    used, total = _number(row["memory.used"]), _number(row["memory.total"])
    out.append({
        "index": int(_number(row["index"]) or 0),
        "name": row["name"],
        "memory_used_mb": used, "memory_total_mb": total,
        # The fraction, computed here rather than in the browser: it is the
        # number every reader actually wants and it should have one definition.
        "memory_frac": (used / total) if used is not None and total else None,
        "util": _number(row["utilization.gpu"]),
        "memory_util": _number(row["utilization.memory"]),
        "temperature_c": _number(row["temperature.gpu"]),
        "processes": [],
    })
  return out


def throttle_reasons(mask: Optional[str]) -> List[str]:
  """The names behind the hex bitmask, in the order nvml defines them."""
  try:
    bits = int((mask or "").strip() or "0", 16)
  except ValueError:
    return []
  return [name for bit, name in THROTTLE if bits & bit]


async def details() -> Dict[int, Dict[str, Any]]:
  """The detail rows, by device index. Empty when the driver refuses any of
  them -- the popup then shows what the first query gave and no more."""
  fields = [f for f, _, _ in DETAIL]
  text = await _smi(f"--query-gpu={','.join(fields)}",
                    "--format=csv,noheader,nounits")
  out: Dict[int, Dict[str, Any]] = {}
  for parts in _rows(text, len(fields)):
    row: Dict[str, Any] = {}
    for (field, key, unit), value in zip(DETAIL, parts):
      if key in ("uuid", "driver", "pstate"):
        row[key] = "" if value in ("[N/A]", "[Not Supported]") else value
      elif key == "throttle_mask":
        row["throttle"] = throttle_reasons(value)
      else:
        row[key] = _number(value)
    index = int(row.pop("index", 0) or 0)
    out[index] = row
  return out


async def compute_apps() -> Dict[int, List[Dict[str, Any]]]:
  """Which pids hold memory on which device.

  `--query-compute-apps` does not report the device on older drivers, so the
  index is asked for and the rows that lack it are attributed to device 0 --
  which is right on a single-GPU machine, where such a driver is usually found.
  """
  text = await _smi("--query-compute-apps=gpu_uuid,pid,used_memory",
                    "--format=csv,noheader,nounits")
  uuids = await _smi("--query-gpu=index,uuid", "--format=csv,noheader,nounits")
  by_uuid: Dict[str, int] = {}
  for parts in _rows(uuids, 2):
    by_uuid[parts[1]] = int(_number(parts[0]) or 0)

  out: Dict[int, List[Dict[str, Any]]] = {}
  for parts in _rows(text, 3):
    index = by_uuid.get(parts[0], 0)
    pid = int(_number(parts[1]) or 0)
    if not pid:
      continue
    out.setdefault(index, []).append(
        {"pid": pid, "memory_mb": _number(parts[2])})
  return out


def _started(pid: int) -> Optional[float]:
  """When a process started, from `/proc`. Linux-only, like nvidia-smi here."""
  try:
    return Path(f"/proc/{pid}").stat().st_ctime
  except OSError:
    return None


def _alive(pid: int) -> bool:
  return Path(f"/proc/{pid}").exists()


def _parent(pid: int) -> int:
  try:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
      if line.startswith("PPid:"):
        return int(line.split()[1])
  except (OSError, ValueError, IndexError):
    pass
  return 0


def ancestors(pid: int, depth: int = 6) -> List[int]:
  """The parent, its parent, and so on -- a few levels, never to init. A
  trainer that forks a worker leaves the worker holding the card, and the
  worker is the run's as surely as the trainer is."""
  out: List[int] = []
  while pid > 1 and len(out) < depth:
    pid = _parent(pid)
    if pid <= 1:
      break
    out.append(pid)
  return out


def _owner(pid: int) -> str:
  """Whose process it is. Another user's trainer is the case where "pid
  982491" is least helpful and a name is most."""
  try:
    uid = Path(f"/proc/{pid}").stat().st_uid
  except OSError:
    return ""
  try:
    return pwd.getpwuid(uid).pw_name
  except KeyError:
    return str(uid)


def _cmdline(pid: int) -> List[str]:
  try:
    raw = Path(f"/proc/{pid}/cmdline").read_bytes()
  except OSError:
    return []
  return [p.decode("utf-8", errors="replace") for p in raw.split(b"\0") if p]


def command_label(argv: List[str]) -> str:
  """The short name a person would use for a process.

  `python` says nothing on a machine where every process is python, so an
  interpreter is named by what it is running: the `-m` module, the script and
  its first argument, or the module a `-c` snippet imports. A browser's GPU
  helper (Chrome can hold memory on every card it can see) is named by its
  binary -- and Chrome rewrites its own argv into one space-joined string, so
  a lone argument with spaces in it is split back into words first.
  """
  if len(argv) == 1 and " " in argv[0]:
    argv = argv[0].split()
  if not argv:
    return ""
  head = os.path.basename(argv[0])
  if not (head.startswith("python") or head.startswith("uv")):
    return head
  rest = [a for a in argv[1:] if a != "run"]           # `uv run <thing>`
  positional: List[str] = []
  i = 0
  while i < len(rest):
    arg = rest[i]
    if arg == "-m" and i + 1 < len(rest):
      return f"{head} -m {rest[i + 1]}"
    if arg == "-c" and i + 1 < len(rest):
      code = rest[i + 1]
      words = code.replace(";", " ").split()
      for j, word in enumerate(words):
        if word in ("from", "import") and j + 1 < len(words):
          return f"{head} -c ({words[j + 1]})"
      return f"{head} -c"
    if not arg.startswith("-"):
      # The script, and the argument right after it when that is short
      # enough to be a name rather than a path -- `rlmcp-train
      # Mjlab-Velocity-Flat-Unitree-Go1` says which run. Only the *adjacent* one: a
      # value after a flag (`--records /a`) is the flag's, not the script's.
      positional.append(os.path.basename(arg))
      after = rest[i + 1] if i + 1 < len(rest) else ""
      if after and not after.startswith("-") and len(after) <= 40:
        positional.append(after)
      break
    i += 1
  return " ".join([head, *positional]) if positional else head


def _process_facts(pid: int) -> Dict[str, Any]:
  argv = _cmdline(pid)
  full = " ".join(argv)
  return {
      "user": _owner(pid),
      "name": command_label(argv),
      # The whole command line is the tooltip; a Chrome helper's is a
      # kilobyte of flags, and nobody needs more than the start of it.
      "command": full if len(full) <= 240 else full[:237] + "...",
  }


async def snapshot(runs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
  """The devices, with each one's processes named as runs where they are runs.

  `runs` is `[{"pid", "run", "task", "started_at"}]` -- what the studio knows
  from its sessions. A pid it cannot name is still listed, with its memory,
  because "something else is using 8 GB of this card" is exactly the thing you
  want to know before launching.
  """
  if not present():
    return {"ok": False,
            "reason": "nvidia-smi is not installed where the studio runs",
            "devices": []}

  found, apps, extra = await asyncio.gather(devices(), compute_apps(), details())
  if not found:
    return {"ok": False, "reason": "nvidia-smi would not answer", "devices": []}

  by_pid = {int(r["pid"]): r for r in (runs or []) if r.get("pid")}
  now = time.time()
  for device in found:
    device.update(extra.get(device["index"], {}))
    for app in apps.get(device["index"], []):
      known = by_pid.get(app["pid"])
      if known is None:
        known = next((by_pid[a] for a in ancestors(app["pid"]) if a in by_pid), None)
      started = (known or {}).get("started_at") or _started(app["pid"])
      device["processes"].append({
          **app,
          **_process_facts(app["pid"]),
          "run": (known or {}).get("run") or "",
          "task": (known or {}).get("task") or "",
          "mine": bool(known),
          "started_at": started,
          "for_seconds": (now - started) if started else None,
      })
    # The run using the most memory is the one the device is *for*, and it is
    # what a one-line indicator should name.
    mine = [p for p in device["processes"] if p["mine"]]
    device["processes"].sort(key=lambda p: -(p.get("memory_mb") or 0))
    device["running"] = max(mine, key=lambda p: p.get("memory_mb") or 0) if mine else None
  return {"ok": True, "reason": "", "devices": found,
          "host": socket.gethostname(), "at": now,
          "driver": next((d.get("driver") for d in found if d.get("driver")), "")}
