from copy import deepcopy
import json
from pathlib import Path

import pytest

from research_harness.cli import main
from research_harness.project import (
    _WRITEBACK_RECEIPT_CHECKPOINT_SKILL, ProjectError, init_project, install_templates,
)
from research_harness.store import ResearchStore


def decision(item_id):
    return {"id": item_id, "type": "decision", "title": item_id, "summary": "", "tags": [],
            "data": {"reason": "Retain the conditional prediction while revising the plan.",
                     "next_action": "Design a discriminating experiment.", "evidence_ids": []}}


def delta():
    return {"schema_version": 1, "records": [decision("decision:old"), decision("decision:new")],
            "edges": [{"id": "edge:revision", "source": "decision:new", "target": "decision:old",
                       "relation": "revises", "status": "structural", "evidence_ids": [],
                       "conditions": {"changed": "Measurement qualification.", "retained": "Conditional prediction."}}]}


def test_receipt_lists_only_the_successfully_submitted_links_after_a_rejected_link_is_removed(tmp_path):
    request = delta()
    rejected = deepcopy(request)
    rejected["edges"].append({**request["edges"][0], "id": "edge:not-saved", "relation": "limits"})
    with ResearchStore(tmp_path, create=True) as store:
        assert store.check_delta(rejected)["valid"] is False
        assert store.list_records() == []
        result = store.apply_delta(request)
        receipt = result["writeback"]
        assert receipt["status"] == "committed"
        assert receipt["record_count"] == 2
        assert receipt["edge_count"] == 1
        assert receipt["truncated"] is False
        assert receipt["edges"] == [{"id": "edge:revision", "source": "decision:new", "relation": "revises",
                                      "target": "decision:old", "status": "structural", "operation": "inserted"}]
        assert "edge:not-saved" not in json.dumps(receipt)
        for item in receipt["records"]:
            assert store.get_record(item["id"])["type"] == item["type"]
        assert {e["id"] for e in store.export_bundle()["edges"]} == {e["id"] for e in receipt["edges"]}


def test_preview_and_replay_never_claim_a_check_committed_changes(tmp_path):
    request = {**delta(), "delta_id": "one-commit"}
    with ResearchStore(tmp_path, create=True) as store:
        before = store.export_bundle()
        preview = store.check_delta(request)
        assert preview["would_apply"]["writeback"]["status"] == "preview"
        assert store.export_bundle() == before
        committed = store.apply_delta(request)
        assert committed["writeback"]["status"] == "committed"
        after, events = store.export_bundle(), store.events(100)
        checked_replay = store.check_delta(request)
        assert checked_replay["would_apply"]["replayed"] is True
        assert checked_replay["would_apply"]["writeback"]["status"] == "preview"
        replay = store.apply_delta(request)
        assert replay == {**committed, "replayed": True}
        assert store.export_bundle() == after
        assert store.events(100) == events


def test_updated_and_unchanged_receipts_describe_stored_operations_not_new_scientific_confirmation(tmp_path):
    with ResearchStore(tmp_path, create=True) as store:
        request = delta()
        store.apply_delta(request)
        unchanged = store.apply_delta(request)
        assert {r["operation"] for r in unchanged["writeback"]["records"]} == {"unchanged"}
        assert unchanged["writeback"]["edges"][0]["operation"] == "unchanged"
        request["records"][1]["title"] = "A revised plan title"
        updated = store.apply_delta(request)
        assert {r["id"]: r["operation"] for r in updated["writeback"]["records"]} == {
            "decision:old": "unchanged", "decision:new": "updated"}
        # Legacy replay events remain readable without fabricating a receipt
        # that was never part of their original transaction.
        old = {**delta(), "delta_id": "legacy"}
        result = store.apply_delta(old)
        result.pop("writeback")
        event = store.conn.execute("SELECT seq,payload_json FROM events WHERE event_type='delta.applied' AND entity_id='legacy'").fetchone()
        payload = json.loads(event["payload_json"])
        payload["result"] = result
        store.conn.execute("UPDATE events SET payload_json=? WHERE seq=?", (json.dumps(payload), event["seq"]))
        store.conn.commit()
        assert store.apply_delta(old) == {**result, "replayed": True}


def test_large_receipt_is_bounded_and_explicitly_incomplete(tmp_path):
    records = [{"id": f"source:{i}", "type": "source", "title": "private passage", "summary": "do not echo",
                "tags": [], "data": {"kind": "note"}} for i in range(70)]
    with ResearchStore(tmp_path, create=True) as store:
        result = store.apply_delta({"schema_version": 1, "records": records, "edges": []})
        receipt = result["writeback"]
        assert receipt["truncated"] is True
        assert receipt["record_count"] == 70
        assert len(receipt["records"]) == 64
        assert len(store.list_records()) == 70
        assert "private passage" not in json.dumps(receipt)
        assert "do not echo" not in json.dumps(receipt)


def test_rollback_after_writes_has_no_durable_receipt_or_partial_memory(tmp_path, monkeypatch):
    with ResearchStore(tmp_path, create=True) as store:
        before = store.export_bundle()
        original = store._write_entities

        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected failure after entity writes")

        monkeypatch.setattr(store, "_write_entities", fail_after_write)
        with pytest.raises(RuntimeError, match="injected failure"):
            store.apply_delta({**delta(), "delta_id": "not-committed"})
        assert store.export_bundle() == before
        assert not store.events(100)


def test_cli_shows_commit_receipt_without_an_additional_read(tmp_path, capsys):
    init_project(tmp_path)
    path = tmp_path / "delta.json"
    path.write_text(json.dumps(delta()), encoding="utf-8")
    assert main(["--project", str(tmp_path), "remember", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["writeback"]["status"] == "committed"
    assert result["writeback"]["edges"][0]["target"] == "decision:old"


def test_receipt_skill_predecessor_upgrade_keeps_user_edits(tmp_path):
    init_project(tmp_path)
    path = tmp_path / ".agents/skills/srh-checkpoint/SKILL.md"
    current = path.read_text(encoding="utf-8")
    assert _WRITEBACK_RECEIPT_CHECKPOINT_SKILL in current
    old = current.replace(_WRITEBACK_RECEIPT_CHECKPOINT_SKILL, "")
    path.write_text(old, encoding="utf-8")
    install_templates(tmp_path)
    assert path.read_text(encoding="utf-8") == current
    custom = old + "\nUser-authored research policy.\n"
    path.write_text(custom, encoding="utf-8")
    with pytest.raises(ProjectError, match="conflicting existing skill"):
        install_templates(tmp_path)
    assert path.read_text(encoding="utf-8") == custom
