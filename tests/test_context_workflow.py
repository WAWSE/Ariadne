from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_harness import cli
from research_harness.context import UNTRUSTED_END, UNTRUSTED_START, _focused_bridge_lines, build_context


class StubStore:
    def __init__(self, records: list[dict[str, Any]], edges: list[dict[str, Any]], state: dict[str, Any]):
        self.records = {record["id"]: record for record in records}
        self.edges = edges
        self.state = state
        self.search_calls: list[tuple[str, str, int]] = []
        self.quality_calls: list[tuple[str | None, int]] = []

    def get_state(self) -> dict[str, Any]:
        return self.state

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        return self.records.get(record_id)

    def list_records(self, limit: int = 100, **_kwargs: Any) -> list[dict[str, Any]]:
        return list(self.records.values())[:limit]

    def graph(self, seed: str | None = None, hops: int = 1, limit: int = 80) -> dict[str, Any]:
        ids = {seed} if seed in self.records else set()
        for edge in self.edges:
            if edge["source"] == seed or edge["target"] == seed:
                ids.update((edge["source"], edge["target"]))
        # Match the store's bounded reverse navigation for experiments linked
        # by their authored hypothesis_id, without turning tests into a global
        # record scan in the renderer itself.
        for record in self.records.values():
            data = record.get("data") if isinstance(record.get("data"), dict) else {}
            if record.get("type") == "experiment" and data.get("hypothesis_id") == seed:
                ids.add(record["id"])
        return {"records": [self.records[record_id] for record_id in sorted(ids) if record_id in self.records][:limit],
                "edges": [edge for edge in self.edges if edge["source"] in ids and edge["target"] in ids], "truncated": False}

    def search(self, query: str, mode: str, limit: int) -> dict[str, Any]:
        self.search_calls.append((query, mode, limit))
        return {"results": [{"record": record} for record in self.records.values() if query.casefold() in str(record).casefold()][:limit], "warnings": []}

    def quality(self, focus: str | None = None, limit: int = 100) -> dict[str, Any]:
        self.quality_calls.append((focus, limit))
        return {"scope": focus, "findings": [{"code": "stale_basis", "severity": "warning", "entity_ids": [focus] if focus else [], "message": "basis needs review"}], "counts": {"error": 0, "warning": 1, "info": 0}, "truncated": False}


def test_state_only_handoff_notes_recover_without_promoting_graph_assertions() -> None:
    store = StubStore([], [], {
        "question": "Does the observed development gain survive a frozen held check?",
        "next_action": "Run the already frozen held comparison once.",
        "notes": [
            "E01 | source/dev | valid observation | five-seed low-SNR gain only",
            "E03 | source/dev | invalid old run | exclude from effect table",
            "E04 | held | planned, not completed | no held metric yet",
        ],
    })
    brief = build_context(store, intent="resume", max_chars=2000)
    assert len(brief) <= 2000
    assert "E01 | source/dev | valid observation" in brief
    assert "E03 | source/dev | invalid old run" in brief
    assert "E04 | held | planned, not completed" in brief
    assert "saved handoff note (unverified; not graph evidence)" in brief
    assert "see unverified handoff notes" in brief
    assert "no active hypothesis or saved unresolved item" not in brief
    assert brief.index(UNTRUSTED_START) < brief.index("E01") < brief.index(UNTRUSTED_END)


def test_plain_handoff_file_recovers_as_unverified_context(tmp_path: Path) -> None:
    store = StubStore([], [], {})
    store.root = tmp_path
    research = tmp_path / ".research"
    research.mkdir()
    (research / "handoff.md").write_text(
        "Question: Will the source/dev gain replicate?\n"
        "E01 | source/dev | valid | delta R5 +0.019448\n"
        "E03 | invalid old run | exclude from effect table\n"
        "E04 | held | planned, not completed\n",
        encoding="utf-8",
    )
    brief = build_context(store, intent="resume", max_chars=2000)
    assert "unverified handoff file: .research/handoff.md" in brief
    assert "E01 | source/dev | valid" in brief
    assert "E03 | invalid old run" in brief
    assert "see unverified handoff notes" in brief
    assert "no active hypothesis or saved unresolved item" not in brief
    assert brief.index(UNTRUSTED_START) < brief.index("E01") < brief.index(UNTRUSTED_END)


def test_plain_handoff_coverage_ledger_is_explicit_but_unverified(tmp_path: Path) -> None:
    store = StubStore([], [], {})
    store.root = tmp_path
    research = tmp_path / ".research"
    research.mkdir()
    (research / "handoff.md").write_text("coverage=E01 used; E02 used; E03 not-read\n", encoding="utf-8")
    brief = build_context(store, intent="resume", max_chars=2000)
    assert "handoff coverage ledger (unverified): E01 used; E02 used; E03 not-read" in brief


def test_focused_context_reports_handoff_ids_absent_from_graph(tmp_path: Path) -> None:
    store = StubStore([
        {"id": "problem:transfer", "type": "problem", "title": "Transfer", "summary": "", "data": {}},
        {"id": "evidence:paper-1", "type": "evidence", "title": "Observation", "summary": "Bounded result", "data": {}},
    ], [{"id": "edge:observed", "source": "problem:transfer", "target": "evidence:paper-1",
         "relation": "has_evidence", "status": "structural", "evidence_ids": []}], {})
    store.root = tmp_path
    (tmp_path / ".research").mkdir()
    (tmp_path / ".research" / "handoff.md").write_text(
        "protocol-v2: prospective experiment; not observed\n"
        "mapping-register: route provenance\n"
        "coverage=paper-1 used; protocol-v2 used; mapping-register used\n", encoding="utf-8")
    brief = build_context(store, intent="map", focus=["problem:transfer"])
    assert "coverage: partial (unverified handoff ledger)" in brief
    assert "IDs not displayed: protocol-v2, mapping-register" in brief
    assert "read task-relevant entries in .research/handoff.md" in brief
    assert "not graph record IDs" in brief
    assert "IDs not displayed: paper-1" not in brief
    assert len(brief) <= 6000
    small = build_context(store, intent="map", focus=["problem:transfer"], max_chars=1000)
    assert len(small) <= 1000
    assert "coverage: partial" in small


def test_partial_graph_does_not_hide_additional_handoff_sources(tmp_path: Path) -> None:
    hypothesis = {
        "id": "hypothesis:partial", "type": "hypothesis", "title": "Partial route",
        "summary": "", "tags": [],
        "data": {"status": "proposed", "prediction": "Gain under C2",
                 "test": "Bounded C2 probe", "alternatives": ["confound"], "evidence_ids": []},
    }
    store = StubStore([hypothesis], [], {"active_hypotheses": [hypothesis["id"]]})
    store.root = tmp_path
    (tmp_path / ".research").mkdir()
    (tmp_path / ".research" / "handoff.md").write_text(
        "E02 limitation: missing matched control\n"
        "E03 external analogy: target transfer remains untested\n"
        "E04 plan: not yet an observation\n"
        "coverage=E02 used; E03 used; E04 used\n", encoding="utf-8",
    )
    brief = build_context(store, intent="resume", max_chars=2000)
    assert len(brief) <= 2000
    assert "active hypothesis: hypothesis:partial" in brief
    assert "E02 limitation: missing matched control" in brief
    assert "E03 external analogy" in brief
    assert "E04 plan: not yet an observation" in brief
    assert "not necessarily graph record IDs" in brief
    assert "handoff coverage ledger (unverified)" in brief
    assert brief.index(UNTRUSTED_START) < brief.index("E02 limitation") < brief.index(UNTRUSTED_END)
    assert not store.search_calls and not store.quality_calls


def _fixture() -> StubStore:
    paper = {"id": "paper:abstract", "type": "paper", "title": "Abstract-only source", "summary": "source", "tags": [],
             "data": {"read_scope": "abstract", "version": "v1", "url": "https://example.invalid"}}
    evidence_positive = {"id": "evidence:positive", "type": "evidence", "title": "positive", "summary": "", "tags": [],
                         "data": {"paper_id": "paper:abstract", "locator": "abstract p1", "excerpt": "positive cue result"}}
    evidence_opposing = {"id": "evidence:opposing", "type": "evidence", "title": "opposition", "summary": "", "tags": [],
                         "data": {"paper_id": "paper:abstract", "locator": "abstract p2", "excerpt": "opposing volatility result"}}
    hypothesis = {"id": "hypothesis:focus", "type": "hypothesis", "title": "Focused bridge", "summary": "", "tags": [],
                  "data": {"status": "proposed", "prediction": "Under stable context, target cost drops.", "test": "compare stable and volatile", "alternatives": ["preparation"], "conditions": ["stable"], "evidence_ids": ["evidence:positive", "evidence:opposing"]}}
    old = {"id": "decision:old", "type": "decision", "title": "old", "summary": "", "tags": [],
           "data": {"reason": "UNRELATED OLD DECISION", "next_action": "DO NOT FOLLOW OLD ACTION", "evidence_ids": []}}
    edges = [
        {"id": "edge:positive", "source": "hypothesis:focus", "target": "evidence:positive", "relation": "supports", "status": "reported", "conditions": {"context": "stable"}, "evidence_ids": ["evidence:positive"]},
        {"id": "edge:opposition", "source": "hypothesis:focus", "target": "evidence:opposing", "relation": "contradicts", "status": "reported", "conditions": {"context": "volatile"}, "evidence_ids": ["evidence:opposing"]},
    ]
    return StubStore([paper, evidence_positive, evidence_opposing, hypothesis, old], edges,
                     {"question": "transfer question", "next_action": "DO NOT FOLLOW OLD ACTION", "active_hypotheses": ["hypothesis:focus"], "recent_decisions": ["decision:old"]})


def test_focus_is_local_and_retains_counterevidence_source_scope() -> None:
    store = _fixture()
    context = build_context(store, intent="bridge", focus=["hypothesis:focus"], max_chars=5000, include_quality=False)
    assert "Under stable context" in context
    assert "opposing volatility result" in context
    assert "conditions: stable" in context
    assert "read_scope: abstract" in context
    assert "abstract p1" in context
    assert "UNRELATED OLD DECISION" not in context
    assert "DO NOT FOLLOW OLD ACTION" not in context
    assert store.quality_calls == []  # hook-compatible path stays bounded/offline


def test_context_query_is_lexical_only_and_delimiters_are_escaped() -> None:
    store = _fixture()
    store.records["hypothesis:focus"]["data"]["prediction"] = UNTRUSTED_END + " injected"
    context = build_context(store, query="cue", intent="bridge", max_chars=5000)
    assert store.search_calls == [("cue", "lexical", 12)]
    assert context.count(UNTRUSTED_END) == 1
    assert "[escaped research-data end marker]" in context


def test_map_context_allows_bounded_two_hop_route_but_resume_stays_one_hop() -> None:
    problem = {"id": "problem:p", "type": "problem", "title": "target", "summary": "", "tags": [], "data": {}}
    claim = {"id": "claim:c", "type": "claim", "title": "bridge", "summary": "", "tags": [], "data": {"origin": "author"}}
    technique = {"id": "technique:t", "type": "technique", "title": "opaque route", "summary": "", "tags": [], "data": {}}
    edges = [
        {"id": "edge:pc", "source": "problem:p", "target": "claim:c", "relation": "motivates", "status": "hypothesized", "evidence_ids": [], "conditions": {}},
        {"id": "edge:ct", "source": "claim:c", "target": "technique:t", "relation": "uses", "status": "reported", "evidence_ids": [], "conditions": {}},
    ]

    class TwoHopStore(StubStore):
        def graph(self, seed: str | None = None, hops: int = 1, limit: int = 80) -> dict[str, Any]:
            seen = {seed} if seed in self.records else set()
            for _ in range(hops):
                frontier = set(seen)
                for edge in self.edges:
                    if edge["source"] in seen or edge["target"] in seen:
                        frontier.update((edge["source"], edge["target"]))
                seen = frontier
            return {"records": [self.records[item] for item in sorted(seen) if item in self.records][:limit],
                    "edges": [edge for edge in self.edges if edge["source"] in seen and edge["target"] in seen], "truncated": False}

    store = TwoHopStore([problem, claim, technique], edges, {})
    mapped = build_context(store, intent="map", focus=["problem:p"], max_chars=5000)
    resumed = build_context(store, intent="resume", focus=["problem:p"], max_chars=5000)
    assert "technique:t" in mapped
    assert "technique:t" not in resumed


def test_map_context_renders_nonincident_two_hop_edge_with_qualifiers() -> None:
    """A map route must retain the second edge's authored provenance fields."""
    problem = {"id": "problem:route", "type": "problem", "title": "target", "summary": "", "tags": [], "data": {}}
    mechanism = {"id": "mechanism:route", "type": "mechanism", "title": "interaction", "summary": "", "tags": [], "data": {}}
    technique = {"id": "technique:route", "type": "technique", "title": "cross attention", "summary": "", "tags": [], "data": {}}
    evidence = {"id": "evidence:route", "type": "evidence", "title": "source", "summary": "", "tags": [], "data": {"evidence_kind": "author_report"}}
    edges = [
        {"id": "edge:route-1", "source": "problem:route", "target": "mechanism:route", "relation": "motivates", "status": "hypothesized", "evidence_ids": [], "conditions": {"task": "target"}},
        {"id": "edge:route-2", "source": "mechanism:route", "target": "technique:route", "relation": "uses", "status": "reported", "evidence_ids": ["evidence:route"], "conditions": {"representation": "factorized"}},
    ]

    class RouteStore(StubStore):
        def graph(self, seed: str | None = None, hops: int = 1, limit: int = 80) -> dict[str, Any]:
            seen = {seed} if seed in self.records else set()
            for _ in range(hops):
                frontier = set(seen)
                for edge in self.edges:
                    if edge["source"] in seen or edge["target"] in seen:
                        frontier.update((edge["source"], edge["target"]))
                seen = frontier
            return {"records": [self.records[item] for item in sorted(seen) if item in self.records][:limit],
                    "edges": [edge for edge in self.edges if edge["source"] in seen and edge["target"] in seen], "truncated": False}

    store = RouteStore([problem, mechanism, technique, evidence], edges, {})
    context = build_context(store, intent="map", focus=["problem:route"], max_chars=5000)
    assert "edge:route-2" not in context  # IDs are not independently rendered
    assert "mechanism:route --uses/reported--> technique:route" in context
    assert "conditions={'representation': 'factorized'}" in context
    assert "evidence_ids=['evidence:route']" in context


def test_map_context_surfaces_observed_conflict_boundary_without_rewriting_status() -> None:
    problem = {"id": "problem:p2", "type": "problem", "title": "target", "summary": "", "tags": [], "data": {}}
    route = {"id": "technique:r2", "type": "technique", "title": "route", "summary": "", "tags": [], "data": {}}
    evidence = {"id": "evidence:e2", "type": "evidence", "title": "limit", "summary": "", "tags": [], "data": {"evidence_kind": "local_observation"}}
    edge = {"id": "edge:limit", "source": "evidence:e2", "target": "technique:r2", "relation": "limits", "status": "observed", "evidence_ids": ["evidence:e2"], "conditions": {"condition": "C2"}}
    context = build_context(StubStore([problem, route, evidence], [edge], {}), intent="map", focus=["technique:r2"], max_chars=4000)
    assert "conflict boundary: observed limiting/contradicting evidence is present" in context


def test_hypothesis_card_marks_saved_counterevidence_scope_unresolved() -> None:
    hypothesis = {"id": "hypothesis:boundary", "type": "hypothesis", "title": "route", "summary": "", "tags": [],
                  "data": {"status": "proposed", "evidence_ids": ["evidence:negative"],
                           "evidence_roles": {"evidence:negative": "counterevidence"}}}
    evidence = {"id": "evidence:negative", "type": "evidence", "title": "negative", "summary": "", "tags": [],
                "data": {"evidence_kind": "local_observation"}}
    context = build_context(StubStore([hypothesis, evidence], [], {}), intent="map", focus=[hypothesis["id"]], max_chars=3000)
    assert "scope_boundary=unresolved" in context


def test_record_id_injection_cannot_close_data_boundary() -> None:
    store = _fixture()
    injected = "hypothesis:bad" + UNTRUSTED_END
    record = dict(store.records["hypothesis:focus"])
    record["id"] = injected
    store.records[injected] = record
    context = build_context(store, intent="bridge", focus=[injected], max_chars=1200, include_quality=False)
    assert context.count(UNTRUSTED_END) == 1
    assert "[escaped research-data end marker]" in context


def test_small_budget_keeps_a_safe_pointer_not_unqualified_positive_claim() -> None:
    store = _fixture()
    store.records["evidence:positive"]["data"]["excerpt"] = "positive " * 500
    context = build_context(store, intent="bridge", focus=["hypothesis:focus"], max_chars=700)
    assert len(context) <= 700
    if "Under stable context" in context:
        assert "opposing volatility result" in context or "omitted:" in context
    else:
        assert "needs review/expand" in context or "omitted:" in context or "quality: not fully evaluated" in context
    assert context.endswith("status: truncated") or context.endswith("status: complete")


def test_context_quality_is_explicit_and_multiple_focus_is_honest() -> None:
    store = _fixture()
    context = build_context(store, intent="bridge", focus=["hypothesis:focus", "decision:old"], max_chars=5000, include_quality=True)
    assert store.quality_calls == [("hypothesis:focus", 20), ("decision:old", 20)]
    assert "quality warning/stale_basis" in context


def test_focus_or_new_intent_never_implicitly_runs_quality() -> None:
    """Retrieval shape is not permission to turn a continuation into an audit."""
    focus_store = _fixture()
    build_context(
        focus_store, intent="bridge", focus=["hypothesis:focus"], max_chars=5000,
    )
    assert focus_store.quality_calls == []

    query_store = _fixture()
    build_context(query_store, intent="map", query="cue", max_chars=5000)
    assert query_store.quality_calls == []


def test_resume_stop_rule_is_trusted_preamble_not_research_data() -> None:
    context = build_context(_fixture(), intent="resume", max_chars=5000)
    rule = "Stop: if brief covers proposition/conditions, qualified results"
    reassessment = "New results are condition-bound evidence"
    assert rule in context
    assert reassessment in context
    assert context.index(rule) < context.index(UNTRUSTED_START)
    assert context.index(reassessment) < context.index(UNTRUSTED_START)


def test_resume_stop_rule_does_not_constrain_an_explicit_task_context() -> None:
    """A retrieval stop rule is for ordinary resume, not a map/bridge request."""

    rule = "Stop: if brief covers proposition/conditions, qualified results"
    focused = build_context(_fixture(), intent="bridge", focus=["hypothesis:focus"], max_chars=5000)
    mapped = build_context(_fixture(), intent="map", query="target", max_chars=5000)
    assert rule not in focused
    assert rule not in mapped


def test_unqualified_resume_defaults_to_recovery_only_and_opt_ins_are_independent() -> None:
    summary_store = _fixture()
    summary = build_context(summary_store, intent="resume", max_chars=5000)
    assert "## Recovery priorities" in summary
    assert "### evidence bundle" not in summary
    assert summary_store.quality_calls == []

    details_store = _fixture()
    details = build_context(details_store, intent="resume", max_chars=5000, include_details=True)
    assert "### evidence bundle: hypothesis:focus" in details
    assert details_store.quality_calls == []

    quality_store = _fixture()
    quality = build_context(quality_store, intent="resume", max_chars=5000, include_quality=True)
    assert "### evidence bundle" not in quality
    assert quality_store.quality_calls == [("hypothesis:focus", 20)]

    both_store = _fixture()
    both = build_context(
        both_store, intent="resume", max_chars=5000,
        include_details=True, include_quality=True,
    )
    assert "### evidence bundle: hypothesis:focus" in both
    assert both_store.quality_calls == [("hypothesis:focus", 20)]


def test_cli_context_opt_in_flags_preserve_default_intent_behavior(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "_project_for", lambda _args: tmp_path)
    monkeypatch.setattr(
        cli, "context_for_project",
        lambda _root, **kwargs: calls.append(kwargs) or "context",
    )

    args = cli._parser().parse_args(["context", "--intent", "resume"])
    assert cli._dispatch(args) == "context"
    assert calls.pop() == {
        "query": None, "max_chars": 6000, "intent": "resume", "focus": [],
        "include_details": None, "include_quality": None,
    }

    args = cli._parser().parse_args(["context", "--intent", "resume", "--with-details", "--with-quality"])
    assert cli._dispatch(args) == "context"
    call = calls.pop()
    assert call["include_details"] is True
    assert call["include_quality"] is True


def test_intent_changes_deterministic_field_priority_without_classifier() -> None:
    store = _fixture()
    bridge = build_context(store, intent="bridge", focus=["hypothesis:focus"], max_chars=5000, include_quality=False)
    read = build_context(store, intent="read", focus=["hypothesis:focus"], max_chars=5000, include_quality=False)
    # The same stored data is present, but bridge foregrounds its test/conditions
    # while read foregrounds source scope/locator. No intent classifier is used.
    assert bridge.index("prediction:") < bridge.index("locator: abstract p1")
    assert "locator: abstract p1" in read
    assert "prediction:" not in read


def test_explicit_workflow_focus_keeps_a_bridge_critical_path_at_small_budgets() -> None:
    """The real workflow fixture must not collapse to an expand pointer at 1800."""
    root = Path(__file__).resolve().parents[1]
    delta = json.loads((root / "examples" / "workflow-remember-delta.json").read_text(encoding="utf-8"))
    state = json.loads((root / "examples" / "workflow-context-state.json").read_text(encoding="utf-8"))
    for budget in (1800, 3000):
        store = StubStore(delta["records"], delta["edges"], state)
        context = build_context(
            store,
            intent="bridge",
            focus=["hypothesis:workflow-transfer"],
            query="volatile context",
            max_chars=budget,
        )
        assert len(context) <= budget
        assert (
            "hypothesis: hypothesis:workflow-transfer | status=proposed" in context
            or "record: hypothesis:workflow-transfer | type=hypothesis | status=proposed" in context
        )
        assert "prediction: Under stable target context" in context
        assert "test: Compare intermittent and matched continuous cues" in context
        assert "alternatives: The cue changes preparation rather than gating." in context
        assert ("conditions={'context': 'volatile'}" in context or "failure_condition': 'volatile target context may remove the effect" in context)
        assert "evidence:workflow-counterexample" in context
        assert "locator: Abstract, paragraph 2" in context
        assert "association disappeared when context volatility increased" in context
        assert "read_scope: abstract" in context
        assert "shown only as an expand pointer" not in context


def test_resume_recovery_digest_preserves_current_work_before_old_action() -> None:
    store = _fixture()
    store.records["hypothesis:focus"]["data"].update({
        "applicability": {"domain": "target task", "conditions": {}},
        "evidence_roles": {"evidence:opposing": "counterevidence"},
    })
    store.records["evidence:opposing"]["data"].update({
        "evidence_kind": "observation",
        "applicability": {"domain": "target task", "conditions": {}},
    })
    store.edges[1].update({
        "source": "evidence:opposing", "target": "hypothesis:focus", "status": "observed",
        "evidence_roles": {"evidence:opposing": "counterevidence"},
    })
    context = build_context(store, intent="resume", max_chars=2000, include_quality=False)
    assert len(context) <= 2000
    assert "active hypothesis: hypothesis:focus | status=proposed" in context
    assert "prediction: Under stable context, target cost drops." in context
    assert "test: compare stable and volatile" in context
    assert "recent decision: decision:old" in context
    assert "decision reason: UNRELATED OLD DECISION" in context
    assert "uncertainty (alternatives): preparation" in context
    assert "observed limiting evidence: evidence:opposing | locator: abstract p2" in context
    assert "kind=observation" in context
    assert "recorded_scope=match" in context
    if "DO NOT FOLLOW OLD ACTION" in context:
        assert context.index("DO NOT FOLLOW OLD ACTION") > context.index("observed limiting evidence")


def test_resume_recovery_keeps_labelled_source_report_and_interpretation_for_transfer() -> None:
    store = _fixture()
    source_report = {
        "id": "evidence:source-report", "type": "evidence", "title": "source result", "summary": "", "tags": [],
        "data": {
            "paper_id": "paper:abstract", "locator": "Results, table 2", "excerpt": "Authors report 0.61 -> 0.71 with Cross Attention.",
            "evidence_kind": "author_report", "applicability": {"domain": "speech source", "conditions": {}},
        },
    }
    source_interpretation = {
        "id": "evidence:source-interpretation", "type": "evidence", "title": "source explanation", "summary": "", "tags": [],
        "data": {
            "paper_id": "paper:abstract", "locator": "Discussion, paragraph 1", "excerpt": "Authors suggest selective information exchange improves factor separation.",
            "evidence_kind": "author_interpretation", "applicability": {"domain": "speech source", "conditions": {}},
        },
    }
    target_context = {
        "id": "evidence:target-context", "type": "evidence", "title": "target context", "summary": "", "tags": [],
        "data": {
            "paper_id": "paper:abstract", "locator": "Methods, paragraph 1", "excerpt": "Unpaired target images.",
            "evidence_kind": "source_excerpt", "applicability": {"domain": "microscopy target", "conditions": {}},
        },
    }
    store.records.update({record["id"]: record for record in (source_report, source_interpretation, target_context)})
    hypothesis = store.records["hypothesis:focus"]
    hypothesis["data"].update({
        "status": "proposed",
        "assertion_role": "transfer_proposal",
        "applicability": {"target_domain": "microscopy target", "conditions": {}},
        "evidence_ids": ["evidence:source-report", "evidence:source-interpretation", "evidence:target-context"],
        "evidence_roles": {
            "evidence:source-report": "analogy", "evidence:source-interpretation": "analogy",
            "evidence:target-context": "context",
        },
    })
    context = build_context(store, intent="resume", max_chars=2000, include_quality=False)
    assert len(context) <= 2000
    assert "active hypothesis: hypothesis:focus | status=proposed" in context
    assert "active hypothesis: hypothesis:focus | status=proposed" in context
    assert "source author report (not target result): evidence:source-report | locator: Results, table 2" in context
    assert "Authors report 0.61 -> 0.71 with Cross Attention." in context
    assert "source author interpretation (attributed, not established mechanism): evidence:source-interpretation" in context
    assert "Authors suggest selective information exchange improves factor separation." in context
    assert "role=analogy" in context
    assert "kind=author_report" in context
    assert "recorded_scope=different" in context
    # Core saved decision context precedes optional provenance cards at the
    # fixed SessionStart budget.
    assert context.index("recent decision") < context.index("source author report")
    assert "source author report (not target result): evidence:target-context" not in context


def test_resume_recovery_marks_a_long_author_report_as_not_expanded() -> None:
    """A resume card must not manufacture a qualified source excerpt from sentences."""
    store = _fixture()
    source_report = {
        "id": "evidence:long-source-report", "type": "evidence", "title": "long source report", "summary": "", "tags": [],
        "data": {
            "paper_id": "paper:abstract", "locator": "Very long frozen task-brief locator " * 12,
            "excerpt": (
                "Background wording that does not itself state a result. "
                "The authors report that cross-attention changed the source score from .61 to .71. "
                "With noisy pairing, the reported gain disappeared. "
                + "Additional source discussion that is intentionally irrelevant to recovery. " * 12
            ),
            "evidence_kind": "author_report", "applicability": {"domain": "speech source", "conditions": {}},
        },
    }
    source_interpretation = {
        "id": "evidence:source-interpretation", "type": "evidence", "title": "source explanation", "summary": "", "tags": [],
        "data": {
            "paper_id": "paper:abstract", "locator": "Discussion, paragraph 1",
            "excerpt": "Authors suggest selective information exchange may improve factor separation; this is not an established mechanism.",
            "evidence_kind": "author_interpretation", "applicability": {"domain": "speech source", "conditions": {}},
        },
    }
    store.records.update({record["id"]: record for record in (source_report, source_interpretation)})
    hypothesis = store.records["hypothesis:focus"]
    hypothesis["data"].update({
        "assertion_role": "transfer_proposal",
        "applicability": {"target_domain": "microscopy target", "conditions": {}},
        "prediction": "Very long target prediction " * 30,
        "test": "Very long target test description " * 30,
        "evidence_ids": ["evidence:long-source-report", "evidence:source-interpretation"],
        "evidence_roles": {"evidence:long-source-report": "analogy", "evidence:source-interpretation": "analogy"},
    })
    context = build_context(store, intent="resume", max_chars=2000, include_quality=False)
    assert len(context) <= 2000
    assert "source author report (not target result): evidence:long-source-report" in context
    assert "source basis: [long excerpt not expanded; inspect `srh show evidence:long-source-report`]" in context
    assert "The authors report that cross-attention changed the source score from .61 to .71." not in context
    assert "With noisy pairing, the reported gain disappeared." not in context
    assert "source author interpretation (attributed, not established mechanism): evidence:source-interpretation" in context
    assert "not an established mechanism" in context
    assert "[selected exact excerpt sentences]" not in context


def test_only_a_directed_observed_edge_can_label_limiting_evidence() -> None:
    store = _fixture()
    # The direction is wrong for a limitation of hypothesis:focus, and it does
    # not name evidence. The hypothesis's generic evidence must not be relabelled.
    store.edges = [{
        "id": "edge:wrong-direction", "source": "hypothesis:focus", "target": "evidence:opposing",
        "relation": "limits", "status": "observed", "conditions": {}, "evidence_ids": [],
    }]
    context = build_context(store, intent="resume", max_chars=2000, include_quality=False)
    assert "observed limiting evidence" not in context
    assert "counterexample evidence" not in context
    # With no directional role, recovery preserves the hypothesis's saved
    # evidence order instead of using the lexicographically later ID as a
    # proxy for recency or importance.
    assert "related evidence: evidence:positive" in context

    # Even correct direction/status is insufficient when the edge itself does
    # not identify evidence. Generic hypothesis evidence may not fill that gap.
    store.edges = [{
        "id": "edge:no-evidence", "source": "evidence:opposing", "target": "hypothesis:focus",
        "relation": "limits", "status": "observed", "conditions": {}, "evidence_ids": [],
    }]
    context = build_context(store, intent="resume", max_chars=2000, include_quality=False)
    assert "observed limiting evidence" not in context
    assert "recorded observed limiting relation for hypothesis:focus has no qualified saved evidence locator" in context


def test_hypothesized_edge_is_presented_as_potential_risk_not_counterexample() -> None:
    store = _fixture()
    store.edges[1].update({"source": "evidence:opposing", "target": "hypothesis:focus", "relation": "limits", "status": "hypothesized"})
    context = build_context(store, intent="bridge", focus=["hypothesis:focus"], max_chars=1800, include_quality=False)
    assert "potential-risk path:" in context
    assert "evidence:opposing" in context
    assert "counterexample evidence" not in context


def test_source_scope_cannot_be_rendered_as_an_observed_target_limitation() -> None:
    store = _fixture()
    store.records["hypothesis:focus"]["data"].update({
        "applicability": {"domain": "target task", "conditions": {}},
        "evidence_roles": {"evidence:opposing": "counterevidence"},
    })
    store.records["evidence:opposing"]["data"].update({
        "evidence_kind": "observation",
        "applicability": {"domain": "source task", "conditions": {}},
    })
    store.edges[1].update({
        "source": "evidence:opposing", "target": "hypothesis:focus", "relation": "limits", "status": "observed",
        "evidence_roles": {"evidence:opposing": "counterevidence"},
    })
    context = build_context(store, intent="resume", max_chars=2000, include_quality=False)
    assert "observed limiting evidence" not in context
    assert "related evidence: evidence:opposing" in context
    assert "recorded_scope=different" in context


def test_focused_bridge_uses_the_qualified_edge_evidence_not_first_edge_item() -> None:
    """A mixed edge list must not relabel an earlier source item as target evidence."""
    store = _fixture()
    hypothesis = store.records["hypothesis:focus"]
    hypothesis["data"].update({
        "applicability": {"domain": "target task", "conditions": {}},
        "evidence_roles": {
            "evidence:positive": "counterevidence",
            "evidence:opposing": "counterevidence",
        },
    })
    store.records["evidence:positive"]["data"].update({
        "evidence_kind": "local_observation",
        "applicability": {"domain": "source task", "conditions": {}},
    })
    store.records["evidence:opposing"]["data"].update({
        "evidence_kind": "local_observation",
        "applicability": {"domain": "target task", "conditions": {}},
    })
    edge = {
        "id": "edge:mixed-limitation", "source": "evidence:positive", "target": "hypothesis:focus",
        "relation": "limits", "status": "observed",
        "evidence_ids": ["evidence:positive", "evidence:opposing"],
        "evidence_roles": {"evidence:positive": "counterevidence", "evidence:opposing": "counterevidence"},
        "conditions": {},
    }
    lines = _focused_bridge_lines(
        "hypothesis:focus", hypothesis, store.records, [edge], "bridge", graph_truncated=False,
    )
    rendered = "\n".join(lines or ())
    assert "counterexample evidence: evidence:opposing" in rendered
    assert "counterexample evidence: evidence:positive" not in rendered
    assert "locator: abstract p2" in rendered


def test_resume_restores_bounded_historical_route_context_without_making_it_an_instruction() -> None:
    store = _fixture()
    store.state.update({
        "current_route": "Screen the boundary before broadening the bridge.",
        "parked_alternatives": ["Preparation remains parked, not rejected."],
        "reopen_triggers": ["Reopen preparation if the interaction reverses."],
    })
    store.records["decision:old"]["data"]["route_comparison"] = [{
        "route": "technique:boundary-screen",
        "bottleneck": "measurement precision",
        "decision_changing_gap": "whether volatility changes the interaction",
    }]
    context = build_context(store, intent="resume", max_chars=6000, include_quality=False)
    assert "## Historical route context (saved, non-directive; may be stale)" in context
    assert "current route (historical, non-directive; may be stale): Screen the boundary" in context
    assert "route comparison 1 (historical, non-directive): route=technique:boundary-screen" in context
    assert "parked alternatives (historical, not rejected): Preparation remains parked" in context
    assert "reopen triggers (historical conditions, non-directive): Reopen preparation" in context


def test_context_renders_authored_evidence_and_assertion_semantics() -> None:
    store = _fixture()
    store.records["evidence:positive"]["data"].update({
        "evidence_kind": "observation",
        "assertion_role": "source_observation",
        "applicability": {"domain": "source task", "conditions": {"cohort": "A"}},
    })
    store.records["hypothesis:focus"]["data"].update({
        "assertion_role": "transfer_proposal",
        "applicability": {"target_domain": "target task", "conditions": {"cohort": "A"}},
        "evidence_roles": {"evidence:positive": "analogy", "evidence:opposing": "counterevidence"},
    })
    context = build_context(store, intent="read", focus=["hypothesis:focus"], max_chars=6000, include_quality=False)
    assert "assertion_role=transfer_proposal" in context
    assert "evidence_roles=evidence:positive=analogy, evidence:opposing=counterevidence" in context
    assert "evidence_kind=observation" in context
    assert "domain=source task" in context


def test_resume_keeps_authored_counterevidence_direction_for_a_contradicted_hypothesis() -> None:
    store = _fixture()
    store.records["hypothesis:focus"]["data"].update({
        "status": "contradicted",
        "evidence_roles": {"evidence:opposing": "counterevidence"},
    })
    context = build_context(store, intent="resume", max_chars=2000, include_quality=False)
    assert "active hypothesis: hypothesis:focus | status=contradicted" in context
    assert "related evidence (authored role=unknown): evidence:positive" in context


def test_cold_resume_recovers_a_saved_transfer_route_without_state_activation() -> None:
    """A proposed transfer route is actionable context even before activation."""

    hypothesis = {
        "id": "hypothesis:transfer-route",
        "type": "hypothesis",
        "title": "transfer route",
        "summary": "",
        "tags": [],
        "data": {
            "status": "proposed",
            "assertion_role": "transfer_proposal",
            "prediction": "Target interaction may reduce the target error.",
            "test": "Compare against matched baseline.",
            "transfer_mapping": {"target_problem": "target task"},
        },
    }
    unrelated = {
        "id": "claim:source",
        "type": "claim",
        "title": "source claim",
        "summary": "",
        "tags": [],
        "data": {"status": "reported"},
    }
    store = StubStore([unrelated, hypothesis], [], {"question": "target question"})

    resume = build_context(store, intent="resume", max_chars=3000, include_quality=False)
    bridge = build_context(store, intent="bridge", max_chars=3000, include_quality=False)

    assert "saved route hypothesis: hypothesis:transfer-route | status=proposed" in resume
    assert "Target interaction may reduce the target error." in resume
    assert bridge.index("hypothesis:transfer-route") < bridge.index("claim:source")


def test_cold_resume_prioritizes_numeric_target_counterevidence_and_saved_boundary() -> None:
    """A later session needs the observed magnitude and threshold, not just an ID."""

    source = {
        "id": "source:source-report", "type": "source", "title": "source", "summary": "", "tags": [],
        "data": {"kind": "supplied_source"},
    }
    source_evidence = {
        "id": "evidence:source-effect", "type": "evidence", "title": "source effect", "summary": "", "tags": [],
        "data": {
            "source_id": source["id"], "locator": "source result",
            "excerpt": "Under synchronized streams, the metric changed from 0.42 to 0.27.",
            "evidence_kind": "author_report",
            "applicability": {"domain": "source streams", "task_context": "leakage"},
        },
    }
    target_source = {
        "id": "source:target-run", "type": "source", "title": "target run", "summary": "", "tags": [],
        "data": {"kind": "supplied_target_run"},
    }
    target_evidence = {
        "id": "evidence:target-negative", "type": "evidence", "title": "target negative", "summary": "", "tags": [],
        "data": {
            "source_id": target_source["id"], "locator": "target result",
            "excerpt": "Target metric changed from 0.31 to 0.30; preregistered threshold was 0.05.",
            "evidence_kind": "local_observation",
            "applicability": {
                "domain": "target microscopy", "task_context": "disentanglement",
                "conditions": {"acquisition": "asynchronous"},
            },
        },
    }
    hypothesis = {
        "id": "hypothesis:transfer", "type": "hypothesis", "title": "transfer", "summary": "", "tags": [],
        "data": {
            "status": "contradicted", "assertion_role": "transfer_proposal",
            "prediction": "Target effect improves.", "test": "Test synchronization intervention against matched baseline.",
            "alternatives": [], "evidence_ids": [source_evidence["id"], target_evidence["id"]],
            "evidence_roles": {source_evidence["id"]: "analogy", target_evidence["id"]: "counterevidence"},
            "applicability": {
                "target_domain": "target microscopy", "task_context": "disentanglement",
                "conditions": {"acquisition": "asynchronous"},
            },
            "transfer_mapping": {"non_equivalences": ["Synchronization was not experimentally isolated."]},
            "unknowns": ["Whether the required synchronization condition is satisfied in target acquisition."],
        },
    }
    store = StubStore([source, source_evidence, target_source, target_evidence, hypothesis], [], {"question": "transfer"})
    context = build_context(store, intent="resume", max_chars=3200, include_quality=False)

    assert "0.42 to 0.27" in context
    assert "0.31 to 0.30" in context and "0.05" in context
    assert "direct target counterevidence (authored)" in context
    assert "Synchronization was not experimentally isolated." in context
    assert "required synchronization condition" in context


def test_cold_resume_does_not_recast_a_contradicted_comparison_as_the_next_action() -> None:
    """A result-updated route must recover an authored unresolved gap, not a new causal claim."""

    source = {
        "id": "source:target-run", "type": "source", "title": "target run", "summary": "", "tags": [],
        "data": {"kind": "supplied_target_run"},
    }
    counterevidence = {
        "id": "evidence:target-negative", "type": "evidence", "title": "negative", "summary": "", "tags": [],
        "data": {
            "source_id": source["id"], "locator": "result",
            "excerpt": "The asynchronous target comparison changed from 0.31 to 0.30.",
            "evidence_kind": "local_observation",
            "applicability": {"domain": "target microscopy", "conditions": {"acquisition": "asynchronous"}},
        },
    }
    hypothesis = {
        "id": "hypothesis:transfer", "type": "hypothesis", "title": "transfer", "summary": "", "tags": [],
        "data": {
            "status": "contradicted", "assertion_role": "transfer_proposal",
            "prediction": "Gated interaction improves target disentanglement.",
            "test": "Compare gated interaction with a matched asynchronous baseline.",
            "evidence_ids": [counterevidence["id"]],
            "evidence_roles": {counterevidence["id"]: "counterevidence"},
            "applicability": {"target_domain": "target microscopy", "conditions": {"acquisition": "asynchronous"}},
            "transfer_mapping": {"non_equivalences": ["Synchronization was not experimentally isolated."]},
            "unknowns": ["Whether synchronization changes the target result remains unresolved."],
        },
    }
    store = StubStore([source, counterevidence, hypothesis], [], {"question": "transfer"})

    context = build_context(store, intent="resume", max_chars=3000, include_quality=False)

    assert "direct target counterevidence (authored)" in context
    assert "stored test (saved; execution not inferred; non-automatic)" in context
    assert "next-route gap (saved, unresolved; replication may be valuable)" in context
    assert "Whether synchronization changes the target result remains unresolved." in context
    assert "Synchronization changes the target result." not in context


def test_resume_recovers_an_enzyme_negative_result_without_inferring_test_execution() -> None:
    source = {"id": "source:enzyme", "type": "source", "title": "enzyme run", "summary": "", "tags": [], "data": {"kind": "local"}}
    negative = {
        "id": "evidence:enzyme-negative", "type": "evidence", "title": "enzyme negative", "summary": "", "tags": [],
        "data": {
            "source_id": source["id"], "locator": "assay result",
            "excerpt": "In the matched enzyme assay, activity changed from 41.0 to 40.8; the predefined meaningful increase was 2.0 units.",
            "evidence_kind": "local_observation",
            "applicability": {"domain": "enzyme assay", "conditions": {"buffer": "B"}},
        },
    }
    hypothesis = {
        "id": "hypothesis:enzyme", "type": "hypothesis", "title": "enzyme", "summary": "", "tags": [],
        "data": {
            "status": "contradicted", "prediction": "The treatment increases enzyme activity.",
            "test": "Compare treated and matched control enzyme activity.", "alternatives": [],
            "evidence_ids": [negative["id"]], "evidence_roles": {negative["id"]: "counterevidence"},
            "applicability": {"domain": "enzyme assay", "conditions": {"buffer": "B"}},
        },
    }
    context = build_context(StubStore([source, negative, hypothesis], [], {"active_hypotheses": [hypothesis["id"]]}),
                            intent="resume", max_chars=2000, include_quality=False)

    assert "direct target counterevidence (authored): evidence:enzyme-negative" in context
    assert "41.0 to 40.8" in context and "2.0 units" in context
    assert "stored test (saved; execution not inferred; non-automatic)" in context
    assert "previously evaluated" not in context


def test_resume_prefers_valid_forecast_inconclusive_evidence_and_keeps_pending_run_pending() -> None:
    invalid_source = {"id": "source:old", "type": "source", "title": "old", "summary": "", "tags": [], "data": {"kind": "local"}}
    valid_source = {"id": "source:valid", "type": "source", "title": "valid", "summary": "", "tags": [], "data": {"kind": "local"}}
    invalid = {
        "id": "evidence:old-invalid", "type": "evidence", "title": "old invalid", "summary": "", "tags": [],
        "data": {
            "source_id": invalid_source["id"], "locator": "random split",
            "excerpt": "The invalid random-split estimate changed MAE from 18 to 14.", "evidence_kind": "local_observation",
            "experiment_id": "experiment:invalid-random-split",
            "applicability": {"domain": "energy demand forecasting", "task_context": "random split", "conditions": {"split": "random"}},
        },
    }
    invalid_experiment = {
        "id": "experiment:invalid-random-split", "type": "experiment", "title": "invalid random split", "summary": "", "tags": [],
        "data": {"status": "invalid", "outcome": "future-derived information"},
    }
    valid = {
        "id": "evidence:valid-one-fold", "type": "evidence", "title": "valid fold", "summary": "", "tags": [],
        "data": {
            "source_id": valid_source["id"], "locator": "chronological fold",
            "excerpt": "Energy MAE changed from 18.0 to 17.8; bootstrap 95% CI [-0.7, 1.1]; predefined meaningful reduction was 1.0 MAE.",
            "evidence_kind": "local_observation",
            "applicability": {
                "domain": "energy demand forecasting", "task_context": "chronological evaluation",
                "conditions": {"split": "rolling-origin", "normalization": "training-only"},
            },
        },
    }
    hypothesis = {
        "id": "hypothesis:forecast", "type": "hypothesis", "title": "forecast", "summary": "", "tags": [],
        "data": {
            "status": "inconclusive", "prediction": "Normalization lowers MAE by at least 1.0.",
            "test": "Run additional chronological folds (NOT YET EXECUTED).", "alternatives": [],
            "evidence_ids": [invalid["id"], valid["id"]],
            "evidence_roles": {invalid["id"]: "context", valid["id"]: "inconclusive"},
            "applicability": {
                "domain": "energy demand forecasting", "task_context": "chronological evaluation",
                "conditions": {"split": "rolling-origin", "normalization": "training-only"},
            },
        },
    }
    completed = {
        "id": "experiment:one-fold", "type": "experiment", "title": "one fold", "summary": "", "tags": [],
        "data": {"hypothesis_id": hypothesis["id"], "status": "completed", "outcome": "inconclusive"},
    }
    pending = {
        "id": "experiment:many-folds", "type": "experiment", "title": "many folds", "summary": "", "tags": [],
        "data": {"hypothesis_id": hypothesis["id"], "status": "planned"},
    }
    store = StubStore([invalid_source, valid_source, invalid, invalid_experiment, valid, hypothesis, completed, pending], [],
                      {"active_hypotheses": [hypothesis["id"]]})
    context = build_context(store, intent="resume", max_chars=2000, include_quality=False)

    assert "direct target inconclusive evidence (authored): evidence:valid-one-fold" in context
    assert "18.0 to 17.8" in context and "[-0.7, 1.1]" in context and "1.0 MAE" in context
    assert "related evidence: evidence:old-invalid" not in context
    assert "context evidence (explicitly invalid measurement; authored role=context): evidence:old-invalid" in context
    assert "changed MAE from 18 to 14" in context
    assert "outcome=future-derived information" in context
    # Evidence cards take precedence at the 2k budget; execution records are
    # retained when they fit, but neither label nor partial execution history
    # is fabricated when they do not.
    assert "linked experiment (saved association): experiment:many-folds | status=planned" in context
    assert "stored test (saved; execution not inferred; non-automatic)" in context
    assert "stored test (completed" not in context and "previously evaluated" not in context
    assert "not a complete/latest evidence set" in context
    assert "Stop: if brief covers" not in context  # 2k reserves space for evidence cards


def test_resume_scope_role_selection_is_id_rename_invariant() -> None:
    def render(support_id: str) -> str:
        source = {"id": "source:scope", "type": "source", "title": "scope", "summary": "", "tags": [], "data": {"kind": "local"}}
        mismatch = {
            "id": "evidence:mismatched-counter", "type": "evidence", "title": "mismatch", "summary": "", "tags": [],
            "data": {
                "source_id": source["id"], "locator": "other task", "excerpt": "Other-domain value was 0.1.",
                "evidence_kind": "local_observation", "applicability": {"domain": "other task", "conditions": {"cohort": "A"}},
            },
        }
        support = {
            "id": support_id, "type": "evidence", "title": "support", "summary": "", "tags": [],
            "data": {
                "source_id": source["id"], "locator": "target assay", "excerpt": "Target value changed from 7.0 to 8.2 with threshold 0.5.",
                "evidence_kind": "local_observation", "applicability": {"domain": "target task", "conditions": {"cohort": "A"}},
            },
        }
        hypothesis = {
            "id": "hypothesis:scope", "type": "hypothesis", "title": "scope", "summary": "", "tags": [],
            "data": {
                "status": "inconclusive", "prediction": "Target value rises.", "test": "Compare target values.", "alternatives": [],
                "evidence_ids": [mismatch["id"], support_id],
                "evidence_roles": {mismatch["id"]: "counterevidence", support_id: "support"},
                "applicability": {"domain": "target task", "conditions": {"cohort": "A"}},
            },
        }
        return build_context(StubStore([source, mismatch, support, hypothesis], [], {"active_hypotheses": [hypothesis["id"]]}),
                             intent="resume", max_chars=3000, include_quality=False)

    first, renamed = render("evidence:alpha"), render("evidence:renamed")
    for context, support_id in ((first, "evidence:alpha"), (renamed, "evidence:renamed")):
        assert f"direct target supporting evidence (authored): {support_id}" in context
        assert "7.0 to 8.2" in context and "threshold 0.5" in context
        assert "direct target counterevidence (authored): evidence:mismatched-counter" not in context
    assert "target observation: Target value changed from 7.0 to 8.2 with threshold 0.5." in first
    assert "target observation: Target value changed from 7.0 to 8.2 with threshold 0.5." in renamed


def test_resume_evidence_cards_keep_third_sentence_qualifier_atomically() -> None:
    source = {"id": "source:qualifier", "type": "source", "title": "run", "summary": "", "tags": [], "data": {}}
    evidence = {
        "id": "evidence:three-sentence", "type": "evidence", "title": "three", "summary": "", "tags": [],
        "data": {
            "source_id": source["id"], "locator": "result p3",
            "excerpt": "The mean changed from 41.0 to 40.8. The interval crossed zero. This result applies only under buffer B.",
            "evidence_kind": "local_observation", "applicability": {"domain": "enzyme", "conditions": {"buffer": "B"}},
        },
    }
    hypothesis = {
        "id": "hypothesis:qualifier", "type": "hypothesis", "title": "h", "summary": "", "tags": [],
        "data": {"status": "contradicted", "evidence_ids": [evidence["id"]],
                 "evidence_roles": {evidence["id"]: "counterevidence"},
                 "applicability": {"domain": "enzyme", "conditions": {"buffer": "B"}}},
    }
    context = build_context(StubStore([source, evidence, hypothesis], [], {"active_hypotheses": [hypothesis["id"]]}),
                            intent="resume", max_chars=2000, include_quality=False)
    assert "The mean changed from 41.0 to 40.8. The interval crossed zero. This result applies only under buffer B." in context
    assert context.count("evidence:three-sentence") >= 1


def test_resume_does_not_select_numeric_or_english_sentences_from_long_multilingual_excerpt() -> None:
    source = {"id": "source:multi", "type": "source", "title": "run", "summary": "", "tags": [], "data": {}}
    excerpt = "中文限定：只适用于缓冲液B。" + " Additional neutral text without a result. " * 35 + "Value 19 appears here."
    evidence = {
        "id": "evidence:multilingual", "type": "evidence", "title": "multi", "summary": "summary 19", "tags": [],
        "data": {"source_id": source["id"], "locator": "appendix", "excerpt": excerpt,
                 "evidence_kind": "local_observation", "applicability": {"domain": "target", "conditions": {}}},
    }
    hypothesis = {
        "id": "hypothesis:multi", "type": "hypothesis", "title": "h", "summary": "", "tags": [],
        "data": {"status": "inconclusive", "evidence_ids": [evidence["id"]],
                 "evidence_roles": {evidence["id"]: "inconclusive"},
                 "applicability": {"domain": "target", "conditions": {}}},
    }
    context = build_context(StubStore([source, evidence, hypothesis], [], {"active_hypotheses": [hypothesis["id"]]}),
                            intent="resume", max_chars=2000, include_quality=False)
    assert "[long excerpt not expanded; inspect `srh show evidence:multilingual`]" in context
    assert "Value 19 appears here." not in context
    assert "summary 19" not in context


def test_resume_cards_are_atomic_when_2000_budget_cannot_fit_qualifications() -> None:
    source = {"id": "source:atomic", "type": "source", "title": "run", "summary": "", "tags": [], "data": {}}
    evidence = {
        "id": "evidence:atomic", "type": "evidence", "title": "atomic", "summary": "", "tags": [],
        "data": {"source_id": source["id"], "locator": "L" * 160,
                 "excerpt": "Observation is qualified by condition C. " * 12,
                 "evidence_kind": "local_observation", "applicability": {"domain": "target", "conditions": {"condition": "C"}}},
    }
    hypothesis = {
        "id": "hypothesis:atomic", "type": "hypothesis", "title": "h", "summary": "", "tags": [],
        "data": {"status": "contradicted", "prediction": "P" * 250, "test": "T" * 250,
                 "evidence_ids": [evidence["id"]], "evidence_roles": {evidence["id"]: "counterevidence"},
                 "applicability": {"domain": "target", "conditions": {"condition": "C"}}},
    }
    context = build_context(StubStore([source, evidence, hypothesis], [], {"active_hypotheses": [hypothesis["id"]]}),
                            intent="resume", max_chars=2000, include_quality=False)
    label = "direct target counterevidence (authored): evidence:atomic"
    if label in context:
        start = context.index(label)
        assert "qualification:" in context[start:]
        assert "target observation:" in context[start:]
    else:
        assert "some recovery fields did not fit" in context


def test_resume_shows_at_most_two_other_cards_and_never_repeats_source_card() -> None:
    source = {"id": "source:report", "type": "source", "title": "source", "summary": "", "tags": [], "data": {}}
    source_report = {
        "id": "evidence:source-report", "type": "evidence", "title": "source", "summary": "", "tags": [],
        "data": {"source_id": source["id"], "locator": "source result", "excerpt": "Source result was reported.",
                 "evidence_kind": "author_report", "applicability": {"domain": "source", "conditions": {}}},
    }
    direct = {
        "id": "evidence:direct", "type": "evidence", "title": "direct", "summary": "", "tags": [],
        "data": {"locator": "target", "excerpt": "Target observation is inconclusive.",
                 "evidence_kind": "local_observation", "applicability": {"domain": "target", "conditions": {}}},
    }
    others = [
        {"id": f"evidence:other-{index}", "type": "evidence", "title": "other", "summary": "", "tags": [],
         "data": {"locator": f"other {index}", "excerpt": f"Other {index}.",
                  "evidence_kind": "observation", "applicability": {"domain": "other", "conditions": {}}}}
        for index in range(3)
    ]
    hypothesis = {
        "id": "hypothesis:cards", "type": "hypothesis", "title": "cards", "summary": "", "tags": [],
        "data": {
            "status": "inconclusive", "evidence_ids": [source_report["id"], direct["id"], *(item["id"] for item in others)],
            "evidence_roles": {source_report["id"]: "analogy", direct["id"]: "inconclusive",
                               others[0]["id"]: "context", others[1]["id"]: "support", others[2]["id"]: "context"},
            "applicability": {"domain": "target", "conditions": {}},
        },
    }
    context = build_context(StubStore([source, source_report, direct, *others, hypothesis], [],
                                       {"active_hypotheses": [hypothesis["id"]]}),
                            intent="resume", max_chars=6000, include_quality=False)
    assert context.count("evidence:source-report") == 1
    assert context.count("related evidence (authored role=") + context.count("context evidence (authored role=") <= 2
    assert "other related evidence selection: 1 saved item(s) omitted" in context


def test_resume_keeps_saved_unknowns_beside_unresolved_pointers_without_repeating_gap() -> None:
    direct = {
        "id": "evidence:negative", "type": "evidence", "title": "negative", "summary": "", "tags": [],
        "data": {"locator": "target", "excerpt": "Target result was null.", "evidence_kind": "local_observation",
                 "applicability": {"domain": "target", "conditions": {}}},
    }
    unknowns = ["Check buffer B.", "Check batch C.", "Check day D.", "Check operator E.", "Check instrument F."]
    hypothesis = {
        "id": "hypothesis:unknowns", "type": "hypothesis", "title": "unknowns", "summary": "", "tags": [],
        "data": {"status": "contradicted", "evidence_ids": [direct["id"]],
                 "evidence_roles": {direct["id"]: "counterevidence"},
                 "applicability": {"domain": "target", "conditions": {}},
                 "unknowns": unknowns, "alternatives": ["Alternative A.", "Alternative A.", "Alternative B."]},
    }
    context = build_context(StubStore([direct, hypothesis], [], {
        "active_hypotheses": [hypothesis["id"]], "unresolved": ["decision:pending"],
    }), intent="resume", max_chars=6000, include_quality=False)
    assert "uncertainty (saved unresolved): decision:pending" in context
    assert context.count("Check buffer B.") == 1
    assert "Check batch C." in context and "Check day D." in context
    assert "1 additional saved unknowns item(s) omitted" in context
    assert context.count("Alternative A.") == 1 and "Alternative B." in context


def test_resume_keeps_a_realistic_complete_scope_at_default_budget() -> None:
    scope = {
        "domain": "deployment energy forecasting",
        "task_context": "chronological rolling-origin preprocessing with training-only normalization",
        "conditions": {
            "metric": "MAE", "split": "rolling-origin", "normalization": "training-only per-series",
            "horizon": "one week", "cohort": "regional demand series",
        },
    }
    evidence = {
        "id": "evidence:complete-scope", "type": "evidence", "title": "scope", "summary": "", "tags": [],
        "data": {"locator": "fold one", "excerpt": "The result was inconclusive.",
                 "evidence_kind": "local_observation", "applicability": scope},
    }
    hypothesis = {
        "id": "hypothesis:complete-scope", "type": "hypothesis", "title": "scope", "summary": "", "tags": [],
        "data": {"status": "inconclusive", "evidence_ids": [evidence["id"]],
                 "evidence_roles": {evidence["id"]: "inconclusive"}, "applicability": scope},
    }
    context = build_context(StubStore([evidence, hypothesis], [], {"active_hypotheses": [hypothesis["id"]]}),
                            intent="resume", max_chars=6000, include_quality=False)
    assert "chronological rolling-origin preprocessing with training-only normalization" in context
    assert "'normalization': 'training-only per-series'" in context
    assert "[long applicability omitted; inspect `srh show evidence:complete-scope`]" not in context


def test_explicit_query_is_not_displaced_by_many_historic_active_hypotheses() -> None:
    records = [
        {"id": f"hypothesis:old-{index:02d}", "type": "hypothesis", "title": "old", "summary": "", "tags": [],
         "data": {"status": "active", "prediction": "historic", "alternatives": [], "test": "old"}}
        for index in range(20)
    ]
    records.append({"id": "paper:query-hit", "type": "paper", "title": "needle", "summary": "needle target source", "tags": [],
                    "data": {"read_scope": "abstract"}})
    store = StubStore(records, [], {"active_hypotheses": [record["id"] for record in records[:-1]]})
    context = build_context(store, query="needle", intent="map", max_chars=4000, include_quality=False)
    assert "paper:query-hit" in context


def test_query_pack_order_preserves_lexical_relevance_order() -> None:
    first = {"id": "hypothesis:z-first", "type": "hypothesis", "title": "first", "summary": "", "tags": [],
             "data": {"status": "proposed", "prediction": "needle first result"}}
    second = {"id": "hypothesis:a-second", "type": "hypothesis", "title": "second", "summary": "", "tags": [],
              "data": {"status": "proposed", "prediction": "needle second result"}}
    store = StubStore([first, second], [], {})
    context = build_context(store, query="needle", intent="map", max_chars=6000, include_quality=False)
    assert context.index("hypothesis:z-first") < context.index("hypothesis:a-second")


def test_complete_packs_keep_experiment_reproducibility_and_read_provenance() -> None:
    store = _fixture()
    experiment = {
        "id": "experiment:complete", "type": "experiment", "title": "complete", "summary": "", "tags": [],
        "data": {
            "hypothesis_id": "hypothesis:focus", "status": "planned", "command": "python run.py --seed 7",
            "budget": "ten minutes", "config": {"condition": "stable"}, "seed": 7,
            "code_ref": "run.py", "data_ref": "synthetic.csv",
        },
    }
    claim = {
        "id": "claim:read", "type": "claim", "title": "read", "summary": "", "tags": [],
        "data": {"origin": "author", "status": "reported", "evidence_ids": ["evidence:positive"]},
    }
    store.records[experiment["id"]] = experiment
    store.records[claim["id"]] = claim
    store.records["paper:abstract"]["data"]["read_sections"] = ["abstract", "methods"]
    experiment_context = build_context(store, intent="experiment", focus=[experiment["id"]], max_chars=15000, include_quality=False)
    assert "command: python run.py --seed 7" in experiment_context
    assert "budget: ten minutes" in experiment_context
    assert "config: {'condition': 'stable'}" in experiment_context
    assert "seed: 7" in experiment_context
    assert "code_ref: run.py" in experiment_context
    assert "data_ref: synthetic.csv" in experiment_context
    read_context = build_context(store, intent="read", focus=[claim["id"]], max_chars=15000, include_quality=False)
    assert "origin: author" in read_context
    assert "read_sections: abstract; methods" in read_context
