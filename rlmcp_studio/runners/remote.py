"""A run on another machine: :class:`RemoteRunner` over ``rlmcp hostd``.

The same contract as :class:`~rlmcp_studio.runners.local.LocalRunner`
(`runners/base.py`), executed on a host this studio reaches through rl-mcp's
wire (`rlmcp.wire`). Three things are different, and each is a
decision written down here rather than discovered in a traceback:

**The trainer there gets no record id.** rl-mcp's trainer writes its record
through a filesystem store, and the records are *here*, with the studio, not
on the host. So a remote launch passes no
``--record-run``; the record is opened here before launch, as always, and
filled from what the wire reports: the session's address once the host
lists it, ``running`` while the host says so. The trainer on the host never
holds a record, which is also what keeps the host disposable.

**The code is the stamped tree, sent ahead.** A tree is stamped here and
kept under the record's ref; ``git archive`` of it is pushed to the host
(``PUT /v1/trees/<tree>``, once per tree) and the run executes
from the host's copy with it first on ``PYTHONPATH`` -- the same three acts
as a local launch (`runners/trees.py`), with the copy on another machine.
The trainer is told ``--code-root ''``: the record already carries the
stamp. A host whose rl-mcp has no trees endpoint runs its own checkout
instead, and the job says so in its note.

**A host is a settings entry.** ``hosts`` in the settings file (or
``$RLMCP_STUDIO_HOSTS`` as JSON): ``name``, ``url``, ``token``, ``python``
(the interpreter with the simulator, on the host), ``cwd`` (the task
repository there) and ``log_root``. Nothing is discovered; a host that is
not written down does not exist.
"""

from __future__ import annotations

import os
import shlex
import time
from typing import Any, Callable, Dict, List, Optional

from rlmcp_studio.runners.base import GpuSlot, Job, JobState, NotSupported, RunSpec
from rlmcp_studio.sources.taskenv import ENTRY

OnSession = Callable[[Job, str], None]
"""Called once, when a submitted job's session appears on the host:
``(job, address)``. The app uses it to write the address onto the record."""

OnConfig = Callable[[Job, Dict[str, Any]], None]
"""Called once, when the run's parameter values are readable over the wire:
``(job, config)``, a flat ``{key: value}`` -- the same snapshot a local
trainer writes on its record at its first iteration (`snapshot_config`)."""


class RemoteRunner:
  """One host, reached over the wire. Satisfies the `Runner` contract."""

  def __init__(self, entry: Dict[str, Any], on_session: Optional[OnSession] = None,
               on_config: Optional[OnConfig] = None):
    from rlmcp import wire  # an rl-mcp without it cannot have a remote runner
    self.entry = dict(entry)
    self.label = str(entry.get("name") or entry.get("url") or "host")
    self.name = f"host:{self.label}"
    self.url = str(entry["url"]).rstrip("/")
    self.python = str(entry.get("python") or "python")
    self.cwd = str(entry.get("cwd") or "")
    self.log_root = str(entry.get("log_root") or "")
    self.token = str(entry.get("token") or "")
    self.host = wire.WireHost(self.url, token=self.token)
    # So a session address on this host opens with this host's credential,
    # wherever in the studio it is opened from.
    from rlmcp_studio.sources.session import register_host_token
    register_host_token(self.url, self.token)
    self.on_session = on_session
    self.on_config = on_config
    self._jobs: Dict[str, Job] = {}
    self._remote: Dict[str, str] = {}   # our job id → the host's job id
    self._announced: set = set()
    self._configured: set = set()
    self._config_tries: Dict[str, int] = {}

  # ── the contract ────────────────────────────────────────────────────────

  def capacity(self) -> List[GpuSlot]:
    """The host's GPUs, and which of them one of our jobs is on.

    Asked of the host each time: a fleet's whole point is that this changes
    without anything here being told.
    """
    try:
      described = self.host.host()
    except Exception as exc:  # noqa: BLE001 - an unreachable host has no capacity
      return [GpuSlot(id=f"{self.label}:down", label=f"{self.label} ({exc})",
                      busy_with="unreachable")]
    busy = next((job.id for job in self._jobs.values()
                 if job.state in (JobState.STARTING, JobState.RUNNING)), None)
    slots = []
    for gpu in described.get("gpus") or []:
      slots.append(GpuSlot(id=f"{self.label}:{gpu.get('index', 0)}",
                           label=f"{self.label} {gpu.get('name', 'gpu')}",
                           total_memory_mb=int(gpu.get("memory_total_mb") or 0),
                           busy_with=busy if not slots else None))
    if not slots:
      slots.append(GpuSlot(id=f"{self.label}:cpu", label=f"{self.label} (no GPU)",
                           busy_with=busy))
    return slots

  async def submit(self, spec: RunSpec) -> Job:
    job_id = f"{self.name}-{len(self._jobs) + 1}-{int(time.time())}"
    job = Job(id=job_id, spec=spec, runner=self.name, state=JobState.STARTING)
    self._jobs[job_id] = job
    job.workdir = spec.workdir or self.cwd
    env: Dict[str, str] = {**spec.env, "RLMCP_STUDIO_LAUNCH": job_id}
    code_root: Optional[str] = None
    if spec.materialize and spec.code.get("tree"):
      try:
        copy = self._materialize_on_host(spec.code)
      except Exception as exc:  # noqa: BLE001 - reported, and the launch still goes
        copy = ""
        job.note = (f"could not send the stamped tree to the host ({exc}); the host "
                    "runs its own checkout and the record's stamp is this studio's")
      if copy:
        job.workdir = copy
        job.materialized = True
        # The copy first, and its `src/` beside it whether or not the layout
        # has one: a directory that is not there is nothing on PYTHONPATH,
        # and the wire has no `stat` to ask with.
        env["PYTHONPATH"] = os.pathsep.join([copy, copy + "/src"])
        code_root = ""
        job.note = f"runs the stamped tree {spec.code['tree'][:12]} on {self.label} at {copy}"
    elif spec.code.get("tree"):
      job.note = "launched in place on the host; the record's stamp is this studio's"
    try:
      answer = self.host.submit_job(self.argv(spec, code_root=code_root), cwd=job.workdir,
                                    env=env, label=spec.record_id or spec.task)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
      job.state = JobState.FAILED
      job.error = f"the host did not take it: {exc}"
      return job
    self._remote[job_id] = str(answer.get("id") or "")
    job.started_at = time.time()
    job.state = _state(answer.get("state"))
    if job.state == JobState.FAILED:
      job.error = str(answer.get("error") or "failed to start on the host")
    return job

  def poll(self, job_id: str) -> Job:
    job = self._jobs.get(job_id)
    if job is None:
      raise KeyError(job_id)
    remote = self._remote.get(job_id)
    if not remote or job.state.terminal:
      return job
    try:
      described = self.host.job(remote, tail=0)
    except Exception:  # noqa: BLE001 - a host that does not answer keeps its last state
      return job
    job.state = _state(described.get("state"))
    job.exit_code = described.get("exit_code")
    job.ended_at = described.get("ended_at")
    if job.state == JobState.FAILED and not job.error:
      job.error = str(described.get("error") or f"exited with code {job.exit_code}")
    # The session directory is the trainer's to choose (`rlmcp train` names it
    # after the time it started), so it is discovered: the newest session on
    # the host for this task that began after we submitted.
    if not job.session_dir:
      address = self._session_for(job)
      if address:
        job.session_dir = address
        if self.on_session and job_id not in self._announced:
          self._announced.add(job_id)
          try:
            self.on_session(job, address)
          except Exception:  # noqa: BLE001 - a record that cannot be written is not the run's fault
            pass
    # The config snapshot a local trainer writes at its first iteration: here
    # it is read from the parameters the trainer publishes, once they exist.
    if job.session_dir and self.on_config and job_id not in self._configured:
      tries = self._config_tries.get(job_id, 0)
      if tries < 60:
        self._config_tries[job_id] = tries + 1
        config = self._config_of(job.session_dir)
        if config:
          self._configured.add(job_id)
          try:
            self.on_config(job, config)
          except Exception:  # noqa: BLE001
            pass
    return job

  async def cancel(self, job_id: str) -> Job:
    """Ask the run to stop cleanly, then ask the host to insist."""
    job = self.poll(job_id)
    remote = self._remote.get(job_id)
    if remote is None or job.state.terminal:
      return job
    if job.session_dir:
      try:
        from rlmcp_studio.sources.session import open_client
        open_client(job.session_dir).call("stop", timeout=8.0)
      except Exception:  # noqa: BLE001 - the host's SIGTERM is the fallback
        pass
    try:
      self.host.cancel_job(remote)
    except Exception as exc:  # noqa: BLE001
      job.error = job.error or f"could not cancel on the host: {exc}"
    return self.poll(job_id)

  def logs(self, job_id: str, tail: int = 200) -> List[str]:
    if job_id not in self._jobs:
      raise NotSupported("that job has no log")
    remote = self._remote.get(job_id)
    if not remote:
      # Submitted and refused, or never reached the host: the job's `error`
      # is the whole story, and an empty log beside it is the honest shape.
      return []
    try:
      return list(self.host.job(remote, tail=tail).get("log") or [])
    except Exception:  # noqa: BLE001
      return []

  def jobs(self) -> List[Job]:
    return [self.poll(job.id) for job in self._jobs.values()]

  # ── the rest ────────────────────────────────────────────────────────────

  def argv(self, spec: RunSpec, code_root: Optional[str] = None) -> List[str]:
    """`rlmcp train …` in the host's interpreter, without a record id (see above)."""
    command = list(spec.entrypoint or ["train"])
    argv = [self.python, "-c", ENTRY, *command, spec.task]
    args = dict(spec.args)
    args.pop("record-run", None)
    args.pop("records-root", None)
    if self.log_root:
      args.setdefault("log-root", self.log_root)
    if code_root is not None:
      args.setdefault("code-root", code_root)
    for key, value in args.items():
      if value is None or value is False:
        continue
      if value is True:
        argv.append(f"--{key}")
      else:
        argv += [f"--{key}", str(value)]
    return argv

  def command_line(self, spec: RunSpec) -> str:
    return f"[{self.label}] " + " ".join(shlex.quote(part) for part in self.argv(spec))

  def _materialize_on_host(self, code: Dict[str, Any]) -> str:
    """The host's path for this tree, sending it if the host lacks it."""
    tree = str(code["tree"])
    found = self.host.has_tree(tree)
    if found:
      return found
    from rlmcp_studio.runners import trees
    return self.host.push_tree(tree, trees.archive(code))

  def _config_of(self, address: str) -> Dict[str, Any]:
    """The flat ``{key: value}`` a local trainer would have snapshotted."""
    try:
      from rlmcp_studio.sources.session import LiveSession
      schema = LiveSession(address).schema()
    except Exception:  # noqa: BLE001 - not there yet, or the host did not answer
      return {}
    out: Dict[str, Any] = {}
    # rl-mcp's `ParameterSpec.to_dict` says `current`; other rl-mcp versions
    # say `current_value` or `value`. The first one present wins.
    for key, spec in (schema or {}).items():
      if not isinstance(spec, dict):
        continue
      for field in ("current", "current_value", "value"):
        if field in spec:
          out[str(key)] = spec[field]
          break
    return out

  def _session_for(self, job: Job) -> str:
    try:
      rows = list(self.host.sessions())
    except Exception:  # noqa: BLE001
      return ""
    since = job.submitted_at - 2.0
    for session in rows:  # newest first
      try:
        info = session.info()
      except Exception:  # noqa: BLE001
        continue
      if info.get("task") != job.spec.task:
        continue
      started = info.get("started_at") or 0
      if isinstance(started, (int, float)) and started >= since:
        return session.address
    return ""


def _state(value: Any) -> JobState:
  try:
    return JobState(str(value))
  except ValueError:
    return JobState.RUNNING


class Hosts:
  """Every host written down in the settings, as runners."""

  def __init__(self, entries: List[Dict[str, Any]], on_session: Optional[OnSession] = None,
               on_config: Optional[OnConfig] = None):
    self.runners: Dict[str, RemoteRunner] = {}
    self.problems: Dict[str, str] = {}
    for entry in entries or []:
      name = str(entry.get("name") or entry.get("url") or "")
      if not name or not entry.get("url"):
        continue
      try:
        self.runners[name] = RemoteRunner(entry, on_session=on_session, on_config=on_config)
      except Exception as exc:  # noqa: BLE001 - one bad entry must not hide the others
        self.problems[name] = f"{type(exc).__name__}: {exc}"

  def get(self, name: str) -> Optional[RemoteRunner]:
    return self.runners.get(name)

  def jobs(self) -> List[Job]:
    return [job for runner in self.runners.values() for job in runner.jobs()]

  def find(self, job_id: str) -> Optional[RemoteRunner]:
    for runner in self.runners.values():
      if job_id in runner._jobs:
        return runner
    return None

  def capacity(self) -> List[GpuSlot]:
    return [slot for runner in self.runners.values() for slot in runner.capacity()]

  def describe(self) -> List[Dict[str, Any]]:
    out = []
    for name, runner in self.runners.items():
      out.append({"name": name, "url": runner.url, "cwd": runner.cwd,
                  "python": runner.python, "log_root": runner.log_root})
    for name, problem in self.problems.items():
      out.append({"name": name, "problem": problem})
    return out

