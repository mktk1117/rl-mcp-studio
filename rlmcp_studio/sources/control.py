"""Steering: sending commands to a running trainer.

The studio does not know the protocol. rlmcp's :class:`~rlmcp.session.Session`
already has the client half -- ``submit``, ``poll``, ``wait``, ``call`` -- so
this module is a thin async wrapper around it plus the one rule the CLI and MCP
server both follow: **refuse up front when the trainer is dead**, and say what
still works.

Commands execute between rollout batches, so nothing here can race the
simulator. That is rlmcp's guarantee, not ours; we only have to not undermine
it, which means never touching inbox/ or outbox/ directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

from rlmcp.session import Session

DEFAULT_TIMEOUT = 120.0

#: Commands that only look. Everything else changes a live run and the studio
#: asks first -- the same line agents/policy.py draws, drawn once more here
#: because the HTTP surface is reachable without an agent.
READ_ONLY = {
    "help", "status", "list_parameters", "get_parameter", "list_metrics",
    "get_metrics", "plot_metrics", "screenshot", "record_video", "record_trace",
    "plot_trace", "diagnose", "curriculum_status", "list_checkpoints",
}


class TrainerGone(RuntimeError):
  """The trainer is not there to answer.

  Distinct from a failed command: one means "ask something else", the other
  means "that did not work".
  """



async def call(session_dir: Path | str, cmd: str,
               timeout: float = DEFAULT_TIMEOUT, **args: Any) -> Dict[str, Any]:
  """Send one command and wait for its answer.

  ``Session.call`` blocks on the filesystem, so it runs in a worker thread --
  the event loop is also serving the websocket that carries this run's live
  metrics, and stalling it would freeze the very thing you are watching.
  """
  directory = Path(session_dir)
  session = Session.open(directory)
  live = session.liveness_info()

  if live.get("state") == "dead":
    raise TrainerGone(
        f"the trainer for this run is {live.get('state')} "
        f"({live.get('reason') or 'no heartbeat'}). Metrics, records, code and "
        "diffs still work; anything that needs the process does not.")

  clean = {k: v for k, v in args.items() if v is not None}

  def blocking() -> Any:
    return session.call(cmd, timeout=timeout, **clean)

  response = await asyncio.to_thread(blocking)
  if getattr(response, "ok", False):
    result = response.result
    payload = result if isinstance(result, dict) else {"result": result}
    return {"ok": True, "cmd": cmd, **payload}
  return {"ok": False, "cmd": cmd, "error": getattr(response, "error", "no answer")}


async def parameters(session_dir: Path | str, contains: str = "") -> Dict[str, Any]:
  """The parameter schema: live if the trainer answers, from disk if not.

  Degrading to the published snapshot matters more than it looks -- most runs
  you want to read the parameters of have already finished.
  """
  directory = Path(session_dir)
  try:
    answer = await call(directory, "list_parameters", timeout=20, contains=contains or None)
    if answer.get("ok"):
      # The same shape as the snapshot path below. The live answer is a *list*
      # of specs and the snapshot is a map, and letting both out of here would
      # make every consumer know that -- including the shape check in front of
      # `set_parameter`, which would quietly stop checking whenever the trainer
      # was the one answering.
      return {**answer, "live": True,
              "parameters": _as_parameters(answer.get("parameters"))}
  except TrainerGone:
    pass
  except Exception:
    pass

  session = Session.open(directory)
  schema = session.params() or {}
  # rlmcp writes params.json as a flat map of key -> spec. Splatting that into
  # the payload would put every parameter at the *top level*, beside `ok` and
  # `live`, where the client cannot find them -- a finished run's Parameters
  # tab would read "this run published no parameter schema" with the whole
  # schema sitting in the response. One shape out of here, whatever came in.
  return {"ok": bool(schema), "live": False, "source": str(session.params_file),
          "parameters": _as_parameters(schema)}


def _as_parameters(schema: Any) -> Dict[str, Any]:
  """rlmcp's parameter schema in the one shape this app passes around: a map
  from key to spec, whichever of the three shapes rlmcp used."""
  if isinstance(schema, list):
    return {p.get("key"): p for p in schema if isinstance(p, dict) and p.get("key")}
  if not isinstance(schema, dict):
    return {}
  for key in ("parameters", "schema", "params"):
    if key in schema:
      return _as_parameters(schema[key])
  return schema
