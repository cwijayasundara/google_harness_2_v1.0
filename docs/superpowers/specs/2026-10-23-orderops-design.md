# OrderOps self-evolving harness benchmark

Date: 2026-10-23

## Purpose

Replace the shipped portfolio example with a local operations investigation
benchmark that separates harness modification from measured improvement. The
user authorized the design, implementation, and removal of
`examples/portfolio-harness`. Implementation proceeds inline on a feature branch.
The existing portfolio application is retained as legacy functionality; its
bundle becomes a test-only fixture. Existing archived portfolio bundles remain
usable, while starting a new portfolio experiment fails with migration guidance.

## Chosen approach

Use file-based OrderOps cases on the existing TaskSpec, Docker executor, Pi editor,
controller, and experience library. Alternatives were incident investigation
(harder to build reliable simulated ground truth) and coding repair (more runtime
infrastructure). No new frontend or external operational services are required.

## Cases and isolation

A deterministic, versioned generator writes 30 development and 20 held-out cases
by default. Each task contains orders, payment/refund events, warehouse counts,
policy, and a public response contract. Amounts are integer GBP pence. Development
families include refund shortfalls, duplicate deliveries of payment events,
pending refunds, settled refunds, stock discrepancies, and missing evidence.
Held-out cases include unseen event-state transitions and renamed CSV columns,
alongside fresh instances of development families. At least some correct answers
are no-action and some are insufficient-evidence decisions.

Generate answers from latent transaction state, independently of rendering CSV
evidence. Events have event_id, transaction_id, state, sequence and amount. Repeated
event_id denotes retransmission; transaction state comes from its latest sequence.
Completed refunds reduce outstanding liability, pending refunds reserve liability,
failed refunds do neither. Known shortages use expected minus counted inventory;
missing warehouse counts require escalation. The public policy states these rules.

Public tasks and hidden answers must occupy disjoint directory trees. Generation
refuses existing destinations and overlapping public/private roots before writes.
The generator seed and scenario-family labels stay in the private manifest.
Online workers mount only case sources and candidate harnesses, never the manifest,
answers, generator, or grader. The executor image strips generation/scoring code and their bytecode from the
working source tree and both installed Python environments. Output paths are findings.json,
proposed_actions.json, and report.md. Findings include order_id, issue, amount_pence,
and row evidence references; actions include order_id, action, amount_pence.
The issue/action vocabulary and schemas are public. Every order requires a finding
and decision, including no_action, so empty output cannot receive full marks.

## Harness and evolution

The base OrderOps harness supplies a competent investigation procedure, CSV
inspection helper, reconciliation and verification skills, and an optional
reviewer. It must not contain a reference solver, scenario-specific answers, or
deliberately broken code. Pi can evolve the existing editable surface.

Three experiment arms use the same model/configuration:

1. frozen: execute the original bundle on development tasks, no edits;
2. local: Propose–Probe–Compose on each task, reset bundle and experience each time;
3. persistent: refine each task from the preceding selected valid bundle and
   provide earlier evidence-supported lessons.

Use the existing practical selector so the baseline is eligible, sequential mode,
k=1 by default, and Pi for code-bearing proposals. Structural validation is not a
claim of improvement. Carry only completed, delivered, schema-valid selected
outputs; keep the previous bundle otherwise. Validate output contracts through a host-side DockerExecutor hook
before an execution is published and the controller can select a delivery. Record per-task starting digest,
selected digest, errors, execution usage, and elapsed execution time.

For each arm, freeze the final digest and execute held-out tasks exactly once,
without editors or reflection. A fourth retry control takes the same number of
base-harness attempts per task as the observed local refinement's execution count and uses
the existing anonymous selector. This is an attempt-matched control, not an exact
dollar match: editor calls and actual token costs are reported separately. If the
local arm is omitted, retries uses the scheduled 4*k attempts. Local holdout freezes
the last development case's selected bundle, chosen before seeing holdout scores.
Never pick a retry using hidden scores.

## Evaluation

An offline command reads answers and recorded outputs, never supplying scores to
online agents. Reject reports under task roots or online run directories. Match
findings and actions by order ID, reject duplicate IDs and unknown records, require
strict nonnegative integer amounts, check row references against supplied sources,
and report exact case success, finding accuracy, action accuracy, false active
actions, evidence-reference validity, delivery coverage, usage, and elapsed time.
Evidence-reference validity means a cited row exists; semantic support is not
proven by this metric. Narrative quality is ungraded. Failed/missing cases count
against success. Report all arms on identical held-out cases and show paired
case outcomes. A positive result requires better held-out performance; no gain is
a valid outcome. Small pilot results are descriptive, not statistical proof.

The evaluator does not import or call the generator's latent-state solver.
Hand-calculated adversarial fixtures test its scoring independently.

## Interfaces and persistence

CLI: `orderops generate`, `orderops run`, and `orderops evaluate`.
Generation is free. Live runs require Fireworks credentials, Docker, and an explicit
total --max-usd cap shared across all arms. A capped BudgetLedger meters every
stage in the shared RunStore; per-task allocations from the existing controller
remain enforced. Frozen/retry/held-out executions also use the same ledger.

Fingerprint public inputs, configuration, base digest, and image IDs before
resuming. Persist each finished execution and task; replay does not repeat finished
paid work. Generation seed changes require new destination paths; configuration
or source changes require a fresh run directory. Run admission rejects task-source
and run-directory overlap. Hidden grading is a separate process and command.
Store immutable input copies under the run root for later evaluation, with fingerprints
verified on resume. Holdout is never used for reflection or bundle edits.

## Implementation and validation

New modules under src/harness_clone/orderops/: cases.py (generation), contracts.py
(artifact schemas), score.py (offline grader), experiment.py (arms and persistence),
cli.py (command adapter). Add examples/orderops-harness and focused tests covering
independent money calculations, state transitions, invalid outputs, grader
separation, ancestry/lesson causality, failure fallback, frozen holdout, changed-input
resume rejection and no duplicate execution on compatible resume.

Move the legacy bundle to tests/fixtures/portfolio-harness, update legacy tests,
and remove portfolio onboarding from README in favor of OrderOps instructions.
Run the complete Python suite and lint, validate packaged examples, and smoke-test
generation/evaluation. Paid live runs need a user-chosen cap; do not infer one.
