# nanobot Writing Agent 项目开发计划

> 状态：P1 Document Runtime 核心闭环已完成；P2 Knowledge Runtime MVP 已完成，进入知识增强写作集成
> 更新日期：2026-08-04
> 原则：不改写 AgentLoop / AgentRunner 核心循环，优先通过 Domain Service、Tool、Runtime Context 和 WebUI Adapter 扩展。

## 1. 产品路线

### Phase 0：可用的 Agent Workspace（已基本完成）

- [x] Workspace 访问范围与文件工具
- [x] Agent Trace / Timeline
- [x] 文件预览、文件树和 Diff
- [x] Working Plan / Writing Plan
- [x] `request_user_input` 结构化 Human-in-the-loop
- [x] 结构化文件修改的 Read-only / Ask before apply / Auto apply
- [x] 文件片段引用：`path + start_line + end_line + quote`
- [ ] 浏览器端多行引用手动验收
- [ ] Shell 写入纳入统一审批边界

P0 的完成定义是：Agent 能修改工作区文件，用户能看到文件、Diff、计划、确认表单，并继续与 Agent 交互。它不包含 Document、Revision 和 Review 领域模型。

### Phase 1：Document Runtime（当前阶段，最高优先级）

目标：把“文件操作”升级为“文档资产管理”。

```text
Agent
  ↓
Document Tool
  ↓
Artifact / Document / Chapter
  ↓
ChangeSet → Approval → Revision
  ↓
File System
```

交付范围：

- [x] Writing Domain Model：Project、Artifact、Document、Chapter、Revision、ChangeSet、Review
- [x] 工作区内的文件型 Artifact Store，使用原子写入
- [x] 按章节生成和读取，不默认读取整篇文档
- [x] ChangeSet：修改目标、原因、影响、来源、基线版本和状态
- [x] Revision：不可变版本、比较、回滚
- [x] Document Tools：创建/读取项目、文档和章节，生成 ChangeSet，应用已批准变更
- [x] Writing Runtime Context：当前项目、文档、章节、Revision 和待审问题
- [x] WebUI Adapter：文档资产状态、ChangeSet 和 Revision 事件
- [x] P1 后端与服务测试、冲突测试、恢复测试

### Phase 2：Knowledge Runtime

目标：让 Agent 拥有结构化领域知识，而不是只有向量检索。

- [x] `knowledge/raw` 原始资料空间
- [x] Schema 驱动的 Wiki 生成
- [x] Entity / Concept / Source / Comparison / Synthesis 结构
- [x] Knowledge Extraction Tool/Service（由 Agent 调用工具完成，暂不拆成独立 Agent）
- [x] 关系和来源的可回溯定位
- [x] `graph.json` 持久化和受限 Workspace 预览；复杂图谱交互延后

### Phase 3：Knowledge Enhanced Writing Agent

- [ ] Knowledge Agent 构建资料网络（当前由 Knowledge Skill + Tools + Service 完成）
- [ ] Planning Agent 生成论文结构
- [x] Writing Agent 按章节和证据起草的后端桥接：`knowledge_search` citations → `writing_changeset.sources`
- [ ] Review Agent 检查引用、一致性、术语和事实状态

### Phase 4：高级 Agent 协作

- [ ] Writing Supervisor
- [ ] Research Agent
- [ ] Writing Agent
- [ ] Review Agent
- [ ] Citation Agent
- [ ] Knowledge Agent

## 2. P1 数据和存储约定

第一版不引入 SQLite。文档正文仍是可直接查看的 Markdown，元数据和版本快照采用工作区内 JSON + 原子替换：

```text
<workspace>/writing/<project_id>/
├── project.json
├── documents/<document_id>/
│   ├── document.json
│   └── chapters/<chapter_id>.md
├── revisions/<revision_id>.json
├── changesets/<changeset_id>.json
└── reviews/<review_id>.json
```

Session metadata 只保存当前指针，不保存整篇正文：

```json
{
  "writing_project_id": "project_...",
  "document_id": "document_...",
  "chapter_id": "chapter_...",
  "revision_id": "rev_..."
}
```

所有修改必须带 `base_revision`。基线不一致时返回冲突，不能静默覆盖用户或 Agent 的新修改。

## 3. P1 开发拆分

### P1-A：领域模型和 Store

- [x] 定义稳定 ID、状态、时间、哈希和版本字段
- [x] 实现 `WritingStore` 文件型持久化
- [x] 实现 Project / Document / Chapter 的创建、读取和更新
- [x] 实现路径安全和原子写入

### P1-B：Revision 和 ChangeSet

- [x] 从章节旧内容生成 ChangeSet
- [x] 保存 bounded unified diff 和前后内容哈希
- [x] 审批后生成不可变 Revision
- [x] 支持 Revision 比较、回滚和基线冲突

#### P1-B.1 Approval policy and feedback loop (2026-08-03)

- [x] `auto` policy: a proposed ChangeSet is applied immediately and produces a new Revision.
- [x] `ask` policy: the ChangeSet remains in `review`, exposes a bounded unified Diff, and waits for the existing approval interaction.
- [x] Rejecting a ChangeSet never mutates chapter content.
- [x] Optional rejection feedback is persisted on the ChangeSet and as an append-only `ReviewIssue` decision record.
- [x] The Agent receives an explicit next action to propose a revised ChangeSet using the saved feedback.
- [ ] Direct server-side approval execution from the WebUI (currently the existing structured interaction resumes the Agent, which calls `writing_changeset(action="approve")`).

### P1-C：Agent Tools 和 Runtime Context

- [x] `writing_project`
- [x] `writing_document`
- [x] `writing_chapter`
- [x] `writing_changeset`
- [x] `writing_revision`
- [x] `writing_review`
- [x] Writing Context Provider 接入 Tool Registry

### P1-D：WebUI Adapter 和验收

- [x] 发送文档资产更新事件
- [x] 在现有 Document Workspace 中显示 Artifact / Chapter / Revision 状态
- [x] 复用现有 ChangeApprovalCard 展示 ChangeSet
- [x] REST 恢复和 WebSocket 增量通知一致
- [ ] 断线、刷新、冲突、拒绝和回滚的 WebUI 集成验收

## 4. 当前开发日志

### 2026-08-04

- 完成 Knowledge Runtime MVP：workspace 级 raw/IR/wiki/graph 持久化、任务恢复、校验发布、限定检索和来源 citations。
- 增加知识增强写作的第一条后端闭环：当前选中知识库的最近检索 citations 可自动进入未显式提供 sources 的 `writing_changeset`；按 Knowledge project id 隔离，避免跨库污染。
- `.venv` 验证：`tests/knowledge` + `tests/writing` 共 14 passed，相关 Ruff 通过；浏览器端多行引用仍待手动验收。

### 2026-08-02

- 建立 Phase 0–4 路线和 P1 验收边界。
- P0 核心闭环已具备：Working Plan、User Input、Workspace、Preview、Diff、结构化文件修改审批。
- 实现 `nanobot/writing/`：模型、原子 Store、Artifact/Document/Revision/ChangeSet/Review Service。
- 增加 `writing_*` Agent Tools 和 Writing Runtime Context；不修改 AgentLoop / AgentRunner 主循环。
- 增加 `writing-runtime` REST 快照、`writing_artifact` WebSocket 增量事件和右侧 Document Workspace 状态条。
- ChangeSet `propose` 会创建结构化 `change_approval` 请求并复用现有 ChangeApprovalCard；`auto` 会直接生成 Revision，`ask` 由用户确认，拒绝时的反馈会持久化并返回给下一轮 Agent。
- `.venv` 验证：P1 服务测试 6 passed；ruff 和 basedpyright 均通过；WebUI production build 通过。

## 5. P1 完成标准

用户可以：

1. 创建一个 Writing Project 和 Document；
2. 将 Document 组织为多个 Chapter，而不是把章节仅当作普通文件；
3. 让 Agent 针对一个 Chapter 生成 ChangeSet；
4. 查看修改目标、原因、影响、来源和 Diff；
5. 审批或拒绝 ChangeSet；
6. 审批后生成新的 Revision；
7. 查看历史版本、比较版本并回滚；
8. 在刷新或断线后恢复当前 Document Runtime 状态；
9. 在版本冲突时得到明确提示，而不是被静默覆盖。
