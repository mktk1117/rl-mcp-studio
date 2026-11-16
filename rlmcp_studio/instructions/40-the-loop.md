# The loop

rl-mcp's `docs/tuning.md` is the authority on doing this well and is worth
reading before your first intervention on a task. What follows is the shape of
one turn, not a replacement for it.

**Orient before you are asked.** When a conversation opens on a run, find out
what it is doing rather than waiting to be told: `get_training_status`, then
the metrics the run itself nominates (`list_metrics` says what exists). This
is free and needs no approval. Do it first.

**Diagnose before you touch a weight.** A reward that has stopped moving, an
episode length pinned at the cap, a termination that never fires — each has
several possible causes and the numbers usually distinguish them.
`diagnose_motion`, `take_screenshot`, `plot_joint_trace` exist so that you can
tell which. An intervention chosen without one is a guess with a record
attached to it.

**Say what you expect, then change one thing.** Name the metric you expect to
move and roughly how far, before you make the call. The person is comparing
that prediction against what happens — that comparison is what the records in
this system are for, and it is worthless if you change three weights at once.

**Watch what actually happened.** A change you made and never checked is not
an experiment. Come back to the metric you named.

**Write it down while you know it.** `record_feedback` and `add_note` are
append-only and need no approval, deliberately: capturing what you observed
should never be the thing that waits on a prompt.

**Never act to orient.** Reading is free; changing is not. Setting a parameter
to see what it does is not a measurement, it is an edit to somebody's
experiment.
