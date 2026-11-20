# Scoped reading writeback reference

Use a small `remember` delta after a material reading, with `schema_version: 1`,
`records`, and `edges`. `reviewed_ids` plus a nonempty `review_note` are optional
and only apply when explicitly rereading existing saved entities. `evidence_kind`,
`assertion_role`, and `applicability` below are authored labels that preserve the
difference between a checked source passage, an author report, an author
interpretation, a local observation, and a plan; they do not verify a
source or transfer it to another domain. `remember` captures direct dependency
baselines; it never makes an observation true. All `example.invalid` values below
are illustrative, not citations.

```json
{
  "schema_version": 1,
  "records": [
    {"id":"paper:source","type":"paper","title":"Source title","summary":"Methods section read; discussion remains unread.","tags":["topic"],"data":{"url":"https://example.invalid/source","identifiers":{},"read_scope":"sections","read_sections":["Methods"],"applicability":{"domain":"synthetic source task","task_context":"specified source sample"}}},
    {"id":"evidence:source-methods","type":"evidence","title":"Checked methods passage","summary":"A checked source-method passage, not a measured local result.","tags":[],"data":{"paper_id":"paper:source","locator":"Methods, paragraph 3","excerpt":"Exact observed text.","source_uri":"https://example.invalid/source","evidence_kind":"source_excerpt","assertion_role":"source_assertion","applicability":{"domain":"synthetic source task","task_context":"specified source sample"}}},
    {"id":"evidence:author-report","type":"evidence","title":"Authors' reported result","summary":"A report attributed to the authors rather than a new observation.","tags":[],"data":{"paper_id":"paper:source","locator":"Results, paragraph 2","excerpt":"The authors report the stated result.","source_uri":"https://example.invalid/source","evidence_kind":"author_report","assertion_role":"source_assertion","applicability":{"domain":"synthetic source task","task_context":"specified source sample"}}},
    {"id":"evidence:author-interpretation","type":"evidence","title":"Authors' interpretation","summary":"An attributed explanation, kept distinct from the reported result.","tags":[],"data":{"paper_id":"paper:source","locator":"Discussion, paragraph 1","excerpt":"The authors interpret the result as a mechanism.","source_uri":"https://example.invalid/source","evidence_kind":"author_interpretation","assertion_role":"interpretation","applicability":{"domain":"synthetic source task","task_context":"specified source sample"}}},
    {"id":"claim:reported","type":"claim","title":"Reported source claim","summary":"The authors' reported result, separate from interpretation.","tags":[],"data":{"origin":"author","status":"reported","evidence_ids":["evidence:author-report"],"evidence_roles":{"evidence:author-report":"support"},"assertion_role":"source_assertion","applicability":{"domain":"synthetic source task","task_context":"specified source sample"},"conditions":["specified sample"],"alternative_explanations":["unread discussion limitations"]}},
    {"id":"mechanism:author-interpretation","type":"mechanism","title":"Authors' proposed mechanism","summary":"An attributed interpretation, not an established target mechanism.","tags":[],"data":{"origin":"author","status":"reported","evidence_ids":["evidence:author-interpretation"],"evidence_roles":{"evidence:author-interpretation":"support"},"assertion_role":"interpretation","applicability":{"domain":"synthetic source task","task_context":"specified source sample"}}},
    {"id":"limitation:scope-gap","type":"limitation","title":"Discussion not read","summary":"No claim about the unread section.","tags":[],"data":{"evidence_ids":[],"assertion_role":"limitation","applicability":{"domain":"synthetic source task","task_context":"specified source sample"}}}
  ],
  "edges": [
    {"id":"edge:paper-observation","source":"paper:source","target":"evidence:source-methods","relation":"has_evidence","status":"structural","evidence_ids":[],"conditions":{},"applicability":{"domain":"synthetic source task","task_context":"specified source sample"}},
    {"id":"edge:paper-report","source":"paper:source","target":"evidence:author-report","relation":"has_evidence","status":"structural","evidence_ids":[],"conditions":{},"applicability":{"domain":"synthetic source task","task_context":"specified source sample"}},
    {"id":"edge:paper-interpretation","source":"paper:source","target":"evidence:author-interpretation","relation":"has_evidence","status":"structural","evidence_ids":[],"conditions":{},"applicability":{"domain":"synthetic source task","task_context":"specified source sample"}},
    {"id":"edge:claim-report","source":"evidence:author-report","target":"claim:reported","relation":"supports","status":"reported","evidence_ids":["evidence:author-report"],"evidence_roles":{"evidence:author-report":"support"},"assertion_role":"source_assertion","applicability":{"domain":"synthetic source task","task_context":"specified source sample"},"conditions":{"scope":"Results"}},
    {"id":"edge:mechanism-interpretation","source":"evidence:author-interpretation","target":"mechanism:author-interpretation","relation":"supports","status":"reported","evidence_ids":["evidence:author-interpretation"],"evidence_roles":{"evidence:author-interpretation":"support"},"assertion_role":"interpretation","applicability":{"domain":"synthetic source task","task_context":"specified source sample"},"conditions":{"scope":"Discussion"}}
  ]
}
```

Use exact locators and short observed excerpts. A source card's `read_scope` is
metadata, abstract, sections, or fulltext; `read_sections` names the actual
sections seen. Do not turn an author rationale into an observation. Mechanical
validation cannot verify a paper, excerpt, causal identification, or conclusion.
