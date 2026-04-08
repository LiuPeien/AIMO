# Simple AI Agent (Python + Web UI)

一个可本地运行的简易 AI Agent，支持：

- Python FastAPI 后端 + 原生 HTML/JS 前端。
- 对接 AWS Bedrock（你提到的 Redrock，这里按 Bedrock 实现）并支持模型切换。
- 会话管理（创建/切换会话）和历史消息持久化（SQLite）。
- “自我进化”：和会话统一到同一聊天入口（进化模式或 `/evolve xxx`），根据需求生成新能力模块（Python 文件）并动态执行。
- 历史对话沉淀为“经验”（memories），在后续对话中检索并注入上下文。
- 受控自我管理约束：在涉及代码任务时，优先输出“现状->计划->确认->修改->验证->汇报”的结构化流程。

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
export BEDROCK_MODELS="us.anthropic.claude-sonnet-4-6,us.anthropic.claude-opus-4-6-v1,us.anthropic.claude-haiku-4-5-20251001-v1:0"
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
- `POST /api/manage/plan`: 只读项目分析与改动计划输出（不执行写入/删除/高风险命令）。
- `POST /api/manage/execute`: 在确认后执行受控小步修改（仅允许规划文件、默认不删除）。
- `POST /api/manage/verify`: 对本次改动执行自动验证（测试/语法/smoke）。
- `POST /api/manage/workflow`: 串联主流程（plan -> confirm -> execute -> verify）。

`/api/manage/plan` 返回稳定结构化规划结果，核心字段包括：
- `planner.user_goal`
- `planner.involved_files`
- `planner.expected_new_modules`
- `planner.potential_modification_points`
- `planner.risk_level`
- `planner.verification_plan`


验证结果状态说明：
- `success`: 自动验证通过
- `failed`: 自动验证失败（会附失败详情）
- `unverifiable`: 当前环境无法自动验证（会给手动验证步骤）

`/api/manage/execute` 约束：
- 必须 `confirmed=true`
- 仅允许修改 `allowed_files` 列表中的文件
- 默认不删除文件
- 单次任务默认最多修改 5 个文件
- 修改前会先读取并校验目标文件（可用 `expected_contains` 做一致性检查）


### 最小演示路径（"给项目增加一个简单日志模块"）

1. **用户请求**：调用 `/api/manage/workflow`，`step=plan`，`request="给项目增加一个简单日志模块"`。
2. **项目分析 + 生成计划**：系统返回 `workflow_stage=plan_generated`，其中 `data` 为结构化计划（目标、涉及文件、风险、验证方式）。
3. **用户确认**：客户端检查计划并确认 `allowed_files` 与编辑内容。
4. **执行改动**：再次调用 `/api/manage/workflow`，`step=execute`，携带 `confirmed=true`、`allowed_files`、`edits`。
5. **执行验证**：执行结果中自动包含 `verification`（success/failed/unverifiable）。
6. **输出结果**：返回 `execution.changed_files` 与 `execution.change_summary`，以及后续建议。


### Agent 使用方式（简版）

- 第一步：`POST /api/manage/workflow`，`step=plan`，提交自然语言需求。
- 第二步：审阅返回计划，确认 `allowed_files` 与拟修改内容。
- 第三步：`POST /api/manage/workflow`，`step=execute`，并设置 `confirmed=true`。
- 第四步：读取返回中的 `verification`（`success` / `failed` / `unverifiable`）。

### 当前版本能力边界

**已经支持**
- 受控项目分析与结构化计划生成。
- 受控小步执行（仅允许规划文件、先读后写、限制文件数量）。
- 自动验证（测试、语法/import、smoke）与手动验证建议。
- 路径安全控制（拦截越界路径与潜在软链接逃逸）。

**暂时不支持**
- 自动回滚与事务化恢复。
- 多轮审批流（多人签核/权限分级）。
- 高级语义 diff 合并与冲突自动解决。
- 跨仓库/跨目录协同改造。

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

## 5) 受控自我管理能力（新增）

在聊天与进化生成模块时，后端会把“受控自我管理规则”注入给模型，核心约束包括：

- 仅限当前项目目录内操作。
- 不使用 git / 分支 / commit / PR。
- 先读代码再改，先给计划并等待确认，再执行修改与验证。
- 优先小步增量，保持现有结构与风格。
- 输出中要包含：已读文件、待改文件、改动原因、风险、验证方式。
- 路径安全控制：统一路径校验，拦截 `../`、越界绝对路径、以及潜在软链接逃逸。

说明：当前版本通过 Prompt 约束实现“受控流程”，便于先做最小改造；生产环境建议进一步增加执行层面的硬约束（例如沙箱、白名单与审批状态机）。
