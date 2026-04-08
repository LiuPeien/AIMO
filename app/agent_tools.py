from __future__ import annotations

"""Project-scoped local tools for controlled engineering actions."""

from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess
from typing import Any

from app.path_safety import safe_read_text, safe_write_text, validate_project_path


class ToolValidationError(ValueError):
    """Raised when a tool request violates security constraints."""


@dataclass(frozen=True)
class CommandPolicy:
    executable: str
    allowed_prefixes: tuple[tuple[str, ...], ...]


DEFAULT_COMMAND_POLICY = CommandPolicy(
    executable="local-runner",
    allowed_prefixes=(
        ("pytest",),
        ("python", "-m", "py_compile"),
        ("python", "-m", "unittest"),
        ("uvicorn", "app.main:app"),
    ),
)


class LocalToolbox:
    def __init__(self, root: Path, *, command_policy: CommandPolicy = DEFAULT_COMMAND_POLICY) -> None:
        self.root = validate_project_path(root, ".")
        self.command_policy = command_policy

    def _resolve(self, target: str | Path) -> Path:
        return validate_project_path(self.root, target)

    def list_dir(self, path: str = ".") -> dict[str, Any]:
        target = self._resolve(path)
        if not target.exists() or not target.is_dir():
            raise ToolValidationError(f"Directory not found: {path}")

        items: list[dict[str, Any]] = []
        for child in sorted(target.iterdir(), key=lambda p: p.name):
            rel = str(child.relative_to(self.root))
            items.append({"name": child.name, "path": rel, "type": "dir" if child.is_dir() else "file"})
        return {"path": str(target.relative_to(self.root)) if target != self.root else ".", "items": items}

    def read_file(self, path: str) -> dict[str, Any]:
        text = safe_read_text(self.root, path)
        resolved = self._resolve(path)
        return {"path": str(resolved.relative_to(self.root)), "content": text}

    def search_code(self, query: str, *, max_results: int = 20) -> dict[str, Any]:
        if not query.strip():
            raise ToolValidationError("query must not be empty")

        matches: list[dict[str, Any]] = []
        for file_path in sorted(self.root.rglob("*")):
            if len(matches) >= max_results:
                break
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(self.root)
            if any(part in {".git", "__pycache__", ".venv", "venv"} for part in rel.parts):
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            for line_no, line in enumerate(content.splitlines(), start=1):
                if query.lower() in line.lower():
                    matches.append({"path": str(rel), "line": line_no, "snippet": line.strip()[:200]})
                    if len(matches) >= max_results:
                        break

        return {"query": query, "matches": matches}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        safe_write_text(self.root, path, content)
        resolved = self._resolve(path)
        return {"path": str(resolved.relative_to(self.root)), "bytes": len(content.encode("utf-8"))}

    def run_command(self, cmd: str, *, cwd: str = ".") -> dict[str, Any]:
        tokens = shlex.split(cmd)
        if not tokens:
            raise ToolValidationError("cmd must not be empty")

        if not self._is_allowed_command(tokens):
            raise ToolValidationError(f"Command is not in whitelist: {cmd}")

        run_cwd = self._resolve(cwd)
        completed = subprocess.run(
            tokens,
            cwd=str(run_cwd),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return {
            "cmd": cmd,
            "cwd": str(run_cwd.relative_to(self.root)) if run_cwd != self.root else ".",
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def _is_allowed_command(self, tokens: list[str]) -> bool:
        for prefix in self.command_policy.allowed_prefixes:
            if tuple(tokens[: len(prefix)]) == prefix:
                return True
        return False
