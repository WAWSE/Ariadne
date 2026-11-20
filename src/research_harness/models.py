"""Validation and normalization for the research harness graph.

The model in this module is deliberately small: records are source-bound JSON
objects, while mechanical validation only checks shape and references.  It does
not attempt to decide whether a paper, excerpt, or scientific conclusion is
true.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_VERSION = 1
RECORD_TYPES = {
    "paper", "source", "problem", "method", "technique", "mechanism", "claim",
    "evidence", "hypothesis", "experiment", "result", "limitation",
    "decision", "search",
}
EDGE_STATUSES = {"reported", "observed", "hypothesized", "structural"}
EDGE_RELATIONS = {
    "cites", "addresses", "uses", "motivates", "proposes_mechanism", "tests",
    "produces", "supports", "contradicts", "limits", "analogous_to",
    "derived_from", "has_evidence", "revises",
}
STRUCTURAL_RELATIONS = {"cites", "has_evidence", "tests", "produces", "derived_from", "revises"}

_READ_SCOPES = {"metadata", "abstract", "sections", "fulltext"}
_CLAIM_STATUSES = {"proposed", "reported", "supported", "contradicted", "inconclusive"}
_HYPOTHESIS_STATUSES = {"proposed", "active", "supported", "contradicted", "inconclusive", "parked"}
_EXPERIMENT_STATUSES = {"planned", "running", "completed", "invalid", "interrupted"}
ASSERTION_ROLES = frozenset({
    "source_observation", "target_observation", "source_assertion",
    "target_assertion", "transfer_proposal", "analogy", "limitation",
    "interpretation",
})
# ``observation`` and ``interpretation`` are legacy aliases retained so an
# existing local research store remains readable.  New Skills write the
# explicit taxonomy: a checked source passage, an author's reported result,
# an author's mechanism interpretation, a locally measured observation, or a
# plan.  The distinction prevents paper reading from being represented as an
# independent observation of the underlying phenomenon.
EVIDENCE_KINDS = frozenset({
    "source_excerpt", "author_report", "author_interpretation",
    "local_observation", "planned",
    "observation", "interpretation", "unknown",
})
# A role is the direction in which one assertion uses one evidence item.  It
# is deliberately separate from ``evidence_kind``: an author report and a
# local observation may each support, limit, or leave a particular assertion
# inconclusive.  The explicit neutral role avoids forcing a genuinely
# indeterminate measurement to masquerade as either positive support or a
# counterexample.
EVIDENCE_ROLES = frozenset({"support", "counterevidence", "inconclusive", "context", "analogy"})

# ``remember --compact-template`` uses these impossible-to-mistake markers
# instead of fabricating source content.  Reject them at the model boundary so
# a template can be inspected/expanded but cannot accidentally become durable
# research memory through either compact or raw writeback.
_TEMPLATE_PLACEHOLDER_PREFIX = "__SRH_TEMPLATE_"


def canonical_json(value: Any) -> str:
    # JSON's default would emit NaN/Infinity, values which SQLite and other
    # JSON consumers do not treat as valid portable JSON.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def dependency_content_hash(value: Any) -> str:
    """Hash a record or edge as a dependency baseline.

    A baseline must not include its *own* baseline map.  Otherwise reviewing
    two records which refer to each other would require recursively calculating
    hashes.  The function intentionally makes a JSON copy: callers can safely
    use it on objects that are about to be stored.
    """

    try:
        payload = json.loads(canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"dependency payload contains non-JSON data: {exc}") from exc
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            data.pop("basis", None)
        # Edge baselines live at the top level.  Removing this key for every
        # entity is harmless and keeps the helper useful for either shape.
        payload.pop("basis", None)
    return content_hash(payload)


def record_dependency_refs(record: dict[str, Any]) -> list[str]:
    """Return explicit, typed provenance references carried by a record.

    These are stored fields, not inferred causal relationships.  Keeping the
    small helper here lets graph traversal, quality checks, and delta review
    use the same conservative dependency definition.
    """

    data = record.get("data", {}) if isinstance(record, dict) else {}
    if not isinstance(data, dict):
        return []
    refs: list[Any] = []
    typ = record.get("type")
    if typ == "evidence":
        refs.append(data.get("source_id") or data.get("paper_id") or data.get("experiment_id"))
    elif typ == "result":
        refs.append(data.get("experiment_id"))
    refs.extend(data.get("evidence_ids", []) if isinstance(data.get("evidence_ids", []), list) else [])
    return list(dict.fromkeys(ref for ref in refs if isinstance(ref, str) and ref))


def record_navigation_refs(record: dict[str, Any]) -> list[str]:
    """Return all typed record references suitable for graph navigation.

    An experiment's ``hypothesis_id`` says what it tests; it is deliberately
    excluded from dependency freshness because changing a hypothesis label must
    not falsely make a completed experiment's observation stale.
    """

    refs = record_dependency_refs(record)
    data = record.get("data", {}) if isinstance(record, dict) else {}
    if record.get("type") == "experiment" and isinstance(data, dict):
        hypothesis_id = data.get("hypothesis_id")
        if isinstance(hypothesis_id, str) and hypothesis_id:
            refs.append(hypothesis_id)
    return list(dict.fromkeys(refs))


def edge_dependency_refs(edge: dict[str, Any]) -> list[str]:
    """Return an edge's endpoint and evidence dependencies."""

    refs: list[Any] = [edge.get("source"), edge.get("target")]
    evidence = edge.get("evidence_ids", []) if isinstance(edge, dict) else []
    refs.extend(evidence if isinstance(evidence, list) else [])
    return list(dict.fromkeys(ref for ref in refs if isinstance(ref, str) and ref))


def _err(path: str, message: str) -> ValueError:
    return ValueError(f"{path}: {message}")


def _string(value: Any, path: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str):
        raise _err(path, "must be a string")
    if nonempty and not value.strip():
        raise _err(path, "must not be empty")
    return value


def _list_of_strings(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise _err(path, "must be a list of strings")
    return value


def _dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _err(path, "must be an object")
    return value


def _reject_template_placeholders(value: Any, path: str) -> None:
    """Reject the reserved authoring markers anywhere in a persisted record."""

    if isinstance(value, str):
        if _TEMPLATE_PLACEHOLDER_PREFIX in value:
            raise _err(path, "contains an unreplaced SRH template placeholder")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_template_placeholders(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_template_placeholders(key, f"{path} key")
            _reject_template_placeholders(item, f"{path}.{key}")


def _basis(value: Any, path: str) -> dict[str, str]:
    basis = _dict(value, path)
    out: dict[str, str] = {}
    for key, digest in basis.items():
        _string(key, f"{path} key", nonempty=True)
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise _err(f"{path}[{key!r}]", "must be a lowercase SHA-256 hex digest")
        out[key] = digest
    return out


def _basis_review(value: Any, path: str) -> dict[str, str]:
    review = _dict(value, path)
    if set(review) != {"note", "reviewed_at"}:
        raise _err(path, "must contain only note and reviewed_at")
    return {
        "note": _string(review.get("note"), f"{path}.note", nonempty=True),
        "reviewed_at": _string(review.get("reviewed_at"), f"{path}.reviewed_at", nonempty=True),
    }


def _optional_string(value: Any, path: str) -> None:
    if value is not None and not isinstance(value, str):
        raise _err(path, "must be a string or null")


def _validate_applicability(container: dict[str, Any], path: str) -> None:
    """Validate declarative scope labels without treating labels as facts.

    ``source_domain``, ``target_domain``, and ``task_context`` are authored
    provenance labels.  They are deliberately not normalized, resolved, or
    compared here: a person/Codex must explicitly state applicability and the
    quality layer can later report recorded differences, not scientific
    incompatibility. Unknown object keys are kept
    for forward-compatible schema-v1 imports.
    """

    if "applicability" in container:
        applicability = _dict(container["applicability"], f"{path}.applicability")
        for key in ("domain", "source_domain", "target_domain", "task_context"):
            if key in applicability:
                _string(applicability[key], f"{path}.applicability.{key}", nonempty=True)
        if "conditions" in applicability:
            _dict(applicability["conditions"], f"{path}.applicability.conditions")
    if "assertion_role" in container:
        role = _string(container["assertion_role"], f"{path}.assertion_role", nonempty=True)
        if role not in ASSERTION_ROLES:
            raise _err(f"{path}.assertion_role", "must be a recognized explicit assertion role")
    if "evidence_roles" in container:
        roles = _dict(container["evidence_roles"], f"{path}.evidence_roles")
        for evidence_id, role in roles.items():
            _string(evidence_id, f"{path}.evidence_roles key", nonempty=True)
            if not isinstance(role, str) or role not in EVIDENCE_ROLES:
                raise _err(f"{path}.evidence_roles[{evidence_id!r}]", "must be support, counterevidence, inconclusive, context, or analogy")


def normalize_record(record: Any, path: str = "record") -> dict[str, Any]:
    r = _dict(record, path)
    for key in ("id", "type", "title", "summary", "tags", "data"):
        if key not in r:
            raise _err(f"{path}.{key}", "is required")
    rid = _string(r["id"], f"{path}.id", nonempty=True)
    typ = _string(r["type"], f"{path}.type", nonempty=True)
    if typ not in RECORD_TYPES:
        raise _err(f"{path}.type", f"unsupported record type {typ!r}")
    title = _string(r["title"], f"{path}.title")
    summary = _string(r["summary"], f"{path}.summary")
    tags = _list_of_strings(r["tags"], f"{path}.tags")
    # Round-trip the object now, both to detach it from caller-owned data and
    # to retain optional, forward-compatible fields such as a provenance
    # baseline.  ``canonical_json`` below is the portability check.
    data = _dict(r["data"], f"{path}.data")
    if "evidence_ids" in data:
        _list_of_strings(data["evidence_ids"], f"{path}.data.evidence_ids")
    # Make a fresh object so callers cannot mutate an object while it is being
    # committed.  JSON serialization also catches non-serializable values.
    out = {"id": rid, "type": typ, "title": title, "summary": summary,
           "tags": list(tags), "data": data}
    try:
        json.loads(canonical_json(out))
    except (TypeError, ValueError) as exc:
        raise _err(path, f"contains non-JSON data ({exc})") from exc
    _reject_template_placeholders(out, path)
    out["data"] = json.loads(canonical_json(data))
    if "basis" in out["data"]:
        out["data"]["basis"] = _basis(out["data"]["basis"], f"{path}.data.basis")
    if "basis_review" in out["data"]:
        out["data"]["basis_review"] = _basis_review(out["data"]["basis_review"], f"{path}.data.basis_review")
    _validate_record_data(out)
    return out


def _validate_record_data(record: dict[str, Any]) -> None:
    typ, d, path = record["type"], record["data"], f"record[{record['id']!r}].data"
    _validate_applicability(d, path)
    if typ == "paper":
        if "url" in d and not isinstance(d["url"], str):
            raise _err(f"{path}.url", "must be a string")
        if "identifiers" in d and not isinstance(d["identifiers"], dict):
            raise _err(f"{path}.identifiers", "must be an object")
        if "read_scope" in d and d["read_scope"] not in _READ_SCOPES:
            raise _err(f"{path}.read_scope", "must be metadata, abstract, sections, or fulltext")
        if "read_sections" in d:
            _list_of_strings(d["read_sections"], f"{path}.read_sections")
    elif typ == "source":
        # A source is intentionally a small, generic provenance anchor for
        # material which is not a paper or an experiment (for example a task
        # brief, dataset card, lab notebook, protocol, or supplied document).
        # ``kind`` is an authored label, not a closed taxonomy: callers must
        # say what it is without the schema pretending to classify it.
        _string(d.get("kind"), f"{path}.kind", nonempty=True)
    elif typ == "evidence":
        has_source = isinstance(d.get("source_id"), str) and bool(d.get("source_id").strip())
        has_paper = isinstance(d.get("paper_id"), str) and bool(d.get("paper_id").strip())
        has_exp = isinstance(d.get("experiment_id"), str) and bool(d.get("experiment_id").strip())
        if sum((has_source, has_paper, has_exp)) != 1:
            raise _err(path, "evidence requires exactly one source_id, paper_id, or experiment_id")
        # Older exports may lack a locator/excerpt.  This is important enough
        # to report in ``quality()``, but it is not a broken reference or a
        # reason to lose otherwise durable legacy provenance during import.
        if "locator" in d:
            _string(d["locator"], f"{path}.locator")
        if "excerpt" in d:
            _string(d["excerpt"], f"{path}.excerpt")
        for k in ("source_uri", "source_hash", "verification"):
            if k in d and not isinstance(d[k], (str, dict)):
                raise _err(f"{path}.{k}", "must be a string or object")
        if "evidence_kind" in d and d["evidence_kind"] not in EVIDENCE_KINDS:
            raise _err(
                f"{path}.evidence_kind",
                "must be source_excerpt, author_report, author_interpretation, local_observation, planned, unknown, or a legacy alias",
            )
    elif typ == "claim":
        if d.get("origin") not in {"author", "assistant", "user"}:
            raise _err(f"{path}.origin", "must be author, assistant, or user")
        if d.get("status") not in _CLAIM_STATUSES:
            raise _err(f"{path}.status", "has an invalid epistemic status")
        if "evidence_ids" in d:
            _list_of_strings(d["evidence_ids"], f"{path}.evidence_ids")
        if d["status"] != "proposed" and not d.get("evidence_ids"):
            raise _err(path, "non-proposed claims require evidence_ids")
    elif typ == "hypothesis":
        for k in ("prediction", "alternatives", "test"):
            if k not in d:
                raise _err(f"{path}.{k}", "is required")
        if d.get("status") not in _HYPOTHESIS_STATUSES:
            raise _err(f"{path}.status", "has an invalid hypothesis status")
        _string(d["prediction"], f"{path}.prediction", nonempty=True)
        _string(d["test"], f"{path}.test", nonempty=True)
        _list_of_strings(d["alternatives"], f"{path}.alternatives")
        if "evidence_ids" in d:
            _list_of_strings(d["evidence_ids"], f"{path}.evidence_ids")
        if d["status"] in {"supported", "contradicted", "inconclusive"} and not d.get("evidence_ids"):
            raise _err(path, "empirical status requires evidence_ids")
    elif typ == "experiment":
        # ``outcome`` is intentionally optional while an experiment is planned
        # or running.  A completed experiment must state its outcome below.
        for k in ("hypothesis_id", "status", "command", "budget", "config", "seed", "code_ref", "data_ref", "metrics", "artifacts"):
            if k not in d:
                raise _err(f"{path}.{k}", "is required")
        if d.get("status") not in _EXPERIMENT_STATUSES:
            raise _err(f"{path}.status", "has an invalid experiment status")
        _string(d["hypothesis_id"], f"{path}.hypothesis_id", nonempty=True)
        for key in ("command", "budget", "code_ref", "data_ref"):
            _optional_string(d[key], f"{path}.{key}")
        _dict(d["config"], f"{path}.config")
        if d["seed"] is not None and (not isinstance(d["seed"], (int, str)) or isinstance(d["seed"], bool)):
            raise _err(f"{path}.seed", "must be an integer, string, or null")
        if not isinstance(d["metrics"], (list, dict)):
            raise _err(f"{path}.metrics", "must be a list or object")
        if not isinstance(d["artifacts"], list):
            raise _err(f"{path}.artifacts", "must be a list")
        if "outcome" in d and d["outcome"] not in {None, "support", "contradict", "inconclusive", "invalid"}:
            raise _err(f"{path}.outcome", "has an invalid experiment outcome")
        if d["status"] == "completed" and d.get("outcome") not in {"support", "contradict", "inconclusive", "invalid"}:
            raise _err(f"{path}.outcome", "completed experiments require an outcome")
        if d["status"] == "completed" and d.get("outcome") in {"support", "contradict", "inconclusive"}:
            for k in ("command", "config", "seed", "code_ref", "data_ref"):
                if d.get(k) in (None, "", {}):
                    raise _err(path, f"completed valid experiment requires {k}")
    elif typ == "result":
        for k in ("experiment_id", "metrics", "interpretation", "limitations"):
            if k not in d:
                raise _err(f"{path}.{k}", "is required")
        _string(d["experiment_id"], f"{path}.experiment_id", nonempty=True)
        if not isinstance(d["metrics"], (list, dict)):
            raise _err(f"{path}.metrics", "must be a list or object")
        _string(d["interpretation"], f"{path}.interpretation")
    elif typ == "search":
        for k in ("query", "provider", "searched_at", "status"):
            if k not in d:
                raise _err(f"{path}.{k}", "is required")
        for key in ("query", "provider", "searched_at", "status"):
            _string(d[key], f"{path}.{key}", nonempty=True)
    elif typ == "decision":
        for k in ("reason", "evidence_ids", "next_action"):
            if k not in d:
                raise _err(f"{path}.{k}", "is required")
        _list_of_strings(d["evidence_ids"], f"{path}.evidence_ids")
        _string(d["reason"], f"{path}.reason", nonempty=True)
        _string(d["next_action"], f"{path}.next_action", nonempty=True)
    elif typ == "mechanism":
        # Mechanisms remain deliberately less prescriptive than claims: old
        # exports can be read/imported, while quality() flags an established
        # mechanism with no evidence rather than silently treating it as one.
        if "status" in d and d["status"] not in _CLAIM_STATUSES:
            raise _err(f"{path}.status", "has an invalid epistemic status")
        if "evidence_ids" in d:
            _list_of_strings(d["evidence_ids"], f"{path}.evidence_ids")


def normalize_edge(edge: Any, path: str = "edge") -> dict[str, Any]:
    e = _dict(edge, path)
    _reject_template_placeholders(e, path)
    for key in ("id", "source", "target", "relation", "status", "evidence_ids", "conditions"):
        if key not in e:
            raise _err(f"{path}.{key}", "is required")
    out = {
        "id": _string(e["id"], f"{path}.id", nonempty=True),
        "source": _string(e["source"], f"{path}.source", nonempty=True),
        "target": _string(e["target"], f"{path}.target", nonempty=True),
        "relation": _string(e["relation"], f"{path}.relation", nonempty=True),
        "status": _string(e["status"], f"{path}.status", nonempty=True),
        "evidence_ids": _list_of_strings(e["evidence_ids"], f"{path}.evidence_ids"),
        "conditions": _dict(e["conditions"], f"{path}.conditions"),
    }
    if "basis" in e:
        out["basis"] = _basis(e["basis"], f"{path}.basis")
    if "basis_review" in e:
        out["basis_review"] = _basis_review(e["basis_review"], f"{path}.basis_review")
    if "applicability" in e:
        out["applicability"] = _dict(e["applicability"], f"{path}.applicability")
    if "assertion_role" in e:
        out["assertion_role"] = _string(e["assertion_role"], f"{path}.assertion_role", nonempty=True)
    if "evidence_roles" in e:
        out["evidence_roles"] = _dict(e["evidence_roles"], f"{path}.evidence_roles")
    if out["relation"] not in EDGE_RELATIONS:
        raise _err(f"{path}.relation", f"unsupported relation {out['relation']!r}")
    if out["relation"] == "revises" and out["source"] == out["target"]:
        raise _err(f"{path}.target", "a decision cannot revise itself; use a distinct decision record")
    if out["status"] not in EDGE_STATUSES:
        raise _err(f"{path}.status", "has an invalid edge status; must be " + ", ".join(sorted(EDGE_STATUSES)))
    if out["status"] == "structural" and out["relation"] not in STRUCTURAL_RELATIONS:
        raise _err(f"{path}.status", f"structural is not valid for {out['relation']!r}; author reported, observed, or hypothesized as appropriate")
    _validate_applicability(out, path)
    try:
        json.loads(canonical_json(out))
    except (TypeError, ValueError) as exc:
        raise _err(path, f"contains non-JSON data ({exc})") from exc
    return out


def normalize_bundle(bundle: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    b = _dict(bundle, "bundle")
    if b.get("schema_version") != SCHEMA_VERSION:
        raise _err("bundle.schema_version", f"must be {SCHEMA_VERSION}")
    records_raw, edges_raw = b.get("records"), b.get("edges")
    if not isinstance(records_raw, list) or not isinstance(edges_raw, list):
        raise _err("bundle", "records and edges must be arrays")
    records, edges = [], []
    seen_r: dict[str, str] = {}
    for i, raw in enumerate(records_raw):
        item = normalize_record(raw, f"bundle.records[{i}]")
        h = content_hash(item)
        if item["id"] in seen_r and seen_r[item["id"]] != h:
            raise _err(f"bundle.records[{i}].id", "conflicting duplicate id")
        if item["id"] not in seen_r:
            seen_r[item["id"]] = h
            records.append(item)
    seen_e: dict[str, str] = {}
    for i, raw in enumerate(edges_raw):
        item = normalize_edge(raw, f"bundle.edges[{i}]")
        h = content_hash(item)
        if item["id"] in seen_e and seen_e[item["id"]] != h:
            raise _err(f"bundle.edges[{i}].id", "conflicting duplicate id")
        if item["id"] not in seen_e:
            seen_e[item["id"]] = h
            edges.append(item)
    return records, edges


def normalize_bundle_state(bundle: Any) -> dict[str, Any] | None:
    """Validate an optional state snapshot carried by an exported bundle.

    State is intentionally an extensible JSON object, rather than a second
    scientific schema.  It is nevertheless validated before ingest opens its
    transaction so an export can be restored atomically with its graph.
    """
    if "state" not in bundle:
        return None
    state = _dict(bundle["state"], "bundle.state")
    try:
        return json.loads(canonical_json(state))
    except (TypeError, ValueError) as exc:
        raise _err("bundle.state", f"contains non-JSON data ({exc})") from exc
