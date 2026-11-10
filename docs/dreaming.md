# Dreaming: offline consolidation of the experience library

Date: 2026-11-08
Status: Implemented (deterministic dream + live distillation)
References:
- Anthropic, "The components of agent memory" (Nov 2026) — research post
- Claude Cookbook, "Dreaming with a dynamic workflow" (Managed Agents)

## 1. Why

In-session reflection is the hippocampus: it captures salience while a task
runs, but only from the context that session could see, and it is never
revisited. A lesson that was wrong when written — or that drifted as the
harness evolved — is trusted by every later task that reads it. Anthropic's
Pokémon run is the canonical failure: an uncorrected wrong note caused a
three-time loop and the run gave up. The fix is a second, offline pass —
dreaming — that reads the store's own records with hindsight and improves
what future sessions read.

This repo already had the capture half (`editor.reflect()` →
`ExperienceLibrary` snapshots). `dreaming.py` adds the consolidation half.

## 2. Mapping the research to this codebase

| Concept (Anthropic) | Here |
|---|---|
| Transcripts (ground truth) | Execution records + traces in `run.sqlite` (`kind='execution'`) |
| Memory notes | `ExperienceEntry` (cue / lesson / evidence_refs / confidence) |
| Salience tagging in the moment | `editor.reflect()` after each adopted execution (existing) |
| Dream extracts what reflection missed | `distill.py` (`--distill`): one metered analyzer call per unanalyzed transcript over the FULL trace (failed attempts, fallbacks, error→repair), guarded by resolvable citations, credential scrub, and per-input accounting |
| Dream checks memory against transcripts | `audit_library` re-resolves every evidence ref (structural); `--verify` adds the semantic judgment: one structured call re-reads the full traces a lesson cites (`semantic.py`) |
| "How it is known" — validated rules | `verified_on` date + `confidence="verified"` on dream restatements |
| Later correction of a stale rule | Correction restatement keeps the newest lesson, notes what it replaced with source + date, supersedes the old entries |
| Conflicts: keep both sides, never guess | `CONFLICT` note quoting every side with task, bundle, and verification date; snapshot serves the note instead of either side |
| Duplicates merged, `MOVED TO` stubs | Merge restatements supersede the group; superseded entries stay in the store (append-only audit trail) but `snapshot()` stops serving them |
| Wiki: small linked files + `_index.md` catalog | `memory/<domain>.md` files with frontmatter + `memory/AGENTS.md` index with `[[slug]]` links; pages that would exceed the injected budget are split into indexed parts, never truncated |
| Small injected top layer (MEMORY.md 200-line cap) | The compiler preloads memory pages within `memory_budget_tokens` (index first, then best-fit); what did not fit is named in the prompt for on-demand reading |
| Never copy secrets into memory | Credential-shape scan drops secret-bearing lessons from the wiki; store copies stay for audit |
| Dreams keep working memory small / focused | Hand-written `AGENTS.md` standing text is preserved; only the marker-delimited index block is regenerated |
| Cadence; failed nights retry | The dream is deterministic and free; rerun anytime — it converges (no new restatements when nothing changed) |
| Adoption stays governed | Dream output is a candidate bundle in the run store; adopting it into a portfolio base dir is an explicit `--adopt` |

## 3. Pipeline

`harness-clone dream --run-dir <dir> [--base-dir runs/portfolio] [--adopt]
[--distill --max-usd <cap>] [--verify --max-usd <cap>] [--organize --max-usd <cap>]`

0. **Distill** (optional, metered). With `--distill`, every execution
   transcript not yet analyzed is read with hindsight by one structured
   model call (`distill.py`): full trace — failed attempts, fallbacks,
   error→repair cycles the in-session reflect only ever saw excerpts of.
   Proposed observations must cite trace rows that exist in the analyzed
   transcript (hallucinated citations are counted as skips, never
   stored), pass the credential scrub, and land at an ordinal past every
   existing entry so hindsight reaches only later tasks. Transcripts are
   embedded as untrusted data; every call is metered by the same gateway
   and budget ledger as editing; each input is accounted for in the
   report (`analyzed` / `distilled` / `distill_skipped`) and every
   analyzer outcome — entry, skip, or reason — is persisted under
   `kind='distilled'`, so repeated dreams converge. Unattributable
   transcripts (no stream task-map entry) are skipped, not guessed at.
   Distilled entries carry `origin="distilled"` and are then audited,
   verified, merged, and restated by the deterministic stages below
   exactly like session-captured lessons.
0b. **Semantic audit** (optional, metered). With `--verify`, every
   structurally-intact, not-yet-verdict-ed lesson is judged by one
   structured model call against the full transcripts its citations
   point into (`semantic.py`) — the Mt. Moon test the deterministic
   audit cannot do. Verdicts persist (`kind='semantic-verdict'`), so
   repeated dreams re-verify nothing. `contradicted` lessons are
   corrected by a governed restatement (supersede, `verified_on`,
   `confidence="verified"`) — but only when the rebuttal's own
   citations resolve in the store; a hallucinated rebuttal is recorded
   as inconclusive, never guessed. `supported` verdicts make the later
   restatement truly verified; structural-only restatements say so
   (`confidence="structural"`). Stale-bundle entries are verified
   first — the information most likely to have changed. Report:
   `semantic_checked / supported / corrected / inconclusive`.
0c. **Organize** (optional, metered). With `--organize`, one structured
   call per wiki page rewrites the page around its lessons — sections,
   cross-links, promotion of load-bearing rules — keeping every lesson's
   meaning and provenance; the rewrite replaces only that page's body in
   the memory-only patch, and outcomes persist under `kind='organized'`
   keyed by page name plus the raw page's digest, so a second dream over
   unchanged entries reuses the cached rewrite and calls the model zero
   times. Credential-shaped or implausibly bloated rewrites are skipped
   with a recorded reason. Report: `organized / organizer_skipped`.
0d. **Link repair** (deterministic, free). Every `[[slug]]` target is
   checked against the pages that exist: normalized aliases are repointed
   at the canonical page, dangling brackets are dropped (the words stay).
   Hand-written standing text is never touched. Report:
   `links_repaired / links_dropped`.
1. **Audit** (deterministic, free). Load every entry; classify:
   `verified` (refs resolve, no verification date yet), `intact` (already
   restated), `broken-ref`, `stale` (bundle differs from adopted),
   `duplicate`, `conflict`, `secret`, `superseded`.
2. **Consolidate** (deterministic, free). Append-only corrections:
   - duplicates → one merged restatement (richest member wins) superseding the rest;
   - later correction (newest member states a different lesson *and* its
     evidence resolves) → restate it with `verified_on` and a
     `replaces "..." (source, date, bundle)` note;
   - unresolved disagreements → one `CONFLICT` note superseding all sides;
   - broken-ref / secret / intact / stale entries are left untouched.
   Keys are revisioned (`namespace, domain, ordinal, task_id, revision`), so
   restatements never collide with originals and repeated dreams converge.
3. **Wiki** (deterministic, free). Served entries grouped by domain become
   `memory/<domain>.md` (frontmatter, plain current-understanding
   statements, inline provenance: cue, verified date or unverified, task,
   bundle, staleness). `memory/AGENTS.md` keeps the standing text and
   regenerates only the index block. Pages are chunked to respect both
   `retrieval_limit` and `memory_budget_tokens`; oversized pages split
   into indexed parts.
4. **Publish**. The wiki becomes a memory-only `HarnessPatch` against the
   adopted digest, applied and published as a normal digest-verified bundle
   (`apply_patch` → `publish_bundle`). Nothing outside the editable surface
   is touched. `--adopt` additionally archives it and writes
   `adopted.json` in the portfolio base dir; without `--adopt` the dream
   bundle waits for the next evolution or a human.

## 4. Guarantees

- **No model calls, no spend — unless you ask.** The default dream is
  deterministic Python; the budget ledger is untouched. `--distill`
  opts into metered analyzer calls through the same gateway as editing.
  The cockpit's dream button stays free (distillation is CLI-governed
  spend).
- **Structural, not semantic — unless you ask.** The deterministic dream
  proves citations resolve, duplicates merge, conflicts surface, and
  bundles are fresh. It cannot prove a well-cited lesson is *true*;
  `--verify` adds that judgment as a metered stage (and a semantic-audit
  pass over existing entries is the same seam).
- **Append-only truth.** Nothing in the store is deleted or rewritten;
  superseded entries remain inspectable.
- **Snapshots stay causal.** Ordinal scoping is preserved; corrections are
  visible exactly when the entries they replace were visible.
- **Trust boundary intact.** Dream output flows through bundle validation;
  it can never edit graders, tasks, or controller code.
- **Convergence.** Dream-origin entries are never restated; verdicts and
  distilled-outcome records persist; a second dream over unchanged
  state publishes the identical digest.

## 5. Live distillation and the semantic audit

Both model-driven stages are implemented. The deterministic dream
verifies and organizes what was captured; distillation adds what
reflection could not see (one cheap-model analyzer per unanalyzed
transcript — `distill.py`), and the semantic audit judges existing
lessons against the transcripts they cite (`semantic.py`). In both
cases the organizer is the deterministic dream itself — new entries and
corrections flow through the same audit → consolidate → wiki pipeline
as session-captured lessons, so verification, conflict handling, and
convergence are shared, not duplicated.

Guards, in order:

1. **Budget.** `--distill` requires `--max-usd > 0` and a configured
   `FIREWORKS_API_KEY`; every call is metered via the gateway.
2. **Data, not instructions.** The transcript is written to the virtual
   `/context.json` that `run_structured` already labels untrusted; the
   analyzer prompt repeats that transcript content is data, never
   instructions.
3. **Citations must resolve.** An observation whose evidence refs do not
   exist in the analyzed transcript is counted and recorded, never
   stored.
4. **Credentials never enter memory.** Credential-shaped cues or lessons
   are dropped before append.
5. **Accountability.** The report counts `analyzed` / `distilled` /
   `distill_skipped`; the store keeps one `kind='distilled'` record per
   execution with the outcome, which is what makes repeated dreams
   idempotent.

## 6. The semantic audit (`--verify`)

The deterministic audit proves citations *resolve*; it cannot prove a
well-cited lesson is *true*. `--verify` adds the missing judgment
(`semantic.py`): one structured model call per unverified entry re-reads
the **full traces** the lesson's citations point into — artifacts
truncated to keep prompts bounded — and returns `supported`,
`contradicted`, or `not_determined` with a reason.

Properties:

- **Verdicts persist.** Stored under `kind='semantic-verdict'` keyed by
  the entry's revisioned key, so repeated dreams re-verify nothing.
- **Corrections are governed restatements.** A `contradicted` verdict
  with a `corrected_lesson` produces a superseding entry
  (`origin="dream"`, `verified_on`, `confidence="verified"`) — but only
  if the rebuttal's own `contradicting_refs` resolve in the store. A
  hallucinated rebuttal is recorded as inconclusive, never guessed;
  a secret-shaped correction is dropped like any secret-bearing lesson.
- **Confidence is honest.** Restatements of `supported` lessons are
  `confidence="verified"`; without `--verify`, dream restatements say
  `confidence="structural"` — structurally intact, semantically
  unjudged.
- **Stale first.** Entries whose bundle digest differs from the adopted
  digest are verified before current-bundle entries, oldest first —
  the information most likely to have changed gets the budget.
- **Report.** `semantic_checked / supported / corrected /
  inconclusive` join the existing counters; the free dream never
  changes.

## 8. Goal-shaped memory: `instructions.md` and the review loop

Anthropic's workflow ends with a rewrite of "how this agent should think
about its memory" — the criteria that map failures to better remembering.
Here that loop is:

- **`memory/instructions.md` is the reflect-steering source**, part of
  the editable harness surface (versioned by bundle digest, patchable,
  adoptable). The reflect step reads it from the parent bundle through
  `instructions.load_instructions`; bundles predating the file fall back
  to the built-in default text. Task agents never see it preloaded — it
  steers the memory-writing step, not the task.
- **`harness-clone review --run-dir <dir> --evaluation <scoreboard.json>
  --max-usd <cap> [--adopt]`** closes the loop: the offline evaluator
  (`harness-clone evaluate`) produces a scoreboard; when criteria fail,
  one metered structured call re-reads the current instructions plus the
  failed tasks' chosen-execution evidence and rewrites the file so the
  next evolution week's reflect steers toward the missing checks, traps,
  and procedures. The rewrite lands as a memory-only `HarnessPatch`
  against the parent digest and is published like any bundle; adoption
  is explicit. Credential-shaped, empty, or implausibly bloated rewrites
  are recorded skips (`kind='reviewed'`, keyed by the instructions'
  digest plus the scoreboard's — reviewing the same scoreboard twice
  converges: zero model calls, identical bundle digest).
- **No criteria failed → no call, no patch.** The loop only spends when
  the scoreboard says something is wrong.

## 9. The rubric judge (`harness-clone judge`)

Exact artifact assertions cannot grade "the reconciliation explains the
discrepancy". Expectations files may carry optional rubric criteria per
task (`"rubric": ["criterion", ...]`); the deterministic evaluator ignores
that key, and `harness-clone judge --run-dir <dir> --expectations <file>
--output <json> [--evaluation <artifact-scoreboard>] --max-usd <cap>`
grades it with one metered structured call per rubric task
(`judge.py`).

- **Same scoreboard shape.** The report mirrors `evaluate`'s; each rubric
  row carries per-criterion grades (`criterion / passed / reason`). With
  `--evaluation`, rubric grades merge into the artifact scoreboard: a
  task passes only if both agree (`artifact_status` and `rubric_status`
  kept on the row).
- **Strict, never guessed.** A grader that fails, omits a criterion, or
  has no chosen output to grade leaves the task `unscored` with a
  recorded reason — never a pass by default.
- **Convergence.** Outcomes persist under `kind='judged'` keyed by the
  chosen artifacts' digest plus the criteria's digest; judging the same
  outputs against the same rubric twice makes zero model calls.
- **Measurement, not memory.** Nothing here touches the experience
  library, the bundles, or the budget-ledger experiments — grading is
  offline and never feeds refinement agents.
- Benchmark adapters stay out of scope by design (declared substrate
  boundary).

## 7. Representations: link repair and the organizer (`--organize`)

The wiki is rebuilt from entries every dream, so its shape stays
regenerable. Two layers improve that shape:

**Link repair — deterministic and free, part of every dream.** Every
`[[slug]]` target is checked against the pages that actually exist:
a target that exists under a normalized alias (`[[Code_Checkpoint]]`,
`[[OPS]]`) is repointed at the canonical page; a target that resolves to
nothing has its brackets dropped — the words stay, the dead link goes.
Hand-written standing text in `AGENTS.md` is never touched; only the
regenerated index block is repaired. The report counts
`links_repaired / links_dropped`. This also sanitizes links that model
stages emit (distilled lessons, organizer rewrites).

**The organizer pass — `--organize`, metered.** One structured call per
wiki page rewrites the page around its lessons: sections, cross-links,
promotion of load-bearing rules to the top. The prompt requires every
lesson's meaning and provenance to survive unchanged; the output
replaces only that page's body inside the same memory-only
`HarnessPatch` (frontmatter is regenerated deterministically, not by the
model). The same guards apply as everywhere else: credential-shaped
rewrites and implausibly bloated rewrites are skipped with a recorded
reason, organizer failures count as skips, and outcomes persist under
`kind='organized'` keyed by the page name plus the raw page's digest —
so a second dream over unchanged entries reuses the cached rewrite and
makes **zero** model calls. Report: `organized / organizer_skipped`.
