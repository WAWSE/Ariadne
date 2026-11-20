---
name: srh-hypothesis-bridge
description: Turn source evidence into an explicitly conditional, target-domain hypothesis and a discriminating target test.
---

Use this when a source result might inform a different target domain. Reuse a
matching brief already returned in this session; otherwise run plain `srh
context` once. If it already names the proposition/conditions, relevant
qualified results, execution state, and unresolved decision, assess the bridge
directly. This is a retrieval stop rule, not a scientific sufficiency claim.
Only for a named missing field that could change the bridge, prefer one batch
`show ID1 ID2`; use `srh context --intent bridge --focus ID` for an evidence
relationship path or a different target task context. Do not call `--help`,
`list`, `graph`, `state`, or `quality` merely to confirm a brief. Quality is for
explicit quality/status-upgrade/dependency work, a full audit, or broad
exploration. Search is a lead, not proof. The current task determines whether a
bridge is useful at all.

Keep four things distinct: the labelled source evidence (a checked passage,
author report, author interpretation, or local observation); authored transfer
assumptions and failure conditions; the proposed target hypothesis; and any
separately labelled target evidence. Use `applicability`, `assertion_role`, and
`evidence_roles` when they make that distinction inspectable. Source evidence
must retain its locator and counterevidence must remain visible. A source
counterexample may limit a source assertion and motivate a target boundary, but
must not be written as an observed target `limits` assertion. Do not infer target
support from an analogy, auto-promote a hypothesis, or merge mechanisms because
terms match.

Before importing a result into a target forecast or decision, pass the
transfer/update gate. Label whether it is a historical/source report, verified
reanalysis, new target observation, or forecast, then name its estimand. Compare
population/cohort, intervention, time, outcome, reference, and data-generating
process between source and target. An exact condition or matching method name is
not an exact estimand. If a field differs or is unknown, retain the result as
source-domain evidence and a conditional transfer proposal; never overwrite the
target forecast or mark the target mechanism established. Record the transfer
assumption, failure risk, and a competing prediction or decision-changing test.
Only explicitly matching target observations can directly update a target value.

For a mismatched estimand, do not copy a source residual or apply a calibration
factor to the target point forecast. Preserve the target forecast and record any
numeric transfer as a conditional sensitivity/range with its assumption; it is a
proposal for testing, not target evidence or a terminal allocation outcome.

When an author mechanism interpretation materially grounds a bridge, persist it
as a separate `author_interpretation` evidence record with its locator and keep
it distinct from the author-reported result. Do not save every passing thought.

Write back the smallest closed research-memory unit that could change a later
research decision: a real anchor, a locator/excerpt, and the claim, proposal,
or boundary it qualifies. Anchor first in the same `remember` delta (or reuse a
saved anchor), then add evidence. This is a writeback pattern, not a required
research pipeline: do not manufacture a hypothesis or decision when preserving
the source alone is the useful next step.

Every evidence record needs **exactly one** nonempty `source_id`, `paper_id`,
or `experiment_id` that resolves to a matching `source`, `paper`, or
`experiment` record already stored or included in the same delta. Do not use an
empty identifier or an invented experiment ID merely to record target context.
Use a `source` record with an explicit authored `data.kind` for a user task
brief, notebook, dataset card, protocol, supplied document, or another
non-paper/non-experiment input; reference it with `source_id`. Use `paper_id`
for literature, and `experiment_id` only after the matching experiment record
is present.

When a bridge is actually chosen, persist only the meaningful new
bridge/edge/decision through `remember`.
For a nontrivial first write, use `remember --check FILE` before `remember
FILE`: it validates the same delta without saving it. Treat its anchors and enum
lists as a mechanical contract; correct the authored delta yourself rather than
asking it to infer a source, role, or target result.
Read [the bridge schema](references/ingest-schema.md): it records dependencies,
not causal identification or transfer validity.
