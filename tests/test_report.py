"""The task's whole history as one file, in the studio's own look.

The page is composed in the browser out of the studio's panels, so most of
what makes it right is checked by looking at it in a browser.
What can be pinned here is the contract around it: the price is quoted per
media choice before anything is written, the file lands under the task's own
records root and downloads rather than renders, the route refuses what is not
a page, and the client module really is built from the studio's components
rather than a second drawing of them.
"""

from __future__ import annotations

import re
from pathlib import Path

from rlmcp_studio.sources import library as source_library
from rlmcp_studio.sources import report as source_report
from rlmcp_studio.sources.records import Library
from tests.client_source import read_js

TASK = "Fake-Task-G1"
JS = Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web" / "js"

PAGE = "<!doctype html><html><head><title>t</title></head><body><div class='app rep'>hi</div></body></html>"


def _roots(library: Library, task: str = TASK):
  def root_of(record):
    _found, source = library.find(record.id, task)
    return source.store.media_root if source else None
  return root_of


def _plan(records_root: Path):
  library = Library([records_root])
  records = source_library.collect(library.sources, TASK)
  return source_report.plan(records, _roots(library))


def test_the_plan_prices_each_media_choice_before_anything_is_written(records_root, store,
                                                                       make_frame, tmp_path):
  first = make_frame(tmp_path / "progress_env0_it000000.png")
  last = make_frame(tmp_path / "progress_env0_it000100.png")
  plot = make_frame(tmp_path / "tracking.png")
  record = store.get_record("001")
  record.assets = {
      "videos": [[store.media.put("001", str(first), "iteration 0", kind="videos"), "iteration 0"],
                 [store.media.put("001", str(last), "iteration 100", kind="videos"), "iteration 100"]],
      "plots": [[store.media.put("001", str(plot), "tracking", kind="plots"), "tracking"]],
  }
  store.put_record(record)

  planned = _plan(records_root)
  assert planned["runs"] == 2 and planned["clips"] == 2
  modes = planned["modes"]
  # Every clip, then the last one, then none -- the still stays in all three.
  assert modes["all"]["files"] == 3
  assert modes["latest"]["files"] == 2
  assert modes["none"]["files"] == 1
  assert modes["all"]["bytes"] > modes["latest"]["bytes"] > modes["none"]["bytes"] > 0


def test_a_task_with_no_media_still_has_a_price(records_root):
  planned = _plan(records_root)
  assert planned["runs"] == 2 and planned["clips"] == 0
  assert all(m["bytes"] == 0 for m in planned["modes"].values())


def test_writing_dates_the_file_and_keeps_it_with_the_task(tmp_path):
  target = source_report.write("<html></html>", tmp_path / "records" / "reports",
                               "Fake Task/With:odd chars", at=0)
  assert target.parent == tmp_path / "records" / "reports"
  assert target.name.startswith("Fake_Task_With_odd_chars-")
  assert target.suffix == ".html"
  assert target.read_text() == "<html></html>"


def test_a_page_is_told_from_something_else():
  assert source_report.looks_like_a_page(PAGE.encode())
  assert not source_report.looks_like_a_page(b'{"task": "x"}')
  assert not source_report.looks_like_a_page(b"")


# ── over HTTP ─────────────────────────────────────────────────────────────

def test_the_plan_route_answers_for_a_task(client, records_root):
  planned = client.get("/api/report/plan", params={"task": TASK}).json()
  assert planned["task"] == TASK and planned["runs"] == 2
  assert set(planned["modes"]) == set(source_report.MEDIA_MODES)
  assert planned["out_dir"] == str(records_root / "reports")


def test_saving_files_the_page_under_the_records_root_and_it_downloads(client, records_root):
  answer = client.post("/api/report/save", params={"task": TASK, "media": "latest"},
                       content=PAGE.encode(), headers={"content-type": "text/html"}).json()
  assert answer["ok"] is True and answer["runs"] == 2 and answer["media"] == "latest"
  path = Path(answer["path"])
  assert path.parent == records_root / "reports" and path.is_file()
  assert path.read_text() == PAGE
  assert answer["size"] == path.stat().st_size

  served = client.get(answer["url"])
  assert served.status_code == 200
  # As a download: served as HTML on this origin the page would run with the
  # studio's API in reach, which `/api/artifact` already refuses to do.
  assert "attachment" in served.headers.get("content-disposition", "")
  assert served.content == path.read_bytes()


def test_what_is_not_a_page_is_refused(client, records_root):
  refused = client.post("/api/report/save", params={"task": TASK},
                        content=b'{"not": "html"}', headers={"content-type": "text/html"})
  assert refused.status_code == 400
  assert not (records_root / "reports").exists(), "nothing was written"


def test_a_task_with_no_runs_is_refused_and_a_bad_mode_is_refused(client):
  assert client.post("/api/report/save", params={"task": "Nobody-Trained-This"},
                     content=PAGE.encode()).status_code == 404
  assert client.post("/api/report/save", params={"task": TASK, "media": "everything"},
                     content=PAGE.encode()).status_code == 400


# ── the client ────────────────────────────────────────────────────────────

def test_the_page_is_built_from_the_studios_own_panels():
  """The point of doing it in the browser: no second drawing of a run. The
  module must reach for the panels the layouts use, not describe them."""
  text = (JS / "report.js").read_text()
  for name in ("renderView", "recordBrief", "measuredCard", "feedbackCard",
               "metricCards", "clipsCard", "overviewView", "cardPane", "recipeView",
               "renderMarkdown"):
    assert re.search(rf"\b{name}\(", text), f"the report does not use {name}"
  # And the studio's stylesheets, verbatim, rather than a copy of some rules:
  # every one the app's page links, in the same order.
  page = (JS.parent / "index.html").read_text()
  linked = re.findall(r'<link rel="stylesheet" href="(/static/css/[^"]+)"', page)
  assert "/static/css/tokens.css" in linked and len(linked) > 1
  named = [href for href in re.findall(r'"(/static/css/[^"]+)"', text)]
  assert named == linked, "the report carries the stylesheets index.html links"


def test_the_file_carries_its_media_rather_than_pointing_home():
  text = (JS / "report.js").read_text()
  assert "readAsDataURL" in text, "media is embedded as data URIs"
  # Every kind of pointer back at the studio is swapped: pictures, posters,
  # the story previews, the run's player, and the strip's clips.
  for selector in ('img[src]', 'video[poster]', '.story-film video', 'video.clip',
                   '.strip.clips .thumb'):
    assert selector in text, f"nothing embeds {selector}"


def test_the_button_is_where_a_task_is_looked_at_as_a_whole():
  """The Records toolbar and the library card, and nowhere a *run* is looked at."""
  assert "reportControl(" in read_js("views/records.js")
  assert "reportControl(" in (JS / "library.js").read_text()
  main = read_js("app/actions-app.js")
  for action in ("openReport", "closeReport", "buildReport"):
    assert f"{action}:" in main, f"actions.{action} is not published"
