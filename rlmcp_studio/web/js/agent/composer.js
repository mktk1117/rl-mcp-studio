// The box you type into, the menus beside it, and sending what was typed.

import { el, on } from "../ui.js";
import { api } from "../api.js";
import { state, set, setView, subject } from "../state.js";

/** The box you type into. Exported because the Console layout hosts one too,
 *  and two of these would be two places to fix the next IME bug in. */
export function composer(status) {
  const disabled = !status.available || !subject(state);
  // Working, and therefore not able to take another message. It is not
  // disabled: the whole point of typing here is that you are watching the
  // agent do the wrong thing, and the text has to survive until it can go.
  const working = !disabled && !!state.agentBusy;
  const input = el("textarea", {
    class: "agent-input", rows: 2, disabled,
    // "unavailable" would be true and useless. The two reasons a message
    // cannot go are different problems with different fixes, and only one of
    // them is about the agent at all.
    placeholder: !status.available
      ? "signed out — sign in above"
      : disabled ? "select a run to ask about it" : "Ask about this run…",
    onkeydown: (e) => {
      // `isComposing` is the IME still deciding: this Enter is confirming a
      // candidate, not sending a message. Without the guard, Japanese input
      // sends half a sentence.
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); }
    },
  });
  // Keep what was typed across the repaints the live feed causes.
  input.value = state.draft || "";
  on(input, "input", (e) => { state.draft = e.currentTarget.value; });

  return el("div", { class: "agent-c" },
    // `agentError` is set by several different failures -- a send, a
    // decision, a missing sign-in. A message that fails to send has to say so
    // where you sent it from.
    state.agentError
      ? el("div", { class: "m-error", style: { marginBottom: "8px" } },
           state.agentError,
           el("button", { class: "act", style: { marginLeft: "8px" },
                          onclick: () => set({ agentError: "" }) }, "dismiss"))
      : null,
    // Send beside the box rather than under it: it acts on what is in the box,
    // and Stop takes its place while a turn is running, because those two are
    // never both the thing you want.
    // The button sits *inside* the box, at the corner you finish typing in,
    // the way Claude Code's does. A full-width primary button beside a
    // two-line textarea is the heaviest thing in a panel whose subject is the
    // conversation above it, and would compete with it.
    // What you are about to send with it. Above the box rather than beside
    // Send, because it is part of the message and not part of the button.
    state.attached
      ? el("div", { class: "attached" },
          el("img", { class: "attached-img", src: state.attached.url, alt: "attached" }),
          el("span", { class: "attached-note",
                       text: state.attached.label || "screenshot" }),
          el("span", { class: "sp" }),
          el("button", { class: "act", title: "do not send this picture",
                         onclick: () => window.__studioActions.dropAttachment() }, "×"))
      : null,
    el("div", { class: "agent-box" },
      input,
      working
        // Exactly while it is working, and nowhere else. Without it, ending a
        // runaway turn takes a terminal and a curl.
        //
        // Two buttons, because two different things are wanted of a turn
        // going wrong: end it, or end it *and say what instead*. Enter does
        // the second -- what you type here is the correction, and a box that
        // refused it until Stop had been pressed and the turn had ended would
        // lose the correction at the moment it is clearest.
        ? [el("button", { class: "agent-send interrupt", "aria-label": "interrupt and send",
                          title: "end the turn it is in and send this instead "
                               + "— or press enter",
                          onclick: () => send(input) },
              el("span", { class: "glyph", text: "\u21B5" })),
           el("button", { class: "agent-send stop", "aria-label": "stop",
                          title: "end the turn it is in. The conversation stays, "
                               + "so you can say what it should have done",
                          onclick: () => window.__studioActions.stopAgent() },
              el("span", { class: "glyph", text: "\u25A0" }))]
        : el("button", { class: "agent-send", "aria-label": "send",
                         title: "send — or press enter",
                         disabled: disabled || undefined,
                         onclick: () => send(input) },
             el("span", { class: "glyph", text: "\u21B5" }))),
    el("div", { class: "agent-row" },
      // How much runs without asking, which model runs it, and how much of
      // the studio's own text it was started with. Beside the composer
      // because they are properties of *the next thing you send* -- Claude
      // Code puts its mode here for the same reason.
      modeButton(status), modelButton(status), harnessButton(status),
      el("span", { style: { marginLeft: "auto" } }),
      // No "enter to send": the box is a text box beside a Send button, which
      // says it already. What is worth a line is the case where the honest
      // answer to "can I send this" is no.
      working
        ? el("span", { class: "hint-note",
                       text: "working — enter interrupts and sends; \u25A0 stops" })
        : state.sending
          ? el("span", { class: "hint-note", text: "ending its turn, then sending…" })
          : null));
}

/** How much the agent may do without asking.
 *
 * The list is the provider's -- `{id, label, note}` -- so a second provider
 * offering different modes needs no change here.
 */
function modeButton(status) {
  const modes = status.modes || [];
  if (!modes.length) return null;
  const current = modes.find(m => m.id === status.mode) || modes[0];
  const open = state.modeOpen;

  const button = el("button", {
    class: `modebtn m-${current.id}`, "aria-expanded": open ? "true" : "false",
    title: current.note,
    onclick: (e) => { e.stopPropagation(); set({ modeOpen: !open, modelOpen: false, harnessOpen: false, providerOpen: false }); },
  }, el("span", { class: "mode-dot" }), current.label,
     el("span", { class: "conv-caret", text: "\u25BE" }));
  if (!open) return el("span", { class: "menuwrap" }, button);

  return el("span", { class: "menuwrap" }, button,
    el("div", { class: "menu modemenu" },
      el("div", { class: "menu-h" },
        el("span", { class: "eyebrow", text: "how much runs without asking" }),
        // Not "from now on": the handler reads the mode per call, and the
        // open sessions are told too.
        el("span", { class: "menu-note",
                     text: "applies now, to this conversation as well" })),
      modes.map(m => el("button", {
        class: "mrow", "aria-current": m.id === status.mode ? "true" : null,
        onclick: () => { set({ modeOpen: false });
                         window.__studioActions.setAgentMode(m.id); },
      },
        el("span", { class: `mode-dot m-${m.id}` }),
        el("span", {}, el("span", { class: "mn", text: m.label }),
                       el("span", { class: "mr", text: m.note })),
        el("span", { class: "mc" })))));
}

/** Which model answers, and what it has spent.
 *
 * The usage sits on this button rather than in a strip of its own: it is a
 * fact *about the model that is answering*, and a second row of chrome above
 * a two-line composer is more than the number is worth.
 */
function modelButton(status) {
  const models = status.models || [];
  if (!models.length) return null;
  const current = models.find(m => m.id === status.model) || models[0];
  const open = state.modelOpen;
  const use = state.usage || null;

  const button = el("button", {
    class: "modelbtn", "aria-expanded": open ? "true" : "false",
    title: current.note || "",
    onclick: (e) => { e.stopPropagation(); set({ modelOpen: !open, modeOpen: false, harnessOpen: false, providerOpen: false }); },
  }, current.label,
     use && use.context ? el("small", { class: "usage", text: context(use) }) : null,
     el("span", { class: "conv-caret", text: "\u25BE" }));
  if (!open) return el("span", { class: "menuwrap" }, button);

  return el("span", { class: "menuwrap" }, button,
    el("div", { class: "menu modelmenu" },
      el("div", { class: "menu-h" },
        el("span", { class: "eyebrow", text: "model" }),
        el("span", { class: "menu-note",
                     text: "answers from the next message on, in this "
                         + "conversation too \u2014 nothing is lost" })),
      models.map(m => el("button", {
        class: "mrow", "aria-current": m.id === status.model ? "true" : null,
        onclick: () => { set({ modelOpen: false });
                         window.__studioActions.setAgentModel(m.id); },
      },
        el("span", {}, el("span", { class: "mn", text: m.label }),
                       el("span", { class: "mr", text: m.note || "" })),
        el("span", { class: "mc" })))));
}

/** How much of the studio's own text the agent is started with.
 *
 * The studio replaces Claude Code's system prompt with its own instructions,
 * and that is the right default here -- it is what makes the agent open a
 * record before it launches a run. It is also the thing to be able to put
 * down: when the agent behaves oddly, "is that the model or is that our
 * prompt" has no answer while the prompt cannot be removed, and somebody who
 * lives in Claude Code has their own instructions that the replacement is
 * exactly what stops reading normally.
 *
 * The menu ends in the way to *read* what any of these means, because a
 * choice between three descriptions of a prompt is a choice nobody can make
 * well without seeing the prompt.
 */
function harnessButton(status) {
  const list = status.harnesses || [];
  if (!list.length) return null;
  const current = list.find(h => h.id === status.harness) || list[0];
  const open = state.harnessOpen;

  const button = el("button", {
    class: `harnessbtn h-${current.id}`, "aria-expanded": open ? "true" : "false",
    title: `harness: ${current.note}`,
    onclick: (e) => { e.stopPropagation();
                      set({ harnessOpen: !open, modeOpen: false, modelOpen: false, providerOpen: false }); },
  }, current.label, el("span", { class: "conv-caret", text: "\u25BE" }));
  if (!open) return el("span", { class: "menuwrap" }, button);

  return el("span", { class: "menuwrap" }, button,
    el("div", { class: "menu harnessmenu" },
      el("div", { class: "menu-h" },
        el("span", { class: "eyebrow", text: "what it is started with" }),
        // Not "applies now". A system prompt is given to the CLI once, at
        // connect, and no control message replaces it -- so unlike the mode
        // and the model, this one cannot reach the conversation above.
        el("span", { class: "menu-note",
                     text: "the next conversation \u2014 an open one keeps the "
                         + "prompt it connected with" })),
      list.map(h => el("button", {
        class: "mrow", "aria-current": h.id === status.harness ? "true" : null,
        onclick: () => { set({ harnessOpen: false });
                         window.__studioActions.setAgentHarness(h.id); },
      },
        el("span", {}, el("span", { class: "mn", text: h.label }),
                       el("span", { class: "mr", text: h.note })),
        el("span", { class: "mc" }))),
      el("button", { class: "mrow mrow-more",
                     onclick: () => { set({ harnessOpen: false });
                                      window.__studioActions.openBriefing(); } },
        el("span", {}, el("span", { class: "mn", text: "Read what it is given" }),
                       el("span", { class: "mr",
                                    text: "the instructions, the tool list and "
                                        + "this run's context, as sent" })))));
}

/** How full the context is, as a fraction rather than a raw token count.
 *
 * "34k" answers nothing on its own -- the question is how much room is left,
 * and that needs the window beside it. */
function context(use) {
  // The last request's size when the provider says it; the turn's total is a
  // cost, not a size, and only the fallback.
  const used = Number(use.context_tokens || use.used || 0), size = Number(use.context || 0);
  if (!size) return "";
  const k = (n) => n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
  return `${k(used)}/${k(size)}`;
}

async function send(input) {
  // The draft is the authority, for the reason in `ui.js`: `input` here is
  // whichever copy of the box this closure was built with, and after a morph
  // that is the copy that was thrown away -- always empty, however much was
  // typed into the one on screen.
  const live = document.querySelector(".agent-input");
  const text = ((live && live.value) || state.draft || "").trim();
  const attached = state.attached;
  // A picture on its own is a complete thing to say. Requiring words with it
  // would be the app being pedantic about a message that is already clear.
  if (!text && !attached) return;

  // The Console prints `/metrics`, `/tb`, `/diff` under this box, which reads
  // as an invitation to type them -- and without this, typing one would send
  // it to the agent as a question. A bare slash-word that names a view
  // switches to it.
  // `setView` ignores anything that is not one of the nine, so a message that
  // merely starts with a slash still goes to the agent.
  const slash = /^\/(\w+)$/.exec(text);
  if (slash) {
    const before = state.view;
    setView(slash[1]);
    if (state.view !== before) {
      if (live) live.value = "";
      state.draft = "";
      set({});
      return;
    }
  }
  // Typed over a turn that is still running. The server answers a message
  // into a running turn with 409, and refusing is the wrong answer to the
  // commonest reason for typing here: you type *because* the agent is going
  // wrong, and the message is the correction.
  // So the turn is interrupted and this is sent once it has ended. The
  // server does the ordering, so the correction lands under "You stopped
  // it." and after whatever the model had already produced.
  //
  // Still not a queue: nothing waits for the turn to finish on its own. The
  // turn is ended, which is what typing over it asked for.
  const interrupting = !!state.agentBusy;
  if (live) live.value = "";
  state.draft = "";
  // The attachment is cleared with the box, and put back with it if the send
  // fails -- losing the picture you just framed would be worse than losing
  // the sentence, because it cannot be retyped.
  set({ agentError: "", attached: null,
        // Mirrors `stopAgent`: the panel stops saying "working…" now, and the
        // tail of the interrupted turn does not put it back. `sending` is the
        // ghost bubble, on screen until the server echoes the message.
        sending: interrupting ? text : "",
        agentBusy: interrupting ? false : state.agentBusy,
        agentStopping: interrupting ? true : state.agentStopping });
  try {
    // The composer is one box over two doors -- see `askAgent` in main.js.
    const who = subject(state);
    if (who.startsWith("task:")) await api.buildAgent(state.task, text, state.thread, interrupting);
    // `who`, not `state.runId`: the composer types into the conversation the
    // panel is showing, which is the pinned one when you have opened one.
    else await api.agentSend(who, text, state.thread,
                             attached ? attached.url : undefined, interrupting);
    // ...and speaking into a conversation keeps you in it while you go and
    // look at whatever it is doing.
    set({ convPin: who });
  } catch (err) {
    // The box was emptied optimistically, which is right when the send works
    // and destructive when it does not: "still working on the last message" is
    // a *retry later*, and must not arrive having thrown the message away.
    // Put it back, in the store and in the live node, the way a failed
    // feedback write does.
    state.draft = text;
    const box = document.querySelector(".agent-input");
    if (box) box.value = text;
    set({ agentError: err.message, attached });
  } finally {
    set({ sending: "" });
  }
}
