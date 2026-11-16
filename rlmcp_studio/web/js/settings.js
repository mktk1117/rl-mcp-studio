// The settings window.
//
// Everything the studio remembers about how it looks and what it points at, in
// one place. Without it these are scattered across the chrome — the theme in
// two buttons, the accent behind a swatch, the layout in a menu — and the half
// that needs a restart (where the records are, which packages register tasks)
// can only be changed by editing the settings file by hand or by relaunching
// with different flags.
//
// The one rule this window is written around: **say what takes effect when.**
// A studio that accepted a new records root and went on serving the old one
// would be lying in the one place it must not, so a server setting is saved
// and then plainly marked as waiting for a restart, rather than appearing to
// have been applied.

import { el, on, chip } from "./ui.js";
import { state, set, setScheme, setLayout, setAccent, ACCENTS } from "./state.js";
import { machineCard } from "./components.js";
import * as editorHost from "./editor.js";

// There is deliberately no list here of what needs a restart. The server
// keeps one (`settings.SERVER_KEYS`) and its reply carries `restart_required`;
// a second copy in the browser is a rule that can disagree with itself, and
// this window's whole job is to be honest about when a setting takes effect.

/** A line of text you can edit, one item per line.
 *
 * Records roots and task packages are both "a list of strings, usually two or
 * three" — a textarea beats a repeating row of inputs with add and remove
 * buttons for something that size, and it pastes.
 */
/** The layouts, in the order the picker offers them. Workbench first because
 *  it is the default a studio that has never been told opens in. */
const LAYOUTS = [["workbench", "Workbench"], ["focus", "Focus"],
                 ["console", "Console"]];

function lines(value) {
  return String(value || "").split("\n").map(s => s.trim()).filter(Boolean);
}

export function settingsOverlay(s, actions) {
  if (!s.settingsOpen) return null;
  const saved = s.settings || {};
  const agent = s.agent || {};

  const close = () => actions.closeSettings();

  return el("div", {
    class: "zoom",
    onclick: (e) => { if (e.target.classList.contains("zoom")) close(); },
  },
    el("div", { class: "z-box settings" },
      el("div", { class: "z-head" },
        el("span", { class: "z-name", text: "Settings" }),
        el("span", { class: "sp" }),
        saved.path
          ? el("span", { class: "set-path", title: saved.path, text: saved.path })
          : null,
        el("button", { class: "act", onclick: close }, "Close")),

      el("div", { class: "set-body" },
        appearance(s),
        agentSection(s, agent),
        roots(s, saved),
        // The same card a fresh install opens on, kept here for every day
        // after: `rlmcp-studio --check` without leaving the browser.
        section("This machine", "what the install can do here, and the fix for "
                              + "anything it cannot — re-read each time this opens",
          machineCard(s, { title: "rlmcp-studio --check" })),
        s.settingsError ? el("div", { class: "m-error", text: s.settingsError }) : null,
        s.settingsRestart && s.settingsRestart.length
          ? el("div", { class: "set-restart" },
              chip("restart to apply", true),
              el("span", { text: `Saved. ${s.settingsRestart.join(" and ")} `
                               + `${s.settingsRestart.length > 1 ? "take" : "takes"} `
                               + "effect when the studio is started again — this "
                               + "process is still serving what it opened with." }))
          : null)));
}

// ── appearance ───────────────────────────────────────────────────────────
function appearance(s) {
  return section("Appearance", "follows you rather than this browser, because it "
                             + "is saved to the file above",
    el("div", { class: "set-row" },
      el("span", { class: "set-label", text: "theme" }),
      el("div", { class: "seg" },
        ["dark", "light"].map(name => el("button", {
          class: "seg-btn", "aria-pressed": s.scheme === name ? "true" : "false",
          onclick: () => { setScheme(name); editorHost.applyTheme(name); },
        }, name)))),

    el("div", { class: "set-row" },
      el("span", { class: "set-label", text: "accent" }),
      el("div", { class: "set-swatches" },
        ACCENTS.map(name => el("button", {
          class: `set-swatch accent-${name}${s.accent === name ? " on" : ""}`,
          title: name, "aria-pressed": s.accent === name ? "true" : "false",
          onclick: () => setAccent(name),
        })))),

    el("div", { class: "set-row" },
      el("span", { class: "set-label", text: "layout" }),
      el("div", { class: "seg" },
        LAYOUTS.map(([id, label]) => el("button", {
          class: "seg-btn", "aria-pressed": s.layout === id ? "true" : "false",
          // The one control that writes the layout down. The bar switches for
          // now; this says what the studio comes up in tomorrow.
          onclick: () => setLayout(id, { remember: true }),
        }, label))),
      el("span", { class: "why-note",
                   text: "the studio opens in this one, and only this control "
                       + "changes that \u2014 switching in the bar is for now. "
                       + "Workbench is the default until you choose otherwise" })));
}

// ── the agent ────────────────────────────────────────────────────────────
function agentSection(s, agent) {
  const models = agent.models || [];
  const modes = agent.modes || [];
  if (!models.length && !modes.length) {
    return section("Agent", "", el("div", { class: "empty",
      text: agent.reason || "no agent provider on this studio" }));
  }
  return section("Agent",
    "applies to the conversation already open as well: the model answers from "
    + "your next message, and how much runs without asking changes at the very "
    + "next tool call",

    // Who drives. Only when there is a choice: one provider is a fact, not a
    // setting, and a row of one button is a row that asks a question with one
    // answer.
    (agent.providers || []).length > 1
      ? el("div", { class: "set-row" },
          el("span", { class: "set-label", text: "provider" }),
          el("div", { class: "set-choices" },
            agent.providers.map(p => el("button", {
              class: "set-choice",
              "aria-pressed": p.name === agent.provider ? "true" : "false",
              title: p.available ? "" : (p.reason || ""),
              onclick: () => window.__studioActions.setAgentProvider(p.name),
            }, el("b", { text: p.label || p.name }),
               el("small", { text: p.available ? "ready" : (p.reason || "not available") })))),
          el("div", { class: "set-note",
                      text: "who drives conversations started from now on — an "
                          + "open one keeps the provider it started with. One "
                          + "that is not ready says why, and never stops the "
                          + "studio; the other stays a click away." }))
      : null,

    // How the chosen provider signs in, when it has more than one way. The
    // list is the provider's (docs/providers.md says which ways each one
    // offers, and why), so the panel draws whatever it offers.
    (agent.signins || []).length > 1
      ? el("div", { class: "set-row" },
          el("span", { class: "set-label", text: "sign-in" }),
          el("div", { class: "set-choices" },
            agent.signins.map(a => el("button", {
              class: "set-choice",
              "aria-pressed": a.id === agent.signin ? "true" : "false",
              onclick: () => window.__studioActions.setAgentAuth(a.id),
            }, el("b", { text: a.label }),
               a.note ? el("small", { text: a.note }) : null))),
          el("div", { class: "set-note",
                      text: "applies to conversations started from now on." }))
      : null,

    el("div", { class: "set-row" },
      el("span", { class: "set-label", text: "model" }),
      el("div", { class: "set-choices" },
        models.map(m => el("button", {
          class: "set-choice", "aria-pressed": m.id === agent.model ? "true" : "false",
          onclick: () => window.__studioActions.setAgentModel(m.id),
        }, el("b", { text: m.label }),
           m.note ? el("small", { text: m.note }) : null)))),

    el("div", { class: "set-row" },
      el("span", { class: "set-label", text: "permission" }),
      el("div", { class: "set-choices" },
        modes.map(m => el("button", {
          class: `set-choice mode-${m.id}`,
          "aria-pressed": m.id === agent.mode ? "true" : "false",
          onclick: () => window.__studioActions.setAgentMode(m.id),
        }, el("span", { class: `mode-dot m-${m.id}` }),
           el("b", { text: m.label }),
           m.note ? el("small", { text: m.note }) : null)))));
}

// ── where the studio looks ───────────────────────────────────────────────
function roots(s, saved) {
  const current = saved.settings || {};
  // `.value` as a *property*, not as an attribute. `el()` puts unknown keys on
  // the node with `setAttribute`, and a `<textarea>` has no value attribute --
  // its content is its child text -- so as an attribute both boxes come up
  // empty over a file that plainly has roots in it. The reconciler copies
  // `value` across a morph for the same reason, which is why this survives a
  // repaint.
  const recordsBox = el("textarea", {
    class: "fb-input set-lines", rows: "3", spellcheck: "false",
    placeholder: "/path/to/records",
  });
  recordsBox.value = s.setRecords !== null && s.setRecords !== undefined
    ? s.setRecords : (current.records || []).join("\n");
  on(recordsBox, "input", (e) => { state.setRecords = e.currentTarget.value; });

  const packagesBox = el("textarea", {
    class: "fb-input set-lines", rows: "3", spellcheck: "false",
    placeholder: "my_tasks.locomotion",
  });
  packagesBox.value = s.setPackages !== null && s.setPackages !== undefined
    ? s.setPackages : (current.taskPackages || []).join("\n");
  on(packagesBox, "input", (e) => { state.setPackages = e.currentTarget.value; });

  return section("Where the studio looks",
    "one per line. These are the two settings this process cannot adopt while "
    + "it runs",

    el("div", { class: "set-col" },
      el("span", { class: "set-label", text: "records roots" }),
      recordsBox,
      el("span", { class: "why-note",
                   text: "every root is read; a record id that exists in two of "
                       + "them is resolved by task" })),

    el("div", { class: "set-col" },
      el("span", { class: "set-label", text: "task packages" }),
      packagesBox,
      el("span", { class: "why-note",
                   text: "imported to register their tasks, so a task with no "
                       + "runs is still in the switcher" })),

    el("div", { class: "set-go" },
      el("button", {
        class: "act primary", disabled: s.settingsBusy || undefined,
        onclick: () => window.__studioActions.saveRoots({
          records: lines(state.setRecords !== null && state.setRecords !== undefined
                           ? state.setRecords : (current.records || []).join("\n")),
          taskPackages: lines(state.setPackages !== null && state.setPackages !== undefined
                                ? state.setPackages : (current.taskPackages || []).join("\n")),
        }),
      }, s.settingsBusy ? "saving…" : "Save"),
      el("span", { class: "eyebrow", text: "written to the file; takes effect on restart" })));
}

function section(title, note, ...body) {
  return el("div", { class: "set-section" },
    el("div", { class: "set-head" },
      el("h3", { text: title }),
      note ? el("span", { class: "set-note", text: note }) : null),
    ...body);
}
