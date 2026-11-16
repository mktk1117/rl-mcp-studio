// Signing in, and choosing which provider new conversations get.

import { el, dot, chip, on } from "../ui.js";
import { api } from "../api.js";
import { state, set } from "../state.js";

// ── signing in, without opening a terminal ──────────────────────────────
//
// The studio never touches the credential. It asks the local `claude` CLI to
// start a sign-in, shows you the URL it prints, and hands the one-time code
// you get back to that same process. The code goes to the local CLI and
// nowhere else.
export function signIn(status, { heading = "", why = "" } = {}) {
  // Only Claude has a sign-in to drive. Another provider authenticates some
  // other way, and its card says which way -- plus the one control that helps
  // everywhere, which is to look again.
  if ((status.provider || "claude") !== "claude") return providerCard(status);
  const login = status.login || {};
  const box = el("div", { class: "signin" });

  if (!status.cli) {
    box.append(el("div", { class: "notyet" },
      el("b", { text: "The claude CLI is not installed" }),
      "The Agent SDK runs it. Install Claude Code, then reload this page.",
      switchTo(status)));
    return box;
  }

  if (login.waiting_for_code) {
    box.append(
      el("div", { class: "si-h" }, el("b", { text: "Two steps left" })),
      el("ol", { class: "si-steps" },
        el("li", {},
          "Open the sign-in page: ",
          el("a", { class: "si-link", href: login.url, target: "_blank",
                    rel: "noopener noreferrer" }, "authorize in your browser"),
          // The tab is opened for you, and a popup blocker is the usual reason
          // it did not appear. Said here rather than left as a page that
          // silently never opened.
          el("div", { class: "si-note",
            text: "The studio already opened this in a new tab. If nothing "
                + "appeared, your browser blocked it — use the link." })),
        el("li", {}, "Approve it there, then copy the code Anthropic shows you."),
        el("li", {}, "Come back to this panel and paste the code here:")),
      el("div", { class: "si-row" },
        codeInput(),
        el("button", { class: "primary", onclick: submitCode }, "Finish")),
      el("div", { class: "si-note",
        text: "The code goes straight to the claude CLI on this machine. The studio does not store it." }),
      el("button", { class: "act", onclick: cancelLogin }, "Cancel"));
    if (login.error) box.append(el("div", { class: "m-error", text: login.error }));
    return box;
  }

  // What to do, in the order you would do it. One button and one line of note
  // is enough when you already know the flow and nothing at all when the app
  // has just told you to re-authenticate: a sentence that says *that* you
  // must sign in again does not say where.
  box.append(
    el("div", { class: "notyet" },
      el("b", { text: heading || "The agent is not signed in" }),
      why || status.reason || "not signed in",
      el("ol", { class: "si-steps", style: { marginTop: "9px" } },
        el("li", {}, "Press ", el("b", { text: "Sign in" }),
                     ". The studio runs ", el("code", { text: "claude auth login" }),
                     " here and opens Anthropic's page in a new tab."),
        el("li", {}, "Approve it there and copy the code you are given."),
        el("li", {}, "Paste that code into the box this panel puts in its place.")),
      el("div", { class: "si-row", style: { marginTop: "11px" } },
        el("button", { class: "primary", onclick: () => startLogin("claudeai") },
          "Sign in"),
        el("button", { onclick: () => startLogin("console") }, "Use API billing"),
        // For a sign-in done anywhere but here. Without it the only way to
        // make this panel look again is to reload the whole app, which throws
        // away every open menu and the scroll position of the conversation.
        el("button", { class: "act", onclick: recheckAuth,
                       title: "ask the CLI again — for a sign-in done in a "
                            + "terminal rather than here" },
          "Check again")),
      el("div", { class: "si-note" },
        el("div", { text: "Sign in is Claude.ai (Pro or Max). Use API billing "
                        + "is a console account billed per token." }),
        el("div", { style: { marginTop: "4px" } },
          "Prefer a terminal? Open the studio's own with ",
          el("b", { text: "Terminal" }), " in the top bar, run ",
          el("code", { text: "claude auth login" }), ", then press ",
          el("b", { text: "Check again" }), "."),
        el("div", { style: { marginTop: "4px" },
                    text: "Or set ANTHROPIC_API_KEY in the environment and "
                        + "restart the studio — no sign-in at all." })),
      switchTo(status)));
  if (login.error) box.append(el("div", { class: "m-error", text: login.error }));
  return box;
}

function codeInput() {
  // Everything typed into this app has to survive a repaint, and this field
  // most of all: signing in happens *while* a run streams status, so the tree
  // is rebuilt every few seconds underneath the paste. Without the draft and
  // the focus key, the code vanishes between the paste and the button.
  const input = el("input", { class: "si-input", type: "text", id: "si-code",
    placeholder: "paste the code", autocomplete: "off", spellcheck: "false",
    onkeydown: (e) => {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === "Enter") submitCode();
    } });
  input.value = state.codeDraft || "";
  on(input, "input", (e) => { state.codeDraft = e.currentTarget.value; });
  return input;
}

async function startLogin(mode) {
  set({ agentBusyAuth: true });
  try {
    const flow = await api.agentLogin(mode);
    set({ agent: { ...state.agent, login: flow }, agentBusyAuth: false });
    if (flow.url) window.open(flow.url, "_blank", "noopener");
  } catch (err) {
    set({ agent: { ...state.agent, login: { error: err.message } }, agentBusyAuth: false });
  }
}

async function submitCode() {
  const input = document.getElementById("si-code");
  // The draft is the authority: the field may have been rebuilt since the
  // paste, and reading a freshly-built empty input would submit nothing.
  const code = ((input && input.value) || state.codeDraft || "").trim();
  if (!code) {
    set({ agent: { ...state.agent, login: { ...(state.agent || {}).login,
                                            error: "paste the code first" } } });
    return;
  }
  try {
    const result = await api.agentLoginCode(code);
    // A one-time code is worth nothing after it is spent, and keeping it in the
    // store would put it in every later render.
    state.codeDraft = "";
    set({ agent: { ...state.agent, ...result, login: { running: false, error: result.error || "" } } });
  } catch (err) {
    set({ agent: { ...state.agent, login: { error: err.message } } });
  }
}

/** Ask the server to re-read `claude auth status`, rather than wait for it to.
 *
 * The panel learns about sign-in over the socket, and a sign-in done outside
 * the app -- in the studio's terminal, or in another window -- publishes
 * nothing. `force`, because the server caches the CLI's answer for a few
 * seconds and this button is pressed in exactly those seconds.
 */
async function recheckAuth() {
  set({ agentBusyAuth: true });
  try {
    const status = await api.agentStatus(true);
    set({ agent: { ...state.agent, ...status }, agentBusyAuth: false });
  } catch (err) {
    set({ agent: { ...state.agent, login: { error: err.message } },
          agentBusyAuth: false });
  }
}

async function cancelLogin() {
  const flow = await api.agentLoginCancel().catch(() => ({}));
  set({ agent: { ...state.agent, login: flow } });
}


// ── who is driving ──────────────────────────────────────────────────────

/** Which provider new conversations get.
 *
 * A menu on the panel's own chip rather than only in Settings, because the
 * reason to switch is usually visible right here -- one provider signed out,
 * or a turn you want a second model's opinion on. The list is the server's,
 * `{name, label, available, reason}` per provider, so a third one needs no
 * change in the browser. It applies to conversations started from now on: an
 * open one keeps the provider it started with, the way it keeps its prompt,
 * because its context lives inside that provider's session.
 */
export function providerButton(status) {
  const providers = status.providers || [];
  const current = providers.find(p => p.name === status.provider)
    || { name: status.provider || "claude", label: status.provider || "claude" };
  if (providers.length < 2) return chip(current.label || current.name);
  const open = state.providerOpen;
  const button = el("button", {
    class: "modebtn provbtn", "aria-expanded": open ? "true" : "false",
    title: current.available === false ? current.reason : "who is driving",
    onclick: (e) => { e.stopPropagation();
                      set({ providerOpen: !open, modeOpen: false, modelOpen: false,
                            harnessOpen: false }); },
  }, dot(current.available === false ? "planned" : "running", false),
     current.label || current.name,
     el("span", { class: "conv-caret", text: "\u25BE" }));
  if (!open) return el("span", { class: "menuwrap" }, button);

  return el("span", { class: "menuwrap" }, button,
    el("div", { class: "menu provmenu" },
      el("div", { class: "menu-h" },
        el("span", { class: "eyebrow", text: "who drives the next conversation" }),
        el("span", { class: "menu-note",
                     text: "an open conversation keeps the provider it started with" })),
      providers.map(p => el("button", {
        class: "mrow", "aria-current": p.name === status.provider ? "true" : null,
        onclick: () => { set({ providerOpen: false });
                         window.__studioActions.setAgentProvider(p.name); },
      },
        // The row is a grid whose first column is a dot: whether this one
        // could answer right now, which is the fact the menu is opened for.
        dot(p.available ? "running" : "planned", false),
        el("span", {}, el("span", { class: "mn", text: p.label || p.name }),
                       el("span", { class: "mr",
                                    text: p.available ? "ready" : (p.reason || "not available") })),
        el("span", { class: "mc" })))));
}

/** The not-ready card for a provider that has no sign-in to drive.
 *
 * Antigravity's SDK authenticates with a Gemini API key, or with Vertex
 * credentials, read from the environment the studio was started in -- it
 * does not use the Antigravity IDE's Google login. So there is nothing for
 * a button to run, and the honest card is the steps in the order you would
 * do them, plus Check again for once you have.
 */
function providerCard(status) {
  const box = el("div", { class: "signin" });
  const me = (status.providers || []).find(p => p.name === status.provider) || {};
  const label = me.label || status.provider || "this provider";
  const auths = status.signins || [];
  const missing = /not installed/.test(status.reason || "");
  box.append(
    el("div", { class: "notyet" },
      el("b", { text: `${label} is not ready` }),
      status.reason || me.reason || "not available",
      // Which way in. Two, and the card is where you find out the one you
      // are on does not work, so the other is offered right here.
      auths.length > 1
        ? el("div", { class: "si-row", style: { marginTop: "9px" } },
            auths.map(a => el("button", {
              class: "act", "aria-pressed": a.id === status.signin ? "true" : "false",
              title: a.note || "",
              onclick: () => window.__studioActions.setAgentAuth(a.id),
            }, a.label)))
        : null,
      // Antigravity has one way in: the SDK with a key. A Google-account
      // sign-in is deliberately not offered (Google's terms; docs/providers.md).
      status.provider === "antigravity"
        ? el("ol", { class: "si-steps", style: { marginTop: "9px" } },
            el("li", {}, "Put a key in the studio's environment before starting it: ",
              el("code", { text: "export GEMINI_API_KEY=…" }),
              " (or the Vertex variables)."),
            el("li", {}, "Restart the studio, then press ",
              el("strong", { text: "Check again" }), "."))
        : null,
      status.provider === "codex"
        ? el("ol", { class: "si-steps", style: { marginTop: "9px" } },
            missing ? el("li", {}, "Install the Codex CLI, then restart Studio.") : null,
            el("li", {}, "Run ", el("code", { text: "codex login" }),
              " once in Terminal, then press ", el("strong", { text: "Check again" }), "."),
            el("li", {}, "Or start Studio with ",
              el("code", { text: "OPENAI_API_KEY" }), " in its environment."))
        : null,
      el("div", { class: "si-row", style: { marginTop: "11px" } },
        el("button", { class: "act", onclick: recheckAuth,
                       title: "look at the environment again" }, "Check again")),
      el("div", { class: "si-note",
                  text: status.provider === "codex"
                    ? "The studio starts Codex locally; its sign-in and MCP settings stay with Codex."
                    : "The studio never sees the key: the SDK reads it from "
                      + "the environment itself." }),
      switchTo(status)));
  return box;
}

/** The way out of a provider that cannot answer: the ones that can.
 *
 * Every not-ready card carries this, whichever provider it is about. The
 * studio must not be stuck behind a missing CLI or a missing key when the
 * other agent is one click away -- and a card that only explains the fault
 * ("install Claude Code and reload", with another provider ready in the same
 * menu) is exactly that. Nothing when there is nowhere to go, rather than a
 * row of buttons that all lead back here.
 */
function switchTo(status) {
  const others = (status.providers || [])
    .filter(p => p.name !== status.provider && p.available);
  if (!others.length) return null;
  return el("div", { class: "si-row", style: { marginTop: "9px" } },
    others.map(p => el("button", {
      class: "act", title: "for conversations started from now on",
      onclick: () => window.__studioActions.setAgentProvider(p.name),
    }, `Use ${p.label || p.name} instead`)));
}
