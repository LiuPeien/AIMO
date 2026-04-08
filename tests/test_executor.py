from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.executor import ExecutionValidationError, FileEdit, execute_edits


def test_execute_edits_rejects_unapproved_target(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("old", encoding="utf-8")

    with pytest.raises(ExecutionValidationError):
        execute_edits(
            root=project,
            allowed_files=["a.txt"],
            edits=[FileEdit(path="b.txt", new_content="x", allow_create=True)],
        )


def test_execute_edits_applies_small_approved_change(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("hello", encoding="utf-8")

    result = execute_edits(
        root=project,
        allowed_files=["a.txt"],
        edits=[FileEdit(path="a.txt", new_content="hello world", expected_contains="hello")],
        max_files=2,
    )

    assert result["applied"] is True
    assert result["change_summary"]["total_files"] == 1
    assert target.read_text(encoding="utf-8") == "hello world"
