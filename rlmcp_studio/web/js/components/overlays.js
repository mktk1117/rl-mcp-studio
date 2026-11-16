// The overlays that begin something: a task, an adopted session, the next record.

import { el, on } from "../ui.js";
import { set } from "../state.js";

/** Writing a record for a session that has none. */
// ── beginning a task ────────────────────────────────────────────────────
//
// The one act in this app with no prior state: no id, no package, no session,
// no record. It is not a view -- a view of nothing is nothing
// -- so it lives where the app names what exists, which is the task switcher.
//
// It asks for what a task actually needs and no more, and it writes no code:
// what a task looks like is rl-mcp's to say, so the studio makes the directory
// and the agent writes the task into it.

export function newTaskOverlay(state, actions) {
  if (!state.newOpen) return null;

  const id = el("input", { class: "fb-input", type: "text", value: state.newTask || "",
    placeholder: "Example-Task-v0" });
  on(id, "input", (e) => { state.newTask = e.currentTarget.value; });
  const pkg = el("input", { class: "fb-input", type: "text", value: state.newPackage || "",
    placeholder: "my_tasks" });
  on(pkg, "input", (e) => { state.newPackage = e.currentTarget.value; });
  const robot = el("input", { class: "fb-input", type: "text", value: state.newRobot || "",
    placeholder: "unitree_g1" });
  on(robot, "input", (e) => { state.newRobot = e.currentTarget.value; });
  const what = el("textarea", { class: "fb-input why", rows: "5",
    placeholder: "what should the robot do, and what would count as doing it?" });
  what.value = state.newWhat || "";
  on(what, "input", (e) => { state.newWhat = e.currentTarget.value; });

  return el("div", {
    class: "zoom",
    onclick: (e) => { if (e.target.classList.contains("zoom")) actions.closeNewTask(); },
  },
    el("div", { class: "z-box adopt" },
      el("div", { class: "z-head" },
        el("span", { class: "z-name", text: "New task" }),
        el("span", { class: "sp" }),
        el("button", { class: "act", onclick: () => actions.closeNewTask() }, "Close")),
      el("div", { class: "launch-form" },
        el("label", {}, el("span", { text: "task id" }), id,
          el("span", { class: "why-note", text: "what --task takes, and what every "
                       + "record of every run will carry" })),
        el("label", {}, el("span", { text: "package" }), pkg,
          el("span", { class: "why-note", text: "a directory and an import, made "
                       + "in the task repository beside the records" })),
        el("label", {}, el("span", { text: "robot" }), robot),
        el("label", {}, el("span", { text: "what it should do" }), what,
          el("span", { class: "why-note", text: "the agent writes the package "
                       + "from this and from rl-mcp's own docs; you check it in "
                       + "Build" }))),
      state.newError ? el("p", { class: "m-error", text: state.newError }) : null,
      el("div", { class: "launch-go" },
        el("button", { class: "act primary", disabled: state.newBusy || undefined,
                       onclick: () => actions.createTask() },
          state.newBusy ? "starting…" : "Make the directory and begin"),
        el("span", { class: "eyebrow", text: "nothing is written until you press it" }))));
}

export function adoptOverlay(state, actions) {
  const row = state.adopting;
  if (!row) return null;

  const slug = el("input", { class: "fb-input", type: "text",
    placeholder: "a short name for this run", value: state.adoptSlug || "" });
  on(slug, "input", (e) => { state.adoptSlug = e.currentTarget.value; });
  const why = el("input", { class: "fb-input", type: "text",
    placeholder: "what were you testing? (optional, and editable later)",
    value: state.adoptWhy || "" });
  on(why, "input", (e) => { state.adoptWhy = e.currentTarget.value; });

  return el("div", {
    class: "zoom",
    onclick: (e) => { if (e.target.classList.contains("zoom")) actions.cancelAdopt(); },
  },
    el("div", { class: "z-box adopt" },
      el("div", { class: "z-head" },
        el("span", { class: "z-name", text: "This run has no record" }),
        el("span", { class: "sp" }),
        el("button", { class: "act", onclick: () => actions.cancelAdopt() }, "Close")),
      el("div", { class: "adopt-what" },
        el("div", { class: "kv" }, el("span", { text: "session" }),
           el("span", { text: row.name })),
        el("div", { class: "kv" }, el("span", { text: "task" }),
           el("span", { text: row.task || "unknown" })),
        el("div", { class: "kv" }, el("span", { text: "state" }),
           el("span", { text: `${row.state}${row.iteration != null ? ` · iteration ${row.iteration}` : ""}` }))),
      el("p", { class: "adopt-why",
        text: "The rail is built from records, so this run is invisible here "
            + "until one exists. Writing it does not change the run — it gives "
            + "the studio something to hang the frames, metrics and remarks on." }),
      el("div", { class: "fb-row" }, slug),
      el("div", { class: "fb-row" }, why),
      state.adoptError ? el("div", { class: "m-error", text: state.adoptError }) : null,
      el("div", { class: "fb-row" },
        el("span", { class: "sp" }),
        el("button", { class: "primary", disabled: !!state.adoptBusy,
          // The store, not these nodes -- see `ui.js`.
          onclick: () => actions.adopt(state.adoptSlug || "", state.adoptWhy || "") },
          state.adoptBusy ? "writing…" : "Write the record"))));
}

/** Opening the record an edit belongs to.
 *
 * The other half of the banner in the code pane: it says the edit cannot
 * reach the running process, and this is what to do about it. Same
 * shape as `adoptOverlay` on purpose -- both write a record, and a record is
 * always written by a person pressing a button.
 */
export function nextRecordOverlay(state, actions) {
  if (!state.nextRecord) return null;
  const parent = state.runs.find(r => r.id === state.runId);

  const slug = el("input", { class: "fb-input", type: "text",
    placeholder: "a short name for the next run", value: state.nextSlug || "" });
  on(slug, "input", (e) => { state.nextSlug = e.currentTarget.value; });
  const why = el("textarea", { class: "fb-input", rows: 3,
    placeholder: "what do you expect this edit to change, and by how much?" });
  why.value = state.nextWhy || "";
  on(why, "input", (e) => { state.nextWhy = e.currentTarget.value; });

  const changed = state.nextFiles || [];

  return el("div", {
    class: "zoom",
    onclick: (e) => { if (e.target.classList.contains("zoom")) set({ nextRecord: false }); },
  },
    el("div", { class: "z-box adopt" },
      el("div", { class: "z-head" },
        el("span", { class: "z-name", text: "The record this edit belongs to" }),
        el("span", { class: "sp" }),
        el("button", { class: "act", onclick: () => set({ nextRecord: false }) }, "Close")),
      el("div", { class: "adopt-what" },
        el("div", { class: "kv" }, el("span", { text: "parent" }),
           el("span", { text: parent ? `${parent.id} · ${parent.slug}` : "—" })),
        el("div", { class: "kv" }, el("span", { text: "task" }),
           el("span", { text: (parent && parent.task) || "—" })),
        el("div", { class: "kv" }, el("span", { text: "changed" }),
           // Tested for presence, not length: `[]` is the answer for a clean
           // working tree, and "reading…" must not sit there for ever on one.
           el("span", { text: state.nextFiles
                          ? (changed.length ? `${changed.length} file(s)`
                                            : "nothing — the working tree is clean")
                          : "reading…" }))),
      changed.length
        ? el("div", { class: "pol-list", style: { margin: "0 0 10px" } },
            changed.slice(0, 12).map(f => el("code", { text: f.split("/").slice(-2).join("/") })))
        : null,
      el("p", { class: "adopt-why",
        text: "Nothing runs yet. This writes a planned record parented on the "
            + "run above, carrying the edited files as its change — so when you "
            + "launch, the run already has a claim to be judged against." }),
      // The relevance rule found nothing that looks like task code. Said
      // plainly rather than writing a record that claims a README is the
      // experiment.
      state.nextOffTopic
        ? el("div", { class: "m-error",
              text: "None of these look like task code — they are docs, "
                  + "records or config. Check this is the edit you mean." })
        : null,
      el("div", { class: "fb-row" }, slug),
      el("div", { class: "fb-row" }, why),
      state.nextError ? el("div", { class: "m-error", text: state.nextError }) : null,
      el("div", { class: "fb-row" },
        el("span", { class: "sp" }),
        el("button", { class: "primary", disabled: !!state.nextBusy,
          onclick: () => actions.openNextRecord() },
          state.nextBusy ? "writing…" : "Write the record"))));
}
