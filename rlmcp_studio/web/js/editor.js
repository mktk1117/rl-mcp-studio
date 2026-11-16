// Monaco, hosted so that repaints cannot destroy it.
//
// The studio repaints its whole tree whenever the live feed moves, which is
// several times a minute. An editor recreated that often would lose the
// cursor, the scroll, the undo stack and any unsaved edit — so the editor's
// container is created once and *re-parented* into each fresh render rather
// than rebuilt. Moving a DOM node keeps the node, and Monaco keeps its state.
//
// Monaco is a pinned browser dependency (`rlmcp_studio/vendor.py`): the min/vs
// build is prebuilt AMD and loads from a script tag, so a local tool with no Node
// toolchain and no network can still have a real editor.

import { el } from "./ui.js";

const VENDOR = "/static/vendor/monaco";

let loading = null;
let monaco = null;
let host = null;          // the div Monaco owns, created once
let editor = null;
let currentPath = "";
let onSaveHook = null;
let onAskHook = null;
let dirty = false;
let listeners = new Set();

export function onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function announce() { for (const fn of listeners) fn(); }

export function isDirty() { return dirty; }
export function openPath() { return currentPath; }

/** Load the vendored AMD bundle once. */
export function load() {
  if (monaco) return Promise.resolve(monaco);
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `${VENDOR}/loader.js`;
    script.onload = () => {
      const require = window.require;
      require.config({ paths: { vs: VENDOR } });
      // The worker is cross-origin-safe here because it is same-origin, but
      // Monaco still wants an explicit factory when not bundled.
      window.MonacoEnvironment = {
        getWorkerUrl: () => `${VENDOR}/editor/editor.worker.js`,
      };
      require(["vs/editor/editor.main"], () => {
        monaco = window.monaco;
        defineThemes();
        resolve(monaco);
      });
    };
    script.onerror = () => reject(new Error("could not load the vendored editor"));
    document.head.append(script);
  });
  return loading;
}

// Monaco's own themes ignore our tokens, so the editor would be the one panel
// that does not follow the scheme. These map onto the same palette.
//
// The ground is `--paper` rather than `--sunken` for the light scheme's sake:
// `rules: []` means the syntax colours are inherited from Monaco's `vs`, which
// was designed against white. On the app's sunken ground those blues and
// greens go muddy. Dark is unaffected — there `--paper` and `--sunken` carry
// the same value.
function defineThemes() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  for (const [name, base] of [["studio-dark", "vs-dark"], ["studio-light", "vs"]]) {
    monaco.editor.defineTheme(name, {
      base, inherit: true, rules: [],
      colors: {
        "editor.background": v("--paper") || "#0B1012",
        "editor.foreground": v("--text") || "#DDE7E9",
        "editorLineNumber.foreground": v("--dim") || "#5C6B72",
        "editorLineNumber.activeForeground": v("--accent") || "#8E9BFF",
        "editor.selectionBackground": v("--accent-soft") || "#2C3350",
        "editor.lineHighlightBackground": v("--raised") || "#1C2529",
        "editorIndentGuide.background1": v("--line-soft") || "#1F282D",
        "editorGutter.background": v("--paper") || "#0B1012",
      },
    });
  }
}

export function applyTheme(scheme) {
  if (!monaco) return;
  defineThemes();                       // tokens may have changed with the scheme
  monaco.editor.setTheme(scheme === "light" ? "studio-light" : "studio-dark");
}

const LANGUAGES = {
  py: "python", json: "json", jsonl: "json", yaml: "yaml", yml: "yaml",
  md: "markdown", toml: "ini", cfg: "ini", ini: "ini", sh: "shell",
  js: "javascript", css: "css", html: "html", txt: "plaintext",
};

/** The element to place in the render tree. Created once, moved thereafter. */
export function mount() {
  if (!host) {
    host = el("div", { class: "monaco-host" });
  }
  return host;
}

export function relayout() {
  if (editor) editor.layout();
}

/**
 * Show a file. Reuses the editor and swaps its model, so opening a second file
 * keeps the instance (and its undo history, per model).
 */
export function show(file, { readOnly = true, scheme = "dark", onSave, onAsk } = {}) {
  onSaveHook = onSave || onSaveHook;
  onAskHook = onAsk || onAskHook;
  if (!monaco || !host) return;

  const language = LANGUAGES[(file.path.split(".").pop() || "").toLowerCase()] || "plaintext";
  const uri = monaco.Uri.file(file.path);
  let model = monaco.editor.getModel(uri);
  if (!model) {
    model = monaco.editor.createModel(file.lines.join("\n"), language, uri);
  }

  if (!editor) {
    editor = monaco.editor.create(host, {
      model,
      readOnly,
      automaticLayout: true,
      theme: scheme === "light" ? "studio-light" : "studio-dark",
      fontSize: 12.5,
      fontFamily: getComputedStyle(document.documentElement)
        .getPropertyValue("--mono").trim() || "monospace",
      lineNumbersMinChars: 4,
      minimap: { enabled: true, renderCharacters: false },
      scrollBeyondLastLine: false,
      renderWhitespace: "selection",
      smoothScrolling: true,
      padding: { top: 8, bottom: 8 },
    });

    editor.onDidChangeModelContent(() => {
      if (editor.getRawOptions().readOnly) return;
      if (!dirty) { dirty = true; announce(); }
    });

    // Ctrl/Cmd+S saves; Ctrl/Cmd+K asks the agent about the selection. Both are
    // what a hand already reaches for, so they should not need the mouse.
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS,
      () => { if (onSaveHook) onSaveHook(); });
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK,
      () => { if (onAskHook) onAskHook(selection()); });

    // In-file definition jumps. A real cross-file jump needs a language server;
    // this finds `def name` / `class name` in the open file, which is most of
    // what you want while reading one reward module.
    monaco.languages.registerDefinitionProvider("python", {
      provideDefinition(model, position) {
        const word = model.getWordAtPosition(position);
        if (!word) return null;
        const matches = model.findMatches(
          `^\\s*(def|class)\\s+${word.word}\\b`, true, true, false, null, true);
        if (!matches.length) return null;
        return matches.map(m => ({ uri: model.uri, range: m.range }));
      },
    });
  } else if (editor.getModel() !== model) {
    editor.setModel(model);
  }

  editor.updateOptions({ readOnly });
  if (currentPath !== file.path) {
    currentPath = file.path;
    dirty = false;
    announce();
  }
  // A line asked for before this file existed. Held until the file is here:
  // revealing on a timer races the fetch, and a click on a search hit would
  // open the file at the top with the request simply lost.
  if (pendingLine && pendingFile === file.path) {
    const line = pendingLine;
    pendingLine = 0;
    pendingFile = "";
    // After the model is in place and Monaco has measured itself. A timer, not
    // a frame callback: a frame never arrives while the window is not being
    // composited, and the jump would simply be dropped -- the same trap
    // `state.js` documents for repaints.
    setTimeout(() => reveal(line), 0);
  }
  return editor;
}

export function selection() {
  if (!editor) return null;
  const model = editor.getModel();
  const sel = editor.getSelection();
  if (!model || !sel) return null;
  const from = sel.startLineNumber;
  const to = sel.endLineNumber;
  const text = sel.isEmpty()
    ? model.getLineContent(from)
    : model.getValueInRange(sel);
  return { from, to, text, path: currentPath };
}

export function contents() {
  return editor && editor.getModel() ? editor.getModel().getValue() : "";
}

export function markSaved() { dirty = false; announce(); }

let pendingLine = 0;
let pendingFile = "";

/** Reveal `line` once `path` is the file on screen, however long that takes. */
export function revealWhenLoaded(path, line) {
  if (!line) return;
  if (currentPath === path && editor && editor.getModel()) {
    reveal(line);
    return;
  }
  pendingFile = path;
  pendingLine = line;
}

export function reveal(line) {
  if (editor) {
    editor.revealLineInCenter(line);
    editor.setPosition({ lineNumber: line, column: 1 });
    editor.focus();
  }
}
