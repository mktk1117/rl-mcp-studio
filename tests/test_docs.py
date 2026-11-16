"""The Docs view's spine: which markdown belongs to a run, and what serves it.

The failure this is written against is the one the feature exists to fix —
a document an agent wrote that the app cannot see. So the fixtures write files
the way an agent would (a `Write` into a directory), and the assertions are
about *where* the studio looks rather than about how it renders.
"""

from __future__ import annotations

from pathlib import Path

from rlmcp_studio.sources import docs as source_docs
from rlmcp_studio.sources.watcher import _classify
from tests.client_source import read_css, read_js


def record_dir(store, record_id: str) -> Path:
  return Path(store.record_dir(store.get_record(record_id)))


# ── what belongs to a run ────────────────────────────────────────────────
def test_the_plan_and_report_beside_a_record_are_found(store):
  """rl-mcp writes both itself (`rlmcp.records.report`), so the commonest case
  is a store that already has them."""
  directory = record_dir(store, "002")
  (directory / "PLAN.md").write_text("# The plan\n\nA gate on chatter.\n")
  (directory / "REPORT.md").write_text("# What happened\n")

  found = source_docs.for_run(store.get_record("002"), store)
  run = next(g for g in found["groups"] if g["id"] == "run")

  assert {d["name"] for d in run["docs"]} == {"PLAN.md", "REPORT.md"}
  assert found["count"] == 2
  assert run["dir"] == str(directory)


def test_a_document_an_agent_invented_is_listed_with_them(store):
  """The point of the feature: not a fixed pair of filenames, but whatever
  markdown was written where the run's documents live."""
  directory = record_dir(store, "002")
  (directory / "analysis.md").write_text("# Why it plateaued\n")

  run = next(g for g in source_docs.for_run(store.get_record("002"), store)["groups"]
             if g["id"] == "run")
  assert [d["name"] for d in run["docs"]] == ["analysis.md"]


def test_a_document_in_the_session_counts_as_the_run_s(store, session_dir):
  """Record 002 names this session. A run whose record write is lagging, or
  one nobody registered, still writes into `<session>/artifacts` -- the same
  argument clips.py makes."""
  (session_dir / "artifacts" / "notes.md").write_text("# Mid-run\n")

  run = next(g for g in source_docs.for_run(store.get_record("002"), store)["groups"]
             if g["id"] == "run")
  assert [(d["name"], d["source"]) for d in run["docs"]] == [("notes.md", "session")]


def test_the_task_s_own_notes_are_a_separate_group(tmp_path, store):
  """`README.md` in the task repository and `REPORT.md` in the record are not
  two copies of anything, so they are not merged into one list."""
  (tmp_path / "README.md").write_text("# The task\n")
  (tmp_path / "docs").mkdir()
  (tmp_path / "docs" / "tuning.md").write_text("# Tuning\n")
  (tmp_path / "docs" / "notes.txt").write_text("not markdown")

  found = source_docs.for_run(store.get_record("002"), store, [tmp_path])
  task = next(g for g in found["groups"] if g["id"] == "task")

  assert {d["name"] for d in task["docs"]} == {"README.md", "tuning.md"}


def test_a_task_with_no_run_still_has_its_notes(tmp_path):
  """Build's case, and the one a records-shaped lister would have missed."""
  (tmp_path / "PLAN.md").write_text("# A task being written\n")
  found = source_docs.for_run(None, None, [tmp_path])

  assert found["count"] == 1
  assert [g["id"] for g in found["groups"]] == ["task"]


def test_the_newest_document_is_first(store):
  """What an agent just wrote is what you came to read."""
  directory = record_dir(store, "002")
  import os
  for name, when in (("old.md", 1_000_000), ("new.md", 2_000_000)):
    path = directory / name
    path.write_text("# x\n")
    os.utime(path, (when, when))

  run = next(g for g in source_docs.for_run(store.get_record("002"), store)["groups"]
             if g["id"] == "run")
  assert [d["name"] for d in run["docs"]] == ["new.md", "old.md"]


# ── the title ────────────────────────────────────────────────────────────
def test_the_title_is_the_first_heading(tmp_path):
  (tmp_path / "a.md").write_text("\n\n# Warm start, from 046\n\nprose\n")
  assert source_docs.describe(tmp_path / "a.md", "task")["title"] \
      == "Warm start, from 046"


def test_a_file_with_no_heading_gets_no_invented_title(tmp_path):
  """A filename is not a title, and putting one in a column read as "what this
  document says about itself" would be putting words in the writer's mouth."""
  (tmp_path / "warm-start.md").write_text("just some prose\n")
  assert source_docs.describe(tmp_path / "warm-start.md", "task")["title"] == ""


# ── reading one ──────────────────────────────────────────────────────────
def test_a_document_is_read_whole_and_says_when_it_is_not(tmp_path, monkeypatch):
  path = tmp_path / "big.md"
  path.write_text("x" * 5000)
  monkeypatch.setattr(source_docs, "MAX_BYTES", 100)

  answer = source_docs.read(path)
  assert answer["ok"] and len(answer["text"]) == 100 and answer["truncated"]


# ── the watcher ──────────────────────────────────────────────────────────
def test_a_markdown_write_is_an_event_the_client_can_hear():
  """Without this the view is correct and never updates: an agent writes the
  report, and the pane keeps showing the empty state until you click away and
  back."""
  assert _classify(Path("/records/runs/047-x/REPORT.md")) == "docs.changed"


def test_prose_in_the_artifacts_directory_is_not_mistaken_for_a_frame():
  """`artifacts/` is where frames and clips land, so the directory rule would
  have claimed a `.md` there and refreshed the clip strip instead."""
  assert _classify(Path("/logs/run/rlmcp/artifacts/notes.md")) == "docs.changed"
  assert _classify(Path("/logs/run/rlmcp/artifacts/shot_it10.png")) \
      == "artifacts.changed"


# ── the routes ───────────────────────────────────────────────────────────
def test_the_route_lists_a_run_s_documents(client, store):
  (record_dir(store, "002") / "REPORT.md").write_text("# What happened\n")
  answer = client.get("/api/docs", params={"record_id": "002"})

  assert answer.status_code == 200
  assert answer.json()["count"] == 1


def test_the_route_answers_without_a_run(client):
  """Build has no record, and asking for its notes must not be a 404."""
  assert client.get("/api/docs").status_code == 200


def test_a_record_that_does_not_exist_is_a_404(client):
  assert client.get("/api/docs", params={"record_id": "999"}).status_code == 404


def test_a_document_is_served_as_text(client, store):
  path = record_dir(store, "002") / "REPORT.md"
  path.write_text("# What happened\n\nIt held.\n")
  answer = client.get("/api/doc", params={"path": str(path)})

  assert answer.status_code == 200
  assert answer.json()["text"].startswith("# What happened")


def test_a_file_outside_every_root_is_refused(client):
  assert client.get("/api/doc", params={"path": "/etc/hosts"}).status_code in (403, 404)


def test_a_traversal_dressed_up_as_a_document_is_refused(client, records_root):
  sneaky = str(records_root / ".." / ".." / ".." / "etc" / "passwd")
  assert client.get("/api/doc", params={"path": sneaky}).status_code in (403, 404)


def test_something_that_is_not_markdown_is_not_served_here(client, frame):
  """The route reads text into a JSON field; a PNG through it would be a
  megabyte of replacement characters, and `/api/artifact` is what serves it."""
  assert client.get("/api/doc", params={"path": str(frame)}).status_code == 404


# ── how the view renders it ──────────────────────────────────────────────
#
# Read off the source, like the rest of the client checks: there is no
# JavaScript engine here, and `web/dev/modules.html` is the gate that runs it.

def test_the_scroller_and_the_reading_column_are_different_elements():
  """A single element that both scrolls and carries `max-width` puts its
  scrollbar at the right edge of the *text*, floating in the middle of a wide
  pane instead of on the pane's edge."""
  from pathlib import Path
  js = (Path(__file__).parent.parent / "rlmcp_studio" / "web" / "js" / "docs.js").read_text()
  css = read_css()

  assert 'class: "md-body prose"' in js, "the reading column is its own node"
  scroller = css.split(".md {", 1)[1].split("}", 1)[0]
  column = css.split(".md-body {", 1)[1].split("}", 1)[0]
  assert "overflow-y: auto" in scroller and "max-width" not in scroller
  assert "max-width" in column and "overflow" not in column


def test_a_column_of_numbers_is_right_aligned_without_being_asked():
  """Markdown makes you write `---:`, and nobody writing a run report at two
  in the morning does -- so a results table's decimal points do not line up,
  which is the one thing a results table is for. A column that is *not* all
  numbers must stay left: a run report's status column can end in a row like
  `held (at iteration 4999)`."""
  from pathlib import Path
  js = (Path(__file__).parent.parent / "rlmcp_studio" / "web" / "js" / "docs.js").read_text()
  rule = js.split("const NUMERIC = ", 1)[1].split("\n", 1)[0]

  import re
  pattern = re.compile(
      rule.strip().rstrip(";").strip("/").replace("\\\\", "\\").replace("$/", "$"))
  for cell in ("0.033", "173.8", "-0.15", "3.3%", "4.5x", "1,024", "0.02 m/s"):
    assert pattern.match(cell), f"{cell} should read as a measurement"
  for cell in ("held (at iteration 4999)", "feet_air_time", "yes"):
    assert not pattern.match(cell), f"{cell} is not a measurement"

  assert "numericColumns(rows, given)" in js, "the table has to use the rule"


def test_the_docs_pane_has_its_own_ground_in_both_schemes():
  """`--panel` is white in light and a step *up* from the ground in dark, so a
  code block built from it vanishes on a white page and reads correctly on a
  dark one. One token cannot serve both: "raised" means lighter in dark and
  darker in light. Hence the paper set, defined in both schemes — the rule
  tokens.css states in its own first paragraph."""
  from pathlib import Path
  tokens = (Path(__file__).parent.parent / "rlmcp_studio" / "web" / "css" / "tokens.css").read_text()
  app = read_css()

  dark = tokens.split(":root {", 1)[1].split("}", 1)[0]
  light = tokens.split(':root[data-scheme="light"] {', 1)[1].split("}", 1)[0]
  paper = {"--paper", "--paper-rail", "--paper-block", "--paper-head",
           "--paper-stripe"}

  for name in paper:
    assert f"{name}:" in dark, f"{name} has no dark value"
    assert f"{name}:" in light, f"{name} is only defined in one scheme"

  # And the Docs rules read them rather than the panel tokens.
  docs_css = app.split("/* ── docs:", 1)[1]
  # `.prose`, not `.md`: the block styles are shared with the agent panel,
  # which renders the same markdown from the same renderer.
  for rule in (".docpane {", ".doclist {", ".prose .md-code {",
               ".prose .md-table th {"):
    body = docs_css.split(rule, 1)[1].split("}", 1)[0]
    assert "--paper" in body, f"{rule} should sit on the paper set"


# ── the accent is a preference ───────────────────────────────────────────
def test_every_accent_is_declared_in_both_schemes():
  """`data-accent` on the root matching no rule is the silent failure: the app
  keeps the default while the picker shows a tick beside something else. The
  list `state.js` guards is the list `tokens.css` defines, and each one needs
  a dark *and* a light declaration or choosing it in one scheme does nothing."""
  import re
  from pathlib import Path
  web = Path(__file__).parent.parent / "rlmcp_studio" / "web"
  tokens = (web / "css" / "tokens.css").read_text()
  state = (web / "js" / "state.js").read_text()

  named = set(re.findall(r'"(\w+)"',
                         state.split("export const ACCENTS = [", 1)[1]
                              .split("]", 1)[0]))
  assert named, "the accent list should not be empty"

  for name in named:
    assert f':root[data-accent="{name}"]' in tokens, f"{name} has no dark palette"
    assert f':root[data-scheme="light"][data-accent="{name}"]' in tokens, \
        f"{name} has no light palette"
    # The swatch in the picker paints from the same declaration, so a hue
    # cannot be shown as one colour and applied as another.
    assert f".accent-{name}" in tokens, f"{name} has no swatch class"

  declared = set(re.findall(r':root\[data-accent="(\w+)"\]', tokens))
  assert declared == named, (
      f"tokens.css declares {sorted(declared)}, state.js offers {sorted(named)}")


def test_slate_is_what_an_unset_accent_resolves_to():
  """A page that has never been told an accent still has one, and it is the
  same one the picker ticks."""
  from pathlib import Path
  web = Path(__file__).parent.parent / "rlmcp_studio" / "web"
  tokens = (web / "css" / "tokens.css").read_text()
  state = (web / "js" / "state.js").read_text()

  base_dark = tokens.split(":root {", 1)[1].split("}", 1)[0]
  base_light = tokens.split(':root[data-scheme="light"] {', 1)[1].split("}", 1)[0]
  slate_dark = tokens.split(':root[data-accent="slate"]', 1)[1].split("}", 1)[0]
  slate_light = tokens.split(':root[data-scheme="light"] .accent-slate', 1)[1].split("}", 1)[0]

  for name in ("--accent:", "--accent-soft:", "--accent-dim:"):
    for base, slate in ((base_dark, slate_dark), (base_light, slate_light)):
      value = base.split(name, 1)[1].split(";", 1)[0].strip()
      assert value == slate.split(name, 1)[1].split(";", 1)[0].strip(), \
          f"the default {name} is not slate's"

  assert '"slate"' in state.split("localStorage.getItem(\"rlmcp.accent\")", 1)[1][:120], \
      "an unknown stored accent should fall back to slate"


def test_the_accent_is_saved_like_every_other_appearance_setting():
  """It has to follow *you*, not this browser -- which is what the settings
  file is for. Three places, and missing any one of them loses it: the
  mirror that adopts the file at boot, the setter that writes it, and the
  snapshot that seeds an empty file from this browser."""
  from pathlib import Path
  state = (Path(__file__).parent.parent / "rlmcp_studio" / "web" / "js" / "state.js").read_text()
  mirrored = state.split("const MIRRORED = {", 1)[1].split("\n};", 1)[0]
  current = state.split("function currentPreferences()", 1)[1].split("}", 1)[0]

  assert "accent:" in mirrored, "the settings file's accent is never adopted"
  assert "accent: state.accent" in current, "an empty file would not learn it"
  assert 'settingChanged("accent"' in state, "changing it never reaches the file"


def test_the_agent_renders_markdown_rather_than_printing_it():
  """A model writes markdown -- that is what they write -- and printed raw, a
  reply shows its source: `**bold**` with its asterisks, backticks around every
  identifier, list markers as literal dashes. It shares the Docs renderer,
  which builds nodes rather than markup, so a reply cannot execute anything it
  happens to contain."""
  agent = read_js("agent.js", "agent/*.js")
  css = read_css()

  assert "renderMarkdown" in agent, "the agent's replies are printed, not rendered"
  assert 'class: "m-bub prose"' in agent, "and they have to pick up the prose styles"

  # One renderer, one set of block styles, two surfaces.
  assert ".prose .md-p" in css and ".md .md-p" not in css, \
      "the block styles should belong to `prose`, not to the Docs pane"
  assert ".m-bub.prose .md-p" in css, \
      "a transcript column needs a tighter scale than a document page"


# ── how a parameter moved ────────────────────────────────────────────────
def test_the_parameter_history_is_rl_mcps_and_not_a_second_answer():
  """`rlmcp.records.params.build_history` lays this out, and its own docstring
  says the payload is "deliberately viewer-shaped". Deriving the offsets, the
  series or the index here would be a second answer to a question rl-mcp
  already answers -- and it would drop the `why` each live edit carries, which
  is the half of "what changed" no chart can draw."""
  from pathlib import Path
  source = (Path(__file__).parent.parent / "rlmcp_studio" / "sources"
            / "records.py").read_text()
  body = source.split("def param_history(", 1)[1].split("\n  def ", 1)[0]
  assert "build_history" in body, "the history has to come from rl-mcp"

  # The *code*, not the prose: the docstring explains what offsets are and
  # would fail a naive word search for them.
  import re as _re
  code = _re.sub(r'"""».*?"""'.replace("»", ""), "", body, flags=_re.S)
  code = "\n".join(line for line in code.splitlines()
                   if not line.lstrip().startswith("#"))
  assert "offset" not in code and "iteration" not in code, \
      "the studio should carry this payload, not recompute the layout"
  # A thin delegation: filter to the task, hand it to rl-mcp, return.
  statements = [l for l in code.splitlines() if l.strip()]
  assert len(statements) < 12, "this should be carrying, not computing"


def test_the_history_route_answers(client):
  answer = client.get("/api/params/history", params={"task": "Fake-Task-G1"})
  assert answer.status_code == 200
  body = answer.json()
  assert set(body) >= {"runs", "index", "span"}


def test_a_change_is_shown_with_the_reason_that_was_given():
  """The table says what a parameter is now. What it was, when it moved and
  why are three facts the app already holds, and a reader asking for the diff
  of a parameter needs them together."""
  views = read_js("views/params.js")
  changes = views.split("function historyChanges(", 1)[1].split("\n}", 1)[0]
  assert "p.why" in changes, "the reason is the part a chart cannot show"
  assert "source !== \"launch\"" in changes, \
      "a launch value is not a change made to a running run"


# ── re-reading one, without reloading the app ─────────────────────────────
#
# The watcher's roots are the records and the sessions. The *task repository*
# is not among them, and it is where an agent's `Write NOTES.md` lands -- so
# `docs.changed` never fires for one, and without these the only way to see it
# would be reloading the whole app by hand. Two buttons, because they answer two questions.

DOCS_JS = (Path(__file__).parent.parent / "rlmcp_studio" / "web" / "js" / "docs.js").read_text()


def test_one_document_can_be_read_again_without_rescanning_for_others():
  """Re-reading the open file is one `stat` and one `read`; looking for new
  documents walks three directories. Pressing the wrong button and getting the
  other's work done is worse than having two."""
  reload_fn = DOCS_JS.split("export function reloadDoc(", 1)[1].split("\n}", 1)[0]

  assert "docStamp: null" in reload_fn
  assert "docsStamp" not in reload_fn, \
      "re-reading one document must not discard the listing"


def test_rescanning_re_reads_the_open_document_too():
  """An agent that has just written a new document has usually rewritten the
  one you are looking at, which is what `refreshDocs` already assumed."""
  rescan = DOCS_JS.split("export function rescanDocs(", 1)[1].split("\n}", 1)[0]

  assert "refreshDocs()" in rescan


def test_the_reader_shows_the_age_that_proves_the_reload_worked():
  """Nothing on screen changes when you re-read a file that has not changed --
  the pane keeps what it has rather than blinking, deliberately. Without a
  stamp that moves, the button looks broken every time it works."""
  header = DOCS_JS.split('class: "docpane-h"', 1)[1].split("s.docRaw\n", 1)[0]

  assert "written(doc.at)" in header
  assert "reloadDoc()" in header


def test_the_listing_says_how_many_it_found():
  """A rescan that finds nothing new has to be distinguishable from a rescan
  that did not run."""
  head = DOCS_JS.split("function listHead(", 1)[1].split("\n}\n", 1)[0]

  assert "payload.count" in head
  assert '"Rescan"' in head


def test_the_reload_verb_is_a_glyph_with_a_name_on_it():
  """The document header carries a filename, a chip, an age and three verbs,
  and in the focus layout's narrow pane that wraps. The word moves to the
  label a screen reader was reading anyway -- a bare glyph with no accessible
  name would be a button nobody can name."""
  header = DOCS_JS.split('class: "docpane-h"', 1)[1].split("s.docRaw\n", 1)[0]

  assert '"aria-label": "reload this document"' in header
  assert "\\u21BB" in header, "the reload glyph"


def test_a_clip_in_a_document_is_the_same_player_on_every_paint():
  """A `<video>` keeps its frame and whether it is playing on the node, and
  the reconciler keeps a node only while it matches by position. The text
  changing above a clip, the pane saying "opening…" while the file is re-read,
  a layout switch -- each built a new player, which starts from zero. Takahiro
  could not watch a clip in the Docs view: it kept jumping back to the start.

  So the panes that repaint hand the renderer a Map to reuse players from, and
  the export -- which must not take the player out of the pane -- does not."""
  video = DOCS_JS.split("function video(", 1)[1].split("\n}", 1)[0]
  assert "players.get(key)" in video, "a clip already on screen is handed back"
  assert '"key"' in video, "keyed, so another clip is never morphed into its place"
  assert "play()" in video, "a player the browser paused while out of the page resumes"

  pane = DOCS_JS.split("function docPane(", 1)[1].split("\n}", 1)[0]
  assert "players: docPlayers" in pane
  overview = (Path(__file__).parent.parent / "rlmcp_studio" / "web" / "js" / "overview.js").read_text()
  assert "players: reportPlayers" in overview
  report = (Path(__file__).parent.parent / "rlmcp_studio" / "web" / "js" / "report.js").read_text()
  assert "players:" not in report, "the export builds its own players"
