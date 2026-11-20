"""Deterministic, offline graph-quality checks.

The checks in this module deliberately describe record completeness and
provenance maintenance.  They never verify papers, excerpts, experiments, or
scientific conclusions.
"""
from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any

from .models import (
    EDGE_RELATIONS,
    dependency_content_hash,
    edge_dependency_refs,
    record_dependency_refs,
)
from .semantics import (
    compare_recorded_applicability,
    applicability_domain as _target_domain,
    assertion_required_evidence_kinds,
    assertion_required_evidence_roles,
    is_qualified_assertion_evidence,
)


_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_ESTABLISHED_STATUSES = {"reported", "supported", "contradicted", "inconclusive"}


def _entity_key(kind: str, entity_id: str) -> str:
    return f"{kind}:{entity_id}"


def _normal_identifier(kind: str, value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().casefold()
    if kind == "doi":
        text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
        text = re.sub(r"^doi:\s*", "", text)
        return text.rstrip(" .") or None
    if kind == "arxiv":
        text = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", text)
        text = re.sub(r"^arxiv:\s*", "", text)
        text = re.sub(r"\.pdf$", "", text)
        # Versions identify a revision of the same preprint, not a different
        # candidate paper for the conservative duplicate warning.
        return re.sub(r"v\d+$", "", text) or None
    return text


def _relation_is_compatible(relation: str, source_type: str | None, target_type: str | None) -> bool:
    """The same conservative relation matrix used by ingest validation."""

    all_types = {
        "paper", "source", "problem", "method", "technique", "mechanism", "claim",
        "evidence", "hypothesis", "experiment", "result", "limitation",
        "decision", "search",
    }
    pairs: dict[str, tuple[set[str], set[str]]] = {
        "cites": (all_types - {"evidence", "result", "search"}, {"paper"}),
        "addresses": (all_types - {"evidence", "result", "search"}, {"problem"}),
        "uses": ({"paper", "method", "technique", "mechanism", "claim", "hypothesis", "experiment", "result"}, {"paper", "method", "technique", "mechanism", "evidence", "result"}),
        "motivates": (all_types - {"search"}, {"problem", "claim", "hypothesis", "experiment", "decision"}),
        "proposes_mechanism": ({"paper", "claim", "hypothesis", "evidence", "decision", "result"}, {"mechanism"}),
        "tests": ({"experiment", "paper", "evidence", "result"}, {"hypothesis", "claim", "mechanism", "method", "technique"}),
        "supports": ({"paper", "evidence", "experiment", "result", "claim", "hypothesis"}, {"claim", "hypothesis", "mechanism", "method", "technique", "limitation"}),
        "contradicts": ({"paper", "evidence", "experiment", "result", "claim", "hypothesis"}, {"claim", "hypothesis", "mechanism", "method", "technique", "limitation"}),
        "limits": ({"paper", "evidence", "experiment", "result", "claim", "hypothesis", "limitation"}, {"claim", "hypothesis", "mechanism", "method", "technique", "experiment"}),
        "analogous_to": (all_types - {"search"}, all_types - {"search"}),
        "derived_from": ({"mechanism", "method", "technique", "claim", "hypothesis", "result"}, {"paper", "claim", "evidence", "mechanism", "method", "technique", "result"}),
        "has_evidence": (all_types - {"evidence", "search"}, {"evidence"}),
        # Authored decision lineage, not scientific contradiction or automatic
        # supersession. Conditions retain what changed and what remains valid.
        "revises": ({"decision"}, {"decision"}),
    }
    if relation == "produces":
        return (source_type == "search" and target_type in {"paper", "evidence"}) or (
            source_type == "experiment" and target_type in {"result", "evidence"}
        )
    allowed = pairs.get(relation)
    return bool(allowed and source_type in allowed[0] and target_type in allowed[1])


def relation_is_compatible(relation: str, source_type: str | None, target_type: str | None) -> bool:
    """Public mechanical relation check for the store's ingest path."""

    return relation in EDGE_RELATIONS and _relation_is_compatible(relation, source_type, target_type)


def _record_provenance_fields(record: dict[str, Any]) -> list[tuple[str, str]]:
    data = record.get("data", {})
    if not isinstance(data, dict):
        return []
    refs: list[tuple[str, str]] = []
    typ = record.get("type")
    if typ == "evidence":
        ref = data.get("source_id") or data.get("paper_id") or data.get("experiment_id")
        if isinstance(ref, str) and ref:
            refs.append((ref, "source_id" if data.get("source_id") else "paper_id" if data.get("paper_id") else "experiment_id"))
    elif typ == "experiment" and isinstance(data.get("hypothesis_id"), str):
        refs.append((data["hypothesis_id"], "hypothesis_id"))
    elif typ == "result" and isinstance(data.get("experiment_id"), str):
        refs.append((data["experiment_id"], "experiment_id"))
    for ref in data.get("evidence_ids", []) if isinstance(data.get("evidence_ids", []), list) else []:
        if isinstance(ref, str) and ref:
            refs.append((ref, "evidence_ids"))
    return list(dict.fromkeys(refs))


def _typed_record_refs(record: dict[str, Any]) -> list[tuple[Any, str]]:
    """The reference type contract, retained for diagnosing legacy rows."""

    data = record.get("data", {})
    if not isinstance(data, dict):
        return []
    refs: list[tuple[Any, str]] = []
    if record.get("type") == "evidence":
        refs.append((
            data.get("source_id") or data.get("paper_id") or data.get("experiment_id"),
            "source" if data.get("source_id") else "paper" if data.get("paper_id") else "experiment",
        ))
    elif record.get("type") == "experiment":
        refs.append((data.get("hypothesis_id"), "hypothesis"))
    elif record.get("type") == "result":
        refs.append((data.get("experiment_id"), "experiment"))
    refs.extend((ref, "evidence") for ref in data.get("evidence_ids", []) if isinstance(data.get("evidence_ids", []), list))
    return refs


def _scope_entities(records: list[dict[str, Any]], edges: list[dict[str, Any]], focus: str | None) -> tuple[set[str], bool]:
    """Return a bounded connected provenance neighbourhood for a focus."""

    keys: dict[str, tuple[str, dict[str, Any]]] = {}
    record_by_id: dict[str, str] = {}
    for record in records:
        key = _entity_key("record", record["id"])
        keys[key] = ("record", record)
        record_by_id[record["id"]] = key
    for edge in edges:
        keys[_entity_key("edge", edge["id"])] = ("edge", edge)
    if focus is None:
        return set(keys), False
    # Focus is an exact durable identifier, never a title substring or a
    # case-folded text query.  A quality command for paper:A must not quietly
    # report a similarly titled, unrelated record as though it were in scope.
    literal = {key for key, (_kind, item) in keys.items() if item.get("id") == focus}
    # IDs commonly look like ``edge:foo``.  Interpret a typed selector only
    # when it is not already a literal durable ID; ``edge:edge:foo`` selects
    # the edge whose actual ID is ``edge:foo``.
    if literal:
        starts = literal
    elif focus.startswith("record:"):
        candidate = _entity_key("record", focus[7:])
        starts = {candidate} if candidate in keys else set()
    elif focus.startswith("edge:"):
        candidate = _entity_key("edge", focus[5:])
        starts = {candidate} if candidate in keys else set()
    else:
        starts = set()
    if not starts:
        return set(), False
    # Dependencies form provenance links.  Their reverse links make a revised
    # source focus include dependent evidence, claims, and structural edges.
    adjacent: dict[str, set[str]] = defaultdict(set)
    for key, (kind, item) in keys.items():
        deps = record_dependency_refs(item) if kind == "record" else edge_dependency_refs(item)
        for dep in deps:
            dep_key = record_by_id.get(dep)
            if dep_key:
                adjacent[key].add(dep_key)
                adjacent[dep_key].add(key)
    chosen = set(starts)
    queue = deque(sorted(starts))
    max_entities = 500
    truncated = False
    while queue:
        key = queue.popleft()
        for other in sorted(adjacent[key]):
            if other in chosen:
                continue
            if len(chosen) >= max_entities:
                truncated = True
                continue
            chosen.add(other)
            queue.append(other)
    return chosen, truncated


def assess_quality(
    records: list[dict[str, Any]], edges: list[dict[str, Any]], focus: str | None = None,
    limit: int = 100, identifier_records: list[dict[str, Any]] | None = None,
    known_types: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assess mechanical graph maintenance without judging scientific truth."""

    if not isinstance(focus, (str, type(None))):
        raise ValueError("focus must be a string or null")
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    selected, scope_truncated = _scope_entities(records, edges, focus)
    record_by_id = {record["id"]: record for record in records}
    entity_items: list[tuple[str, str, dict[str, Any]]] = []
    for record in records:
        key = _entity_key("record", record["id"])
        if key in selected:
            entity_items.append((key, "record", record))
    for edge in edges:
        key = _entity_key("edge", edge["id"])
        if key in selected:
            entity_items.append((key, "edge", edge))

    findings: list[dict[str, Any]] = []

    def add(
        code: str, severity: str, entity_ids: list[str], message: str,
        *, subject_id: str | None = None, subject_kind: str | None = None,
    ) -> None:
        # ``entity_ids`` retains all involved provenance.  The explicit subject
        # removes ambiguity for callers: a stale edge which mentions claim:C
        # must not look like a stale claim:C.
        ordered_ids = sorted(dict.fromkeys(entity_ids))
        finding: dict[str, Any] = {"code": code, "severity": severity, "entity_ids": ordered_ids, "message": message}
        if subject_id is not None:
            finding["subject_id"] = subject_id
        elif entity_ids:
            finding["subject_id"] = entity_ids[0]
        if subject_kind is not None:
            finding["subject_kind"] = subject_kind
        findings.append(finding)

    if focus is not None and not selected:
        add("unknown_focus", "error", [focus], "No record or edge has this exact focus ID; no graph-quality scope was assessed.")

    # Record/source completeness checks.
    # Identifier normalization is intentionally global but lightweight.  The
    # finding is emitted only if it intersects the focused graph, so checking
    # paper:A can reveal unconnected paper:B with the same DOI without turning
    # all of paper:B's unrelated quality findings into the result.
    identifier_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    alias_groups: dict[str, list[str]] = defaultdict(list)
    selected_record_ids = {item["id"] for _key, kind, item in entity_items if kind == "record"}
    for paper in (identifier_records if identifier_records is not None else records):
        if paper.get("type") != "paper":
            continue
        data = paper.get("data", {})
        if not isinstance(data, dict):
            continue
        identifiers = data.get("identifiers", {})
        if isinstance(identifiers, dict):
            for raw_kind, value in identifiers.items():
                ident_kind = str(raw_kind).casefold()
                if ident_kind not in {"doi", "arxiv"}:
                    continue
                for candidate in value if isinstance(value, list) else [value]:
                    normalized = _normal_identifier(ident_kind, candidate)
                    if normalized:
                        identifier_groups[(ident_kind, normalized)].append(paper["id"])
        aliases = data.get("aliases", [])
        if isinstance(aliases, (list, str)):
            for alias in aliases if isinstance(aliases, list) else [aliases]:
                normalized = _normal_identifier("alias", alias)
                if normalized:
                    alias_groups[normalized].append(paper["id"])
    for _key, kind, item in entity_items:
        if kind != "record":
            continue
        typ, item_id, data = item.get("type"), item["id"], item.get("data", {})
        if not isinstance(data, dict):
            continue
        if typ == "paper":
            scope = data.get("read_scope")
            if not isinstance(scope, str) or not scope.strip():
                add("missing_read_scope", "warning", [item_id], "Paper has no recorded read_scope; source coverage is unknown.")
            elif scope in {"metadata", "abstract"}:
                add("limited_read_scope", "info", [item_id], f"Paper read_scope is {scope!r}; it is limited source coverage, not full-text verification.")
        elif typ == "evidence":
            # A map writer must make the evidence qualification explicit.  A
            # legacy record may still be readable, but an omitted/unknown
            # kind or scope is never allowed to qualify an assertion.
            evidence_kind_value = data.get("evidence_kind")
            if not isinstance(evidence_kind_value, str) or not evidence_kind_value.strip():
                add("missing_evidence_kind", "warning", [item_id], "Evidence has no explicit evidence_kind; treat its role as unknown.")
            elif evidence_kind_value.strip().casefold() == "unknown":
                add("unknown_evidence_kind", "info", [item_id], "Evidence kind is explicitly unknown; it cannot qualify an established assertion.")
            applicability_value = data.get("applicability")
            if not isinstance(applicability_value, dict):
                add("missing_applicability", "warning", [item_id], "Evidence has no explicit applicability object; domain and conditions remain unknown.")
            else:
                if _target_domain(item) is None:
                    add("unknown_applicability", "info", [item_id], "Evidence applicability is unknown; it cannot qualify a target assertion.")
            locator = data.get("locator")
            if not isinstance(locator, str) or not locator.strip():
                add("missing_locator", "warning", [item_id], "Evidence has no usable source locator.")
            elif locator.strip().casefold() in {"paper", "source", "abstract", "fulltext", "full text"}:
                add("weak_locator", "info", [item_id], "Evidence locator is broad; retain a section, page, figure, or paragraph when available.")
            if not isinstance(data.get("source_uri"), str) or not data.get("source_uri", "").strip():
                add("missing_source_uri", "info", [item_id], "Evidence has no source_uri; its typed paper/experiment/source reference remains the provenance link.")
        elif typ == "mechanism":
            if not isinstance(data.get("origin"), str) or not data.get("origin", "").strip():
                add("mechanism_missing_origin", "warning", [item_id], "Mechanism has no recorded origin; keep interpretation provenance explicit.")
            conditions = data.get("conditions")
            if conditions in (None, "", [], {}):
                add("mechanism_missing_conditions", "warning", [item_id], "Mechanism has no recorded conditions or boundary assumptions.")
            if data.get("status") in _ESTABLISHED_STATUSES and not data.get("evidence_ids"):
                add("mechanism_established_without_evidence", "warning", [item_id], "Established mechanism has no evidence_ids. Legacy data remains readable, but this status has no recorded provenance.", subject_id=item_id, subject_kind="record")
            if data.get("status") in {"reported", "supported"}:
                limited_sources: list[str] = []
                for evidence_id in data.get("evidence_ids", []) if isinstance(data.get("evidence_ids", []), list) else []:
                    evidence = record_by_id.get(evidence_id, {})
                    source_id = evidence.get("data", {}).get("paper_id") if isinstance(evidence.get("data", {}), dict) else None
                    source = record_by_id.get(source_id, {}) if isinstance(source_id, str) else {}
                    scope = source.get("data", {}).get("read_scope") if isinstance(source.get("data", {}), dict) else None
                    if scope in {"metadata", "abstract"}:
                        limited_sources.append(evidence_id)
                if limited_sources:
                    add("mechanism_limited_source_coverage", "warning", [item_id, *limited_sources], "Mechanism is recorded with an established status but its linked source coverage is metadata/abstract only; this does not verify or change the status.")

    for (ident_kind, normalized), ids in sorted(identifier_groups.items()):
        if len(set(ids)) > 1 and (focus is None or set(ids) & selected_record_ids):
            add("duplicate_identifier_candidate", "warning", ids, f"Papers share normalized {ident_kind} {normalized!r}; this is an alias/duplicate candidate and was not merged.")
    for alias, ids in sorted(alias_groups.items()):
        if len(set(ids)) > 1 and (focus is None or set(ids) & selected_record_ids):
            add("alias_candidate", "warning", ids, f"Papers share alias {alias!r}; this is a candidate only and was not merged.")

    # Current dependency baselines, plus propagation from a stale predecessor.
    direct_stale: set[str] = set()
    reverse: dict[str, set[str]] = defaultdict(set)
    for key, kind, item in entity_items:
        if kind == "record":
            for ref, expected in _typed_record_refs(item):
                actual = record_by_id.get(ref, {}).get("type") if isinstance(ref, str) else None
                if actual is None and isinstance(ref, str) and known_types is not None:
                    actual = known_types.get(ref)
                if actual != expected:
                    add("missing_dependency", "error", [item["id"], str(ref)], f"Typed reference must resolve to {expected}; legacy graph data has {actual or 'no matching record'}.", subject_id=item["id"], subject_kind="record")
        deps = record_dependency_refs(item) if kind == "record" else edge_dependency_refs(item)
        basis = item.get("data", {}).get("basis") if kind == "record" else item.get("basis")
        basis = basis if isinstance(basis, dict) else {}
        for dep in deps:
            dep_key = _entity_key("record", dep)
            if dep_key in selected:
                reverse[dep_key].add(key)
            dep_item = record_by_id.get(dep)
            if dep not in basis:
                add("dependency_untracked", "warning", [item["id"], dep], "Dependency has no captured basis; it is untracked rather than fresh.", subject_id=item["id"], subject_kind=kind)
            elif dep_item is not None and basis[dep] != dependency_content_hash(dep_item):
                direct_stale.add(key)
                add("dependency_basis_drift", "warning", [item["id"], dep], "Captured dependency basis differs from the current source payload; this entity needs review.", subject_id=item["id"], subject_kind=kind)
            elif dep_item is None and known_types is not None and dep in known_types:
                add("dependency_not_assessed", "info", [item["id"], dep], "Dependency exists but is outside the bounded quality payload; its captured basis was not compared.", subject_id=item["id"], subject_kind=kind)

    propagated: set[str] = set()
    queue = deque(sorted(direct_stale))
    seen = set(direct_stale)
    while queue:
        stale_key = queue.popleft()
        for dependent in sorted(reverse.get(stale_key, ())):
            if dependent not in seen:
                seen.add(dependent)
                propagated.add(dependent)
                queue.append(dependent)
    for key in sorted(propagated):
        _kind, item = next((kind, item) for candidate, kind, item in entity_items if candidate == key)
        add("dependency_needs_review", "warning", [item["id"]], "A dependency path has changed; review is needed before treating this record or edge as current.", subject_id=item["id"], subject_kind=_kind)

    # A planned experiment can produce useful *proposed* evidence, but it is
    # not an established observation.  Flag only when it is used for an
    # established claim/hypothesis label; no scientific conclusion is drawn.
    for _key, kind, item in entity_items:
        if kind != "record" or item.get("type") not in {"claim", "hypothesis"}:
            continue
        data = item.get("data", {})
        status = data.get("status") if isinstance(data, dict) else None
        established = status in {"reported", "supported", "contradicted", "inconclusive"}
        if not established:
            continue
        proposed_evidence: list[str] = []
        for evidence_id in data.get("evidence_ids", []) if isinstance(data.get("evidence_ids", []), list) else []:
            evidence = record_by_id.get(evidence_id)
            if not evidence or evidence.get("type") != "evidence":
                continue
            edata = evidence.get("data", {})
            source_id = edata.get("experiment_id") if isinstance(edata, dict) else None
            experiment = record_by_id.get(source_id) if isinstance(source_id, str) else None
            proposed = (
                isinstance(edata, dict) and edata.get("status") == "proposed"
            ) or (
                isinstance(edata, dict) and edata.get("verification") == "proposed"
            ) or (
                isinstance(experiment, dict) and experiment.get("data", {}).get("status") in {"planned", "running"}
            )
            if proposed:
                proposed_evidence.append(evidence_id)
        if proposed_evidence:
            add("proposed_evidence_as_established", "warning", [item["id"], *proposed_evidence], "Proposed-only evidence is attached to an established status; this is a maintenance warning, not a scientific contradiction.")
        unusable_evidence: list[str] = []
        for evidence_id in data.get("evidence_ids", []) if isinstance(data.get("evidence_ids", []), list) else []:
            evidence = record_by_id.get(evidence_id)
            edata = evidence.get("data", {}) if isinstance(evidence, dict) else {}
            source_id = edata.get("experiment_id") if isinstance(edata, dict) else None
            experiment = record_by_id.get(source_id) if isinstance(source_id, str) else None
            experiment_data = experiment.get("data", {}) if isinstance(experiment, dict) else {}
            if isinstance(experiment_data, dict) and (
                experiment_data.get("status") in {"invalid", "interrupted"}
                or (experiment_data.get("status") == "completed" and experiment_data.get("outcome") == "invalid")
            ):
                unusable_evidence.append(evidence_id)
        if unusable_evidence:
            add("invalid_experiment_evidence_as_established", "warning", [item["id"], *unusable_evidence], "Evidence from an invalid or interrupted experiment is attached to an established status; this is an advisory provenance issue, not a scientific conclusion.")

    # Evidence IDs are provenance links, not automatic scientific support.
    # Claims, hypotheses, and mechanisms share one assertion/evidence contract:
    # a status-directional usage role, an allowed empirical/reported evidence
    # kind for the saved status, and exact explicitly authored applicability.
    # It is deliberately a diagnostic: it never changes an epistemic status
    # or asserts a scientific result is false. A legacy
    # evidence.assertion_role is descriptive only and cannot prohibit the
    # same evidence being used differently elsewhere.
    for _key, kind, assertion in entity_items:
        if kind != "record" or assertion.get("type") not in {"claim", "hypothesis", "mechanism"}:
            continue
        data = assertion.get("data", {})
        if not isinstance(data, dict):
            continue
        required_kinds = assertion_required_evidence_kinds(assertion)
        if not required_kinds:
            continue
        required_roles = assertion_required_evidence_roles(assertion)
        support_ids = {item for item in data.get("evidence_ids", []) if isinstance(item, str)}
        domain = _target_domain(assertion)
        analogy_edges: list[str] = []
        for _edge_key, edge_kind, edge in entity_items:
            if edge_kind != "edge" or edge.get("status") != "hypothesized" or assertion["id"] not in {edge.get("source"), edge.get("target")}:
                continue
            if edge.get("relation") == "analogous_to":
                analogy_edges.append(edge["id"])
        qualified = {
            evidence_id for evidence_id in support_ids
            if isinstance(record_by_id.get(evidence_id), dict)
            and record_by_id[evidence_id].get("type") == "evidence"
            and is_qualified_assertion_evidence(assertion, record_by_id[evidence_id])
        }
        if domain is None or not qualified:
            kind_text = ", ".join(sorted(required_kinds))
            role_text = ", ".join(sorted(required_roles))
            detail = (
                "no applicability domain is recorded"
                if domain is None else
                "no evidence has an assertion-direction evidence_roles[id] "
                f"({role_text}), an allowed evidence_kind ({kind_text}), and exact explicitly authored applicability"
            )
            typ = str(assertion.get("type"))
            code = {
                "hypothesis": "transfer_support_gap",
                "claim": "claim_target_support_gap",
                "mechanism": "mechanism_evidence_qualification_gap",
            }[typ]
            noun = "hypothesis" if typ == "hypothesis" else typ
            add(
                code,
                "warning",
                [assertion["id"], *sorted(support_ids), *sorted(analogy_edges)],
                f"Established {noun} has {detail}; source-domain evidence and hypothesized analogy/limits remain provenance only unless they meet this assertion's explicit role and scope. This advisory does not alter status or require a local experiment.",
                subject_id=assertion["id"], subject_kind="record",
            )

    # Explicit support and contradiction to the same target are intentionally
    # retained.  Differing conditions make the pair a potential disagreement,
    # never an assertion that one scientific result is false.
    assertions: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"supports": [], "contradicts": []})
    for _key, kind, edge in entity_items:
        if kind == "edge" and edge.get("relation") in {"supports", "contradicts"}:
            assertions[str(edge.get("target"))][str(edge.get("relation"))].append(edge)
        if kind == "edge":
            source_type = record_by_id.get(edge.get("source"), {}).get("type") or (known_types or {}).get(edge.get("source"))
            target_type = record_by_id.get(edge.get("target"), {}).get("type") or (known_types or {}).get(edge.get("target"))
            if not relation_is_compatible(str(edge.get("relation")), source_type, target_type):
                add("invalid_relation_endpoints", "error", [edge["id"], str(edge.get("source")), str(edge.get("target"))], "Relation endpoint types are mechanically incompatible.")
            if edge.get("status") in {"reported", "observed"} and edge.get("relation") in {"supports", "contradicts", "limits"}:
                target = record_by_id.get(edge.get("target"), {})
                target_scope = target if _target_domain(target) is not None else edge
                for evidence_id in edge.get("evidence_ids", []) if isinstance(edge.get("evidence_ids", []), list) else []:
                    evidence = record_by_id.get(evidence_id)
                    comparison = compare_recorded_applicability(evidence, target_scope) if isinstance(evidence, dict) else "unknown"
                    if comparison != "match":
                        code = "edge_evidence_applicability_different" if comparison == "different" else "edge_evidence_applicability_unknown"
                        detail = "differs in recorded domain/task/condition fields" if comparison == "different" else "has incomplete or unknown recorded domain/task/condition fields"
                        add(code, "warning", [edge["id"], str(edge.get("target")), evidence_id], f"Observed/reported assertion edge evidence {detail} for the relation target. This is recorded-field bookkeeping, not scientific incompatibility or proof of semantic equivalence; scientific applicability remains unverified.", subject_id=edge["id"], subject_kind="edge")
    for target, grouped in sorted(assertions.items()):
        if grouped["supports"] and grouped["contradicts"]:
            supporting = grouped["supports"]
            opposing = grouped["contradicts"]
            condition_note = " Conditions differ and may describe a boundary condition." if any(a.get("conditions") != b.get("conditions") for a in supporting for b in opposing) else " Conditions should be reviewed."
            add("potential_disagreement", "warning", [target, *[edge["id"] for edge in supporting], *[edge["id"] for edge in opposing]], "Explicit supporting and opposing assertions target the same entity; retain both as a potential disagreement, not a scientific contradiction." + condition_note)

    findings.sort(key=lambda item: (_SEVERITY_ORDER[item["severity"]], item["code"], item["entity_ids"], item["message"]))
    counts = {severity: sum(1 for finding in findings if finding["severity"] == severity) for severity in ("error", "warning", "info")}
    return {
        "scope": "mechanical/advisory; not scientific verification",
        "findings": findings[:limit],
        "counts": counts,
        "truncated": scope_truncated or len(findings) > limit,
    }
