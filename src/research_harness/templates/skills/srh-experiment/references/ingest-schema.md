# Experiment writeback reference

An experiment record makes an authorized plan inspectable; SRH will not execute
its command. Use `remember` for a planned experiment or a meaningful result, not
every tentative idea or tool call. A valid completed result needs its reproducibility
fields. Record which result would continue, change, park, or reopen the route; this
is a planning decision, not a prediction of the result. The block assumes an
existing `hypothesis:target`; it is a dependent delta.

```json
{
  "schema_version": 1,
  "records": [
    {"id":"experiment:target-test","type":"experiment","title":"Planned discriminating test","summary":"Separates predicted mechanism from a preparation alternative.","tags":["planned"],"data":{"hypothesis_id":"hypothesis:target","status":"planned","command":"python run_test.py --seed 7","budget":"One authorized local run; 10 minutes CPU.","config":{"control":"matched","contexts":["stable","volatile"]},"seed":7,"code_ref":"repo revision or path","data_ref":"approved dataset reference","metrics":["primary metric","validity check"],"artifacts":[],"outcome":null,"applicability":{"target_domain":"synthetic target task","task_context":"stable-or-volatile target context"},"route_changes":{"support":"Continue the target route and inspect the preparation alternative only as a residual limitation.","contradict":"Park the transfer route and compare the preparation alternative before reopening it.","inconclusive":"Improve precision or measurement before choosing a route.","invalid":"Repair the validity check; do not update route choice."}}},
    {"id":"decision:outcome-criteria","type":"decision","title":"Outcome criteria","summary":"Negative, inconclusive, and invalid outcomes are not interchangeable.","tags":[],"data":{"reason":"Precommit interpretation and route consequences before observing the result.","evidence_ids":[],"next_action":"Run the authorized comparison, then apply the recorded route consequence rather than auto-promoting the hypothesis.","applicability":{"target_domain":"synthetic target task","task_context":"stable-or-volatile target context"},"route_changes":{"support":"Continue the target route.","contradict":"Park the transfer and test the preparation alternative.","inconclusive":"Keep both routes unresolved and improve precision.","invalid":"Reopen only after the validity check is repaired."}}}
  ],
  "edges": [
    {"id":"edge:test","source":"experiment:target-test","target":"hypothesis:target","relation":"tests","status":"structural","evidence_ids":[],"conditions":{"positive":"predicted interaction","negative":"prespecified effect excluded with adequate precision","inconclusive":"null estimate too imprecise to exclude the effect","invalid":"failed measurement or execution validity check"},"applicability":{"target_domain":"synthetic target task","task_context":"stable-or-volatile target context"}}
  ]
}
```

For a result, link a result/evidence record to the experiment with metrics,
artifacts, interpretation, and limitations. Do not auto-promote the hypothesis:
support, contradiction, inconclusive result, and invalid run remain distinct.
