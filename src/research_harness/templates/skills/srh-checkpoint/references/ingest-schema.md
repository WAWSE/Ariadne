# State and checkpoint writeback reference

Read the current state before a state-changing checkpoint:

```text
srh state --hash
srh context --intent resume --max-chars 3000
```

Apply a delta atomically. `expected_state_hash` is optional and is the lowercase
SHA-256 state hash returned by `state --hash`; state-only changes can use
`srh state --file PATCH --expected-hash HASH`. The example's review IDs assume
those entities already exist and were actually reread. Route, parked alternative,
and reopen trigger are authored handoff metadata, not claims that a route has been
scientifically resolved.

```json
{
  "schema_version": 1,
  "expected_state_hash": "<current state_hash>",
  "reviewed_ids": ["hypothesis:target", "evidence:counterexample"],
  "review_note": "Reviewed the saved target proposal and counterexample before handoff.",
  "records": [
    {"id":"decision:next","type":"decision","title":"Research decision","summary":"A durable, evidence-aware checkpoint.","tags":["checkpoint"],"data":{"reason":"The counterexample leaves the boundary unresolved.","evidence_ids":["evidence:counterexample"],"evidence_roles":{"evidence:counterexample":"counterevidence"},"next_action":"Read the named limitation section before selecting another experiment.","applicability":{"target_domain":"synthetic target task","task_context":"current route handoff"},"current_route":"Read the named limitation before selecting another experiment.","parked_alternative":"Preparation explanation remains parked, not rejected.","reopen_trigger":"Reopen the parked alternative if the limitation weakens the volatility boundary or the planned comparison contradicts it."}}
  ],
  "edges": [],
  "state_patch": {"recent_decisions":["decision:next"],"next_action":"Read the named limitation section before selecting another experiment.","current_route":"Read the named limitation before selecting another experiment.","parked_alternatives":["Preparation explanation remains parked, not rejected."],"reopen_triggers":["Reopen if the limitation weakens the volatility boundary or the planned comparison contradicts it."]}
}
```

`reviewed_ids` requires `review_note` and creates a review receipt; it records
that a person/agent says it reread those entities, not that they are scientifically
true. Omit both when no existing entity was reread. Stop/checkpoint exports can
only include records already saved to SQLite.

For an ambiguous submission retry, optionally add a nonempty field such as
`"delta_id": "handoff-retry-01"` to the complete delta **before its first
submission**. Repeating that exact full delta returns the original result plus
`replayed: true` without another write, event, or review receipt; reusing the key
with different content errors. It is not required for normal `remember`: without
it, another explicit review is deliberately a new receipt.
