"""Mechanical forecast/update receipts keep transfer sensitivities separate."""
import hashlib
import json
from pathlib import Path

import pytest

from research_harness.cli import (
    FORECAST_UPDATE_FORMAT,
    _commit_forecast_update,
    _forecast_update_receipt,
    _forecast_update_template,
)
from research_harness.project import init_project, ProjectError


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt(root: Path, *, matched: bool = False, kind: str = "conditional_sensitivity") -> dict:
    baseline = root / "forecast-before.json"
    target = root / "forecast-after.json"
    sensitivity = root / "forecast-sensitivity.json"
    return {
        "format": FORECAST_UPDATE_FORMAT,
        "id": "forecast-update:test-1",
        "baseline": {"path": "forecast-before.json", "sha256": _sha(baseline)},
        "target": {"path": "forecast-after.json", "sha256": _sha(target)},
        "evidence": {
            "origin": "new_target_observation" if matched else "historical_source_report",
            "estimand": "target terminal mean",
            "source_scope": "target additives 9/10",
            "target_scope": "target additives 13/23",
            "estimand_match": matched,
        },
        "update": {
            "kind": kind,
            "assumptions": [] if kind != "conditional_sensitivity" else ["additive response transfers"],
            "failure_risks": [] if kind != "conditional_sensitivity" else ["terminal interaction differs"],
            "sensitivity_path": "forecast-sensitivity.json" if kind == "conditional_sensitivity" else None,
            "sensitivity_sha256": _sha(sensitivity) if kind == "conditional_sensitivity" else None,
        },
        "decision_use": "baseline" if matched else "defer",
    }


def _project(tmp_path: Path) -> Path:
    init_project(tmp_path, hooks=False)
    (tmp_path / "forecast-before.json").write_text('{"prediction": 1}\n', encoding="utf-8")
    (tmp_path / "forecast-after.json").write_text('{"prediction": 1}\n', encoding="utf-8")
    (tmp_path / "forecast-sensitivity.json").write_text('{"prediction": 3}\n', encoding="utf-8")
    return tmp_path


def test_template_is_explicit_and_does_not_read_project():
    template = _forecast_update_template()
    assert template["format"] == FORECAST_UPDATE_FORMAT
    assert template["evidence"]["estimand_match"] is False
    assert template["update"]["kind"] == "conditional_sensitivity"


def test_conditional_receipt_requires_unchanged_target_and_separate_sensitivity(tmp_path: Path):
    root = _project(tmp_path)
    receipt = _forecast_update_receipt(root, _receipt(root))
    committed = _commit_forecast_update(root, receipt)
    saved = Path(committed["receipt"])
    assert saved.is_file()
    assert json.loads(saved.read_text(encoding="utf-8"))["update"]["kind"] == "conditional_sensitivity"
    with pytest.raises(ProjectError, match="already exists"):
        _commit_forecast_update(root, receipt)


def test_mismatched_point_replacement_is_rejected(tmp_path: Path):
    root = _project(tmp_path)
    (root / "forecast-after.json").write_text('{"prediction": 2}\n', encoding="utf-8")
    with pytest.raises(ProjectError, match="cannot replace"):
        _forecast_update_receipt(root, _receipt(root))


def test_direct_matching_observation_may_change_target_artifact(tmp_path: Path):
    root = _project(tmp_path)
    (root / "forecast-after.json").write_text('{"prediction": 2}\n', encoding="utf-8")
    receipt = _receipt(root, matched=True, kind="direct_target_observation")
    normalized = _forecast_update_receipt(root, receipt)
    assert normalized["update"]["kind"] == "direct_target_observation"


def test_direct_update_rejects_unmatched_evidence(tmp_path: Path):
    root = _project(tmp_path)
    (root / "forecast-after.json").write_text('{"prediction": 2}\n', encoding="utf-8")
    receipt = _receipt(root, matched=False, kind="direct_target_observation")
    with pytest.raises(ProjectError, match="matching target observation"):
        _forecast_update_receipt(root, receipt)
