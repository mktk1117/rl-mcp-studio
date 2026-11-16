"""Runs on the studio's own machine: the local implementation of the runner
contract.

It closes the loop the env builder opens: you write a task, hold it and check
it here, and then train it here too, without going to a terminal.

This is a subprocess, and deliberately not more than one. Everything hard
about launching — which environment can construct the task, which record the
run fills, where its telemetry lands — is already answered elsewhere in this
app, and a launcher that re-answered any of them would be a second opinion:

* **the environment** is `sources/taskenv.py`'s, the same interpreter the
  listing, the gates and the preview use;
* **the record** is opened *before* launch and passed to `rlmcp train
  --record-run`, so the claim costs something and the run appears in the rail
  the moment it starts rather than when somebody remembers to write it down;
* **the telemetry** is a session directory, which is the whole join between
  execution and observation — a runner's only real job is to produce one.

What it does not do is queue. One machine, one GPU: a second run submitted
while the first holds the card is the user's decision to make, and a queue
that made it for them would be the studio deciding when experiments run.
`capacity()` says what is busy; `submit()` refuses only what it cannot do.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import signal
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from rlmcp_studio.runners import trees
from rlmcp_studio.runners.base import GpuSlot, Job, JobState, NotSupported, RunSpec
from rlmcp_studio.sources.taskenv import TaskEnv

LOG_TAIL = 200
STOP_GRACE = 20.0
"""Seconds a run is given to end itself after `rlmcp stop`. Longer than a
preview's: a trainer between rollout batches can take a moment to notice, and
killing one that was about to checkpoint loses the checkpoint."""


def default_entrypoint() -> List[str]:
  """`rlmcp train`, which is rl-mcp's own general launcher.

  Not the task package's `train.py`. A project's launcher is free to do
  anything before it calls `rlmcp.wrap` -- load a prior, build a curriculum,
  read its own flags -- and the studio cannot know that. `rlmcp train` is the
  one launcher that works for *any* registered task, which is the only kind
  the studio can honestly offer to press a button for. A task that needs its
  own launcher says so in its spec.
  """
  return ["train"]


class LocalRunner:
  """One machine, one process per run."""

  name = "local"

  def __init__(self, env: TaskEnv, log_root: str = "", records_root: str = "") -> None:
    self.env = env
    self.log_root = log_root
    self.records_root = records_root
    self._jobs: Dict[str, Job] = {}
    self._procs: Dict[str, asyncio.subprocess.Process] = {}
    self._logs: Dict[str, Path] = {}
    self._trees: Dict[str, Path] = {}   # job id → the materialized copy it runs from
    self._lock = asyncio.Lock()

  # ── the contract ────────────────────────────────────────────────────────

  def capacity(self) -> List[GpuSlot]:
    """What the studio's machine has, and what is on it.

    Reported from `nvidia-smi` when it answers and as one unlabelled slot when
    it does not: a machine with no CUDA tooling can still train on CPU, and
    saying "no capacity" would be a refusal rather than a fact.
    """
    busy = next((job.id for job in self._jobs.values()
                 if job.state in (JobState.STARTING, JobState.RUNNING)), None)
    slots = _gpus()
    if not slots:
      return [GpuSlot(id="cpu", label="local machine", busy_with=busy)]
    slots[0].busy_with = busy
    return slots

  async def submit(self, spec: RunSpec) -> Job:
    """Start the run. Returns as soon as the process exists."""
    async with self._lock:
      job_id = f"{self.name}-{len(self._jobs) + 1}-{int(time.time())}"
      job = Job(id=job_id, spec=spec, runner=self.name, state=JobState.STARTING)
      self._jobs[job_id] = job

      source = Path(spec.workdir or self.env.cwd or ".").expanduser()
      # Logs and the session directory stay beside the *source* repository
      # whatever the run executes from: that is where the rail looks, and a
      # materialized copy is deleted when the run ends.
      log_root = Path(spec.args.get("log-root") or self.log_root
                      or (source / "logs" / "rsl_rl"))

      # Where the run executes from. The stamped tree when there is one
      # (runners/trees.py): an edit made after this moment, or a sibling
      # variant, cannot reach a copy of its own. Otherwise the source tree as
      # it is, with the trainer stamping whatever it finds -- what a launch
      # outside git gets.
      root, extra_env, code_root = source, {}, None
      if spec.materialize and spec.code.get("kind") == "git" and spec.code.get("tree"):
        home = trees.runs_home() / (spec.record_id or job_id)
        try:
          root = trees.materialize(spec.code, home)
        except trees.TreeError as exc:
          job.state = JobState.FAILED
          job.error = f"could not materialize the stamped tree: {exc}"
          return job
        self._trees[job_id] = root
        job.workdir = str(root)
        job.materialized = True
        # The copy first on the path, so the package imported is the stamped
        # one even where the task repository is installed editable.
        extra_env = {"PYTHONPATH": os.pathsep.join(
            trees.import_roots(root) + [p for p in [self.env.environ().get("PYTHONPATH", "")] if p])}
        # The record already carries the stamp; tell the trainer not to
        # re-stamp from a plain directory. An older rl-mcp would erase it
        # instead, so with one of those the trainer stamps the source tree,
        # which is at least the truth of a moment ago.
        code_root = "" if trees.trainer_keeps_launcher_stamp() else str(source)
        if code_root:
          job.error = ""  # not an error, but worth a note in the log
          job.note = ("this rl-mcp cannot keep a launcher's stamp; the trainer stamped "
                      "the source tree as it was at launch")
      argv = self._argv(spec, log_root, code_root=code_root)

      log_path = _log_path(log_root, spec, job_id)
      try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "wb")
      except OSError as exc:
        job.state = JobState.FAILED
        job.error = f"could not open {log_path}: {exc}"
        return job
      self._logs[job_id] = log_path

      try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=handle, stderr=asyncio.subprocess.STDOUT,
            cwd=str(root), env={**self.env.environ(), **extra_env, **spec.env},
            # Its own process group: stopping a run must not signal the studio,
            # and a trainer spawns children of its own.
            start_new_session=True)
      except Exception as exc:                    # noqa: BLE001 - reported, not raised
        job.state = JobState.FAILED
        job.error = f"could not start it: {exc}"
        return job
      finally:
        handle.close()

      self._procs[job_id] = proc
      job.started_at = time.time()
      job.state = JobState.RUNNING
      asyncio.ensure_future(self._reap(job_id))
      return job

  def poll(self, job_id: str) -> Job:
    job = self._jobs.get(job_id)
    if job is None:
      raise KeyError(job_id)
    # The session directory is the trainer's to choose, so it is discovered
    # rather than assumed: `rlmcp train` names it after the time it started.
    if not job.session_dir and job.state == JobState.RUNNING:
      found = _session_under(self._logs.get(job_id))
      if found:
        job.session_dir = str(found)
    return job

  async def cancel(self, job_id: str) -> Job:
    """Ask the run to stop, then insist. Idempotent."""
    job = self.poll(job_id)
    proc = self._procs.get(job_id)
    if proc is None or proc.returncode is not None:
      return job

    if job.session_dir:
      # `rlmcp stop` is the clean path: the trainer finishes its batch, writes
      # its checkpoint and records its own end. A signal skips all three.
      try:
        from rlmcp_studio.sources import control

        await asyncio.wait_for(control.call(job.session_dir, "stop", timeout=8.0),
                               timeout=10.0)
      except Exception:
        pass
    try:
      await asyncio.wait_for(proc.wait(), timeout=STOP_GRACE)
    except asyncio.TimeoutError:
      proc.terminate()
      try:
        await asyncio.wait_for(proc.wait(), timeout=6.0)
      except asyncio.TimeoutError:
        proc.kill()
    return self.poll(job_id)

  def logs(self, job_id: str, tail: int = LOG_TAIL) -> Iterable[str]:
    path = self._logs.get(job_id)
    if path is None:
      raise NotSupported("that job has no log")
    try:
      return path.read_text(errors="replace").splitlines()[-tail:]
    except OSError:
      return []

  # ── the rest ────────────────────────────────────────────────────────────

  def jobs(self) -> List[Job]:
    return [self.poll(job.id) for job in self._jobs.values()]

  def latest(self) -> Optional[Job]:
    return self.jobs()[-1] if self._jobs else None

  async def stop_all(self) -> None:
    await asyncio.gather(*(self.cancel(job_id) for job_id in list(self._procs)),
                         return_exceptions=True)

  def _argv(self, spec: RunSpec, log_root: Path,
            code_root: Optional[str] = None) -> List[str]:
    command = list(spec.entrypoint or default_entrypoint())
    argv = self.env.argv(*command, spec.task)
    args = dict(spec.args)
    args.setdefault("log-root", str(log_root))
    # `""` is a real value here: "stamp nothing, the record already carries
    # it". None means the flag is not passed and the trainer stamps its cwd.
    if code_root is not None:
      args.setdefault("code-root", code_root)
    if spec.record_id:
      args.setdefault("record-run", spec.record_id)
      if self.records_root:
        args.setdefault("records-root", self.records_root)
    for key, value in args.items():
      if value is None or value is False:
        continue
      if value is True:
        argv.append(f"--{key}")
      else:
        argv += [f"--{key}", str(value)]
    return argv

  async def _reap(self, job_id: str) -> None:
    proc = self._procs.get(job_id)
    job = self._jobs.get(job_id)
    if proc is None or job is None:
      return
    code = await proc.wait()
    job.exit_code = code
    job.ended_at = time.time()
    job.state = (JobState.SUCCEEDED if code == 0
                 else JobState.CANCELLED if code in (-signal.SIGTERM, -signal.SIGINT)
                 else JobState.FAILED)
    if job.state == JobState.FAILED and not job.error:
      job.error = f"the trainer exited with code {code}"
    # The materialized copy was only ever the place the run executed from:
    # the tree lives on under its ref, the logs and session beside the
    # source. A failed run keeps its copy for a look; a finished one does not.
    copy = self._trees.pop(job_id, None)
    if copy is not None and job.state == JobState.SUCCEEDED:
      shutil.rmtree(copy, ignore_errors=True)


def command_line(runner: LocalRunner, spec: RunSpec) -> str:
  """The command this spec would run, for a panel to show before it runs it.

  A launch button that does not say what it is about to do is a button people
  press once and then stop trusting.
  """
  root = Path(spec.workdir or runner.env.cwd or ".").expanduser()
  log_root = Path(spec.args.get("log-root") or runner.log_root
                  or (root / "logs" / "rsl_rl"))
  return " ".join(shlex.quote(part) for part in runner._argv(spec, log_root))


def _gpus() -> List[GpuSlot]:
  """What `nvidia-smi` says, or nothing at all."""
  import subprocess

  try:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=4)
  except Exception:                               # noqa: BLE001 - absence is an answer
    return []
  if out.returncode != 0:
    return []
  slots: List[GpuSlot] = []
  for line in out.stdout.strip().splitlines():
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
      continue
    try:
      slots.append(GpuSlot(id=f"cuda:{parts[0]}", label=parts[1],
                           total_memory_mb=int(float(parts[2]))))
    except ValueError:
      continue
  return slots


def _log_path(log_root: Path, spec: RunSpec, job_id: str) -> Path:
  stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
  name = spec.record_id or spec.labels.get("slug") or job_id
  return Path(log_root) / "studio-launches" / f"{stamp}_{name}.log"


def _session_under(log_path: Optional[Path]) -> Optional[Path]:
  """The session directory the trainer announced in its own output.

  Read from the log rather than guessed from the clock: `rlmcp train` prints
  `[rlmcp] session ready: <dir>`, and a guess would race the trainer's own
  naming on any machine where a second passes between the two.
  """
  if log_path is None:
    return None
  try:
    text = log_path.read_text(errors="replace")
  except OSError:
    return None
  for line in text.splitlines():
    if "session ready:" in line:
      candidate = Path(line.split("session ready:", 1)[1].strip())
      if candidate.exists():
        return candidate
  return None


__all__ = ["LocalRunner", "command_line", "default_entrypoint"]
