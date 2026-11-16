"""The live feed: one websocket for every event."""

from __future__ import annotations

import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from rlmcp_studio.context import Studio
from rlmcp_studio.events import event
from rlmcp_studio.hub import REPLAY


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the live-feed websocket."""
  hub = studio.hub
  app_guard = studio.app_guard

  # ── live ──────────────────────────────────────────────────────────────
  @app.websocket("/ws")
  async def ws(socket: WebSocket) -> None:
    # Refused before accept(), for the reason the terminal's socket gives:
    # a client told "connected, goodbye" retries; one refused outright does not.
    problem = app_guard.refuse_socket(socket.headers, socket.cookies)
    if problem:
      print(f"[rlmcp-studio] live feed refused: {problem}", flush=True)
      await socket.close(code=1008)
      return
    await socket.accept()
    hub.clients.add(socket)
    try:
      await socket.send_text(json.dumps(
          event("hello", {"replay_window": REPLAY}).to_dict()))
      served = 0
      while True:
        raw = await socket.receive_text()
        try:
          message = json.loads(raw)
        except json.JSONDecodeError:
          continue
        # A frame that is not an object is not a request. Raising here would
        # kill the socket with no close frame and no message -- the client's
        # live feed would simply stop, looking like the app had frozen.
        if not isinstance(message, dict):
          continue
        if message.get("kind") != "resume":
          continue
        try:
          since = int(message.get("seq") or 0)
        except (TypeError, ValueError, OverflowError):
          continue
        # One replay per connection, and only forwards. Otherwise a few hundred
        # bytes of repeated `resume` frames could pull megabytes back, each one
        # replaying the whole backlog again.
        if since < served:
          continue
        served = max(served, since)
        for missed in hub.since(since):
          served = max(served, missed.get("seq", served))
          await socket.send_text(json.dumps(missed))
    except WebSocketDisconnect:
      pass
    finally:
      hub.clients.discard(socket)
