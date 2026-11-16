# The defaults, and the one rule under them

**Use rlmcp. Never re-implement it.** Every capability you need to watch,
measure, steer or record a run exists as an rlmcp tool or command. Parsing
`metrics.jsonl` by hand, writing your own frame tiler, inventing a stage
ladder in a scratch file — each of those is a tempting shortcut, and each is
wrong. If a capability seems missing, say so; the fix is a change to rl-mcp,
not a helper you write.

**mjlab is the backend.** Tasks in this studio are mjlab tasks, registered
through `register_mjlab_task`. rl-mcp's adapter contract admits IsaacLab and
one exists upstream, but assume mjlab unless the task itself says otherwise.

**The record is the deliverable.** A run that trained and taught you something
is worth nothing if the reasoning is only in this transcript. Write what you
learned into the record — `record_feedback`, `add_note`, `set_record_headline`
— those are append-only and need no approval, which is deliberate.

**A warm start is not a clean result.** A run that started from another run's
weights cannot claim `validated` on its own, and rl-mcp will say so when you
close it. Know which you are looking at before you draw a conclusion from it.
