# Simple AI Agent (Python + Web UI)

一个可本地运行的简易 AI Agent，支持：

- Python FastAPI 后端 + 原生 HTML/JS 前端。
- 对接 AWS Bedrock（你提到的 Redrock，这里按 Bedrock 实现）并支持模型切换。
- 会话管理（创建/切换会话）和历史消息持久化（SQLite）。
- “自我进化”：和会话统一到同一聊天入口（进化模式或 `/evolve xxx`），根据需求生成新能力模块（Python 文件）并动态执行。
- 历史对话沉淀为“经验”（memories），在后续对话中检索并注入上下文。

## 1) 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

打开 http://127.0.0.1:8000

## 2) Bedrock 配置

设置 AWS 凭据后会走真实 Bedrock；不设置则使用 MOCK 回答。

### 方式 A：环境变量（优先级最高）

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1
# 可选：自定义 UI 下拉模型列表
export BEDROCK_MODELS="anthropic.claude-sonnet-4-6,anthropic.claude-opus-4-6-v1,anthropic.claude-haiku-4-5-20251001-v1:0"
```

### 方式 B：配置文件（适合统一管理 token）

1. 复制模板并填写：

```bash
cp config/tokens.example.json config/tokens.json
```

2. 在 `config/tokens.json` 中配置 AWS 与其他模块 token。

说明：
- 已将 `config/tokens.json` 加入 `.gitignore`，避免误提交敏感信息。
- 环境变量优先于配置文件（方便线上覆盖）。

## 3) 关键接口

- `GET /api/models`: 模型列表 + 是否启用 Bedrock。
- `POST /api/chat`: 统一对话入口（chat/evolve），自动创建会话、保存消息、检索经验、调用动态能力。
- `POST /api/evolve`: 根据需求生成新能力模块。
- `GET /api/sessions`: 会话列表。
- `GET /api/sessions/{id}/messages`: 指定会话历史。
- `GET /api/abilities`: 查看已创建能力。
- `GET /api/memories`: 查看沉淀经验。

## 4) 自我进化机制说明

当 `POST /api/chat` 的 `mode=evolve`（或输入 `/evolve 需求`）时，会把用户需求发给模型，要求返回 JSON：

```json
{
  "module_name": "xxx",
  "module_description": "...",
  "python_code": "def handle(text: str) -> str: ..."
}
```

后端将 `python_code` 写入 `modules/<module_name>.py`，并在聊天时自动加载每个模块执行 `handle(text)`。

> 这是一个教学性质的最简方案。生产环境建议增加沙箱执行、代码审计、权限控制、版本回滚等安全机制。
