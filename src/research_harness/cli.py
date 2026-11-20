"""The ``srh`` command line interface."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .context import INTENTS, context_for_project
from .hooks import run as run_hook
from .models import SCHEMA_VERSION, content_hash, normalize_record
from .semantics import is_qualified_assertion_evidence
from .project import ProjectError, _atomic_write_project, find_project, init_project


# This is deliberately an input-format marker, not a second memory schema.
# ``remember --compact`` expands it to an ordinary schema-v1 delta and then
# calls the exact same strict store path as ``remember FILE``.  Keeping the
# marker separate from ``schema_version`` makes it impossible to mistake a
# compact convenience envelope for a new version of the durable graph.
COMPACT_REMEMBER_FORMAT = "srh.remember.compact.v1"
# Unlike the general compact form, this is deliberately a narrow update
# envelope.  It lets a session attach one newly inspected result to one saved
# hypothesis without copying the hypothesis (and accidentally dropping its
# transfer mapping, uncertainty, or review metadata) into a new JSON file.
RESULT_UPDATE_REMEMBER_FORMAT = "srh.remember.result-update.v1"
FORECAST_UPDATE_FORMAT = "srh.forecast.update.v1"
_COMPACT_CHECK = object()
SHOW_MAX_UNIQUE_IDS = 12


def _json_dump(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="srh", description="Scientific Research Harness")
    parser.add_argument("--project", type=Path, help="research project directory (default: nearest .research)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="initialize a research project")
    p.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    p.add_argument("--question")
    p.add_argument("--hooks", action="store_true", help="install repo-scoped hooks (review /hooks trust settings)")

    p = sub.add_parser("ingest", help="ingest a JSON bundle")
    p.add_argument("file", type=str)
    p = sub.add_parser("show", help="show one record, or a small explicit batch")
    p.add_argument(
        "ids", nargs="+", metavar="ID",
        help=f"one or more record IDs (at most {SHOW_MAX_UNIQUE_IDS} unique IDs)",
    )
    p = sub.add_parser("list", help="list records")
    p.add_argument("--type")
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("search", help="search records")
    p.add_argument("query")
    p.add_argument("--mode", choices=("lexical", "vector", "hybrid"), default="hybrid")
    p.add_argument("--type")
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("graph", help="inspect graph neighbourhood")
    p.add_argument("id", nargs="?")
    p.add_argument("--hops", type=int, default=1)
    p.add_argument("--limit", type=int, default=80)
    p = sub.add_parser("index", help="explicitly build the vector index")
    p.add_argument("--provider", choices=("fastembed", "hash"))
    p.add_argument("--model")
    p = sub.add_parser("state", help="read or patch research state")
    p.add_argument("--file", type=Path)
    p.add_argument("--hash", action="store_true", help="include the current state hash")
    p.add_argument("--expected-hash", help="require this state hash when applying --file")
    p = sub.add_parser("context", help="emit bounded context for Codex")
    p.add_argument("--query")
    p.add_argument("--max-chars", type=int, default=6000)
    p.add_argument("--intent", choices=INTENTS, default="resume", help="explicit work intent; Codex chooses it")
    p.add_argument("--focus", action="append", default=[], metavar="ID", help="prioritize this record ID (repeatable)")
    p.add_argument("--with-details", action="store_true", default=None,
                   help="include graph evidence bundles for an otherwise summary-only resume")
    p.add_argument("--with-quality", action="store_true", default=None,
                   help="run bounded quality checks for an otherwise summary-only resume")
    p = sub.add_parser("quality", help="report bounded mechanical/advisory data-quality findings")
    p.add_argument("--focus", metavar="ID")
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("remember", help="apply a reviewed research delta JSON file")
    p.add_argument("file", nargs="?", type=str, help="delta JSON file, or - for stdin")
    p.add_argument(
        "--check", metavar="FILE", nargs="?", const=_COMPACT_CHECK,
        help="validate FILE without writing; with --compact FILE, validate that compact input",
    )
    p.add_argument(
        "--compact", metavar="FILE", type=str,
        help=("apply compact envelope FILE (format srh.remember.compact.v1); "
              "only fills schema_version, edges, record summary, and record tags"),
    )
    p.add_argument(
        "--evidence", metavar="FILE", type=str,
        help="save one source-located evidence unit; no hypothesis, status, or state inference",
    )
    p.add_argument(
        "--compact-template", choices=("source-evidence", "scoped-claim", "transfer-hypothesis", "decision-revision"), metavar="KIND",
        help=("print a generic compact JSON scaffold without reading or writing a project; "
              "replace its placeholders, then use --compact FILE --check"),
    )
    p.add_argument(
        "--result-update", metavar="FILE", type=str,
        help=("apply a narrow result-evidence/hypothesis-status envelope "
              f"(format {RESULT_UPDATE_REMEMBER_FORMAT}); preserves the saved hypothesis fields"),
    )
    p.add_argument(
        "--result-update-template", metavar="HYPOTHESIS_ID",
        help=("print a project-aware result-update scaffold for this existing hypothesis; "
              "fill it, then use --result-update FILE --check"),
    )
    p = sub.add_parser(
        "forecast-update",
        help="validate or commit a forecast/update receipt without replacing forecast artifacts",
    )
    p.add_argument("file", nargs="?", type=str, help="forecast update receipt JSON")
    p.add_argument(
        "--check", metavar="FILE", type=str,
        help="validate a forecast update receipt without writing it",
    )
    p.add_argument(
        "--template", action="store_true",
        help="print a generic forecast update receipt scaffold",
    )
    p = sub.add_parser("checkpoint", help="write an inspectable export snapshot")
    p.add_argument("--note")
    p = sub.add_parser("doctor", help="read-only integration status")
    sub.add_parser("export", help="export the complete bundle")
    sub.choices["export"].add_argument("file", nargs="?", type=Path)
    p = sub.add_parser("hook", help="trusted hook entrypoint")
    p.add_argument("event", choices=("session-start", "stop"))
    p.add_argument("--project", dest="hook_project", type=Path)
    return parser


def _project_for(args: argparse.Namespace) -> Path:
    if args.command == "init":
        return args.path.expanduser().resolve()
    explicit = getattr(args, "hook_project", None) or args.project
    return (explicit.expanduser().resolve() if explicit else find_project()).resolve()


def _read_json_file(path: str) -> dict[str, Any]:
    try:
        if path == "-":
            value = json.load(sys.stdin)
        else:
            with open(path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"cannot read JSON bundle {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectError("ingest input must be a JSON object")
    return value


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORECAST_UPDATE_ORIGINS = {
    "historical_source_report", "verified_reanalysis", "new_target_observation", "forecast",
}
_FORECAST_UPDATE_KINDS = {"direct_target_observation", "conditional_sensitivity", "status_only"}
_FORECAST_DECISION_USES = {"baseline", "sensitivity_robustness", "defer"}


def _forecast_update_template() -> dict[str, Any]:
    """Return a fail-closed receipt scaffold; it never reads a project."""

    return {
        "format": FORECAST_UPDATE_FORMAT,
        "id": "forecast-update:__SRH_TEMPLATE_ID__",
        "baseline": {"path": "forecast-before.json", "sha256": "__SRH_TEMPLATE_BASELINE_SHA256__"},
        "target": {"path": "forecast-before.json", "sha256": "__SRH_TEMPLATE_TARGET_SHA256__"},
        "evidence": {
            "origin": "historical_source_report",
            "estimand": "__SRH_TEMPLATE_ESTIMAND__",
            "source_scope": "__SRH_TEMPLATE_SOURCE_SCOPE__",
            "target_scope": "__SRH_TEMPLATE_TARGET_SCOPE__",
            "estimand_match": False,
        },
        "update": {
            "kind": "conditional_sensitivity",
            "assumptions": ["__SRH_TEMPLATE_TRANSFER_ASSUMPTION__"],
            "failure_risks": ["__SRH_TEMPLATE_TRANSFER_FAILURE_RISK__"],
            "sensitivity_path": "forecast-sensitivity.json",
            "sensitivity_sha256": "__SRH_TEMPLATE_SENSITIVITY_SHA256__",
        },
        "decision_use": "defer",
    }


def _forecast_update_path(root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ProjectError(f"forecast-update.{field} must be a non-empty project-relative path")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ProjectError(f"forecast-update.{field} must stay inside the project")
    try:
        resolved = (root / candidate).resolve()
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ProjectError(f"forecast-update.{field} escapes the project") from exc
    if not resolved.is_file():
        raise ProjectError(f"forecast-update.{field} does not exist: {value}")
    return resolved


def _forecast_update_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProjectError(f"forecast-update.{field} must be a lowercase SHA-256 digest")
    return value


def _forecast_update_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectError(f"forecast-update.{field} must be a non-empty string")
    return value


def _forecast_update_list(value: Any, field: str, *, required: bool) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ProjectError(f"forecast-update.{field} must be a list of non-empty strings")
    if required and not value:
        raise ProjectError(f"forecast-update.{field} must not be empty")
    return list(value)


def _forecast_update_receipt(root: Path, value: dict[str, Any]) -> dict[str, Any]:
    expected = {"format", "id", "baseline", "target", "evidence", "update", "decision_use"}
    if set(value) != expected:
        raise ProjectError(
            "forecast-update receipt must contain exactly format, id, baseline, target, evidence, update, decision_use"
        )
    if value.get("format") != FORECAST_UPDATE_FORMAT:
        raise ProjectError(f"forecast-update.format must be {FORECAST_UPDATE_FORMAT!r}")
    update_id = _forecast_update_text(value.get("id"), "id")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", update_id):
        raise ProjectError("forecast-update.id contains unsupported filename characters")
    baseline = value["baseline"]
    target = value["target"]
    evidence = value["evidence"]
    update = value["update"]
    if not isinstance(baseline, dict) or set(baseline) != {"path", "sha256"}:
        raise ProjectError("forecast-update.baseline must contain only path and sha256")
    if not isinstance(target, dict) or set(target) != {"path", "sha256"}:
        raise ProjectError("forecast-update.target must contain only path and sha256")
    if not isinstance(evidence, dict) or set(evidence) != {"origin", "estimand", "source_scope", "target_scope", "estimand_match"}:
        raise ProjectError("forecast-update.evidence has an invalid shape")
    if not isinstance(update, dict) or set(update) != {"kind", "assumptions", "failure_risks", "sensitivity_path", "sensitivity_sha256"}:
        raise ProjectError("forecast-update.update has an invalid shape")
    decision_use = value.get("decision_use")
    if decision_use not in _FORECAST_DECISION_USES:
        raise ProjectError("forecast-update.decision_use must be baseline, sensitivity_robustness, or defer")
    baseline_path = _forecast_update_path(root, baseline.get("path"), "baseline.path")
    target_path = _forecast_update_path(root, target.get("path"), "target.path")
    baseline_hash = _forecast_update_hash(baseline.get("sha256"), "baseline.sha256")
    target_hash = _forecast_update_hash(target.get("sha256"), "target.sha256")
    if hashlib.sha256(baseline_path.read_bytes()).hexdigest() != baseline_hash:
        raise ProjectError("forecast-update.baseline.sha256 does not match the saved artifact")
    if hashlib.sha256(target_path.read_bytes()).hexdigest() != target_hash:
        raise ProjectError("forecast-update.target.sha256 does not match the saved artifact")
    origin = evidence.get("origin")
    if origin not in _FORECAST_UPDATE_ORIGINS:
        raise ProjectError("forecast-update.evidence.origin is not recognized")
    _forecast_update_text(evidence.get("estimand"), "evidence.estimand")
    _forecast_update_text(evidence.get("source_scope"), "evidence.source_scope")
    _forecast_update_text(evidence.get("target_scope"), "evidence.target_scope")
    if type(evidence.get("estimand_match")) is not bool:
        raise ProjectError("forecast-update.evidence.estimand_match must be boolean")
    kind = update.get("kind")
    if kind not in _FORECAST_UPDATE_KINDS:
        raise ProjectError("forecast-update.update.kind is not recognized")
    assumptions = _forecast_update_list(update.get("assumptions"), "update.assumptions", required=kind == "conditional_sensitivity")
    risks = _forecast_update_list(update.get("failure_risks"), "update.failure_risks", required=kind == "conditional_sensitivity")
    sensitivity_path_value = update.get("sensitivity_path")
    sensitivity_hash_value = update.get("sensitivity_sha256")
    if kind == "direct_target_observation":
        if evidence.get("estimand_match") is not True or origin not in {"new_target_observation", "verified_reanalysis"}:
            raise ProjectError("direct target updates require a matching target observation or verified reanalysis")
        if sensitivity_path_value is not None or sensitivity_hash_value is not None:
            raise ProjectError("direct target updates cannot carry a sensitivity artifact")
    elif kind == "conditional_sensitivity":
        if evidence.get("estimand_match") is True:
            raise ProjectError("conditional sensitivity is for a non-matching or unresolved estimand")
        if target_hash != baseline_hash:
            raise ProjectError("mismatched estimand cannot replace the target point forecast; target must equal baseline")
        sensitivity_path = _forecast_update_path(root, sensitivity_path_value, "update.sensitivity_path")
        sensitivity_hash = _forecast_update_hash(sensitivity_hash_value, "update.sensitivity_sha256")
        if sensitivity_path == target_path or sensitivity_path == baseline_path:
            raise ProjectError("sensitivity artifact must be separate from baseline and target")
        if hashlib.sha256(sensitivity_path.read_bytes()).hexdigest() != sensitivity_hash:
            raise ProjectError("forecast-update.sensitivity_sha256 does not match the saved artifact")
    else:
        if target_hash != baseline_hash or sensitivity_path_value is not None or sensitivity_hash_value is not None:
            raise ProjectError("status-only update must preserve the baseline and have no sensitivity artifact")
    return {
        "format": FORECAST_UPDATE_FORMAT,
        "id": update_id,
        "baseline": {"path": str(baseline_path.relative_to(root)).replace("\\", "/"), "sha256": baseline_hash},
        "target": {"path": str(target_path.relative_to(root)).replace("\\", "/"), "sha256": target_hash},
        "evidence": dict(evidence),
        "update": {"kind": kind, "assumptions": assumptions, "failure_risks": risks,
                    "sensitivity_path": (str(_forecast_update_path(root, sensitivity_path_value, "update.sensitivity_path").relative_to(root)).replace("\\", "/")
                                          if kind == "conditional_sensitivity" else None),
                    "sensitivity_sha256": (sensitivity_hash_value if kind == "conditional_sensitivity" else None)},
        "decision_use": decision_use,
    }


def _commit_forecast_update(root: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", receipt["id"]).strip("._") or "update"
    relative = Path(".research/forecast-updates") / f"{slug}.json"
    destination = root / relative
    if destination.exists() or destination.is_symlink():
        raise ProjectError(f"forecast update receipt already exists: {relative}")
    payload = (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_project(root, relative, payload)
    return {"receipt": str(destination), "id": receipt["id"], "kind": receipt["update"]["kind"], "decision_use": receipt["decision_use"]}


def _expand_compact_remember(envelope: dict[str, Any]) -> dict[str, Any]:
    """Expand a compact writeback envelope into the canonical raw delta shape.

    Compact input is intentionally syntactic convenience, not an inference
    layer.  A compact envelope needs its explicit format marker; an already
    complete raw schema-v1 delta is accepted unchanged, so a mistaken
    ``--compact`` flag does not block otherwise valid research memory. It supplies the four
    harmless empty structural values which make small, closed memory units
    verbose in raw JSON.  In particular, it never supplies a source anchor,
    provenance role, epistemic status, applicability, relation, experiment
    outcome, state patch, or any other scientific meaning.
    """

    marker = envelope.get("format")
    if marker != COMPACT_REMEMBER_FORMAT:
        if marker is None:
            if envelope.get("schema_version") == SCHEMA_VERSION and isinstance(envelope.get("records"), list) and isinstance(envelope.get("edges"), list):
                # No scientific field is inferred or relaxed: the ordinary
                # strict delta validator still sees the original raw object.
                return envelope
            raise ProjectError(
                f"compact input requires format {COMPACT_REMEMBER_FORMAT!r}; "
                "for a raw schema-v1 delta use `remember FILE` (or `remember --check FILE`)"
            )
        raise ProjectError(
            f"compact input format must be {COMPACT_REMEMBER_FORMAT!r}"
        )
    if "records" not in envelope:
        raise ProjectError("compact.records is required")
    records = envelope["records"]
    if not isinstance(records, list):
        # This is a structural property of the compact envelope.  The ordinary
        # normalizer will still validate every record/data field after expansion.
        raise ProjectError("compact.records must be an array")

    expanded: dict[str, Any] = {
        key: value for key, value in envelope.items() if key != "format"
    }
    expanded.setdefault("schema_version", SCHEMA_VERSION)
    expanded.setdefault("edges", [])

    compact_records: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ProjectError(f"compact.records[{index}] must be an object")
        # These fields identify the entity and carry every scientific detail.
        # Do not turn a typo or an incomplete thought into a plausible record
        # by inventing any of them.
        for field in ("id", "type", "title", "data"):
            if field not in record:
                raise ProjectError(f"compact.records[{index}].{field} is required")
        item = dict(record)
        item.setdefault("summary", "")
        item.setdefault("tags", [])
        compact_records.append(item)
    expanded["records"] = compact_records
    return expanded


def _result_update_template(hypothesis: dict[str, Any]) -> dict[str, Any]:
    """Return the only short writeback scaffold that updates an assertion.

    This is intentionally project-aware: a hash of the exact saved
    hypothesis makes a check/commit pair fail closed if another session
    changes that hypothesis in between.  The template never copies its
    prediction, mapping, applicability, unknowns, or state into author input.
    """

    hypothesis_id = hypothesis["id"]
    source_id = "source:__SRH_TEMPLATE_RESULT_SOURCE_ID__"
    evidence_id = "evidence:__SRH_TEMPLATE_RESULT_EVIDENCE_ID__"
    return {
        "format": RESULT_UPDATE_REMEMBER_FORMAT,
        "expected_hypothesis_hash": content_hash(hypothesis),
        "records": [
            {
                "id": source_id,
                "type": "source",
                "title": "__SRH_TEMPLATE_RESULT_SOURCE_TITLE__",
                "summary": "",
                "tags": [],
                "data": {"kind": "__SRH_TEMPLATE_RESULT_SOURCE_KIND__"},
            },
            {
                "id": evidence_id,
                "type": "evidence",
                "title": "__SRH_TEMPLATE_RESULT_EVIDENCE_TITLE__",
                "summary": "",
                "tags": [],
                "data": {
                    "source_id": source_id,
                    "locator": "__SRH_TEMPLATE_RESULT_LOCATOR__",
                    "excerpt": "__SRH_TEMPLATE_RESULT_EXCERPT__",
                    "evidence_kind": "__SRH_TEMPLATE_RESULT_EVIDENCE_KIND__",
                    "applicability": {
                        "domain": "__SRH_TEMPLATE_TARGET_DOMAIN__",
                        "task_context": "__SRH_TEMPLATE_TARGET_TASK_CONTEXT__",
                        "conditions": {"study_context": "__SRH_TEMPLATE_TARGET_CONDITIONS__"},
                    },
                },
            },
        ],
        "hypothesis_update": {
            "hypothesis_id": hypothesis_id,
            "status": "__SRH_TEMPLATE_UPDATED_STATUS__",
            "evidence_id": evidence_id,
            "evidence_role": "__SRH_TEMPLATE_UPDATED_EVIDENCE_ROLE__",
        },
    }


def _expand_result_update_remember(envelope: dict[str, Any], store: Any) -> dict[str, Any]:
    """Expand one explicit result/evidence update into the ordinary delta path.

    This helper is deliberately not a scientific inference layer.  The caller
    names a saved hypothesis, a new source/evidence unit, final epistemic
    status, and directional evidence role.  We only preserve the existing
    hypothesis payload while adding that evidence reference.  No edge, state
    patch, mechanism, experiment, result record, or status is invented.
    """

    if envelope.get("format") != RESULT_UPDATE_REMEMBER_FORMAT:
        raise ProjectError(
            f"result-update input requires format {RESULT_UPDATE_REMEMBER_FORMAT!r}"
        )
    allowed = {"format", "expected_hypothesis_hash", "records", "hypothesis_update"}
    unexpected = sorted(set(envelope) - allowed)
    if unexpected:
        raise ProjectError(
            "result-update accepts only format, expected_hypothesis_hash, records, and hypothesis_update; "
            f"unexpected {', '.join(unexpected)}"
        )
    expected_hash = envelope.get("expected_hypothesis_hash")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ProjectError("result-update.expected_hypothesis_hash must be the 64-character hash from its template")
    update = envelope.get("hypothesis_update")
    if not isinstance(update, dict) or set(update) != {"hypothesis_id", "status", "evidence_id", "evidence_role"}:
        raise ProjectError(
            "result-update.hypothesis_update must contain only hypothesis_id, status, evidence_id, and evidence_role"
        )
    hypothesis_id = update.get("hypothesis_id")
    evidence_id = update.get("evidence_id")
    status = update.get("status")
    evidence_role = update.get("evidence_role")
    for key, value in (("hypothesis_id", hypothesis_id), ("evidence_id", evidence_id), ("status", status), ("evidence_role", evidence_role)):
        if not isinstance(value, str) or not value.strip():
            raise ProjectError(f"result-update.hypothesis_update.{key} must be a non-empty string")
        if "__SRH_TEMPLATE_" in value:
            raise ProjectError(f"result-update.hypothesis_update.{key} contains an unreplaced SRH template placeholder")
    # A completed result can only make this small path a directional empirical
    # update.  Route activation/parking and all state changes remain authored
    # separately; the command must not guess them from a single observation.
    expected_roles = {
        "supported": "support",
        "contradicted": "counterevidence",
        "inconclusive": "inconclusive",
    }
    if status not in expected_roles:
        raise ProjectError("result-update.hypothesis_update.status must be supported, contradicted, or inconclusive")
    if evidence_role != expected_roles[status]:
        raise ProjectError(
            f"result-update status {status!r} requires evidence_role {expected_roles[status]!r}"
        )
    raw_records = envelope.get("records")
    if not isinstance(raw_records, list) or len(raw_records) != 2:
        raise ProjectError("result-update.records must contain exactly one new source and one new evidence record")
    # Reuse compact's harmless authoring defaults; normalizing below ensures
    # the short envelope does not create a second record schema.
    compact_records = _expand_compact_remember({"format": COMPACT_REMEMBER_FORMAT, "records": raw_records})["records"]
    try:
        records = [normalize_record(record, f"result-update.records[{index}]") for index, record in enumerate(compact_records)]
    except ValueError as exc:
        raise ProjectError(str(exc)) from exc
    sources = [record for record in records if record["type"] == "source"]
    evidences = [record for record in records if record["type"] == "evidence"]
    if len(sources) != 1 or len(evidences) != 1:
        raise ProjectError("result-update.records must contain one source record and one evidence record, with no other record types")
    source, evidence = sources[0], evidences[0]
    if evidence["id"] != evidence_id:
        raise ProjectError("result-update.hypothesis_update.evidence_id must exactly match the new evidence record ID")
    if evidence["data"].get("source_id") != source["id"]:
        raise ProjectError("result-update evidence must use source_id matching the new source record; paper_id and experiment_id are not accepted here")
    if store.get_record(source["id"]) is not None or store.get_record(evidence_id) is not None:
        raise ProjectError("result-update source and evidence IDs must be new; use a raw reviewed delta to revise existing provenance")
    hypothesis = store.get_record(hypothesis_id)
    if hypothesis is None:
        raise ProjectError(f"result-update target hypothesis {hypothesis_id!r} was not found")
    if hypothesis.get("type") != "hypothesis":
        raise ProjectError(f"result-update target {hypothesis_id!r} must be a hypothesis")
    if content_hash(hypothesis) != expected_hash:
        raise ProjectError(
            "result-update.expected_hypothesis_hash does not match the saved hypothesis; regenerate the template before writing"
        )
    # Preserve every existing authored hypothesis field verbatim except the
    # explicitly requested empirical status/evidence pairing.
    updated_hypothesis = json.loads(json.dumps(hypothesis, ensure_ascii=False))
    data = updated_hypothesis["data"]
    old_evidence_ids = data.get("evidence_ids", [])
    data["evidence_ids"] = list(old_evidence_ids) if isinstance(old_evidence_ids, list) else []
    if evidence_id not in data["evidence_ids"]:
        data["evidence_ids"].append(evidence_id)
    old_roles = data.get("evidence_roles", {})
    data["evidence_roles"] = dict(old_roles) if isinstance(old_roles, dict) else {}
    data["evidence_roles"][evidence_id] = evidence_role
    data["status"] = status
    try:
        updated_hypothesis = normalize_record(updated_hypothesis, "result-update.updated_hypothesis")
    except ValueError as exc:
        raise ProjectError(str(exc)) from exc
    if not is_qualified_assertion_evidence(updated_hypothesis, evidence):
        raise ProjectError(
            "result-update evidence must be direct empirical evidence with an exact explicit applicability match to the target hypothesis"
        )
    # Generating this project-aware template read the exact saved hypothesis
    # whose hash is checked above.  Record that narrow reread so the ordinary
    # delta transaction refreshes the hypothesis dependency basis for the new
    # direct evidence.  This is not a claim that the science is verified; it
    # is only provenance for the guarded merge performed by this short path.
    return {
        "schema_version": SCHEMA_VERSION,
        "records": [source, evidence, updated_hypothesis],
        "edges": [],
        "reviewed_ids": [hypothesis_id],
        "review_note": "Result-update template generated from the current saved hypothesis before attaching one new direct evidence item.",
    }


def _compact_remember_template(kind: str) -> dict[str, Any]:
    """Return a small authored-memory scaffold without making an inference.

    The reserved placeholders are intentionally rejected by the ordinary
    record validator.  A Codex session must replace them with source material
    it has actually inspected before ``remember --compact --check`` succeeds.
    This command therefore saves JSON authoring effort without supplying a
    source kind, quotation, claim, or scientific interpretation itself. The
    two assertion templates explicitly retain status, evidence direction,
    scope, and uncertainty rather than treating a source passage as automatic
    target-domain support.
    """
    if kind == "decision-revision":
        # Explicit authorship only: no selection of a predecessor, polarity,
        # or scientific invalidation from the project's current state.
        decision_id = "decision:__SRH_TEMPLATE_NEW_DECISION_ID__"
        evidence_id = "evidence:__SRH_TEMPLATE_EVIDENCE_ID__"
        return {
            "format": COMPACT_REMEMBER_FORMAT,
            "records": [{
                "id": decision_id,
                "type": "decision",
                "title": "__SRH_TEMPLATE_DECISION_TITLE__",
                "data": {
                    "reason": "__SRH_TEMPLATE_DECISION_REASON__",
                    "next_action": "__SRH_TEMPLATE_NEXT_ACTION__",
                    "evidence_ids": [evidence_id],
                    "evidence_roles": {evidence_id: "__SRH_TEMPLATE_DECISION_EVIDENCE_ROLE__"},
                    "applicability": {
                        "domain": "__SRH_TEMPLATE_TARGET_DOMAIN__",
                        "conditions": {"study_context": "__SRH_TEMPLATE_TARGET_CONDITIONS__"},
                    },
                },
            }],
            "edges": [{
                "id": "edge:__SRH_TEMPLATE_REVISION_EDGE_ID__",
                "source": decision_id,
                "target": "decision:__SRH_TEMPLATE_PREVIOUS_DECISION_ID__",
                "relation": "revises",
                "status": "structural",
                "evidence_ids": [],
                "conditions": {
                    "changed": "__SRH_TEMPLATE_CHANGED_SCOPE__",
                    "retained": "__SRH_TEMPLATE_RETAINED_SCOPE__",
                },
            }],
            "state_patch": {"recent_decisions": [decision_id]},
        }
    if kind == "source-evidence":
        source_id = "source:__SRH_TEMPLATE_SOURCE_ID__"
        records = [
            {
                "id": source_id,
                "type": "source",
                "title": "__SRH_TEMPLATE_SOURCE_TITLE__",
                "data": {"kind": "__SRH_TEMPLATE_SOURCE_KIND__"},
            },
            {
                "id": "evidence:__SRH_TEMPLATE_EVIDENCE_ID__",
                "type": "evidence",
                "title": "__SRH_TEMPLATE_EVIDENCE_TITLE__",
                "data": {
                    "source_id": source_id,
                    "locator": "__SRH_TEMPLATE_LOCATOR__",
                    "excerpt": "__SRH_TEMPLATE_EXCERPT__",
                    "evidence_kind": "__SRH_TEMPLATE_EVIDENCE_KIND__",
                    "applicability": {
                        "domain": "__SRH_TEMPLATE_EVIDENCE_DOMAIN__",
                        "conditions": {"study_context": "__SRH_TEMPLATE_EVIDENCE_CONDITIONS__"},
                    },
                },
            },
        ]
    elif kind == "scoped-claim":
        source_id = "source:__SRH_TEMPLATE_SOURCE_ID__"
        evidence_id = "evidence:__SRH_TEMPLATE_EVIDENCE_ID__"
        records = [
            {
                "id": source_id,
                "type": "source",
                "title": "__SRH_TEMPLATE_SOURCE_TITLE__",
                "data": {"kind": "__SRH_TEMPLATE_SOURCE_KIND__"},
            },
            {
                "id": evidence_id,
                "type": "evidence",
                "title": "__SRH_TEMPLATE_EVIDENCE_TITLE__",
                "data": {
                    "source_id": source_id,
                    "locator": "__SRH_TEMPLATE_LOCATOR__",
                    "excerpt": "__SRH_TEMPLATE_EXCERPT__",
                    "evidence_kind": "__SRH_TEMPLATE_EVIDENCE_KIND__",
                    "applicability": {
                        "domain": "__SRH_TEMPLATE_EVIDENCE_DOMAIN__",
                        "task_context": "__SRH_TEMPLATE_EVIDENCE_TASK_CONTEXT__",
                        "conditions": {"study_context": "__SRH_TEMPLATE_EVIDENCE_CONDITIONS__"},
                    },
                },
            },
            {
                "id": "claim:__SRH_TEMPLATE_CLAIM_ID__",
                "type": "claim",
                "title": "__SRH_TEMPLATE_CLAIM_TEXT__",
                "data": {
                    "origin": "__SRH_TEMPLATE_CLAIM_ORIGIN__",
                    "status": "__SRH_TEMPLATE_CLAIM_STATUS__",
                    "evidence_ids": [evidence_id],
                    "evidence_roles": {evidence_id: "__SRH_TEMPLATE_CLAIM_EVIDENCE_ROLE__"},
                    "applicability": {
                        "domain": "__SRH_TEMPLATE_CLAIM_DOMAIN__",
                        "task_context": "__SRH_TEMPLATE_CLAIM_TASK_CONTEXT__",
                        "conditions": {"study_context": "__SRH_TEMPLATE_CLAIM_CONDITIONS__"},
                    },
                    "alternative_explanations": ["__SRH_TEMPLATE_ALTERNATIVE_EXPLANATION__"],
                    "unknowns": ["__SRH_TEMPLATE_REMAINING_UNKNOWN__"],
                },
            },
        ]
    elif kind == "transfer-hypothesis":
        source_anchor_id = "source:__SRH_TEMPLATE_SOURCE_BASIS_ID__"
        target_anchor_id = "source:__SRH_TEMPLATE_TARGET_CONTEXT_ID__"
        source_evidence_id = "evidence:__SRH_TEMPLATE_SOURCE_BASIS_EVIDENCE_ID__"
        target_evidence_id = "evidence:__SRH_TEMPLATE_TARGET_CONTEXT_EVIDENCE_ID__"
        records = [
            {
                "id": source_anchor_id,
                "type": "source",
                "title": "__SRH_TEMPLATE_SOURCE_BASIS_TITLE__",
                "data": {"kind": "__SRH_TEMPLATE_SOURCE_BASIS_KIND__"},
            },
            {
                "id": target_anchor_id,
                "type": "source",
                "title": "__SRH_TEMPLATE_TARGET_CONTEXT_TITLE__",
                "data": {"kind": "__SRH_TEMPLATE_TARGET_CONTEXT_KIND__"},
            },
            {
                "id": source_evidence_id,
                "type": "evidence",
                "title": "__SRH_TEMPLATE_SOURCE_BASIS_EVIDENCE_TITLE__",
                "data": {
                    "source_id": source_anchor_id,
                    "locator": "__SRH_TEMPLATE_SOURCE_BASIS_LOCATOR__",
                    "excerpt": "__SRH_TEMPLATE_SOURCE_BASIS_EXCERPT__",
                    "evidence_kind": "__SRH_TEMPLATE_SOURCE_BASIS_EVIDENCE_KIND__",
                    "applicability": {
                        "domain": "__SRH_TEMPLATE_SOURCE_DOMAIN__",
                        "task_context": "__SRH_TEMPLATE_SOURCE_TASK_CONTEXT__",
                        "conditions": {"study_context": "__SRH_TEMPLATE_SOURCE_CONDITIONS__"},
                    },
                },
            },
            {
                "id": target_evidence_id,
                "type": "evidence",
                "title": "__SRH_TEMPLATE_TARGET_CONTEXT_EVIDENCE_TITLE__",
                "data": {
                    "source_id": target_anchor_id,
                    "locator": "__SRH_TEMPLATE_TARGET_CONTEXT_LOCATOR__",
                    "excerpt": "__SRH_TEMPLATE_TARGET_CONTEXT_EXCERPT__",
                    "evidence_kind": "__SRH_TEMPLATE_TARGET_CONTEXT_EVIDENCE_KIND__",
                    "applicability": {
                        "domain": "__SRH_TEMPLATE_TARGET_DOMAIN__",
                        "task_context": "__SRH_TEMPLATE_TARGET_TASK_CONTEXT__",
                        "conditions": {"study_context": "__SRH_TEMPLATE_TARGET_CONDITIONS__"},
                    },
                },
            },
            {
                "id": "hypothesis:__SRH_TEMPLATE_HYPOTHESIS_ID__",
                "type": "hypothesis",
                "title": "__SRH_TEMPLATE_HYPOTHESIS_TITLE__",
                "data": {
                    "prediction": "__SRH_TEMPLATE_TARGET_PREDICTION__",
                    "alternatives": ["__SRH_TEMPLATE_TARGET_ALTERNATIVE__"],
                    "test": "__SRH_TEMPLATE_FALSIFIABLE_TEST__",
                    "status": "proposed",
                    "assertion_role": "transfer_proposal",
                    "evidence_ids": [source_evidence_id, target_evidence_id],
                    "evidence_roles": {
                        source_evidence_id: "analogy",
                        target_evidence_id: "context",
                    },
                    "applicability": {
                        "target_domain": "__SRH_TEMPLATE_TARGET_DOMAIN__",
                        "task_context": "__SRH_TEMPLATE_TARGET_TASK_CONTEXT__",
                        "conditions": {"study_context": "__SRH_TEMPLATE_TARGET_CONDITIONS__"},
                    },
                    "transfer_mapping": {
                        "source_problem": "__SRH_TEMPLATE_SOURCE_PROBLEM__",
                        "target_problem": "__SRH_TEMPLATE_TARGET_PROBLEM__",
                        "shared_constraint_or_mechanism": "__SRH_TEMPLATE_SHARED_BASIS__",
                        "source_to_target_variable_mapping": "__SRH_TEMPLATE_VARIABLE_MAPPING__",
                        "non_equivalences": ["__SRH_TEMPLATE_NON_EQUIVALENCE__"],
                    },
                    "unknowns": ["__SRH_TEMPLATE_TRANSFER_UNKNOWN__"],
                    "minimum_falsifier": "__SRH_TEMPLATE_MINIMUM_FALSIFIER__",
                },
            },
        ]
    else:
        # argparse currently owns public choice validation. This boundary still
        # keeps the helper safe for direct callers and future additions.
        raise ProjectError(f"unknown compact remember template: {kind}")
    return {"format": COMPACT_REMEMBER_FORMAT, "records": records}


def _expand_evidence_remember(unit: dict[str, Any], store: Any) -> dict[str, Any]:
    """A single source/evidence unit, compiled to the ordinary guarded delta.

    IDs, kinds, locators, excerpts and scope are authored, never inferred.
    Existing records may be reused verbatim, not silently replaced by a short
    input that omits their metadata. The guard is checked inside apply_delta.
    """
    required = {"id", "title", "source", "locator", "excerpt", "evidence_kind"}
    optional = {"applicability", "assertion_role"}
    if set(unit) - required - optional or required - set(unit):
        raise ProjectError("evidence unit requires id, title, source, locator, excerpt, evidence_kind; "
                           "optional: applicability, assertion_role; no other keys")
    source = unit["source"]
    if not isinstance(source, dict) or set(source) != {"id", "title", "kind"}:
        raise ProjectError("evidence unit source requires exactly id, title, kind")
    for path, value in [(key, unit[key]) for key in required - {"source"}] + [
        (f"source.{key}", value) for key, value in source.items()
    ]:
        if not isinstance(value, str) or not value.strip():
            raise ProjectError(f"evidence unit {path} must be a nonempty string")
    if source["id"] == unit["id"]:
        raise ProjectError("evidence and source IDs must differ")
    source_record = {
        "id": source["id"], "type": "source", "title": source["title"],
        "summary": "", "tags": [], "data": {"kind": source["kind"]},
    }
    evidence_record = {
        "id": unit["id"], "type": "evidence", "title": unit["title"], "summary": "", "tags": [],
        "data": {"source_id": source["id"], **{key: value for key, value in unit.items()
                 if key not in {"id", "title", "source"}}},
    }
    records, guards = [], {}
    for candidate in (source_record, evidence_record):
        existing = store.get_record(candidate["id"])
        guards[candidate["id"]] = content_hash(existing) if existing is not None else None
        if existing is not None:
            matches = existing["type"] == candidate["type"] and existing["title"] == candidate["title"]
            if candidate["type"] == "source":
                matches = matches and existing["data"].get("kind") == source["kind"]
            else:
                matches = matches and all(existing["data"].get(key) == value
                                          for key, value in candidate["data"].items())
            if not matches:
                raise ProjectError(f"evidence short path cannot replace existing record {candidate['id']!r}; "
                                   "use an ordinary reviewed remember delta for changes")
            candidate = existing
        records.append(candidate)
    return {"schema_version": SCHEMA_VERSION, "records": records, "edges": [],
            "expected_record_hashes": guards}


def _compact_check_result(result: dict[str, Any]) -> dict[str, Any]:
    """Describe the compact invocation without changing validator semantics."""

    guidance = result.get("guidance")
    if not isinstance(guidance, dict):
        return result
    adjusted = dict(result)
    if isinstance(adjusted.get("next_step"), str):
        adjusted["next_step"] = adjusted["next_step"].replace(
            "srh remember FILE", "srh remember --compact FILE"
        )
    compact_guidance = dict(guidance)
    compact_guidance["submit"] = "srh remember --compact FILE"
    compact_guidance["compact"] = (
        "Compact input expands only schema_version=1, edges=[], and missing "
        "record summary='' / tags=[]; it does not infer or repair research content."
    )
    adjusted["guidance"] = compact_guidance
    return adjusted


def _result_update_check_result(result: dict[str, Any]) -> dict[str, Any]:
    """Describe the narrow status-update path without weakening its checks."""

    guidance = result.get("guidance")
    if not isinstance(guidance, dict):
        return result
    adjusted = dict(result)
    update_guidance = dict(guidance)
    update_guidance["submit"] = "srh remember --result-update FILE"
    update_guidance["result_update"] = (
        "This path writes exactly one new source, one new direct empirical evidence item, and an explicit "
        "status/evidence-role update to the named saved hypothesis. It preserves other hypothesis fields and never patches research state."
    )
    adjusted["guidance"] = update_guidance
    return adjusted


def _doctor(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"project": str(root), "sqlite": sqlite3.sqlite_version}
    try:
        import sqlite_vec  # noqa: F401

        result["sqlite_vec"] = {"available": True, "version": getattr(sqlite_vec, "__version__", "unknown")}
    except Exception as exc:
        result["sqlite_vec"] = {"available": False, "error": str(exc)}
    try:
        from .store import ResearchStore

        with ResearchStore(root) as store:
            result["store"] = "ready"
            result["stats"] = store.stats()
            result["state"] = store.get_state()
    except Exception as exc:
        result["store"] = "error"
        result["error"] = str(exc)
    result["host_integration"] = {
        "configured": {
            "skills_directory": (root / ".agents" / "skills").is_dir(),
            "hooks_file": (root / ".codex" / "hooks.json").is_file(),
        },
        "runtime": {
            "skills": "not verified by doctor; availability depends on the Codex host session",
            "hooks": "not verified by doctor; review repository hook trust in the Codex host",
        },
    }
    return result


def _dispatch(args: argparse.Namespace) -> Any:
    if args.command == "init":
        return init_project(args.path, question=args.question, hooks=args.hooks)
    if args.command == "forecast-update" and args.template:
        if args.file or args.check:
            raise ProjectError("forecast-update --template cannot be combined with FILE or --check")
        return _forecast_update_template()
    # A template is an authoring aid rather than a project operation.  It must
    # work before a research directory exists and must not open/create a store.
    if args.command == "remember" and args.compact_template:
        if args.file or args.check is not None or args.compact or args.evidence or args.result_update or args.result_update_template:
            raise ProjectError(
                "remember --compact-template KIND cannot be combined with FILE, --check, or --compact"
            )
        return _compact_remember_template(args.compact_template)
    root = _project_for(args)
    if args.command == "hook":
        return run_hook(args.event, root)
    if args.command == "forecast-update":
        if bool(args.file) == bool(args.check):
            raise ProjectError("forecast-update accepts exactly one of FILE or --check FILE")
        input_file = args.check or args.file
        receipt = _forecast_update_receipt(root, _read_json_file(input_file))
        if args.check:
            return {"checked": True, "receipt": receipt}
        return _commit_forecast_update(root, receipt)
    if args.command == "context":
        return context_for_project(
            root, query=args.query, max_chars=args.max_chars, intent=args.intent,
            focus=args.focus, include_details=args.with_details,
            include_quality=args.with_quality,
        )
    from .store import ResearchStore

    with ResearchStore(root) as store:
        if args.command == "ingest":
            result = store.ingest(_read_json_file(args.file))
            # Keep the detailed per-kind counters while offering a compact
            # aggregate for shell callers.
            if isinstance(result, dict) and "inserted" not in result:
                result["inserted"] = int(result.get("inserted_records", 0)) + int(result.get("inserted_edges", 0))
                result["updated"] = int(result.get("updated_records", 0)) + int(result.get("updated_edges", 0))
                result["unchanged"] = int(result.get("unchanged_records", 0)) + int(result.get("unchanged_edges", 0))
            return result
        if args.command == "remember":
            if args.result_update_template:
                if args.file or args.check is not None or args.compact or args.evidence or args.result_update:
                    raise ProjectError(
                        "remember --result-update-template HYPOTHESIS_ID cannot be combined with FILE, --check, --compact, or --result-update"
                    )
                hypothesis = store.get_record(args.result_update_template)
                if hypothesis is None:
                    raise ProjectError(f"result-update target hypothesis {args.result_update_template!r} was not found")
                if hypothesis.get("type") != "hypothesis":
                    raise ProjectError(f"result-update target {args.result_update_template!r} must be a hypothesis")
                return _result_update_template(hypothesis)
            compact_check = args.check is _COMPACT_CHECK
            raw_check = isinstance(args.check, str)
            result_update = bool(args.result_update)
            selected_inputs = sum(bool(value) for value in (args.file, args.compact, args.result_update, args.evidence))
            if selected_inputs > 1:
                raise ProjectError("remember accepts either FILE or --compact FILE (or --result-update FILE or --evidence FILE), but not more than one")
            if raw_check and (args.file or args.compact or args.result_update or args.evidence):
                raise ProjectError("remember --check FILE cannot be combined with FILE, --compact FILE, --result-update FILE, or --evidence FILE")
            if compact_check and (args.file or not (args.compact or args.result_update or args.evidence)):
                raise ProjectError("remember --check without FILE is only valid with --compact FILE, --result-update FILE, or --evidence FILE")

            compact = bool(args.compact)
            if compact:
                delta_file = args.compact
            elif result_update:
                delta_file = args.result_update
            elif args.evidence:
                delta_file = args.evidence
            elif raw_check:
                delta_file = args.check
            elif args.file:
                delta_file = args.file
            else:
                raise ProjectError("remember requires FILE, --check FILE, or --compact FILE")

            delta = _read_json_file(delta_file)
            if compact:
                delta = _expand_compact_remember(delta)
            elif result_update:
                delta = _expand_result_update_remember(delta, store)
            elif args.evidence:
                delta = _expand_evidence_remember(delta, store)
            checking = raw_check or compact_check
            result = store.check_delta(delta) if checking else store.apply_delta(delta)
            if args.evidence and checking:
                result["guidance"]["submit"] = "srh remember --evidence FILE"
                if isinstance(result.get("next_step"), str):
                    result["next_step"] = result["next_step"].replace("srh remember FILE", "srh remember --evidence FILE")
                return result
            if compact and checking:
                return _compact_check_result(result)
            if result_update and checking:
                return _result_update_check_result(result)
            return result
        if args.command == "show":
            # Keep a single lookup byte-for-byte compatible for callers that
            # consume the record object directly.  Batch lookup is explicit:
            # it is merely a bounded sequence of direct record reads, not a
            # graph/context/quality/vector expansion.
            ids = list(dict.fromkeys(args.ids))
            if len(ids) > SHOW_MAX_UNIQUE_IDS:
                raise ProjectError(
                    f"show accepts at most {SHOW_MAX_UNIQUE_IDS} unique record IDs"
                )
            if len(args.ids) == 1:
                value = store.get_record(ids[0])
                if value is None:
                    raise ProjectError("record not found")
                return value

            records: list[dict[str, Any]] = []
            missing_ids: list[str] = []
            for record_id in ids:
                value = store.get_record(record_id)
                if value is None:
                    missing_ids.append(record_id)
                else:
                    records.append(value)
            return {"records": records, "missing_ids": missing_ids}
        if args.command == "list":
            return store.list_records(record_type=args.type, limit=args.limit)
        if args.command == "search":
            return store.search(args.query, mode=args.mode, limit=args.limit, record_type=args.type)
        if args.command == "graph":
            return store.graph(seed=args.id, hops=args.hops, limit=args.limit)
        if args.command == "index":
            return store.build_index(provider=args.provider, model=args.model)
        if args.command == "state":
            if args.expected_hash and not args.file:
                raise ProjectError("--expected-hash requires --file")
            if args.file:
                try:
                    patch = json.loads(args.file.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise ProjectError(f"cannot read state patch: {exc}") from exc
                if not isinstance(patch, dict):
                    raise ProjectError("state patch must be a JSON object")
                if any(key in patch for key in ("schema_version", "records", "edges", "state_patch")):
                    raise ProjectError(
                        "state --file expects the state patch itself (for example question, notes, next_action), "
                        "not a remember delta with schema_version/records/edges/state_patch; "
                        "submit a graph delta with `remember FILE` instead"
                    )
                state = (store.update_state(patch, expected_hash=args.expected_hash)
                         if args.expected_hash is not None else store.update_state(patch))
                if args.hash:
                    return {"state": state, "state_hash": content_hash(state)}
                return state
            state = store.get_state()
            return {"state": state, "state_hash": content_hash(state)} if args.hash else state
        if args.command == "quality":
            return store.quality(focus=args.focus, limit=args.limit)
        if args.command == "doctor":
            return _doctor(root)
        if args.command == "export":
            bundle = store.export_bundle()
            if args.file:
                destination = args.file.expanduser()
                if destination.exists() or destination.is_symlink():
                    raise ProjectError(f"refusing to overwrite existing export: {destination}")
                if destination.parent.exists() and not destination.parent.is_dir():
                    raise ProjectError(f"export parent is not a directory: {destination.parent}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with destination.open("x", encoding="utf-8", newline="\n") as handle:
                        json.dump(bundle, handle, ensure_ascii=False, indent=2)
                        handle.write("\n")
                except FileExistsError as exc:
                    raise ProjectError(f"refusing to overwrite existing export: {destination}") from exc
                return {"file": str(args.file), "records": len(bundle.get("records", [])), "edges": len(bundle.get("edges", []))}
            return bundle
        if args.command == "checkpoint":
            note = args.note or "Checkpoint created."
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            # A checkpoint is an export boundary, not a scientific decision.
            # Inserting a generic decision here used to crowd the bounded
            # recovery context and could make a snapshot look like evidence or
            # a route choice. Meaningful research memory must go through an
            # explicit reviewed ``remember`` delta before this export.
            bundle = store.export_bundle()
            exports = root / ".research" / "exports"
            exports.mkdir(parents=True, exist_ok=True)
            md = f"# Research checkpoint ({stamp})\n\n{note}\n\nRecords: {len(bundle.get('records', []))}; edges: {len(bundle.get('edges', []))}.\n"
            json_rel = Path(".research/exports") / f"checkpoint-{stamp}.json"
            md_rel = Path(".research/exports") / f"checkpoint-{stamp}.md"
            _atomic_write_project(root, json_rel, (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            _atomic_write_project(root, md_rel, md.encode("utf-8"))
            return {
                "export": str(exports / f"checkpoint-{stamp}.json"),
                "markdown": str(exports / f"checkpoint-{stamp}.md"),
                "note": note,
                "records": len(bundle.get("records", [])),
                "edges": len(bundle.get("edges", [])),
            }
    raise ProjectError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        value = _dispatch(args)
        if isinstance(value, str):
            print(value)
        else:
            _json_dump(value)
        return 0
    except (ProjectError, ValueError, OSError) as exc:
        print(f"srh: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # defensive CLI boundary; no traceback by default
        print(f"srh: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("srh: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
