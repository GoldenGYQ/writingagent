# nanobot 写作智能体：项目认知与扩展方案

本文基于当前仓库代码梳理 nanobot 从底层智能体到服务、WebUI 的真实构建方式，并给出将其扩展为“写作能力优化智能体”的推荐边界和实施顺序。

## 1. 项目定位

nanobot 不是单一聊天机器人，而是一个小型、可扩展的 Agent Runtime：

- Channel 负责把 WebUI、CLI、Telegram 等外部消息转换为统一事件；
- `MessageBus` 解耦消息接入、Agent 执行与结果投递；
- `AgentLoop` 负责一次请求的完整生命周期；
- `AgentRunner` 负责模型调用、工具调用和多轮迭代；
- Provider 层统一不同 LLM；
- Tool、Skill 和 MCP 是主要能力扩展点；
- Session 保存当前会话，Memory/Dream 维护跨会话长期信息；
- Gateway 同时编排 Agent、Channel、Cron、WebUI 和健康检查；
- React WebUI 使用 REST 恢复持久状态，使用 WebSocket 接收实时增量。

现有架构的核心原则是：核心循环保持小而稳定，新业务能力尽量放在 Skill、Tool、MCP、Channel 或 WebUI 边缘模块中。写作智能体也应遵循这个原则，不应把“写作工作流”硬编码进 `AgentLoop` 或 `AgentRunner`。

## 2. 一次请求的真实调用链

```mermaid
sequenceDiagram
    participant UI as React WebUI
    participant WS as WebSocketChannel
    participant Bus as MessageBus
    participant Loop as AgentLoop
    participant Ctx as ContextBuilder
    participant Runner as AgentRunner
    participant LLM as LLM Provider
    participant Tool as Tool Registry
    participant Store as Session/Memory

    UI->>WS: message envelope
    WS->>WS: 校验文本、附件、权限、工作区
    WS->>Bus: InboundMessage
    Bus->>Loop: consume_inbound
    Loop->>Store: 恢复 Session、必要时压缩历史
    Loop->>Ctx: 组装 system prompt、历史、技能、记忆、当前消息
    Loop->>Runner: AgentRunSpec
    loop 模型/工具迭代
        Runner->>LLM: messages + tool definitions
        LLM-->>Runner: 文本、推理增量或 tool calls
        Runner->>Tool: execute
        Tool-->>Runner: tool result
    end
    Runner-->>Loop: final content + messages + usage + stop reason
    Loop->>Store: 保存 Session、checkpoint、归档信息
    Loop->>Bus: OutboundMessage / runtime events
    Bus->>WS: 增量、工具进度、turn_end
    WS-->>UI: WebSocket events
    UI->>WS: REST thread refresh
    WS-->>UI: 持久化后的规范快照
```

### 2.1 Channel 和 MessageBus

Channel 是外部系统的适配层。每个 Channel 自己处理认证、消息格式、媒体、重试和平台限制，然后发布统一的 `InboundMessage`。

WebUI 对应的 WebSocket Channel 在接收消息时会依次处理：

1. 校验 `chat_id`、文本和附件；
2. 检查客户端是否仍在允许列表；
3. 解析工作区作用域；
4. 保存 WebUI 展示用 transcript；
5. 转换成 `InboundMessage` 并写入 `MessageBus`；
6. 返回 `message_accepted`；
7. 后续把流式文本、推理、工具进度、文件修改和 `turn_end` 发给订阅该会话的客户端。

### 2.2 AgentLoop：请求生命周期编排

`AgentLoop._process_message()` 将一次请求明确拆成七个阶段：

1. `restore`：恢复 Session 和未完成的运行 checkpoint；
2. `compact`：处理过期或需要压缩的上下文；
3. `command`：处理斜杠命令；
4. `build`：选择模型运行时、取历史、持久化用户输入、构建上下文；
5. `run`：调用 `AgentRunner`；
6. `save`：保存本轮新消息、usage、provider state 和状态事件；
7. `respond`：组装并投递最终消息。

`AgentLoop` 还负责：

- session key 和工作区作用域；
- 每个会话的串行锁与全局并发限制；
- 中途用户补充消息和 subagent 结果注入；
- Cron、local trigger、持续目标和恢复逻辑；
- Tool Registry、MCP 连接和运行时事件；
- Session 压缩和后台持久化。

### 2.3 AgentRunner：模型与工具迭代

`AgentRunner` 不关心 Telegram、WebUI 或项目业务。它接收 `AgentRunSpec` 后：

1. 使用 Context Governor 为模型准备安全、受预算约束的消息副本；
2. 调用当前 Provider；
3. 处理流式文本、reasoning 和 usage；
4. 如果模型返回 tool calls，则执行工具并把结果追加为 `tool` 消息；
5. 保存 `awaiting_tools`、`tools_completed`、`final_response` checkpoint；
6. 继续下一次模型调用，直到得到最终文本；
7. 处理空回复、长度截断、最大迭代、工具错误和中途消息注入。

工具可并发执行，但 Runner 会按工具能力和调用批次决定是否并发。安全边界错误会被转成模型可理解、但不可绕过的工具结果。

### 2.4 ContextBuilder：模型真正看到什么

系统上下文由以下内容组合：

- 运行平台、当前项目工作区和 Agent 工作区；
- 项目的 `AGENTS.md`；
- Agent 的 `SOUL.md` 和 `USER.md`；
- 通用 Tool Contract；
- `memory/MEMORY.md`；
- always-on Skill 和用户通过 `$skill-name` 显式调用的 Skill；
- 其他 Skill 的摘要清单；
- Dream 尚未处理的近期历史；
- Session 压缩后的摘要；
- 当前会话历史、当前输入、图片和运行时上下文块。

写作能力不应只靠扩大 system prompt。合适的做法是把“写作方法”放入 Skill，把“稿件、版本、资料和引用”通过 Tool 与 Runtime Context 按需注入。

### 2.5 Session、Memory 和 Dream

项目中有三类容易混淆的状态：

| 状态 | 作用 | 当前存储 |
|---|---|---|
| Session | 当前会话可回放消息、metadata、provider state | `<workspace>/sessions/*.jsonl` |
| History | 被压缩或归档的会话摘要 | `<workspace>/memory/history.jsonl` |
| Long-term Memory | 用户偏好、稳定事实、Agent 行为资料 | `MEMORY.md`、`SOUL.md`、`USER.md` |

Session JSONL 使用临时文件、替换和可选 fsync 做原子保存，并能跳过损坏行进行修复。上下文超过预算时，Consolidator 会把较老消息总结到 history，再移动 `last_consolidated`。

Dream 是一个定时的内部 Agent 任务。它读取尚未处理的 history，只获得受限文件工具，用来维护长期记忆和 Skill。Dream 适合记住“用户喜欢简洁学术风格”这类稳定偏好，不适合保存整篇稿件或每一版正文。

### 2.6 Gateway、API 与 WebUI

Gateway 创建并并发运行：

- `MessageBus` 和 `RuntimeEventBus`；
- `SessionManager`；
- `AgentLoop`；
- `ChannelManager`；
- Cron、Dream、Heartbeat 和 local trigger；
- 配置文件 watcher；
- 健康检查服务。

项目还有独立的 OpenAI 兼容 API：`/v1/chat/completions` 直接调用 `AgentLoop.process_direct()`，流式响应使用 SSE。

WebUI 的数据策略值得保留：

- WebSocket 负责 `delta`、`reasoning_delta`、工具进度、运行状态等实时事件；
- REST 的 `/api/sessions/.../webui-thread` 负责持久状态恢复；
- 前端用 `turn_id`、run generation 和完成 fence 处理断线重连、迟到事件与重复消息；
- `turn_end` 后再拉取规范快照，避免只相信内存中的流式状态。

写作工作台应继续采用“REST 是持久真相，WebSocket 是增量通知”的模式。

## 3. 现有能力对写作场景的可复用程度

| 现有能力 | 可直接复用 | 当前不足 |
|---|---|---|
| 多 Provider 和模型预设 | 可为规划、起草、审校选择不同模型 | 没有写作任务级的模型路由策略 |
| Skill | 可承载文体、流程、检查清单 | 缺少写作领域 Skill |
| 文件工具 | 可读写 Markdown/文本，可提取 PDF、DOCX、XLSX、PPTX 文本 | 不能结构化编辑 DOCX，缺少章节级语义操作 |
| Web Search/MCP | 可检索资料 | 缺少来源库、引用绑定和证据状态 |
| Session/Memory | 可保存对话和稳定偏好 | 不应作为稿件数据库 |
| Runtime Context Provider | 可按当前项目注入大纲、风格、当前章节 | 尚无 Writing Project 上下文提供器 |
| 流式与工具进度 | 可展示写作过程和审校任务 | 没有写作专用事件与进度模型 |
| WebUI 文件修改轨迹 | 可展示文本 diff | 没有按章节接受/拒绝修改的产品流程 |
| 附件上传 | 已支持 PDF、DOCX、XLSX、PPTX | 只作为消息附件，未进入可管理的资料库 |
| WebUI 文件预览 | 可预览工作区文本文件 | 二进制文档不能在侧栏中高质量预览 |

## 4. 写作智能体需要新增的核心能力

### 4.1 从“聊天生成文本”升级为“管理写作项目”

写作的持久对象不应是一串聊天消息，而应是明确的领域对象：

- `WritingProject`：主题、目标读者、体裁、语言、交付要求；
- `Document`：一篇具体文档；
- `Outline`：有顺序和层级的章节树；
- `Section`：可独立生成、审校和版本化的正文单元；
- `DraftVersion`：不可变版本，记录父版本、作者、时间和变更摘要；
- `Source`：上传文件、网页或手工资料；
- `Citation`：正文位置与来源证据的绑定；
- `StyleProfile`：语气、术语、禁用表达、格式规则和示例；
- `ReviewReport`：问题、严重程度、证据、建议修改；
- `ChangeSet`：针对某个基线版本的结构化修改集合。

### 4.2 版本安全

Agent 不应直接覆盖当前稿件。每次修改都必须携带：

- `document_id`；
- `base_version`；
- 操作目标（章节或文本范围）；
- 修改内容；
- 修改理由；
- 可选引用变更。

后端只在 `base_version` 仍是当前版本时应用 ChangeSet；版本已变化时返回冲突，让 Agent 或用户重新基于最新稿件生成。这样可以避免用户编辑和 Agent 修改互相覆盖。

### 4.3 长文上下文治理

不要把整篇长文每轮都塞给模型。推荐按层次取上下文：

1. 项目简报和 Style Profile；
2. 当前大纲；
3. 当前章节全文；
4. 前后相邻章节摘要；
5. 与当前章节相关的 Source 片段；
6. 全文术语表、人物/实体表和事实约束；
7. 当前任务的 ReviewReport。

可使用现有 Runtime Context Provider，把这些块注入当前请求，同时继续使用 Runner 的 token budget 和 Context Governor。

### 4.4 可验证的写作流程

建议把写作拆成可观察阶段，而不是要求模型一次完成全部工作：

1. 需求澄清与写作简报；
2. 资料盘点和缺口识别；
3. 大纲生成与确认；
4. 分章节起草；
5. 一致性检查；
6. 事实与引用检查；
7. 文风和可读性审校；
8. ChangeSet 审阅与应用；
9. 导出和版式验证。

这些阶段首先由 Skill 教模型如何执行，具体状态通过 Writing Tool 持久化。只有在需要稳定批处理时，才增加后端 workflow service；不需要把 LangGraph 一类编排框架塞进核心循环。

## 5. 推荐的后端扩展

### 5.1 新增独立 Writing 领域模块

建议新增：

```text
nanobot/writing/
  models.py          # Pydantic 领域模型和边界校验
  store.py           # 项目、章节、版本、来源的原子持久化
  service.py         # 领域操作和 ChangeSet 冲突检查
  context.py         # 按当前章节构建 Runtime Context
  review.py          # 一致性、文风、引用检查结果模型
  export/
    markdown.py
    docx.py
    pdf.py
```

第一阶段建议继续采用工作区文件存储，符合项目现有风格，也方便用户直接查看和 Git 管理：

```text
<workspace>/writing/<project-id>/
  project.json
  style.json
  outline.json
  documents/<document-id>/sections/<section-id>.md
  documents/<document-id>/versions/<version-id>.json
  sources/<source-id>/metadata.json
  sources/<source-id>/original.*
  reviews/<review-id>.json
  exports/
```

版本文件第一阶段可保存完整章节快照，优先保证简单、可恢复。等版本量或多人协作确实成为问题，再迁移到 SQLite 或增量对象存储。

所有写入应采用原子替换；路径必须经过现有工作区 resolver，不能为写作附件绕过 workspace 和 SSRF 安全边界。

### 5.2 增加窄而明确的 Writing Tools

推荐 Tool：

- `writing_project`：创建、读取和更新项目简报；
- `writing_outline`：读取和修改大纲；
- `writing_draft`：读取章节、创建 ChangeSet、应用版本；
- `writing_sources`：导入、检索和绑定来源；
- `writing_review`：创建、读取和关闭审校问题；
- `writing_export`：导出 Markdown、DOCX、PDF。

Tool 返回值应包含稳定 ID、版本号、冲突状态和下一步提示，不要只返回一大段自然语言。

不要让模型通过通用 `write_file` 直接修改受管理稿件；受管理文档应由 Writing Tool 维护版本和不变量。普通文件工具仍可处理项目之外的文本。

### 5.3 增加 Writing Skills

建议先提供以下 Skill：

- `writing-project-brief`：把模糊需求整理成写作简报；
- `writing-outline`：从目标和资料生成可审查大纲；
- `writing-draft`：按章节、证据和 Style Profile 起草；
- `writing-revise`：基于 ReviewReport 生成最小 ChangeSet；
- `writing-citation`：引用查找、绑定和缺失证据标记；
- `writing-export`：交付前检查与导出。

Skill 适合保存写作方法和操作步骤；代码负责版本、引用、导出和安全校验。

### 5.4 接入 Runtime Context

利用 `AgentLoop.register_runtime_context_provider()` 增加 Writing Context Provider。会话 metadata 中只保存当前的：

- `writing_project_id`；
- `document_id`；
- `section_id`；
- `document_version`。

Provider 根据这些 ID 读取最新项目状态，并注入有上限的上下文块。不要把整篇正文复制到 Session metadata 或长期 Memory。

### 5.5 写作任务模型路由

现有 `ModelPresetConfig` 已支持 provider、model、temperature、max tokens 和 reasoning effort。可以增加用户配置的预设，例如：

- `writing-planner`：较强推理，输出大纲和缺口；
- `writing-drafter`：较长输出，较稳定文风；
- `writing-reviewer`：低温度、严格检查；
- `writing-export-checker`：针对格式和交付清单。

第一阶段允许用户手工选择预设。第二阶段由 Writing Service 对明确的后台子任务调用指定 preset，但不改变主会话默认模型。

### 5.6 来源、引用与事实状态

来源库至少应记录：

- 标题、作者、日期、URL 或原文件；
- 文件摘要和可检索片段；
- 来源类型与可信度标签；
- 引用定位信息（页码、章节、URL fragment）；
- 抽取时间和内容哈希；
- 是否已人工确认。

正文中的事实可标为 `supported`、`unsupported`、`conflicted`、`needs_review`。模型可以建议引用，但只有能回溯到 Source 片段的引用才能标为 supported。

### 5.7 审校应产出结构化结果

审校不要只返回“这篇文章写得不错”。每条问题至少包含：

- issue ID；
- 所在章节和文本锚点；
- 类型：结构、逻辑、事实、引用、术语、文风、语法、格式；
- 严重程度；
- 解释和证据；
- 建议修改；
- 状态：open、accepted、dismissed、fixed。

审校与改稿分开。Reviewer 先产出问题，Reviser 再基于用户接受的问题生成 ChangeSet。

### 5.8 WebUI REST 与 WebSocket 接口

推荐 REST：

```text
GET/POST   /api/writing/projects
GET/PATCH  /api/writing/projects/{project_id}
GET/PUT    /api/writing/projects/{project_id}/outline
GET        /api/writing/documents/{document_id}
GET        /api/writing/documents/{document_id}/sections/{section_id}
POST       /api/writing/documents/{document_id}/changesets
POST       /api/writing/changesets/{changeset_id}/apply
GET        /api/writing/documents/{document_id}/versions
POST       /api/writing/documents/{document_id}/restore
GET/POST   /api/writing/projects/{project_id}/sources
GET/POST   /api/writing/documents/{document_id}/reviews
POST       /api/writing/documents/{document_id}/exports
```

REST 返回当前 `version` 和内容哈希。写操作必须带 `base_version`。

推荐 WebSocket 只发送失效和进度事件：

- `writing_task_progress`；
- `writing_document_updated`；
- `writing_review_updated`；
- `writing_export_ready`；
- `writing_conflict`。

客户端收到 `writing_document_updated` 后通过 REST 拉取最新规范快照，不依赖 WebSocket 承担完整稿件持久化。

## 6. 推荐的前端扩展

### 6.1 新增 Writing Workbench，而不是把聊天页硬改成编辑器

在现有 Shell 增加 `writing` 路由，复用认证、ClientProvider、主题、设置、模型选择、附件上传和 Agent Activity 组件。

推荐桌面布局：

```text
┌──────────────┬─────────────────────────────┬────────────────────┐
│ 项目与大纲   │ 正文编辑器                  │ Agent / 来源 / 审校 │
│              │                             │                    │
│ 文档         │ 当前章节                    │ 对话               │
│ 章节树       │ 版本状态                    │ 引用                │
│ 资料库       │ 接受/拒绝修改               │ Review issues       │
└──────────────┴─────────────────────────────┴────────────────────┘
```

移动端改为单栏页签：正文、大纲、Agent、资料、审校。

### 6.2 编辑器选择

如果目标只是快速 MVP，可先使用章节级 Markdown 编辑器。若目标是正式写作产品，推荐采用基于 ProseMirror 的 TipTap：

- 支持标题、段落、列表、表格、脚注和引用节点；
- 可以给 Citation 和 Review Issue 定义自有 node mark；
- 便于建立稳定文本锚点；
- 可以实现建议模式和接受/拒绝变更；
- 后续导出 DOCX 时更容易保留结构。

无论采用哪种编辑器，内部都应保存结构化章节和纯文本/Markdown可导出表示，不能只保存浏览器 HTML。

### 6.3 必须具备的交互

- 大纲拖拽排序和层级调整；
- 章节状态：未开始、起草中、待审、已完成；
- 自动保存和显式“生成版本”；
- 当前版本、是否有未保存修改、上次保存时间；
- Agent 建议以 ChangeSet 展示；
- 逐条或整组接受/拒绝；
- 版本历史、diff 和回滚；
- 来源侧栏、引用插入和缺失引用警告；
- Review Issue 定位与状态更新；
- 长任务进度、停止、失败恢复；
- DOCX/PDF 导出和下载。

### 6.4 复用现有 WebUI 能力

可直接复用：

- `NanobotClient` 的连接、重连和认证；
- `useNanobotStream` 的文本、推理、工具进度处理；
- REST + WebSocket 的规范快照对账思想；
- `AgentActivityCluster`、文件修改轨迹和错误展示；
- Workspace Scope；
- 附件上传和媒体签名 URL；
- 模型预设、Skill、MCP 和设置页。

不建议把 `ThreadShell` 继续扩成一个超大组件。Writing Workbench 应有独立状态 hooks，例如：

- `useWritingProject()`；
- `useDocumentSnapshot()`；
- `useSectionEditor()`；
- `useChangeSets()`；
- `useSources()`；
- `useReviews()`。

### 6.5 二进制文档预览

当前文件预览接口只适合文本。写作场景至少需要：

- DOCX：服务端转换为 HTML 或 PDF 后预览；
- PDF：页级缩略图和文本定位；
- 来源文件：原文与抽取文本并排；
- 导出结果：下载前进行最终视觉检查。

第一阶段可以先提供下载和抽取文本预览，后续再加入高保真渲染。

## 7. 推荐实施顺序

### Phase 1：可用的章节式写作 MVP

- Writing Project、Outline、Section、Version、ChangeSet 模型；
- 文件型原子 Store；
- `writing_project`、`writing_outline`、`writing_draft` Tool；
- Writing Skill；
- Runtime Context Provider；
- Writing Workbench 基础三栏；
- 章节编辑、自动保存、Agent ChangeSet、版本回滚；
- Markdown 和基础 DOCX 导出。

完成标准：

- 用户能创建项目和大纲；
- 能逐章写作，不需要每轮发送全文；
- Agent 修改不会静默覆盖用户编辑；
- 刷新或断线后能从 REST 恢复一致状态；
- 能查看版本 diff、回滚并导出文档。

### Phase 2：资料与审校

- Source Library 和文档切片检索；
- 引用对象和文本锚点；
- 结构化 ReviewReport；
- 事实、引用、一致性和文风检查；
- Review Issue 接受、忽略、修复闭环；
- DOCX/PDF 更完整的格式和预览。

### Phase 3：高级写作生产线

- 模板和 Style Profile 市场；
- 多模型规划/起草/审校路由；
- 跨文档术语库和实体一致性；
- 批量章节任务和可恢复后台队列；
- 多人协作、评论和权限；
- 更高保真 Word 修订模式、脚注、目录和参考文献。

### Deferred：Wiki 知识库上下文

Wiki 知识库选择、结构化知识页、混合检索和受控关系扩展属于上下文工程的增强能力，暂不进入 Phase 1—3 的交付范围。该能力应在章节式写作、版本管理、Source Library 和基础引用闭环稳定后再评估。

详细的产品边界、数据模型、后端/前端待办和启动条件见 [Wiki 知识库上下文能力 TODO](wiki-knowledge-context-todo.md)。

## 8. 不建议的实现方式

- 不要直接修改 `AgentRunner` 来硬编码“大纲 → 起草 → 审校”；
- 不要把全文和所有资料永久塞进 system prompt；
- 不要把聊天 Session 当作稿件数据库；
- 不要让 Agent 用通用 `write_file` 无版本覆盖受管理稿件；
- 不要让 WebSocket 成为稿件的唯一真相；
- 不要把每次审校都变成全文重写；
- 不要在没有来源定位的情况下把模型生成的引用标成已验证；
- 不要一开始引入复杂工作流框架或多人协作数据库。

## 9. 最合适的第一步

最稳妥的切入点不是先改模型循环，而是做一个垂直闭环：

1. 创建 Writing Project；
2. 生成并确认 Outline；
3. 选择一个 Section；
4. Context Provider 只注入该章节所需上下文；
5. Agent 通过 `writing_draft` 生成 ChangeSet；
6. 前端展示 diff；
7. 用户接受后生成新 Version；
8. 导出 Markdown/DOCX。

这个闭环能验证最关键的产品假设：nanobot 的通用 Agent Runtime 是否可以通过“Skill + 领域 Tool + 持久稿件 + 专用 Workbench”获得稳定、可控、可恢复的写作能力，同时不破坏现有聊天、渠道和服务架构。
