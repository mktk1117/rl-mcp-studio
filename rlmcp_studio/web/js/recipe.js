// The recipe — one run, folded along the ancestry that made it.
//
// Records answers "what runs are there" and Diff answers "this one against
// that one". Neither answers the question somebody actually opens a record
// with: *what is this run*, counting everything it inherited. That answer is a
// fold, and it is computed in `sources/recipe.py` on rl-mcp's own ancestry
// walk — this module only draws it.
//
// One rule shapes every panel below: **a recipe is what changed, never what
// the config currently is.** A run's config has dozens of fields and most of
// them have never been touched by a human being; printing them is a wall of
// values nobody reads. So the top of this view is one sentence —
// "run-042 = entropy_coef 0.005 → 0.0015 + action_rate_l2 -0.01 → -0.05" —
// and everything longer is folded away behind a summary that says how much
// there is.

import { el, add, chip, dot, fmtVal, verdictColor } from "./ui.js";
import { set, loadOnce } from "./state.js";
import { api } from "./api.js";

/** Re-fold on the next paint. A record write anywhere in the ancestry changes
 *  the answer, and the ancestry is not one task's worth of records. */
export function refreshRecipe() {
  set({ recipeStamp: null });
}

export function recipeView(s, actions) {
  if (!s.runId) {
    return el("div", { class: "empty", text: "pick a run to see its recipe" });
  }
  loadOnce("recipe", `${s.runId}::${s.task || ""}`, () => api.recipe(s.runId));
  const payload = s.recipe;
  if (!payload) return el("div", { class: "empty", text: "folding the ancestry…" });
  if (payload.error) {
    return el("div", { class: "notyet" },
      el("b", { text: "The recipe could not be folded" }), payload.error);
  }
  if (!payload.ok) {
    return el("div", { class: "notyet" },
      el("b", { text: "No recipe for this run" }), payload.reason || "");
  }

  const view = el("div", { class: "recipeview" }, headline(payload), buildCard(s, actions),
                  foldCard(payload));
  add(view, phaseCard(payload), ladderCard(payload), chainCard(payload, actions));
  return view;
}

function buildCard(s, actions) {
  // The fold above is the recipe *read*; this writes it to disk as rlmcp's
  // recipe directory -- config, ladder, seed, packages, env, policy -- which
  // the Build view can then train again as recipe-<run>.
  const busy = s.recipeBusy === s.runId;
  const built = s.recipeBuilt && s.recipeBuilt.record === s.runId ? s.recipeBuilt : null;
  return el("div", { class: "card" },
    el("div", { class: "card-h" }, el("h3", { text: "Run it again" }),
      el("span", { class: "sp" }),
      el("button", { class: "act", disabled: busy || undefined,
                     onclick: () => actions.buildRecipe(s.runId) },
        busy ? "writing…" : built ? "Write the recipe again" : "Write a recipe")),
    el("div", { class: "card-b" },
      built
        ? el("div", {},
            el("p", { text: `Written to ${built.path}` }),
            (built.missing || []).length
              ? el("ul", { class: "muted" },
                  ...built.missing.map(m => el("li", { text: `missing: ${m}` })))
              : el("p", { class: "muted", text: "nothing missing" }),
            el("p", { class: "muted",
              text: "Train it from the Build view, under Recipes: the record "
                  + `opens as recipe-${s.runId}.` }))
        : el("p", { class: "muted",
            text: "`rlmcp recipe build` writes this run's config, ladder, seed, "
                + "packages, environment and final policy beside the records, "
                + "as a directory `rlmcp train --recipe` can launch." }),
      s.recipeError && s.recipeBusy === "" && !built
        ? el("p", { class: "m-error", text: s.recipeError }) : null));
}

// ── the sentence ─────────────────────────────────────────────────────────

function headline(p) {
  return el("div", { class: "card" },
    el("div", { class: "card-h" },
      el("h3", { text: "Recipe" }),
      p.verdict ? el("span", {
        class: "chip",
        style: { color: verdictColor(p.verdict), borderColor: verdictColor(p.verdict) },
        text: p.verdict,
      }) : null,
      el("span", { class: "sp" }),
      el("span", { class: "k", text: `${p.depth} runs from ${p.root}` })),
    el("div", { class: "card-b" },
      el("div", { class: "rcp-line" },
        el("b", { text: p.record }),
        el("span", { class: "rcp-eq", text: "=" }),
        el("span", { text: p.line })),
      el("div", { class: "k", style: { color: "var(--dim)", marginTop: "7px" },
                  text: "folded from the config edges, never stored — so it "
                        + "cannot drift from the records it is made of" })));
}

// ── what it is, accumulated ──────────────────────────────────────────────

function foldCard(p) {
  const config = p.config || {};
  const moved = config.moved || [];
  const body = el("div", { class: "card-b" });

  if (moved.length) {
    for (const row of moved) add(body, movedRow(row));
  } else {
    add(body, el("div", { class: "brief-none",
      text: "nothing this chain recorded was ever moved off the value it "
            + "started with" }));
  }

  // The keys that merely *appeared* — the run that first recorded a config
  // introduces all of them at once, and calling dozens of appearances dozens
  // of decisions is the failure this whole view exists to avoid. Counted here,
  // readable if you ask.
  if (config.introduced_total) {
    const list = el("div", { class: "rcp-intro" });
    for (const row of config.introduced || []) {
      list.append(el("div", { class: "dline added" },
        el("span", { class: "dk", text: row.key }),
        el("span", { class: "dv" },
          el("span", { class: "new", text: fmtVal(row.new) }),
          el("span", { class: "rcp-by", text: row.by }))));
    }
    body.append(el("details", { class: "rcp-fold" },
      el("summary", { text: `${config.introduced_total} more fields first `
                            + `recorded along the way, never changed since` }),
      list));
  }

  add(body, codeFold(p.code));
  return el("div", { class: "card" },
    el("div", { class: "card-h" },
      el("h3", { text: "What accumulated" }),
      el("span", { class: "sp" }),
      el("span", { class: "k", text: `${moved.length} moved, of `
                                     + `${config.keys || 0} this chain recorded` })),
    body);
}

function movedRow(row) {
  const kind = row.kind === "gone" ? "removed" : "changed";
  return el("div", { class: `dline ${kind}` },
    el("span", { class: "dk", title: row.key },
      el("b", { text: row.short }),
      // The abbreviation is two segments of the tail, so on a shallow key it
      // *is* the key — and printing both then reads as the same name twice.
      row.short === row.key ? null : el("span", { class: "rcp-full", text: row.key })),
    el("span", { class: "dv" },
      // Its own spans rather than `.dv .old`: the Diff view strikes the old
      // value through because that value is *gone*, and here it is the start
      // of a journey the arrow finishes.
      el("span", { class: "rcp-was", text: fmtVal(row.old) }),
      el("span", { class: "rcp-arrow", text: "→" }),
      el("span", { class: "rcp-now", text: row.kind === "gone" ? "gone" : fmtVal(row.new) }),
      // Who moved it last, and whether it was a config edit at launch or
      // somebody turning the knob mid-run. Those are kept apart because they
      // are different claims about the same number.
      el("span", { class: "rcp-by", text: row.layer === "runtime"
                    ? `${row.by} · mid-run` : row.by })));
}

function codeFold(code) {
  if (!code) return null;
  if (!code.ok) {
    return el("div", { class: "rcp-note", text: code.reason || "" });
  }
  const files = code.files || [];
  const head = `${code.changed} file${code.changed === 1 ? "" : "s"}, `
             + `+${code.added}/−${code.removed} since ${code.from}`;
  const list = el("div", { class: "rcp-files" });
  for (const file of files) {
    list.append(el("div", { class: `dline${file.trivial ? " removed" : ""}` },
      el("span", { class: "dk", text: file.path }),
      el("span", { class: "dv" },
        el("span", { class: "new", text: `+${file.added}` }),
        el("span", { class: "old", text: `−${file.removed}` }))));
  }
  return el("details", { class: "rcp-fold" },
    el("summary", { text: head }), list);
}

// ── between phases: the warm-start chain ─────────────────────────────────

function phaseCard(p) {
  const phases = p.phases || [];
  if (phases.length < 2) return null;      // a single segment is not a chain
  const body = el("div", { class: "card-b" });
  for (const phase of phases) {
    body.append(el("div", { class: "rcp-phase" },
      dot(phase.verdict, false),
      el("span", { class: "rcp-run", text: phase.run }),
      el("span", { class: "rcp-slug", text: phase.slug || "" }),
      el("span", { class: "sp" }),
      el("span", { class: "rcp-cost",
        text: phase.iterations === null || phase.iterations === undefined
              ? "iterations unknown" : `${phase.iterations} iterations` }),
      el("span", { class: "rcp-src",
        text: phase.scratch ? "from random init"
              : phase.dangling ? `weights from ${phase.from}, not in this store`
              : `weights ← ${phase.from}` })));
  }
  for (const cut of p.shortcuts || []) add(body, shortcutRow(cut));
  return el("div", { class: "card" },
    el("div", { class: "card-h" },
      el("h3", { text: "Phases" }),
      el("span", { class: "sp" }),
      el("span", { class: "k", text: "each reload of weights is a segment" })),
    body);
}

/** A from-scratch run that reached the same config, so the chain could start
 *  later than it did. Shown as an estimate and nothing stronger — a segment's
 *  cost is what it took *that time* from *that* checkpoint. */
function shortcutRow(cut) {
  return el("div", { class: "rcp-cut" },
    el("b", { text: cut.run }),
    el("span", { text: ` reached ${cut.instead_of}'s config from random init`
                       + (cut.iterations ? ` in ${cut.iterations} iterations` : "")
                       + `, which would skip ${cut.skips.join(", ")}.` }),
    el("span", { class: "rcp-note", text: "An estimate: that cost was paid "
                 + "from a different starting point." }));
}

// ── within a phase: the ladder ───────────────────────────────────────────

function ladderCard(p) {
  const ladder = p.curriculum || {};
  const body = el("div", { class: "card-b" });
  if (!ladder.ok) {
    body.append(el("div", { class: "brief-none", text: ladder.reason || "" }));
  } else {
    for (const stage of ladder.stages || []) {
      const params = el("span", { class: "rcp-stage-p" });
      for (const item of stage.parameters || []) {
        params.append(el("span", { class: "rcp-set", title: item.key },
          el("b", { text: item.short }), el("span", { text: ` = ${item.value}` })));
      }
      body.append(el("div", { class: "rcp-stage" },
        el("span", { class: "rcp-stage-n", text: stage.name }),
        el("span", {},
          params,
          stage.notes ? el("span", { class: "rcp-why", text: stage.notes }) : null)));
    }
    body.append(el("div", { class: "rcp-note", text: ladder.note || "" }));
  }
  return el("div", { class: "card" },
    el("div", { class: "card-h" },
      el("h3", { text: "Curriculum" }),
      el("span", { class: "sp" }),
      el("span", { class: "k", text: "the ladder that would have produced this run" })),
    body);
}

// ── the rungs ────────────────────────────────────────────────────────────

function chainCard(p, actions) {
  const body = el("div", { class: "card-b" });
  for (const step of p.steps || []) {
    const here = step.id === p.record;
    body.append(el("button", {
      class: `rcp-step${here ? " rcp-here" : ""}`,
      "aria-current": here ? "true" : null,
      // `e.currentTarget`, never the node this closure built: `patch()` keeps
      // the copy already on screen and throws this one away.
      onclick: (e) => actions.selectRun(e.currentTarget.dataset.step),
      "data-step": step.id,
    },
      el("span", { class: "stripe", style: { background: verdictColor(step.verdict) } }),
      el("span", { class: "rcp-step-b" },
        el("span", { class: "rcp-step-h" },
          el("span", { class: "rcp-run", text: step.id }),
          el("span", { class: "rcp-slug", text: step.slug || "" }),
          step.runtime && step.runtime.edits && step.runtime.edits.length
            ? chip(`${step.runtime.edits.length} mid-run`, true) : null),
        el("span", { class: "rcp-step-l", text: step.line }),
        // The record's own sentence for the change, under the derived one.
        // They are different kinds of statement and a reader is owed both --
        // except on a rung with nothing derived, where the line already *is*
        // the record's sentence and printing it twice looks like a bug.
        (step.said || []).length && !echoes(step.said[0], step.line)
          ? el("span", { class: "rcp-said", text: step.said[0] }) : null)));
  }
  return el("div", { class: "card" },
    el("div", { class: "card-h" },
      el("h3", { text: "Every rung" }),
      el("span", { class: "sp" }),
      el("span", { class: "k", text: "root first — click one to open it" })),
    body);
}

/** Whether the line is just the record's own sentence, trimmed to fit.
 *
 * A rung with nothing derived falls back to the prose, and the fold clips it
 * with an ellipsis and collapses its whitespace — so an equality test says
 * "different" about two copies of one sentence. */
function echoes(said, line) {
  const head = String(line).replace(/…$/, "").trim();
  return head.length > 0 && String(said).replace(/\s+/g, " ").startsWith(head);
}
