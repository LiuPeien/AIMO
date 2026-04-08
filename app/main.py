from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import textwrap
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent_tools import LocalToolbox, ToolValidationError
from app.executor import ExecutionValidationError, FileEdit, execute_edits
from app.orchestrator import AgentOrchestrator
from app.path_safety import PathSecurityError, safe_iter_files, safe_read_text, safe_write_text, validate_project_path
from app.planner import build_structured_plan
from app.verifier import run_post_change_verification

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "agent.db"
MODULE_DIR = ROOT / "modules"
TOKEN_CONFIG_PATH = ROOT / "config" / "tokens.json"



CONTROLLED_SELF_MANAGEMENT_RULES = """
受控自我管理执行规则（必须遵守）：
1) 只能操作当前项目目录中的文件与子目录，禁止访问项目目录之外路径。
2) 禁止使用 git、禁止创建分支、禁止提交 commit、禁止生成 PR。
3) 禁止大范围删除文件或破坏性重构，除非用户明确要求。
4) 修改代码前必须先阅读相关文件，禁止假设文件或符号存在。
5) 执行顺序固定：先理解现状 -> 再给改动计划 -> 等用户确认 -> 再修改 -> 修改后验证 -> 最后汇报。
6) 优先小步、局部、增量改造，避免一次性重写。
7) 新增能力优先复用现有结构与风格。
8) 输出必须结构化说明：已读文件、待改文件、原因、风险、验证方式。
9) 需求不明确时，先基于当前代码给出最合理最小方案，不空泛讨论。
10) 若结构不支持目标能力，先给最小改造路径。
""".strip()

STRUCTURED_RESPONSE_INSTRUCTION = """
每次回答都要先返回结构化结果，优先使用 JSON（不要 markdown 代码块包裹）：
{
  "task_analysis": {
    "goal": "用户目标",
    "scope": "影响范围",
    "assumptions": [],
    "risks": []
  },
  "change_plan": [
    {"step": 1, "title": "先读代码", "details": "先读取哪些文件"}
  ],
  "tool_calls_request": {
    "commands": ["建议本地执行的命令，尽量只读命令起步"],
    "files_to_read": ["需要读取的文件路径"],
    "files_to_modify": ["计划修改的文件路径"]
  },
  "workflow_control": {
    "current_stage": "read_code|plan|await_confirm|modify|verify|report",
    "need_user_confirm": true,
    "next_action": "下一步动作"
  }
}
要求：
- 严格遵守“先读代码 -> 再出计划 -> 确认后修改 -> 验证 -> 汇报”。
- 所有路径与命令都限制在当前项目目录内，不访问项目外部。
- 如果用户尚未确认，不输出执行写入的最终动作。
""".strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    validate_project_path(ROOT, DB_PATH.parent).mkdir(parents=True, exist_ok=True)
    validate_project_path(ROOT, MODULE_DIR).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            model_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        );
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            tags TEXT,
            score REAL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS abilities (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            description TEXT NOT NULL,
            file_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def load_token_config() -> dict[str, Any]:
    if not TOKEN_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(safe_read_text(ROOT, TOKEN_CONFIG_PATH))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


TOKEN_CONFIG = load_token_config()


def config_value(env_key: str, config_path: tuple[str, ...], default: str = "") -> str:
    env_value = os.getenv(env_key)
    if env_value:
        return env_value

    current: Any = TOKEN_CONFIG
    for part in config_path:
        if not isinstance(current, dict):
            return default
        current = current.get(part)
        if current is None:
            return default
    return str(current) if current else default


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def configured_models() -> list[str]:
    raw = config_value("BEDROCK_MODELS", ("bedrock", "models"), "")
    if raw.strip():
        return [m.strip() for m in raw.split(",") if m.strip()]
    return [
        "us.anthropic.claude-sonnet-4-6",
        "us.anthropic.claude-opus-4-6-v1",
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    ]


@dataclass
class BedrockClient:
    region: str
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""

    def __post_init__(self) -> None:
        client_kwargs: dict[str, str] = {"region_name": self.region}
        if self.aws_access_key_id and self.aws_secret_access_key:
            client_kwargs["aws_access_key_id"] = self.aws_access_key_id
            client_kwargs["aws_secret_access_key"] = self.aws_secret_access_key
            if self.aws_session_token:
                client_kwargs["aws_session_token"] = self.aws_session_token
        self.client = boto3.client("bedrock-runtime", **client_kwargs)

    def generate(self, model_id: str, prompt: str) -> str:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }
        try:
            response = self.client.invoke_model(modelId=model_id, body=json.dumps(body))
            payload = json.loads(response["body"].read().decode("utf-8"))
            content = payload.get("content", [])
            if content and isinstance(content, list):
                text_blocks = [part.get("text", "") for part in content if isinstance(part, dict)]
                return "\n".join(x for x in text_blocks if x).strip()
            return json.dumps(payload)
        except (ClientError, BotoCoreError) as exc:
            raise RuntimeError(f"Bedrock request failed: {exc}") from exc


class ChatRequest(BaseModel):
    session_id: str | None = None
    model_id: str
    message: str = Field(min_length=1)
    mode: str = "chat"


class SessionCreateRequest(BaseModel):
    title: str = "新会话"


class SessionUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class EvolveRequest(BaseModel):
    model_id: str
    requirement: str = Field(min_length=6)




class ToolActionRequest(BaseModel):
    tool: Literal["list_dir", "read_file", "search_code", "write_file", "run_command"]
    args: dict[str, Any] = Field(default_factory=dict)


class AgentPlanRequest(BaseModel):
    request: str = Field(min_length=4)


class AgentExecuteRequest(BaseModel):
    confirmed: bool = False
    actions: list[ToolActionRequest] = Field(default_factory=list)

class ManagePlanRequest(BaseModel):
    request: str = Field(min_length=4)
    focus_paths: list[str] = Field(default_factory=list)
    max_files: int = Field(default=60, ge=1, le=300)


class ExecuteEditRequest(BaseModel):
    path: str = Field(min_length=1)
    new_content: str
    allow_create: bool = False
    expected_contains: str = ""


class ManageExecuteRequest(BaseModel):
    confirmed: bool = False
    allowed_files: list[str] = Field(default_factory=list)
    edits: list[ExecuteEditRequest] = Field(default_factory=list)
    max_files: int = Field(default=5, ge=1, le=20)
    verify_after_execute: bool = True


class ManageVerifyRequest(BaseModel):
    changed_files: list[str] = Field(default_factory=list)


class ManageWorkflowRequest(BaseModel):
    step: Literal["plan", "execute"]
    request: str = ""
    focus_paths: list[str] = Field(default_factory=list)
    max_scan_files: int = Field(default=60, ge=1, le=300)
    confirmed: bool = False
    allowed_files: list[str] = Field(default_factory=list)
    edits: list[ExecuteEditRequest] = Field(default_factory=list)
    max_edit_files: int = Field(default=5, ge=1, le=20)
    verify_after_execute: bool = True


TOOLBOX = LocalToolbox(ROOT)
ORCHESTRATOR = AgentOrchestrator(TOOLBOX)

app = FastAPI(title="Simple AI Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def is_bedrock_enabled() -> bool:
    access_key = config_value("AWS_ACCESS_KEY_ID", ("aws", "access_key_id"), "")
    secret_key = config_value("AWS_SECRET_ACCESS_KEY", ("aws", "secret_access_key"), "")
    return bool(access_key and secret_key)


def call_ai(model_id: str, prompt: str) -> str:
    if is_bedrock_enabled():
        region = config_value("AWS_REGION", ("aws", "region"), "us-east-1")
        access_key = config_value("AWS_ACCESS_KEY_ID", ("aws", "access_key_id"), "")
        secret_key = config_value("AWS_SECRET_ACCESS_KEY", ("aws", "secret_access_key"), "")
        session_token = config_value("AWS_SESSION_TOKEN", ("aws", "session_token"), "")
        return BedrockClient(
            region=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
        ).generate(model_id=model_id, prompt=prompt)
    return (
        "[MOCK 回答] 当前未配置 AWS 认证，因此返回模拟结果。\n"
        f"模型: {model_id}\n"
        f"你说的是: {prompt[:300]}"
    )


def title_from_message(message: str) -> str:
    msg = re.sub(r"\s+", " ", message).strip()
    return msg[:20] + ("..." if len(msg) > 20 else "")


def fetch_relevant_memories(conn: sqlite3.Connection, message: str, limit: int = 3) -> list[str]:
    words = {w for w in re.findall(r"[\w\u4e00-\u9fff]+", message.lower()) if len(w) > 1}
    if not words:
        return []
    rows = conn.execute("SELECT id, content, tags, score FROM memories").fetchall()
    scored: list[tuple[float, str, str]] = []
    for row in rows:
        haystack = f"{row['content']} {row['tags'] or ''}".lower()
        overlap = sum(1 for w in words if w in haystack)
        if overlap > 0:
            scored.append((overlap * float(row["score"]), row["id"], row["content"]))
    scored.sort(reverse=True)
    result: list[str] = []
    now = utc_now()
    for _, memory_id, content in scored[:limit]:
        conn.execute("UPDATE memories SET last_used_at = ?, score = score + 0.2 WHERE id = ?", (now, memory_id))
        result.append(content)
    conn.commit()
    return result


def save_experience(conn: sqlite3.Connection, user_message: str, assistant_message: str) -> None:
    if len(user_message) < 8 and len(assistant_message) < 8:
        return
    snippet = textwrap.shorten(f"用户: {user_message}\n助手: {assistant_message}", width=220, placeholder="...")
    tags = ",".join(re.findall(r"[\w\u4e00-\u9fff]+", user_message.lower())[:8])
    now = utc_now()
    conn.execute(
        "INSERT INTO memories (id, content, tags, score, created_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), snippet, tags, 1.0, now, now),
    )
    conn.commit()


def run_dynamic_abilities(input_text: str) -> str:
    outputs: list[str] = []
    for path in safe_iter_files(ROOT, MODULE_DIR):
        if path.suffix != ".py":
            continue
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if not spec or not spec.loader:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "handle"):
            try:
                out = module.handle(input_text)
                if out:
                    outputs.append(f"[{path.stem}] {out}")
            except Exception as exc:  # noqa: BLE001
                outputs.append(f"[{path.stem}] error: {exc}")
    return "\n".join(outputs)


def build_evolution_prompt(requirement: str) -> str:
    return (
        "你是一个代码生成器。根据用户要求返回严格 JSON，包含字段:"
        "module_name, module_description, python_code。"
        "python_code 必须定义 handle(text: str) -> str。"
        "不要包含 markdown。生成代码时必须遵守以下受控自我管理规则：\n"
        f"{CONTROLLED_SELF_MANAGEMENT_RULES}\n"
        "用户需求: "
        + requirement
    )


def build_chat_prompt(
    *,
    history: str,
    memories: list[str],
    dynamic_output: str,
    user_message: str,
) -> str:
    return (
        "你是可扩展 AI Agent。结合对话历史、历史经验和动态模块输出回答。"
        "在涉及代码改动、新增模块、功能修改时，必须按受控自我管理规则执行并输出结构化结果。\n"
        f"规则:\n{CONTROLLED_SELF_MANAGEMENT_RULES}\n"
        f"结构化输出规范:\n{STRUCTURED_RESPONSE_INSTRUCTION}\n"
        f"历史:\n{history}"
        f"\n经验:\n{memories}"
        f"\n模块输出:\n{dynamic_output}"
        f"\n用户问题:\n{user_message}"
    )


def create_module_from_ai(model_id: str, requirement: str) -> dict[str, str]:
    raw = call_ai(model_id, build_evolution_prompt(requirement))
    try:
        data = json.loads(raw)
        module_name = re.sub(r"[^a-zA-Z0-9_]", "_", data["module_name"]).lower()
        if not module_name:
            raise ValueError("empty module_name")
        code = data["python_code"]
        path = MODULE_DIR / f"{module_name}.py"
        safe_write_text(ROOT, path, code)
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO abilities (id, name, description, file_path, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), module_name, data.get("module_description", "dynamic ability"), str(path), utc_now()),
        )
        conn.commit()
        conn.close()
        return {"module_name": module_name, "file_path": str(path), "raw": raw}
    except Exception:  # noqa: BLE001
        fallback = """def handle(text: str) -> str:\n    if '总结' in text:\n        return '动态模块: 我可以做基础总结。'\n    return ''\n"""
        module_name = f"ability_{uuid.uuid4().hex[:8]}"
        path = MODULE_DIR / f"{module_name}.py"
        safe_write_text(ROOT, path, fallback)
        conn = get_conn()
        conn.execute(
            "INSERT INTO abilities (id, name, description, file_path, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), module_name, f"Fallback from requirement: {requirement}", str(path), utc_now()),
        )
        conn.commit()
        conn.close()
        return {"module_name": module_name, "file_path": str(path), "raw": raw}


def _resolve_in_project(path_value: str | Path) -> Path:
    """Unified path guard for all project file operations."""
    try:
        return validate_project_path(ROOT, path_value)
    except PathSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def scan_project_files(limit: int = 120) -> list[str]:
    """Return a small, deterministic file list for project analysis only."""
    ignored_parts = {".git", ".pytest_cache", "__pycache__", ".venv", "venv"}
    files: list[str] = []
    for path in sorted(safe_iter_files(ROOT, ".")):
        rel = path.relative_to(ROOT)
        if any(part in ignored_parts for part in rel.parts):
            continue
        files.append(str(rel))
        if len(files) >= limit:
            break
    return files


def read_project_file_snippet(rel_path: str, max_chars: int = 1800) -> str:
    """Read text snippet from a project file for planning context (read-only)."""
    target = _resolve_in_project(rel_path)
    try:
        text = safe_read_text(ROOT, target)
    except PathSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UnicodeDecodeError:
        return "[binary or non-utf8 file omitted]"
    return text[:max_chars]


def build_manage_plan(user_request: str, focus_paths: list[str], max_files: int) -> dict[str, Any]:
    """Stage-1 planner: read project + analyze request + output a safe, non-executing plan."""
    scanned = scan_project_files(limit=max_files)
    sampled_paths = focus_paths if focus_paths else ["README.md", "app/main.py", "tests/test_api.py", "requirements.txt"]

    read_context: dict[str, str] = {}
    for rel in sampled_paths:
        try:
            read_context[rel] = read_project_file_snippet(rel)
        except HTTPException:
            read_context[rel] = "[unavailable]"

    lowered = user_request.lower()
    target_areas: list[str] = []
    if any(k in lowered for k in ["api", "接口", "route", "endpoint"]):
        target_areas.append("API 路由层 (app/main.py)")
    if any(k in lowered for k in ["前端", "ui", "页面", "web"]):
        target_areas.append("静态前端层 (static/index.html + static/app.js)")
    if any(k in lowered for k in ["测试", "test", "验证"]):
        target_areas.append("测试层 (tests/test_api.py)")
    if any(k in lowered for k in ["模型", "bedrock", "llm", "ai"]):
        target_areas.append("模型调用层 (call_ai / BedrockClient)")
    if not target_areas:
        target_areas.append("默认从后端入口与测试入手")

    involved_files = list(read_context.keys())
    planner = build_structured_plan(user_goal=user_request, involved_files=involved_files)

    return {
        "mode": "manage_plan",
        "scope": {
            "project_root": str(ROOT),
            "write_enabled": False,
            "delete_enabled": False,
            "high_risk_commands_enabled": False,
        },
        "read_files": involved_files,
        "project_scan": {
            "total_files_sampled": len(scanned),
            "sample_files": scanned[: min(30, len(scanned))],
        },
        "planner": planner,
        "analysis": {
            "request": user_request,
            "target_areas": target_areas,
            "notes": [
                "当前阶段仅提供读取项目、分析项目与输出改动计划。",
                "不会自动写代码、不会删除文件、不会执行高风险命令。",
            ],
        },
        "proposed_plan": [
            "Step 1: 阅读并确认目标相关文件（只读）。",
            "Step 2: 输出最小改动方案（文件级 + 函数级）。",
            "Step 3: 等待用户确认后再进入实现阶段。",
            "Step 4: 实现阶段将保持小步增量并附带验证建议。",
        ],
        "file_snippets": read_context,
    }


@app.post("/api/agent/plan")
def agent_plan(payload: AgentPlanRequest) -> dict[str, Any]:
    """Generate structured plan for controlled local engineering workflow."""
    try:
        result = ORCHESTRATOR.plan(payload.request)
    except ToolValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "mode": "agent_plan",
        "workflow": "read_code -> plan -> await_confirm -> modify -> verify -> report",
        **result,
    }


@app.post("/api/agent/execute")
def agent_execute(payload: AgentExecuteRequest) -> dict[str, Any]:
    """Execute confirmed tool actions via local controlled executor only."""
    try:
        result = ORCHESTRATOR.execute(
            [{"tool": item.tool, "args": item.args} for item in payload.actions],
            confirmed=payload.confirmed,
        )
    except ToolValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PathSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    changed_files: list[str] = []
    for output in result.get("outputs", []):
        if output.get("tool") == "write_file":
            path = output.get("result", {}).get("path")
            if isinstance(path, str):
                changed_files.append(path)

    verification = run_post_change_verification(ROOT, changed_files) if changed_files else {
        "overall_status": "unverifiable",
        "checks": [],
        "manual_steps": [],
        "next_steps": ["No file changes requested"],
    }

    return {"mode": "agent_execute", "execution": result, "verification": verification}


@app.post("/api/manage/plan")
def manage_plan(payload: ManagePlanRequest) -> dict[str, Any]:
    """Generate a controlled change plan without performing any write/exec action."""
    return build_manage_plan(
        user_request=payload.request,
        focus_paths=payload.focus_paths,
        max_files=payload.max_files,
    )


@app.post("/api/manage/execute")
def manage_execute(payload: ManageExecuteRequest) -> dict[str, Any]:
    """Apply confirmed, limited edits only for files explicitly approved in planning."""
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="Execution requires explicit confirmed=true.")

    outcome = _execute_and_verify(
        allowed_files=payload.allowed_files,
        edits=payload.edits,
        max_files=payload.max_files,
        verify_after_execute=payload.verify_after_execute,
    )

    return {
        "mode": "manage_execute",
        "constraints": {
            "delete_enabled": False,
            "max_files": payload.max_files,
            "allowed_files": payload.allowed_files,
        },
        **outcome["execution"],
        "verification": outcome["verification"],
    }


@app.post("/api/manage/verify")
def manage_verify(payload: ManageVerifyRequest) -> dict[str, Any]:
    """Run post-change verification manually for a list of changed files."""
    return {
        "mode": "manage_verify",
        "verification": run_post_change_verification(ROOT, payload.changed_files),
    }


def _execute_and_verify(
    *,
    allowed_files: list[str],
    edits: list[ExecuteEditRequest],
    max_files: int,
    verify_after_execute: bool,
) -> dict[str, Any]:
    """Shared helper for confirmed execution + optional verification."""
    try:
        execution = execute_edits(
            root=ROOT,
            allowed_files=allowed_files,
            edits=[
                FileEdit(
                    path=item.path,
                    new_content=item.new_content,
                    allow_create=item.allow_create,
                    expected_contains=item.expected_contains,
                )
                for item in edits
            ],
            max_files=max_files,
        )
    except ExecutionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PathSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    verification = (
        run_post_change_verification(ROOT, [item["path"] for item in execution.get("changed_files", [])])
        if verify_after_execute
        else {"overall_status": "unverifiable", "checks": [], "manual_steps": [], "next_steps": ["Verification skipped"]}
    )
    return {"execution": execution, "verification": verification}


def run_manage_workflow(payload: ManageWorkflowRequest) -> dict[str, Any]:
    """Single-entry managed workflow: request -> plan -> confirm -> execute -> verify."""
    if payload.step == "plan":
        if not payload.request.strip():
            raise HTTPException(status_code=400, detail="`request` is required for plan step.")
        plan = build_manage_plan(payload.request, payload.focus_paths, payload.max_scan_files)
        return {
            "workflow_stage": "plan_generated",
            "next_stage": "execute",
            "data": plan,
        }

    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="Execution requires explicit confirmed=true.")

    outcome = _execute_and_verify(
        allowed_files=payload.allowed_files,
        edits=payload.edits,
        max_files=payload.max_edit_files,
        verify_after_execute=payload.verify_after_execute,
    )

    return {
        "workflow_stage": "completed",
        "next_stage": "done",
        "data": outcome,
    }


@app.post("/api/manage/workflow")
def manage_workflow(payload: ManageWorkflowRequest) -> dict[str, Any]:
    """Unified managed flow endpoint for planning and confirmed execution."""
    return run_manage_workflow(payload)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/models")
def models() -> dict[str, Any]:
    return {"models": configured_models(), "bedrock_enabled": is_bedrock_enabled()}


@app.post("/api/sessions")
def create_session(payload: SessionCreateRequest) -> dict[str, str]:
    sid = str(uuid.uuid4())
    now = utc_now()
    conn = get_conn()
    conn.execute(
        "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (sid, payload.title, now, now),
    )
    conn.commit()
    conn.close()
    return {"session_id": sid, "title": payload.title}


@app.get("/api/sessions")
def list_sessions() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.patch("/api/sessions/{session_id}")
def update_session(session_id: str, payload: SessionUpdateRequest) -> dict[str, str]:
    conn = get_conn()
    exists = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not exists:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")
    now = utc_now()
    conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?", (payload.title, now, session_id))
    conn.commit()
    conn.close()
    return {"session_id": session_id, "title": payload.title}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, str]:
    conn = get_conn()
    exists = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not exists:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return {"session_id": session_id, "message": "deleted"}


@app.get("/api/sessions/{session_id}/messages")
def session_messages(session_id: str) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content, model_id, created_at FROM messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]




def run_agent_chat_turn(model_id: str, user_message: str, *, confirmed: bool) -> dict[str, Any]:
    """Run one structured agent turn: model output -> parse -> optional local tool execution."""
    prompt = build_chat_prompt(history="", memories=[], dynamic_output="", user_message=user_message)
    raw = call_ai(model_id, prompt)
    parsed = ORCHESTRATOR.parse_model_response(raw)

    actions = parsed.get("actions", [])
    if not actions:
        return {
            "mode": "agent",
            "model_reply": raw,
            "parsed": parsed.get("parsed", False),
            "actions": [],
            "execution": {"executed": False, "reason": "No actionable tool calls returned by model"},
        }

    if not confirmed:
        return {
            "mode": "agent",
            "model_reply": raw,
            "parsed": parsed.get("parsed", False),
            "actions": actions,
            "execution": {"executed": False, "reason": "Awaiting explicit confirmation"},
        }

    execution = ORCHESTRATOR.execute(actions, confirmed=True)
    return {
        "mode": "agent",
        "model_reply": raw,
        "parsed": parsed.get("parsed", False),
        "actions": actions,
        "execution": execution,
    }

@app.post("/api/chat")
def chat(payload: ChatRequest) -> dict[str, Any]:
    conn = get_conn()
    sid = payload.session_id
    now = utc_now()
    if not sid:
        sid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (sid, title_from_message(payload.message), now, now),
        )

    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, model_id, created_at) VALUES (?, ?, 'user', ?, ?, ?)",
        (str(uuid.uuid4()), sid, payload.message, payload.model_id, now),
    )

    evolve_mode = payload.mode == "evolve" or payload.message.strip().startswith("/evolve ")
    agent_mode = payload.mode == "agent" or payload.message.strip().startswith("/agent ")
    if evolve_mode:
        requirement = payload.message.replace("/evolve", "", 1).strip() or payload.message.strip()
        created = create_module_from_ai(payload.model_id, requirement)
        reply = (
            f"✅ 已在当前会话中完成进化：{created['module_name']}\n"
            f"模块位置：{created['file_path']}\n"
            "你现在可以继续在本会话里直接使用这个新能力。"
        )
        memories = [f"evolution:{created['module_name']}"]
        dynamic_output = ""
    elif agent_mode:
        message = payload.message.replace("/agent", "", 1).strip() or payload.message.strip()
        confirmed = "#confirm" in payload.message
        result = run_agent_chat_turn(payload.model_id, message, confirmed=confirmed)
        reply = json.dumps(result, ensure_ascii=False)
        memories = ["agent_mode"]
        dynamic_output = ""
    else:
        memories = fetch_relevant_memories(conn, payload.message)
        dynamic_output = run_dynamic_abilities(payload.message)

        prior = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT 6", (sid,)
        ).fetchall()
        history = "\n".join(f"{r['role']}: {r['content']}" for r in reversed(prior))

        prompt = build_chat_prompt(
            history=history,
            memories=memories,
            dynamic_output=dynamic_output,
            user_message=payload.message,
        )
        reply = call_ai(payload.model_id, prompt)

    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, model_id, created_at) VALUES (?, ?, 'assistant', ?, ?, ?)",
        (str(uuid.uuid4()), sid, reply, payload.model_id, utc_now()),
    )
    conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (utc_now(), sid))
    save_experience(conn, payload.message, reply)
    conn.commit()
    conn.close()
    return {
        "session_id": sid,
        "reply": reply,
        "memories_used": memories,
        "ability_output": dynamic_output,
        "mode": "evolve" if evolve_mode else "chat",
    }


@app.post("/api/evolve")
def evolve(payload: EvolveRequest) -> dict[str, str]:
    try:
        created = create_module_from_ai(payload.model_id, payload.requirement)
        return {
            "message": "新能力已创建",
            "module_name": created["module_name"],
            "file_path": created["file_path"],
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/abilities")
def abilities() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute("SELECT name, description, file_path, created_at FROM abilities ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/memories")
def memories() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT content, tags, score, created_at, last_used_at FROM memories ORDER BY last_used_at DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
