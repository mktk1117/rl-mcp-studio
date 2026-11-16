// The task every id-bearing call carries.
//
// Record ids are per *store*, so on a studio with two records roots an id
// alone can name two different runs -- see `Library.find`. The client always
// knows which task it is showing, so it says so, and the server resolves the
// id inside that task. Read here rather than passed by every caller: there is
// exactly one answer at any moment, and threading it through twenty call sites
// would only create places to forget it.
import { state as store } from "./state.js";

// Every read goes through here. The client never guesses a shape: the backend
// derives all of it from rlmcp, and this module only carries it.

// The token is minted per process and the page at `/` is what hands it to a
// browser (guard.py). So a tab that was open across a restart holds the old
// process's cookie: every fetch answers 401, the feed is refused, and the pane
// it opens next is empty -- the previous conversations look missing -- with
// nothing on screen saying why. Reloading is the sign-in: `/` sets the new
// cookie. Once per half-minute, because a 401 that survives a reload is not a
// stale cookie, and reloading forever would hide whatever it is.
const RELOGIN = "rlmcp.relogin";

function refused(status) {
  if (status !== 401) return false;
  let last = 0;
  try { last = Number(sessionStorage.getItem(RELOGIN) || 0); } catch { /* private window */ }
  if (Date.now() - last < 30000) return false;
  try { sessionStorage.setItem(RELOGIN, String(Date.now())); } catch { /* ditto */ }
  window.location.reload();
  return true;
}

async function get(path, params = {}) {
  const url = new URL(path, window.location.origin);
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined) url.searchParams.set(k, v);
  }
  const response = await fetch(url);
  if (!response.ok) {
    refused(response.status);
    throw new Error(`${path} → ${response.status}`);
  }
  return response.json();
}

const post = (path, body) => send("POST", path, body);

async function send(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    refused(response.status);
    throw new Error(data.error || `${path} → ${response.status}`);
  }
  return data;
}

const here = () => store.task || undefined;

export const api = {
  config: () => get("/api/config"),
  doctor: () => get("/api/doctor"),
  gpu: () => get("/api/gpu"),
  settings: () => get("/api/settings"),
  saveSettings: (body) => send("PATCH", "/api/settings", body),
  tasks: () => get("/api/tasks"),
  unrecorded: () => get("/api/sessions/unrecorded"),
  adopt: (session, slug, hypothesis) =>
    post("/api/sessions/adopt", { session, slug, hypothesis }),
  runs: (task) => get("/api/runs", { task }),
  build: (task) => get("/api/build", { task }),
  newTask: (body) => post("/api/build/new", body),
  preview: (task, action, how = {}) =>
    post("/api/build/preview", { task, action, ...how }),
  buildCheck: (task, how = {}) => post("/api/build/check", { task, ...how }),
  // The whole suite: both checks, the readings over them and the frames.
  // It answers before it has finished, so nothing here waits on it.
  buildVerify: (task, how = {}) => post("/api/build/verify", { task, ...how }),
  stopVerify: (task) => post("/api/build/verify", { task, stop: true }),
  launch: (task, body) => post("/api/build/launch", { task, ...body }),
  // Recipes: what `rlmcp recipe build` wrote beside the records, and the
  // button that trains one again under a record named for it.
  recipes: (task) => get("/api/build/recipes", { task }),
  buildRecipe: (task, record) => post("/api/build/recipe", { task, record }),
  launchRecipe: (task, recipe, anyway = false) =>
    post("/api/build/launch", { task, recipe, anyway }),
  launches: () => get("/api/build/launches"),
  cancelLaunch: (job) => post("/api/build/launch/cancel", { job }),
  buildCommand: (task, cmd, args = {}, opts = {}) =>
    post("/api/build/command", { task, cmd, args, ...opts }),
  recordGraph: (task) => get("/api/records/graph", { task }),
  paramHistory: (task) => get("/api/params/history", { task }),
  nextRecord: (body) => post("/api/records/next", { ...body, task: here() }),
  run: (id, task) => get(`/api/run/${encodeURIComponent(id)}`, { task: task || here() }),
  session: (path, tail) => get("/api/session", { path, tail }),
  clips: (id) => get(`/api/run/${encodeURIComponent(id)}/clips`, { task: here() }),
  // The markdown belonging to a run and to its task. `record_id` is optional:
  // a task being written has no run and still has notes.
  docs: (run) => get("/api/docs", { record_id: run || undefined, task: here() }),
  doc: (path) => get("/api/doc", { path }),
  // What the task *is*: the report an agent wrote about it, and the facts the
  // studio measured. Task-scoped, so it takes the task rather than a run.
  overview: (task) => get("/api/overview", { task: task || here() }),
  writeOverview: (task, note) =>
    post("/api/overview/write", { task: task || here(), note }),
  // The task library. `card` takes the task explicitly rather than reading
  // `here()`: the shelf can ask about a task you have not opened.
  library: () => get("/api/library"),
  card: (task) => get("/api/library/card", { task: task || here() }),
  // The whole history of a task as one file. `plan` says what it would weigh
  // under each media choice; `build` writes it and answers with where.
  reportPlan: (task) => get("/api/report/plan", { task: task || here() }),
  // The page arrives whole, as HTML: it is the studio's own panels serialised
  // by report.js, with its media written in, and can be hundreds of MB.
  saveReport: async (task, media, html) => {
    const url = new URL("/api/report/save", window.location.origin);
    url.searchParams.set("task", task || here() || "");
    url.searchParams.set("media", media);
    const response = await fetch(url, {
      method: "POST", headers: { "content-type": "text/html; charset=utf-8" }, body: html });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `report save → ${response.status}`);
    return data;
  },
  tensorboard: (id) => get(`/api/run/${encodeURIComponent(id)}/tensorboard`, { task: here() }),
  events: (path, tail) => get("/api/events", { path, tail }),
  trace: (path) => get("/api/trace", { path }),
  addAsset: (run, path, kind, caption) =>
    post(`/api/run/${encodeURIComponent(run)}/asset?task=${encodeURIComponent(here() || "")}`,
         { path, kind, caption }),
  traces: (path) => get("/api/traces", { path }),
  command: (run, cmd, args = {}, opts = {}) =>
    post(`/api/run/${encodeURIComponent(run)}/command?task=${encodeURIComponent(here() || "")}`,
         { cmd, args, ...opts }),
  // Playing a finished run's policy. The trainer is usually gone by the time
  // you want this, which is why it is a process of its own rather than a
  // command sent to one.
  play: (run, body = {}) =>
    post(`/api/run/${encodeURIComponent(run)}/play?task=${encodeURIComponent(here() || "")}`, body),
  playState: (run) =>
    get(`/api/run/${encodeURIComponent(run)}/play`, { task: here() }),
  parameters: (run, contains) =>
    get(`/api/run/${encodeURIComponent(run)}/parameters`, { contains, task: here() }),
  artifactUrl: (path) => `/api/artifact?path=${encodeURIComponent(path)}`,
  diff: (run, against) => get("/api/diff", { record_id: run, against, task: here() }),
  // The ancestry fold. The task disambiguates the id; the fold
  // itself deliberately reads the whole store, because ancestry crosses tasks.
  recipe: (run) => get("/api/recipe", { record_id: run, task: here() }),
  sourceTree: (root) => get("/api/source/tree", { root }),
  sourceFile: (path) => get("/api/source/file", { path }),
  sourceSearch: (q) => get("/api/source/search", { q }),
  saveFile: (path, text, expect_size) =>
    send("PUT", "/api/source/file", { path, text, expect_size }),
  // Closing a run out. The verdict is judged by rl-mcp, and its refusal comes
  // back as the error on this call.
  closeRun: (run, body) =>
    post(`/api/run/${encodeURIComponent(run)}/close?task=${encodeURIComponent(here() || "")}`,
         body),
  annotate: (run, body) =>
    post(`/api/run/${encodeURIComponent(run)}/annotate?task=${encodeURIComponent(here() || "")}`,
         body),
  // `interrupt` is the composer typing over a turn that is still running: the
  // server ends that turn first and sends this once it has ended.
  buildAgent: (task, text, thread, interrupt = false) =>
    post("/api/build/agent", { task, text, thread, interrupt }),
  // `force` asks the server to re-read `claude auth status` rather than answer
  // from its few-second cache -- for the "check again" button, which exists to
  // notice a sign-in that happened somewhere else.
  agentStatus: (force = false) => get("/api/agent", force ? { force: 1 } : {}),
  agentChoose: (body) => post("/api/agent/settings", body),
  agentLogin: (mode) => post("/api/agent/auth/login", { mode }),
  agentLoginCode: (code) => post("/api/agent/auth/code", { code }),
  agentLoginCancel: () => post("/api/agent/auth/cancel", {}),
  agentTranscript: (run, thread) => get(`/api/agent/${encodeURIComponent(run)}`, { thread }),
  agentConversations: (run) => get("/api/agent/conversations", { record_id: run }),
  // The apps' own sessions about this subject's directory, and continuing
  // one of them here. See `app.api_agent_sessions` / `api_agent_attach`.
  agentSessions: (run) =>
    get(`/api/agent/${encodeURIComponent(run)}/sessions`, { task: here() }),
  agentAttach: (run, session_id, thread = "main", provider = "claude") =>
    post(`/api/agent/${encodeURIComponent(run)}/attach?task=${encodeURIComponent(here() || "")}`,
         { session_id, thread, provider }),
  // What a conversation was started with, section by section — or what one
  // opened now would be. See `app.api_agent_briefing`.
  agentBriefing: (run, thread) =>
    get(`/api/agent/${encodeURIComponent(run)}/briefing`, { thread, task: here() }),
  // `image` is a data url: a video frame and an SVG chart are not files, they
  // exist only in the page until something draws them to a canvas.
  agentSend: (run, text, thread, image, interrupt = false) =>
    post(`/api/agent/${encodeURIComponent(run)}/message?task=${encodeURIComponent(here() || "")}`,
         { text, thread, image, interrupt }),
  agentDecide: (run, request_id, decision, note = "", thread = "main") =>
    post(`/api/agent/${encodeURIComponent(run)}/decide`, { request_id, decision, note, thread }),
  // `run` is the conversation's subject — a record id, or `task:<id>` in Build.
  agentStop: (run, thread = "main") =>
    post(`/api/agent/${encodeURIComponent(run)}/stop`, { thread }),
};

// One socket, one envelope, dispatched on `kind` through a registry — so a new
// event kind is a new handler, never an edit to a switch statement.
export function connect({ onEvent, onOpen, onClose, lastSeq = () => 0 }) {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  let socket, retry = 0, closed = false;

  function open() {
    socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
    socket.addEventListener("open", () => {
      retry = 0;
      socket.send(JSON.stringify({ kind: "resume", seq: lastSeq() }));
      onOpen && onOpen();
    });
    socket.addEventListener("message", (e) => {
      let envelope;
      try { envelope = JSON.parse(e.data); } catch { return; }
      onEvent && onEvent(envelope);
    });
    socket.addEventListener("close", () => {
      onClose && onClose();
      if (closed) return;
      retry = Math.min(retry + 1, 6);
      setTimeout(open, 250 * 2 ** retry);   // back off, then resume from lastSeq
      // A handshake the guard refuses closes like a server that went away,
      // and this tab makes no request on its own until somebody clicks. Ask
      // once per attempt: a 401 here is the restart, and `get` signs back in.
      get("/api/config").catch(() => { /* down, or refused and handled */ });
    });
  }

  open();
  return { close() { closed = true; socket && socket.close(); } };
}
