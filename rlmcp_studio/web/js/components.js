// Components that every layout shares. Each one takes what it needs and makes
// no assumption about its container — no fixed widths, no "I sit beside a
// transcript". That is what lets the same component appear in all three.
//
// They live in `components/`, one module per area; this module is the one
// place layouts import them from.

export { runRail, noRunsYet, nothingHere, machineCard, notYet } from "./components/rail.js";
export { newTaskOverlay, adoptOverlay, nextRecordOverlay } from "./components/overlays.js";
export { verbs, framesCard } from "./components/frames.js";
export { clipsCard, recordVideo } from "./components/clips.js";
export { measuredCard, recordBrief, claimList } from "./components/record.js";
export { robotCard, viserSlot, reloadViserFrame } from "./components/robot.js";
export { gpuCells, statusbar } from "./components/statusbar.js";
export { rankMetrics, headlineStats, headlineNames, metricCards,
         headlineConfig, openable, chartOverlay } from "./components/metrics.js";
export { closeCard, feedbackCard } from "./components/close.js";

// The agent panel lives in its own module; re-exported so layouts import one
// place regardless of how big it grows.
export { agentPanel } from "./agent.js";
