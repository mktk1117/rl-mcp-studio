"""The GPU source, against a scripted `nvidia-smi`.

The binary is a subprocess and the machine running the suite may not have one,
so `_smi` is replaced with a function that answers the way the real one does:
CSV, no header, no units, one line per device -- and `None` for a query the
driver refuses, which is the case the detail query is separated out for.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from rlmcp_studio.sources import gpu

UUID0 = "GPU-4fdc8e60-f606-d4c3-3bf4-7e689f022ee9"
UUID1 = "GPU-aaaaaaaa-0000-0000-0000-000000000001"


def scripted(*, detail_ok=True, mine_pid=None):
  me = mine_pid or os.getpid()

  async def fake(*args):
    q = args[0]
    if q == f"--query-gpu={','.join(gpu.FIELDS)}":
      return ("0, NVIDIA Example GPU, 14155, 24576, 100, 66, 71\n"
              "1, NVIDIA Example GPU, 0, 24576, 0, 0, 31\n")
    if q.startswith("--query-gpu=") and "power.draw" in q:
      if not detail_ok:
        return None
      return (f"0, {UUID0}, 595.84, P2, 223.68, 350.00, 1965, 2130, 9501, 9751, 53, 453, "
              "4, 4, 16, 16, 0x0000000000000044\n"
              f"1, {UUID1}, 595.84, P8, 20.10, 350.00, 210, 2130, 405, 9751, 0, 453, "
              "1, 4, 16, 16, 0x0000000000000001\n")
    if q.startswith("--query-gpu=index,uuid"):
      return f"0, {UUID0}\n1, {UUID1}\n"
    if q.startswith("--query-compute-apps"):
      return (f"{UUID0}, {me}, 8556\n"
              f"{UUID0}, 10740, 303\n"
              f"{UUID1}, 0, 0\n")
    raise AssertionError(f"unexpected query {args}")
  return fake


@pytest.fixture
def smi(monkeypatch):
  monkeypatch.setattr(gpu, "present", lambda: True)
  monkeypatch.setattr(gpu, "_smi", scripted())


def test_the_snapshot_carries_the_detail_and_names_the_runs(smi):
  me = os.getpid()
  snap = asyncio.run(gpu.snapshot([{"pid": me, "run": "047", "task": "Example-Task-v0",
                                    "started_at": 1000.0}]))
  assert snap["ok"] and snap["driver"] == "595.84" and snap["host"]
  d0, d1 = snap["devices"]
  assert d0["memory_frac"] == pytest.approx(14155 / 24576)
  assert d0["power_w"] == 223.68 and d0["power_limit_w"] == 350.0
  assert d0["clock_sm_mhz"] == 1965 and d0["clock_sm_max_mhz"] == 2130
  assert d0["fan_pct"] == 53 and d0["pstate"] == "P2" and d0["pcie_gen"] == 4
  # 0x44 is the software power cap and the hardware thermal slowdown, named.
  assert d0["throttle"] == ["software power cap", "hardware thermal slowdown"]
  # 0x1 is gpu_idle, which is not a reason anyone needs told about.
  assert d1["throttle"] == []

  # The trainer is this studio's run and is named as such; the other pid is
  # still there, with its memory, sorted below by size.
  procs = d0["processes"]
  assert [p["pid"] for p in procs] == [me, 10740]
  assert procs[0]["mine"] and procs[0]["run"] == "047" and procs[0]["task"] == "Example-Task-v0"
  assert procs[0]["user"] and procs[0]["name"]        # read from /proc for a live pid
  assert not procs[1]["mine"] and procs[1]["memory_mb"] == 303
  assert d0["running"]["pid"] == me
  assert d1["processes"] == [] and d1["running"] is None


def test_a_child_of_the_trainer_is_the_run_too(monkeypatch):
  """The pid on the card is this process; the run's recorded pid is its
  parent. The child is named for the run, as a worker a trainer forked."""
  monkeypatch.setattr(gpu, "present", lambda: True)
  monkeypatch.setattr(gpu, "_smi", scripted())
  parent = os.getppid()
  snap = asyncio.run(gpu.snapshot([{"pid": parent, "run": "048", "task": "T",
                                    "started_at": None}]))
  p = snap["devices"][0]["processes"][0]
  assert p["pid"] == os.getpid() and p["mine"] and p["run"] == "048"
  assert parent in gpu.ancestors(os.getpid())


def test_a_driver_that_refuses_the_detail_still_gives_the_glance(monkeypatch):
  monkeypatch.setattr(gpu, "present", lambda: True)
  monkeypatch.setattr(gpu, "_smi", scripted(detail_ok=False))
  snap = asyncio.run(gpu.snapshot([]))
  assert snap["ok"]
  d0 = snap["devices"][0]
  assert d0["util"] == 100 and d0["memory_used_mb"] == 14155
  assert "power_w" not in d0 and "throttle" not in d0
  assert snap["driver"] == ""


def test_no_binary_says_so(monkeypatch):
  monkeypatch.setattr(gpu, "present", lambda: False)
  snap = asyncio.run(gpu.snapshot([]))
  assert not snap["ok"] and "nvidia-smi" in snap["reason"] and snap["devices"] == []


@pytest.mark.parametrize("argv, label", [
    (["/opt/google/chrome/chrome --type=gpu-process --x=1"], "chrome"),
    ([".venv/bin/python", "-c", "from rlmcp.cli import main; main()", "train", "X"],
     "python -c (rlmcp.cli)"),
    (["/x/.venv/bin/python3", "/x/.venv/bin/rlmcp-train", "Example-Task-v0",
      "--task-package=my_tasks"], "python3 rlmcp-train Example-Task-v0"),
    (["python", "-m", "rlmcp_studio.agents.fleet_mcp"],
     "python -m rlmcp_studio.agents.fleet_mcp"),
    (["uv", "run", "rlmcp-studio", "--records", "/a"], "uv rlmcp-studio"),
    (["python3", "train.py", "/a/path/long/enough/not/to/be/a/name/at/all/x"], "python3 train.py"),
    (["python3"], "python3"),
    ([], ""),
])
def test_a_process_is_named_the_way_a_person_would(argv, label):
  assert gpu.command_label(argv) == label


def test_throttle_mask_parsing_is_forgiving():
  assert gpu.throttle_reasons("[N/A]") == []
  assert gpu.throttle_reasons("") == []
  assert gpu.throttle_reasons("0x0000000000000084") == ["software power cap",
                                                        "hardware power brake"]
