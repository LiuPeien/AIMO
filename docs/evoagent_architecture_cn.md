# EvoAgent 架构总览（中文版）

## 1) 整体架构图

```text
┌─────────────────────────────────────────────────────────────────┐
│                         用户 (User)                              │
│              Streamlit UI / Notebook / CLI                       │
└────────────────────────────┬────────────────────────────────────┘
                             │ send(message)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      EvoAgent API Layer                          │
│                      api/agent_api.py                            │
│  - 管理 conversation_history                                     │
│  - 注册所有 Tools                                                │
│  - 调用 run_agent()                                              │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    ReAct Agent Core                              │
│                    agent/agent.py                                │
│                                                                  │
│  System Prompt (每轮重建)                                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  角色定义 + 工具表 + Skill Guide 表 + Knowledge Index     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  LangGraph create_react_agent                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Think → Tool Call → Observe → Think → ... → Answer      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  MemorySaver (checkpointer) — 跨轮次保持完整对话状态             │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         Tools Layer    Skills Layer   Knowledge Base
         (tools/)       (skills/)      (knowledge_base/)
```

## 2) ReAct 执行循环

```text
用户问题
   │
   ▼
[Think]  LLM 分析问题，决定下一步
   │
   ▼
[Act]    调用工具（read_file, call_presto, call_jira...）
   │
   ▼
[Observe] 获取工具返回结果
   │
   ▼
[Think]  LLM 分析结果，决定继续还是回答
   │
   ▼
（循环，最多 MAX_REACT_STEPS=40 步）
   │
   ▼
[Answer] 生成最终回答
```

- 最大步数：40 步（防止无限循环）
- 超时：300 秒（复杂任务有足够时间）

## 3) System Prompt 构建机制

每一轮对话，`build_system_prompt()` 会重新构建 Prompt，由三部分拼接：

1. `system.md` 模板
   - 角色定义
   - 工具表（每个工具的功能说明）
   - Skill Guide 表（何时读哪个 `SKILL.md`）
   - 通用规则（不自动保存、不重复调用工具等）
2. Knowledge Index（`index_slim.yaml`）
   - 29 条知识条目的一行摘要
   - 格式：`K-NNNN: "Title" [tags]`
   - 用于决策“是否需要读取某个 KB 文件”
3. 绝对路径替换
   - 将 skill 路径替换为绝对路径
   - 确保 `read_file` 能准确读取 `SKILL.md`

> 为什么每轮重建：Knowledge Base 可能在会话中更新，重建可保证每轮都使用最新索引。

## 4) 记忆机制（MemorySaver）

- `_checkpointer = MemorySaver()`（全局单例，跨轮次共享）
- 全量保存：每轮所有消息（Human + AI + Tool）
- `thread_id` 隔离：每个会话独立
- 重启恢复：可通过 `history` 重新注入
- 当前策略：不做 token 裁剪（未来可加入智能记忆层）

## 5) Skill 机制：按需加载专家指南

- System Prompt 中仅放“Skill 目录表”，不预加载全部指南。
- LLM 根据用户问题自主决定是否读取某个 `SKILL.md`。
- 典型流程：
  1) 用户问题触发某领域（如 Hoover++）
  2) LLM 先调用 `read_file(".../SKILL.md")`
  3) 再按指南调用数据/检索工具完成分析

优势：减少 prompt 体积、避免无关知识占用 token、提高任务适配性。

## 6) Knowledge Base 机制

文件结构：

```text
knowledge_base/
├── K-0002_hoover-plus-plus-overview.md
├── K-0003_...
├── ...
├── index.yaml
├── index_slim.yaml
└── _map.md
```

知识文件格式（frontmatter + 正文）：

```yaml
---
id: K-0002
title: "Hoover++ New Data Model — Overview & Design Rationale"
tags: [hoover-model, data-model, hoover]
status: active
created: 2024-01-15
updated: 2024-03-20
---
```

三级索引：
- `index_slim.yaml`：注入系统提示词，供 LLM 快速检索候选知识
- `index.yaml`：完整索引，含 description 等管理字段
- `_map.md`：人工导航地图，按系统/类型分组

## 7) Sub-Agent 机制

复杂任务可由主 Agent 派生子 Agent：

- `spawn_sub_agent("...")`：单子任务
- `spawn_sub_agents_parallel([...])`：并行子任务（最多 8 个）

主 Agent 汇总结果，子 Agent trace 以 `__SUB_AGENT_TRACE__` 嵌入主 trace，便于 UI 展示执行树。

## 8) 工具权限与安全

`tools/wrapper.py` 统一路径授权：

- `authorize_path(path, write=False)`：只读授权
- `authorize_path(path, write=True)`：读写授权（需用户明确确认）

三条铁律：
1. 写操作必须用户确认（`write_file`/`patch_file` 先展示再执行）
2. 路径授权不能自授（仅用户明确授权后可 `grant_access`）
3. 同一轮避免重复调用同参数工具

## 9) 端到端数据流

```text
用户输入
   │
   ▼
build_system_prompt()
  [角色 + 工具表 + Skill目录 + KB索引]
   │
   ▼
LangGraph ReAct Loop (最多40步)
  Think → Act(read SKILL/KB/DB/Jira/GitHub) → Observe → Think
   │
   ▼
最终回答 + trace + token_usage
   │
   ▼
写入 conversation_history
   │
   ▼
UI 渲染（支持流式输出）
```

## 10) 设计哲学

- Skill Guide 按需读取：避免系统提示词过载
- KB 索引注入：让模型“知道有什么”但不强行加载全文
- 每轮重建 Prompt：确保知识更新即时生效
- MemorySaver 全量保存：优先一致性与可追溯性
- ReAct：以动态决策代替固定链路
- Sub-Agent 并行：提升复杂任务处理能力
- 写操作强确认：安全优先
