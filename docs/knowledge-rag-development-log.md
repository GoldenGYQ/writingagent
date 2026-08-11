# Knowledge RAG 开发历程

> 这份日志记录 Knowledge RAG 的技术选型、验证指标和阶段性结果。它是实现记录，不是对运行时上下文的注入内容。

## 目标与边界

- 保留现有 Wiki、`graph.json`、Knowledge Service/Store 和 AgentLoop/AgentRunner。
- 只增加派生索引、受限检索、图扩展和结果展示契约；不引入 Neo4j、复杂 GraphRAG 或新的图数据库。
- 检索结果只返回 bounded metadata/snippet/citation，禁止把整个 Wiki 注入模型上下文。
- 所有写入知识库仍然必须经过 `scan → extract(IR) → compile → validate/review → publish`。

## 架构优先级（本次需求确认）

RAG 的主能力是 **Tool-first**：普通对话中，Agent 根据问题决定是否调用 `knowledge_search`，工具返回受限文档片段、关系和来源引用，模型再基于这些结果回答。Runtime Context 不负责自动注入知识正文。

`knowledge-rag` Skill 只提供使用时机、检索模式、引用和不确定性处理规则；后续的 Agentic RAG 应在此基础上增加“查询分解 → 多次检索 → 证据合并 → 置信度/停止判断”，而不是把检索逻辑塞进 AgentLoop。

Wiki 与 Knowledge Graph 是可选的观察和导航层：用于人工浏览、结果定位和关系可视化，不是 RAG 的必要依赖，也不应成为回答知识问题的唯一入口。

## 技术选型记录

### 2026-08-08：先采用依赖无关的本地基线

当前 `pyproject.toml` 没有 FAISS、Chroma、numpy 或 sentence-transformers。第一版采用固定维度 SHA-256 feature-hash 向量，并保留 `LocalVectorStore` 接口，原因是：

1. 不改变环境、不增加大型依赖，Windows/离线工作区可以直接运行；
2. 向量维度、算法版本和索引文件可复现，便于回归测试；
3. 未来如果接入真实 embedding，只需替换 vector store，不触碰 AgentLoop 和工具契约。

这不是语义 embedding 的最终方案，因此结果中明确返回 `index_algorithm` 和 `fallback`，避免把词法特征误称为模型语义检索。

## 阶段记录

### Phase 1：只读审计（已完成）

- 确认发布 Wiki 位于 `wikis/<project_id>/wiki/`，派生图位于 `wikis/<project_id>/knowledge/graph/graph.json`。
- 确认 `graph.json` 已是无向拓扑快照；当前检索工具此前只有全文 substring 搜索。
- 确认不应改动 AgentLoop/AgentRunner，RAG 应通过 Tool、Service、Store 和 Runtime Context 接入。

### Phase 2：派生索引与统一检索（已完成首个基线）

- 新增 `KnowledgeDocument`、`KnowledgeChunk`、`KnowledgeSearchResult`。
- 新增 Markdown 分段索引、可失效重建 manifest、feature-hash 本地向量索引。
- 索引写入 `knowledge/retrieval/`，属于可删除的派生缓存，不覆盖发布页面。
- `knowledge_search` 保留既有 `matches/claims/citations`，并新增 `documents/relations/retrieval/mode`；旧调用方无需迁移。

### Phase 3：Graph Expansion（已完成首个基线）

- 从现有 `graph.json` 做 0–2 跳 bounded BFS。
- 规范化为无向边、去自环、合并反向重复边，并保留原边字段（包括 evidence/source_path）。

### Phase 4：Runtime/Tool 接入（已完成首个基线）

- 新增 `knowledge-rag` Skill，只描述何时查询、如何引用和如何处理不确定性，不承载业务逻辑。
- Runtime Context 只保存最近一次检索的 query/mode/计数/seed 节点，不注入 snippet 或 Wiki 正文。
- `knowledge_search` 仍是只读 Tool，检索失败不改变发布知识库。

### Phase 5：Wiki 工作台接入（已完成首个基线）

- 新增 `/api/sessions/{key}/knowledge-projects/{id}/search`，复用 Agent Tool 的同一检索器。
- Wiki 页面搜索框在输入至少 2 个字符后 debounce 查询，展示文档命中数/关系数，点击结果打开对应 Wiki 文档；原有 Cytoscape 页面、筛选和详情面板保持不变。

### Phase 6：Bounded Agentic RAG Tool（已完成首个基线）

- 新增 `knowledge_research` 工具。Agent 可以提交 1–4 个聚焦子查询，工具最多执行 4 次既有 `knowledge_search`，合并去重后的 documents/claims/relations/citations。
- 结果包含 `planned_queries`、`executed_queries`、`iterations`、`budget`、`stop_reason` 和失败摘要；支持 `evidence_sufficient`、`budget_exhausted`、`no_results` 三类停止原因。
- 这是上层 Tool 编排，不修改 AgentLoop/AgentRunner，不自动循环调用模型，也不把整个 Wiki 放进上下文。

### 指标与评价方法

| 指标 | 当前基线/目标 | 记录方式 |
| --- | --- | --- |
| 结果上限 | 单次最多 20 个文档、40 条关系 | 单元测试断言与 `retrieval` 元数据 |
| 上下文上限 | 单个 snippet ≤ 1200 字符；不返回全文 | 单元测试检查返回长度 |
| 可复现性 | 同一 manifest/query 得到同序结果 | deterministic vector/retrieval 测试 |
| 索引失效 | 页面 content hash 变化后重建 | indexer 测试 |
| 图扩展 | 最多 2 跳、24 节点 | graph retriever 测试 |
| 性能 | fixture 上记录 build/search wall time，不设不稳定硬门槛 | pytest 输出与后续 benchmark 脚本 |

后续接入真实 embedding 前，必须在同一 fixture 上比较 Recall@K、nDCG@K、索引耗时、查询 p95 和索引体积；不能仅凭主观观感替换当前基线。

## 本次验证记录

- Python：`pytest tests/knowledge -q` → **34 passed**。
- Python lint：Ruff 检查新增索引、检索器、图扩展、Tool、Runtime Context 和 WebUI route → **All checks passed**。
- WebUI：`bun run test -- src/tests/wiki-view.test.tsx --reporter=dot` → **3 passed**。
- WebUI：`bun run build` → **成功**（Vite/TypeScript 构建完成；保留项目原有 chunk size warning）。
- 全量 WebUI 测试未作为通过依据：`bun run test` 在现有 Settings 测试尝试连接 `127.0.0.1:3000` 后超时，未发现由本次 Wiki/RAG 改动引起的编译错误；后续应在测试 mock/服务约束明确后单独修复该环境问题。
- fixture 基线：索引 build wall time 以测试中 `<5s` 作为本机 sanity check；这不是跨机器性能承诺。正式比较需要固定 fixture、模型/维度和冷/热缓存后再记录 p95。

## 下一阶段

- 在固定评测集上比较 lexical、feature-hash 和真实 embedding 的 Recall@K、nDCG@K、p95 延迟与索引体积。
- 将 Agentic RAG 保持为上层 Skill/Tool 编排：查询分解、迭代检索、证据去重、停止条件和预算控制。
- 继续增强前端来源行号、引用跳转和图谱定位，但不把 Wiki UI 作为 RAG 主链路。

## 2026-08-09：可个性化的检索配置

RAG 参数现在通过现有 Settings 链路持久化，不需要重启 Agent Runtime：

- `parameter_mode=auto` 使用保守默认值：hybrid、Top-K 8、图扩展 1 hop、research 最多 3 个子查询；
- `parameter_mode=manual` 使用用户配置的检索模式、Top-K、图扩展深度、research 预算和证据目标；
- `query_rewrite` 控制 `knowledge_research` 是否允许使用 Agent 提交的有界子查询列表。当前不会在后台偷偷调用 LLM 重写，重写仍由 Agent/Skill 负责；
- 单次工具调用显式传入的参数始终覆盖 Settings，保证临时任务可控。

前端新增 Settings → Knowledge / RAG 页面，保存后只影响下一次检索。这样既能展示个性化能力，也不会把一组滑块误认为论文级 GraphRAG 的质量保证。

## 2026-08-09：多模态归一化、真实语义检索与 Writing 闭环

### 文档与 OCR 选型

- DOCX：迁移结构化 ZIP/XML 提取能力，输出 `text.md`、图片、CSV 表格、OMML 公式和 manifest；不要求模型一次读取整个文档。
- 旧版 DOC：目标样本是 699,850,720 字节的 OLE2 文件。实现 FIB 文本跨度恢复，并以 4 MiB 流式窗口从 `Data` stream 恢复 PNG/JPEG；这不是 Word 排版渲染器，WMF/EMF、嵌套 OLE 和页面布局仍属于明确限制。
- OCR：采用 RapidOCR + ONNX Runtime，本地失败时可回退 Tesseract。OCR 结果只作为候选 evidence，图片路径、stream offset、hash 与置信度继续保留；证照、账户、金额和有效期不得仅凭 OCR 自动成为新标书事实。
- 大文件续作：`knowledge_normalize` 复用已有资产 manifest，每次只推进 `max_ocr_assets` 个尚未处理的图片；PDF 支持 `start_page/end_page` 有界页批次。

### 语义检索选型

- 增加可选 FastEmbed 后端，实测模型为 `BAAI/bge-small-zh-v1.5`（512 维）；中文查询使用模型要求的 retrieval instruction 前缀。
- 保留 feature-hash 作为不下载模型的确定性 fallback。索引 manifest 保存实际算法与模型名，设置页可以在 Lightweight / Semantic 间切换。
- 本阶段未加入 reranker。当前排名为 embedding、词法命中与图邻域的有界融合；进入 reranker 前需要固定标书问答集并记录 Recall@K、nDCG@K、p95、内存和索引体积。

### NANOTEST5 实测

- 原始旧版 DOC：699,850,720 字节。
- 正文恢复：35,271 字符。
- 嵌入资产：994 个 PNG/JPEG；流式恢复不将 688 MiB `Data` stream 整体载入内存。
- OCR 首批：8 个资产；续作验证从 8 推进至 9，复用 994 个已恢复资产，5.286 秒完成，没有再次执行图片 carve。
- Knowledge IR：4 个 typed page、1 个 entity、2 条关系、3 个 claim；最终 candidate 校验 0 issues，并产生已批准 revision。
- FastEmbed hybrid 查询“企业资质与许可”返回对应 concept 为首位，发布 Wiki 中早期乱码/重复派生页面已通过 candidate 集合对账清理。
- Writing 验证：检索出的 2 条 Wiki 引用进入 Writing ChangeSet，ChangeSet 成功应用并创建 1 个 Revision；718 字验证草案把历史事实与新标书待补字段分离。

### Knowledge Gap / Evidence Request

`request_user_input` 现在支持非阻塞式 evidence request：用户可以直接在原输入框回复或拖入文档/图片，消息会恢复原任务。`response_scope` 明确区分：

- `task`：默认，只用于当前写作任务；
- `knowledge_candidate`：仅在用户明确要求长期保留时使用，材料仍必须经过 Knowledge IR、ChangeSet、审查和发布门禁。

普通审批表单仍保持阻塞，避免把“补资料”和“批准修改”混成同一种交互。

### 当前边界

1. DOC 图片 carve 不等同于 Word 页面还原，图片与正文只能靠 stream offset、OCR 和后续人工/视觉核验建立证据关系。
2. 994 个资产尚未全部 OCR；工程策略是按写作章节和知识缺口选择性推进，而非盲目全量 OCR。
3. 现场照片无文字时会标记 `requires_vision`，当前尚未接入视觉模型的语义描述。
4. PDF 页批次可以续作，但还没有后台队列、取消/重试 UI 和跨进程进度条。
5. 自然 Evidence Request 已覆盖文本恢复路径；真实大附件上传仍受现有 WebSocket 媒体大小与安全策略约束。

### 质量门禁

- Python 最终扩展回归：Knowledge、Writing、User Input、WebSocket Channel 和 Settings 共 **458 passed**；此前的 309 项阶段回归也已通过。
- BasedPyright：`nanobot/knowledge`、Knowledge/User Input Tool、WebSocket Runtime、Settings API 与 WebUI HTTP Adapter 共 **0 errors / 0 warnings**。
- Ruff：新增 Knowledge、Tool、WebSocket、Settings 与测试代码全部通过。
- WebUI：Interaction/Wiki/Settings 定向测试通过；ThreadShell 59 tests 在 10 秒测试超时下通过（默认 5 秒的一次运行出现已有异步探测时序超时）。
- WebUI production build：TypeScript + Vite 成功；保留现有大 chunk warning。
- `NANOTEST5` 真实闭环复验：FastEmbed 检索结果携带 2 条引用进入 Writing ChangeSet，ChangeSet 自动应用并创建 Revision；验证草稿 718 字符，检索算法记录为 `fastembed:BAAI/bge-small-zh-v1.5`。

## 2026-08-09：FAISS / Chroma / BGE / Reranker 对比

新增 `scripts/benchmark_knowledge_retrieval.py`，把表示模型、向量索引和二阶段重排序拆开评估。NANOTEST4 的 41 个真实 Wiki chunk 与 6 条人工相关性标签表明：BGE 相比 feature hash 主要改善排序质量；NumPy、FAISS FlatIP、Chroma 在该规模质量相同，而 Chroma 固定成本最高；BGE reranker 显著改善 MRR/nDCG，但 CPU p50 约 5.7 秒。因此当前默认继续采用 BGE + 进程内 flat/hybrid，FAISS/Chroma 和 reranker 保留为规模化或高价值任务的可选路径。完整解释、指标与面试表达见 `docs/knowledge-retrieval-experiments.md`。

同时新增 OCR 图片候选标签：证照、银行账户、发票、票据、合同、授权书、检测报告和待视觉识别图片均保留 `document_type/tags/entities/sensitive/review_status/label_method`。标签仅用于证据路由，不自动升级为确定 Knowledge Claim。

## 2026-08-09：NANOTEST5 真实浏览器闭环重建

本轮通过真实 WebUI 会话对 `kb_58a6846508954d74ba494dec82b418e4` 执行长程任务，而不是直接调用后端脚本替代 Agent。实际链路为：Working Plan → bounded normalize → IR extract → compile candidate → validate/review → publish → `knowledge_search` 自测。

- 旧 DOC 已有 994 张恢复图片，本轮 OCR 从 9 条推进到 39 条；资产总数保持不变，未重新 carve 688 MiB Data stream。39 条中 38 张有文本，`asset_000001` 为空，955 张仍待有界 OCR/视觉核验。
- 首次让 DeepSeek 把 28 页 IR 全部塞进一次 `knowledge_extract` tool call 时，连续发生输出截断/上下文压缩，工具始终未真正执行。为此增加 `knowledge_extract.ir_draft_path`：大 IR 可先写入 Knowledge project 内不超过 5 MiB 的 JSON 草稿，再由工具做路径边界、项目和 source 一致性校验后原子导入。
- `max_legacy_assets=0` 现在明确表示 legacy DOC 的 resume-only 模式；只有现存 normalization 的 source hash、size、assets 和 `text.md` 均匹配时才允许，避免为了续做 OCR 重新读取 Data stream。
- 发布结果：28 个内容页（1 source、5 entity、21 concept、1 query），27 条显式关系，图中 49 条无向边，34 条事实级 claim，12 个页面携带图片证据，6 条非阻塞 review issue；硬校验 0 issues。
- 为兼容旧发布页残留的 wikilink，IR 保留 `enterprise-qualifications-and-licenses` 历史索引页，但不再聚合证书事实；它只指向压力容器、管道安装、管道设计、ASME、ISO14001、ISO45001 和 AAA 等细粒度资产。
- 四组 hybrid 自测均首位命中正确资产：特种设备生产许可证 `pipe-design-license-gc2`（0.833）、企业银行账户证明 `basic-bank-account-certificate`（0.786）、投标履约承诺 `bid-letter-and-commitments`（0.829）、合同业绩 `contracts-and-performance`（0.695）。
- WebUI 验收确认 Wiki 导航显示 28 个图节点/49 条关系，证书页面可在 Preview/Source 间切换，Graph 社区、节点详情、来源与 Related 联动正常。

### 本轮仍暴露的质量欠账

1. 旧项目标题和少量 merge-existing 历史标签仍含 `?` 乱码；新页面正文与大多数标签正常，但应在独立迁移中清理旧 metadata，而不是直接修改生成 Markdown。
2. 该项目早期扫描把 14 个诊断脚本/文本也镜像进 raw sources，Wiki 左侧显示 15 个原始资料；后续需要 source include/exclude 规则与重新扫描策略。
3. 38 张有效 OCR 只覆盖证照与审计材料的开头区域，合同附件、发票/票据、现场照片、加工检测设备和荣誉图片仍未逐张核验。
