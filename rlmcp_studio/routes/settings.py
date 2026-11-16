"""Settings, the install check, and the configuration the client boots with."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rlmcp_studio import settings as settings_store
from rlmcp_studio.context import Studio
from rlmcp_studio.sources import closeout
from rlmcp_studio.sources import registry as source_registry


def _rlmcp_capabilities() -> Dict[str, bool]:
  """Which optional pieces the installed rl-mcp has.

  Asked of the library rather than assumed from a version number, because an
  installed rl-mcp may be a working checkout on a branch, whose version says
  nothing about which features it carries.
  """
  try:
    from rlmcp.core.extensions import Extension
    selectors = hasattr(Extension, "selectors")
  except Exception:                       # an rl-mcp that moved the class
    selectors = False
  # Whether a task can exist here before its first run at all: without the
  # registry, a task is known only from its records (sources/registry.py).
  # The probe imports no simulator.
  listing = source_registry.capability()
  return {"selectors": selectors,
          "task_registry": listing["available"],
          "task_registry_reason": listing["reason"]}


def mount(app: FastAPI, studio: Studio) -> None:
  """Register the settings, doctor and config routes."""
  cfg = studio.cfg
  library = studio.library

  @app.get("/api/settings")
  def api_settings() -> JSONResponse:
    saved, problem = settings_store.load(cfg.settings_path)
    return JSONResponse({"path": str(cfg.settings_path or ""),
                         "settings": saved, "error": problem})

  @app.patch("/api/settings")
  def api_settings_write(body: Dict[str, Any]) -> JSONResponse:
    """Merge one change into the settings file.

    A *patch*, not a put: the GUI sends the setting it changed, and two tabs
    open on the same studio would otherwise each save their own idea of every
    other setting, with the last one to touch anything winning everything.
    """
    body = body or {}
    if not isinstance(body, dict):
      return JSONResponse({"error": "settings must be a JSON object"},
                          status_code=400)

    merged, problem = settings_store.patch(body, cfg.settings_path)
    if problem:
      return JSONResponse({"error": problem, "settings": merged},
                          status_code=500)

    # Keep this process's own view current, so a reader that asks a second
    # later is not told the old answer by the config it started with. Only the
    # half the GUI owns: changing where the records are takes a restart, and
    # pretending otherwise would leave every open store pointing at the old
    # root. The response says so rather than hiding it.
    if isinstance(body.get(settings_store.UI_KEY), dict):
      cfg.ui = dict(merged.get(settings_store.UI_KEY) or {})

    return JSONResponse({"ok": True, "path": str(cfg.settings_path or ""),
                         "settings": merged,
                         "restart_required": _needs_restart(body)})

  def _needs_restart(changed: Dict[str, Any]) -> List[str]:
    """Which of these settings this process cannot adopt without starting again.

    Said out loud rather than applied quietly: a studio that accepted a new
    records root and went on serving the old one would be lying in the one
    place it must not.
    """
    return sorted(k for k in changed if k in settings_store.SERVER_KEYS)

  # ── cold reads ────────────────────────────────────────────────────────
  @app.get("/api/doctor")
  def api_doctor() -> JSONResponse:
    """What this install and its machine can do -- `rlmcp-studio --check`, as JSON.

    The first thing a fresh install shows, and a section of Settings after
    that: which of the things training needs are here (a GPU, an agent
    credential, a records root, a task package, the menagerie) and the fix
    for each that is not. Computed on request rather than cached, because
    the answer changes exactly when the person acts on it and the page
    should agree with them the moment they come back.
    """
    from rlmcp_studio import doctor
    findings = doctor.run(cfg)
    return JSONResponse({
        "findings": [f.as_dict() for f in findings],
        "healthy": doctor.healthy(findings),
        "where": str(doctor.HERE),
    })

  @app.get("/api/config")
  def api_config() -> JSONResponse:
    return JSONResponse({
        "roots": cfg.describe(),
        # The GUI's own remembered state, so a browser that has never seen
        # this studio still opens in the theme and the panel sizes you chose.
        # The server does not read it; it keeps it, because `localStorage`
        # belongs to one browser and a preference should belong to you.
        "settings": cfg.ui,
        "palette": library.palette(),
        # rl-mcp's verdict vocabulary, beside rl-mcp's verdict colours. The
        # close-out control offers these words and no others: a GUI list would
        # drift from the store's, and every disagreement is either a verdict
        # you cannot reach or one that can only be refused.
        "verdicts": closeout.verdict_vocabulary(),
        # What the *installed* rl-mcp can do, so a panel explaining an absence
        # can name the real reason: "rl-mcp lacks this feature" and "the
        # installed rl-mcp predates it" are different sentences, and only a
        # probe of the installed library can tell them apart.
        "rlmcp": _rlmcp_capabilities(),
    })
