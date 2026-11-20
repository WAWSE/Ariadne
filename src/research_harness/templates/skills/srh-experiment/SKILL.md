---
name: srh-experiment
description: Design or record an authorized, cheap discriminating experiment while keeping negative and invalid outcomes distinct.
---

Use this when an uncertainty is consequential enough to test. Reuse a matching
brief already returned in this session; otherwise run plain `srh context` once.
If it already names the proposition/conditions, qualified results, execution
state, and unresolved decision needed to design the test, choose the test
directly. This is a retrieval stop rule, not a scientific sufficiency claim.
When a named missing field could change the design, prefer one batch `show ID1
ID2`. Use `srh context --intent experiment --focus ID` only when the evidence
relationship or task context differs from the available brief. Do not use
`--help`, `list`, `graph`, `state`, or `quality` merely to reconfirm it; quality
is for explicit quality/status-upgrade/dependency work, audits, or broad
exploration. Choose the cheapest test that can distinguish the prediction from
a credible alternative; there is no fixed research phase order. Only run work
within explicit scope, budget, privacy constraints, and authorization.

Before choosing the next experiment, compare it with completed valid observations
under the requested conditions. Identify the remaining uncertainty: an unmeasured
effect, conflicting results, mechanism attribution, or transfer to new conditions.
Choose a comparison that separates the remaining explanations, not merely the
saved next action. Repeating a measured condition is useful when its replication
or protocol-control purpose is explicit; do not describe it as a first observation.

Before using a result to update a forecast or allocation, apply the transfer/update
gate. Label the result as a historical/source report, a verified reanalysis, a new
target observation, or a forecast, and state the estimand it measures. Compare the
source and target across population/cohort, intervention, time, outcome, reference,
and data-generating process. Matching labels or conditions do not establish a
matching estimand. If any decisive field differs or is unknown, keep the result as
source-domain evidence: do not overwrite a target forecast or treat it as a
terminal outcome. State the transfer assumption, failure risk, and one competing
prediction or decision-changing test. Only a new observation with an explicitly
matching target estimand may directly update that target value; unresolved transfer
risk must remain visible in allocation and may justify replication or defer.

When the estimand does not match, do not apply a residual, offset, calibration,
or copied point estimate to the target forecast. Keep the target numeric forecast
unchanged and store the transferred value as a separately labelled conditional
sensitivity or range with its assumption. An allocation may use that scenario only
as an explicit robustness analysis; it is not an observed target outcome.

For a forecast handoff, save the baseline and any conditional sensitivity as
separate artifacts and run `.research/srh forecast-update --check FILE` before
committing the receipt with `.research/srh forecast-update FILE`. The receipt
checks artifact hashes and refuses a mismatched-estimand point replacement; it
does not execute a model or edit the forecast for you.

Before execution, make intervention/control, prediction, alternatives, metrics,
stopping/budget, config, seed, code/data references, and artifacts inspectable.
State in advance what would count as positive support, an interpretable negative
result, an inconclusive/null result, and an invalid measurement or execution—and
which of those outcomes would continue, change, park, or reopen the current
technical route. A command in a record is not permission to run paid,
destructive, networked, or private-data work.

When predicting an intervention's effect on a comparison, specify what changes
in both the studied system and its reference. Removing one information source
need not restore the reference outcome: a frozen system may deteriorate, and
the reference may shift too. If those responses are unidentified, give conditional
predictions or a range rather than assuming a zero contrast or a unique mechanism
diagnosis.

When a decision depends on computed numbers, check a decisive value against
the stated calculation and the saved artifact, including numeric precision,
clipping, ties, and candidate ID-to-condition mapping. Valid ranges and complete
JSON do not establish that the intended calculation ran. Reuse the calculation
path for this check; do not add a general code audit. Keep display rounding
separate from decision precision and preserve the original forecast on revision.

After an authorized run, retain outcome, artifacts, limitations, and local
evidence. A completed result never automatically confirms a hypothesis. Record
planned and actually run work explicitly; do not persist every tentative idea or
tool call. Finish when the planned test or its recorded limitation is clear; write
it with `remember` only if it changes future work. A negative or no-response
outcome does not identify a unique cause; retain plausible alternatives, while
replication can reduce uncertainty. Evaluate the predicted effect separately from
mechanism attribution—mechanism testing is not required for every experiment. See
[the experiment schema](references/ingest-schema.md).

After a completed result, treat it as condition-bound evidence and re-enumerate
all currently legal terminal routes, including stop/defer when allowed. Compare
each route under current conditions, posterior/uncertainty, and utility; do not
promote a pre-run conditional route map into a conclusion. If public material
fully specifies priors, likelihoods, terminal payoffs, and costs, calculate the
action and post-outcome route utilities mechanically. Once an action/outcome is
fixed, do not select another action or observation or charge its sunk cost again.
Without that complete model, do not invent probabilities or utilities.
The harness never executes a recorded command.
