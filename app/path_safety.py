from __future__ import annotations

"""Project-scoped path safety helpers for read/write/search operations."""

from pathlib import Path
from typing import Iterable


class PathSecurityError(ValueError):
    """Raised when a path tries to escape the project root."""


def _resolved_root(root: Path) -> Path:
    return root.resolve(strict=True)


def validate_project_path(root: Path, target: str | Path, *, allow_root: bool = True) -> Path:
    """Resolve and validate a path so it always stays inside the project root.

    Supports both relative and absolute inputs, and rejects traversal, absolute
    out-of-root access, and symlink-based escapes.
    """
    root_resolved = _resolved_root(root)
    raw = Path(target)
    candidate = raw if raw.is_absolute() else root_resolved / raw

    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:  # pragma: no cover - defensive guard
        raise PathSecurityError(f"Invalid path: {target}") from exc

    in_scope = resolved == root_resolved or root_resolved in resolved.parents
    if not in_scope:
        raise PathSecurityError(
            f"Path is outside project root. root={root_resolved} target={target} resolved={resolved}"
        )

    if not allow_root and resolved == root_resolved:
        raise PathSecurityError(f"Project root path is not allowed here: {target}")

    return resolved


def safe_read_text(root: Path, target: str | Path, *, encoding: str = "utf-8") -> str:
    resolved = validate_project_path(root, target)
    if not resolved.is_file():
        raise FileNotFoundError(f"File not found: {target}")
    return resolved.read_text(encoding=encoding)


def safe_write_text(root: Path, target: str | Path, content: str, *, encoding: str = "utf-8") -> None:
    resolved = validate_project_path(root, target, allow_root=False)
    if resolved.exists() and resolved.is_dir():
        raise IsADirectoryError(f"Target is a directory, not a file: {target}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding=encoding)


def safe_iter_files(root: Path, start: str | Path = ".") -> Iterable[Path]:
    start_path = validate_project_path(root, start)
    if start_path.is_file():
        yield start_path
        return
    for path in start_path.rglob("*"):
        try:
            resolved = validate_project_path(root, path)
        except PathSecurityError:
            continue
        if resolved.is_file():
            yield resolved
