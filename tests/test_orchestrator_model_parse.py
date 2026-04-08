from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent_tools import LocalToolbox
from app.orchestrator import AgentOrchestrator


def test_parse_model_response_direct_tool_calls(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    orchestrator = AgentOrchestrator(LocalToolbox(project))

    raw = '{"tool_calls": [{"tool": "list_dir", "args": {"path": "."}}]}'
    parsed = orchestrator.parse_model_response(raw)

    assert parsed["parsed"] is True
    assert parsed["actions"] == [{"tool": "list_dir", "args": {"path": "."}}]


def test_parse_model_response_legacy_shape(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    orchestrator = AgentOrchestrator(LocalToolbox(project))

    raw = '{"tool_calls_request": {"files_to_read": ["README.md"], "commands": ["pytest -q"]}}'
    parsed = orchestrator.parse_model_response(raw)

    assert parsed["parsed"] is True
    assert {"tool": "read_file", "args": {"path": "README.md"}} in parsed["actions"]
    assert {"tool": "run_command", "args": {"cmd": "pytest -q", "cwd": "."}} in parsed["actions"]


def test_parse_model_response_non_json(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    orchestrator = AgentOrchestrator(LocalToolbox(project))

    parsed = orchestrator.parse_model_response("just text")
    assert parsed["parsed"] is False
    assert parsed["actions"] == []
