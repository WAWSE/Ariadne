"""Small, deterministic helpers for assertion/evidence semantics.

The graph deliberately keeps scientific content as authored data.  These
helpers only describe whether the *recorded* data meets a narrow display or
quality contract; they never fill in a missing role, scope, or observation.
"""
from __future__ import annotations

from typing import Any

from .models import canonical_json


UNKNOWN = "unknown"

# Evidence kinds intentionally describe *what the saved item is*, rather than
# how strongly some later assertion may use it.  In particular, reading a
# paper is an observation of a passage, not an independent observation of the
# phenomenon the paper reports.  ``observation`` is accepted as a legacy
# spelling for old project stores; new write paths should use the explicit
# kinds below.
# An external author report is direct empirical evidence *attributed to its
# source*.  It must remain distinguishable from a local observation, but
# reading such a report does not make it unusable scientific evidence.  The
# scope/role checks below are what prevent it being silently promoted from a
# source-domain result into target-domain support.
DIRECT_EMPIRICAL_EVIDENCE_KINDS = frozenset({
    "local_observation", "observation", "author_report",
})
# Retained as a public compatibility name for callers that mean an observation
# recorded by this project rather than any direct empirical evidence.
DIRECT_OBSERVATION_KINDS = frozenset({"local_observation", "observation"})
REPORTED_RESULT_KINDS = frozenset({"author_report"})
MECHANISM_REPORT_KINDS = frozenset({"author_interpretation", "interpretation"})


def authored_data(item: Any) -> dict[str, Any]:
    """Return an item's authored payload (records) or fields (edges)."""

    if not isinstance(item, dict):
        return {}
    data = item.get("data", item)
    return data if isinstance(data, dict) else {}


def applicability(item: Any) -> dict[str, Any]:
    """Return only an explicitly authored applicability object."""

    value = authored_data(item).get("applicability")
    return value if isinstance(value, dict) else {}


def _unknown_scope_value(value: Any) -> bool:
    """Recognize missing values and the documented sentinel, not synonyms."""

    return value is None or (isinstance(value, str) and value.strip().casefold() in {"", UNKNOWN})


def _recorded_domain(item: Any, *, observed: bool) -> str | None:
    """Read an observed domain or a proposition's intended target, never mix them."""

    data = authored_data(item)
    scope = applicability(item)
    keys = ("domain", "source_domain") if observed else ("target_domain", "domain")
    # A present unknown field stays unknown; a more convenient alternate label
    # must not silently replace it. Evidence's intended target is not a record
    # of where an observation was made, even if it names this assertion.
    value = next((scope[key] for key in keys if key in scope), None)
    if not observed and not any(key in scope for key in keys):
        value = data.get("target_domain")  # schema-v1 assertion fallback
    if _unknown_scope_value(value):
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def applicability_domain(item: Any) -> str | None:
    """Return the authored observed (evidence) or target (assertion) domain."""

    return _recorded_domain(item, observed=isinstance(item, dict) and item.get("type") == "evidence")


def compare_recorded_applicability(evidence: Any, assertion: Any) -> str:
    """Compare saved fields as ``match``, ``different``, or ``unknown``.

    ``different`` reports unequal recorded values, NOT scientific conflict or
    semantic incompatibility. Different wording, overlapping sets and unit
    conversions require scientific interpretation outside this deterministic
    helper. ``match`` requires equal explicit, known scope; it is bookkeeping,
    not validation of the underlying science. Missing/unknown common values or
    extra keys prevent a match. A known field difference remains visible even
    when another field is missing. No synonym, subset or transfer inference.
    """

    evidence_scope, assertion_scope = applicability(evidence), applicability(assertion)
    evidence_domain = _recorded_domain(evidence, observed=True)
    assertion_domain = _recorded_domain(assertion, observed=False)
    different = evidence_domain is not None and assertion_domain is not None and evidence_domain != assertion_domain
    incomplete = evidence_domain is None or assertion_domain is None

    evidence_context = evidence_scope.get("task_context")
    assertion_context = assertion_scope.get("task_context")
    if evidence_context is not None or assertion_context is not None:
        left_valid = isinstance(evidence_context, str) and not _unknown_scope_value(evidence_context)
        right_valid = isinstance(assertion_context, str) and not _unknown_scope_value(assertion_context)
        if left_valid and right_valid:
            different = different or evidence_context != assertion_context
        else:
            incomplete = True

    def compare_conditions(left: Any, right: Any) -> tuple[bool, bool]:
        """Return (recorded_difference, incomplete_scope) without inference.

        The recursive walk makes a nested common condition such as
        ``split.seed`` visible even if a sibling key is absent.  We must see
        that difference before reporting the unrelated missing information as
        merely unknown.
        """

        if _unknown_scope_value(left) or _unknown_scope_value(right):
            return False, True
        if isinstance(left, dict) and isinstance(right, dict):
            conflict = False
            incomplete_here = set(left) != set(right)
            for key in set(left) & set(right):
                child_conflict, child_incomplete = compare_conditions(left[key], right[key])
                conflict = conflict or child_conflict
                incomplete_here = incomplete_here or child_incomplete
            return conflict, incomplete_here
        if isinstance(left, dict) or isinstance(right, dict):
            return True, False
        if isinstance(left, list) and isinstance(right, list):
            comparisons = [compare_conditions(a, b) for a, b in zip(left, right)]
            return (len(left) != len(right) or any(diff for diff, _ in comparisons),
                    any(missing for _, missing in comparisons))
        if isinstance(left, list) or isinstance(right, list):
            return True, False
        return canonical_json(left) != canonical_json(right), False

    evidence_conditions = evidence_scope.get("conditions")
    assertion_conditions = assertion_scope.get("conditions")
    if not isinstance(evidence_conditions, dict) or not isinstance(assertion_conditions, dict):
        incomplete = True
    else:
        conditions_different, conditions_incomplete = compare_conditions(evidence_conditions, assertion_conditions)
        different = different or conditions_different
        incomplete = incomplete or conditions_incomplete

    # A recorded difference wins over missing information elsewhere. It never
    # asserts that those differences actually invalidate scientific transfer.
    if different:
        return "different"
    return UNKNOWN if incomplete else "match"


# Preserve imports for existing integrations, but not the misleading legacy
# ``mismatch`` label. Both names now return recorded-field comparison only.
applicability_compatibility = compare_recorded_applicability


def evidence_kind(evidence: Any) -> str:
    """Return the authored evidence kind, or explicit ``unknown``."""

    value = authored_data(evidence).get("evidence_kind")
    return value if isinstance(value, str) and value.strip() else UNKNOWN


def assertion_role(assertion: Any) -> str:
    """Return the legacy descriptive role without assigning a usage role."""

    value = authored_data(assertion).get("assertion_role")
    return value if isinstance(value, str) and value.strip() else UNKNOWN


def usage_role(assertion: Any, evidence_id: str) -> str:
    """Return one evidence's role *for this assertion*, never globally.

    ``evidence_roles`` is stored on a record (including a decision) or edge.
    For a record the role is relative to its proposition/decision; on an
    assertion edge (supports/contradicts/limits) it is relative to its target.
    An evidence record's legacy ``assertion_role`` intentionally has no bearing
    here, so the same observation can support one assertion and contextualize
    or limit another.
    """

    roles = authored_data(assertion).get("evidence_roles")
    value = roles.get(evidence_id) if isinstance(roles, dict) else None
    return value if isinstance(value, str) and value.strip() else UNKNOWN


def evidence_usage_roles(assertion: Any) -> list[tuple[str, str]]:
    """Return every listed evidence usage, including missing roles as unknown."""

    data = authored_data(assertion)
    evidence_ids = data.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return []
    return [(evidence_id, usage_role(assertion, evidence_id)) for evidence_id in evidence_ids if isinstance(evidence_id, str)]


def assertion_required_evidence_kinds(assertion: Any) -> frozenset[str]:
    """Return the narrow evidence taxonomy required by an established assertion.

    This is a bookkeeping contract, not a claim that a model can determine
    scientific truth. ``reported`` says an author made a report (or an author
    offered a mechanism interpretation). ``supported``, ``contradicted``, and
    ``inconclusive`` need an explicitly saved direct empirical item. That may
    be a local observation or an attributed external author report; neither
    is inferred from a source excerpt. Proposed and active assertions
    intentionally require no direct support.
    """

    data = authored_data(assertion)
    status = data.get("status")
    typ = assertion.get("type") if isinstance(assertion, dict) else None
    if status == "reported":
        return MECHANISM_REPORT_KINDS if typ == "mechanism" else REPORTED_RESULT_KINDS
    if status in {"supported", "contradicted", "inconclusive"}:
        return DIRECT_EMPIRICAL_EVIDENCE_KINDS
    return frozenset()


def assertion_required_evidence_roles(assertion: Any) -> frozenset[str]:
    """Return the allowed *directional use* of qualifying assertion evidence.

    Evidence direction belongs to an assertion/evidence pairing, not to an
    evidence record globally.  In particular, a counterexample may qualify a
    contradicted assertion, and an indeterminate finding need not be recorded
    as positive support merely because a hypothesis is now inconclusive.

    Older stores that used ``support`` or ``counterevidence`` for an
    inconclusive record remain readable. New writing can state the neutral
    direction directly with the ``inconclusive`` usage role. ``analogy`` and
    ``context`` never qualify an established target assertion: they retain
    useful provenance but do not themselves assess that assertion.
    """

    status = authored_data(assertion).get("status")
    if status in {"reported", "supported"}:
        return frozenset({"support"})
    if status == "contradicted":
        return frozenset({"counterevidence"})
    if status == "inconclusive":
        return frozenset({"inconclusive", "support", "counterevidence"})
    return frozenset()


def is_qualified_assertion_evidence(assertion: Any, evidence: Any) -> bool:
    """Whether evidence meets the explicit directional contract for one assertion.

    The assertion must name the evidence with a role compatible with its
    recorded status. It must be one of that status's allowed evidence kinds
    and have exact explicit applicability. This never promotes an assertion;
    callers use it only to explain missing provenance or render conservative
    labels. In particular, an ``author_report`` remains an attributed
    external report, not a local observation.
    """

    if not isinstance(assertion, dict) or not isinstance(evidence, dict):
        return False
    evidence_id = evidence.get("id")
    if not isinstance(evidence_id, str):
        return False
    required_kinds = assertion_required_evidence_kinds(assertion)
    required_roles = assertion_required_evidence_roles(assertion)
    return (
        bool(required_kinds)
        and usage_role(assertion, evidence_id) in required_roles
        and evidence_kind(evidence) in required_kinds
        and compare_recorded_applicability(evidence, assertion) == "match"
    )


def is_qualified_observed_limiting_relation(edge: Any, assertion: Any, evidence: Any) -> bool:
    """Whether an edge may be rendered as an observed target limitation.

    The evidence must be named by that edge, use a counterevidence role on the
    edge or current assertion, be an observation, and exactly match the
    assertion's authored scope.  Each condition is deliberately conjunctive.
    """

    if not isinstance(edge, dict) or not isinstance(assertion, dict) or not isinstance(evidence, dict):
        return False
    assertion_id = assertion.get("id")
    evidence_id = evidence.get("id")
    if not isinstance(assertion_id, str) or not isinstance(evidence_id, str):
        return False
    if edge.get("target") != assertion_id:
        return False
    if edge.get("relation") not in {"limits", "contradicts"} or edge.get("status") != "observed":
        return False
    edge_evidence_ids = edge.get("evidence_ids")
    if not isinstance(edge_evidence_ids, list) or evidence_id not in edge_evidence_ids:
        return False
    if usage_role(edge, evidence_id) != "counterevidence" and usage_role(assertion, evidence_id) != "counterevidence":
        return False
    return evidence_kind(evidence) in DIRECT_OBSERVATION_KINDS and compare_recorded_applicability(evidence, assertion) == "match"
