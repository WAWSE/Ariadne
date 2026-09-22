# Bridge writeback reference

Write a target proposal through `remember` only when the source path and target
test are explicit. The hypothesis remains proposed until separately assessed;
an `analogous_to` edge is hypothesized, never evidence that the transfer holds.
The IDs `evidence:source`, `evidence:opposing`, `claim:source`, and
`paper:target` below must already be saved. The target observation added here is
only `context`, not target support. The source counterexample limits the source
claim; it is retained as a target boundary through the proposed hypothesis rather
than misrepresented as an observed target limitation.

```json
{
  "schema_version": 1,
  "records": [
    {"id":"evidence:target-context","type":"evidence","title":"Target-context observation","summary":"A target-domain context observation; it does not establish the transfer.","tags":["target-context"],"data":{"paper_id":"paper:target","locator":"Methods, paragraph 1","excerpt":"Synthetic target-task context observation.","source_uri":"https://example.invalid/target","evidence_kind":"observation","assertion_role":"target_observation","applicability":{"domain":"synthetic target task","task_context":"stable-or-volatile target context"}}},
    {"id":"hypothesis:target","type":"hypothesis","title":"Target proposal","summary":"A conditional transfer proposal, not a source result.","tags":["proposed"],"data":{"prediction":"Under stable target context, intervention changes the target outcome.","alternatives":["Preparation rather than the proposed mechanism explains the effect."],"test":"Compare the intervention with matched control under stable and volatile target contexts.","status":"proposed","evidence_ids":["evidence:source","evidence:opposing","evidence:target-context"],"evidence_roles":{"evidence:source":"analogy","evidence:opposing":"counterevidence","evidence:target-context":"context"},"assertion_role":"transfer_proposal","applicability":{"source_domain":"synthetic source task","target_domain":"synthetic target task","task_context":"stable-or-volatile target context"},"transfer_assumptions":["target measurement is comparable to the source measurement","volatile target context may remove the effect"]}},
    {"id":"decision:bridge-boundary","type":"decision","title":"Keep the volatile boundary","summary":"Target test must expose the source failure condition.","tags":[],"data":{"reason":"The source counterexample limits the source assertion under volatile context; it does not observe a target limitation.","evidence_ids":["evidence:opposing","evidence:target-context"],"evidence_roles":{"evidence:opposing":"counterevidence","evidence:target-context":"context"},"next_action":"Run the smallest stable-versus-volatile target comparison.","applicability":{"target_domain":"synthetic target task","task_context":"stable-or-volatile target context"}}}
  ],
  "edges": [
    {"id":"edge:target-paper-evidence","source":"paper:target","target":"evidence:target-context","relation":"has_evidence","status":"structural","evidence_ids":[],"conditions":{},"applicability":{"domain":"synthetic target task","task_context":"stable-or-volatile target context"}},
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
