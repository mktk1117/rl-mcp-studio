"""The task library: a card per task, and the index of them.

Everything here is built from records rl-mcp wrote, through rl-mcp's own store,
so a change in what a record *is* fails these rather than the browser.
"""

from __future__ import annotations

import re
from pathlib import Path

from rlmcp.records import Feedback

from rlmcp_studio.sources import library as source_library
from rlmcp_studio.sources.records import Library
from tests.client_source import read_css, read_js

JS = Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web" / "js"
WEB = Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web"

TASK = "Fake-Task-G1"


def cards_for(root: Path, task: str = TASK, **kw):
  """The card for one task, out of one records root."""
  sources = Library([root]).sources
  return source_library.card(task, source_library.collect(sources, task), **kw)


def test_a_task_with_no_runs_still_has_a_card(records_root):
  """A task exists because a package registers it; runs are what happened to
  it. The task being built has none by definition, and a blank
  page there would say it does not exist."""
  card = cards_for(records_root, "Task-Nobody-Has-Trained",
                   row={"task": "Task-Nobody-Has-Trained", "registered": True,
                        "package": "some_pkg.tasks.thing"},
                   blurb="walk across rough ground")

  assert card["task"] == "Task-Nobody-Has-Trained"
  assert card["registered"] is True
  assert card["package"] == "some_pkg.tasks.thing"
  assert card["blurb"] == "walk across rough ground"
  assert card["history"]["runs"] == 0
  assert card["findings"] == [] and card["pitfalls"] == []


def test_every_finding_and_pitfall_cites_a_run(records_root, store):
  """The rule that separates a library from a pile of opinions. A claim
  with no run behind it cannot be checked by the person who reads it next."""
  store.new_record("wrist_drift", task=TASK, verdict="falsified", parent="001",
                   hypothesis="a stiffer wrist holds the pose",
                   outcome="it did not; the drift is in the shoulder")
  store.add_feedback("002", Feedback(text="stop chasing the wrist", kind="reject"))

  card = cards_for(records_root)
  assert card["findings"] and card["pitfalls"]
  for entry in card["findings"] + card["pitfalls"] + card["open_questions"]:
    assert entry["run"], f"an entry with no run cited: {entry}"
    assert store.get_record(entry["run"]) is not None


def test_a_run_that_killed_its_hypothesis_is_a_finding(records_root, store):
  """`falsified` is a good outcome in rl-mcp's own vocabulary, and it is what
  saves the next person the week -- so it belongs in the findings, marked as
  what it is rather than folded in with the confirmations."""
  store.new_record("stiff_wrist", task=TASK, verdict="falsified",
                   outcome="the wrist was never the binding constraint")

  card = cards_for(records_root)
  stances = {e["run"]: e["stance"] for e in card["findings"]}
  ruled = [e for e in card["findings"] if e["stance"] == "ruled out"]
  assert ruled and "binding constraint" in ruled[0]["text"]
  assert stances["001"] == "confirmed", "a validated run is still a confirmation"


def test_a_remark_that_says_stop_becomes_a_pitfall(records_root, store):
  """The ledger is the only place a sentence like this survives. `reject`,
  `correct` and `constrain` are warnings; `approve` and `observe` are not."""
  store.add_feedback("002", Feedback(text="never train this on flat first",
                                     kind="constrain"))
  store.add_feedback("002", Feedback(text="looking good", kind="approve"))
  store.add_feedback("002", Feedback(text="it wobbles a little", kind="observe"))

  said = [p["text"] for p in cards_for(records_root)["pitfalls"]]
  assert "never train this on flat first" in said
  assert "looking good" not in said and "it wobbles a little" not in said


def test_an_instruction_nobody_answered_is_an_open_question(records_root, store):
  """An open question is a result, and hiding it is how a library rots.
  Answering it takes it off the list, which is what makes the list worth
  reading a second time."""
  store.add_feedback("001", Feedback(text="check the contact schedule", kind="steer"))
  asked = [q["text"] for q in cards_for(records_root)["open_questions"]]
  assert "check the contact schedule" in asked

  store.answer_feedback("001", 0, "checked; it was already right")
  answered = [q["text"] for q in cards_for(records_root)["open_questions"]]
  assert "check the contact schedule" not in answered


def test_a_run_still_training_is_an_open_question(records_root):
  """`002` is `running` in the fixture store: the task has not settled it."""
  asked = cards_for(records_root)["open_questions"]
  assert any(q["run"] == "002" for q in asked)


def test_the_card_does_not_pretend_to_be_a_published_one(records_root):
  """A published card is immutable, versioned and written by rl-mcp's card
  skill from the whole graph. This one is assembled from what is already on disk, and
  a card that did not say so would be the studio quietly claiming a capability
  it does not have."""
  published = cards_for(records_root)["published"]
  assert published["exists"] is False
  assert "rl-mcp" in published["reason"]
  # And where one would live, when the studio has been told: `$RLMCP_LIBRARY`.
  # Reported, never scanned -- nothing writes that directory yet.
  assert published["roots"] == []
  assert cards_for(records_root, library_roots=["/tmp/cards"])[
      "published"]["roots"] == ["/tmp/cards"]


def test_the_reading_list_puts_the_task_notes_before_the_run_write_ups(records_root):
  """A note about the task describes the problem; `REPORT.md` for run 047
  assumes you already know it."""
  docs = {"groups": [
      {"id": "run", "label": "047", "docs": [{"path": "/x/REPORT.md", "name": "REPORT.md"}]},
      {"id": "task", "label": "tasks", "docs": [{"path": "/y/NOTES.md", "name": "NOTES.md"}]},
  ]}
  reading = cards_for(records_root, docs=docs)["read_first"]
  assert [d["name"] for d in reading] == ["NOTES.md", "REPORT.md"]


def test_the_history_counts_how_the_runs_ended(records_root):
  history = cards_for(records_root)["history"]
  assert history["runs"] == 2                    # the third record is another task
  assert history["verdicts"] == {"validated": 1, "running": 1}
  assert history["open"] == 1
  assert history["latest"]["id"] == "002"


def test_the_index_holds_a_row_for_a_task_that_has_never_trained(records_root):
  """The index is built from the switcher's merged list, so a registered task
  with no records is a row here too -- which is the case the library exists for
  as much as any: a new task starts by reading the last one's pitfalls."""
  rows = source_library.index(
      [{"task": TASK, "recorded": True, "live": 0},
       {"task": "Brand-New-Task", "registered": True, "live": 0}],
      source_library.by_task(Library([records_root]).sources))

  by_name = {r["task"]: r for r in rows}
  assert by_name["Brand-New-Task"]["runs"] == 0
  assert by_name[TASK]["runs"] == 2
  assert by_name[TASK]["line"], "a row says what the newest run of it said"


def test_the_index_is_ordered_by_when_a_task_was_last_worked_on(records_root, store):
  """The shelf is how you get back to what you were doing. The registry's own
  order is alphabetical, which answers no question anybody has: the task with a
  trainer on it right now sat wherever its name put it."""
  store.new_record("later_run", task="Second-Task-G1", verdict="running")

  rows = source_library.index(
      [{"task": TASK, "recorded": True, "live": 0},
       {"task": "Brand-New-Task", "registered": True, "live": 0},
       {"task": "Second-Task-G1", "recorded": True, "live": 0},
       {"task": "Another-New-Task", "registered": True, "live": 0}],
      source_library.by_task(Library([records_root]).sources))

  names = [r["task"] for r in rows]
  assert names[0] == "Second-Task-G1", "the task last written to leads"
  assert names.index(TASK) == 1
  # Nothing to sort the never-trained ones by, so they keep the order the
  # switcher gave them rather than shuffling on every refresh.
  assert names[2:] == ["Brand-New-Task", "Another-New-Task"]


def test_a_task_being_written_is_dated_by_its_own_files(records_root):
  """A task that has never trained has no record to date it -- and it is
  exactly the task somebody is in the middle of writing, so the bottom of the
  shelf is the wrong place for it. Its files are the only clock it has."""
  clocks = {"Being-Written": 4_000_000_000.0, "Old-Package": 1_000.0}
  rows = source_library.index(
      [{"task": TASK, "recorded": True, "live": 0},
       {"task": "Old-Package", "registered": True, "live": 0},
       {"task": "Being-Written", "registered": True, "live": 0}],
      source_library.by_task(Library([records_root]).sources),
      code_at=lambda task: clocks.get(task, 0.0))

  names = [r["task"] for r in rows]
  assert names[0] == "Being-Written", "edited this afternoon, ahead of every record"
  assert names.index(TASK) < names.index("Old-Package")
  by_name = {r["task"]: r for r in rows}
  # Said on the row, so a task high on the shelf with no runs explains itself.
  assert by_name["Being-Written"]["edited"]
  assert not by_name[TASK]["edited"], "a task with runs is dated by its runs"


def test_the_files_of_a_shared_package_do_not_reorder_the_tasks_that_ran():
  """One package can register twenty tasks out of one directory.
  Folding its mtime into tasks that have runs would lift all twenty every time
  anybody touched any of them -- the alphabet again, in a worse disguise."""
  js = (Path(__file__).resolve().parent.parent
        / "rlmcp_studio" / "sources" / "library.py").read_text()
  fallback = js.split("edited = ", 1)[1].split("\n", 1)[0]
  assert "if updated" in fallback, "the code clock is a fallback, never a maximum"


def test_a_row_says_when_the_task_was_last_touched_to_the_second(records_root):
  """`date` is a *day*: four runs of four tasks on one afternoon all compare
  equal, and that afternoon is exactly the one worth ordering."""
  rows = source_library.index([{"task": TASK, "recorded": True, "live": 0}],
                              source_library.by_task(Library([records_root]).sources))
  assert rows[0]["updated"] > 0
  assert rows[0]["updated"] % 1, "an epoch, not a date rounded to the day"


def test_a_card_carries_the_clips_the_records_kept(records_root, store, make_frame,
                                                   tmp_path):
  """What is on the record, not what is in `logs/`: a clip in the logs goes
  when the logs are cleaned, and a card pointing at one would rot quietly."""
  clip = make_frame(tmp_path / "clip.png")
  key = store.media.put("002", str(clip), "the last one", kind="videos")
  record = store.get_record("002")
  record.assets = {"videos": [[key, "the last one"]]}
  store.put_record(record)

  media = cards_for(records_root)["media"]
  assert media and media[0]["run"] == "002" and media[0]["caption"] == "the last one"


def test_the_clip_a_card_leads_with_is_the_last_one_kept(records_root, store,
                                                         make_frame, tmp_path):
  """A run that filmed its own progress keeps `it000000` first -- the untrained
  robot falling over, which is the worst possible clip to open a card about the
  task with. The newest promotion leads."""
  first = store.media.put("002", str(make_frame(tmp_path / "it0.png")), kind="videos")
  last = store.media.put("002", str(make_frame(tmp_path / "final.png")), kind="videos")
  record = store.get_record("002")
  record.assets = {"videos": [[first, "iteration 0"], [last, "the final clip"]]}
  store.put_record(record)

  assert cards_for(records_root)["media"][0]["caption"] == "the final clip"


# ── the routes ───────────────────────────────────────────────────────────

def test_the_library_lists_the_tasks_the_studio_can_see(client):
  answer = client.get("/api/library").json()
  tasks = {row["task"]: row for row in answer["tasks"]}
  assert TASK in tasks and "Second-Task-G1" in tasks
  assert tasks[TASK]["runs"] == 2


def test_a_card_can_be_asked_for_by_task(client):
  card = client.get("/api/library/card", params={"task": TASK}).json()
  assert card["task"] == TASK
  assert card["history"]["runs"] == 2
  assert "groups" not in card, "the reading list is a list, not the docs payload"


def test_a_card_for_a_task_nothing_knows_about_is_empty_rather_than_an_error(client):
  """The switcher can name a task the records have never heard of, and asking
  for its card must answer the honest empty rather than a 404 the view would
  have to render as a failure."""
  answer = client.get("/api/library/card", params={"task": "Nothing-Here"})
  assert answer.status_code == 200
  assert answer.json()["history"]["runs"] == 0


# ── the client half ──────────────────────────────────────────────────────

def test_the_library_is_reachable_as_a_view():
  """Four hand-written lists have to agree or the tab points at nothing: the
  registry, the ids the store accepts, the layout walker and the syntax gate.
  The generic tests in `test_client_modules.py` check three of those pairwise;
  this one says the library is in all of them."""
  registry = read_js("views.js").split("export const VIEWS = [", 1)[1]
  assert 'id: "library"' in registry.split("\n];", 1)[0]
  assert '"library"' in (JS / "state.js").read_text().split("VIEW_IDS = new Set(", 1)[1]
  assert '"library"' in (WEB / "dev" / "checks.html").read_text()
  assert "library.js" in (WEB / "dev" / "modules.html").read_text()


def test_the_library_renders_without_a_run_selected():
  """A task with no runs is exactly the task whose card you want, so the
  library has to be in the set the layouts render with nothing selected --
  otherwise it draws "no run selected" over the one view that does not need
  one."""
  views = read_js("views.js")
  task_views = views.split("export const TASK_VIEWS = new Set(", 1)[1].split(")", 1)[0]
  assert '"library"' in task_views


def test_the_library_fills_the_stage():
  """`.b-scroll` is a two-column grid of `max-content` rows, so `height: 100%`
  on a view inside it resolves against a row sized by its own content. Left out
  of `FULL_HEIGHT`, the library laid out at ten thousand pixels in the left
  column with the right one empty: neither pane scrolled inside itself, and
  every repaint of a live run relaid the whole of it under the pointer."""
  views = read_js("views.js")
  full = views.split("export const FULL_HEIGHT = new Set(", 1)[1].split(")", 1)[0]
  assert '"library"' in full


def test_a_record_changing_does_not_blank_the_library():
  """Anything writing to the records moves this view's stamp, and clearing the
  payload as the next one is asked for dropped both panes to "reading…" and
  took the scroll position of each with it. With a live run that is a page that
  blinks at you while you try to read it."""
  js = (JS / "library.js").read_text()
  refresh = js.split("export function refreshLibrary() {", 1)[1].split("}", 1)[0]
  assert "null" not in refresh, "a refresh must not clear what is on screen"
  assert "libNonce" in refresh


def test_the_shelf_filters_as_a_subsequence_rather_than_a_substring():
  """Task names here are four or five hyphenated words, and what anybody
  remembers about one is rarely a contiguous run of it."""
  js = (JS / "library.js").read_text()
  assert "export function fuzzy" in js
  assert re.search(r"\.split\(/\\s\+/\)", js), "space-separated terms, as fzf has"


def test_the_card_is_not_squeezed_by_the_shelf():
  """Looked at rather than asserted: in Workbench with the agent panel open the
  stage is about 500px, and a fixed 288px shelf would leave the card 200 — the
  task title wraps over two lines and the records root breaks mid-word. The
  thing you picked is what you came to read, so the shelf is the half that
  gives way."""
  css = read_css()
  for view in (".lib {", ".docs {"):
    rule = css.split(view, 1)[1].split("}", 1)[0]
    assert "clamp(150px, 32%, 288px)" in rule, f"{view} still has a fixed shelf"

  # A path has joints; breaking it mid-word is not "wrapping" it.
  value = css.split(".lib-id dd {", 1)[1].split("}", 1)[0]
  assert "text-overflow: ellipsis" in value and "overflow-wrap: anywhere" not in value

  js = (Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web" / "js" / "library.js").read_text()
  assert "title: value" in js, "and the whole of it is in the tooltip"


def test_the_shelf_can_be_walked_from_the_keyboard():
  """Enter opened the top match and nothing else, so the second-best match --
  which for a query like `g1` is most of a long shelf -- could only be
  reached by letting go of the keyboard and pointing at it. The arrows move a
  pick, Enter takes it, and the pick is *clamped* against the list on screen,
  because the list it indexes into shrinks as you type."""
  js = (JS / "library.js").read_text()
  assert "libPicked" in (JS / "state.js").read_text(), "the pick lives in the store"
  keys = js.split('on(box, "keydown"', 1)[1].split("\n  return box;", 1)[0]
  assert '"ArrowDown"' in keys and '"ArrowUp"' in keys
  assert "e.preventDefault()" in keys, "or every arrow moves the caret instead"
  clamp = js.split("function pickOf(", 1)[1].split("\n}", 1)[0]
  assert "Math.min(picked, shown.length - 1)" in clamp


def test_the_pick_is_read_from_the_store_and_not_from_the_render():
  """The handler on the box was built by one render and answers keys pressed
  after several more. Two arrows inside one repaint read the same stale index
  and computed the same next row, so a held-down arrow walked half as far as it
  was pressed."""
  js = (JS / "library.js").read_text()
  keys = js.split('on(box, "keydown"', 1)[1].split("\n  return box;", 1)[0]
  assert "pickOf(state, shown)" in keys


def test_enter_opens_what_is_in_the_box_rather_than_what_was_last_drawn():
  """`typeLibFilter` holds the query out of the store for 80ms so the shelf is
  not rebuilt on every keystroke. Enter inside that window read the shelf from
  before the last letters landed: typing `cartpole` fast and pressing Enter
  opened the top of the *unfiltered* shelf -- the most recently worked-on task,
  which is the one thing nobody typing a name could have meant."""
  js = (JS / "library.js").read_text()
  keys = js.split('on(box, "keydown"', 1)[1].split("\n  return box;", 1)[0]
  assert "narrow(rows, query)" in keys, "re-narrowed from the box's own value"
  assert "e.currentTarget.value" in keys


def test_the_row_you_are_on_is_brought_back_onto_the_shelf():
  """A task is picked in four places -- the shelf, the switcher, Build, and a
  citation on a card -- and in three of them the shelf was left marking a row
  twenty above the scrollport. `nearest`, so a row already on screen does not
  move; once per target, so scrolling away from your own selection sticks; and
  on a timeout rather than a frame, because a hidden tab is handed no frames
  and would come back marking a row that is not on the shelf."""
  js = (JS / "library.js").read_text()
  body = js.split("function keepInView(", 1)[1].split("\n}", 1)[0]
  assert "scrollListTo(" in body, "the shelf scrolls, and nothing beside it does"
  assert "requestAnimationFrame" not in body and "setTimeout(" in body
  assert "want === inView" in body, "scrolled once per target, not once per paint"


def test_switching_task_keeps_the_card_frame():
  """The one moment this pane cannot keep what it had: the previous card
  belongs to another task. Collapsing the whole pane to one grey line is the
  shelf's own blink in the other half of the view, and reads the same way -- as
  a panel that broke -- so it keeps the name you just asked for, in the place
  the name goes."""
  js = (JS / "library.js").read_text()
  pane = js.split("function cardPane(", 1)[1].split("\n  const history", 1)[0]
  assert 'el("h2", { text: s.task })' in pane
  assert '"reading the card…"' not in pane


def test_the_keyboard_mark_does_not_move_the_row():
  """Two marks answering two questions: `on` is the task you have open,
  `picked` is where Enter would take you. A real border on the pick would move
  every row by a pixel the moment the keyboard touched the shelf, which is
  exactly the jitter a shelf must not have."""
  css = read_css()
  rule = css.split(".lib-row.picked {", 1)[1].split("}", 1)[0]
  assert "inset" in rule and "border:" not in rule
