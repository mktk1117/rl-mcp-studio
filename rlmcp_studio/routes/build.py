"""The Build view: the task list, one task's preview, gates and verification, and beginning a task."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rlmcp_studio import briefs
from rlmcp_studio.api import validate
from rlmcp_studio.context import Studio
from rlmcp_studio.sources import control
from rlmcp_studio.sources import gates as source_gates
from rlmcp_studio.sources import preview as source_preview
from rlmcp_studio.sources import registry as source_registry
from rlmcp_studio.sources import starting as source_starting
from rlmcp_studio.sources import verify as source_verify


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the Build view's routes."""
  library = studio.library
  task_env = studio.task_env
  registry = studio.registry
  previews = studio.previews
  gates = studio.gates
  verification = studio.verification
  starting = studio.starting
  hub = studio.hub

  @app.get("/api/tasks")
  async def api_tasks(refresh: bool = False) -> JSONResponse:
    """Every task, from both of the places a task can come from.

    The records say what has run; the registry says what *could* run, which is
    the only way a task being built -- one with no run yet -- appears here at
    all. The registry half is a subprocess and may be absent or slow, so its state
    travels beside the list rather than being folded into it -- a switcher that
    silently lists fewer tasks than exist is worse than one that says why.
    """
    listing = await registry.tasks(refresh=refresh)
    tasks, status = source_registry.merge(library.tasks(), listing)
    # And the ones somebody has begun here, which are a task in the only sense
    # that matters for the next twenty minutes: there is a directory, and
    # something to build.
    tasks = source_starting.merge(tasks, starting.all())
    return JSONResponse({"tasks": tasks, "registry": status})

  @app.get("/api/build")
  async def api_build(task: str) -> JSONResponse:
    """Everything the Build view draws for one task.

    Deliberately one read. The view shows the preview, the gates and where
    both of them run, and three requests for three panels of one screen is
    three chances for them to disagree about which task you are looking at.
    """
    name = validate.text(task, "task", limit=200)
    # The third clock. The preview holds the environment it built when it
    # started and the gate strip holds the answer `rlmcp check` gave when it
    # ran; an edit invalidates both, and without this clock nothing on screen
    # could tell you that the green gates and the running preview were about
    # different versions of the same task.
    scope = studio.task_scope(name)
    return JSONResponse({
        "task": name,
        "preview": previews.describe(name),
        "gates": gates.describe(name),
        "verify": verification.describe(name),
        "code": studio.code_clock(scope),
        "where": task_env.describe(),
    })

  @app.post("/api/build/preview")
  async def api_preview(body: Dict[str, Any]) -> JSONResponse:
    """Start, restart or stop the preview for one task.

    A restart rather than a reload, because Python binds modules at import and
    a reloaded env is a stale env that looks like a bad reward.
    """
    body = body or {}
    name = validate.text(body.get("task"), "task", limit=200)
    action = validate.text(body.get("action"), "action", limit=20)
    if action not in ("start", "restart", "stop"):
      return JSONResponse({"error": "action is start, restart or stop"},
                          status_code=400)
    how = {
        "policy": validate.text(body.get("policy") or "zero", "policy", limit=10),
        "num_envs": validate.number(body.get("num_envs"), "num_envs",
                                    default=source_preview.DEFAULT_ENVS,
                                    low=1, high=64),
        "device": validate.text(body.get("device") or "cpu", "device", limit=20),
    }
    preview = previews.of(name)
    if action == "stop":
      described = await preview.stop()
    elif action == "restart":
      described = await preview.restart(policy=how["policy"],
                                        num_envs=int(how["num_envs"]),
                                        device=how["device"])
    else:
      described = await preview.start(policy=how["policy"],
                                      num_envs=int(how["num_envs"]),
                                      device=how["device"])
    await hub.publish("build.preview", {"task": name, "state": described["state"]},
                      None)
    return JSONResponse({"preview": described})

  @app.post("/api/build/command")
  async def api_build_command(body: Dict[str, Any]) -> JSONResponse:
    """Send one rlmcp command to a task's preview.

    The preview is a session, so this is the run command route with a
    different way of naming the session -- by task rather than by record,
    because a task being built has no record. The same confirmation rule
    applies: read-only commands go straight through, anything else says it
    means it.
    """
    body = body or {}
    name = validate.text(body.get("task"), "task", limit=200)
    cmd = validate.text(body.get("cmd"), "cmd", limit=120)
    args = validate.mapping(body.get("args"), "args",
                            reserved=("cmd", "timeout", "session_dir"))
    confirmed = validate.flag(body.get("confirmed"), "confirmed")
    timeout = validate.number(body.get("timeout"), "timeout",
                              default=120, low=0.1, high=3600)

    preview = previews.of(name)
    if not preview.running:
      return JSONResponse(
          {"error": "there is no preview of this task to talk to; start one",
           "no_preview": True}, status_code=409)
    if cmd not in control.READ_ONLY and not confirmed:
      return JSONResponse(
          {"error": f"`{cmd}` changes the preview; resend with confirmed=true",
           "needs_confirmation": True}, status_code=428)
    try:
      answer = await control.call(preview.session_dir, cmd, timeout=timeout, **args)
    except control.TrainerGone as exc:
      return JSONResponse({"error": str(exc), "trainer_gone": True}, status_code=409)
    except Exception as exc:
      return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(answer)

  @app.post("/api/build/new")
  async def api_new_task(body: Dict[str, Any]) -> JSONResponse:
    """Begin a task: a directory, a note of what it is for, and an agent on it.

    The studio writes **no code here**, and that is the design rather than an
    omission: what a task looks like is rl-mcp's to say, in
    `docs/your-task.md`, and a template kept in this repository would go on
    producing an outdated shape in silence. So this makes the directory, says
    what the task is meant to do, and hands both to the agent -- with that
    directory as the one place it may edit without asking.
    """
    body = body or {}
    task = validate.text(body.get("task"), "task", limit=120)
    package = validate.text(body.get("package"), "package", limit=80)
    what = validate.text(body.get("what"), "what", limit=4000)
    robot = validate.text(body.get("robot") or "", "robot", required=False, limit=80)
    shape = validate.text(body.get("shape") or "full", "shape", required=False, limit=20)

    problem = source_starting.check_names(task, package)
    if problem:
      return JSONResponse({"error": problem}, status_code=400)

    # `Path("")` is `Path(".")`, so the empty answer -- no task repository
    # beside the records root -- must be refused explicitly, or the package
    # would be made in whatever directory the studio was started from: the
    # source tree under a test, or `$HOME` for a studio started there.
    root = Path(task_env.cwd) if task_env.cwd else None
    if root is None or not root.is_dir():
      return JSONResponse(
          {"error": "there is no task repository next to the records root, so "
                    "there is nowhere to put a package"}, status_code=409)
    where = root / package
    if where.exists():
      return JSONResponse(
          {"error": f"{where} already exists. Pick another package name, or "
                    "open the task that lives there."}, status_code=409)

    listing = registry.cached() or {}
    if any(row.get("task") == task for row in listing.get("tasks", [])):
      return JSONResponse({"error": f"`{task}` is already registered here"},
                          status_code=409)

    try:
      where.mkdir(parents=True)
    except OSError as exc:
      return JSONResponse({"error": f"could not make {where}: {exc}"},
                          status_code=500)

    entry = starting.add(task, package, str(where), what, robot, shape)
    # Teach this studio to import what it has just made. Both environments:
    # `task_env` is what the gates, the preview and the launch run under, and
    # the registry keeps its own for the listing subprocess.
    task_env.learn(package)
    registry.learn(package)
    await hub.publish("records.changed", {"record": ""}, None)
    return JSONResponse({"ok": True, "task": task, "starting": entry})

  @app.post("/api/build/agent")
  async def api_build_agent(body: Dict[str, Any]) -> JSONResponse:
    """Say something to the agent about a *task*, not about a run.

    Every other conversation in this app is keyed to a record, because every
    other conversation is about an experiment that happened. A task being
    built has no record and may never have had a run, and it is exactly the
    thing you most want to talk to an agent about -- so the conversation is
    keyed to the task instead, and the id says which kind it is.
    """
    body = body or {}
    task = validate.text(body.get("task"), "task", limit=200)
    text = validate.text(body.get("text"), "text", limit=20000)
    thread = validate.identifier(body.get("thread") or "main", "thread")
    interrupt = validate.flag(body.get("interrupt"), "interrupt")

    return await briefs.tell_task_agent(studio, task, text, thread, interrupt=interrupt)

  @app.post("/api/build/check")
  async def api_check(body: Dict[str, Any]) -> JSONResponse:
    """Run rl-mcp's own gates against this task, and keep the answer."""
    body = body or {}
    name = validate.text(body.get("task"), "task", limit=200)
    steps = validate.number(body.get("steps"), "steps",
                            default=source_gates.DEFAULT_STEPS, low=1, high=5000)
    num_envs = validate.number(body.get("num_envs"), "num_envs", default=4,
                               low=1, high=64)
    device = validate.text(body.get("device") or "cpu", "device", limit=20)
    policy = validate.text(body.get("policy") or "zero", "policy", limit=10)
    answer = await gates.run(name, steps=int(steps), num_envs=int(num_envs),
                             device=device, policy=policy)
    await hub.publish("build.gates", {"task": name,
                                      "passed": (answer.get("answer") or {}).get("passed")},
                      None)
    return JSONResponse({"gates": answer})

  @app.post("/api/build/verify")
  async def api_verify(body: Dict[str, Any]) -> JSONResponse:
    """Run the whole suite against this task, or stop the one running.

    Returns before the suite has finished, which is the only honest shape for
    it: two environment builds and two held simulators is minutes, and an HTTP
    request that has not returned is a spinner nobody can cancel. The Build
    view polls `/api/build` and each step lands in the report as it answers.
    """
    body = body or {}
    name = validate.text(body.get("task"), "task", limit=200)
    if body.get("stop"):
      return JSONResponse({"verify": await verification.stop(name)})
    how: Dict[str, Any] = {
        "steps": int(validate.number(body.get("steps"), "steps",
                                     default=source_verify.STEPS, low=1, high=5000)),
        "num_envs": int(validate.number(body.get("num_envs"), "num_envs",
                                        default=source_verify.ENVS, low=1, high=64)),
        "device": validate.text(body.get("device") or "cpu", "device", limit=20),
        "frames": int(validate.number(body.get("frames"), "frames",
                                      default=source_verify.FRAMES, low=1, high=12)),
        # Looking costs two more environment builds than the numbers do. Worth
        # being able to skip on a machine with no renderer, and not worth
        # skipping by default -- the picture is the half no gate replaces.
        "look": body.get("look", True) is not False,
    }
    answer = verification.start(name, **how)
    await hub.publish("build.verify", {"task": name, "running": True}, None)
    return JSONResponse({"verify": answer})
