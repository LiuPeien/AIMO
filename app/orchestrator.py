from __future__ import annotations

"""Controlled orchestrator for planning and confirmed execution."""

from dataclasses import dataclass
from typing import Any

from app.agent_tools import LocalToolbox, ToolValidationError


@dataclass
class PlanResult:
    goal: str
    read_files: list[str]
    modify_files: list[str]
    risks: list[str]
    need_confirmation: bool
    verify_commands: list[str]


class AgentOrchestrator:
    def __init__(self, toolbox: LocalToolbox) -> None:
        self.toolbox = toolbox

    def plan(self, user_request: str) -> dict[str, Any]:
        goal = " ".join(user_request.split()).strip()
        if not goal:
            raise ToolValidationError("request must not be empty")

        scanned = self.toolbox.list_dir(".")
        top_files = [item["path"] for item in scanned["items"] if item["type"] == "file"]

        read_files = [path for path in ["README.md", "app/main.py", "requirements.txt"] if path in top_files or path.startswith("app/")]
        modify_files = ["app/main.py"]
        if "test" in goal.lower() or "验证" in goal:
            modify_files.append("tests/test_api.py")

        risks = ["多文件修改可能引入回归", "命令执行仅允许白名单"]
        verify_commands = ["pytest -q", "python -m py_compile app/main.py"]

        return {
            "plan": PlanResult(
                goal=goal,
                read_files=read_files,
                modify_files=modify_files,
                risks=risks,
                need_confirmation=True,
                verify_commands=verify_commands,
            ).__dict__,
            "next_stage": "await_confirm",
        }

    def execute(self, actions: list[dict[str, Any]], *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ToolValidationError("execution requires confirmed=true")

        outputs: list[dict[str, Any]] = []
        for action in actions:
            tool = action.get("tool")
            args = action.get("args", {})
            if tool == "list_dir":
                outputs.append({"tool": tool, "result": self.toolbox.list_dir(**args)})
            elif tool == "read_file":
                outputs.append({"tool": tool, "result": self.toolbox.read_file(**args)})
            elif tool == "search_code":
                outputs.append({"tool": tool, "result": self.toolbox.search_code(**args)})
            elif tool == "write_file":
                outputs.append({"tool": tool, "result": self.toolbox.write_file(**args)})
            elif tool == "run_command":
                outputs.append({"tool": tool, "result": self.toolbox.run_command(**args)})
            else:
                raise ToolValidationError(f"unsupported tool: {tool}")

        return {"executed": True, "outputs": outputs}
