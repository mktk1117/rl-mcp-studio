"""Where a training run actually executes.

To launch runs -- locally, or on a box you attached -- the studio needs one
contract with several implementations, kept independent of any one of them so
that one runner's assumptions do not leak everywhere.

Deliberately shaped like ``rlmcp.adapters.base``: only a few methods are
mandatory, and a capability a backend lacks raises :class:`NotSupported` rather
than returning something falsy. A runner that cannot stream logs says so; it is
not silently log-less.

Implementations:

``LocalRunner``    a subprocess on the studio's machine, pinned to a GPU index.
``RemoteRunner``   a box you attached over SSH. Same contract, different host.

A managed cloud executor, for GPUs you do not own, would be a third
implementation of the same contract.

Nothing above this module knows which one it is talking to.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol


class NotSupported(RuntimeError):
  """This runner does not have that capability.

  Same convention as rlmcp's adapters: never stand in an empty result for a
  capability that is absent, because the caller cannot tell the difference
  between "nothing to report" and "cannot report".
  """


class JobState(str, Enum):
  QUEUED = "queued"
  STARTING = "starting"
  RUNNING = "running"
  SUCCEEDED = "succeeded"
  FAILED = "failed"
  CANCELLED = "cancelled"

  @property
  def terminal(self) -> bool:
    return self in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED)


@dataclass
class GpuSlot:
  """One unit of compute a runner can place a job on."""

  id: str
  label: str = ""
  total_memory_mb: int = 0
  busy_with: Optional[str] = None
  """Job id currently occupying it, if any."""


@dataclass
class RunSpec:
  """Everything needed to start one run, and nothing about how it is executed.

  This is the studio's launch request, not a training config: the *content* of
  the config belongs to the task package. Keeping the two apart is what lets
  the same spec run locally and in the cloud.
  """

  task: str
  """Environment / task id, as the task package registers it."""

  entrypoint: List[str]
  """Argv of the launcher, e.g. ``["python", "-m", "my_tasks.train"]``."""

  args: Dict[str, Any] = field(default_factory=dict)
  """Flags folded onto the entrypoint. Kept structured so a variant can edit one."""

  record_id: Optional[str] = None
  """The record this run fills. Opened before launch, so the claim costs something."""

  parent: Optional[str] = None
  weights_from: Optional[str] = None
  """Warm start. A different edge from ``parent`` -- see rlmcp's record graph."""

  env: Dict[str, str] = field(default_factory=dict)
  workdir: str = ""
  gpus: int = 1
  labels: Dict[str, str] = field(default_factory=dict)
  """Free tags. A fan-out stamps its variant here so siblings are recognisable."""

  code: Dict[str, Any] = field(default_factory=dict)
  """The record's ``code`` payload, stamped by the launcher before submit.

  ``kind: git`` with a ``tree`` means the run can be materialized: written
  out from that tree into a directory of its own and executed there
  (`runners/trees.py`), so an edit made after launch, or a sibling variant,
  cannot reach it. Empty, or ``kind: none``, means the run executes from
  ``workdir`` as it is, and the trainer stamps what it finds there.
  """

  materialize: bool = True
  """Run from the stamped tree when there is one. Off is the escape hatch."""


@dataclass
class Job:
  """A submitted run, as the runner sees it."""

  id: str
  spec: RunSpec
  state: JobState = JobState.QUEUED
  runner: str = ""
  slot: Optional[str] = None
  session_dir: str = ""
  """Where the run publishes telemetry, once it has started.

  This is the join between execution and observation: the studio already knows
  how to read a session directory, so a runner's whole job is to produce one.
  """
  workdir: str = ""
  """Where the run executes from: the materialized copy, or the source tree."""
  note: str = ""
  """Something true about how it was launched that is not an error."""
  materialized: bool = False
  """The run executes from a copy of the stamped tree, not from a checkout.

  Set by the runner at the moment it has that copy, because it is the only
  one that knows: `workdir` alone cannot say it, since a remote job always
  has one (the host's checkout when the push failed, or when told to run in
  place) and a stamp does not mean the stamp was used.
  """

  submitted_at: float = field(default_factory=time.time)
  started_at: Optional[float] = None
  ended_at: Optional[float] = None
  exit_code: Optional[int] = None
  error: str = ""

  def describe(self) -> Dict[str, Any]:
    return {
        "id": self.id, "state": self.state.value, "runner": self.runner,
        "slot": self.slot, "session_dir": self.session_dir,
        "workdir": self.workdir, "note": self.note,
        "task": self.spec.task, "record_id": self.spec.record_id,
        "labels": dict(self.spec.labels),
        # What code the run executes: the stamped tree, when there is one,
        # and whether the process runs from a copy of it or from a checkout
        # that may since have moved. `base` is the tree a variant was patched
        # onto and `clean` whether the stamp matched HEAD -- the two facts
        # that say whether "tree 3f2a…" is a commit you can find or an edit
        # that exists only under the run's ref. All four are what
        # `trees.stamp`/`trees.variant` wrote on the record; nothing here
        # re-derives them.
        "tree": (self.spec.code or {}).get("tree", ""),
        "base": (self.spec.code or {}).get("base", ""),
        "clean": (self.spec.code or {}).get("clean"),
        "materialized": self.materialized,
        "submitted_at": self.submitted_at, "started_at": self.started_at,
        "ended_at": self.ended_at, "exit_code": self.exit_code,
        "error": self.error,
    }


class Runner(Protocol):
  """The contract. Only the first four are mandatory."""

  name: str

  def capacity(self) -> List[GpuSlot]:
    """What this runner can place work on, right now."""

  def submit(self, spec: RunSpec) -> Job:
    """Accept a run. Returns immediately; the job may still be queued."""

  def poll(self, job_id: str) -> Job:
    """Current state. Cheap enough to call on a timer."""

  def cancel(self, job_id: str) -> Job:
    """Ask a job to stop. Idempotent on an already-terminal job."""

  # ── optional, and honest about it ────────────────────────────────────
  def logs(self, job_id: str, tail: int = 200) -> Iterable[str]:
    raise NotSupported(f"{self.name} cannot stream logs")

  def attach(self, endpoint: str, credentials: Optional[Dict[str, str]] = None) -> None:
    """Add a machine to this runner's pool. Only pooled runners support it."""
    raise NotSupported(f"{self.name} has no attachable pool")

