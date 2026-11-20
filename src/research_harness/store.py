"""SQLite source of truth for the Scientific Research Harness."""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .embeddings import DEFAULT_DIMENSION, DEFAULT_MODEL, DEFAULT_PROVIDER, EmbeddingError, make_embedder, record_text
from .models import (
    ASSERTION_ROLES, EDGE_RELATIONS, EDGE_STATUSES, EVIDENCE_KINDS, EVIDENCE_ROLES,
    RECORD_TYPES, SCHEMA_VERSION, STRUCTURAL_RELATIONS,
    canonical_json, content_hash, dependency_content_hash,
    edge_dependency_refs, normalize_bundle, normalize_bundle_state,
    record_dependency_refs, record_navigation_refs,
)
from .quality import assess_quality, relation_is_compatible


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _review_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _copy(value: Any) -> Any:
    return json.loads(canonical_json(value))


class ResearchStore:
    """Transactional project-local graph; it records, but does not verify, research."""

    def __init__(self, root: Path | str, create: bool = False):
        self.root = Path(root)
        self.research_dir = self.root / ".research"
        self.db_path = self.research_dir / "knowledge.sqlite3"
        cache = os.environ.get("FASTEMBED_CACHE_PATH")
        self.model_cache_dir = Path(cache) if cache else None
        if not create and not self.db_path.exists():
            raise ValueError(f"research database is not initialized: {self.db_path}")
        if create:
            self.research_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._vec_available = False
        try:
            import sqlite_vec
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            self._vec_available = True
        except Exception:
            pass
        if create:
            self._initialize()
        else:
            try:
                self._check_initialized()
            except Exception:
                self.close()
                raise

    def _check_initialized(self) -> None:
        row = self.conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if row is None or int(json.loads(row[0])) != SCHEMA_VERSION:
            raise ValueError(f"uninitialized or unsupported research database: {self.db_path}")

    def _initialize(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records (
              id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
              tags_json TEXT NOT NULL, data_json TEXT NOT NULL, payload_json TEXT NOT NULL,
              content_hash TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS edges (
              id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL, relation TEXT NOT NULL,
              status TEXT NOT NULL, evidence_json TEXT NOT NULL, conditions_json TEXT NOT NULL,
              payload_json TEXT NOT NULL, content_hash TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, entity_id TEXT,
              payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK (id=1), value_json TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(record_id UNINDEXED, text);
            CREATE TABLE IF NOT EXISTS vector_meta (
              record_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, provider TEXT NOT NULL,
              model TEXT NOT NULL, dimension INTEGER NOT NULL);
            INSERT OR IGNORE INTO metadata(key,value) VALUES
              ('schema_version','1'), ('provider','"fastembed"'), ('model','"BAAI/bge-small-en-v1.5"'), ('dimension','384');
            INSERT OR IGNORE INTO state(id,value_json) VALUES
              (1,'{"question":"","active_hypotheses":[],"unresolved":[],"next_action":""}');
        """)
        if self._vec_available:
            self._ensure_vec_table(DEFAULT_DIMENSION)
        self.conn.commit()

    def _ensure_vec_table(self, dimension: int) -> None:
        if self._vec_available and not self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='vec_records'").fetchone():
            self.conn.execute(f"CREATE VIRTUAL TABLE vec_records USING vec0(record_id TEXT PRIMARY KEY, embedding FLOAT[{int(dimension)}] distance_metric=cosine)")

    def close(self) -> None:
        if getattr(self, "conn", None) is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> "ResearchStore":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def _all_ids(self) -> dict[str, str]:
        return {row["id"]: row["type"] for row in self.conn.execute("SELECT id,type FROM records")}

    def _records(self) -> dict[str, dict[str, Any]]:
        return {row["id"]: json.loads(row["payload_json"]) for row in self.conn.execute("SELECT id,payload_json FROM records")}

    def _edges(self) -> dict[str, dict[str, Any]]:
        return {row["id"]: json.loads(row["payload_json"]) for row in self.conn.execute("SELECT id,payload_json FROM edges")}

    def _current_state(self) -> dict:
        row = self.conn.execute("SELECT value_json FROM state WHERE id=1").fetchone()
        if row is None:
            raise ValueError("research state is missing")
        return json.loads(row[0])

    @staticmethod
    def _typed_refs(record: dict[str, Any]) -> list[tuple[Any, str]]:
        data, typ = record["data"], record["type"]
        refs: list[tuple[Any, str]] = []
        if typ == "evidence":
            refs.append((
                data.get("source_id") or data.get("paper_id") or data.get("experiment_id"),
                "source" if data.get("source_id") else "paper" if data.get("paper_id") else "experiment",
            ))
        elif typ == "experiment":
            refs.append((data.get("hypothesis_id"), "hypothesis"))
        elif typ == "result":
            refs.append((data.get("experiment_id"), "experiment"))
        if "evidence_ids" in data:
            refs.extend((item, "evidence") for item in data["evidence_ids"])
        return refs

    def _validate_refs(self, records: list[dict], edges: list[dict], *, strict_new_assertions: bool = False) -> dict[str, str]:
        """Call only after BEGIN IMMEDIATE: types are current plus final input."""
        types = self._all_ids()
        for record in records:
            old_type = types.get(record["id"])
            if old_type is not None and old_type != record["type"]:
                raise ValueError(f"record {record['id']!r} cannot change type")
            types[record["id"]] = record["type"]
        for record in records:
            data = record["data"]
            if strict_new_assertions and record["type"] == "mechanism" and data.get("status") in {"reported", "supported", "contradicted", "inconclusive"} and not data.get("evidence_ids"):
                raise ValueError(f"established mechanism {record['id']!r} requires evidence_ids")
            for ref, expected in self._typed_refs(record):
                if not isinstance(ref, str) or not ref or types.get(ref) != expected:
                    raise ValueError(f"record {record['id']!r} reference {ref!r} must resolve to {expected}")
            roles = data.get("evidence_roles")
            if isinstance(roles, dict):
                for evidence_id in roles:
                    if evidence_id not in data.get("evidence_ids", []) or types.get(evidence_id) != "evidence":
                        raise ValueError(f"record {record['id']!r} evidence_roles key {evidence_id!r} must be a listed evidence record")
            basis = record["data"].get("basis")
            if isinstance(basis, dict):
                for ref in basis:
                    if ref not in types:
                        raise ValueError(f"record {record['id']!r} basis reference {ref!r} is unknown")
        for edge in edges:
            source_type, target_type = types.get(edge["source"]), types.get(edge["target"])
            if source_type is None or target_type is None:
                raise ValueError(f"edge {edge['id']!r} references an unknown endpoint")
            if not relation_is_compatible(edge["relation"], source_type, target_type):
                targets = sorted(typ for typ in RECORD_TYPES if relation_is_compatible(edge["relation"], source_type, typ))
                raise ValueError(
                    f"edge {edge['id']!r} relation {edge['relation']!r} is incompatible with {source_type}->{target_type}; "
                    f"allowed target types for {source_type}: {', '.join(targets) or 'none'}"
                )
            for evidence_id in edge["evidence_ids"]:
                if types.get(evidence_id) != "evidence":
                    raise ValueError(f"edge {edge['id']!r} evidence ref {evidence_id!r} is not an evidence record")
            roles = edge.get("evidence_roles")
            if isinstance(roles, dict):
                for evidence_id in roles:
                    if evidence_id not in edge["evidence_ids"]:
                        raise ValueError(f"edge {edge['id']!r} evidence_roles key {evidence_id!r} must be a listed evidence record")
            if edge["status"] in {"reported", "observed"} and edge["relation"] not in STRUCTURAL_RELATIONS and not edge["evidence_ids"]:
                raise ValueError(f"reported/observed edge {edge['id']!r} requires evidence_ids")
            basis = edge.get("basis")
            if isinstance(basis, dict):
                for ref in basis:
                    if ref not in types:
                        raise ValueError(f"edge {edge['id']!r} basis reference {ref!r} is unknown")
        return types

    @staticmethod
    def _validate_state(state: dict, types: dict[str, str]) -> dict:
        if not isinstance(state, dict):
            raise ValueError("state must be an object")
        try:
            state = _copy(state)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"state contains non-JSON data: {exc}") from exc
        for field in ("question", "next_action"):
            if field in state and not isinstance(state[field], str):
                raise ValueError(f"state.{field} must be a string")
        checks = {"active_hypotheses": "hypothesis", "unresolved_hypotheses": "hypothesis", "recent_decisions": "decision", "unresolved": None}
        for field, expected in checks.items():
            if field not in state:
                continue
            values = state[field]
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"state.{field} must be a list of record IDs")
            for value in values:
                if value not in types:
                    raise ValueError(f"state.{field} reference {value!r} is unknown")
                if expected is not None and types[value] != expected:
                    raise ValueError(f"state.{field} reference {value!r} must resolve to {expected}")
        return state

    @staticmethod
    def _counts() -> dict[str, int]:
        return {"inserted_records": 0, "updated_records": 0, "unchanged_records": 0,
                "inserted_edges": 0, "updated_edges": 0, "unchanged_edges": 0, "state_updated": 0}

    def _write_entities(self, records: list[dict], edges: list[dict], counts: dict[str, int], now: str,
                        *, receipt_limit: int = 0) -> dict | None:
        # Build the receipt on the actual write path, without an extra graph
        # read or any scientific text. It is returned only after the caller's
        # transaction succeeds (or explicitly labelled as a rolled-back preview).
        receipt = ({"records": [], "edges": [], "record_count": len(records), "edge_count": len(edges),
                    "truncated": len(records) > receipt_limit or len(edges) > receipt_limit}
                   if receipt_limit else None)

        def capture(kind: str, item: dict, operation: str) -> None:
            if receipt is None or len(receipt[kind]) >= receipt_limit:
                return
            fields = ("id", "type") if kind == "records" else ("id", "source", "relation", "target", "status")
            receipt[kind].append({**{field: item[field] for field in fields}, "operation": operation})

        for record in records:
            record_id, digest = record["id"], content_hash(record)
            old = self.conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
            if old and old["content_hash"] == digest:
                counts["unchanged_records"] += 1
                capture("records", record, "unchanged")
                continue
            revision = int(old["revision"]) + 1 if old else 1
            self.conn.execute("INSERT OR REPLACE INTO records(id,type,title,summary,tags_json,data_json,payload_json,content_hash,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (record_id, record["type"], record["title"], record["summary"], json.dumps(record["tags"], ensure_ascii=False), canonical_json(record["data"]), canonical_json(record), digest, revision, old["created_at"] if old else now, now))
            self.conn.execute("DELETE FROM records_fts WHERE record_id=?", (record_id,))
            self.conn.execute("INSERT INTO records_fts(record_id,text) VALUES (?,?)", (record_id, record_text(record)))
            if self._vec_available:
                self.conn.execute("DELETE FROM vector_meta WHERE record_id=?", (record_id,))
                self.conn.execute("DELETE FROM vec_records WHERE record_id=?", (record_id,))
            event = "record.inserted" if old is None else "record.updated"
            self.conn.execute("INSERT INTO events(event_type,entity_id,payload_json,created_at) VALUES(?,?,?,?)", (event, record_id, canonical_json({"record": record, "previous": json.loads(old["payload_json"]) if old else None, "revision": revision}), now))
            counts["inserted_records" if old is None else "updated_records"] += 1
            capture("records", record, "inserted" if old is None else "updated")
        for edge in edges:
            edge_id, digest = edge["id"], content_hash(edge)
            old = self.conn.execute("SELECT * FROM edges WHERE id=?", (edge_id,)).fetchone()
            if old and old["content_hash"] == digest:
                counts["unchanged_edges"] += 1
                capture("edges", edge, "unchanged")
                continue
            revision = int(old["revision"]) + 1 if old else 1
            self.conn.execute("INSERT OR REPLACE INTO edges(id,source,target,relation,status,evidence_json,conditions_json,payload_json,content_hash,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (edge_id, edge["source"], edge["target"], edge["relation"], edge["status"], canonical_json(edge["evidence_ids"]), canonical_json(edge["conditions"]), canonical_json(edge), digest, revision, old["created_at"] if old else now, now))
            event = "edge.inserted" if old is None else "edge.updated"
            self.conn.execute("INSERT INTO events(event_type,entity_id,payload_json,created_at) VALUES(?,?,?,?)", (event, edge_id, canonical_json({"edge": edge, "previous": json.loads(old["payload_json"]) if old else None, "revision": revision}), now))
            counts["inserted_edges" if old is None else "updated_edges"] += 1
            capture("edges", edge, "inserted" if old is None else "updated")
        return receipt

    def _preserve_ingest_maintenance(self, records: list[dict], edges: list[dict]) -> None:
        """Do not erase durable review history on a plain replay/update.

        ``ingest`` accepts source bundles which usually pre-date maintenance
        fields.  Absence means "not supplied", not a fresh review or an
        instruction to clear ``basis``/``basis_review``.  An export carrying
        either field remains an exact restore because supplied values win.
        """
        existing_records, existing_edges = self._records(), self._edges()
        for record in records:
            old_data = existing_records.get(record["id"], {}).get("data", {})
            for field in ("basis", "basis_review"):
                if field in record["data"] and record["id"] in existing_records and record["data"][field] != old_data.get(field):
                    raise ValueError(f"ingest cannot change {field} for existing record {record['id']!r}; use apply_delta with reviewed_ids")
                if field not in record["data"] and isinstance(old_data.get(field), dict):
                    record["data"][field] = _copy(old_data[field])
        for edge in edges:
            old = existing_edges.get(edge["id"], {})
            for field in ("basis", "basis_review"):
                if field in edge and edge["id"] in existing_edges and edge[field] != old.get(field):
                    raise ValueError(f"ingest cannot change {field} for existing edge {edge['id']!r}; use apply_delta with reviewed_ids")
                if field not in edge and isinstance(old.get(field), dict):
                    edge[field] = _copy(old[field])

    def ingest(self, bundle: dict) -> dict:
        records, edges = normalize_bundle(bundle)
        state = normalize_bundle_state(bundle)
        counts, now = self._counts(), _now()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            types = self._validate_refs(records, edges)
            if state is not None:
                state = self._validate_state(state, types)
            self._preserve_ingest_maintenance(records, edges)
            self._write_entities(records, edges, counts, now)
            if state is not None and state != self._current_state():
                previous = self._current_state()
                self.conn.execute("UPDATE state SET value_json=? WHERE id=1", (canonical_json(state),))
                self.conn.execute("INSERT INTO events(event_type,entity_id,payload_json,created_at) VALUES(?,?,?,?)", ("state.restored", "state", canonical_json({"state": state, "previous": previous}), now))
                counts["state_updated"] = 1
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return counts

    @staticmethod
    def _resolve_review_ids(reviewed_ids: list[str], records: dict[str, dict], edges: dict[str, dict]) -> tuple[set[str], set[str]]:
        reviewed_records: set[str] = set()
        reviewed_edges: set[str] = set()
        for raw in reviewed_ids:
            # Record IDs conventionally include prefixes such as ``edge:``;
            # prefer a literal match before treating record:/edge: as a typed
            # addressing escape hatch.
            if raw in records and raw in edges:
                raise ValueError(f"reviewed ID {raw!r} is ambiguous; use record:{raw} or edge:{raw}")
            if raw in records:
                reviewed_records.add(raw)
            elif raw in edges:
                reviewed_edges.add(raw)
            elif raw.startswith("record:"):
                entity_id = raw[7:]
                if entity_id not in records:
                    raise ValueError(f"reviewed record {entity_id!r} is unknown")
                reviewed_records.add(entity_id)
            elif raw.startswith("edge:"):
                entity_id = raw[5:]
                if entity_id not in edges:
                    raise ValueError(f"reviewed edge {entity_id!r} is unknown")
                reviewed_edges.add(entity_id)
            else:
                raise ValueError(f"reviewed entity {raw!r} is unknown")
        return reviewed_records, reviewed_edges

    @staticmethod
    def _remember_check_contract() -> dict[str, Any]:
        """Return the compact, schema-owned contract shown by ``remember --check``.

        This deliberately tells a caller how to submit an authored delta, but
        never tries to infer an anchor, role, or scientific interpretation.
        Keeping the enum values beside the validator avoids a second, stale
        command-specific schema.
        """

        return {
            "submit": "srh remember FILE",
            "evidence_anchor": (
                "Every evidence record needs exactly one nonempty source_id, "
                "paper_id, or experiment_id resolving to a source, paper, or "
                "experiment record stored already or included in this delta."
            ),
            "evidence_role_target": (
                "A record's evidence_roles[id] is relative to that record's proposition or decision, "
                "not whether the evidence is bad news for a previous route. On supports/contradicts/limits "
                "edges it is relative to the named target assertion. A new decision may use the same "
                "evidence as support while revising an old decision via new --revises/structural--> old; "
                "describe changed and retained scope in conditions. Validation checks shape/references, "
                "not scientific correctness."
            ),
            "enums": {
                "assertion_role": sorted(ASSERTION_ROLES),
                "evidence_kind": sorted(EVIDENCE_KINDS),
                "evidence_roles": sorted(EVIDENCE_ROLES),
                "edge_status": sorted(EDGE_STATUSES),
                "edge_relation": sorted(EDGE_RELATIONS),
            },
        }

    @staticmethod
    def _remember_check_path(message: str) -> str | None:
        """Return a conservative validation path only when its syntax is clear."""

        match = re.match(
            r"^(?P<path>(?:bundle|delta|state)(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*|expected_state_hash)\s*:",
            message,
        )
        if match:
            return match.group("path")
        # Normalizers include a concrete record ID in this form.  Restrict the
        # capture to unambiguous conventional IDs rather than returning an
        # arbitrary user-controlled string as a structured path.
        match = re.match(
            r"^(?P<path>record\['[A-Za-z0-9:_-]+'\]\.data(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*:",
            message,
        )
        return match.group("path") if match else None

    @staticmethod
    def _remember_check_category(message: str) -> str:
        """Map validator text to a small stable category without parsing science."""

        lowered = message.lower()
        if "delta_id" in lowered and ("already used" in lowered or "replay" in lowered):
            return "replay"
        if "expected_state_hash" in lowered or lowered.startswith("state"):
            return "state"
        if "template placeholder" in lowered:
            return "template"
        if any(token in lowered for token in ("reference", "unknown endpoint", "evidence ref", "must resolve")):
            return "reference"
        if any(token in lowered for token in ("assertion_role", "evidence_kind", "evidence_roles")):
            return "semantic"
        if "schema_version" in lowered:
            return "schema"
        return "validation"

    def _available_evidence_anchors(self, limit: int = 12) -> list[dict[str, str]]:
        """Return a bounded, typed anchor inventory only for reference repair."""

        rows = self.conn.execute(
            "SELECT id,type FROM records WHERE type IN ('source','paper','experiment') ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        field_for_type = {"source": "source_id", "paper": "paper_id", "experiment": "experiment_id"}
        return [
            {"id": row["id"], "type": row["type"], "field": field_for_type[row["type"]]}
            for row in rows
        ]

    def check_delta(self, delta: dict) -> dict[str, Any]:
        """Validate a ``remember`` delta in the live store and roll back every write.

        The check deliberately exercises the same transaction as ``apply_delta``
        (including delta-id replay and state compare-and-swap).  It is a typing
        aid for authored research memory, not an auto-repair or a weaker import
        path.  Ordinary validation errors are data so a Codex session can fix a
        compact delta without losing its whole trajectory.
        """

        try:
            would_apply = self._apply_delta(delta, dry_run=True)
        except ValueError as exc:
            message = str(exc)
            category = self._remember_check_category(message)
            error: dict[str, str] = {"category": category, "message": message}
            path = self._remember_check_path(message)
            if path is not None:
                error["path"] = path
            result: dict[str, Any] = {
                "valid": False,
                "error": error,
                "guidance": self._remember_check_contract(),
            }
            if category == "reference":
                anchors = self._available_evidence_anchors()
                if anchors:
                    result["available_anchors"] = anchors
                result["next_step"] = (
                    "Use a compatible stored anchor or include its matching source, paper, or experiment record in the same delta."
                )
            elif category == "state":
                result["next_step"] = "Refresh the current state hash before submitting a delta that uses expected_state_hash."
            elif category == "template":
                result["next_step"] = (
                    "Replace every __SRH_TEMPLATE_*__ placeholder with inspected source material, then run this check again."
                )
            else:
                result["next_step"] = "Correct the authored delta, run this check again, then submit it with srh remember FILE."
            return result
        return {
            "valid": True,
            "would_apply": would_apply,
            "guidance": self._remember_check_contract(),
        }

    def apply_delta(self, delta: dict) -> dict:
        """Atomically apply a Codex semantic delta and explicit review receipts."""

        return self._apply_delta(delta, dry_run=False)

    def _apply_delta(self, delta: dict, *, dry_run: bool) -> dict:
        """Run the one strict delta path, committing only when requested.

        ``dry_run`` intentionally reaches the ordinary write calls before a
        database rollback.  This makes preflight cover the exact replay,
        optimistic-concurrency, reference, receipt, semantic, and state path
        used by a real ``remember`` while leaving no durable record, edge,
        event, state, or retry receipt behind.
        """
        if not isinstance(delta, dict):
            raise ValueError("delta must be an object")
        if delta.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"delta.schema_version must be {SCHEMA_VERSION}")
        if not isinstance(delta.get("records"), list) or not isinstance(delta.get("edges"), list):
            raise ValueError("delta.records and delta.edges must be arrays")
        patch = delta.get("state_patch", {})
        if not isinstance(patch, dict):
            raise ValueError("delta.state_patch must be an object")
        expected = delta.get("expected_state_hash")
        record_guards = delta.get("expected_record_hashes", {})
        if not isinstance(record_guards, dict) or any(
            not isinstance(key, str) or not key or (value is not None and (
                not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            )) for key, value in record_guards.items()
        ):
            raise ValueError("delta.expected_record_hashes must map record IDs to SHA-256 strings or null (absent)")
        if expected is not None and (not isinstance(expected, str) or len(expected) != 64):
            raise ValueError("delta.expected_state_hash must be a SHA-256 hash string")
        reviewed_ids = delta.get("reviewed_ids", [])
        if not isinstance(reviewed_ids, list) or any(not isinstance(item, str) or not item for item in reviewed_ids):
            raise ValueError("delta.reviewed_ids must be a list of entity IDs")
        note = delta.get("review_note")
        if reviewed_ids and (not isinstance(note, str) or not note.strip()):
            raise ValueError("delta.review_note is required when reviewed_ids are supplied")
        if note is not None and not isinstance(note, str):
            raise ValueError("delta.review_note must be a string")
        delta_id = delta.get("delta_id")
        if delta_id is not None and (not isinstance(delta_id, str) or not delta_id.strip()):
            raise ValueError("delta.delta_id must be a non-empty string")
        # Hash the author's complete incoming request before system-generated
        # basis receipts exist.  Canonical JSON makes replay independent of
        # dictionary ordering while treating any semantic input change as a
        # conflict for the same durable key.
        request_hash = content_hash(delta) if delta_id is not None else None
        records, edges = normalize_bundle({"schema_version": SCHEMA_VERSION, "records": delta["records"], "edges": delta["edges"]})
        counts, now = self._counts(), _now()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            if delta_id is not None:
                prior = self.conn.execute(
                    "SELECT payload_json FROM events WHERE event_type='delta.applied' AND entity_id=? ORDER BY seq DESC LIMIT 1",
                    (delta_id,),
                ).fetchone()
                if prior is not None:
                    prior_payload = json.loads(prior[0])
                    if prior_payload.get("input_hash") != request_hash:
                        raise ValueError(f"delta_id {delta_id!r} was already used with different content")
                    result = prior_payload.get("result")
                    if not isinstance(result, dict):
                        raise ValueError(f"delta_id {delta_id!r} has an invalid replay event")
                    if dry_run:
                        self.conn.rollback()
                    else:
                        self.conn.commit()
                    replayed = _copy(result)
                    replayed["replayed"] = True
                    if dry_run and isinstance(replayed.get("writeback"), dict):
                        replayed["writeback"]["status"] = "preview"
                    return replayed
            for record_id, expected_hash in record_guards.items():
                row = self.conn.execute("SELECT payload_json FROM records WHERE id=?", (record_id,)).fetchone()
                actual_hash = content_hash(json.loads(row[0])) if row is not None else None
                if actual_hash != expected_hash:
                    raise ValueError(f"expected_record_hashes: record {record_id!r} changed; refresh before writing")
            current_state = self._current_state()
            if expected is not None and expected != content_hash(current_state):
                raise ValueError("expected_state_hash does not match the current state")
            old_records, old_edges = self._records(), self._edges()
            incoming_records = {item["id"]: _copy(item) for item in records}
            incoming_edges = {item["id"]: _copy(item) for item in edges}
            final_records, final_edges = {**old_records, **incoming_records}, {**old_edges, **incoming_edges}
            reviewed_records, reviewed_edges = self._resolve_review_ids(reviewed_ids, final_records, final_edges)
            for record_id in reviewed_records:
                incoming_records.setdefault(record_id, _copy(old_records.get(record_id, final_records[record_id])))
            for edge_id in reviewed_edges:
                incoming_edges.setdefault(edge_id, _copy(old_edges.get(edge_id, final_edges[edge_id])))
            # Existing edits keep baselines/receipts exactly.  This intentionally
            # leaves a newly introduced ref untracked until a selected review.
            for record_id, record in incoming_records.items():
                old = old_records.get(record_id)
                if old is not None and record_id not in reviewed_records:
                    for field in ("basis", "basis_review"):
                        if isinstance(old.get("data", {}).get(field), dict):
                            record["data"][field] = _copy(old["data"][field])
                        else:
                            record["data"].pop(field, None)
            for edge_id, edge in incoming_edges.items():
                old = old_edges.get(edge_id)
                if old is not None and edge_id not in reviewed_edges:
                    for field in ("basis", "basis_review"):
                        if isinstance(old.get(field), dict):
                            edge[field] = _copy(old[field])
                        else:
                            edge.pop(field, None)
            final_records = {**old_records, **incoming_records}
            # Locked validation before hash capture sees all incoming endpoint/source payloads.
            types = self._validate_refs(list(incoming_records.values()), list(incoming_edges.values()), strict_new_assertions=True)
            reviewed_at = _review_now()
            # The receipt is part of the reviewed entity's content hash.  Put
            # all receipts in place before capturing any basis so reviewing an
            # evidence item and its claim in one delta records a genuinely
            # current claim baseline.  ``basis`` itself remains excluded from
            # dependency hashes, so this has no recursive hash problem.
            for record_id in reviewed_records:
                incoming_records[record_id]["data"]["basis_review"] = {"note": note.strip(), "reviewed_at": reviewed_at}
            for edge_id in reviewed_edges:
                incoming_edges[edge_id]["basis_review"] = {"note": note.strip(), "reviewed_at": reviewed_at}
            final_records = {**old_records, **incoming_records}
            for record_id, record in incoming_records.items():
                if record_id not in old_records or record_id in reviewed_records:
                    record["data"]["basis"] = {ref: dependency_content_hash(final_records[ref]) for ref in record_dependency_refs(record) if ref in final_records}
            for edge_id, edge in incoming_edges.items():
                if edge_id not in old_edges or edge_id in reviewed_edges:
                    edge["basis"] = {ref: dependency_content_hash(final_records[ref]) for ref in edge_dependency_refs(edge) if ref in final_records}
            types = self._validate_refs(list(incoming_records.values()), list(incoming_edges.values()), strict_new_assertions=True)
            next_state = self._validate_state({**current_state, **patch}, types)
            receipt = self._write_entities(list(incoming_records.values()), list(incoming_edges.values()), counts, now,
                                           receipt_limit=64)
            counts["writeback"] = {"status": "preview" if dry_run else "committed", **receipt}
            if next_state != current_state:
                self.conn.execute("UPDATE state SET value_json=? WHERE id=1", (canonical_json(next_state),))
                self.conn.execute("INSERT INTO events(event_type,entity_id,payload_json,created_at) VALUES(?,?,?,?)", ("state.updated", "state", canonical_json({"patch": patch, "state": next_state}), now))
                counts["state_updated"] = 1
            for record_id in sorted(reviewed_records):
                self.conn.execute("INSERT INTO events(event_type,entity_id,payload_json,created_at) VALUES(?,?,?,?)", ("dependency.reviewed", record_id, canonical_json({"kind": "record", "note": note.strip()}), now))
            for edge_id in sorted(reviewed_edges):
                self.conn.execute("INSERT INTO events(event_type,entity_id,payload_json,created_at) VALUES(?,?,?,?)", ("dependency.reviewed", edge_id, canonical_json({"kind": "edge", "note": note.strip()}), now))
            counts["state_hash"] = content_hash(next_state)
            if delta_id is not None:
                self.conn.execute(
                    "INSERT INTO events(event_type,entity_id,payload_json,created_at) VALUES(?,?,?,?)",
                    ("delta.applied", delta_id, canonical_json({"input_hash": request_hash, "result": counts}), now),
                )
            if dry_run:
                self.conn.rollback()
            else:
                self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        counts["state_hash"] = content_hash(next_state)
        return counts

    def get_record(self, record_id: str) -> dict | None:
        row = self.conn.execute("SELECT payload_json FROM records WHERE id=?", (record_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_records(self, record_type: str | None = None, limit: int = 100) -> list[dict]:
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        sql = "SELECT payload_json FROM records" + (" WHERE type=?" if record_type else "") + " ORDER BY id LIMIT ?"
        values: tuple[Any, ...] = (record_type, limit) if record_type else (limit,)
        return [json.loads(row[0]) for row in self.conn.execute(sql, values)]

    def get_state(self) -> dict:
        return self._current_state()

    def decision_revisions(self, decision_id: str, limit: int = 2) -> dict:
        """Read bounded outgoing revision links without loading the graph.

        Authored lineage is not a truth judgment or a choice of current route.
        The payload is bounded; on older databases the SQL may scan edges.
        Reading never migrates schemas or creates an index.
        """
        if type(limit) is not int or limit <= 0 or limit > 12:
            raise ValueError("limit must be an integer from 1 to 12")
        rows = self.conn.execute(
            "SELECT payload_json FROM edges WHERE source=? AND relation='revises' ORDER BY id LIMIT ?",
            (decision_id, limit + 1),
        ).fetchall()
        return {"edges": [json.loads(row[0]) for row in rows[:limit]], "truncated": len(rows) > limit}

    def state_hash(self) -> str:
        return content_hash(self._current_state())

    def update_state(self, patch: dict, expected_hash: str | None = None) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("state patch must be an object")
        if expected_hash is not None and (not isinstance(expected_hash, str) or len(expected_hash) != 64):
            raise ValueError("expected_hash must be a SHA-256 hash string")
        now = _now()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            current = self._current_state()
            if expected_hash is not None and expected_hash != content_hash(current):
                raise ValueError("expected_hash does not match the current state")
            next_state = self._validate_state({**current, **patch}, self._all_ids())
            if next_state != current:
                self.conn.execute("UPDATE state SET value_json=? WHERE id=1", (canonical_json(next_state),))
                self.conn.execute("INSERT INTO events(event_type,entity_id,payload_json,created_at) VALUES(?,?,?,?)", ("state.updated", "state", canonical_json({"patch": patch, "state": next_state}), now))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return next_state

    def _provenance_fields(self, record: dict) -> list[tuple[str, str]]:
        data, typ = record.get("data", {}), record.get("type")
        refs: list[tuple[str, str]] = []
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

    def graph(self, seed: str | None = None, hops: int = 1, limit: int = 80) -> dict:
        if not isinstance(hops, int) or hops < 0 or not isinstance(limit, int) or limit <= 0:
            raise ValueError("hops must be >= 0 and limit must be positive")
        backing_truncated, stopped_for_limit = False, False
        if seed is None:
            rows = self.conn.execute("SELECT payload_json FROM records ORDER BY id LIMIT ?", (limit + 1,)).fetchall()
            records = {item["id"]: item for item in (json.loads(row[0]) for row in rows[:limit])}
            selected, truncated = set(records), len(rows) > limit
            if selected:
                marks = ",".join("?" * len(selected))
                edge_rows = self.conn.execute(f"SELECT id,payload_json FROM edges WHERE source IN ({marks}) AND target IN ({marks}) ORDER BY id", tuple(selected) * 2).fetchall()
                edges = {row["id"]: json.loads(row["payload_json"]) for row in edge_rows}
            else:
                edges = {}
        else:
            # The focus loader uses bounded SQL reverse-reference traversal;
            # graph/context no longer deserialize every historical payload.
            scoped_records, scoped_edges, backing_truncated = self._quality_focus_payloads(seed, max_entities=max(500, limit * 6), include_navigation=True)
            records = {item["id"]: item for item in scoped_records}
            edges = {item["id"]: item for item in scoped_edges}
            if seed not in records:
                return {"records": [], "edges": [], "provenance": [], "truncated": False,
                        "boundary": {"hops": hops, "more_connections": "unknown",
                                     "continuation_ids": [], "continuation_ids_truncated": False}}
            selected, frontier, truncated = {seed}, {seed}, backing_truncated
            reverse: dict[str, set[str]] = {}
            for record_id, record in records.items():
                for ref in record_navigation_refs(record):
                    reverse.setdefault(ref, set()).add(record_id)
            for _ in range(hops):
                if not frontier:
                    break
                if len(selected) >= limit:
                    stopped_for_limit = True
                    break
                nearby: set[str] = set()
                for record_id in frontier:
                    nearby.update(record_navigation_refs(records[record_id]))
                    nearby.update(reverse.get(record_id, set()))
                    for edge in edges.values():
                        if edge["source"] == record_id:
                            nearby.add(edge["target"]); nearby.update(edge.get("evidence_ids", []))
                        elif edge["target"] == record_id:
                            nearby.add(edge["source"]); nearby.update(edge.get("evidence_ids", []))
                        # Usage roles can live on an assertion edge rather
                        # than either endpoint's data.evidence_ids. A saved
                        # evidence item must navigate back to that actual owner.
                        elif record_id in edge.get("evidence_ids", []):
                            nearby.update((edge["source"], edge["target"]))
                            nearby.update(edge.get("evidence_ids", []))
                nearby = {item for item in nearby if item in records and item not in selected}
                room, ordered = limit - len(selected), sorted(nearby)
                if len(ordered) > room:
                    truncated = True
                frontier = set(ordered[:room])
                selected.update(frontier)
        # A requested hop radius is not an exhaustive literature/route search.
        # Reuse the bounded loaded component; do not scan more records, infer
        # an unseen conflict, or auto-expand the caller's selected neighborhood.
        continuation_ids: set[str] = set()
        if seed is not None:
            for record_id, record in records.items():
                refs = set(record_navigation_refs(record))
                if record_id in selected:
                    if any(ref in records and ref not in selected for ref in refs):
                        continuation_ids.add(record_id)
                else:
                    continuation_ids.update(refs & selected)
            for edge in edges.values():
                connected = {edge["source"], edge["target"], *edge.get("evidence_ids", [])}
                if any(ref in records and ref not in selected for ref in connected):
                    continuation_ids.update(connected & selected)
        ordered_continuations = sorted(continuation_ids)
        if stopped_for_limit and continuation_ids:
            truncated = True
        boundary = {
            "hops": hops if seed is not None else None,
            "more_connections": ("known" if continuation_ids else "unknown"
                                 if backing_truncated or seed is None else "none_in_loaded_graph"),
            "continuation_ids": ordered_continuations[:8],
            "continuation_ids_truncated": len(ordered_continuations) > 8,
        }
        explicit_edges = [edge for _edge_id, edge in sorted(edges.items()) if edge["source"] in selected and edge["target"] in selected]
        provenance = [{"source": record_id, "target": ref, "field": field, "kind": "record_ref"}
                      for record_id in sorted(selected) for ref, field in self._provenance_fields(records[record_id]) if ref in selected]
        return {"records": [records[item] for item in sorted(selected)], "edges": explicit_edges,
                "provenance": provenance, "truncated": bool(truncated), "boundary": boundary}

    def _lexical(self, query: str, limit: int, record_type: str | None) -> list[tuple[str, float]]:
        terms = [item for item in query.replace('"', " ").split() if item]
        if not terms:
            return []
        match = " AND ".join('"' + item.replace('"', " ") + '"*' for item in terms)
        try:
            sql, values = "SELECT f.record_id,bm25(records_fts) rank FROM records_fts f JOIN records r ON r.id=f.record_id WHERE records_fts MATCH ?", [match]
            if record_type:
                sql += " AND r.type=?"; values.append(record_type)
            sql += " ORDER BY rank LIMIT ?"; values.append(limit)
            rows = self.conn.execute(sql, values).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(row[0], 1.0 / rank) for rank, row in enumerate(rows, 1)]

    def _vector(self, query: str, limit: int, record_type: str | None) -> list[tuple[str, float]]:
        if not self._vec_available:
            raise EmbeddingError("sqlite-vec extension unavailable; vector search cannot run")
        provider = json.loads(self.conn.execute("SELECT value FROM metadata WHERE key='provider'").fetchone()[0])
        model = json.loads(self.conn.execute("SELECT value FROM metadata WHERE key='model'").fetchone()[0])
        dimension = int(json.loads(self.conn.execute("SELECT value FROM metadata WHERE key='dimension'").fetchone()[0]))
        count = self.conn.execute("SELECT count(*) FROM vector_meta").fetchone()[0]
        if count == 0:
            raise EmbeddingError("vector index is empty; run explicit index build")
        if self.conn.execute("SELECT 1 FROM vector_meta WHERE provider<>? OR model<>? OR dimension<>? LIMIT 1", (provider, model, dimension)).fetchone():
            raise EmbeddingError("vector index identity metadata is inconsistent; rebuild explicitly")
        try:
            vector = make_embedder(provider, model, allow_download=False, dimension=dimension, cache_dir=self.model_cache_dir).embed_one(query)
            if len(vector) != dimension or any(not math.isfinite(float(value)) for value in vector):
                raise EmbeddingError("embedding provider returned an invalid vector")
            rows = self.conn.execute("SELECT v.record_id,v.distance FROM vec_records v JOIN vector_meta m ON m.record_id=v.record_id JOIN records r ON r.id=v.record_id WHERE v.embedding MATCH ? AND v.k=? AND m.content_hash=r.content_hash", (self._serialize(vector), count)).fetchall()
            output = [(row[0], 1.0 / (1.0 + float(row[1]))) for row in rows]
            if record_type:
                allowed = {row["id"] for row in self.conn.execute("SELECT id FROM records WHERE type=?", (record_type,))}
                output = [(item, score) for item, score in output if item in allowed]
            return output[:limit]
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"vector channel unavailable: {exc}") from exc

    @staticmethod
    def _serialize(vector: list[float]) -> bytes:
        import sqlite_vec
        if not vector or any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in vector):
            raise EmbeddingError("vector contains no values or non-finite values")
        return sqlite_vec.serialize_float32(vector)

    def _neighbors(self, record_id: str) -> list[dict]:
        records, record = self._records(), self.get_record(record_id)
        if not record:
            return []
        nearby = set(record_navigation_refs(record))
        for edge in self._edges().values():
            if edge["source"] == record_id:
                nearby.add(edge["target"]); nearby.update(edge.get("evidence_ids", []))
            elif edge["target"] == record_id:
                nearby.add(edge["source"]); nearby.update(edge.get("evidence_ids", []))
        for item in list(nearby):
            if records.get(item, {}).get("type") == "evidence":
                nearby.update(record_navigation_refs(records[item]))
        return [records[item] for item in sorted(nearby) if item in records and item != record_id][:20]

    def _index_freshness(self) -> tuple[int, int]:
        row = self.conn.execute("SELECT sum(CASE WHEN m.record_id IS NOT NULL AND m.content_hash=r.content_hash THEN 1 ELSE 0 END),sum(CASE WHEN m.record_id IS NULL OR m.content_hash<>r.content_hash THEN 1 ELSE 0 END) FROM records r LEFT JOIN vector_meta m ON m.record_id=r.id").fetchone()
        return int(row[0] or 0), int(row[1] or 0)

    def search(self, query: str, mode: str = "hybrid", limit: int = 10, record_type: str | None = None) -> dict:
        if mode not in {"lexical", "vector", "hybrid"}:
            raise ValueError("mode must be lexical, vector, or hybrid")
        if not isinstance(query, str) or not query.strip() or not isinstance(limit, int) or limit <= 0:
            raise ValueError("query must not be empty and limit must be positive")
        warnings: list[str] = []
        _fresh, unindexed = self._index_freshness()
        if unindexed and mode in {"vector", "hybrid"}:
            warnings.append(f"{unindexed} {'record needs' if unindexed == 1 else 'records need'} explicit vector indexing; stale vectors are excluded")
        lexical = self._lexical(query, limit * 4, record_type) if mode in {"lexical", "hybrid"} else []
        vector: list[tuple[str, float]] = []
        if mode in {"vector", "hybrid"}:
            try:
                vector = self._vector(query, limit * 4, record_type)
            except EmbeddingError as exc:
                if mode == "vector":
                    raise
                warnings.append(str(exc))
        scores: dict[str, float] = {}; channels: dict[str, dict[str, float]] = {}
        for name, rows in (("lexical", lexical), ("vector", vector)):
            for rank, (record_id, score) in enumerate(rows):
                scores[record_id] = scores.get(record_id, 0.0) + (1 / (61 + rank) if mode == "hybrid" else score)
                channels.setdefault(record_id, {})[name] = score
        results = []
        for record_id in sorted(scores, key=lambda item: (-scores[item], item))[:limit]:
            record = self.get_record(record_id)
            if record:
                results.append({"record": record, "score": scores[record_id], "channels": channels.get(record_id, {}), "neighbors": self._neighbors(record_id), "evidence_refs": record.get("data", {}).get("evidence_ids", [])})
        if mode == "hybrid" and not lexical: warnings.append("lexical channel returned no matches")
        if mode == "hybrid" and not vector: warnings.append("vector channel returned no matches")
        identity = self._index_identity()
        identity.update({"semantic": identity["provider"] != "hash", "label": "offline lexical feature hashing only; not semantic understanding" if identity["provider"] == "hash" else "local embedding provider; relevance is not scientific confidence"})
        return {"results": results, "warnings": warnings, "mode": mode, "embedding": identity}

    def _index_identity(self) -> dict[str, Any]:
        values = dict(self.conn.execute("SELECT key,value FROM metadata WHERE key IN ('provider','model','dimension')"))
        return {"provider": json.loads(values.get("provider", json.dumps(DEFAULT_PROVIDER))), "model": json.loads(values.get("model", json.dumps(DEFAULT_MODEL))), "dimension": int(json.loads(values.get("dimension", str(DEFAULT_DIMENSION))))}

    def build_index(self, provider: str | None = None, model: str | None = None) -> dict:
        old_provider = self.conn.execute("SELECT value FROM metadata WHERE key='provider'").fetchone()
        old_model = self.conn.execute("SELECT value FROM metadata WHERE key='model'").fetchone()
        requested = provider; provider = provider or (json.loads(old_provider[0]) if old_provider else DEFAULT_PROVIDER)
        # An explicit hash model is validated by the provider too.  Silently
        # dropping it would turn a malformed requested identity into a valid
        # default build and discard the caller's safety check.
        model = (model or None) if provider == "hash" else (model or (DEFAULT_MODEL if requested else (json.loads(old_model[0]) if old_model else DEFAULT_MODEL)))
        records = [(row["id"], row["content_hash"], json.loads(row["payload_json"])) for row in self.conn.execute("SELECT id,content_hash,payload_json FROM records ORDER BY id")]
        embedder = make_embedder(provider, model, allow_download=True, cache_dir=self.model_cache_dir)
        vectors = embedder.embed([record_text(item[2]) for item in records])
        if len(vectors) != len(records) or (vectors and any(len(vector) != embedder.identity.dimension for vector in vectors)) or any(not math.isfinite(float(value)) for vector in vectors for value in vector):
            raise EmbeddingError("embedding provider returned invalid vectors")
        if not self._vec_available:
            raise EmbeddingError("sqlite-vec extension unavailable")
        dimension = embedder.identity.dimension
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            old_dimension = int(json.loads(self.conn.execute("SELECT value FROM metadata WHERE key='dimension'").fetchone()[0]))
            if old_dimension != dimension:
                self.conn.execute("DROP TABLE vec_records")
                self.conn.execute(f"CREATE VIRTUAL TABLE vec_records USING vec0(record_id TEXT PRIMARY KEY, embedding FLOAT[{dimension}] distance_metric=cosine)")
            self.conn.execute("DELETE FROM vec_records"); self.conn.execute("DELETE FROM vector_meta")
            for (record_id, _digest, _record), vector in zip(records, vectors):
                self.conn.execute("INSERT INTO vec_records(record_id,embedding) VALUES (?,?)", (record_id, self._serialize(vector)))
            for record_id, digest, _record in records:
                self.conn.execute("INSERT INTO vector_meta(record_id,content_hash,provider,model,dimension) VALUES(?,?,?,?,?)", (record_id, digest, embedder.identity.provider, embedder.identity.model, dimension))
            for key, value in (("provider", embedder.identity.provider), ("model", embedder.identity.model), ("dimension", str(dimension))):
                self.conn.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", (key, json.dumps(value)))
            self.conn.execute("INSERT INTO events(event_type,entity_id,payload_json,created_at) VALUES(?,?,?,?)", ("index.rebuilt", "index", canonical_json({"provider": embedder.identity.provider, "model": embedder.identity.model, "dimension": dimension, "count": len(records)}), _now()))
            self.conn.commit()
        except Exception:
            self.conn.rollback(); raise
        return {"provider": embedder.identity.provider, "model": embedder.identity.model, "dimension": dimension, "count": len(records)}

    def _quality_focus_payloads(self, focus: str, max_entities: int = 500, *, include_navigation: bool = False) -> tuple[list[dict], list[dict], bool]:
        """Load a bounded provenance neighbourhood without deserializing the corpus.

        SQLite's JSON functions locate the reverse typed references.  This is
        deliberately a graph-maintenance traversal, not a text/title search;
        an unknown focus stays unknown rather than accidentally widening to a
        similarly named record.
        """
        if not isinstance(focus, str):
            return [], [], False
        records: dict[str, dict] = {}
        edges: dict[str, dict] = {}

        def record_row(record_id: str) -> dict | None:
            row = self.conn.execute("SELECT payload_json FROM records WHERE id=?", (record_id,)).fetchone()
            return json.loads(row[0]) if row else None

        def edge_row(edge_id: str) -> dict | None:
            row = self.conn.execute("SELECT payload_json FROM edges WHERE id=?", (edge_id,)).fetchone()
            return json.loads(row[0]) if row else None

        literal_record, literal_edge = record_row(focus), edge_row(focus)
        starts: list[tuple[str, str, dict]] = []
        if literal_record is not None:
            starts.append(("record", focus, literal_record))
        if literal_edge is not None:
            starts.append(("edge", focus, literal_edge))
        if not starts and focus.startswith("record:"):
            candidate = focus[7:]; item = record_row(candidate)
            if item is not None: starts.append(("record", candidate, item))
        if not starts and focus.startswith("edge:"):
            candidate = focus[5:]; item = edge_row(candidate)
            if item is not None: starts.append(("edge", candidate, item))
        if not starts:
            return [], [], False
        queue: list[tuple[str, str]] = []
        for kind, entity_id, item in starts:
            if kind == "record": records[entity_id] = item
            else: edges[entity_id] = item
            queue.append((kind, entity_id))
        truncated = False

        def add_record(record_id: str, item: dict | None = None) -> None:
            nonlocal truncated
            if record_id in records:
                return
            if len(records) + len(edges) >= max_entities:
                truncated = True; return
            item = item if item is not None else record_row(record_id)
            if item is not None:
                records[record_id] = item; queue.append(("record", record_id))

        def add_edge(edge_id: str, item: dict | None = None) -> None:
            nonlocal truncated
            if edge_id in edges:
                return
            if len(records) + len(edges) >= max_entities:
                truncated = True; return
            item = item if item is not None else edge_row(edge_id)
            if item is not None:
                edges[edge_id] = item; queue.append(("edge", edge_id))

        while queue:
            kind, entity_id = queue.pop(0)
            if kind == "edge":
                for ref in edge_dependency_refs(edges[entity_id]):
                    add_record(ref)
                continue
            item = records[entity_id]
            refs = record_navigation_refs(item) if include_navigation else record_dependency_refs(item)
            for ref in refs:
                add_record(ref)
            # Reverse typed refs, evidence lists, and explicit graph edges.
            remaining = max_entities - len(records) - len(edges)
            if remaining <= 0:
                truncated = True
                continue
            record_rows = self.conn.execute(
                "SELECT id,payload_json FROM records WHERE "
                "json_extract(data_json,'$.source_id')=? OR json_extract(data_json,'$.paper_id')=? OR json_extract(data_json,'$.experiment_id')=? "
                "OR json_extract(data_json,'$.hypothesis_id')=? OR EXISTS "
                "(SELECT 1 FROM json_each(data_json,'$.evidence_ids') WHERE value=?) ORDER BY id LIMIT ?",
                (entity_id, entity_id, entity_id, entity_id, entity_id, remaining + 1),
            ).fetchall()
            if len(record_rows) > remaining:
                truncated = True
            for row in record_rows[:remaining]:
                add_record(row["id"], json.loads(row["payload_json"]))
            remaining = max_entities - len(records) - len(edges)
            if remaining <= 0:
                truncated = True
                continue
            edge_rows = self.conn.execute(
                "SELECT id,payload_json FROM edges WHERE source=? OR target=? OR EXISTS "
                "(SELECT 1 FROM json_each(evidence_json) WHERE value=?) ORDER BY id LIMIT ?",
                (entity_id, entity_id, entity_id, remaining + 1),
            ).fetchall()
            if len(edge_rows) > remaining:
                truncated = True
            for row in edge_rows[:remaining]:
                add_edge(row["id"], json.loads(row["payload_json"]))
        return list(records.values()), list(edges.values()), truncated

    def _identifier_stubs(self) -> list[dict]:
        """Fetch only DOI/arXiv-bearing data for global duplicate candidates."""
        rows = self.conn.execute("SELECT id,json_extract(data_json,'$.identifiers'),json_extract(data_json,'$.aliases') FROM records WHERE type='paper' ORDER BY id").fetchall()
        stubs: list[dict] = []
        for row in rows:
            identifiers = json.loads(row[1]) if isinstance(row[1], str) else {}
            aliases = json.loads(row[2]) if isinstance(row[2], str) else []
            stubs.append({"id": row["id"], "type": "paper", "data": {"identifiers": identifiers or {}, "aliases": aliases or []}})
        return stubs

    def _known_types_for(self, records: list[dict], edges: list[dict]) -> dict[str, str]:
        """Cheap type lookup for references omitted by a bounded traversal."""
        wanted: set[str] = set()
        for record in records:
            wanted.update(ref for ref, _expected in self._typed_refs(record) if isinstance(ref, str) and ref)
            basis = record.get("data", {}).get("basis") if isinstance(record.get("data"), dict) else {}
            if isinstance(basis, dict): wanted.update(ref for ref in basis if isinstance(ref, str))
        for edge in edges:
            wanted.update(edge_dependency_refs(edge))
            basis = edge.get("basis")
            if isinstance(basis, dict): wanted.update(ref for ref in basis if isinstance(ref, str))
        present = {record["id"] for record in records}
        unresolved = sorted(wanted - present)
        types: dict[str, str] = {}
        for start in range(0, len(unresolved), 900):
            chunk = unresolved[start:start + 900]
            if not chunk: continue
            marks = ",".join("?" * len(chunk))
            for row in self.conn.execute(f"SELECT id,type FROM records WHERE id IN ({marks})", chunk):
                types[row["id"]] = row["type"]
        return types

    def quality(self, focus: str | None = None, limit: int = 100) -> dict:
        if focus is None:
            return assess_quality(self.list_records(limit=1_000_000), [json.loads(row[0]) for row in self.conn.execute("SELECT payload_json FROM edges ORDER BY id")], None, limit)
        records, edges, scope_truncated = self._quality_focus_payloads(focus)
        report = assess_quality(records, edges, focus, limit, identifier_records=self._identifier_stubs(), known_types=self._known_types_for(records, edges))
        report["truncated"] = bool(report["truncated"] or scope_truncated)
        return report

    def stats(self) -> dict:
        fresh, stale = self._index_freshness()
        return {"records": self.conn.execute("SELECT count(*) FROM records").fetchone()[0], "edges": self.conn.execute("SELECT count(*) FROM edges").fetchone()[0], "events": self.conn.execute("SELECT count(*) FROM events").fetchone()[0], "vectors": self.conn.execute("SELECT count(*) FROM vector_meta").fetchone()[0], "fresh_vectors": fresh, "unindexed_records": stale, "vector_available": self._vec_available, "provider": json.loads(self.conn.execute("SELECT value FROM metadata WHERE key='provider'").fetchone()[0]), "model": json.loads(self.conn.execute("SELECT value FROM metadata WHERE key='model'").fetchone()[0])}

    def events(self, limit: int = 30) -> list[dict]:
        if not isinstance(limit, int) or limit <= 0: raise ValueError("limit must be positive")
        rows = self.conn.execute("SELECT seq,event_type,entity_id,payload_json,created_at FROM events ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [{"seq": row[0], "event_type": row[1], "entity_id": row[2], "payload": json.loads(row[3]), "created_at": row[4]} for row in rows]

    def export_bundle(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "records": self.list_records(limit=1_000_000), "edges": [json.loads(row[0]) for row in self.conn.execute("SELECT payload_json FROM edges ORDER BY id")], "state": self.get_state()}
