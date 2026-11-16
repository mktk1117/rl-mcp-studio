// Actions for building a task: the preview and its checks, a new task, and
// launching a run.

import { state, set, setView } from "../state.js";
import { api } from "../api.js";
import { refreshBuild } from "../build.js";
import { actions } from "./actions-app.js";
import { reloadRuns, selectRun, selectTask } from "./load.js";
import { closeMenu } from "./chrome.js";

export const buildActions = {
  // ── the env builder ───────────────────────────────────────────────────
  //
  // Every one of these ends in `refreshBuild()` rather than trusting the reply:
  // the reply describes the moment the command returned, and a preview that is
  // "starting" then is holding four seconds later. One source of truth for the
  // panel, polled, is simpler than three that can disagree.
  preview: async (action) => {
    if (!state.task) return;
    set({ buildBusy: action, buildError: "" });
    try {
      await api.preview(state.task, action);
    } catch (err) {
      set({ buildError: err.message });
    } finally {
      set({ buildBusy: "" });
      await refreshBuild();
    }
  },
  runCheck: async () => {
    if (!state.task) return;
    // Not marked busy: the check takes seconds and the panel says "checking…"
    // from the server's own state, which survives a reload of this page.
    set({ buildError: "" });
    try {
      await api.buildCheck(state.task);
    } catch (err) {
      set({ buildError: err.message });
    }
    await refreshBuild();
  },
  // The whole suite. Not marked busy either, and for a stronger reason than
  // the check's: it takes minutes, the server owns whether it is running, and
  // a flag in this tab would say "not running" to a second tab watching the
  // same task.
  runVerify: async () => {
    if (!state.task) return;
    set({ buildError: "" });
    try {
      await api.buildVerify(state.task);
    } catch (err) {
      set({ buildError: err.message });
    }
    await refreshBuild();
  },
  stopVerify: async () => {
    if (!state.task) return;
    try {
      await api.stopVerify(state.task);
    } catch (err) {
      set({ buildError: err.message });
    }
    await refreshBuild();
  },
  previewView: async (on) => {
    if (!state.task) return;
    set({ buildBusy: "view", buildError: "" });
    try {
      const answer = await api.buildCommand(state.task, "live_view",
                                            { enabled: !!on }, { confirmed: true });
      if (!answer.ok) throw new Error(answer.error || "the preview refused");
    } catch (err) {
      set({ buildError: err.message });
    } finally {
      set({ buildBusy: "" });
      await refreshBuild();
    }
  },
  // ── beginning a task ──────────────────────────────────────────────────
  openNewTask: () => { closeMenu(); set({ newOpen: true, newError: "" }); },
  closeNewTask: () => set({ newOpen: false, newError: "" }),
  createTask: async () => {
    const task = (state.newTask || "").trim();
    const pkg = (state.newPackage || "").trim();
    const what = (state.newWhat || "").trim();
    if (!task || !pkg) {
      set({ newError: "a task id and a package name, both" });
      return;
    }
    if (!what) {
      set({ newError: "say what the robot should do. The agent writes the task "
                    + "from this, and a task nobody described is a task nobody "
                    + "can check." });
      return;
    }
    set({ newBusy: true, newError: "" });
    try {
      await api.newTask({ task, package: pkg, robot: (state.newRobot || "").trim(),
                          what });
      // Straight into Build, on the new task, with the agent already on it --
      // the first thing you should see of a task is the thing being built.
      const tasks = await api.tasks();
      set({ tasks: tasks.tasks, registry: tasks.registry || null,
            newOpen: false, newBusy: false, newTask: "", newPackage: "",
            newRobot: "", newWhat: "" });
      await selectTask(task);
      setView("build");
      await refreshBuild();
      await actions.askAgent(
          // The brief carries a working discipline, not just a request:
          // without it an agent can spend hundreds of turns reading and write
          // no files, while told to write first and read narrowly it produces
          // a package in minutes. An empty directory invites a survey; this
          // asks for a first draft that can fail a gate, which is the loop's
          // unit.
          `Write this task. It is called \`${task}\`, its package is \`${pkg}\`, `
          + `and it should do this:\n\n${what}\n\n`
          + "How to go about it:\n"
          + "1. Write a first version NOW, before reading widely. The smallest "
          + "package that constructs beats a correct plan, because the gates "
          + "can only tell you about code that exists.\n"
          + "2. Read narrowly and locally: rl-mcp's docs/your-task.md for the "
          + "shape, and the one task package next door that is closest to this "
          + "one. Do not survey the repository.\n"
          + "3. Register the task id exactly as written above.\n"
          + "4. Then say so, and I will run the checks. Expect the first ones "
          + "to fail — that is the loop working, not a setback. Fix what the "
          + "failing gate says, in this package, and ask me to check again.");
    } catch (err) {
      set({ newBusy: false, newError: err.message });
    }
  },

  toggleLaunch: () => set({ launchOpen: !state.launchOpen, launchError: "" }),
  launchRecipe: async (path, anyway = false) => {
    // The recipe is the plan, so there is nothing to type: rlmcp opens the
    // record as recipe-<run> and trains from the recipe's own files.
    set({ recipeBusy: path, recipeError: "" });
    try {
      const answer = await api.launchRecipe(state.task, path, anyway);
      set({ launches: { jobs: [...(((state.launches || {}).jobs) || []), answer.job] } });
      actions.openRun(answer.record);
    } catch (err) {
      if (/already training/.test(err.message) && !anyway
          && window.confirm(err.message + "\n\nLaunch anyway?")) {
        set({ recipeBusy: "" });
        return actions.launchRecipe(path, true);
      }
      set({ recipeError: err.message });
    } finally {
      set({ recipeBusy: "" });
    }
  },
  buildRecipe: async (runId) => {
    // `rlmcp recipe build <id>` beside this run's records root; the Build
    // view then lists it under Recipes.
    set({ recipeBusy: runId, recipeError: "" });
    try {
      const answer = await api.buildRecipe(state.task, runId);
      set({ recipeBuilt: answer });
    } catch (err) {
      set({ recipeError: err.message });
    } finally {
      set({ recipeBusy: "" });
    }
  },
  launch: async () => {
    if (!state.task) return;
    const slug = (state.launchSlug || "").trim();
    const why = (state.launchWhy || "").trim();
    // Both refused here as well as by the endpoint, because the endpoint's
    // refusal arrives after the box has been cleared -- and the hypothesis is
    // the one field nobody wants to type twice.
    if (!slug) { set({ launchError: "give the run a short name first" }); return; }
    if (!why) {
      set({ launchError: "say what you expect from it. A run nobody can be "
                       + "wrong about is not an experiment." });
      return;
    }
    set({ launchBusy: true, launchError: "" });
    try {
      const answer = await api.launch(state.task, {
        slug, hypothesis: why,
        num_envs: state.launchEnvs, device: state.launchDevice,
        max_iterations: state.launchIterations, video_every: state.launchVideo,
      });
      if (!answer.ok) throw new Error((answer.job || {}).error || "it did not start");
      set({ launchOpen: false, launchSlug: "", launchWhy: "" });
      await reloadRuns();
      // Launching is a statement about what you now want to be looking at.
      // Without this, the rail, the status bar and Control would all still
      // hold the previous run -- perhaps a failed one, with its hypothesis and
      // its `interrupted` state -- and finding the new record would be your
      // job. The record exists before the trainer does (that is what
      // `--record-run` is for), so there is something to select the moment
      // this returns.
      if (answer.record) await actions.openRun(answer.record);
    } catch (err) {
      set({ launchError: err.message });
    } finally {
      set({ launchBusy: false });
      await refreshBuild();
    }
  },
  cancelLaunch: async (job) => {
    try {
      await api.cancelLaunch(job);
    } catch (err) {
      set({ launchError: err.message });
    }
    await refreshBuild();
  },
  openRun: async (id) => {
    if (!id) return;
    setView("control");
    await selectRun(id);
  },
  previewCommand: async (cmd, args) => {
    if (!state.task) return;
    set({ buildError: "" });
    try {
      const answer = await api.buildCommand(state.task, cmd, args || {},
                                            { confirmed: true });
      if (!answer.ok) throw new Error(answer.error || "the preview refused");
    } catch (err) {
      set({ buildError: err.message });
    }
    await refreshBuild();
  },
};
