---
name: srh-experiment
description: Design or record an authorized, cheap discriminating experiment while keeping negative and invalid outcomes distinct.
---

Use this when an uncertainty is consequential enough to test. Begin with
`srh context --intent experiment --focus hypothesis-or-experiment-ID`. Choose
the cheapest test that can distinguish the prediction from a credible alternative;
there is no fixed research phase order. Only run work within explicit scope,
budget, privacy constraints, and authorization.

Before execution, make intervention/control, prediction, alternatives, metrics,
stopping/budget, config, seed, code/data references, and artifacts inspectable.
State in advance what would count as positive support, an interpretable negative
result, an inconclusive/null result, and an invalid measurement or execution—and
which of those outcomes would continue, change, park, or reopen the current
technical route. A command in a record is not permission to run paid,
destructive, networked, or private-data work.

After an authorized run, retain outcome, artifacts, limitations, and local
evidence. A completed result never automatically confirms a hypothesis. Record
planned and actually run work explicitly; do not persist every tentative idea or
tool call. Finish when the planned test or its recorded limitation is clear; write
it with `remember` only if it changes future work. See [the experiment schema](references/ingest-schema.md).
The harness never executes a recorded command.
