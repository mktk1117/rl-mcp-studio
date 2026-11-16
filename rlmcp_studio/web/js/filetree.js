// Finding a file, and finding a line.
//
// A flat list with a substring filter answers "which file is called
// rewards.py" and nothing else, and the question people actually arrive with is "where is the thing that computes foot clearance".
//
// So there are two searches, and they are different questions:
//
//   **names** — fuzzy, ranked, in the browser. The tree is already here, so it
//   costs no round trip and can update on every keystroke. `mytask/rew`
//   finds `my_tasks/env/rewards.py`, and the characters that matched are
//   marked, so it is clear *why* a result is a result.
//
//   **in files** — a request. Ripgrep where it exists, a bounded Python scan
//   where it does not; either way the answer is lines, with the file grouped
//   above them, and clicking one opens the file at that line.
//
// Keyboard throughout: type, arrow, Enter. A file picker you have to aim at
// with a mouse is a file picker you stop using.

import { el, on } from "./ui.js";
import { state, set } from "./state.js";

// ── fuzzy matching ────────────────────────────────────────────────────────
//
// Subsequence matching, the shape every editor's file picker uses: every
// character of the query appears in order, not necessarily adjacent. The score
// rewards what a person means by "close" — matching in the file's name rather
// than deep in its path, matching at a word boundary, and matching in a run
// rather than scattered.

export function fuzzy(query, target) {
  if (!query) return { score: 0, hits: [] };
  const needle = query.toLowerCase();
  const hay = target.toLowerCase();
  const hits = [];
  let at = 0;
  let score = 0;
  let streak = 0;

  for (const char of needle) {
    if (char === " ") continue;
    const found = hay.indexOf(char, at);
    if (found === -1) return null;                 // not a match at all
    const previous = found ? hay[found - 1] : "/";
    const boundary = "/_-. ".includes(previous);
    streak = found === at ? streak + 1 : 0;
    score += 1 + streak * 3 + (boundary ? 6 : 0);
    hits.push(found);
    at = found + 1;
  }

  // The name matters more than the path it sits in, and a short path that
  // matches beats a long one that also matches.
  const nameAt = target.lastIndexOf("/") + 1;
  if (hits[0] >= nameAt) score += 12;
  score -= Math.min(target.length / 12, 12);
  return { score, hits };
}

/** The tree's files, ranked against a query, best first. */
export function rank(entries, query, limit = 60) {
  const out = [];
  for (const entry of entries) {
    if (entry.dir) continue;
    const match = fuzzy(query, entry.path);
    if (match) out.push({ entry, ...match });
  }
  out.sort((a, b) => b.score - a.score || a.entry.path.length - b.entry.path.length);
  return out.slice(0, limit);
}

/** The text of `value`, with the characters at `hits` marked. */
export function highlight(value, hits, offset = 0) {
  const wrap = el("span", {});
  const marked = new Set(hits.map(h => h - offset));
  let run = "";
  let runMarked = false;
  const flush = () => {
    if (!run) return;
    wrap.append(runMarked ? el("b", { class: "hit", text: run })
                          : document.createTextNode(run));
    run = "";
  };
  for (let i = 0; i < value.length; i++) {
    const isHit = marked.has(i);
    if (isHit !== runMarked) { flush(); runMarked = isHit; }
    run += value[i];
  }
  flush();
  return wrap;
}

// ── the tree ──────────────────────────────────────────────────────────────
//
// Built once per payload into parent/child arrays, so deciding what is visible
// is a walk from the roots rather than, for every entry, a scan of every
// collapsed path, which would be O(n²).

function build(entries, root) {
  const nodes = new Map();
  const roots = [];
  for (const entry of entries) {
    nodes.set(entry.path, { ...entry, children: [] });
  }
  for (const entry of entries) {
    const node = nodes.get(entry.path);
    const parentPath = entry.path.slice(0, entry.path.lastIndexOf("/"));
    const parent = nodes.get(parentPath);
    if (parent && parentPath !== entry.path) parent.children.push(node);
    else roots.push(node);
  }
  // Directories first, then files; alphabetical within each. The server sorts
  // already, but a tree that is assembled has to state its own order.
  const order = (list) => {
    list.sort((a, b) => (b.dir - a.dir) || a.name.localeCompare(b.name));
    list.forEach(node => order(node.children));
  };
  order(roots);
  return roots;
}

function visible(roots, collapsed, out = [], depth = 0) {
  for (const node of roots) {
    out.push({ node, depth });
    if (node.dir && !collapsed[node.path]) visible(node.children, collapsed, out, depth + 1);
  }
  return out;
}

// ── the pane ──────────────────────────────────────────────────────────────
const MODES = [
  { id: "name", label: "names", hint: "fuzzy: mytask/rew finds my_tasks/env/rewards.py" },
  { id: "text", label: "in files", hint: "every line containing what you type" },
];

export function filePicker(payload, actions) {
  const query = state.fileFilter || "";
  const mode = state.fileMode || "name";
  const roots = build(payload.entries || [], payload.root);

  const search = el("input", {
    class: "ftree-filter", type: "text",
    placeholder: mode === "text" ? "search in files…" : "find a file…",
    value: query,
    onkeydown: (e) => onKey(e, payload),
  });
  on(search, "input", (e) => actions.typeFilter(e.target.value));

  const head = el("div", { class: "ftree-head" },
    search,
    el("div", { class: "ftree-chips" },
      MODES.map(m => el("button", {
        "aria-pressed": mode === m.id ? "true" : "false", title: m.hint,
        // Switching mode with something already typed has to *answer* in the
        // new mode. Only setting the mode would leave the old answer on screen
        // under the new label, which reads as the search being broken.
        onclick: () => {
          set({ fileMode: m.id, filePicked: 0, fileSearch: null });
          if (state.fileFilter) actions.typeFilter(state.fileFilter);
        },
      }, m.label))),
    el("span", { class: "ftree-count", text: countLabel(payload, mode, query) }));

  const list = el("div", { class: "ftree-list" });
  if (query && mode === "text") textResults(list, actions);
  else if (query) nameResults(list, payload, query, actions);
  else treeRows(list, roots, actions);

  return el("div", { class: "ftree" }, head, list);
}

function countLabel(payload, mode, query) {
  const files = (payload.entries || []).filter(e => !e.dir).length;
  if (!query) return `${files} files`;
  if (mode === "text") {
    const found = state.fileSearch;
    if (!found) return "searching…";
    if (found.error) return "search failed";
    return `${found.count}${found.truncated ? "+" : ""} lines in ${found.files.length} files`;
  }
  return `${rank(payload.entries, query).length} of ${files}`;
}

function treeRows(list, roots, actions) {
  const collapsed = state.collapsed || {};
  for (const { node, depth } of visible(roots, collapsed)) {
    const open = node.dir && !collapsed[node.path];
    list.append(el("button", {
      class: `fnode${node.dir ? " dir" : ""}`,
      style: { paddingLeft: `${8 + depth * 13}px` },
      title: node.path,
      "aria-current": node.path === state.filePath ? "true" : null,
      onclick: node.dir
        ? () => set({ collapsed: { ...collapsed, [node.path]: open } })
        : () => actions.openFile(node.path),
    },
      el("span", { class: `tw${node.dir && !open ? " closed" : ""}`,
                   text: node.dir ? "▾" : "" }),
      el("span", { text: node.name }),
      node.dir ? el("span", { class: "fcount", text: String(node.children.length) })
               : null));
  }
}

function nameResults(list, payload, query, actions) {
  const results = rank(payload.entries, query);
  const picked = Math.min(state.filePicked || 0, Math.max(results.length - 1, 0));
  if (!results.length) {
    list.append(el("div", { class: "empty", text: `no file matches “${query}”` }));
    return;
  }
  const root = (payload.root || "") + "/";
  results.forEach((result, index) => {
    const relative = result.entry.path.startsWith(root)
      ? result.entry.path.slice(root.length) : result.entry.path;
    const offset = result.entry.path.length - relative.length;
    // Name first, directory after it and dimmed. One line of full path per row
    // runs off the side of a 200 px pane and takes the file name with it -- the
    // one part of the row anybody reads -- behind a horizontal scrollbar.
    const cut = relative.lastIndexOf("/") + 1;
    list.append(el("button", {
      class: "fnode found", "aria-current": index === picked ? "true" : null,
      title: result.entry.path,
      onclick: () => actions.openFile(result.entry.path),
    },
      el("span", { class: "fname" }, highlight(relative.slice(cut), result.hits, offset + cut)),
      cut ? el("span", { class: "fdir" },
               highlight(relative.slice(0, cut - 1), result.hits, offset)) : null));
  });
}

function textResults(list, actions) {
  const found = state.fileSearch;
  if (!found) { list.append(el("div", { class: "empty", text: "searching…" })); return; }
  if (found.error) { list.append(el("div", { class: "m-error", text: found.error })); return; }
  if (!found.ok) {
    list.append(el("div", { class: "empty", text: found.reason || "nothing to search" }));
    return;
  }
  if (!found.files.length) {
    list.append(el("div", { class: "empty", text: `nothing contains “${found.query}”` }));
    return;
  }
  for (const file of found.files) {
    list.append(el("div", { class: "fhit-file" },
      el("span", { text: file.path.split("/").slice(-2).join("/") }),
      el("span", { class: "fcount", text: String(file.lines.length) })));
    for (const hit of file.lines) {
      list.append(el("button", {
        class: "fhit", title: `${file.path}:${hit.line}`,
        onclick: () => actions.openFile(file.path, hit.line),
      },
        el("span", { class: "fhit-n", text: String(hit.line) }),
        highlight(hit.text.trim(),
                  hitRange(hit, found.query), 0)));
    }
  }
  if (found.truncated) {
    list.append(el("div", { class: "ftree-more", text: found.reason }));
  }
}

function hitRange(hit, query) {
  // Where the query sits in the trimmed line, so the same highlighter can mark
  // it. Recomputed rather than trusting the column, which counts from the
  // untrimmed line.
  const at = hit.text.trim().toLowerCase().indexOf((query || "").toLowerCase());
  if (at === -1) return [];
  return Array.from({ length: query.length }, (_, i) => at + i);
}

// ── keyboard ──────────────────────────────────────────────────────────────
function onKey(event, payload) {
  if (event.isComposing || event.keyCode === 229) return;   // the IME is deciding
  const query = state.fileFilter || "";
  const mode = state.fileMode || "name";

  if (event.key === "Escape") {
    event.preventDefault();
    set({ fileFilter: "", filePicked: 0, fileSearch: null });
    return;
  }
  if (mode !== "name" || !query) return;

  const results = rank(payload.entries, query);
  if (!results.length) return;
  const picked = Math.min(state.filePicked || 0, results.length - 1);

  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    set({ filePicked: (picked + step + results.length) % results.length });
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    window.__studioActions.openFile(results[picked].entry.path);
  }
}
