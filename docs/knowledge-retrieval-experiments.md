# Knowledge Retrieval 选型与实验记录

## 1. 三个概念分别处于哪一层

```text
Wiki / Chunk
    ↓
BGE Embedding                 把文本映射为语义向量
    ↓
NumPy / FAISS / Chroma        保存、索引并召回向量
    ↓
Top-N candidates
    ↓
BGE Reranker                  Query 与候选逐对打分，重新排序
    ↓
Top-K context + citations
```

- **BGE** 是模型族，不是向量数据库。本项目使用 `BAAI/bge-small-zh-v1.5` 生成 512 维中文向量，并实验 `BAAI/bge-reranker-base` 做 Cross-Encoder 精排。参考 [BGE 官方文档](https://bge-model.com/)。
- **FAISS** 是面向稠密向量相似度搜索和聚类的索引库。本实验使用 `IndexFlatIP`，它是精确内积搜索，不是近似索引。参考 [FAISS 官方文档](https://faiss.ai/)。
- **Chroma** 是带集合、持久化、元数据和查询 API 的向量数据库。本实验使用本地 Persistent Collection 和 cosine HNSW。参考 [Chroma 官方文档](https://docs.trychroma.com/docs/overview/introduction)。
- **Reranker** 位于召回之后。Embedding 可以独立编码 Query/Document，适合一次召回大量候选；Cross-Encoder 同时读取 Query 与 Document，通常排序更准但成本明显更高。FastEmbed 的相关接口见 [FastEmbed 文档](https://qdrant.github.io/fastembed/)。

## 2. 实验设计

语料使用已发布的 NANOTEST4 Wiki：41 个文档、41 个 chunk。人工建立 6 条检索问题和相关页面标签，指标为：

- Recall@5：相关页面被找回的比例；
- MRR@5：第一个正确结果的倒数排名；
- nDCG@5：综合考虑多个相关页面及其排序位置；
- Build time、Query p50/p95、Index bytes；
- Embedding 和 Rerank 延迟单独记录，避免把不同层成本混在一起。

实验脚本：`scripts/benchmark_knowledge_retrieval.py`。完整 JSON/Markdown 产物保存在 `NANOTEST5/experiments/`。

## 3. 实测结果

### 3.1 Embedding 对比

| Embedding | Embedding 时间 | Recall@5 | MRR@5 | nDCG@5 |
| --- | ---: | ---: | ---: | ---: |
| Feature hash（256 维词法基线） | 23.62 ms | 0.9167 | 0.5278 | 0.6185 |
| BGE-small-zh-v1.5（512 维） | 1686.90 ms | 0.9167 | 0.6722 | 0.6989 |
| BGE + BGE reranker | 1774.36 ms | 1.0000 | 0.9167 | 0.9180 |

BGE 没有提升 Recall@5，但显著提升 MRR/nDCG，说明它主要把正确结果排得更靠前。Reranker 进一步修复了证书页被认证机构页、重复 Query 页压过的问题。

### 3.2 同一批 BGE 向量的索引对比

| Backend | Build | Query p50 | Query p95 | Index bytes | Recall@5 | MRR@5 | nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| NumPy flat IP | 0.006 ms | 0.0051 ms | 0.0116 ms | 83,968 | 0.9167 | 0.6722 | 0.6989 |
| FAISS IndexFlatIP | 64.57 ms | 0.0093 ms | 0.0127 ms | 84,013 | 0.9167 | 0.6722 | 0.6989 |
| Chroma HNSW cosine | 1665.44 ms | 3.6680 ms | 4.2028 ms | 968,868 | 0.9167 | 0.6722 | 0.6989 |

三者质量相同是预期结果：在只有 41 个向量时，它们基本返回同一排序。这个实验不能证明 FAISS 比 NumPy 慢，只能证明在当前极小语料上，索引构建和框架固定成本远大于搜索收益。

### 3.3 Reranker 成本

`BAAI/bge-reranker-base` 将 MRR@5 从 0.6722 提升到 0.9167，nDCG@5 从 0.6989 提升到 0.9180，但 CPU 单查询 p50 约 **5.7 秒**，模型约 1.04GB。因此不应默认用于每次对话：

- 普通对话：BGE + hybrid retrieval；
- 标书定稿、证书核验、来源冲突审查：召回 Top-20 后按需 rerank；
- 后续可评估量化、多语言小模型、GPU 或只对低置信查询触发。

## 4. 当前工程决策

1. 默认保留进程内 flat search：当前个人知识库规模下最简单、最快、最容易调试。
2. FAISS 作为规模扩大后的候选：当 chunk 达到万级以上再做 10K/100K 固定语料压力测试，并比较 Flat、HNSW、IVF 的 recall/latency/memory。
3. Chroma 不作为当前默认依赖：它适合需要持久 Collection、metadata filtering、多项目隔离和服务化查询的阶段，而非几十到几百个 chunk。
4. BGE 是当前默认语义模型；feature hash 保留为离线、无下载 fallback。
5. Reranker 是可选高质量路径，不是默认路径。触发应由任务风险、初检置信度和延迟预算共同决定。

## 5. 面试表达

> 我没有把 FAISS、Chroma 和 BGE 当成三个可替换产品。BGE 位于表示层，FAISS/Chroma 位于向量检索层，Reranker 位于二阶段排序层。我用同一批 BGE 向量对比了 NumPy flat、FAISS FlatIP 和 Chroma HNSW；小规模语料下三者 Recall/MRR 一致，但 Chroma 固定成本更高，所以当前选择简单 flat index。随后加入 BGE Cross-Encoder，MRR@5 从 0.67 提升到 0.92，但 CPU 延迟约 5.7 秒，因此只在高价值定稿和证据核验场景按需触发。这体现的是基于指标和业务 SLA 做选型，而不是为了技术名词堆栈。

## 6. 实验限制

- 6 条人工问题只能作为工程 smoke benchmark，不是论文级评测集；
- NANOTEST5 旧 Wiki 只有 4 个页面，得到 1.0 指标没有区分度，不能用作质量证明；
- 当前没有 10K/100K 真实 chunk，因此不能据此决定近似索引参数；
- 评测集需继续加入负例、同名证书、过期证照、公司主体混淆和跨页问题。
