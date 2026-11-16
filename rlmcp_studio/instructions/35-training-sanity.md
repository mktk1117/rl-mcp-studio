# Automatic training sanity checks

Run these checks proactively at startup, after a material intervention, and when
learning stalls. Do not wait for the person to ask why nothing is improving.
Use the installed rl-mcp diagnostics and the task's nominated metrics; its
`docs/tuning.md` remains the command/workflow authority. These are diagnostic
checks, not universal numerical rules or permission to change another run.
Record the evidence, uncertainty and next discriminating test compactly.

## Establish that the actual task can teach the policy

Before committing to a long run, inspect the resolved task, reset distribution,
reference/command timing, action processing, rewards and termination conditions.
Run the available startup sanity check within the authorized resource budget.
Capture an actual startup video and representative screenshots, including an
ending episode. Check that observed motion, contact/support, resets and the
reported termination reason agree. A reward curve or a single initial pose is
not a substitute for this check. Distinguish success, timeout, failure and an
invalid simulator state; verify timeouts bootstrap only where appropriate.

Check startup completion bias: initially only fast-failing episodes may have
finished, while longer-lived episodes are still censored. A rising mean completed
episode length is not by itself evidence of learning. Compare fixed checkpoints
on the same start/reset cohorts and report unfinished episodes. With middle-motion
resets, report measured progress relative to the physical reset pose and per
reset bin; initial height and reference-clock advance are not executed progress.
Watch the same environment across several episode resets: batched phase diversity
does not establish reset variety within that observed environment. Verify the
actual reset sequence against the intended sampler. Show the reference beside
the robot when supported, with both poses from the same frame rather than mixing
buffered robot playback with current reference targets.

Check whether the reward provides a positive or otherwise useful progress
signal for reachable behavior. Inspect each term's raw values, weight, weighted
contribution and activation frequency, including episode totals and rates.
Check control-dt scaling, reward clipping/normalization, sparse bonuses and
termination costs. A large penalty sum can hide task progress or encourage
ending episodes early. A constant reward can provide little useful advantage
signal. Negative rewards can still learn: positivity is not mathematically
required, and adding a constant does not repair an uninformative objective.
Compare useful behavior with failure, and check return/advantage variation and
value targets before blaming the optimizer.
For reference tracking, confirm that reward depends on the actual robot-to-target
distance. Check its range and slope at the errors the policy actually reaches;
narrow exponential tracking kernels can become effectively zero far from the
target. A moving reference or a nonzero reward weight does not prove a useful
tracking signal. Compare per-limb contributions and representative error values.

Review motion speed early, not only after a long training run. Compare the
intended task timing, reference duration and phase advance per simulated second
with actual robot progress. Separate simulation time, training wall time and
video/replay playback time: a slow reference, a policy that lags its target, slow
sample collection and slowed playback require different fixes. Check initial
holds, retiming multipliers, horizon coverage and playback rate explicitly.
Measure wall seconds per simulated second from rollout length and control dt.
An unbuffered live viewer follows training throughput and can appear slow even
at the intended physical pace. Judge motion using simulation-time playback or
a clip with verified simulated timestamps and frame rate; inspect buffering,
underruns and playback speed separately. A display correction is not a policy
improvement or a change to the reference rate. Keep reference overlays aligned
with the replayed state, or use aligned recorded clips when replay cannot do so.
If buffered playback never produces a complete window, inspect recording,
restart and starvation counters. A wall-clock gap during PPO optimization can
occur between adjacent simulated control steps; it must not be mistaken for
missing simulation time. A reliable recorder checks gaps with
host simulation-step contiguity and the expected control stride, with a
conservative wall-time fallback only when no usable step clock exists.
Test actual skipped, repeated or reset step counters, clock-source changes, environment switches and explicit
pause, as well as long optimizer gaps. Check completed replay windows and
browser-received motion after adoption; WebGL availability and configured
buffer duration alone do not establish playback. Label an unbuffered temporary
view as training-wall-time motion, and distinguish a tested future-import fix
from the backend already loaded by a running trainer. Do not restart training
just to repair presentation without an authorized lifecycle decision.
Default to the intended reference timing: near 1x when the reference carries
real motion timestamps, unless the task or user calls for another pace. Do not
choose extreme slowdown without that intent or measured evidence. If a speed
curriculum is needed, document the target pace, temporary range and planned
return to the intended timing. Per-episode randomized pace is an option when
appropriate to the task, not a universal fixed range.
Notice sluggish progress proactively. A conservative starting reference speed
is not an immutable limit: benchmark reasonable increases within the authorized
budget and inspect actual tracking errors, action clipping, contact failures and
completion on comparable starts. Do not substitute arbitrary conservative
limits for a measured test, or blindly change physics dt, controller gains,
motor caps or required delay to make a video look faster.
Separate physical capability limits from agent-introduced smoothing, slew and
style constraints. Check whether an arbitrary controller limit prevents following
the reference before slowing the task indefinitely. Diagnose its origin and
effect, then test any authorized adjustment with the real motor/delay limits
preserved; do not mistake a convenient training restriction for hardware physics.

For a new difficult task, begin with a simple progress/completion objective and
few or no optional style penalties. Learn rough task completion first, then
introduce smoothness, energy, posture and other style costs in measured stages.
Keep genuine physical constraints, required safety/contact gates and the user's
acceptance criteria intact. Do not quietly erase violations to make the reward
look better. Existing experiments retain their original predicates; propose a
new configuration or recorded intervention when the objective must change.

## Diagnose exploration and learning, beyond the reward curve

When progress stalls, inspect the effective mean policy standard deviation and
its per-joint distribution: minimum/maximum, quantiles and relevant joint groups.
For log-standard-deviation policies, distinguish the learned log parameter, its
raw exponential and the effective value after bounds/clamping. Check entropy,
std at floors/ceilings, and whether clamps leave parameters with zero gradients.
Check action mean magnitude, raw and executed action clipping fractions, target
slew limits, actuator saturation and any warmup masks. A batch-average std can
hide one frozen limb; high std can also waste samples in clipping or impacts.
Do not automatically inflate noise when the evidence points elsewhere.

When online training improves but fixed evaluation does not, first match the
checkpoint, model and normalizer states, actual observations, reference/reset
cohort, controller, assistance and physical settings. Then explicitly compare
the native frozen sampled policy with deterministic inference on paired starts.
Fixed weights do not imply deterministic actions. Inspect the installed actor
API, effective std, sampling RNG and raw versus clipped/executed commands;
record inference mode and distinguish stochastic-only rough progress. Some
start phases may improve while others worsen. One sampled realization neither
estimates expected performance nor establishes universal superiority or a
causal mechanism. Preserve each mode's result and qualification limits.

Large Gaussian noise followed by clipping before filtering can make the mean
clipped sample differ from the clipped deterministic mean. Check this ordering
before attributing a training/evaluation gap solely to exploration or optimizer
failure. A conditional clipped-mean calculation is not closed-loop equivalence:
it does not capture future observation distributions, contact events or
recurrent controller state. Do not silently replace native inference with that
transformation; any such controller experiment needs its own explicit scope,
source guards, paired validation and recorded authorization.

When a bounded noise parameter is stuck beyond its forward-clamp range, test
the raw parameter's gradient both outside and at the bounds. A forward clamp
can keep effective noise valid while its out-of-range raw parameter receives
zero gradient; reducing the entropy coefficient alone need not restore that
gradient. One opt-in remedy is to project the existing raw parameter
onto its existing bounds after each optimizer step and after checkpoint load.
Verify unchanged effective noise at the repair, all other learned tensors and
normalizer statistics, and all optimizer moments. Test native save/load and
that the post-step hook actually executes; expose runtime counters rather than
inferring execution from source alone. Preserved Adam momentum can delay inward
movement even after boundary gradients recover. This repairs that particular
optimization failure, not a task-performance or immediate noise-reduction
guarantee. It is not permission to reset noise, widen bounds or replace the
optimizer; use a separately recorded, tested implementation where applicable.

Also inspect actor/critic losses, KL, effective learning rate, gradient health,
optimizer lineage and observation normalization. Check that observations are
finite, on the expected scale, and include the controller history the policy
actually needs. A warm start into different phases or a changed horizon can
shift normalization and value/timeout targets. Diagnose that shift explicitly;
do not reset learned state or statistics as an unrecorded convenience.

## Benchmark practical parallelism and iteration latency

Aim for roughly two-second iterations when the task and hardware permit useful
updates at that cadence. Treat this as an interactive tuning target, not a hard
limit. Benchmark after compilation/warmup; record environment count, rollout
length, samples per update, collection time, PPO update time, rendering/telemetry
time, total iteration time, useful samples per second, GPU utilization and peak
allocated/reserved memory. Respect other owners' GPU allocations.

Increase environment count aggressively when measured throughput and memory
headroom support it; explicitly test larger requested sizes such as 2048 or
4096 within the available budget. Choose the largest practical batch that
improves time to useful learning, not simply the largest count that allocates.
More environments can slow iterations or change optimization behavior. Compare
wall-clock progress as well as samples per second, and preserve the benchmark
settings so a speed claim is reproducible. Stop or reduce a benchmark after OOM
rather than repeatedly launching the same failing size.

If collection dominates, profile excessive contacts, contact/constraint buffer
use, collision geometry, solver work, physics substeps and rendering. Check
native overflow flags and peaks under representative resets and exploration;
never accept silent contact or constraint truncation to fit more environments.
Inspect native overflow reason bits and the first failing sample, not only
aggregate contact/constraint counts: internal collision scratch capacity can
overflow while those totals remain below their limits. Separate overflow from
missing telemetry samples. Increase only the identified storage; do not enlarge
unrelated buffers blindly. Preserve every fatal guard and validate the change
in a fresh process before claiming the failure is resolved. Extra headroom is
not proof that all future reset/exploration states fit.
Changing timestep, substeps, solver iterations or collision geometry is a
physical/numerical change, not free optimization. Preserve required actuator
delay in milliseconds (for example 15 ms), contact dynamics and task invariants;
validate any authorized change with fresh motion/termination checks and record
its effect. Do not reduce physics fidelity blindly to hit two seconds.

## Make a hard task reachable, then remove scaffolding

If useful reward and exploration are present but the task remains out of reach,
use an explicit curriculum: easier goals, shorter prefixes, intermediate-motion
resets, slower reference motion, or other task-supported stages. Retain coverage
of the original starting condition and expand toward the complete task. If the
user authorizes virtual tracking forces or other training assistance, expose its
applied magnitude and schedule, taper it to zero, and label assisted progress
separately. Final unassisted qualification requires fresh complete episodes with
measured zero assistance; a zero terminal force does not erase earlier support.

After each intervention, compare the predicted change with actual task metrics
and a current video, not reward alone. Declare in advance what observation or
bounded no-progress window triggers diagnosis, promotion or another stage.
Use native curriculum/scheduling facilities where available. Keep tuning seeds
separate from fresh independent acceptance episodes. Record failed trials and
unused budgets honestly. Rough completion, held support, an assisted prefix and
full-task success are different outcomes; name the one actually demonstrated.

For reference curricula with middle-motion resets, measure time spent at the
reference endpoint separately from active task motion, and report it by reset
phase. A longer horizon can mostly add easy endpoint dwell for late starts;
endpoint reward and long survival there are not harder task motion or
additional reference coverage. Once the desired end hold is verified, normally begin a
new segment unless extended holding is an explicit curriculum objective. Plan
reference-completion truncation and timeout/value handling together, preserving
required end-hold checks. This is a prospective curriculum design check, not a
reason to reinterpret the predicate or outcomes of an existing fixed-horizon
comparison.

## Improve the reusable harness from evidence

Whenever a verified tuning breakthrough or recurring failure is found, update
the reusable harness with its generalizable trigger, recorded evidence, tested
intervention (if any), applicability and limits, plus a focused regression check
for the new instruction or behavior. This is part of completing the work, not
an optional note left only in the current run. Consolidate overlapping guidance
instead of accumulating contradictory rules. Keep task-specific numbers and
experiment history in the run's evidence, not universal defaults. Distinguish
an untested hypothesis from a verified solution; a failed intervention teaches
a diagnostic limitation, not a successful recipe. Test the change and verify
that the prospective provider briefing actually includes it, preserving active
conversations and unrelated settings.
