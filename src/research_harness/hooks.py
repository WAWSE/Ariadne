"""Fast, offline Codex hook entrypoints."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import build_context, context_for_project
from .project import ProjectError, _atomic_write_project, _reject_symlink_escape


HOOK_TIMEOUT_SECONDS = 5
HOOK_CONTEXT_LIMIT = 2000


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def session_start(root: Path, max_chars: int = HOOK_CONTEXT_LIMIT) -> dict[str, Any]:
    try:
        # Context is lexical/offline and bounded; hooks never load an embedding
        # model, execute research, or synthesize an unwritten chat delta.
        context = context_for_project(
            root, max_chars=min(max_chars, HOOK_CONTEXT_LIMIT), intent="resume",
            include_details=True, include_quality=False,
        )
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
    except Exception as exc:
        # A hook must always return valid JSON and never prevent a host session.
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": f"Research context unavailable: {exc}"}}


def _snapshot(root: Path) -> None:
    """Refresh a bounded hook snapshot without model loading or network access."""
    from .store import ResearchStore

    _reject_symlink_escape(root, Path(".research"))
    _reject_symlink_escape(root, Path(".research/exports"))
    _reject_symlink_escape(root, Path(".research/exports/latest-hook-snapshot.json"))
    _reject_symlink_escape(root, Path(".research/exports/latest-hook-snapshot.md"))
    with ResearchStore(root) as store:
        context = build_context(
            store, max_chars=HOOK_CONTEXT_LIMIT, intent="resume",
            include_details=True, include_quality=False,
        )
        stats = store.stats()
    snapshot = {
        "snapshot_type": "bounded_hook_snapshot_v1",
        "context": context,
        "stats": stats,
        "note": "Snapshot contains saved store data only. Full exports require explicit `srh checkpoint` or `srh export`.",
    }
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    markdown = "# Bounded research hook snapshot\n\n" + context + "\n"
    _atomic_write_project(root, Path(".research/exports/latest-hook-snapshot.json"), payload)
    _atomic_write_project(root, Path(".research/exports/latest-hook-snapshot.md"), markdown.encode("utf-8"))


def stop(root: Path) -> dict[str, Any]:
    try:
        _snapshot(root)
        # Stop does not use hookSpecificOutput/additionalContext: that field is
        # only supported for SessionStart and a stop hook must not continue work.
        return {"systemMessage": "Research snapshot refreshed."}
    except Exception as exc:
        return {"systemMessage": f"Research snapshot unavailable: {exc}"}


def run(event: str, root: Path) -> dict[str, Any]:
    if event == "session-start":
        return session_start(root)
    if event == "stop":
        return stop(root)
    raise ValueError(f"unknown hook event: {event}")


def main(event: str, root: Path) -> int:
    print(_json(run(event, root)))
    return 0
