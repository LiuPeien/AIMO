from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.path_safety import PathSecurityError, safe_read_text, validate_project_path


def test_validate_project_path_rejects_parent_escape():
    with pytest.raises(PathSecurityError):
        validate_project_path(ROOT, "../etc/passwd")


def test_validate_project_path_rejects_absolute_escape():
    with pytest.raises(PathSecurityError):
        validate_project_path(ROOT, "/etc/passwd")


def test_safe_read_text_rejects_outside_path():
    with pytest.raises(PathSecurityError):
        safe_read_text(ROOT, "../../outside.txt")


def test_validate_project_path_accepts_in_project_file():
    resolved = validate_project_path(ROOT, "app/main.py")
    assert resolved.is_file()
    assert ROOT.resolve() in resolved.parents
