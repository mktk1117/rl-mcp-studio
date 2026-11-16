"""The shared instruction addition must reach the actual provider briefing."""
from rlmcp_studio.agents import harness
from rlmcp_studio.agents.claude import system_value


def test_training_sanity_is_packaged_and_rendered_in_the_shared_harness():
  path = harness.HERE / "35-training-sanity.md"
  assert path in harness.files()
  text = harness.instructions({"tools_attached": "SANITY-TOOLS-SENTINEL"})
  assert text.count("# Automatic training sanity checks") == 1
  assert "SANITY-TOOLS-SENTINEL" in text
  assert "{{" not in text
  for requirement in (
      "startup video", "representative screenshots", "termination reason",
      "startup completion bias", "longer-lived episodes are still censored",
      "same start/reset cohorts", "physical reset pose", "per\nreset bin",
      "reference-clock advance are not executed progress",
      "same environment across several episode resets", "batched phase diversity",
      "both poses from the same frame", "actual robot-to-target",
      "range and slope", "effectively zero far from the",
      "Review motion speed early", "phase advance per simulated second",
      "simulation time, training wall time", "video/replay playback time",
      "wall seconds per simulated second", "simulation-time playback",
      "unbuffered live viewer", "display correction is not a policy",
      "restart and starvation counters", "wall-clock gap during PPO optimization",
      "host simulation-step", "expected control stride", "completed replay windows",
      "browser-received motion", "tested future-import fix",
      "benchmark reasonable increases", "motor caps or required delay",
      "Default to the intended reference timing", "real motion timestamps",
      "temporary range and planned", "Per-episode randomized pace",
      "Separate physical capability limits", "agent-introduced smoothing, slew",
      "before slowing the task indefinitely", "not a universal fixed range",
      "Whenever a verified tuning breakthrough or recurring failure",
      "applicability and limits", "focused regression check",
      "not universal defaults", "untested hypothesis from a verified solution",
      "weighted\ncontribution", "positivity is not mathematically\nrequired",
      "per-joint distribution", "entropy", "executed action clipping",
      "When online training improves but fixed evaluation does not",
      "model and normalizer states", "actual observations", "paired starts",
      "native frozen sampled policy", "Fixed weights do not imply deterministic actions",
      "sampling RNG", "stochastic-only rough progress", "Some\nstart phases may improve",
      "One sampled realization neither", "mean\nclipped sample differ",
      "clipping before filtering", "conditional clipped-mean calculation is not closed-loop equivalence",
      "Do not silently replace native inference", "recorded authorization",
      "raw parameter's gradient", "zero gradient; reducing the entropy",
      "project the existing raw parameter", "after each optimizer step and after checkpoint load",
      "unchanged effective noise", "all optimizer moments", "runtime counters",
      "Preserved Adam momentum", "not a task-performance or immediate noise-reduction",
      "roughly two-second iterations", "2048", "4096", "peak\nallocated/reserved memory",
      "silent contact or constraint truncation", "delay in milliseconds",
      "overflow reason bits", "first failing sample", "internal collision scratch capacity",
      "Increase only the identified storage", "unrelated buffers blindly",
      "Extra headroom is", "before claiming the failure is resolved",
      "rough task completion first", "intermediate-motion\nresets",
      "time spent at the", "reference endpoint separately from active task motion",
      "easy endpoint dwell", "report it by reset", "desired end hold is verified",
      "extended holding is an explicit curriculum objective",
      "reference-completion truncation", "existing fixed-horizon",
      "measured zero assistance"):
    assert requirement in text, requirement


def test_new_instructions_reach_the_readable_prospective_briefing(client):
  response = client.get("/api/agent/002/briefing")
  assert response.status_code == 200
  brief = response.json()
  assert brief["live"] is False and brief["harness"] == "studio"
  sections = {section["id"]: section["text"] for section in brief["sections"]}
  assert "# Automatic training sanity checks" in sections["instructions"]
  assert "Run 002" in sections["context"]
  sent = system_value(brief)
  assert "# Automatic training sanity checks" in sent
  assert "not universal numerical rules" in sent
  assert "project the existing raw parameter" in sent
  assert "reference endpoint separately from active task motion" in sent
  assert "native frozen sampled policy" in sent
  assert "Fixed weights do not imply deterministic actions" in sent


def test_context_only_choice_preserves_the_instruction_opt_out(client):
  response = client.post("/api/agent/settings", json={"harness": "context"})
  assert response.status_code == 200 and response.json()["saved"]
  assert "open one keeps the prompt" in response.json()["harness_applies"]
  brief = client.get("/api/agent/002/briefing").json()
  assert brief["harness"] == "context"
  assert all(section["id"] != "instructions" for section in brief["sections"])
