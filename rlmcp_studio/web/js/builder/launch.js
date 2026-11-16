// Launching a run: the launch card, the recipes, and where runs are written.

import { el, chip, on } from "../ui.js";
import { state } from "../state.js";

// ── launching the first run ─────────────────────────────────────────────
//
// The end of the loop, and the reason the record comes first: `rlmcp train
// --record-run` wants an id, and opening it here rather than afterwards is
// what makes the claim cost something. The hypothesis is required for the
// same reason the record store requires one -- it is the part only a person
// can write, and a run without it is a run nobody can be wrong about.

export function launchCard(build, s, actions) {
  const jobs = ((s.launches || {}).jobs) || [];
  const mine = jobs.filter(j => j.task === s.task);
  const live = mine.find(j => j.state === "running" || j.state === "starting");
  const last = mine[mine.length - 1];

  const head = el("div", { class: "card-h" },
    el("h3", { text: "Training" }),
    live ? chip("running", true) : last ? chip(last.state) : null,
    el("span", { class: "sp" }),
    live
      ? el("button", { class: "act", onclick: () => actions.cancelLaunch(live.id) },
          "Stop the run")
      : el("button", { class: "act", "aria-expanded": s.launchOpen ? "true" : "false",
                       onclick: () => actions.toggleLaunch() },
          s.launchOpen ? "Not now" : "Launch a run"));

  if (live || (last && !s.launchOpen)) {
    const job = live || last;
    return el("div", { class: "card" }, head,
      el("div", { class: "card-b launch-live" },
        el("div", { class: "kv" }, el("span", { text: "record" }),
          el("button", { class: "act",
                         title: "open its record, where you steer it",
                         onclick: () => actions.openRun(job.record_id) },
            job.record_id || "—")),
        el("div", { class: "kv" }, el("span", { text: "state" }),
          el("span", { text: job.state + (job.error ? ` · ${job.error}` : "") })),
        ...launchedFrom(job),
        job.session_dir
          ? el("div", { class: "kv" }, el("span", { text: "session" }),
              el("code", { text: job.session_dir }))
          : el("p", { class: "muted",
                      text: "no session yet — the trainer prints one when the "
                          + "environment is built. Its output is below." }),
        (job.log || []).length
          ? el("details", { class: "build-log" },
              el("summary", { text: `trainer output · ${job.log.length} lines` }),
              el("pre", { text: job.log.join("\n") }))
          : null));
  }

  if (!s.launchOpen) {
    return el("div", { class: "card" }, head,
      el("div", { class: "card-b" },
        el("p", { class: "muted",
                  text: "When the gates pass and the preview looks right, this "
                      + "is where it starts training — on this machine, with a "
                      + "record opened first so the rail can show it." }),
        recipeList(s, actions)));
  }

  const slug = el("input", { class: "fb-input", type: "text",
    placeholder: "a short name for this run", value: s.launchSlug || "" });
  on(slug, "input", (e) => { state.launchSlug = e.currentTarget.value; });
  const why = el("textarea", { class: "fb-input why", rows: "3",
    placeholder: "what do you expect, and what would prove it wrong?" });
  // A textarea's text is a property, not an attribute: `value:` in the markup
  // would be ignored on creation, and the draft has to survive a view switch.
  why.value = s.launchWhy || "";
  on(why, "input", (e) => { state.launchWhy = e.currentTarget.value; });
  const envs = el("input", { class: "fb-input small", type: "number", min: "1",
                             value: String(s.launchEnvs || 4096) });
  on(envs, "input", (e) => { state.launchEnvs = Number(e.currentTarget.value) || 1; });
  const device = el("input", { class: "fb-input small", type: "text",
                               value: s.launchDevice || "cuda:0" });
  on(device, "input", (e) => { state.launchDevice = e.currentTarget.value; });
  const iterations = el("input", { class: "fb-input small", type: "number", min: "0",
                                   value: String(s.launchIterations || 0) });
  on(iterations, "input", (e) => { state.launchIterations = Number(e.currentTarget.value) || 0; });
  const video = el("input", { class: "fb-input small", type: "text",
                              placeholder: "e.g. 50", value: s.launchVideo || "" });
  on(video, "input", (e) => { state.launchVideo = e.currentTarget.value; });

  return el("div", { class: "card" }, head,
    el("div", { class: "card-b launch-form" },
      el("label", {}, el("span", { text: "name" }), slug),
      el("label", {}, el("span", { text: "hypothesis" }), why),
      el("div", { class: "launch-row" },
        el("label", {}, el("span", { text: "envs" }), envs),
        el("label", {}, el("span", { text: "device" }), device),
        el("label", {}, el("span", { text: "iterations" }), iterations),
        el("label", {}, el("span", { text: "clip every" }), video)),
      s.launchError ? el("p", { class: "m-error", text: s.launchError }) : null,
      el("div", { class: "launch-go" },
        el("button", { class: "act primary", disabled: s.launchBusy || undefined,
                       onclick: () => actions.launch() },
          s.launchBusy ? "launching…" : "Open a record and train"),
        el("span", { class: "eyebrow",
                     text: "0 iterations means until you stop it" }))));
}

// What a launched run actually executed, as the runner reported it.
//
// Every launch is stamped before its process exists (runners/trees.py): the
// record carries a git tree, and the runner either runs from a copy of that
// tree or -- told `in_place`, or when the copy could not be made or sent to
// the host -- from a checkout that may have moved since. Those are different
// runs of the "same" code, and without this card saying which, the only way to
// tell is to open the record and compare tree hashes by hand. The facts are
// `Job.describe()`'s, as the runner set them: `tree`, `base`, `clean`,
// `materialized`, `workdir`, and `note`, the runner's own sentence for the
// cases that need one (an rl-mcp too old to keep the stamp, a host that could
// not take the tree). A job with no `tree` key at all is a run this studio did
// not start, and the card says nothing about its code rather than guess.
function launchedFrom(job) {
  const note = job.note ? el("p", { class: "muted", text: job.note }) : null;
  if (!("tree" in job)) return note ? [note] : [];
  const rows = [];
  if (job.tree) {
    const whence = job.base ? ` · a variant of ${job.base.slice(0, 12)}`
      : job.clean === false ? " · with edits not committed at launch"
      : job.clean === true ? " · as committed" : "";
    rows.push(el("div", { class: "kv", title: job.tree },
      el("span", { text: "code" }),
      el("span", { text: `tree ${job.tree.slice(0, 12)}${whence}` })));
    rows.push(job.materialized
      ? el("div", { class: "kv" }, el("span", { text: "ran from" }),
          el("code", { text: job.workdir || "a copy of that tree" }))
      : el("div", { class: "kv" }, el("span", { text: "ran from" }),
          el("span", { text: "the working tree in place, not a copy of the stamp"
                              + (job.workdir ? ` · ${job.workdir}` : "") })));
    // A local copy is only the place the run executed from: removed once the
    // run succeeds (the tree lives on under its git ref), kept when it fails
    // so the failure can be looked at. Without this line the path above is a
    // directory somebody goes looking for and does not find.
    if (job.materialized && job.runner === "local" && job.state === "succeeded") {
      rows.push(el("p", { class: "muted",
        text: "That copy was removed when the run succeeded; the tree is kept under its git ref." }));
    }
  } else {
    rows.push(el("div", { class: "kv" }, el("span", { text: "code" }),
      el("span", { text: "not stamped here — the trainer stamps the directory it starts in" })));
  }
  if (note) rows.push(note);
  return rows;
}

function recipeList(s, actions) {
  // Recipes `rlmcp recipe build` wrote for this task. Training one is the
  // other way a run starts: no hypothesis to type, because the recipe is the
  // plan -- rlmcp opens the record itself, named recipe-<run>, with the
  // original's numbers as the prediction and the band as the falsifier.
  const recipes = s.recipes || [];
  if (!recipes.length) {
    return el("p", { class: "muted",
      text: "No recipe for this task yet. A run's Recipe tab can write one: "
          + "config, ladder, seed, packages and policy, ready to train again." });
  }
  const rows = recipes.map(r => {
    const busy = s.recipeBusy === r.path;
    const facts = [
      r.iterations ? `${r.iterations} it` : null,
      r.num_envs ? `${r.num_envs} envs` : null,
      r.seed != null ? `seed ${r.seed}` : null,
      r.task_packages && r.task_packages.length ? r.task_packages.join(", ")
        : "packages not recorded",
      r.policy ? "policy" : null,
      r.package ? "package" : null,
    ].filter(Boolean).join(" · ");
    const claimed = Object.entries(r.expect || {})
      .map(([k, v]) => `${k} ${v}`).join(", ");
    return el("div", { class: "recipe-row" },
      el("div", { class: "recipe-head" },
        el("b", { text: `recipe of ${r.from_run || "?"}` }),
        el("span", { class: "muted", text: r.headline || "" })),
      el("div", { class: "muted", text: facts }),
      claimed ? el("div", { class: "muted", text: `claims: ${claimed}` }) : null,
      r.error ? el("p", { class: "m-error", text: r.error }) : null,
      el("div", { class: "launch-go" },
        el("button", { class: "act primary", disabled: (busy || r.error) ? true : undefined,
                       title: r.path,
                       onclick: () => actions.launchRecipe(r.path) },
          busy ? "launching…" : `Train it again as recipe-${r.from_run || "?"}`),
        el("code", { class: "muted", text: r.name })));
  });
  return el("div", { class: "recipes" },
    el("h4", { text: "Recipes" }),
    s.recipeError ? el("p", { class: "m-error", text: s.recipeError }) : null,
    ...rows);
}

export function whereCard(build) {
  const where = build.where || {};
  const preview = build.preview || {};
  return el("div", { class: "card" },
    el("div", { class: "card-h" }, el("h3", { text: "Where this is built" })),
    el("div", { class: "card-b build-where" },
      el("div", { class: "kv" }, el("span", { text: "python" }),
        el("code", { text: where.python || "—" })),
      el("div", { class: "kv" }, el("span", { text: "from" }),
        el("code", { text: where.cwd || "—" })),
      preview.session
        ? el("div", { class: "kv" }, el("span", { text: "session" }),
            el("code", { text: preview.session })) : null,
      el("p", { class: "muted",
                text: where.own
                  ? "This is the studio's own interpreter. It has rl-mcp and "
                    + "little else, so a task package that lives elsewhere will "
                    + "not import here."
                  : "The task repository's environment, not the studio's — the "
                    + "one that can actually construct this task." })));
}
