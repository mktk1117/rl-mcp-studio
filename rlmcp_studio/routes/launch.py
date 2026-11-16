"""Launching training runs, here or on a configured host, and the list of what was launched."""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rlmcp_studio.api import validate
from rlmcp_studio.context import Studio
from rlmcp_studio.runners import base as runners_base
from rlmcp_studio.runners import local as local_runner
from rlmcp_studio.runners import trees
from rlmcp_studio.sources import recipes as source_recipes
from rlmcp_studio.sources import taskenv as source_taskenv


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the launch, recipe and job routes."""
  cfg = studio.cfg
  library = studio.library
  task_env = studio.task_env
  runner = studio.runner
  hub = studio.hub
  hosts = studio.hosts

  @app.post("/api/build/launch")
  async def api_launch(body: Dict[str, Any]) -> JSONResponse:
    """Open a record and train the task, locally or on a configured host.

    The record first, always. `rlmcp train --record-run` wants an id, and
    opening it here rather than after the fact is what makes the claim cost
    something: a run that is training is a run somebody said what they expected
    from. The hypothesis is required for the same reason `api_next_record`
    requires it -- it is the part only a person can write.
    """
    body = body or {}
    recipe = validate.text(body.get("recipe") or "", "recipe", required=False,
                           limit=1000)
    if recipe:
      return await _launch_from_recipe(body, Path(recipe))
    task = validate.text(body.get("task"), "task", limit=200)
    slug = validate.text(body.get("slug"), "slug", limit=80)
    hypothesis = validate.text(body.get("hypothesis"), "hypothesis", limit=2000)
    num_envs = validate.number(body.get("num_envs"), "num_envs", default=4096,
                               low=1, high=65536)
    iterations = validate.number(body.get("max_iterations"), "max_iterations",
                                 default=0, low=0, high=1_000_000)
    device = validate.text(body.get("device") or "cuda:0", "device", limit=20)
    video_every = validate.text(body.get("video_every") or "", "video_every",
                                required=False, limit=40)
    parent = validate.text(body.get("parent") or "", "parent",
                           required=False, limit=40)

    source = next((s for s in library.sources if s.available()), None)
    if source is None:
      return JSONResponse({"error": "no writable records root"}, status_code=409)

    refused = _already_training(body)
    if refused is not None:
      return refused

    # A variant: a base run's tree plus a patch, written
    # through a throwaway index and never touching the working tree. The
    # base is a record id; its stamped tree is what the patch applies to.
    variant = body.get("variant") if isinstance(body.get("variant"), dict) else None
    base_tree = ""
    if variant:
      base_id = validate.text(variant.get("base"), "variant.base", limit=40)
      base_record = source.store.get_record(base_id)
      base_tree = ((base_record.code or {}).get("tree") if base_record else "") or ""
      if not base_tree:
        return JSONResponse({"error": f"record {base_id} carries no code tree to "
                                      "base a variant on"}, status_code=409)

    record = source.store.new_record(
        slug, task=task, hypothesis=hypothesis,
        parent=parent or None,
        verdict="planned")

    # Stamp first, run second (runners/trees.py). The record carries the
    # tree the run will execute from before the process exists, and the
    # runner materializes that tree into a directory of its own. Outside a
    # git repository this is `kind: none` and the run goes from the working
    # tree itself -- provenance is never worth a failed launch.
    code_root = task_env.cwd or str(Path.cwd())
    if variant:
      try:
        code = trees.variant(code_root, base_tree,
                             validate.text(variant.get("patch") or "", "variant.patch",
                                           required=False, limit=400_000),
                             record.id)
      except trees.TreeError as exc:
        source.store.delete_record(record.id)   # a record with no run is noise
        return JSONResponse({"error": f"the patch does not apply to {base_id}: {exc}"},
                            status_code=409)
    else:
      code = trees.stamp(code_root, record.id)
    if code.get("kind") == "git":
      record.code = code
      source.store.put_record(record)

    args: Dict[str, Any] = {"num-envs": int(num_envs), "device": device}
    if iterations:
      args["max-iterations"] = int(iterations)
    if video_every:
      args["video-every"] = video_every
    # `host` names a training machine from the settings; without it the run
    # is local. A remote spec carries the host's own working directory, and
    # the stamped tree stays a stamp (runners/remote.py says why).
    host_name = validate.text(body.get("host") or "", "host", required=False, limit=80)
    target = hosts.get(host_name) if host_name else None
    if host_name and target is None:
      source.store.delete_record(record.id)
      return JSONResponse({"error": f"no host named {host_name!r} in this studio's settings",
                           "hosts": hosts.describe()}, status_code=404)
    spec = runners_base.RunSpec(
        task=task, entrypoint=local_runner.default_entrypoint(), args=args,
        record_id=record.id, parent=parent or None,
        workdir=(target.cwd if target else task_env.cwd) or "", labels={"slug": slug},
        code=code if code.get("kind") == "git" else {},
        # The runner decides how to materialize: a copy here, or the tree
        # pushed to the host (runners/remote.py). `in_place` is the escape hatch
        # for both.
        materialize=not validate.flag(body.get("in_place"), "in_place"))
    job = await (target or runner).submit(spec)

    await hub.publish("records.changed", {"record": record.id}, record.id)
    return JSONResponse({
        "ok": job.state.value != "failed",
        "record": record.id,
        "slug": record.slug,
        "job": job.describe(),
        "command": (target.command_line(spec) if target
                    else local_runner.command_line(runner, spec)),
    })

  def _already_training(body: Dict[str, Any]) -> Optional[JSONResponse]:
    """One run at a time on the studio's machine, unless `anyway` says otherwise."""
    busy = [j for j in runner.jobs()
            if j.state.value in ("starting", "running")]
    if busy and not validate.flag(body.get("anyway"), "anyway"):
      return JSONResponse(
          {"error": f"{busy[0].spec.task} is already training here "
                    f"({busy[0].id}). Launch anyway with `anyway`, or stop it.",
           "busy": [j.describe() for j in busy]}, status_code=409)
    return None

  def _source_holding(path: Path):
    """The records root a recipe sits beside, else the first writable one.

    The rerun's record goes into the store the recipe was built from, so
    `recipe-010` lands next to `010` -- and the trainer is told that root
    explicitly, because the runner's default is the primary one.
    """
    resolved = path.resolve()
    for source in library.sources:
      if source.available() and resolved.is_relative_to(source.root.resolve()):
        return source
    return next((s for s in library.sources if s.available()), None)

  async def _launch_from_recipe(body: Dict[str, Any], recipe: Path) -> JSONResponse:
    """Train a recipe again, as `rlmcp train --recipe` would from a shell.

    Nothing to type: the recipe is the plan. rl-mcp opens the record itself,
    named `recipe-<run>`, with the original's claimed numbers as the
    prediction and its band as the falsifier; the studio only chooses the
    store, checks the machine is free, and presses the button.
    """
    try:
      from rlmcp.records.recipe import load_manifest, open_reproduction_record
    except ImportError:
      return JSONResponse(
          {"error": "this rl-mcp cannot train a recipe: `rlmcp train --recipe` "
                    "needs rl-mcp's recipe manifest (rlmcp.records.recipe), "
                    "which older releases lack"},
          status_code=409)
    try:
      manifest = load_manifest(recipe)
    except ValueError as exc:
      return JSONResponse({"error": str(exc)}, status_code=400)
    source = _source_holding(recipe)
    if source is None:
      return JSONResponse({"error": "no writable records root"}, status_code=409)
    refused = _already_training(body)
    if refused is not None:
      return refused

    record = open_reproduction_record(source.store, manifest, recipe)
    args: Dict[str, Any] = {"recipe": str(recipe.resolve()),
                            "records-root": str(source.root)}
    # Overrides, only when asked for: the recipe's own values otherwise.
    if body.get("num_envs") is not None:
      args["num-envs"] = int(validate.number(body.get("num_envs"), "num_envs",
                                             default=0, low=1, high=65536))
    if body.get("max_iterations") is not None:
      args["max-iterations"] = int(validate.number(
          body.get("max_iterations"), "max_iterations", default=0, low=1,
          high=1_000_000))
    if body.get("device"):
      args["device"] = validate.text(body.get("device"), "device", limit=20)
    if body.get("video_every"):
      args["video-every"] = validate.text(body.get("video_every"), "video_every",
                                          limit=40)
    spec = runners_base.RunSpec(
        task=str(manifest["task"]), entrypoint=local_runner.default_entrypoint(),
        args=args, record_id=record.id, parent=record.parent,
        workdir=task_env.cwd or "",
        labels={"slug": record.slug, "recipe": str(recipe)})
    job = await runner.submit(spec)
    await hub.publish("records.changed", {"record": record.id}, record.id)
    return JSONResponse({
        "ok": job.state.value != "failed",
        "record": record.id,
        "slug": record.slug,
        "from_run": manifest.get("from_run"),
        "job": job.describe(),
        "command": local_runner.command_line(runner, spec),
    })

  @app.get("/api/build/recipes")
  def api_recipes(task: str = "") -> JSONResponse:
    """Recipes on disk beside the records, for this task or all of them."""
    name = validate.text(task, "task", required=False, limit=200) if task else ""
    return JSONResponse({"recipes": source_recipes.find_recipes(cfg.records, name or None)})

  @app.post("/api/build/recipe")
  async def api_build_recipe(body: Dict[str, Any]) -> JSONResponse:
    """`rlmcp recipe build <record>`, beside the store the record lives in.

    Through the task repository's interpreter like every other rlmcp command
    here, so the package snapshot and the env export are made by the rl-mcp
    that trained the run. The answer is rlmcp's own payload: what was written
    and what the run could not provide.
    """
    body = body or {}
    record_id = validate.text(body.get("record"), "record", limit=60)
    task = validate.text(body.get("task") or "", "task", required=False, limit=200)
    record, source = library.find(record_id, task or None)
    if record is None or source is None:
      return JSONResponse({"error": f"no record {record_id}"}, status_code=404)
    out = source.root / f"recipe-{record.id}"
    argv = [task_env.python, "-c", source_taskenv.ENTRY, "--json", "recipe", "build",
            record.id, "--records-root", str(source.root), "--out", str(out)]
    try:
      proc = await asyncio.create_subprocess_exec(
          *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
          cwd=task_env.cwd or None, env=task_env.environ())
      stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=900)
    except (OSError, asyncio.TimeoutError) as exc:
      return JSONResponse({"error": f"recipe build did not run: {exc}"}, status_code=500)
    try:
      payload = json.loads(stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
      payload = {}
    if proc.returncode != 0 or not payload.get("ok"):
      tail = stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
      return JSONResponse(
          {"error": payload.get("error") or "recipe build failed",
           "stderr": tail, "command": " ".join(shlex.quote(a) for a in argv)},
          status_code=500)
    payload["path"] = str(out)
    payload["recipe"] = source_recipes.describe(out)
    return JSONResponse(payload)

  @app.get("/api/build/launches")
  def api_launches(tail: int = 40) -> JSONResponse:
    """Runs this studio started, newest last, with the tail of their output.

    A launch that fails does so in its first ten seconds, in a traceback, and
    the rail cannot show it -- a record with no session looks exactly like a
    record whose run has not started yet.
    """
    jobs = []
    for job in runner.jobs():
      described = job.describe()
      try:
        described["log"] = list(runner.logs(job.id, tail=tail))
      except Exception:
        described["log"] = []
      jobs.append(described)
    # And the hosts' jobs, the same shape: one list, wherever a run executes.
    for host_runner in hosts.runners.values():
      for job in host_runner.jobs():
        described = job.describe()
        try:
          described["log"] = host_runner.logs(job.id, tail=tail) if tail else []
        except Exception:  # a host that does not answer is not a reason to hide the list
          described["log"] = []
        jobs.append(described)
    # And the runs nobody here started. The record store's heartbeat is the
    # honest census of what is training: a trainer launched from a terminal
    # with `--record-run` holds a lease and writes a session exactly like one
    # of ours, and a capacity answer that ignores it reports a busy GPU as
    # free -- which is how a launch lands on top of somebody's run.
    active = {j.get("record_id") for j in jobs if j.get("state") in ("starting", "running")}
    external = []
    try:
      for summary in library.summaries(liveness=True):
        if summary.get("live") and summary.get("id") not in active:
          external.append({
              "id": f"external-{summary['id']}", "state": "running",
              "task": summary.get("task", ""), "record_id": summary["id"],
              "session_dir": summary.get("session", ""), "log": [],
              "note": "not started by this studio; discovered live through its record's heartbeat",
          })
    except Exception:  # a store that will not answer is not a reason to hide the launcher's own jobs
      pass
    jobs += external
    capacity = ([vars(slot) for slot in runner.capacity()]
                + [vars(slot) for slot in hosts.capacity()])
    if external and capacity and not capacity[0].get("busy_with"):
      capacity[0]["busy_with"] = external[0]["id"]
    return JSONResponse({"jobs": jobs, "capacity": capacity,
                         "hosts": hosts.describe()})

  @app.post("/api/build/launch/cancel")
  async def api_launch_cancel(body: Dict[str, Any]) -> JSONResponse:
    body = body or {}
    job_id = validate.text(body.get("job"), "job", limit=80)
    owner = hosts.find(job_id) or runner
    try:
      job = await owner.cancel(job_id)
    except KeyError:
      return JSONResponse({"error": f"no job {job_id}"}, status_code=404)
    return JSONResponse({"job": job.describe()})
