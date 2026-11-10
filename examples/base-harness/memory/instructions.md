# Reflection steering

Return a transferable cue and lesson. Memory is a prediction about what a
future task will need: state a rule the harness could not re-derive on its
own, how the rule was verified, and any trap that cost retries.

- Do not record information readable from the task or snapshot every run.
- `evidence_refs` must be copied exactly from `valid_evidence_refs`. Stage
  labels, rationales, and prose are not evidence IDs.
- Adoption is not proof of correctness. Avoid task-specific facts.

This file is part of memory: the `harness-clone review` step rewrites it
from failed evaluation criteria, and the next evolution week's reflect
reads the rewrite. Edit it by hand too — it is your standing policy for
what deserves to be remembered.
