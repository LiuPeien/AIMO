from __future__ import annotations

"""Controlled executor for confirmed, small-scope file edits."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.path_safety import safe_read_text, safe_write_text, validate_project_path


class ExecutionValidationError(ValueError):
    """Raised when an execute request violates controlled constraints."""


@dataclass
class FileEdit:
    path: str
    new_content: str
    allow_create: bool = False
    expected_contains: str = ""


def _normalize_allowed(root: Path, allowed_files: list[str]) -> set[Path]:
    return {validate_project_path(root, item) for item in allowed_files}


def preflight_validate(
    root: Path,
    allowed_files: list[str],
    edits: list[FileEdit],
    *,
    max_files: int,
) -> list[dict[str, Any]]:
    """Validate all edits before writing anything."""
    if not edits:
        raise ExecutionValidationError("No edits provided.")

    if len(edits) > max_files:
        raise ExecutionValidationError(f"Too many edits in one task: {len(edits)} > {max_files}.")

    allowed = _normalize_allowed(root, allowed_files)
    checks: list[dict[str, Any]] = []

    for edit in edits:
        target = validate_project_path(root, edit.path)
        if target not in allowed:
            raise ExecutionValidationError(
                f"Target file is not listed in the approved plan: {edit.path}"
            )

        exists = target.exists()
        if not exists and not edit.allow_create:
            raise ExecutionValidationError(
                f"Target file does not exist and allow_create=False: {edit.path}"
            )

        original = safe_read_text(root, target) if exists else ""
        if edit.expected_contains and edit.expected_contains not in original:
            raise ExecutionValidationError(
                f"Target file content mismatch for {edit.path}: expected snippet not found."
            )

        checks.append(
            {
                "path": edit.path,
                "resolved_path": str(target),
                "existed": exists,
                "original_size": len(original),
                "new_size": len(edit.new_content),
                "original": original,
            }
        )

    return checks


def execute_edits(
    root: Path,
    allowed_files: list[str],
    edits: list[FileEdit],
    *,
    max_files: int = 5,
) -> dict[str, Any]:
    """Execute a small set of approved edits with strict pre-checks and no deletion."""
    checks = preflight_validate(root, allowed_files, edits, max_files=max_files)

    changed_files: list[dict[str, Any]] = []
    for check, edit in zip(checks, edits):
        safe_write_text(root, edit.path, edit.new_content)
        changed_files.append(
            {
                "path": edit.path,
                "created": not check["existed"],
                "old_size": check["original_size"],
                "new_size": check["new_size"],
            }
        )

    return {
        "applied": True,
        "changed_files": changed_files,
        "change_summary": {
            "total_files": len(changed_files),
            "created_files": sum(1 for item in changed_files if item["created"]),
            "updated_files": sum(1 for item in changed_files if not item["created"]),
        },
    }
