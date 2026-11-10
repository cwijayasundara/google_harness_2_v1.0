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
| Dream checks memory against transcripts | `audit_library`: every entry's evidence refs re-resolved against stored executions; digest staleness checked against the adopted digest. A *structural* check — it catches broken citations, not false lessons whose citations resolve; semantic re-reading of transcripts is `distill.py`'s job (and a future audit pass) |
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

`harness-clone dream --run-dir <dir> [--base-dir runs/portfolio] [--adopt]
[--distill --max-usd <cap>]`

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

- **No model calls, no spend — unless you ask.** The default dream is
  deterministic Python; the budget ledger is untouched. `--distill`
  opts into metered analyzer calls through the same gateway as editing.
  The cockpit's dream button stays free (distillation is CLI-governed
  spend).
- **Structural, not semantic.** The dream proves citations resolve,
  duplicates merge, conflicts surface, and bundles are fresh. It cannot
  prove a well-cited lesson is *true* — that needs a model re-reading
  the transcript (distillation writes new lessons; a semantic audit of
  existing ones is future work).
- **Append-only truth.** Nothing in the store is deleted or rewritten;
  superseded entries remain inspectable.
- **Snapshots stay causal.** Ordinal scoping is preserved; corrections are
  visible exactly when the entries they replace were visible.
- **Trust boundary intact.** Dream output flows through bundle validation;
  it can never edit graders, tasks, or controller code.
- **Convergence.** Dream-origin entries are never restated; a second dream
  over unchanged state publishes the identical digest.

## 5. Live distillation

Implemented (stage 0 above). The deterministic dream verifies and
organizes what was captured; distillation adds what reflection could not
see: one cheap-model analyzer per unanalyzed transcript (`distill.py`),
injected with the full trace plus the domain's already-recorded lessons,
returning at most one observation or a recorded reason. The organizer is
the deterministic dream itself — distilled entries flow through the same
audit → consolidate → wiki pipeline as session-captured lessons, so
verification, conflict handling, and convergence are shared, not
duplicated.

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
