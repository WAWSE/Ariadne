"""The ``srh`` command line interface."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .context import INTENTS, context_for_project
from .hooks import run as run_hook
from .models import content_hash
from .project import ProjectError, _atomic_write_project, find_project, init_project


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
    p = sub.add_parser("show", help="show a record")
    p.add_argument("id")
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
    p = sub.add_parser("quality", help="report bounded mechanical/advisory data-quality findings")
    p.add_argument("--focus", metavar="ID")
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("remember", help="apply a reviewed research delta JSON file")
    p.add_argument("file", type=str, help="delta JSON file, or - for stdin")
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
    root = _project_for(args)
    if args.command == "hook":
        return run_hook(args.event, root)
    if args.command == "context":
        return context_for_project(root, query=args.query, max_chars=args.max_chars, intent=args.intent, focus=args.focus)
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
            return store.apply_delta(_read_json_file(args.file))
        if args.command == "show":
            value = store.get_record(args.id)
            if value is None:
                raise ProjectError("record not found")
            return value
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
            decision_id = f"decision:checkpoint-{stamp.lower()}"
            state = store.get_state()
            store.ingest({
                "schema_version": 1,
                "records": [{
                    "id": decision_id,
                    "type": "decision",
                    "title": "Checkpoint note",
                    "summary": note,
                    "tags": ["checkpoint"],
                    "data": {
                        "reason": note,
                        "evidence_ids": [],
                        "next_action": (state.get("next_action") or "Review the active research state.") if isinstance(state, dict) else "Review the active research state.",
                    },
                }],
                "edges": [],
            })
            bundle = store.export_bundle()
            exports = root / ".research" / "exports"
            exports.mkdir(parents=True, exist_ok=True)
            md = f"# Research checkpoint ({stamp})\n\n{note}\n\nRecords: {len(bundle.get('records', []))}; edges: {len(bundle.get('edges', []))}.\n"
            json_rel = Path(".research/exports") / f"checkpoint-{stamp}.json"
            md_rel = Path(".research/exports") / f"checkpoint-{stamp}.md"
            _atomic_write_project(root, json_rel, (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            _atomic_write_project(root, md_rel, md.encode("utf-8"))
            return {"export": str(exports / f"checkpoint-{stamp}.json"), "markdown": str(exports / f"checkpoint-{stamp}.md"), "decision_id": decision_id}
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
