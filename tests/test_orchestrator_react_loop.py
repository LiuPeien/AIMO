from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent_tools import LocalToolbox
from app.orchestrator import AgentOrchestrator, ReactRuntimeConfig


def test_react_loop_stops_when_model_answers(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "README.md").write_text("hello", encoding="utf-8")

    orchestrator = AgentOrchestrator(LocalToolbox(project), react_config=ReactRuntimeConfig(max_steps=4, timeout_seconds=10))

    replies = [
        '{"tool_calls": [{"tool": "list_dir", "args": {"path": "."}}]}',
        'final answer without json',
    ]

    def model_infer(_: str) -> str:
        return replies.pop(0)

    result = orchestrator.run_react_loop(model_infer=model_infer, initial_prompt="test", confirmed=True)

    assert result["completed"] is True
    assert result["reason"] == "answered"
    assert result["execution"]["executed"] is True
    assert len(result["steps"]) == 2


def test_react_loop_dedups_same_action_across_steps(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    orchestrator = AgentOrchestrator(LocalToolbox(project), react_config=ReactRuntimeConfig(max_steps=2, timeout_seconds=10))

    replies = [
        '{"tool_calls": [{"tool": "list_dir", "args": {"path": "."}}]}',
        '{"tool_calls": [{"tool": "list_dir", "args": {"path": "."}}]}',
    ]

    def model_infer(_: str) -> str:
        return replies.pop(0)

    result = orchestrator.run_react_loop(model_infer=model_infer, initial_prompt="test", confirmed=True)

    assert result["completed"] is True
    assert result["reason"] == "answered"
    assert result["actions"] == [{"tool": "list_dir", "args": {"path": "."}}]


def test_react_loop_requires_confirm_before_execution(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    orchestrator = AgentOrchestrator(LocalToolbox(project), react_config=ReactRuntimeConfig(max_steps=2, timeout_seconds=10))

    result = orchestrator.run_react_loop(
        model_infer=lambda _: '{"tool_calls": [{"tool": "list_dir", "args": {"path": "."}}]}',
        initial_prompt="test",
        confirmed=False,
    )

    assert result["completed"] is False
    assert result["reason"] == "await_confirm"
    assert result["execution"]["executed"] is False
