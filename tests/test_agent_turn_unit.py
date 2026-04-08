from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.main as main


def test_run_agent_chat_turn_requires_confirmation(monkeypatch):
    monkeypatch.setattr(
        main,
        "call_ai",
        lambda model_id, prompt: '{"tool_calls": [{"tool": "list_dir", "args": {"path": "."}}]}',
    )

    result = main.run_agent_chat_turn("mock-model", "读取目录", confirmed=False)

    assert result["mode"] == "agent"
    assert result["actions"] == [{"tool": "list_dir", "args": {"path": "."}}]
    assert result["execution"]["executed"] is False


def test_run_agent_chat_turn_executes_after_confirmation(monkeypatch):
    monkeypatch.setattr(
        main,
        "call_ai",
        lambda model_id, prompt: '{"tool_calls": [{"tool": "list_dir", "args": {"path": "."}}]}',
    )

    result = main.run_agent_chat_turn("mock-model", "读取目录", confirmed=True)

    assert result["execution"]["executed"] is True
    assert result["execution"]["outputs"][0]["tool"] == "list_dir"
