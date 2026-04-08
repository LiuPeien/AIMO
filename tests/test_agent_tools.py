from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent_tools import LocalToolbox, ToolValidationError


def test_list_dir_and_read_file(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("hello", encoding="utf-8")

    tools = LocalToolbox(project)
    listed = tools.list_dir(".")
    assert any(item["name"] == "a.txt" for item in listed["items"])

    read = tools.read_file("a.txt")
    assert read["content"] == "hello"


def test_write_file_rejects_escape(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    tools = LocalToolbox(project)

    with pytest.raises(Exception):
        tools.write_file("../hack.txt", "x")


def test_run_command_whitelist(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    tools = LocalToolbox(project)

    with pytest.raises(ToolValidationError):
        tools.run_command("rm -rf /")
