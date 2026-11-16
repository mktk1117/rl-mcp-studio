"""The studio server: HTTP for cold reads, one WebSocket for everything live.

It imports rlmcp and never imports torch or a simulator, exactly like the CLI
and the MCP server. Starting or killing this process cannot disturb training.

This module builds the app: the shared state (`context.py`), the lifespan
tasks, the guard and the client's static files. The routes themselves are in
`routes/`, one module per area of the API.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from rlmcp_studio import context
from rlmcp_studio import vendor
from rlmcp_studio.api.validate import BadRequest
from rlmcp_studio.config import Config, resolve
from rlmcp_studio.routes import (agent, build, docs, feedback, launch, live, machine,
                                 overview, records, run, settings, source)
from rlmcp_studio.sources import remote_ticks as source_remote_ticks
from rlmcp_studio.sources.watcher import Watcher

# Importable from here as well as from where they live: callers and tests
# reach for the app module when they want the server's own answer.
from rlmcp_studio.hub import REPLAY, Hub  # noqa: F401
from rlmcp_studio.routes.agent import MAX_SHOT_CHARS, annotate_tasks  # noqa: F401
from rlmcp_studio.routes.run import RENDERABLE  # noqa: F401
from rlmcp_studio.routes.settings import _rlmcp_capabilities  # noqa: F401

WEB = Path(__file__).resolve().parent / "web"

LIVENESS_POLL_S = 5.0
"""How often to ask whether the trainers are still answering.

A run *ending* is the one change no file announces, so it is the one thing this
app cannot learn from the watcher. Well inside rlmcp's heartbeat interval, and
it costs a stat per session -- well under a millisecond for dozens of them."""

ROUTES = (settings, build, launch, overview, records, run, docs, machine, source,
          feedback, agent, live)
"""Mounted in this order. Where two paths could both match a request the first
one registered wins, so the order is part of the API, not a style choice."""


def create_app(config: Optional[Config] = None) -> FastAPI:
  cfg = config or resolve()
  studio = context.build(cfg)
  library = studio.library
  hub = studio.hub
  agents = studio.agents
  previews = studio.previews
  verification = studio.verification
  terminals = studio.terminals
  app_guard = studio.app_guard
  watch_roots = studio.watch_roots

  @asynccontextmanager
  async def lifespan(_app: FastAPI):
    retired = agents.transcripts.retired
    if retired:
      # Said out loud because it is a one-way move of the user's own work:
      # these were written before conversations were scoped to a records root,
      # so no studio can honestly claim them. Nothing was deleted.
      print(f"[rlmcp-studio] {len(retired)} conversation(s) from before "
            f"per-task scoping moved to "
            f"{agents.transcripts.root.parent / '_unscoped'}", flush=True)
    restored = agents.restore(r["id"] for r in library.summaries())
    if restored:
      print(f"[rlmcp-studio] {restored} conversation(s) read back from disk",
            flush=True)
    watcher = Watcher(watch_roots(), hub.publish)
    watcher.start()

    async def resync() -> None:
      """A run that starts after we did needs its session watched too."""
      known = set(map(str, watcher.roots))
      while True:
        await asyncio.sleep(20)
        current = set(map(str, watch_roots()))
        if current != known:
          known = current
          await watcher.stop()
          watcher.roots = [Path(p) for p in current]
          watcher.start()
          await hub.publish("sessions.rescanned", {"count": len(current)})

    async def heartbeat() -> None:
      """Notice a trainer that *stopped*, which no file change announces.

      Every other update in this app is a file changing under the watcher. A
      run ending is the exception: the process dies, the heartbeat stops being
      written, and nothing happens -- so the last snapshot the browser fetched
      says `running`, and without this it would go on saying it: a status bar
      reading `training` beside numbers frozen an hour earlier.

      Liveness is a function of the clock (`Session.liveness_info` compares the
      heartbeat's age against now), so the only way to learn it is to ask
      again. The event only goes out when the answer *changed*, so a studio
      watching a run that is simply training says nothing.
      """
      signature = library.live_signature()
      while True:
        await asyncio.sleep(LIVENESS_POLL_S)
        try:
          current = library.live_signature()
        except Exception:
          continue
        if current != signature:
          signature = current
          await hub.publish("liveness.changed", {"live": bool(current)})

    async def remote_ticks() -> None:
      """The watcher's events, for runs whose files are on another machine.

      A remote run changes nothing here, so the watcher never speaks for it.
      This asks each open remote session what its status says and publishes
      the same `status.tick` and `liveness.changed` the watcher would have
      (sources/remote_ticks.py), so the client cannot tell the two apart.
      Reads are off the loop: a host that hangs must not stall the app.
      """
      ticker = source_remote_ticks.RemoteTicker()
      while True:
        await asyncio.sleep(source_remote_ticks.REMOTE_POLL_S)
        try:
          addresses = library.live_session_addresses()
          if not addresses:
            continue
          events = await asyncio.to_thread(ticker.poll, addresses)
        except Exception:
          continue
        for kind, data, scope in events:
          await hub.publish(kind, data, scope)

    resyncer = asyncio.create_task(resync(), name="rlmcp-studio-resync")
    beater = asyncio.create_task(heartbeat(), name="rlmcp-studio-heartbeat")
    remote = asyncio.create_task(remote_ticks(), name="rlmcp-studio-remote-ticks")
    try:
      yield
    finally:
      resyncer.cancel()
      beater.cancel()
      remote.cancel()
      await watcher.stop()
      # A preview that outlives the studio is a simulator nobody can see and
      # nobody can stop from the GUI that started it.
      #
      # A *training run* is the opposite, and the asymmetry is deliberate: a
      # run is the experiment, the studio is a window onto it, and closing a
      # window has never been a reason to end an experiment. Launched runs are
      # left alone here on purpose.
      await previews.stop_all()
      # And the environments a verification is holding open to take frames of,
      # for the same reason and with one more: it started two of them without
      # anybody pressing a button per environment.
      await verification.stop_all()
      await terminals.close_all()

  app = FastAPI(title="rlmcp studio", lifespan=lifespan)

  @app.exception_handler(BadRequest)
  async def _bad_request(_request, exc: BadRequest) -> JSONResponse:
    """A malformed field is the caller's problem, and it is told which one.

    Without this, a wrong-typed field would be a bare `Internal Server Error`
    at best and a corrupted record at worst.
    """
    return JSONResponse({"error": str(exc)}, status_code=400)

  @app.exception_handler(OSError)
  async def _os_error(_request, exc: OSError) -> JSONResponse:
    """The filesystem refusing a path the caller supplied. Also theirs."""
    return JSONResponse({"error": f"that path cannot be read: {exc.strerror or exc}"},
                        status_code=400)

  for routes in ROUTES:
    routes.mount(app, studio)

  # ── the client ────────────────────────────────────────────────────────
  if WEB.exists():
    @app.middleware("http")
    async def no_store(request, call_next):
      """Never cache the client.

      The modules are edited constantly and served from disk with no build
      step, so a cached copy means you are looking at code you already
      changed -- a whole class of confusing "the fix did not work" moments.
      There is no CDN here to protect, so the cost is nothing.
      """
      response = await call_next(request)
      if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store, must-revalidate"
      return response

    # The editor and terminal libraries are not in the repository: a wheel
    # carries them in web/vendor, a checkout has them in the user cache
    # (vendor.py). Mounted first, so it wins over the /static mount below.
    app.mount("/static/vendor",
              StaticFiles(directory=vendor.directory() or vendor.cache_dir(), check_dir=False),
              name="vendor")
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def index() -> FileResponse:
      # The page is how a browser gets in: the token rides along as a cookie
      # it cannot read and a cross-site page cannot send (guard.py).
      response = FileResponse(WEB / "index.html")
      response.set_cookie(**app_guard.cookie())
      return response

  app.state.guard = app_guard
  app.state.hosts = studio.hosts

  @app.middleware("http")
  async def guarded(request: Request, call_next):
    """Every route under /api needs the credential; the page and its files do not.

    Refused with one sentence and no hint about which check failed. The
    terminal's own guard stays as it is -- it grants a shell, and a second,
    per-connection check on the one thing that executes is not redundancy.
    """
    path = request.url.path
    if path.startswith("/api/") and not app_guard.allows(
        request.method, request.headers, request.cookies):
      return JSONResponse({"error": "this studio needs its credential: open its page in a "
                                    "browser, or send its token as a bearer"},
                          status_code=401)
    return await call_next(request)

  return app
