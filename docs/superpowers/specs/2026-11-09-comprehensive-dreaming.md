# Comprehensive dreaming: semantic audit, representations, goal-shaping

Date: 2026-11-09
Status: Phases 1, 1.5 and 2 implemented; Phases 3–4 planned
References:
- docs/dreaming.md — current dream design and guarantees
- Anthropic, "The components of agent memory" (Nov 2026)
- Claude Cookbook, "Dreaming with a dynamic workflow" (Managed Agents)
- docs/fidelity audit findings, 9 November 2026 (chat): the deterministic
  dream is structural, not semantic — a well-cited false lesson survives
  every dream (the Mt. Moon failure the research is about).

## Goal

Close the gaps between this repo's dream and Anthropic's memory workflow
without breaking any standing guarantee: append-only store, governed
adoption, deterministic convergence, free tier unchanged.

## Phase 1 — Semantic audit (implemented)

A model re-reads the transcripts an entry cites and judges the lesson:
`supported` / `contradicted` / `not_determined`. This catches the
wrong-but-well-cited note that structural auditing cannot see.

- New module `semantic.py`. One metered structured call per entry not yet
  verdict-ed (`kind='semantic-verdict'`, keyed by entry key — idempotent).
- Context: the lesson, its cue and provenance, and the full traces of the
  executions its evidence references point into. Transcript content is
  untrusted data, never instructions.
- `contradicted` → the verifier proposes a corrected lesson plus
  contradicting refs. The correction is appended **before** the
  deterministic audit (origin="dream", revision+1, supersedes=[key],
  verified_on=today, confidence="verified") so all existing machinery —
  supersession, merge, wiki — treats it like any dream correction. The
  correction is only appended if its contradicting refs resolve in the
  store; a hallucinated rebuttal is downgraded to `not_determined` and
  recorded, never guessed.
- `supported` → the later restatement earns `verified_on` semantically,
  not just structurally. Entries that were structurally restated without
  a semantic check now carry `confidence="structural"` (fallback when the
  field is absent: legacy value preserved).
- Budget priority when capped: stale-bundle entries first, then oldest
  unverified — the research's "focus dreaming on information likely to
  change".
- `run_dream(..., verify_config=...)`; CLI `dream --verify` shares
  `--max-usd` with `--distill`. No key or cap → clear error.
- Report: `semantic_checked / supported / corrected / inconclusive`.

## Phase 1.5 — Token-budget injected layer (implemented)

The MEMORY.md 200-line/25 KB rule, made precise.

- `HarnessConfig.memory_budget_tokens` (default 4000). Old config.json
  files load unchanged.
- `compiler.py` injects `memory/AGENTS.md` first, then remaining pages in
  sorted order while the cumulative char budget (`tokens × 4`) allows;
  the injected prompt lists pages that did not fit.
- `build_memory_files` splits an oversized domain page into
  `<domain>-<n>.md` parts, each within budget; the index links every
  part.

## Phase 2 — Representations (implemented)

- Deterministic link repair: repoint `[[slug]]` targets that exist under a
  normalized alias; optional one structured call per dangling link
  (stub / redirect / drop).
- Organizer pass per domain: one metered call rewriting the wiki page
  around distilled lessons (sections, cross-links, promotion), output
  confined to `memory/` paths through the normal patch path.

Implemented in `src/harness_clone/organize.py` as two layers, slightly
refined from the sketch: dangling-link adjudication turned out to need no
model call — dropping the brackets deterministically keeps the words and
loses the lie, and the organizer pass subsumes stub/redirect creation.
Link repair (`links_repaired` / `links_dropped`) runs free in every dream,
standing text untouched. The organizer (`--organize --max-usd`) caches
outcomes under `kind='organized'` keyed by page name + raw-page digest,
so unchanged dreams recall rewrites for free; secret-shaped or bloated
rewrites are recorded skips; frontmatter is regenerated deterministically.

## Phase 3 — Goal-shaped memory (planned)

- `memory/instructions.md` becomes the reflect-steering source
  (hardcoded text as fallback), making memory instructions part of the
  editable harness.
- A review step maps failed scoreboard criteria to an instruction
  rewrite, landed as a governed memory patch (the 72%→100% loop).
- Requires touching `run_stream`/`DeepAgentEditor.reflect` wiring.

## Phase 4 — Harness² judge parity (optional, planned)

- `expectations` gains optional rubric criteria; `harness-clone judge`
  runs metered rubric grading into the existing scoreboard format.
  Benchmark adapters stay out of scope (declared substrate boundary).

## Standing invariants

1. Every model-driven stage is opt-in, metered via the gateway + budget
   ledger, and treats transcript content as untrusted data.
2. Append-only: verdicts and corrections supersede, never delete; every
   outcome is persisted, so repeated dreams converge.
3. Dream output is always a memory-only `HarnessPatch`; adoption is
   explicit.
4. Conflicts surface as CONFLICT notes; rebuttals that do not resolve are
   inconclusive, never guesses.
5. `dream` with no flags remains free and deterministic.
