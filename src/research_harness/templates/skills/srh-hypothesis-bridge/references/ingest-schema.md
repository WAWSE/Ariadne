# Bridge writeback reference

Write a target proposal through `remember` only when the source path and target
test are explicit. The hypothesis remains proposed until separately assessed;
an `analogous_to` edge is hypothesized, never evidence that the transfer holds.
The IDs `evidence:source`, `evidence:opposing`, and `claim:source` below must
already be saved. Each evidence record needs exactly one nonempty `source_id`,
`paper_id`, or `experiment_id` resolving to a record that is already stored or
appears in this same delta. The user task brief below is therefore persisted as
the clearly labelled `source:task-brief` anchor before its target-context
evidence is used. Its `kind` is an authored label, not a paper classification.
If the target context instead comes from a local run, first include its matching
`planned` experiment record, then use that record's `experiment_id`; never
invent one.
The target observation added here is only `context`, not target support. The
source counterexample limits the source claim; it is retained as a target
boundary through the proposed hypothesis rather than misrepresented as an
observed target limitation.

Assign each role relative to its owning proposition or decision. Here the source
failure is `counterevidence` for the source generalization, `analogy` for the
conditional target proposal, and `support` for deciding to test the boundary.
Supporting that decision does not establish the target effect or mechanism.
If evidence genuinely opposes the new decision, retain it there as counterevidence.

```json
{
  "schema_version": 1,
  "records": [
    {"id":"source:task-brief","type":"source","title":"User-supplied target task brief","summary":"User-supplied target task context, retained as a provenance anchor rather than a literature citation.","tags":["task-brief","target-context"],"data":{"kind":"user_task_brief","applicability":{"domain":"synthetic target task","task_context":"stable-or-volatile target context"}}},
    {"id":"evidence:target-context","type":"evidence","title":"Target-context task-brief passage","summary":"User-supplied target-domain context; it does not establish the transfer.","tags":["target-context"],"data":{"source_id":"source:task-brief","locator":"User-supplied task brief, target-context statement","excerpt":"Synthetic target-task context observation.","evidence_kind":"source_excerpt","assertion_role":"target_assertion","applicability":{"domain":"synthetic target task","task_context":"stable-or-volatile target context"}}},
    {"id":"hypothesis:target","type":"hypothesis","title":"Target proposal","summary":"A conditional transfer proposal, not a source result.","tags":["proposed"],"data":{"prediction":"Under stable target context, intervention changes the target outcome.","alternatives":["Preparation rather than the proposed mechanism explains the effect."],"test":"Compare the intervention with matched control under stable and volatile target contexts.","status":"proposed","evidence_ids":["evidence:source","evidence:opposing","evidence:target-context"],"evidence_roles":{"evidence:source":"analogy","evidence:opposing":"analogy","evidence:target-context":"context"},"assertion_role":"transfer_proposal","applicability":{"source_domain":"synthetic source task","target_domain":"synthetic target task","task_context":"stable-or-volatile target context"},"transfer_assumptions":["target measurement is comparable to the source measurement","volatile target context may remove the effect"]}},
    {"id":"decision:bridge-boundary","type":"decision","title":"Keep the volatile boundary","summary":"Target test must expose the source failure condition.","tags":[],"data":{"reason":"The source counterexample limits the source assertion under volatile context; it does not observe a target limitation.","evidence_ids":["evidence:opposing","evidence:target-context"],"evidence_roles":{"evidence:opposing":"support","evidence:target-context":"context"},"next_action":"Run the smallest stable-versus-volatile target comparison.","applicability":{"target_domain":"synthetic target task","task_context":"stable-or-volatile target context"}}}
  ],
  "edges": [
    {"id":"edge:target-task-brief-evidence","source":"source:task-brief","target":"evidence:target-context","relation":"has_evidence","status":"structural","evidence_ids":[],"conditions":{},"applicability":{"domain":"synthetic target task","task_context":"stable-or-volatile target context"}},
    {"id":"edge:bridge","source":"hypothesis:target","target":"claim:source","relation":"analogous_to","status":"hypothesized","evidence_ids":["evidence:source"],"evidence_roles":{"evidence:source":"analogy"},"assertion_role":"analogy","applicability":{"source_domain":"synthetic source task","target_domain":"synthetic target task","task_context":"stable-or-volatile target context"},"conditions":{"assumption":"target measurement is comparable","failure_condition":"volatile target context may remove the effect"}},
    {"id":"edge:source-opposition-limits","source":"evidence:opposing","target":"claim:source","relation":"limits","status":"observed","evidence_ids":["evidence:opposing"],"evidence_roles":{"evidence:opposing":"counterevidence"},"assertion_role":"limitation","applicability":{"domain":"synthetic source task","task_context":"volatile source context"},"conditions":{"context":"volatile"}}
  ]
}
```

`conditions` and `transfer_assumptions` should make the transfer boundary
inspectable. New direct refs receive basis hashes automatically. A supported target
claim/hypothesis additionally needs explicitly labelled target observation evidence
with role `support`; that remains an advisory provenance rule, not a scientific
verdict or a requirement for a local experiment. The schema checks shape and
references; it cannot establish causal mechanism, analogy validity, or target support.
