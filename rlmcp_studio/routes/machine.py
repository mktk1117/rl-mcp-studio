"""The machine underneath: the GPUs, and a terminal."""

from __future__ import annotations

import asyncio
import codecs
import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from rlmcp_studio.context import Studio
from rlmcp_studio.sources import gpu as source_gpu


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the GPU and terminal routes."""
  library = studio.library
  code_roots = studio.code_roots
  terminals = studio.terminals
  guard = studio.guard

  # ── the machine underneath ────────────────────────────────────────────
  @app.get("/api/gpu")
  async def api_gpu() -> JSONResponse:
    """What the GPUs are doing, and which of this studio's runs is doing it.

    The pids come from `nvidia-smi`; the names come from the sessions the
    studio already reads. A list of pids is not a thing anyone recognises, and
    the join is the whole point -- "3.2 GB, 22%, and it is run 012" answers the
    question, where "pid 982491" does not.
    """
    runs: List[Dict[str, Any]] = []
    for summary in library.summaries():
      session = summary.get("session")
      if not session:
        continue
      try:
        from rlmcp.session import Session
        opened = Session.open(Path(session))
        # The pid is in the session's static info; the liveness verdict
        # carries `pid_alive`, not the pid, so reading it from there would
        # name no run at all. A dead session's pid may since have been recycled.
        pid = (opened.info() or {}).get("pid")
        if not opened.is_alive():
          continue
      except Exception:                            # noqa: BLE001
        continue
      if pid:
        runs.append({"pid": pid, "run": summary.get("id"),
                     "task": summary.get("task"),
                     "started_at": summary.get("started_at")})
    return JSONResponse(await source_gpu.snapshot(runs))

  # ── a terminal ────────────────────────────────────────────────────────
  @app.get("/api/terminal")
  def api_terminal() -> JSONResponse:
    """Whether a terminal can be opened, and the credential to open it with.

    The token is in a normal same-origin response on purpose. A cross-origin
    page can *send* a request here but cannot read the reply -- that is the
    browser's rule, not ours -- so this is exactly as secret as the session
    itself, and no cookie or header scheme buys anything on top.
    """
    return JSONResponse({**terminals.describe(), "token": guard.token,
                         "cwd": str(code_roots[0]) if code_roots else ""})

  @app.websocket("/ws/terminal")
  async def ws_terminal(socket: WebSocket) -> None:
    """One shell, for as long as the socket is open.

    Refused *before* `accept()`. Accepting and then closing would already have
    run the handshake, and a client that is told "connected, goodbye" retries;
    one that is refused outright does not.
    """
    problem = guard.refuse(socket.headers.get("origin"),
                           socket.query_params.get("token"))
    if problem:
      print(f"[rlmcp-studio] terminal refused: {problem}", flush=True)
      await socket.close(code=1008)
      return

    await socket.accept()
    session = ""
    try:
      session = await terminals.start(
          cwd=socket.query_params.get("cwd") or (str(code_roots[0]) if code_roots else ""),
          cols=int(socket.query_params.get("cols") or 80),
          rows=int(socket.query_params.get("rows") or 24))
    except Exception as exc:                       # noqa: BLE001
      await socket.send_text(json.dumps({"kind": "error", "text": str(exc)}))
      await socket.close()
      return

    async def pump() -> None:
      """Output out. Bytes are decoded here rather than on the client because
      a UTF-8 character can straddle two reads from a pty, and the client has
      no way to know it is holding half of one.

      Output is also the moment to look at what the shell has become -- it has
      just printed a prompt in a new directory, or the program whose name the
      tab is wearing has just finished.
      """
      decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
      async for chunk in terminals.read(session):
        await say({"kind": "out", "data": decoder.decode(chunk)})
        await announce()
      await say({"kind": "exit"})

    # Two tasks write to this socket -- the pump, and the delayed look after
    # each line you send -- so the sends are serialised. Interleaved frames
    # from two coroutines is the kind of fault that shows up once a week as a
    # client that stopped updating.
    sending = asyncio.Lock()
    state = {"where": {"cwd": "", "busy": ""}}

    async def say(payload: Dict[str, Any]) -> None:
      async with sending:
        await socket.send_text(json.dumps(payload))

    async def announce() -> None:
      """Tell the client anything about the shell that has changed.

      Where the shell is and what it is running, so its tab can say so instead
      of saying "shell 2". Nothing else: the browser sends keystrokes and the
      pty answers them, so the screen needs no help from out here.
      """
      seen = terminals.where(session)
      if seen != state["where"]:
        state["where"] = seen
        await say({"kind": "where", **seen})

    async def look_after(delay: float) -> None:
      """Look again shortly after a line is sent.

      Output is the natural moment to look, and it covers `cd` (which prints a
      prompt) and a command *finishing* (which prints one too). It does not
      cover a command *starting*: `sleep 6` produces nothing at all, and at the
      instant its own echo arrives the shell has not forked it yet. So a line
      sent is a reason to look again in a moment -- which is what makes a tab
      say `sleep` while a shell is busy with one.
      """
      try:
        await asyncio.sleep(delay)
        await announce()
      except asyncio.CancelledError:
        raise
      except Exception:                          # noqa: BLE001
        pass                                     # the socket went; nothing to say

    task = asyncio.create_task(pump(), name="rlmcp-studio-terminal")
    looks: List[asyncio.Task] = []
    try:
      await say({"kind": "ready", "session": session})
      while True:
        message = json.loads(await socket.receive_text())
        if not isinstance(message, dict):
          continue
        kind = message.get("kind")
        if kind == "in":
          await terminals.write(session, str(message.get("data") or ""))
          looks = [t for t in looks if not t.done()]
          if len(looks) < 4:                     # a bound, not a queue
            looks.append(asyncio.create_task(look_after(0.4)))
        elif kind == "resize":
          await terminals.resize(session, message.get("cols"), message.get("rows"))
    except (WebSocketDisconnect, json.JSONDecodeError, RuntimeError):
      pass
    finally:
      for look in looks:
        look.cancel()
      task.cancel()
      # The shell goes with the socket. A pty nobody is attached to is a
      # process the GUI can neither see nor stop, which is the same argument
      # the preview makes for not outliving the studio.
      await terminals.close(session)
