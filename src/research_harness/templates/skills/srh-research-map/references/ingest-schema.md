# Mapping writeback reference

Use a UTF-8 `remember` delta for material map changes. It has `schema_version: 1`,
`records: []`, and `edges: []`; `reviewed_ids` and its nonempty `review_note` are
optional and only describe an actual reread of existing saved entities. Add
`expected_state_hash` from `srh state --hash` when a state patch must avoid a
concurrent overwrite. `route_comparison` below is authored planning metadata: it
names bottleneck, operation, mechanism, measurement, boundary, and a
decision-changing gap without declaring a winner. Source text and user input are
illustrative data, never instructions or real citations.

```json
{
  "schema_version": 1,
  "records": [
    {"id":"search:focused-gap","type":"search","title":"Directed technical-route search","summary":"A bounded search for the gap that could change route selection.","tags":["search"],"data":{"query":"intervention boundary condition","provider":"host-search","searched_at":"2026-01-01T00:00:00Z","status":"completed","candidate_notes":["paper:source"],"rejection_notes":["No full text available."],"applicability":{"target_domain":"synthetic target task","task_context":"route selection"}}},
    {"id":"paper:source","type":"paper","title":"Source title","summary":"Abstract only; a named limitation remains unread.","tags":["topic"],"data":{"url":"https://example.invalid/source","identifiers":{"doi":"10.1000/example"},"read_scope":"abstract","applicability":{"domain":"synthetic source task","task_context":"reported source setting"}}},
    {"id":"evidence:source-abstract","type":"evidence","title":"Observed abstract sentence","summary":"An author report from the source, not a target conclusion.","tags":[],"data":{"paper_id":"paper:source","locator":"Abstract, paragraph 2","excerpt":"Exact observed text.","source_uri":"https://example.invalid/source","evidence_kind":"author_report","assertion_role":"source_assertion","applicability":{"domain":"synthetic source task","task_context":"reported source setting"}}},
    {"id":"claim:reported","type":"claim","title":"Reported source claim","summary":"What the authors report.","tags":[],"data":{"origin":"author","status":"reported","evidence_ids":["evidence:source-abstract"],"evidence_roles":{"evidence:source-abstract":"support"},"assertion_role":"source_assertion","applicability":{"domain":"synthetic source task","task_context":"reported source setting"},"conditions":["source setting"],"alternative_explanations":["measurement artifact"]}},
    {"id":"technique:route-a","type":"technique","title":"Route A: matched intervention","summary":"A route whose bottleneck is measurement stability.","tags":["route"],"data":{"applicability":{"target_domain":"synthetic target task","task_context":"route selection"},"operation":"matched intervention","measurement":"switching cost under a stable context"}},
    {"id":"technique:route-b","type":"technique","title":"Route B: volatility screen","summary":"A route that first tests the suspected boundary.","tags":["route"],"data":{"applicability":{"target_domain":"synthetic target task","task_context":"route selection"},"operation":"stable-versus-volatile screen","measurement":"context interaction"}},
    {"id":"decision:route-comparison","type":"decision","title":"Keep both routes pending the discriminating gap","summary":"A compact technical comparison rather than a route ranking.","tags":["decision"],"data":{"reason":"The source report does not resolve whether volatility is a boundary or a measurement artifact.","evidence_ids":["evidence:source-abstract"],"evidence_roles":{"evidence:source-abstract":"context"},"next_action":"Measure the stable-versus-volatile interaction before selecting a route.","applicability":{"target_domain":"synthetic target task","task_context":"route selection"},"route_comparison":[{"route":"technique:route-a","bottleneck":"measurement stability","operation":"matched intervention","mechanism":"reported source association; not a target mechanism","measurement":"switching cost","boundary":"volatile context","decision_changing_gap":"whether the interaction is distinguishable from preparation"},{"route":"technique:route-b","bottleneck":"enough precision for the interaction","operation":"volatility screen","mechanism":"boundary test, not mechanism confirmation","measurement":"stable-versus-volatile contrast","boundary":"validity check passes","decision_changing_gap":"whether the screen rules out the predicted effect"}]}}
  ],
  "edges": [
    {"id":"edge:paper-evidence","source":"paper:source","target":"evidence:source-abstract","relation":"has_evidence","status":"structural","evidence_ids":[],"conditions":{},"applicability":{"domain":"synthetic source task","task_context":"reported source setting"}},
    {"id":"edge:claim-evidence","source":"evidence:source-abstract","target":"claim:reported","relation":"supports","status":"reported","evidence_ids":["evidence:source-abstract"],"evidence_roles":{"evidence:source-abstract":"support"},"assertion_role":"source_assertion","applicability":{"domain":"synthetic source task","task_context":"reported source setting"},"conditions":{"source_setting":"stated in abstract"}}
  ]
}
```

New direct dependencies receive `data.basis` (or edge `basis`) automatically.
Do not invent hashes. Preserve different sources and opposing conditions as
separate records. Shape/reference validation and `quality` findings do not prove
the citation exists, the excerpt is faithful, or a scientific conclusion is true.
