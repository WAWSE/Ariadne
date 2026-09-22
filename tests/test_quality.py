from __future__ import annotations

from pathlib import Path

import pytest

from research_harness.models import normalize_bundle
from research_harness.store import ResearchStore


def paper(record_id: str, doi: str, scope: str = "abstract") -> dict:
    return {"id": record_id, "type": "paper", "title": "same title", "summary": "source", "tags": [],
            "data": {"read_scope": scope, "identifiers": {"doi": doi}}}


def evidence(record_id: str, paper_id: str) -> dict:
    return {"id": record_id, "type": "evidence", "title": record_id, "summary": "s", "tags": [],
            "data": {"paper_id": paper_id, "locator": "abstract", "excerpt": "x"}}


def claim(record_id: str, evidence_id: str) -> dict:
    return {"id": record_id, "type": "claim", "title": record_id, "summary": "c", "tags": [],
            "data": {"origin": "author", "status": "reported", "evidence_ids": [evidence_id], "conditions": {}, "alternative_explanations": []}}


def bundle(records: list[dict], edges: list[dict]) -> dict:
    return {"schema_version": 1, "records": records, "edges": edges}


def codes(result: dict) -> set[str]:
    return {finding["code"] for finding in result["findings"]}


def test_quality_duplicate_doi_is_global_candidate_but_focus_is_exact_and_bounded(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        # No graph edge joins these papers.  DOI normalization must still warn
        # about the candidate when checking the newly read paper, never merge.
        store.ingest(bundle([paper("paper:A", "DOI:10.1000/ABC"), paper("paper:B", "https://doi.org/10.1000/abc")], []))
        focused = store.quality("paper:A")
        duplicate = next(finding for finding in focused["findings"] if finding["code"] == "duplicate_identifier_candidate")
        assert duplicate["entity_ids"] == ["paper:A", "paper:B"]
        assert {record["id"] for record in store.list_records("paper")} == {"paper:A", "paper:B"}
        unknown = store.quality("paper:a")
        assert "unknown_focus" in codes(unknown)
        assert unknown["counts"]["error"] == 1
        assert store.quality(limit=1)["truncated"] is True


def test_quality_retains_condition_sensitive_opposition_without_declaring_contradiction(tmp_path: Path) -> None:
    records = [paper("paper:p", "10/p", "fulltext"), evidence("evidence:plus", "paper:p"), evidence("evidence:minus", "paper:p"), claim("claim:c", "evidence:plus")]
    edges = [
        {"id": "edge:plus", "source": "evidence:plus", "target": "claim:c", "relation": "supports", "status": "reported", "evidence_ids": ["evidence:plus"], "conditions": {"temperature": "low"}},
        {"id": "edge:minus", "source": "evidence:minus", "target": "claim:c", "relation": "contradicts", "status": "reported", "evidence_ids": ["evidence:minus"], "conditions": {"temperature": "high"}},
    ]
    with ResearchStore(tmp_path, create=True) as store:
        store.ingest(bundle(records, edges))
        result = store.quality("claim:c")
        finding = next(finding for finding in result["findings"] if finding["code"] == "potential_disagreement")
        assert {"edge:plus", "edge:minus", "claim:c"} <= set(finding["entity_ids"])
        assert "boundary condition" in finding["message"]
        assert "scientific contradiction" in finding["message"]


def test_graph_exposes_typed_provenance_without_inferred_edge(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        store.ingest(bundle([paper("paper:p", "10/p"), evidence("evidence:e", "paper:p"), claim("claim:c", "evidence:e")], []))
        graph = store.graph("claim:c", hops=2)
        assert {"claim:c", "evidence:e", "paper:p"} <= {record["id"] for record in graph["records"]}
        assert {("claim:c", "evidence:e", "evidence_ids"), ("evidence:e", "paper:p", "paper_id")} <= {
            (item["source"], item["target"], item["field"]) for item in graph["provenance"]
        }
        assert graph["edges"] == []


def test_high_degree_focus_is_truncated_without_false_missing_dependency(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        source = paper("paper:p", "10/p", "fulltext")
        observed = evidence("evidence:e", "paper:p")
        claims = [claim(f"claim:{number:03d}", "evidence:e") for number in range(520)]
        store.apply_delta({"schema_version": 1, "records": [source, observed, *claims], "edges": []})
        report = store.quality("paper:p", limit=2000)
        assert report["truncated"] is True
        assert "missing_dependency" not in codes(report)
        assert "invalid_relation_endpoints" not in codes(report)


def test_supported_transfer_backed_only_by_hypothesized_analogy_is_advisory(tmp_path: Path) -> None:
    source = paper("paper:source", "10/source", "fulltext")
    observed = evidence("evidence:source", "paper:source")
    observed["data"].update({"evidence_kind": "observation", "assertion_role": "source_observation", "applicability": {"domain": "source-domain", "conditions": {}}})
    counterexample = evidence("evidence:source-counter", "paper:source")
    counterexample["data"].update({"evidence_kind": "observation", "assertion_role": "source_observation", "applicability": {"domain": "source-domain", "conditions": {"context": "volatile"}}})
    mechanism = {"id": "mechanism:m", "type": "mechanism", "title": "m", "summary": "m", "tags": [],
                 "data": {"origin": "author", "conditions": ["source setting"], "evidence_ids": ["evidence:source"]}}
    transfer = {"id": "hypothesis:transfer", "type": "hypothesis", "title": "transfer", "summary": "t", "tags": [],
                "data": {"prediction": "p", "alternatives": [], "test": "t", "status": "supported", "evidence_ids": ["evidence:source", "evidence:source-counter"], "evidence_roles": {"evidence:source": "support", "evidence:source-counter": "counterevidence"}, "applicability": {"domain": "target-domain", "conditions": {}}}}
    analogy = {"id": "edge:analogy", "source": "hypothesis:transfer", "target": "mechanism:m", "relation": "analogous_to",
               "status": "hypothesized", "evidence_ids": ["evidence:source"], "conditions": {"target": "new domain"}}
    source_limit = {"id": "edge:source-limit", "source": "evidence:source-counter", "target": "hypothesis:transfer", "relation": "limits",
                    "status": "hypothesized", "evidence_ids": ["evidence:source-counter"], "conditions": {"context": "volatile"}}
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta({"schema_version": 1, "records": [source, observed, counterexample, mechanism, transfer], "edges": [analogy, source_limit]})
        report = store.quality("hypothesis:transfer")
        warning = next(finding for finding in report["findings"] if finding["code"] == "transfer_support_gap")
        assert warning["subject_id"] == "hypothesis:transfer"
        assert "does not alter" in warning["message"]

        # A distinct paper-bound observation is allowed as independently
        # recorded target evidence; this rule does not require a local run.
        outside_source = paper("paper:target", "10/target", "fulltext")
        outside_evidence = evidence("evidence:target", "paper:target")
        outside_evidence["data"].update({"evidence_kind": "observation", "assertion_role": "target_observation", "applicability": {"domain": "target-domain", "conditions": {}}})
        upgraded = dict(transfer)
        upgraded["data"] = dict(transfer["data"], evidence_ids=["evidence:source", "evidence:source-counter", "evidence:target"], evidence_roles={"evidence:source": "context", "evidence:source-counter": "counterevidence", "evidence:target": "support"})
        store.apply_delta({"schema_version": 1, "records": [outside_source, outside_evidence, upgraded], "edges": []})
        assert "transfer_support_gap" not in codes(store.quality("hypothesis:transfer"))


def test_established_mechanism_requires_evidence_for_delta_but_legacy_ingests_with_warning(tmp_path: Path) -> None:
    legacy = {"id": "mechanism:legacy", "type": "mechanism", "title": "legacy", "summary": "m", "tags": [],
              "data": {"origin": "author", "status": "supported", "conditions": ["legacy condition"]}}
    with ResearchStore(tmp_path, create=True) as store:
        with pytest.raises(ValueError, match="requires evidence_ids"):
            store.apply_delta({"schema_version": 1, "records": [legacy], "edges": []})
        # Plain exported/legacy import remains compatible and quality makes the
        # unsupported established label visible instead of rejecting history.
        store.ingest({"schema_version": 1, "records": [legacy], "edges": []})
        report = store.quality("mechanism:legacy")
        assert "mechanism_established_without_evidence" in codes(report)


def test_reported_edge_and_supported_claim_need_explicit_target_applicability(tmp_path: Path) -> None:
    source = paper("paper:source", "10/source", "fulltext")
    source_evidence = evidence("evidence:source", "paper:source")
    source_evidence["data"].update({"evidence_kind": "observation", "assertion_role": "source_observation", "applicability": {"domain": "source-domain", "conditions": {}}})
    target_claim = claim("claim:target", "evidence:source")
    target_claim["data"].update({"status": "supported", "evidence_roles": {"evidence:source": "support"}, "applicability": {"domain": "target-domain", "conditions": {}}})
    edge = {"id": "edge:reported", "source": "evidence:source", "target": "claim:target", "relation": "supports",
            "status": "reported", "evidence_ids": ["evidence:source"], "conditions": {}}
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta({"schema_version": 1, "records": [source, source_evidence, target_claim], "edges": [edge]})
        report = store.quality("claim:target")
        assert "claim_target_support_gap" in codes(report)
        mismatch = next(finding for finding in report["findings"] if finding["code"] == "edge_evidence_applicability_mismatch")
        assert mismatch["subject_id"] == "edge:reported"

        target_paper = paper("paper:target", "10/target", "fulltext")
        target_evidence = evidence("evidence:target", "paper:target")
        target_evidence["data"].update({"evidence_kind": "observation", "assertion_role": "target_observation", "applicability": {"domain": "target-domain", "conditions": {}}})
        upgraded_claim = dict(target_claim)
        upgraded_claim["data"] = dict(target_claim["data"], evidence_ids=["evidence:source", "evidence:target"], evidence_roles={"evidence:source": "context", "evidence:target": "support"})
        upgraded_edge = dict(edge, evidence_ids=["evidence:target"])
        store.apply_delta({"schema_version": 1, "records": [target_paper, target_evidence, upgraded_claim], "edges": [upgraded_edge]})
        upgraded_report = store.quality("claim:target")
        assert "claim_target_support_gap" not in codes(upgraded_report)
        assert not any(finding.get("subject_id") == "edge:reported" and finding["code"] in {"edge_evidence_applicability_mismatch", "edge_evidence_applicability_unknown"} for finding in upgraded_report["findings"])


def test_applicability_conditions_must_be_object_and_unknown_conditions_do_not_support(tmp_path: Path) -> None:
    malformed = evidence("evidence:bad", "paper:p")
    malformed["data"]["applicability"] = {"domain": "target", "conditions": ["not an object"]}
    with pytest.raises(ValueError, match="applicability.conditions"):
        normalize_bundle({"schema_version": 1, "records": [paper("paper:p", "10/p"), malformed], "edges": []})

    source = paper("paper:p", "10/p", "fulltext")
    observed = evidence("evidence:unknown", "paper:p")
    observed["data"].update({"evidence_kind": "observation", "assertion_role": "target_observation", "applicability": {"domain": "target"}})
    hypothesis = {"id": "hypothesis:h", "type": "hypothesis", "title": "h", "summary": "h", "tags": [],
                  "data": {"prediction": "p", "alternatives": [], "test": "t", "status": "supported", "evidence_ids": ["evidence:unknown"], "evidence_roles": {"evidence:unknown": "support"}, "applicability": {"domain": "target", "conditions": {"cohort": "A"}}}}
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta({"schema_version": 1, "records": [source, observed, hypothesis], "edges": []})
        assert "transfer_support_gap" in codes(store.quality("hypothesis:h"))


def test_hypothesized_limit_evidence_never_becomes_target_support(tmp_path: Path) -> None:
    source = paper("paper:p", "10/p", "fulltext")
    observed = evidence("evidence:target", "paper:p")
    observed["data"].update({"evidence_kind": "observation", "assertion_role": "target_observation", "applicability": {"domain": "target", "conditions": {}}})
    hypothesis = {"id": "hypothesis:h", "type": "hypothesis", "title": "h", "summary": "h", "tags": [],
                  "data": {"prediction": "p", "alternatives": [], "test": "t", "status": "supported", "evidence_ids": ["evidence:target"], "evidence_roles": {"evidence:target": "support"}, "applicability": {"domain": "target", "conditions": {}}}}
    limit = {"id": "edge:limit", "source": "evidence:target", "target": "hypothesis:h", "relation": "limits", "status": "hypothesized", "evidence_ids": ["evidence:target"], "conditions": {}}
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta({"schema_version": 1, "records": [source, observed, hypothesis], "edges": [limit]})
        assert "transfer_support_gap" in codes(store.quality("hypothesis:h"))
