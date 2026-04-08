from __future__ import annotations

"""Post-change verifier: tests -> syntax/import -> smoke -> manual fallback."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.path_safety import validate_project_path


def _run_cmd(cmd: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "unverifiable", "error": str(exc), "stdout": "", "stderr": ""}

    output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "output": output.strip(),
    }


def _classify_pytest_result(result: dict[str, Any]) -> tuple[str, str]:
    if result.get("status") == "unverifiable":
        return "unverifiable", result.get("error", "pytest execution failed")

    code = result.get("returncode", 1)
    output = result.get("output", "")
    if code == 0:
        return "success", "pytest passed"
    if "No module named 'httpx'" in output:
        return "unverifiable", "pytest dependency missing: httpx"
    return "failed", output[:500] if output else "pytest failed"


def run_post_change_verification(root: Path, changed_files: list[str]) -> dict[str, Any]:
    """Run lightweight verification after controlled execution.

    Priority:
    1) Existing tests
    2) Syntax/import checks
    3) Local smoke check
    4) Manual steps if automation is unavailable
    """
    root = validate_project_path(root, ".")
    checks: list[dict[str, Any]] = []

    # 1) Existing tests
    tests_dir = root / "tests"
    if tests_dir.exists() and tests_dir.is_dir():
        pytest_result = _run_cmd(["pytest", "-q"], cwd=root)
        status, detail = _classify_pytest_result(pytest_result)
        checks.append({"name": "tests", "status": status, "detail": detail})
    else:
        checks.append({"name": "tests", "status": "unverifiable", "detail": "No tests directory found"})

    # 2) Syntax/import checks
    python_targets = [item for item in changed_files if item.endswith(".py")]
    py_compile_targets: list[str] = []
    for item in python_targets:
        target = validate_project_path(root, item)
        if target.exists() and target.is_file():
            py_compile_targets.append(str(target.relative_to(root)))

    if py_compile_targets:
        syntax_result = _run_cmd([sys.executable, "-m", "py_compile", *py_compile_targets], cwd=root)
        if syntax_result.get("returncode", 1) == 0:
            checks.append({"name": "syntax", "status": "success", "detail": "py_compile passed"})
        else:
            checks.append(
                {
                    "name": "syntax",
                    "status": "failed",
                    "detail": (syntax_result.get("output") or "py_compile failed")[:500],
                }
            )
    else:
        checks.append({"name": "syntax", "status": "unverifiable", "detail": "No Python files changed"})

    # 3) Smoke test
    smoke_target = root / "app" / "main.py"
    if smoke_target.exists():
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root)
        smoke_result = _run_cmd([sys.executable, "-c", "import app.main"], cwd=root, env=env)
        if smoke_result.get("returncode", 1) == 0:
            checks.append({"name": "smoke", "status": "success", "detail": "import app.main passed"})
        else:
            checks.append(
                {
                    "name": "smoke",
                    "status": "failed",
                    "detail": (smoke_result.get("output") or "smoke import failed")[:500],
                }
            )
    else:
        checks.append({"name": "smoke", "status": "unverifiable", "detail": "No app/main.py found"})

    statuses = [item["status"] for item in checks]
    if "failed" in statuses:
        overall = "failed"
    elif "success" in statuses:
        overall = "success"
    else:
        overall = "unverifiable"

    manual_steps = [
        "启动服务: uvicorn app.main:app --reload --port 8000",
        "手动调用 /api/manage/plan 与 /api/manage/execute 检查返回结构",
        "若有前端改动，打开 / 验证页面可用性",
    ]

    next_steps: list[str] = []
    if overall == "failed":
        next_steps.append("根据失败详情先修复语法或测试错误，再重新执行验证")
    elif overall == "unverifiable":
        next_steps.append("补齐测试依赖后重试自动验证（例如安装缺失依赖）")
    else:
        next_steps.append("自动验证通过，可进入人工验收")

    return {
        "overall_status": overall,
        "checks": checks,
        "manual_steps": manual_steps,
        "next_steps": next_steps,
    }
