# nanobot Knowledge Runtime 开发计划

更新时间：2026-08-07

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
- [x] 结构化事实层：`KnowledgeEvidence`、`KnowledgeClaim`、`KnowledgeReviewIssue` 与
  `KnowledgeChangeSet`，并对旧 IR JSON 保持向后兼容。
- [x] bounded ingestion readers：提供 `text_lines`、`markdown_lines`、`docx_text_tables`、
  `pdf_pages`、`image_vision_ocr` 和 `html_text` 读取器；可选依赖缺失时显式返回依赖错误，
  不伪造 OCR 结果，也不把整个 PDF/知识库注入上下文。
- [x] candidate-first 发布门禁：`knowledge_compile` 默认写入候选目录，随后必须经过
  `knowledge_validate` 与 `knowledge_approve` 才能原子地更新 live Wiki/graph；
  `knowledge_reject` 保存反馈而不改写知识库。
- [x] 关系拓扑社区：后端构建无向、去自环、合并正反向重复边的确定性社区，并为节点输出
  `community_id`、`community_size`、`centrality`、`color`；旧图没有这些字段时前端按类型降级。
- [x] Wiki Review 页面：WebUI 独立 Wiki 页提供候选变更集、审查状态、问题类型、来源/页面引用
  与 revision 摘要，继续复用现有 Conversation/Agent Timeline，不改 AgentLoop/AgentRunner。

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
`knowledge_validate` 会持久化 Review 结果；验证失败时不会发布。`/knowledge` 只初始化项目/任务元数据并授权 Agent 通过 `create_goal` 建立领域 Goal，不隐藏扫描、抽取或编译；WebUI 通过独立 Wiki 页面提供项目、知识/文件导航、Graph 与文件预览，但不改变 Conversation/Agent Timeline 的既有布局。
`knowledge/task.json` 保存任务阶段、状态、待处理/已处理来源，Runtime Context 只在知识任务或显式选择项目时读取它。

Writing Agent 集成已完成后端第一步：`knowledge_search` 会返回并在会话中保存有界 citations；当当前请求选中了同一 Knowledge project 且 `writing_changeset` 未显式传入 `sources` 时，ChangeSet 会自动携带最近检索的 citations。Writing Runtime Context 只注入选中项目标识和引用数量，不注入整段知识内容。

## 下一步

- [x] 将 Knowledge Workspace 扩展为可折叠的 Raw / IR / Wiki / Graph 分组视图；继续复用现有 Workspace 与 FilePreviewPanel。
- [x] Wiki 独立页面改为项目下拉选择，并提供“知识 / 文件”两种页面导航模式；Graph 与文件预览保持同级联动，不改变 Conversation / Agent Timeline 布局。
- [x] Graph G1 交互：按连接度缩放节点、可调布局参数、悬停高亮一阶邻域与关系边、节点搜索聚焦，以及节点到对应 Wiki 文件的预览联动。
- [x] Graph G2/G3 核心交互：按知识类型提供社区颜色与筛选、关系边悬停提示、平滑布局与搜索聚焦、节点右键菜单、邻域范围切换，以及 Mini Map。
- [x] Graph 关系无向化：合并同一对节点的正反向关系、最多保留三种关系标签、忽略自环、移除方向箭头；连接度和展示计数均基于去重后的无向边。
- [x] 后端将引用片段映射为统一的 `path + start_line + end_line + quote`，并由 `knowledge_search` 返回可供写作 Agent 复用的 source citations。
- [x] 将最近一次选中知识库检索的 citations 接入 `writing_changeset.sources`，并按 Knowledge project id 防止跨库污染。
- [ ] 完成浏览器端多行引用的手动验收，并确认引用卡片在下一条消息中稳定回传。
- [x] 完成 PDF/网页/DOCX/旧版 DOC/OCR 的 bounded 解析适配器；RapidOCR 可用时执行本地候选转录，
  不可用时保留原图路径与 `ocr_available=false`；最终事实仍必须由 Agent 提交带 evidence 的 IR。
- [x] 大文档续作：旧版 DOC 复用已恢复图片并按批次推进 OCR；PDF 支持 `start_page/end_page`，
  source raw mirror、hash、图片锚点和归一化 manifest 均保留。
- [ ] 接入真正的视觉模型调用与版面/表格专用抽取（当前 reader 只提供本地文本/元数据边界，
  不在本地偷偷安装大型依赖）。
- [ ] 将 Review 页面上的 Accept/Reject 按钮接入独立 HTTP/WebSocket action；当前审批能力已
  在 Knowledge Tools 中可调用，UI 先提供审查结果和 ChangeSet 展示，避免绕过现有权限边界。
- [x] 接入可选 FastEmbed 真实语义索引（当前验证模型 `BAAI/bge-small-zh-v1.5`），保留 feature-hash fallback；
  RAG 继续以 `knowledge_search` / `knowledge_research` Tool 形态提供有界上下文，不全量注入 Wiki。
- [ ] 建立固定标书检索评测集并比较 Recall@K、nDCG@K、查询 p95、索引体积，再决定是否加入 reranker。
- [x] Knowledge Gap Evidence Request：用户可自然回复或上传材料；默认 task-local，明确选择后才作为
  knowledge candidate 进入 ChangeSet/审查/发布链路。
- [ ] 后续再评估 Graph/Wiki 的高级可视化：真正的算法社区聚类、时间轴、HTML/Compound Node、WebGL 大图渲染；当前不引入独立 Knowledge Agent 或多 Agent 协作。

## 2026-08-07 BoClaw 风格结构化知识库实现记录

本阶段将原有“IR 直接编译到 live Wiki”的路径收敛为可审查候选路径：

```text
scan(raw mirror + manifest)
  → extract(IR: pages + claims + evidence)
  → compile(candidate Wiki + graph)
  → validate(candidate + ReviewIssue)
  → knowledge_approve(ChangeSet)
  → atomic apply to live Wiki/graph + revision
```

证据以 `source_path` 为必需 provenance，可附带行号、页码、引用片段、图片路径、抽取方式和
置信度；关系保留 evidence/evidence_refs。编译器仍采用 merge-existing 语义，不覆盖已有页面的
正文历史，旧 `knowledge_publish` 调用在没有活动候选时继续保持兼容。

验证记录：

- `.venv\\Scripts\\python.exe -m pytest tests\\knowledge tests\\writing -q`：37 passed。
- `.venv\\Scripts\\ruff.exe check nanobot\\knowledge nanobot\\agent\\tools\\knowledge.py tests\\knowledge\\test_structured_runtime.py`：通过。
- `webui\\bun run build`：TypeScript 检查与 Vite 生产构建通过；仅保留既有的大 chunk warning。
- `webui\\bun run test -- src/tests/wiki-view.test.tsx`：3 passed。
- 新增 `tests/knowledge/test_structured_runtime.py`，覆盖旧 IR 兼容、Markdown/DOCX/图片 bounded
  reader、候选变更集的未发布状态、Review→Approve、无向去重图和社区字段。

当前尚未完成的边界：

1. 依赖 `pytesseract` 的本地 OCR 未安装；视觉模型适配仍应由 provider/skill 按 source manifest
   选择性调用。
2. UI Review 页目前是可视化审查结果，审批动作仍通过 `knowledge_approve/reject` 工具执行；
   后续可在不改 AgentLoop 的前提下增加受权限保护的 action API。
3. 社区算法是无外部依赖的确定性 label propagation，不宣称等价于 Louvain/Leiden；大型图再评估
   可选依赖和 WebGL。

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
