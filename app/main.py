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
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "agent.db"
MODULE_DIR = ROOT / "modules"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODULE_DIR.mkdir(parents=True, exist_ok=True)
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


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def configured_models() -> list[str]:
    raw = os.getenv("BEDROCK_MODELS", "")
    if raw.strip():
        return [m.strip() for m in raw.split(",") if m.strip()]
    return [
        "anthropic.claude-3-5-sonnet-20240620-v1:0",
        "amazon.nova-pro-v1:0",
        "meta.llama3-1-70b-instruct-v1:0",
    ]


@dataclass
class BedrockClient:
    region: str

    def __post_init__(self) -> None:
        self.client = boto3.client("bedrock-runtime", region_name=self.region)

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


class EvolveRequest(BaseModel):
    model_id: str
    requirement: str = Field(min_length=6)


app = FastAPI(title="Simple AI Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def is_bedrock_enabled() -> bool:
    return bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))


def call_ai(model_id: str, prompt: str) -> str:
    if is_bedrock_enabled():
        region = os.getenv("AWS_REGION", "us-east-1")
        return BedrockClient(region=region).generate(model_id=model_id, prompt=prompt)
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
    for path in MODULE_DIR.glob("*.py"):
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
        "不要包含 markdown。用户需求: "
        + requirement
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
        path.write_text(code, encoding="utf-8")
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
        path.write_text(fallback, encoding="utf-8")
        conn = get_conn()
        conn.execute(
            "INSERT INTO abilities (id, name, description, file_path, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), module_name, f"Fallback from requirement: {requirement}", str(path), utc_now()),
        )
        conn.commit()
        conn.close()
        return {"module_name": module_name, "file_path": str(path), "raw": raw}


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


@app.get("/api/sessions/{session_id}/messages")
def session_messages(session_id: str) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content, model_id, created_at FROM messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
    else:
        memories = fetch_relevant_memories(conn, payload.message)
        dynamic_output = run_dynamic_abilities(payload.message)

        prior = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT 6", (sid,)
        ).fetchall()
        history = "\n".join(f"{r['role']}: {r['content']}" for r in reversed(prior))

        prompt = (
            "你是可扩展 AI Agent。结合对话历史、历史经验和动态模块输出回答。"
            f"\n历史:\n{history}"
            f"\n经验:\n{memories}"
            f"\n模块输出:\n{dynamic_output}"
            f"\n用户问题:\n{payload.message}"
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
