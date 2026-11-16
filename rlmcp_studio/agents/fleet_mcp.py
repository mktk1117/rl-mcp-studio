"""The studio's own MCP server: the fleet, keyed by run id.

rl-mcp's server (`rlmcp-server`) is pinned to *one* session: the run this
conversation is about, its parameters, its checkpoints, its screenshots. That
is the right tool for driving a run and the wrong one for choosing the next
one. Choosing is a question about the fleet -- what is training where, what
has been tried, what a sibling did -- and about launching, which is the one
act rl-mcp's server cannot do because it lives inside a run that already
exists.

So this is a second, small server beside it. It is a stdio process the
agent's SDK spawns, and it does nothing itself: every tool is one HTTP call
to the studio that spawned it, over the same routes the GUI uses, so the
agent and the person can never be shown different fleets. `RLMCP_STUDIO_URL`
says where that studio is; `RLMCP_STUDIO_TASK` is the task this conversation
is about, the default for every tool that takes one.

What these tools add for an agent: **exploring several tuning paths at
once**. `studio_launch` with a `base` run and
a `patch` launches that run's stamped tree plus the patch as a sibling, from
a copy of its own, without touching the working tree -- five variants are
five refs from one base and five records under one parent, and
`studio_compare` says what differed. The record tree is the agents' shared
memory of what has been tried, which is why every launch here opens a record
with a hypothesis first: a run nobody said what they expected from is a run
the tree cannot learn from.

Tools take a record id; `host` appears
in every answer and is one constant until there is a fleet to choose from.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

try:
  from mcp.server.mcpserver import MCPServer as _Server
except ImportError:
  try:
    from mcp.server.fastmcp import FastMCP as _Server
  except ImportError:
    try:
      from fastmcp import FastMCP as _Server  # type: ignore
    except ImportError:  # pragma: no cover - depends on the install
      _Server = None  # type: ignore[assignment]

NAME = "studio"
"""The server's name in the agent's config: its tools are `mcp__studio__*`."""

SUMMARY_KEYS = ("id", "slug", "seq", "task", "stage", "date", "verdict", "line", "parent",
                "weights", "change", "metrics", "live", "session", "feedback_open")
"""What a run summary keeps when handed to a model: the record's claim in one
line (`line`: hypothesis, then how it ended), its verdict, what changed from
the parent, and the headline metrics. Everything else the GUI draws (assets,
positions, thumbnails) is noise in a context window."""

RUNS_LIMIT = 40


class StudioError(RuntimeError):
  """The studio answered with an error, or did not answer."""


class Studio:
  """One studio, over HTTP. The only thing here that knows a URL."""

  def __init__(self, url: str = "", task: str = ""):
    self.url = (url or os.environ.get("RLMCP_STUDIO_URL") or "http://127.0.0.1:8730").rstrip("/")
    self.task = task or os.environ.get("RLMCP_STUDIO_TASK") or ""

  def request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
              params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One call, one JSON answer. An error status is an exception with the
    studio's own message, which is written for a person and reads fine to a
    model too."""
    query = ""
    if params:
      from urllib.parse import urlencode
      query = "?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"}
    # The studio guards /api (guard.py); the token reaches this process in
    # its environment, put there by the host that spawned it.
    token = os.environ.get("RLMCP_STUDIO_TOKEN", "")
    if token:
      headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(self.url + path + query, data=data, method=method,
                                 headers=headers)
    try:
      with urllib.request.urlopen(req, timeout=60) as answer:
        return json.load(answer)
    except urllib.error.HTTPError as exc:
      try:
        detail = json.load(exc)
      except Exception:
        detail = {"error": exc.reason}
      raise StudioError(str(detail.get("error") or detail)) from None
    except urllib.error.URLError as exc:
      raise StudioError(f"the studio at {self.url} did not answer: {exc.reason}") from None

  # ── the tools' bodies, plain functions so a test can call them ──────────

  def capacity(self) -> Dict[str, Any]:
    answer = self.request("GET", "/api/build/launches", params={"tail": 0})
    jobs = answer.get("jobs", [])
    busy = [j for j in jobs if j.get("state") in ("starting", "running")]
    return {
        "host": _host(),
        "slots": answer.get("capacity", []),
        "hosts": answer.get("hosts", []),
        "training": [_job(j) for j in busy],
        "free": sum(1 for s in answer.get("capacity", []) if not s.get("busy_with")),
    }

  def runs(self, task: str = "", parent: str = "", limit: int = RUNS_LIMIT) -> Dict[str, Any]:
    task = task or self.task
    answer = self.request("GET", "/api/runs", params={"task": task})
    runs = answer.get("runs", [])
    if parent:
      runs = [r for r in runs if str(r.get("parent") or "") == parent]
    trimmed = [{k: r[k] for k in SUMMARY_KEYS if k in r} for r in runs]
    return {"host": _host(), "task": task, "count": len(trimmed),
            "runs": trimmed[-limit:], "note": (
                f"showing the newest {limit} of {len(trimmed)}" if len(trimmed) > limit else "")}

  def launches(self, tail: int = 20) -> Dict[str, Any]:
    answer = self.request("GET", "/api/build/launches", params={"tail": tail})
    return {"host": _host(), "jobs": [_job(j, log=True) for j in answer.get("jobs", [])]}

  def launch(self, slug: str, hypothesis: str, task: str = "", num_envs: int = 4096,
             max_iterations: int = 0, parent: str = "", base: str = "",
             patch: str = "", anyway: bool = False, host: str = "") -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "task": task or self.task, "slug": slug, "hypothesis": hypothesis,
        "num_envs": num_envs, "max_iterations": max_iterations,
        "parent": parent or base, "anyway": anyway,
    }
    if host:
      body["host"] = host
    if base:
      body["variant"] = {"base": base, "patch": patch or ""}
    elif patch:
      raise StudioError("a patch needs a `base` run whose tree it applies to")
    answer = self.request("POST", "/api/build/launch", body=body)
    job = answer.get("job") or {}
    return {
        "host": _host(), "ok": bool(answer.get("ok")),
        "record": answer.get("record"), "slug": answer.get("slug"),
        "job": _job(job), "command": answer.get("command", ""),
        "variant_of": base or None,
    }

  def cancel(self, job: str) -> Dict[str, Any]:
    answer = self.request("POST", "/api/build/launch/cancel", body={"job": job})
    return {"host": _host(), "job": _job(answer.get("job") or {})}

  def compare(self, record: str, against: str = "", task: str = "") -> Dict[str, Any]:
    answer = self.request("GET", "/api/diff", params={
        "record_id": record, "against": against, "task": task or self.task})
    code = answer.get("code") or answer.get("snapshot") or {}
    config = answer.get("config") or {}
    out = {"host": _host(), "from": against or answer.get("against") or answer.get("from"),
           "to": record}
    if isinstance(config, dict):
      out["config"] = {k: config[k] for k in ("changed", "added", "removed", "entries")
                       if k in config}
    if isinstance(code, dict):
      out["code"] = {k: code[k] for k in ("ok", "reason", "changed", "added", "removed",
                                          "empty", "clean") if k in code}
      files = code.get("files") or []
      out["code"]["files"] = [
          {"path": f.get("path"), "trivial": f.get("trivial", False),
           "hunks": len(f.get("hunks") or [])} for f in files[:60]]
    return out


def _host() -> str:
  return os.environ.get("RLMCP_STUDIO_HOST", "")


def _job(job: Dict[str, Any], log: bool = False) -> Dict[str, Any]:
  keep = ("id", "state", "task", "record_id", "slot", "session_dir", "workdir",
          "tree", "materialized", "note", "error", "exit_code", "started_at", "ended_at")
  out = {k: job[k] for k in keep if k in job and job[k] not in (None, "")}
  if log and job.get("log"):
    out["log_tail"] = list(job["log"])[-12:]
  return out


# ── the server ─────────────────────────────────────────────────────────────


def create_server(studio: Optional[Studio] = None):
  if _Server is None:
    raise RuntimeError("no MCP server SDK in this interpreter: pip install mcp")
  studio = studio or Studio()
  mcp = _Server(NAME)

  @mcp.tool()
  def studio_capacity() -> Dict[str, Any]:
    """What this studio can train on right now: GPU slots, and what is already training.

    Ask before launching. A slot that is busy is somebody's run; launching
    over it needs `anyway=true` and a reason."""
    return studio.capacity()

  @mcp.tool()
  def studio_runs(task: str = "", parent: str = "") -> Dict[str, Any]:
    """Every run of a task as the record tree knows it, newest last.

    `parent` narrows to one run's children: the siblings of a sweep, or the
    variants launched from one base. Each entry is a record: its claim
    (hypothesis, prediction), its verdict, whether it is live, and the
    headline numbers. This is the shared memory of what has been tried;
    read it before proposing what to try next."""
    return studio.runs(task=task, parent=parent)

  @mcp.tool()
  def studio_launches(tail: int = 20) -> Dict[str, Any]:
    """Runs this studio started, with the tail of each one's output.

    Where a launch that failed in its first seconds shows up -- a traceback
    at import, a task the environment cannot build -- which the record tree
    cannot show, since such a run never wrote a session."""
    return studio.launches(tail=tail)

  @mcp.tool()
  def studio_launch(slug: str, hypothesis: str, task: str = "", num_envs: int = 4096,
                    max_iterations: int = 0, parent: str = "", base: str = "",
                    patch: str = "", anyway: bool = False, host: str = "") -> Dict[str, Any]:
    """Open a record and train the task on the machine the studio runs on.

    The record comes first, always: `hypothesis` is what you expect and why,
    in a sentence, and a run nobody said that for is one the tree cannot learn
    from. `slug` names it (short, lowercase, hyphens).

    **A variant** -- to explore several tuning paths at once -- is `base`
    (a record id) plus `patch` (a unified diff against that run's stamped
    tree). It launches from a copy of that tree with the patch applied, never
    from the working tree, so the files in the editor do not move and five
    variants can be in flight from one base; each lands under `base` as its
    parent. A patch that does not fit the base is refused with git's reason.

    Without `base`, the launch stamps the working tree as it is now and runs
    from a copy of that. `max_iterations` 0 means the task's own default.
    One run at a time per slot unless `anyway` is true.

    `host` names a training machine from `studio_capacity` (its `hosts`);
    empty means the studio's own machine. On a host the trainer runs the host's own
    checkout for now, and the record is filled from what the host reports."""
    return studio.launch(slug=slug, hypothesis=hypothesis, task=task, num_envs=num_envs,
                         max_iterations=max_iterations, parent=parent, base=base,
                         patch=patch, anyway=anyway, host=host)

  @mcp.tool()
  def studio_cancel(job: str) -> Dict[str, Any]:
    """Stop a run this studio started, by job id (from studio_launch or studio_launches).

    Asks the trainer to stop cleanly first (it finishes its batch and writes
    a checkpoint), then insists. Say why in the conversation before you do
    it: a run you stop is hours of somebody's GPU."""
    return studio.cancel(job)

  @mcp.tool()
  def studio_compare(record: str, against: str = "", task: str = "") -> Dict[str, Any]:
    """What differed between two runs: config, and the code they were stamped with.

    `against` defaults to the record's parent. The answer is counts and file
    names, not patches -- enough to know *where* two siblings differed; open
    the file if you need the lines."""
    return studio.compare(record=record, against=against, task=task)

  return mcp


def describe() -> List[str]:
  """The tool names, for a preflight that wants to say what is attached."""
  return ["studio_capacity", "studio_runs", "studio_launches", "studio_launch",
          "studio_cancel", "studio_compare"]


def argv() -> List[str]:
  """How the agent's SDK starts this: the studio's own interpreter, this module."""
  return [sys.executable, "-m", "rlmcp_studio.agents.fleet_mcp"]


def main() -> None:
  if "--describe" in sys.argv[1:]:
    print("\n".join(describe()))
    return
  create_server().run()


if __name__ == "__main__":
  main()
