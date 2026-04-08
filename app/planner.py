from __future__ import annotations

"""Lightweight planner that builds stable, machine-friendly plan structures."""

import re
from typing import Any


def _normalize_goal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _guess_expected_new_modules(goal: str) -> list[str]:
    lowered = goal.lower()
    modules: list[str] = []
    if any(k in lowered for k in ["模块", "module", "能力"]):
        modules.append("modules/<new_module>.py")
    if any(k in lowered for k in ["api", "接口", "route", "endpoint"]):
        modules.append("app/main.py (新增路由或处理逻辑)")
    return modules


def _guess_modification_points(goal: str) -> list[str]:
    lowered = goal.lower()
    points: list[str] = []
    if any(k in lowered for k in ["接口", "api", "route", "endpoint"]):
        points.append("路由定义与请求/响应模型")
    if any(k in lowered for k in ["模型", "bedrock", "ai", "llm"]):
        points.append("模型调用链路（call_ai / BedrockClient）")
    if any(k in lowered for k in ["前端", "ui", "页面", "web"]):
        points.append("前端页面与调用逻辑（static/index.html, static/app.js）")
    if any(k in lowered for k in ["测试", "test", "验证"]):
        points.append("测试用例与验证命令")
    if not points:
        points.append("后端入口逻辑与对应测试")
    return points


def _risk_level(goal: str) -> str:
    lowered = goal.lower()
    if any(k in lowered for k in ["删除", "重构", "迁移", "replace all", "drop"]):
        return "high"
    if any(k in lowered for k in ["新增", "添加", "修改", "优化", "module", "feature"]):
        return "medium"
    return "low"


def build_structured_plan(user_goal: str, involved_files: list[str]) -> dict[str, Any]:
    """Build a stable, machine-friendly plan skeleton for stage-1 planning."""
    goal = _normalize_goal(user_goal)
    return {
        "planner_version": "v1",
        "user_goal": goal,
        "involved_files": involved_files,
        "expected_new_modules": _guess_expected_new_modules(goal),
        "potential_modification_points": _guess_modification_points(goal),
        "risk_level": _risk_level(goal),
        "verification_plan": [
            "静态检查：确认涉及文件存在且路径在项目根目录内",
            "变更前评审：先确认计划再进入实现",
            "变更后验证：执行目标相关测试（例如 pytest -q）",
        ],
    }
