"""CLI contract checks for explicit, bounded batched record lookup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_harness.cli import SHOW_MAX_UNIQUE_IDS, main
from research_harness.project import init_project
from research_harness.store import ResearchStore


FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "evidence-bundle.json"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    init_project(root)
    with ResearchStore(root) as store:
        store.ingest(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return root


def _show(project: Path, *ids: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, object]:
    status = main(["--project", str(project), "show", *ids])
    captured = capsys.readouterr()
    return status, json.loads(captured.out) if captured.out else captured.err


def test_show_single_id_remains_a_record_object_and_missing_still_fails(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, value = _show(project, "paper:synthetic-source", capsys=capsys)
    assert status == 0
    assert isinstance(value, dict)
    assert value["id"] == "paper:synthetic-source"
    assert "records" not in value

    status, error = _show(project, "missing:only-one", capsys=capsys)
    assert status == 2
    assert "record not found" in error


def test_show_batch_preserves_unique_request_order_and_reports_missing(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with ResearchStore(project) as store:
        before = store.export_bundle()

    status, value = _show(
        project,
        "evidence:opposing-finding", "missing:first", "paper:synthetic-source",
        "evidence:opposing-finding", "missing:second",
        capsys=capsys,
    )
    assert status == 0
    assert set(value) == {"records", "missing_ids"}
    assert [record["id"] for record in value["records"]] == [
        "evidence:opposing-finding", "paper:synthetic-source"
    ]
    assert value["missing_ids"] == ["missing:first", "missing:second"]

    with ResearchStore(project) as store:
        assert store.export_bundle() == before


def test_show_batch_repeated_single_id_and_all_missing_keep_batch_envelope(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, value = _show(
        project, "paper:synthetic-source", "paper:synthetic-source", capsys=capsys
    )
    assert status == 0
    assert [record["id"] for record in value["records"]] == ["paper:synthetic-source"]
    assert value["missing_ids"] == []

    status, value = _show(
        project, "missing:second", "missing:first", capsys=capsys
    )
    assert status == 0
    assert value == {
        "records": [],
        "missing_ids": ["missing:second", "missing:first"],
    }


def test_show_batch_rejects_too_many_unique_ids_without_changing_data(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with ResearchStore(project) as store:
        before = store.export_bundle()

    ids = [f"missing:{index}" for index in range(SHOW_MAX_UNIQUE_IDS + 1)]
    status, error = _show(project, *ids, capsys=capsys)
    assert status == 2
    assert f"at most {SHOW_MAX_UNIQUE_IDS} unique" in error

    with ResearchStore(project) as store:
        assert store.export_bundle() == before
