"""Everything worth asking a task before anybody trains it, asked in one press.

The Build view has `rlmcp check` in it, and a check answers one question
well: *does this environment survive a zero policy for 150 steps.* That is the
first question and it is nowhere near all of them. A task can pass all six
gates in seconds and still be wrong in ways a person would otherwise have to
notice by hand:

* most of its reward terms pay **exactly zero** across the whole roll, which
  means the task as written prices a few things and mentions many more;
* nothing terminates at all, which means every episode runs to a cap;
* the cap is ``5e10`` seconds, which is not a cap.

None of those is a failed gate. Every one of them is a task that will train
into something nobody wanted, and all three are visible in numbers the check
already returns -- they are simply never read against each other. So this is
not a new measurement. It is the reading, made automatic, plus the two things
a check genuinely does not do:

**It asks twice.** ``--policy zero`` says the task survives doing nothing;
``--policy random`` says it survives being shaken. They fail differently and a
term that is dead under both is dead in a way a term dead under one is not.

**It looks.** A robot spawned inside the floor passes every gate there is.
So the suite holds the environment open under each policy and takes frames
through rl-mcp's own ``screenshot`` command -- the same verb `rlmcp shot`
calls -- at a few seconds apart, so the strip shows whether anything moved.
That is the check no number replaces, and it is why the pictures are part of
the verdict rather than a nicety beside it.

**What is *not* here, on purpose.** Nothing in this module builds an
environment, steps one, renders a frame or decides what a gate means. Every
measurement is `rlmcp check`'s or `rlmcp play`'s, run as a subprocess through
:class:`~rlmcp_studio.sources.taskenv.TaskEnv`, exactly as the gates and the
preview already were. What this adds is sequence, comparison and a place to
put the answer -- rule zero, in the feature most tempted to break it.

The report is written to disk beside the frames, so the last verification of a
task survives a studio restart. A verdict that vanishes when the app is
restarted is a verdict nobody trusts enough to act on.
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from rlmcp_studio.paths import safe_name, state_dir
from rlmcp_studio.sources.preview import Preview
from rlmcp_studio.sources.taskenv import TaskEnv

PASS = "pass"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"
RUNNING = "running"

ORDER = [PASS, SKIP, WARN, FAIL]
"""Worst wins. A suite whose verdict is its own worst step, so a strip that is
green means every question was asked and answered well."""

STEPS = 120
"""Steps per check. Fewer than `rlmcp check`'s own 150, because this runs the
check twice and then holds two environments open, and the questions here are
about *which terms ever move* rather than about long-horizon stability."""

ENVS = 4
"""Environments per check. Four, not one: a term that fires for one robot in
four is a term that fires, and one env would call it dead."""

FRAMES = 3
"""Frames per policy. One frame proves the robot rendered; three, seconds
apart, prove it moved -- which is the thing a still picture cannot say and the
reason a task that spawns inside the floor gets through a gate strip."""

FRAME_GAP = 2.5
"""Seconds between frames. Long enough that a 50 Hz environment has stepped a
hundred times, so 'nothing moved' means nothing moved."""

BUILD_TIMEOUT = 300.0
"""How long a held environment is given to construct before the look stage
gives up on it. The same order as `gates.TIMEOUT`, and sized for the same
thing: a cold simulator import on a machine that is also training."""

CHECK_TIMEOUT = 420.0
"""Per check. Two of them run in series, and the second is the one that has to
survive a random policy shaking a contact-rich scene."""

DEAD_TERM = 1e-12
"""Below this a term paid nothing. Not `== 0.0`: a reward that accumulates to
1e-30 across 480 steps is a term that is switched off, and reading it as alive
because a float is not exactly zero is the mistake this constant prevents."""

BUSY_END = 0.5
"""A termination firing on more than half the steps. An episode that ends
every other step is a task nothing can learn, and it reads on the gate strip
as `terminations: ok`."""

LOPSIDED = 8.0
"""rl-mcp's own dominance ratio, and the Build view's existing threshold for
saying one term pays what the task is not about."""


def home() -> Path:
  """Where verifications are kept: frames, and the report about them.

  Beside the previews rather than in the records store, because a verification
  is about a task and not about a run -- there is no record to put it in, which
  is the whole situation the Build view exists for.
  """
  return state_dir("verify")


def _worst(states: Sequence[str]) -> str:
  """The verdict over a set of them. See :data:`ORDER`."""
  worst = PASS
  for state in states:
    if state in ORDER and ORDER.index(state) > ORDER.index(worst):
      worst = state
  return worst


def _finite(value: Any) -> bool:
  try:
    return math.isfinite(float(value))
  except (TypeError, ValueError):
    return False


def _step(sid: str, label: str, state: str, says: str,
          detail: str = "", **extra: Any) -> Dict[str, Any]:
  """One row of the suite, in the shape the panel draws.

  ``says`` is one line and is the whole row when it is read quickly; ``detail``
  is the sentence underneath for whoever is not reading quickly. Both are plain
  prose: they are rendered as text nodes, and a note about a task that looks
  like markdown source is a note nobody finishes reading.
  """
  return {"id": sid, "label": label, "state": state, "says": says,
          "detail": detail, **extra}


# ── reading two checks against each other ────────────────────────────────
#
# This is the part that is genuinely new, and it is arithmetic over answers
# rl-mcp already gave. A term is interesting when the two policies disagree
# about it, or when they agree that it never happened.

def components(zero: Dict[str, Any], shaken: Dict[str, Any]) -> List[Dict[str, Any]]:
  """Every reward and termination term, with what each policy paid it.

  Both checks, side by side, because "this term is dead" is a claim about the
  task and not about the roll: a shaping term that pays nothing to a robot
  doing nothing is normal, and one that pays nothing to a robot being shaken
  for 480 env-steps is switched off, mis-scoped, or naming a body that never
  contacts anything. One column cannot tell those apart and two can.
  """
  rows: List[Dict[str, Any]] = []
  rows += _reward_rows(zero, shaken)
  rows += _termination_rows(zero, shaken)
  return rows


def _by_term(answer: Dict[str, Any], key: str, field: str) -> Dict[str, float]:
  out: Dict[str, float] = {}
  for row in (answer or {}).get(key) or []:
    name = str(row.get("term") or "")
    if name:
      out[name] = float(row.get(field) or 0.0)
  return out


def _reward_rows(zero: Dict[str, Any], shaken: Dict[str, Any]) -> List[Dict[str, Any]]:
  paid = _by_term(zero, "rewards", "mean")
  shook = _by_term(shaken, "rewards", "mean")
  names = list(paid) + [n for n in shook if n not in paid]
  dom = (zero or {}).get("dominance") or {}
  top = str(dom.get("term") or "")
  ratio = float(dom.get("ratio") or 0.0)

  rows: List[Dict[str, Any]] = []
  for name in names:
    a, b = paid.get(name), shook.get(name)
    state, says = PASS, ""
    if a is not None and not _finite(a) or b is not None and not _finite(b):
      state, says = FAIL, "not a finite number"
    elif _dead(a) and _dead(b):
      # The finding this suite was written for. Said as what it means for the
      # task, not as a statistic: a term at zero is a line in the config that
      # is not pricing anything.
      state, says = WARN, "paid nothing under either policy"
    elif _dead(b) and not _dead(a):
      state, says = WARN, "stops paying once the robot moves"
    elif name == top and ratio >= LOPSIDED:
      state = WARN
      says = f"pays {ratio:.0f}× what {dom.get('over') or 'the next term'} pays"
    rows.append({"kind": "reward", "term": name, "zero": a, "random": b,
                 "state": state, "says": says})
  return rows


def _dead(value: Optional[float]) -> bool:
  return value is not None and abs(value) < DEAD_TERM


def _termination_rows(zero: Dict[str, Any], shaken: Dict[str, Any]) -> List[Dict[str, Any]]:
  ends = _by_term(zero, "terminations", "per_step")
  shook = _by_term(shaken, "terminations", "per_step")
  names = list(ends) + [n for n in shook if n not in ends]

  rows: List[Dict[str, Any]] = []
  for name in names:
    a, b = ends.get(name), shook.get(name)
    state, says = PASS, ""
    if (a or 0.0) > BUSY_END or (b or 0.0) > BUSY_END:
      state, says = FAIL, "fires on more than half the steps"
    elif _dead(a) and _dead(b):
      # Not a failure. `time_out` never firing in 120 steps is the truth about
      # a long episode, not a bug -- so this says what was not observed and
      # leaves the conclusion to whoever knows the task.
      state, says = WARN, "never fired in either roll"
    rows.append({"kind": "termination", "term": name, "zero": a, "random": b,
                 "state": state, "says": says})
  return rows


def scene(answer: Dict[str, Any]) -> Dict[str, Any]:
  """What was actually constructed, and whether the numbers in it are sane.

  `rlmcp check` returns this and the Build view never drew it. Two of the four
  fields have an obviously wrong value that no gate looks at: an action space
  of zero, and an episode cap of 5e10 seconds -- sixteen centuries, which is
  what an unset `episode_length_s` looks like once it reaches the wrapper.
  """
  env = (answer or {}).get("env") or {}
  notes: List[str] = []
  state = PASS

  actions = env.get("actions")
  joints = env.get("joints")
  step_dt = env.get("step_dt")
  cap = env.get("max_episode_length_s")

  if not actions:
    state = FAIL
    notes.append(
        "the action space is empty, so nothing a policy outputs reaches the robot")
  if not joints:
    state = FAIL
    notes.append("no joints were reported, so this scene has no articulated body in it")
  if not step_dt:
    state = _worst([state, WARN])
    notes.append("no control step was reported, so nothing here knows the task's clock")
  if _finite(cap) and float(cap or 0) > 3600:
    state = _worst([state, WARN])
    notes.append(
        f"an episode may run for {float(cap):.3g} s, which is not a cap — an "
        "unset episode length reaches the wrapper as a number this size, and "
        "every episode then ends only when a termination fires")
  elif _finite(cap) and 0 < float(cap or 0) < 1:
    state = _worst([state, WARN])
    notes.append(f"an episode is {float(cap):.3g} s long, which is a handful of steps")

  return {"env": env, "state": state, "notes": notes}


# ── the suite ─────────────────────────────────────────────────────────────

class Verification:
  """The last verification of each task, and at most one in flight.

  Shaped like :class:`~rlmcp_studio.sources.gates.Gates` on purpose -- a panel
  needs the same three things from both: an answer, whether one is being
  computed, and how old the one on screen is. What differs is that this takes
  minutes rather than seconds, so it publishes each step as it lands instead of
  appearing all at once at the end: a person watching a five-minute suite
  needs to see which environment is currently constructing.
  """

  def __init__(self, env: TaskEnv, gates: Any = None,
               root: Optional[Path] = None) -> None:
    self.env = env
    self.gates = gates
    self.root = Path(root) if root else home()
    self._reports: Dict[str, Dict[str, Any]] = {}
    self._running: Dict[str, asyncio.Task] = {}
    self._previews: Dict[str, Preview] = {}

  # ── what the panel reads ────────────────────────────────────────────

  def dir_for(self, task: str) -> Path:
    return self.root / safe_name(task)

  def roots(self) -> List[Path]:
    """Where this module writes frames, for whatever serves them.

    A picture in the Build view is fetched through `/api/artifact`, which is
    confined to directories a run wrote into. These are not that -- the studio
    made them itself -- so it says so here rather than the route guessing.
    """
    return [self.root]

  def describe(self, task: str) -> Dict[str, Any]:
    report = self._reports.get(task)
    if report is None:
      report = self._load(task)
      if report is not None:
        self._reports[task] = report
    running = task in self._running
    if report is None:
      return {"task": task, "running": running, "report": None, "age_s": -1.0}
    return {"task": task, "running": running, "report": report,
            "age_s": round(time.time() - float(report.get("at") or 0), 1)}

  # ── running it ──────────────────────────────────────────────────────

  def start(self, task: str, **how: Any) -> Dict[str, Any]:
    """Begin a verification, and return before it has finished.

    Not awaited by its route, because the whole suite is two environment
    builds and two held simulators -- minutes, during which an HTTP request
    that has not returned is a spinner nobody can cancel. The panel already
    polls, and each step is published as it lands.
    """
    if task in self._running:
      return self.describe(task)
    # Seeded before the coroutine is scheduled, not inside it: `ensure_future`
    # returns before `run` has executed a line, so the first poll after this
    # would otherwise come back `running: true` with last week's report under
    # it -- or with none, which draws as "not verified yet" beside a Stop
    # button. The panel must never show an old verdict as the current one.
    self._reports[task] = _blank(task, how)
    job = asyncio.ensure_future(self.run(task, **how))
    self._running[task] = job
    job.add_done_callback(lambda _t, name=task: self._running.pop(name, None))
    return self.describe(task)

  async def stop(self, task: str) -> Dict[str, Any]:
    """Cancel one in flight, and take down whatever it was holding."""
    job = self._running.pop(task, None)
    if job is not None:
      job.cancel()
      try:
        await job
      except (asyncio.CancelledError, Exception):    # noqa: BLE001 - it is over
        pass
    await self._release(task)
    report = self._reports.get(task)
    if report is not None and report.get("running"):
      report["running"] = False
      report["stage"] = ""
      report["error"] = report.get("error") or "stopped before it finished"
      report["verdict"] = _worst([s["state"] for s in report["steps"]
                                  if s["state"] != RUNNING] or [SKIP])
      self._write(task, report)
    return self.describe(task)

  async def run(self, task: str, steps: int = STEPS, num_envs: int = ENVS,
                device: str = "cpu", frames: int = FRAMES,
                look: bool = True) -> Dict[str, Any]:
    """The whole suite, publishing as it goes.

    The order is the order the answers depend on each other in: the zero check
    first because everything else reads its numbers, the random check second
    because the comparison needs both, then the two readings of them, and the
    pictures last because they are the slowest and the least likely to change
    what the numbers already said.
    """
    started = time.time()
    report = _blank(task, {"steps": steps, "num_envs": num_envs,
                           "device": device, "frames": frames})
    report["at"] = started
    self._reports[task] = report

    try:
      zero = await self._check(task, "zero", steps, num_envs, device, report)
      shaken = await self._check(task, "random", steps, num_envs, device, report)
      self._read_scene(zero, report)
      self._read_components(zero, shaken, report)
      if look:
        await self._look(task, num_envs=1, device=device, frames=frames,
                         report=report)
      else:
        self._publish(report, _step("look", "looks right", SKIP,
                                    "no frames were taken",
                                    "The suite was asked not to look."), "")
    except asyncio.CancelledError:
      await self._release(task)
      raise
    except Exception as exc:                        # noqa: BLE001 - reported
      report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
      await self._release(task)
      report["running"] = False
      report["stage"] = ""
      report["seconds"] = round(time.time() - started, 1)
      report["verdict"] = _worst([s["state"] for s in report["steps"]]) \
          if report["steps"] else FAIL
      if report["error"]:
        report["verdict"] = FAIL
      self._write(task, report)
    return self.describe(task)

  # ── the stages ──────────────────────────────────────────────────────

  async def _check(self, task: str, policy: str, steps: int, num_envs: int,
                   device: str, report: Dict[str, Any]) -> Dict[str, Any]:
    """One `rlmcp check`, in rl-mcp's words, filed under the policy it used.

    The zero pass goes through :class:`Gates` when there is one, so that the
    gate strip above this panel shows the answer this suite just computed
    rather than an older one -- two things on one screen disagreeing about
    whether a task imports is the exact confusion the strip's clock exists to
    prevent.
    """
    label = "survives doing nothing" if policy == "zero" else "survives being shaken"
    self._publish(report, _step(policy, label, RUNNING,
                                f"building the task and rolling a {policy} policy"),
                  policy)

    if policy == "zero" and self.gates is not None:
      await self.gates.run(task, steps=steps, num_envs=num_envs,
                           device=device, policy=policy)
      answer = dict((self.gates.cached(task) or {}))
    else:
      answer = await self._ask(task, policy, steps, num_envs, device)

    report["checks"][policy] = answer
    self._publish(report, _step(policy, label, *self._verdict_of(answer, policy),
                                gates=answer.get("gates") or []), "")
    return answer

  async def _ask(self, task: str, policy: str, steps: int, num_envs: int,
                 device: str) -> Dict[str, Any]:
    """`rlmcp check`, in the shape :class:`Gates` files its own answers in.

    Deliberately the same dictionary: the panel draws a strip of gates from it
    and must not need to know which of the two produced it.
    """
    answer = await self.env.json(
        "--json", "check", "--task", task, "--policy", policy,
        "--steps", str(int(steps)), "--num-envs", str(int(num_envs)),
        "--device", device, timeout=CHECK_TIMEOUT)
    empty = {"at": time.time(), "gates": [], "rewards": [], "terminations": [],
             "passed": None, "will_train": None}
    if not answer["ok"]:
      return {**empty, "ok": False, "error": answer["error"]}
    payload = answer["payload"] or {}
    if not payload.get("ok", False):
      return {**empty, "ok": False,
              "error": payload.get("error") or "the check refused to run"}
    result = payload.get("result") or {}
    return {**empty, "ok": True, "error": "", **result}

  def _verdict_of(self, answer: Dict[str, Any], policy: str):
    """A check's rows, as one state and two sentences."""
    if not answer.get("ok"):
      return FAIL, "the check could not run", answer.get("error") or ""
    rows = answer.get("gates") or []
    failed = [r for r in rows if r.get("ok") is False]
    skipped = [r for r in rows if r.get("ok") is None]
    if failed:
      first = failed[0]
      return FAIL, f"{first.get('gate')} failed", first.get("detail") or ""
    if skipped:
      return WARN, f"{len(rows) - len(skipped)} of {len(rows)} gates answered", \
          "; ".join(str(r.get("detail") or "") for r in skipped)
    shape = answer.get("env") or {}
    return PASS, f"{len(rows)} gates, {answer.get('steps', 0)} steps", \
        (f"{shape.get('actions', '?')} actions over {shape.get('joints', '?')} "
         f"joints, {answer.get('seconds', 0)} s"
         if policy == "zero" else
         f"{answer.get('steps', 0)} steps of random actions on "
         f"{answer.get('num_envs', 0)} envs, {answer.get('seconds', 0)} s")

  def _read_scene(self, zero: Dict[str, Any], report: Dict[str, Any]) -> None:
    if not zero.get("ok"):
      self._publish(report, _step("scene", "the scene is sane", SKIP,
                                  "nothing was built to look at",
                                  "The zero check did not construct the "
                                  "environment, so there is no scene to read."), "")
      return
    found = scene(zero)
    report["scene"] = found
    env = found["env"]
    says = (f"{env.get('actions', '?')} actions · {env.get('joints', '?')} joints "
            f"· {env.get('step_dt', '?')} s step")
    self._publish(report, _step("scene", "the scene is sane", found["state"], says,
                                " ".join(found["notes"]) or
                                "Action space, joints, control step and episode "
                                "cap are all the shapes a task this size has.",
                                env=env), "")

  def _read_components(self, zero: Dict[str, Any], shaken: Dict[str, Any],
                       report: Dict[str, Any]) -> None:
    if not zero.get("ok") and not shaken.get("ok"):
      self._publish(report, _step("components", "every term does something", SKIP,
                                  "no roll to read terms from", ""), "")
      return
    rows = components(zero, shaken)
    report["components"] = rows
    dead = [r for r in rows if r["state"] == WARN and "paid nothing" in r["says"]]
    broken = [r for r in rows if r["state"] == FAIL]
    state = _worst([r["state"] for r in rows] or [SKIP])
    live = len([r for r in rows if r["kind"] == "reward"]) - len(dead)
    total = len([r for r in rows if r["kind"] == "reward"])
    says = f"{live} of {total} reward terms paid something"
    detail = ""
    if broken:
      detail = "; ".join(f"{r['term']} {r['says']}" for r in broken[:3])
    elif dead:
      # Named, not counted. "12 terms are dead" sends somebody to read fifteen
      # terms; naming them sends them to the right three lines of the config.
      detail = ("Nothing priced these, under either policy: "
                + ", ".join(r["term"] for r in dead[:8])
                + (f", and {len(dead) - 8} more" if len(dead) > 8 else "")
                + ". A term at zero is a line in the reward config that is not "
                  "shaping anything yet — which is normal for a task being "
                  "written and wrong for one about to train.")
    self._publish(report, _step("components", "every term does something", state,
                                says, detail), "")

  async def _look(self, task: str, num_envs: int, device: str, frames: int,
                  report: Dict[str, Any]) -> None:
    """Hold the environment under each policy and take frames of it.

    The one stage that is not arithmetic over an answer somebody already gave.
    It is here because a robot spawned inside the floor, an object at the
    origin and an arm folded through the table all pass every gate rl-mcp has, and
    the cheapest instrument that finds them is a picture.
    """
    self._publish(report, _step("look", "looks right", RUNNING,
                                "holding the environment open to take frames"),
                  "look")
    shots: List[Dict[str, Any]] = []
    trouble: List[str] = []
    where = self.dir_for(task)
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True, exist_ok=True)

    for policy in ("zero", "random"):
      taken, why = await self._shoot(task, policy, num_envs, device, frames, where)
      shots += taken
      if why:
        trouble.append(f"{policy}: {why}")
      report["shots"] = shots
      self._publish(report, None, "look")

    report["shots"] = shots
    if not shots:
      self._publish(report, _step("look", "looks right", WARN,
                                  "no frames came back",
                                  " · ".join(trouble) or
                                  "The environment would not hold open long "
                                  "enough to render one."), "")
      return
    moved = _moved(shots)
    state = PASS if moved else WARN
    seen = len({s["policy"] for s in shots})
    says = f"{len(shots)} frames, {'both policies' if seen > 1 else 'one policy'}"
    detail = ("Nothing about these frames is checked automatically — they are "
              "here so that a robot inside the floor, a body at the origin or "
              "an empty scene is one glance rather than one training run.")
    if not moved:
      state = WARN
      detail = ("Every frame is byte-identical to the one before it, which "
                "means the picture did not change while the environment "
                "stepped: either nothing is moving, or the renderer is "
                "handing back the same frame. " + detail)
    if trouble:
      state = _worst([state, WARN])
      detail = " · ".join(trouble) + ". " + detail
    self._publish(report, _step("look", "looks right", state, says, detail), "")

  async def _shoot(self, task: str, policy: str, num_envs: int, device: str,
                   frames: int, where: Path):
    """One held environment, `frames` frames of it, then take it down.

    ``--mode hold`` rather than the preview's ``viser``: this wants an
    environment stepping and nothing serving, and a viewer here would allocate
    a port and a scene for a picture nobody is watching live.
    """
    from rlmcp_studio.sources import control

    key = f"verify:{task}:{policy}"
    held = Preview(task, self.env, handle=key)
    self._previews[key] = held
    taken: List[Dict[str, Any]] = []
    try:
      described = await held.start(policy=policy, num_envs=num_envs,
                                   device=device, mode="hold")
      if described.get("error"):
        return taken, described["error"]

      deadline = time.time() + BUILD_TIMEOUT
      while time.time() < deadline:
        if held.published():
          break
        if not held.running:
          return taken, (held.error or "it stopped before it built") \
              + _because(held.tail(8))
        await asyncio.sleep(1.0)
      else:
        return taken, f"it did not finish building within {BUILD_TIMEOUT:.0f} s"

      for index in range(max(1, int(frames))):
        if index:
          await asyncio.sleep(FRAME_GAP)
        try:
          answer = await control.call(held.session_dir, "screenshot",
                                      timeout=90.0, env_id=0)
        except Exception as exc:                    # noqa: BLE001 - reported
          return taken, f"{type(exc).__name__}: {exc}"
        source = Path(str((answer or {}).get("image_path") or ""))
        if not source.is_file():
          return taken, "the screenshot came back with no file behind it"
        # Copied out of the session, because the next verification wipes it and
        # a report whose pictures have been deleted is worse than no report.
        target = where / f"{policy}-{index}{source.suffix or '.png'}"
        shutil.copyfile(source, target)
        taken.append({"policy": policy, "index": index, "path": str(target),
                      "at": time.time(), "size": (answer or {}).get("size") or [],
                      "bytes": target.stat().st_size})
      return taken, ""
    finally:
      await held.stop()
      self._previews.pop(key, None)

  async def _release(self, task: str) -> None:
    """Nothing this suite started outlives it."""
    held = [p for key, p in list(self._previews.items())
            if key.startswith(f"verify:{task}:")]
    for preview in held:
      try:
        await preview.stop()
      except Exception:                             # noqa: BLE001 - it is going
        pass
    for key in [k for k in self._previews if k.startswith(f"verify:{task}:")]:
      self._previews.pop(key, None)

  # ── keeping it ──────────────────────────────────────────────────────

  def _publish(self, report: Dict[str, Any], step: Optional[Dict[str, Any]],
               stage: str) -> None:
    """Put one step into the report, replacing the running row it finished.

    A step appears the moment it starts, as ``running``, and is replaced in
    place when it answers -- so the panel never reorders and a person watching
    can see which environment is currently being built.
    """
    report["stage"] = stage
    if step is None:
      return
    for index, existing in enumerate(report["steps"]):
      if existing["id"] == step["id"]:
        report["steps"][index] = step
        return
    report["steps"].append(step)

  def _write(self, task: str, report: Dict[str, Any]) -> None:
    """The report, beside its frames, so a restart does not lose the verdict."""
    where = self.dir_for(task)
    try:
      where.mkdir(parents=True, exist_ok=True)
      (where / "report.json").write_text(json.dumps(report, indent=1))
    except OSError:
      pass                                          # the in-memory copy stands

  def _load(self, task: str) -> Optional[Dict[str, Any]]:
    """The last verification of this task from a previous studio."""
    try:
      text = (self.dir_for(task) / "report.json").read_text()
    except OSError:
      return None
    try:
      report = json.loads(text)
    except ValueError:
      return None
    if not isinstance(report, dict):
      return None
    # A studio killed mid-suite leaves `running: true` on disk, and nothing is
    # running now -- this process has no such job. Said as it ended rather than
    # left to a spinner that will never stop.
    if report.get("running"):
      report["running"] = False
      report["stage"] = ""
      report["error"] = report.get("error") or "the studio stopped while it ran"
      report["verdict"] = FAIL
    return report

  async def stop_all(self) -> None:
    for task in list(self._running):
      await self.stop(task)


def _blank(task: str, how: Dict[str, Any]) -> Dict[str, Any]:
  """A report with nothing in it yet, which is what "running" looks like."""
  return {
      "task": task, "at": time.time(), "running": True, "stage": "zero",
      "verdict": RUNNING, "seconds": 0.0, "error": "",
      "how": {"steps": int(how.get("steps", STEPS)),
              "num_envs": int(how.get("num_envs", ENVS)),
              "device": str(how.get("device") or "cpu"),
              "frames": int(how.get("frames", FRAMES))},
      "steps": [], "checks": {}, "components": [], "scene": {}, "shots": [],
  }


def _because(lines: Sequence[str]) -> str:
  """The tail of a dead preview's log, as a clause rather than a wall."""
  useful = [line for line in lines if line.strip()][-3:]
  return (" — " + " / ".join(useful)) if useful else ""


def _moved(shots: Sequence[Dict[str, Any]]) -> bool:
  """Whether any two consecutive frames of one policy actually differ.

  By bytes, not by pixels: this is not measuring motion, it is catching the
  case where the renderer hands back one frame forever -- which looks exactly
  like a working preview. Anything subtler is what the
  pictures themselves are for.
  """
  seen: Dict[str, List[bytes]] = {}
  for shot in shots:
    try:
      seen.setdefault(shot["policy"], []).append(Path(shot["path"]).read_bytes())
    except OSError:
      return True                                   # unreadable, not unmoving
  for frames in seen.values():
    if len(frames) > 1 and any(a != b for a, b in zip(frames, frames[1:])):
      return True
  return not any(len(f) > 1 for f in seen.values())


__all__ = ["BUILD_TIMEOUT", "ENVS", "FAIL", "FRAMES", "PASS", "RUNNING", "SKIP",
           "STEPS", "WARN", "Verification", "components", "home", "scene"]
