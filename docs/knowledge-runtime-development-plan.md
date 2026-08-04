# nanobot Knowledge Runtime 开发计划

更新时间：2026-08-05

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

不修改 AgentLoop / AgentRunner 核心循环，不把知识库构建做成不可观察的超级工作流；普通知识任务只维护自己的 Runtime Context，`/knowledge` 作为领域入口时按 `/goal` 语义请求 Agent 建立 Goal。

## MVP 状态

- [x] `nanobot/knowledge/models.py`：Source、Page、Relation、Entity、IR、Project。
- [x] `store.py`：workspace-scoped、原子写入、路径校验、项目/IR/raw/review/wiki 持久化。
- [x] `service.py`：扫描 source、镜像 raw、保存 IR、编译、校验、Review、发布，并持久化可恢复的 `knowledge/task.json`。
- [x] 编译/校验异常会将 `compile_failed` / `validation_failed` 与有界 `last_error` 写回 task/project，便于恢复和诊断。
- [x] `compiler.py`：frontmatter 页面、`index.md`、`overview.md`、`log.md`、`graph.json`；重复编译合并正文且不重复写 ingest 日志。
- [x] 冲突审查：同一类型/slug 来自不同 source 且正文不一致时生成 `conflict` Review issue，并阻止发布。
- [x] 关系证据校验：`source_path` 必须来自扫描 manifest，`start_line/end_line` 必须成对且满足有效行号范围。
- [x] `knowledge_scan` / `knowledge_extract` / `knowledge_compile` / `knowledge_validate` / `knowledge_publish` 工具。
- [x] `knowledge_search`：仅检索选中的知识项目，支持 page type/tag/source path 过滤，返回有界 quote、wiki 行号和 raw source citations。
- [x] `knowledge-engineering` Skill：约束 scan → extract → compile → validate → publish，并要求保存来源证据。
- [x] `/knowledge [source-directory]`：有路径时初始化可恢复的 KnowledgeProject/Task；无路径时进入 source-selection Runtime 状态；两者都按 `/goal` 语义标记本轮 Goal 请求，不扫描或编译。
- [x] Runtime Context 条件注入：仅在知识请求、已有知识上下文或 WebUI 显式选择项目时注入。
- [x] WebUI 知识库选择器：通过 `/api/sessions/{key}/knowledge-projects` 获取摘要，并在下一条 WebSocket message 中携带 `knowledge_project_id`。
- [x] Knowledge Workspace 轻量入口：项目摘要、任务状态、Raw/IR/Wiki/Graph 快捷预览，复用现有文件树与 FilePreviewPanel。
- [x] Graph preview：在现有 Workspace 摘要内提供受限 Cytoscape.js 关系预览，`graph.json` 仍是持久化真相；无 Canvas 环境有降级提示。
- [x] `.venv\Scripts\python.exe -m pytest tests/knowledge tests/writing -q`：29 passed。
- [x] `webui\bun run build`：TypeScript 与生产构建通过。
- [x] ingestion adapter contract：扫描 manifest 为文本、Markdown、PDF、HTML、图片记录
  `ingestion_adapter`、`extraction_mode` 与受限读取说明；所有原始字节仍镜像到 `raw/`。

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

本轮完成了 MVP 的 durable pipeline：`scan → extract(IR) → compile(wiki + graph) → validate/review → publish`，
并完成了 source manifest 的 ingestion adapter 契约；Knowledge 仍是 Workspace 级能力，不拆成独立 Agent。
`knowledge_validate` 会持久化 Review 结果；验证失败时不会发布。`/knowledge` 只初始化项目/任务元数据并授权 Agent 通过 `create_goal` 建立领域 Goal，不隐藏扫描、抽取或编译；WebUI 仅增加知识项目摘要与快捷入口，不改变 Conversation/Agent Timeline 的既有布局。
`knowledge/task.json` 保存任务阶段、状态、待处理/已处理来源，Runtime Context 只在知识任务或显式选择项目时读取它。

Writing Agent 集成已完成后端第一步：`knowledge_search` 会返回并在会话中保存有界 citations；当当前请求选中了同一 Knowledge project 且 `writing_changeset` 未显式传入 `sources` 时，ChangeSet 会自动携带最近检索的 citations。Writing Runtime Context 只注入选中项目标识和引用数量，不注入整段知识内容。

## 下一步

- [x] 将 Knowledge Workspace 扩展为可折叠的 Raw / IR / Wiki / Graph 分组视图；继续复用现有 Workspace 与 FilePreviewPanel。
- [x] 后端将引用片段映射为统一的 `path + start_line + end_line + quote`，并由 `knowledge_search` 返回可供写作 Agent 复用的 source citations。
- [x] 将最近一次选中知识库检索的 citations 接入 `writing_changeset.sources`，并按 Knowledge project id 防止跨库污染。
- [ ] 完成浏览器端多行引用的手动验收，并确认引用卡片在下一条消息中稳定回传。
- [ ] 完成 PDF/网页/OCR 的实际解析适配器（当前已完成 manifest 契约与 raw 镜像，抽取仍由
  Agent 按契约调用 bounded reader 后提交 IR）。
- [ ] 评估向量检索和 GraphRAG；在证据链、权限边界和可观察性稳定前不全量注入 wiki。
- [ ] 后续再评估 Graph/Wiki 的高级可视化；不引入独立 Knowledge Agent 或多 Agent 协作。

## 已知门禁问题

`tests/tools/test_tool_loader.py` 现有两项测试因 `ToolsConfig` 的 Pydantic forward-reference 未完成而失败（`WebToolsConfig` 未定义），与 Knowledge Runtime 改动无关；应单独修复配置模块后再作为全量门禁。

## 2026-08-05 验证记录

- 真实参考目录 `D:\Users\gyq16\Desktop\PRJ\NANOTEST2\wikis` 可被只读发现为“项目知识库”：185 个 Wiki 页面、14 个原始来源，状态为 `published`。
- `.venv\Scripts\python.exe -m pytest tests/knowledge -q`：22 passed；Knowledge 相关 Ruff 检查通过。
- `.venv\Scripts\python.exe -m pytest tests/knowledge tests/writing -q`：29 passed；Knowledge/Writing 相关 Ruff 检查通过。
- WebUI 的 i18n、文件预览、多行引用、Knowledge Workspace 与 ThreadShell 定向测试：77 passed；本次复跑通过，所有 locale 的资源键结构已对齐。
- 全量 WebUI 在 `--testTimeout=10000` 下为 903/904；剩余失败是未修改的 `ThreadViewport` 动画时序断言（期望 2400，实际值接近目标），不属于 Knowledge/Workspace 变更。

## 2026-08-05 真实模型复核

- 使用本地 WebUI 的真实 DeepSeek 通道在 `D:\Users\gyq16\Desktop\PRJ\NANOTEST3` 执行 `/knowledge sources`，未使用 mock 或离线填充。
- 首次运行暴露了生成质量缺口：实体页的 `description` 过短、`tags/related` 为空；新增 IR schema 约束与 `validate_project()` 质量门禁后，真实运行的 15 个实体与 10 个概念页均通过正文长度、tags、sources 检查。
- 真实任务最终完成 14 个 IR、30 个编译页面（另有 overview 投影）；抽取阶段记录 86 条关系，清理无目标条目后最终图快照为 79 条边。第一次校验的 172 个 wikilink 错误来自 related 使用标题而非 slug，已通过编译器的 title-to-slug 兼容映射固化，并验证 `passed=true`、`quality_issue_count=0`、`issue_count=0`、`published=true`。
- 当前源码复核结果：目标项目 31 个可读 Markdown 页面中，`entity/concept/source` 页面无短正文、无空 tags、无缺失 sources；直接调用当前 `validate_project()` 返回 0 issues。
- 与 `NANOTEST2` 参考库的差异是规模而非结构：参考库 185 页，本轮 30 个抽取页；本轮已对齐 typed frontmatter、source evidence、related/graph、validation/publish 闭环，后续可通过更细粒度的 schema/领域切分提升页面数量与深度。

## 2026-08-05 全新项目清洁回归（最终记录）

- 使用本地 WebUI 的真实 `deepseek-v4-flash` 通道，在 `D:\Users\gyq16\Desktop\PRJ\NANOTEST3` 新建项目 `kb_0286cd6f13724442a25b413c73a3f74e`，未使用 mock、离线填充或人工修正文档。
- Agent 自主完成 `scan → extract → compile → validate → publish`：扫描 14 个 Markdown 源文件，生成 14 个 IR 文件；首次校验发现 19 个问题（2 个正文过短、17 个关系目标问题），随后 Agent 依据校验结果修复 IR，并删除生成目录后重新编译，避免增量产物残留。
- 最终任务状态为 `completed/published`：49 个图节点、142 条关系边、48 个带 frontmatter 的内容页（另含 `index.md`、`log.md`），`quality_issue_count=0`、`issue_count=0`、`published=true`。
- 离线审计结果：`entity/concept/source` 页面无正文过短、无空 tags、无缺失 sources；related 均可解析到标题或图节点；图边端点全部存在。说明质量门和标题到 slug 的兼容映射已在真实模型链路中生效。
- 与 `D:\Users\gyq16\Desktop\PRJ\NANOTEST2\wikis` 的 185 页参考库相比，本轮页面数量仍较少（26 entity、7 concept、14 source），这是模型抽取粒度差异，不是发布结构错误；当前应优先保留“可追溯、可校验、可发布”的闭环，后续再通过 schema 分层、章节级实体拆分提升覆盖率。
