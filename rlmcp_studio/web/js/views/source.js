// Code: the task package on disk, and what changed against another run.

import { el, chip, fmtVal, on, add } from "../ui.js";
import { api } from "../api.js";
import * as editor from "../editor.js";
import { placed, sideVar } from "../resize.js";
import { set, loadOnce } from "../state.js";
import { notYet } from "../components.js";
import { filePicker } from "../filetree.js";
import { ctx } from "./shared.js";

// ── diff ────────────────────────────────────────────────────────────────
function againstPicker(payload) {
  const runs = (ctx.state.runs || []).filter(r => r.id !== ctx.state.runId);
  if (!runs.length) return null;
  const chosen = ctx.state.against || "";
  // The run's own parent, not `payload.against` -- that is whatever is being
  // compared *now*, so once you choose another run the default option would
  // relabel itself with that run and the real parent would be nowhere on
  // screen.
  const mine = (ctx.state.runs || []).find(r => r.id === ctx.state.runId);
  const parent = (mine && mine.parent) || (!chosen ? payload.against : "") || "";

  const select = el("select", { class: "fb-kind" },
    el("option", { value: "", selected: !chosen },
       parent ? `parent · ${parent}` : "parent"),
    runs.map(r => el("option", { value: r.id, selected: chosen === r.id },
                     `${r.id} · ${r.slug}`)));
  on(select, "change", (e) => set({ against: e.currentTarget.value }));

  return el("div", { class: "diff-against" },
    el("span", { class: "eyebrow", text: "compare with" }),
    select,
    chosen ? el("button", { class: "act", onclick: () => set({ against: "" }) },
                "back to the parent") : null);
}

function configDiff(payload) {
  const d = payload.config || { changed: [], added: [], removed: [] };
  const total = d.changed.length + d.added.length + d.removed.length;
  // The fields the two snapshots cover between them. `d.total` is the *other*
  // run's field count, which reads as "64 of 0 differ" on a run whose parent
  // published no config at all.
  const compared = d.compared === undefined ? d.total : d.compared;
  const moved = d.moved_after_launch || [];
  const card = el("div", { class: "card" },
    el("div", { class: "card-h" },
      // Named for its scope, because the count is only about launch. "0 of 67
      // differ" over a record saying a weight moved is two true halves that
      // read as a contradiction, and the fix is for the count to say what it
      // counted.
      el("h3", { text: "Config at launch" }),
      chip(`vs ${payload.against}`),
      el("span", { class: "sp" }),
      el("span", { class: "eyebrow", text: compared
        ? `${total} of ${compared} launch field${compared === 1 ? "" : "s"} differ`
        : "no launch config recorded" })));

  const body = el("div", { class: "card-b" });
  // One side having recorded nothing is not the same as the two sides
  // agreeing, and a count on its own cannot tell them apart.
  if (compared && (!d.fields_here || !d.fields_there)) {
    body.append(el("div", { class: "caveat", text: d.fields_here
      ? `${payload.against} snapshotted no config, so these ${d.fields_here} fields are `
        + `listed as new rather than as a difference from it.`
      : `This run snapshotted no config, so every field below is ${payload.against}'s.` }));
  }
  if (!total) {
    // Three different nothings, and conflating them would mislead: the two
    // runs launched the same, nobody wrote a config down, or they launched the
    // same and what happened afterwards could not be read.
    body.append(el("div", { class: "empty", text: !compared
      ? "neither record snapshotted its config, so there is nothing to compare"
      : `both runs launched with the same values across all ${compared} recorded fields`
        + (moved.length ? "" : d.runtime_known
             ? ", and neither moved one while it trained"
             : "; whether either moved one while it trained is not recorded") }));
  }
  // The join between this layer and the runtime one, said last so it lands on
  // whatever the count above claimed. Without it "the same values" is read as
  // a statement about the two runs, when it is a statement about the two
  // snapshots taken before either of them started.
  if (moved.length) {
    body.append(el("div", { class: "caveat", text:
      `${moved.length} of the ${compared} fields compared here did not stay at `
      + `the launch value: `
      + `${moved.join(", ")}. Which run moved ${moved.length === 1 ? "it" : "them"}, `
      + `when and why is in “What was done to the run”.` }));
  }
  for (const row of d.changed) {
    body.append(el("div", { class: "dline" },
      el("span", { class: "dk", text: row.key }),
      el("span", { class: "dv" },
        el("span", { class: "old", text: fmtVal(row.old) }),
        el("span", { class: "new", text: fmtVal(row.new) }))));
  }
  for (const row of d.added) {
    body.append(el("div", { class: "dline added" },
      el("span", { class: "dk", text: row.key }),
      el("span", { class: "dv" }, el("span", { class: "new", text: `+ ${fmtVal(row.new)}` }))));
  }
  for (const row of d.removed) {
    body.append(el("div", { class: "dline removed" },
      el("span", { class: "dk", text: row.key }),
      el("span", { class: "dv" }, el("span", { class: "old", text: `− ${fmtVal(row.old)}` }))));
  }
  card.append(body);
  return card;
}
function patchFile(file, open, badge = null) {
  return el("details", { class: `patch-file${file.trivial ? " trivial" : ""}`, open },
    el("summary", {},
      el("span", { class: "pf-path", text: file.path }),
      file.trivial ? chip("whitespace") : null,
      el("span", { class: "pf-stat" },
        el("b", { class: "plus", text: `+${file.added}` }),
        el("b", { class: "minus", text: `−${file.removed}` })),
      badge),
    el("pre", { class: "patch" },
      file.lines.map(line => el("span", { class: `pl ${patchClass(line)}`, text: line }))));
}

// The third layer. Config says what the run started from and code says what it
// ran; this says what was done to it while it trained — which is usually the
// half that explains the other two.
function interventionList(payload) {
  const both = payload.interventions || {};
  const mine = both.this || {};
  const theirs = both.against || {};

  // A note or a piece of feedback is filed under rlmcp's `said` layer: a
  // judgement written down, not a change. Chipping the raw item count would
  // announce a run with one parameter edit and one frame annotation as two
  // edits. Counted apart, and both said.
  const changes = mine.changes === undefined
    ? (mine.items || []).length : mine.changes;
  const remarks = mine.remarks || 0;
  const card = el("div", { class: "card" },
    el("div", { class: "card-h" },
      el("h3", { text: "What was done to the run" }),
      el("span", { class: "sp" }),
      el("span", { class: "eyebrow", text: mine.ok
        ? `${changes || "no"} change${changes === 1 ? "" : "s"} after launch`
          + (remarks ? ` · ${remarks} remark${remarks === 1 ? "" : "s"}` : "")
        : "not recorded" })));

  const body = el("div", { class: "card-b" });
  if (!mine.ok) {
    body.append(el("div", { class: "notyet" },
      el("b", { text: "No intervention history" }),
      mine.reason || "this run left no event log"));
  } else if (!mine.items.length) {
    // Worth saying plainly: a run nobody touched is a fact about the run.
    body.append(el("div", { class: "empty", text: "nothing was changed mid-run" }));
  } else {
    body.append(interventionRows(mine.items));
  }

  if (theirs.ok && theirs.items.length) {
    body.append(el("div", { class: "eyebrow", style: { padding: "12px 0 4px" },
                            text: `${payload.against} — for comparison` }));
    body.append(interventionRows(theirs.items, true));
  }
  card.append(body);
  return card;
}

function interventionRows(items, muted = false) {
  const list = el("div", { class: `interv${muted ? " muted" : ""}` });
  for (const item of items) {
    list.append(el("div", { class: "iv" },
      el("span", { class: "iv-it", text: `it ${Number(item.iteration).toLocaleString("en-US")}` }),
      el("span", { class: `iv-layer iv-${item.layer}`, text: item.layer }),
      el("span", { class: "iv-what", text: item.what }),
      // The reason is the point of the row; without it this is a diff of trivia.
      item.why ? el("span", { class: "iv-why", text: item.why }) : null));
  }
  return list;
}

function worktreeDiff(payload) {
  const wt = payload.worktree || {};
  const head = payload.head || {};
  const showAll = !!ctx.state.diffShowAll;

  const files = wt.files || [];
  const relevant = files.filter(f => f.relevant);
  const shown = showAll ? files : relevant;
  const hidden = files.length - shown.length;

  const card = el("div", { class: "card" },
    el("div", { class: "card-h" },
      // Not the code layer of the comparison -- that is a diff between two
      // launch snapshots and lives on the file it changed. This is the
      // checkout in front of you, which is a different question and must not
      // be titled as if it were the same one.
      el("h3", { text: "Code in this checkout now" }),
      head.ok ? chip(`${head.commit} · ${head.branch}`) : null,
      head.dirty_files ? chip(`${head.dirty_files} modified`) : null,
      el("span", { class: "sp" }),
      hidden > 0 || showAll
        ? el("button", { class: "act",
            onclick: () => set({ diffShowAll: !showAll }) },
            showAll ? "Only relevant" : `Show all (${files.length})`)
        : null));

  const body = el("div", { class: "card-b" });
  if (payload.snapshot_note) {
    body.append(el("div", { class: "caveat", text: payload.snapshot_note }));
  }

  if (!wt.ok) {
    // Not the bare reason string as the whole body -- an absolute path and
    // "is not a git repository", centred and unstyled, says nothing about
    // what it is about or what to do.
    body.append(notYet("No working-tree diff",
      wt.reason || "the task package could not be read as a git repository.",
      ""));
  } else if (wt.empty) {
    body.append(el("div", { class: "empty", text: "working tree is clean" }));
  } else if (!shown.length) {
    // The distinction matters: nothing changed in the task, versus nothing
    // changed anywhere.
    body.append(el("div", { class: "empty",
      text: files.length
        ? `nothing in the task package changed — ${files.length} other file(s) did`
        : "no file-level changes" }));
  } else {
    for (const file of shown) {
      body.append(patchFile(file, shown.length <= 2,
                            file.relevant ? null : chip("other")));
    }
    if (!showAll && hidden > 0) {
      body.append(el("div", { class: "eyebrow", style: { paddingTop: "8px" },
        text: `${hidden} file(s) hidden — records, logs and docs are not part of what the policy did` }));
    }
  }
  card.append(body);
  return card;
}

function patchClass(line) {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("diff ") || line.startsWith("index ")) return "meta";
  return "";
}

// ── source ──────────────────────────────────────────────────────────────
//
// Navigation matters more than it looks: a task package is a thousand files
// and you want four of them. So the tree collapses, filters, and offers the
// concepts a run is actually about — rewards, actions, config — matched on the
// shape of names rather than on a list of paths, so another task's layout
// still works.

export function sourceView() {
  loadOnce("source", "tree", () => api.sourceTree());
  const payload = ctx.state.source;
  if (!payload) return el("div", { class: "empty", text: "reading the package…" });
  if (payload.error || !payload.entries || !payload.entries.length) {
    return el("div", { class: "notyet" },
      el("b", { text: "No source found" }),
      payload.error || payload.reason || "nothing readable beside the records.");
  }
  return el("div", { class: "sourceview" },
    changedStrip(),
    changedDetail(),
    el("div", { class: "files", style: { "--side": sideVar("tree", ctx.state.widths) } },
      filePicker(payload, ctx.actions), filePane(payload),
      placed("tree", { side: "left", widths: ctx.state.widths, actions: ctx.actions })));
}

// ── what changed, in one line ───────────────────────────────────────────
//
// Stacked as cards -- an against picker, the config comparison, what the
// record claims changed, the code patch, the runtime interventions and the
// worktree -- every one of them is true, and together they are an answer
// nobody reads.
//
// All of them are about *this run against its parent*, and most are only
// interesting once you know which file to look at. So the summary is one line
// here, and the detail is on the file itself, where you are already looking.

/** Load the comparison for the selected run. Shared by the strip and the
 *  file pane, so opening a file does not fetch it a second time. */
function comparison() {
  const runId = ctx.state.runId;
  if (!runId) return null;
  const against = ctx.state.against || "";
  loadOnce("diff", `${runId}:${against}`, () => api.diff(runId, against || undefined));
  const payload = ctx.state.diff;
  return payload && !payload.error ? payload : null;
}

/** The patch for one file, if this run changed it against its parent. */
function patchFor(path) {
  const payload = comparison();
  if (!payload) return null;
  const files = (payload.code || {}).files || [];
  // The diff names files relative to the package; the picker holds absolute
  // paths. Matching on the tail is what makes the two lists meet.
  return files.find(f => path === f.path || path.endsWith("/" + f.path)) || null;
}

function changedStrip() {
  const s = ctx.state;
  if (!s.runId) return null;
  const open_ = !!s.changedOpen;
  const payload = comparison();
  if (!payload) {
    return el("div", { class: "chg-strip" },
      el("span", { class: "chg-quiet", text: "comparing with the parent run…" }));
  }

  const code = payload.code || {};
  const config = payload.config || {};
  const runtime = (payload.interventions || {}).this || {};
  // Three layers, three counts, each one saying what it counted. One number
  // over all three is a number about nothing, and a layer nobody could read is
  // said to be unread rather than counted as zero -- "no config fields" beside
  // a run whose parent snapshotted none would be a claim the studio never
  // checked.
  const parts = [];
  if (payload.no_parent) {
    parts.push("no parent run to compare against");
  } else {
    // All three kinds, because the card below counts all three. Counting only
    // changed ones, the strip would say "1 config field" over a card saying
    // "7 of 67", leaving out the six fields the parent had and this run does
    // not.
    const fields = (config.changed || []).length + (config.added || []).length
                 + (config.removed || []).length;
    const compared = config.compared === undefined ? config.total : config.compared;
    parts.push(compared
      ? `${fields || "no"} config field${fields === 1 ? " differs" : "s differ"} at launch`
      : "launch config not recorded");
    const moved = (config.moved_after_launch || []).length;
    if (moved) {
      parts.push(`${moved} launch field${moved === 1 ? "" : "s"} moved during the run`);
    }
    if (code.ok) {
      // `changed` is counted from the two trees; `files` is what survived the
      // patch text being clipped to a size a browser can hold. They disagree
      // on a big diff. Only the ones with a patch can be opened and compared,
      // so the summary says which number is which rather than promising
      // buttons that are not there.
      const shown = (code.files || []).length;
      parts.push(`${code.changed || "no"} file${code.changed === 1 ? "" : "s"}`
                 + (code.changed > shown
                      ? ` (${shown} patched here, the rest clipped)` : ""));
    } else {
      parts.push("no code snapshot");
    }
    // Changes only: rlmcp files a note or a frame annotation under `said`, and
    // counting those would report two runtime edits for one weight change.
    const changes = runtime.changes === undefined
      ? (runtime.items || []).length : runtime.changes;
    parts.push(runtime.ok
      ? `${changes || "no"} runtime change${changes === 1 ? "" : "s"}`
      : "runtime changes not recorded");
  }

  const wt = payload.worktree || {};
  const dirty = (wt.files || []).filter(f => f.relevant).length;

  return el("div", { class: "chg-strip" },
    el("span", { class: "eyebrow", text: "against" }),
    againstPicker(payload),
    el("span", { class: "chg-sum", text: parts.join(" · ") }),
    // The record's own words for what changed, which the config snapshots do
    // not always contain -- a weight set at runtime is not a config field.
    // Said here rather than in a card of its own, because it is one sentence.
    (payload.change || []).length
      ? el("span", { class: "chg-said", title: payload.change.join("\n"),
                     text: payload.change.join("; ") })
      : null,
    el("span", { style: { marginLeft: "auto" } }),
    // Your uncommitted edits: about the tree in front of you rather than
    // about the run, which is exactly why it belongs beside the editor.
    dirty
      ? el("span", { class: "chg-dirty",
                     title: "files you have edited and not committed",
                     text: `${dirty} uncommitted` })
      : null,
    // The detail is *reachable*, not gone. Summarising is the simplification;
    // deleting the answer to "which fields, exactly" would be a different and
    // worse thing, and that question has a real user -- it is how you find the
    // knob a run actually moved.
    el("button", { class: open_ ? "act on" : "act",
                   onclick: () => set({ changedOpen: !open_ }) },
      open_ ? "Less" : "In detail"));
}

/** The comparison in full, except the code patch -- that lives on the file.
 *  Shown under the strip when you ask for it. */
function changedDetail() {
  const payload = comparison();
  if (!ctx.state.changedOpen || !payload) return null;
  const cards = el("div", { class: "chg-detail" });
  if (payload.no_parent) {
    add(cards, el("div", { class: "notyet" },
      el("b", { text: "No parent run" }),
      "This run has no config parent, so there is nothing to compare its "
      + "settings against."));
    return cards;
  }
  add(cards, configDiff(payload), interventionList(payload), worktreeDiff(payload));
  return cards;
}

/** What editing this file can and cannot change about the run on screen.
 *
 * A GUI that lets you edit `action_rate_l2` while a run is live, and says
 * nothing, is teaching a false model of the system. A reward
 * *weight* is a parameter and takes effect next batch -- that is the
 * Parameters view. A function *body* is code: the module was imported when the
 * trainer started and is bound; nothing you type here reaches the process that
 * is running. So the pane says which run is executing what, and that the edit
 * belongs to the next one.
 */
function nextRunBanner() {
  const s = ctx.state;
  const live = s.live && s.live.ok && s.live.state === "running";
  const run = s.runs.find(r => r.id === s.runId);
  if (!run) return null;

  const base = (s.detail && s.detail.record && s.detail.record.code
                && s.detail.record.code.head) || "";
  const stamp = base ? ` \u2009·\u2009 ${String(base).slice(0, 9)}` : "";

  const open = el("button", { class: "act", title: "a planned record, parented on this run, carrying the edited files",
                              onclick: () => ctx.actions.startNextRecord() },
                  "Open its record");

  if (live) {
    return el("div", { class: "nextrun live" },
      chip("next run", true),
      el("span", {}, el("b", { text: run.id }),
         ` is executing the code it was launched with${stamp}. `,
         "Saving here changes the package on disk, not that process — the "
         + "module is already imported. Your edit applies to the next run."),
      open);
  }
  return el("div", { class: "nextrun" },
    chip("next run"),
    el("span", {}, "Nothing is training. Saving changes the package on disk; "
       + "the next run launched from it picks the change up, and ",
       el("b", { text: run.id }), " keeps the code it was stamped with."),
    open);
}

function filePane(payload) {
  const path = ctx.state.filePath;
  if (!path) {
    return el("div", { class: "fview" },
      el("div", { class: "empty", text: "pick a file, or search by name or by what is in it" }));
  }

  loadOnce("file", path, () => api.sourceFile(path));
  const file = ctx.state.file;
  const pane = el("div", { class: "fview" });
  if (!file) { pane.append(el("div", { class: "empty", text: "opening…" })); return pane; }
  if (!file.ok) { pane.append(el("div", { class: "empty", text: file.reason || file.error })); return pane; }

  const rel = file.path.replace((payload.root || "") + "/", "");
  const editing = !!ctx.state.editing;
  const dirty = editor.isDirty();
  const patch = patchFor(file.path);
  const comparing = !!ctx.state.comparing && !!patch;

  add(pane,
    el("div", { class: "fview-h" },
      el("span", { class: "fp", text: rel }),
      dirty ? chip("unsaved", true) : null,
      el("span", { style: { marginLeft: "auto" } }),
      el("button", { class: "act", onclick: () => ctx.actions.askAboutSelection() },
        "Ask", el("small", { text: "⌘K" })),
      // The comparison for *this file*, where you are already looking. It
      // appears only when this run actually changed the file, because a
      // Compare button that answers "identical" on every file in the package
      // is a button nobody presses twice.
      patch
        ? el("button", {
            class: comparing ? "act on" : "act",
            title: `+${patch.added} −${patch.removed} against `
                 + `${(ctx.state.against || "the parent run")}`,
            onclick: () => set({ comparing: !comparing }),
          }, comparing ? "Comparing" : "Compare",
             el("small", { text: ` +${patch.added} −${patch.removed}` }))
        : null,
      el("button", {
        class: editing ? "act on" : "act",
        onclick: () => set({ editing: !editing }),
      }, editing ? "Editing" : "Read-only"),
      editing
        ? el("button", { class: "primary", disabled: !dirty,
                         onclick: () => ctx.actions.saveFile() },
            "Save", el("small", { text: "⌘S" }))
        : null),
    ctx.state.saveError
      ? el("div", { class: "m-error", style: { margin: "8px 12px" }, text: ctx.state.saveError })
      : null,
    nextRunBanner(),
    comparing ? null : editor.mount(),
    comparing ? el("div", { class: "fview-patch" }, patchFile(patch, true)) : null);

  // Mounting Monaco while the patch is showing would load the editor into a
  // pane it is not in, and `relayout` on a hidden host measures zero -- which
  // is how an editor comes back the wrong size after a toggle.
  if (comparing) return pane;

  // Monaco is mounted, then told what to show — the host element is moved into
  // this fresh pane on every repaint, but never recreated.
  editor.load().then(() => {
    editor.show(file, {
      readOnly: !editing,
      scheme: ctx.state.scheme,
      onSave: () => ctx.actions.saveFile(),
      onAsk: (sel) => ctx.actions.askAboutSelection(sel),
    });
    editor.relayout();
  }).catch(() => {
    pane.append(el("div", { class: "notyet" },
      el("b", { text: "The editor did not load" }),
      "web/vendor/monaco is missing or unreadable."));
  });

  return pane;
}
