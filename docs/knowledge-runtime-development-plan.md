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
- [x] `store.py`：workspace-scoped、原子写入、路径校验、项目/IR/wiki 持久化。
- [x] `service.py`：扫描 source、保存 IR、编译、校验、发布。
- [x] `compiler.py`：frontmatter 页面、`index.md`、`overview.md`、`log.md`、`graph.json`；重复编译合并正文且不重复写 ingest 日志。
- [x] `knowledge_scan` / `knowledge_extract` / `knowledge_compile` / `knowledge_validate` / `knowledge_publish` 工具。
- [x] `knowledge_search`：仅检索选中的知识项目，返回有界 snippet 和 wiki 路径/行号。
- [x] `knowledge-engineering` Skill：约束 scan → extract → compile → validate → publish，并要求保存来源证据。
- [x] `/knowledge <source-directory>`：只声明任务边界，实际执行仍由 Agent Runtime 调用工具。
- [x] Runtime Context 条件注入：仅在知识请求、已有知识上下文或 WebUI 显式选择项目时注入。
- [x] WebUI 知识库选择器：通过 `/api/sessions/{key}/knowledge-projects` 获取摘要，并在下一条 WebSocket message 中携带 `knowledge_project_id`。
- [x] `.venv\Scripts\python.exe -m pytest tests/knowledge -q`：4 passed。
- [x] `webui\bun run build`：TypeScript 与生产构建通过。

## 参考目录映射

```text
<workspace>/wikis/<project_id>/
├── raw/                 # 用户原始资料的逻辑来源；MVP 不复制原文
├── schema.md            # schema profile
├── knowledge/
│   ├── manifest.json    # scan 结果
│   ├── ir/*.json        # Agent 结构化抽取中间表示
│   └── graph/graph.json # 可重建关系快照
└── knowledge/wiki/
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

## 下一步

- [ ] 增加 WebUI 知识页面/文件树的专用入口，复用现有 Workspace 与 FilePreviewPanel，不引入第二套预览引擎。
- [ ] 为 `knowledge_search` 增加按 page type、tag、source path 的过滤。
- [ ] 将引用片段映射为统一的 `path + start_line + end_line + quote`，供写作 Agent 输入框复用。
- [ ] 增加 PDF/网页/OCR 的可插拔 ingestion adapter；原始内容仍先进入 raw，再进入 IR。
- [ ] 评估向量检索和 GraphRAG；在证据链、权限边界和可观察性稳定前不全量注入 wiki。
- [ ] 后续再评估独立 Knowledge Agent、多 Agent 协作和图谱可视化。

## 已知门禁问题

`tests/tools/test_tool_loader.py` 现有两项测试因 `ToolsConfig` 的 Pydantic forward-reference 未完成而失败（`WebToolsConfig` 未定义），与 Knowledge Runtime 改动无关；应单独修复配置模块后再作为全量门禁。
