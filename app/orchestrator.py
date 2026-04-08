from __future__ import annotations

"""Controlled orchestrator for planning and confirmed execution."""

from dataclasses import dataclass
import json
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
        process: list[dict[str, Any]] = []
        for action in actions:
            tool = action.get("tool")
            args = action.get("args", {})
            step = {"tool": tool, "args": args, "status": "running"}
            try:
                if tool == "list_dir":
                    result = self.toolbox.list_dir(**args)
                    outputs.append({"tool": tool, "result": result})
                    step["status"] = "success"
                elif tool == "read_file":
                    result = self.toolbox.read_file(**args)
                    outputs.append({"tool": tool, "result": result})
                    step["status"] = "success"
                elif tool == "search_code":
                    result = self.toolbox.search_code(**args)
                    outputs.append({"tool": tool, "result": result})
                    step["status"] = "success"
                elif tool == "write_file":
                    result = self.toolbox.write_file(**args)
                    outputs.append({"tool": tool, "result": result})
                    step["status"] = "success"
                elif tool == "run_command":
                    result = self.toolbox.run_command(**args)
                    outputs.append({"tool": tool, "result": result})
                    step["status"] = "success"
                else:
                    raise ToolValidationError(f"unsupported tool: {tool}")
            except Exception as exc:  # noqa: BLE001
                step["status"] = "failed"
                step["error"] = str(exc)
                process.append(step)
                return {"executed": False, "process": process, "outputs": outputs, "error": str(exc)}
            process.append(step)

        return {"executed": True, "process": process, "outputs": outputs}

    def parse_model_response(self, raw_text: str) -> dict[str, Any]:
        """Parse model JSON and normalize tool requests into executable actions.

        Supported shapes:
        - {"tool_calls": [{"tool": "read_file", "args": {"path": "..."}}]}
        - legacy prompt shape with `tool_calls_request`:
          {"tool_calls_request": {"commands": [...], "files_to_read": [...]}}
        """
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return {"parsed": False, "raw": raw_text, "actions": []}

        actions: list[dict[str, Any]] = []
        direct_calls = payload.get("tool_calls")
        if isinstance(direct_calls, list):
            for call in direct_calls:
                if not isinstance(call, dict):
                    continue
                tool = call.get("tool")
                args = call.get("args", {})
                if isinstance(tool, str) and isinstance(args, dict):
                    actions.append({"tool": tool, "args": args})

        request_block = payload.get("tool_calls_request")
        if isinstance(request_block, dict):
            files_to_read = request_block.get("files_to_read", [])
            if isinstance(files_to_read, list):
                for item in files_to_read:
                    if isinstance(item, str) and item.strip():
                        actions.append({"tool": "read_file", "args": {"path": item.strip()}})

            commands = request_block.get("commands", [])
            if isinstance(commands, list):
                for cmd in commands:
                    if isinstance(cmd, str) and cmd.strip():
                        actions.append({"tool": "run_command", "args": {"cmd": cmd.strip(), "cwd": "."}})

        # de-duplicate while preserving order
        dedup: list[dict[str, Any]] = []
        seen: set[str] = set()
        for action in actions:
            marker = json.dumps(action, ensure_ascii=False, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            dedup.append(action)

        return {"parsed": True, "payload": payload, "actions": dedup}
