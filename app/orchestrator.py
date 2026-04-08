from __future__ import annotations

"""Controlled orchestrator for planning and confirmed execution."""

from dataclasses import dataclass
import json
import time
from typing import Any, Callable

from app.agent_tools import LocalToolbox, ToolValidationError


@dataclass
class PlanResult:
    goal: str
    read_files: list[str]
    modify_files: list[str]
    risks: list[str]
    need_confirmation: bool
    verify_commands: list[str]


@dataclass(frozen=True)
class ReactRuntimeConfig:
    max_steps: int = 40
    timeout_seconds: int = 300


class AgentOrchestrator:
    def __init__(self, toolbox: LocalToolbox, *, react_config: ReactRuntimeConfig | None = None) -> None:
        self.toolbox = toolbox
        self.react_config = react_config or ReactRuntimeConfig()

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

    def run_react_loop(
        self,
        *,
        model_infer: Callable[[str], str],
        initial_prompt: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        """Run a bounded Think->Act->Observe loop and return full trace."""
        started_at = time.monotonic()
        prompt = initial_prompt
        steps: list[dict[str, Any]] = []
        all_actions: list[dict[str, Any]] = []
        execution_process: list[dict[str, Any]] = []
        execution_outputs: list[dict[str, Any]] = []
        seen_actions: set[str] = set()

        for idx in range(1, self.react_config.max_steps + 1):
            if time.monotonic() - started_at > self.react_config.timeout_seconds:
                return {
                    "completed": False,
                    "reason": "timeout",
                    "steps": steps,
                    "actions": all_actions,
                    "execution": {
                        "executed": bool(execution_process),
                        "process": execution_process,
                        "outputs": execution_outputs,
                        "reason": f"Reached timeout={self.react_config.timeout_seconds}s",
                    },
                }

            raw = model_infer(prompt)
            parsed = self.parse_model_response(raw)
            actions = parsed.get("actions", [])

            deduped_actions: list[dict[str, Any]] = []
            for action in actions:
                marker = json.dumps(action, ensure_ascii=False, sort_keys=True)
                if marker in seen_actions:
                    continue
                seen_actions.add(marker)
                deduped_actions.append(action)

            step_trace: dict[str, Any] = {
                "step": idx,
                "model_reply": raw,
                "parsed": parsed.get("parsed", False),
                "actions": deduped_actions,
            }

            if not deduped_actions:
                step_trace["state"] = "answer"
                steps.append(step_trace)
                return {
                    "completed": True,
                    "reason": "answered",
                    "steps": steps,
                    "model_reply": raw,
                    "actions": all_actions,
                    "execution": {
                        "executed": bool(execution_process),
                        "process": execution_process,
                        "outputs": execution_outputs,
                    },
                }

            all_actions.extend(deduped_actions)
            if not confirmed:
                step_trace["state"] = "await_confirm"
                steps.append(step_trace)
                return {
                    "completed": False,
                    "reason": "await_confirm",
                    "steps": steps,
                    "model_reply": raw,
                    "actions": all_actions,
                    "execution": {
                        "executed": False,
                        "reason": "Awaiting explicit confirmation",
                        "process": [],
                        "outputs": [],
                    },
                }

            execution = self.execute(deduped_actions, confirmed=True)
            step_trace["state"] = "observe"
            step_trace["execution"] = execution
            steps.append(step_trace)

            execution_process.extend(execution.get("process", []))
            execution_outputs.extend(execution.get("outputs", []))
            if execution.get("executed") is False:
                return {
                    "completed": False,
                    "reason": "execution_failed",
                    "steps": steps,
                    "actions": all_actions,
                    "execution": {
                        "executed": False,
                        "process": execution_process,
                        "outputs": execution_outputs,
                        "error": execution.get("error"),
                    },
                }

            prompt = (
                f"{initial_prompt}\n\n"
                f"[ReAct Step {idx} Observation]\n"
                f"{json.dumps(execution, ensure_ascii=False)}\n"
                "如果信息已经足够，请直接给最终回答且不再请求工具。"
            )

        return {
            "completed": False,
            "reason": "max_steps_reached",
            "steps": steps,
            "actions": all_actions,
            "execution": {
                "executed": bool(execution_process),
                "process": execution_process,
                "outputs": execution_outputs,
                "reason": f"Reached MAX_REACT_STEPS={self.react_config.max_steps}",
            },
        }

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
