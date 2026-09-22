---
name: srh-hypothesis-bridge
description: Turn source evidence into an explicitly conditional, target-domain hypothesis and a discriminating target test.
---

Use this when a source result might inform a different target domain. Start with
`srh context --intent bridge --focus ID` and inspect the stored evidence path;
search is a lead, not proof. The current task determines whether a bridge is
useful at all.

Keep four things distinct: the labelled source observation; authored transfer
assumptions and failure conditions; the proposed target hypothesis; and any
separately labelled target evidence. Use `applicability`, `assertion_role`, and
`evidence_roles` when they make that distinction inspectable. Source evidence
must retain its locator and counterevidence must remain visible. A source
counterexample may limit a source assertion and motivate a target boundary, but
must not be written as an observed target `limits` assertion. Do not infer target
support from an analogy, auto-promote a hypothesis, or merge mechanisms because
terms match.

Complete with an explicit proposed/active hypothesis or an explicit decision not
to bridge. Persist only a meaningful new bridge/edge/decision through `remember`.
Read [the bridge schema](references/ingest-schema.md): it records dependencies,
not causal identification or transfer validity.
