# nanobot Knowledge Runtime 开发计划

更新时间：2026-08-04

本计划记录基于参考知识库结构的 Phase 2 实现进度。边界保持为：

```text
/knowledge 入口
    ↓
Knowledge Skill（方法）
    ↓
Knowledge Tools（可观察接口）
    ↓
Knowledge Service（核心逻辑）
    ↓
<workspace>/wikis/<project_id>/
```

不修改 AgentLoop / AgentRunner 核心循环，不把知识库构建做成不可观察的超级工作流；大任务是否创建 Goal 由用户任务决定，知识任务本身只维护自己的 Runtime Context。

## MVP 状态

- [x] `nanobot/knowledge/models.py`：Source、Page、Relation、Entity、IR、Project。
- [x] `store.py`：workspace-scoped、原子写入、路径校验、项目/IR/raw/review/wiki 持久化。
- [x] `service.py`：扫描 source、镜像 raw、保存 IR、编译、校验、Review、发布，并持久化可恢复的 `knowledge/task.json`。
- [x] `compiler.py`：frontmatter 页面、`index.md`、`overview.md`、`log.md`、`graph.json`；重复编译合并正文且不重复写 ingest 日志。
- [x] `knowledge_scan` / `knowledge_extract` / `knowledge_compile` / `knowledge_validate` / `knowledge_publish` 工具。
- [x] `knowledge_search`：仅检索选中的知识项目，支持 page type/tag/source path 过滤，返回有界 quote、wiki 行号和 raw source citations。
- [x] `knowledge-engineering` Skill：约束 scan → extract → compile → validate → publish，并要求保存来源证据。
- [x] `/knowledge <source-directory>`：只声明任务边界，实际执行仍由 Agent Runtime 调用工具。
- [x] Runtime Context 条件注入：仅在知识请求、已有知识上下文或 WebUI 显式选择项目时注入。
- [x] WebUI 知识库选择器：通过 `/api/sessions/{key}/knowledge-projects` 获取摘要，并在下一条 WebSocket message 中携带 `knowledge_project_id`。
- [x] Knowledge Workspace 轻量入口：项目摘要、任务状态、Raw/IR/Wiki/Graph 快捷预览，复用现有文件树与 FilePreviewPanel。
- [x] Graph preview：在现有 Workspace 摘要内提供受限 SVG 关系预览，`graph.json` 仍是持久化真相。
- [x] `.venv\Scripts\python.exe -m pytest tests/knowledge -q`：6 passed。
- [x] `webui\bun run build`：TypeScript 与生产构建通过。

## 参考目录映射

```text
<workspace>/wikis/<project_id>/
├── raw/                 # scan 时镜像的原始资料（sources/ 下保留相对路径）
├── assets/              # 预留附件、图片与二进制资料
├── schema.md            # schema profile
├── knowledge/
│   ├── manifest.json    # scan 结果
│   ├── ir/*.json        # Agent 结构化抽取中间表示
│   └── graph/graph.json # 可重建关系快照
└── wiki/                    # 新项目默认采用参考目录形态；旧 MVP 项目仍可读取 knowledge/wiki/
    ├── index.md
    ├── overview.md
    ├── log.md
    ├── entities/*.md
    ├── concepts/*.md
    ├── sources/*.md
    ├── queries/*.md
    ├── comparisons/*.md
    └── synthesis/*.md
```

## 当前阶段结论

本轮完成了 MVP 的 durable pipeline：`scan → extract(IR) → compile(wiki + graph) → validate/review → publish`。
`knowledge_validate` 会持久化 Review 结果；验证失败时不会发布。WebUI 仅增加知识项目摘要与快捷入口，不改变 Conversation/Agent Timeline 的既有布局。
`knowledge/task.json` 保存任务阶段、状态、待处理/已处理来源，Runtime Context 只在知识任务或显式选择项目时读取它。

Writing Agent 集成已完成后端第一步：`knowledge_search` 会返回并在会话中保存有界 citations；当当前请求选中了同一 Knowledge project 且 `writing_changeset` 未显式传入 `sources` 时，ChangeSet 会自动携带最近检索的 citations。Writing Runtime Context 只注入选中项目标识和引用数量，不注入整段知识内容。

## 下一步

- [x] 将 Knowledge Workspace 扩展为可折叠的 Raw / IR / Wiki / Graph 分组视图；继续复用现有 Workspace 与 FilePreviewPanel。
- [x] 后端将引用片段映射为统一的 `path + start_line + end_line + quote`，并由 `knowledge_search` 返回可供写作 Agent 复用的 source citations。
- [x] 将最近一次选中知识库检索的 citations 接入 `writing_changeset.sources`，并按 Knowledge project id 防止跨库污染。
- [ ] 完成浏览器端多行引用的手动验收，并确认引用卡片在下一条消息中稳定回传。
- [ ] 增加 PDF/网页/OCR 的可插拔 ingestion adapter；原始内容仍先进入 raw，再进入 IR。
- [ ] 评估向量检索和 GraphRAG；在证据链、权限边界和可观察性稳定前不全量注入 wiki。
- [ ] 后续再评估独立 Knowledge Agent、多 Agent 协作和图谱可视化。

## 已知门禁问题

`tests/tools/test_tool_loader.py` 现有两项测试因 `ToolsConfig` 的 Pydantic forward-reference 未完成而失败（`WebToolsConfig` 未定义），与 Knowledge Runtime 改动无关；应单独修复配置模块后再作为全量门禁。
