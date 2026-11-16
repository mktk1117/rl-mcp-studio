# The task report

Every task in this studio has one document that says **what it is**: `TASK.md`,
beside the package that registers it, rendered by the Overview tab. When you
are asked to write or refresh one, what follows is the discipline. The ask
itself names the file, the sections and the facts the studio already holds —
they are listed there rather than here so there is one copy of them.

**It is a definition, not a result.** No learning curves, no reward at
iteration 3000, no verdict on a run. Those belong to the record, and every
other view in this app already shows them. Somebody opens this to find out
what the policy sees, what it does, what it is paid for and what ends the
episode — usually *before* deciding whether any of that was sensible.

**It is a definition, so it is complete.** Not a summary, not an overview,
not the interesting half. Every reward term, every termination term, every
observation term in every group, every action term, every command, every
curriculum term — one row each, in the manager's own order, including the ones
whose weight is zero and the ones that never fire. No `…`, no "and others", no
"the main ones are". Somebody reads this beside the config to decide the reward
is wrong, and the term they are hunting is exactly the one a summary dropped.

**Exact values, with units.** Weights as written; every parameter a term takes,
with its value; thresholds, ranges, scales, offsets, clip bounds, decimation,
episode length, the noise on each observation term, the dimension of each. The
per-term dimensions must add up to the group's, and the groups to the
observation the policy is handed. Where a number is computed at construction,
give the expression and the file rather than a rounded guess, and where the
live environment can be asked — `list_parameters` on a preview or a run — the
live value wins over a default read out of a config.

**The studio checks part of it.** `rlmcp check` names every reward and
termination term the environment has, and the Overview panel says which of them
your report does not mention. That check does not exist for observations and
actions; nothing upstream enumerates those terms, which makes them the half
where being exhaustive is entirely on you.

**Read it out of the code, term by term.** Every claim comes from the task
package or from an answer `rlmcp check` gave, and names the file it came from.
A weight you remember from a sibling task is the failure mode here: these
documents are read by somebody about to decide the reward is wrong, and a
number carried over from another task sends them into the wrong file with
confidence. If two places disagree — the config and the term's default, say —
write down both and which one the registration actually uses.

**Show it.** A task described only in words is a task nobody can picture. The
preview holds the environment under a zero policy, so a screenshot of it is a
picture of the scene as it really is, and a short clip is the episode ending
you are describing. Capture them with the rlmcp tools, put them in the `media/`
directory beside the report, and reference them from the section they belong
to — not in a gallery at the end.

**Say what you could not determine.** An observation whose dimension is
computed at construction, a term whose parameters come from a config you cannot
find: write the question down under its own heading. A report that quietly
omits what it could not read is one nobody can trust the rest of.

**Refresh rather than rewrite.** When the code has changed under a report that
already exists, read it first and change what the change touched. Keeping the
sentences somebody already agreed with is what makes the diff worth reading.
