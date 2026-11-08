# Dreaming: offline consolidation of the experience library

Date: 2026-11-08
Status: Implemented (deterministic dream); live distillation deferred
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
| Dream verifies memory against transcripts | `audit_library`: every entry's evidence refs re-resolved against stored executions; digest staleness checked against the adopted bundle |
| "How it is known" — validated rules | `verified_on` date + `confidence="verified"` on dream restatements |
| Later correction of a stale rule | Correction restatement keeps the newest lesson, notes what it replaced with source + date, supersedes the old entries |
| Conflicts: keep both sides, never guess | `CONFLICT` note quoting every side with task, bundle, and verification date; snapshot serves the note instead of either side |
| Duplicates merged, `MOVED TO` stubs | Merge restatements supersede the group; superseded entries stay in the store (append-only audit trail) but `snapshot()` stops serving them |
| Wiki: small linked files + `_index.md` catalog | `memory/<domain>.md` files with frontmatter + `memory/AGENTS.md` index with `[[slug]]` links |
| Small injected top layer (MEMORY.md 200-line cap) | The wiki files are capped so `retrieval_limit` bounds what loads at startup; `AGENTS.md` is the index, detail one link away |
| Never copy secrets into memory | Credential-shape scan drops secret-bearing lessons from the wiki; store copies stay for audit |
| Dreams keep working memory small / focused | Hand-written `AGENTS.md` standing text is preserved; only the marker-delimited index block is regenerated |
| Cadence; failed nights retry | The dream is deterministic and free; rerun anytime — it converges (no new restatements when nothing changed) |
| Adoption stays governed | Dream output is a candidate bundle in the run store; adopting it into a portfolio base dir is an explicit `--adopt` |

## 3. Pipeline

`harness-clone dream --run-dir <dir> [--base-dir runs/portfolio] [--adopt]`

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
   regenerates only the index block. Files are chunked to respect
   `retrieval_limit` — the injected layer stays small.
4. **Publish**. The wiki becomes a memory-only `HarnessPatch` against the
   adopted digest, applied and published as a normal digest-verified bundle
   (`apply_patch` → `publish_bundle`). Nothing outside the editable surface
   is touched. `--adopt` additionally archives it and writes
   `adopted.json` in the portfolio base dir; without `--adopt` the dream
   bundle waits for the next evolution or a human.

## 4. Guarantees

- **No model calls, no spend.** The dream is deterministic Python; the
  budget ledger is untouched.
- **Append-only truth.** Nothing in the store is deleted or rewritten;
  superseded entries remain inspectable.
- **Snapshots stay causal.** Ordinal scoping is preserved; corrections are
  visible exactly when the entries they replace were visible.
- **Trust boundary intact.** Dream output flows through bundle validation;
  it can never edit graders, tasks, or controller code.
- **Convergence.** Dream-origin entries are never restated; a second dream
  over unchanged state publishes the identical digest.

## 5. Deferred: live distillation

The cookbook's analyzers also *extract* new facts from full transcripts
with hindsight (failed attempts, fallbacks, error→repair cycles the
in-session reflect never saw). The deterministic dream verifies and
organizes what was captured; distilling what was missed requires model
calls through the gateway with budget staging — the same machinery as
`DeepAgentEditor._run`. When added, it should follow the cookbook's
shape: one cheap-model analyzer per task result, an organizer pass after,
transcript content treated as data (never instructions), and the report
counting every input that failed to yield an entry.
