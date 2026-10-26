# 学员检索问答服务（kb-api，`/v1`）

面向学员的只读检索问答服务：复用 `apps/admin-api` 的 Provider / Milvus / Mongo 适配层，
不连接 PostgreSQL，不依赖管理端进程。

```
POST /v1/chat          一次返回完整回答 + 引用
POST /v1/chat/stream   SSE：meta → citation* → delta* → done|error
GET  /v1/health        别名、行数、依赖连通性
```

## 目录

```
kb_app/
├─ main.py                 FastAPI 入口、错误映射、SSE、answer_records 落库
├─ config.py               KB_* 配置（其余基础设施变量沿用 admin-api 的 env）
├─ schemas.py              请求 / 响应模型
├─ auth.py                 X-API-Key 校验（KB_API_KEY 为空时为开发模式）
├─ infra/
│  ├─ retrieval.py         BGE-M3 查询向量 + 教材/题目各 dense/sparse 四路 Milvus 检索
│  └─ llm.py               回答生成、流式生成、rerank
└─ query/
   ├─ state.py             QueryState（TypedDict）
   ├─ graph.py             START → confirm → retrieve → rrf → rerank → generate → END
   └─ nodes/               五个节点，一个文件一个节点
```

设计说明见 [学员检索问答服务设计.md](../../docs/3_开发文档/学员检索问答服务设计.md)。

## 检索通道

| 通道 | 集合 | 字段 | 权重 |
| --- | --- | --- | --- |
| `chunk_dense` | `chunks_active` | `dense_vector`（COSINE） | 1.0 |
| `chunk_sparse` | `chunks_active` | `sparse_vector`（IP） | 1.0 |
| `question_dense` | `questions_active` | `stem_vector`（COSINE） | `KB_QUESTION_WEIGHT` |
| `question_sparse` | `questions_active` | `sparse_vector`（IP） | `KB_QUESTION_WEIGHT` |

四路按 RRF 融合后截断到 `KB_FUSED_TOP_N`；其中最多 `KB_QUESTION_QUOTA` 个槽位预留给题目，
避免 305 条教材片段把 30 道练习题挤出候选窗口。引用带 `source`（`chunk` / `question`），
题目引用额外带 `question_id`、`question_type`、`difficulty`。

## 运行

服务直接复用 admin-api 的虚拟环境（需要 `pymilvus`、`FlagEmbedding`、`torch`）：

```bash
# 先按 apps/admin-api/README.md 准备 apps/admin-api/.env
cd apps/kb-api
../admin-api/.venv/bin/python -m uvicorn kb_app.main:app \
  --host 127.0.0.1 --port 8001 \
  --env-file ../admin-api/.env
```

Windows 下把解释器路径换成 `..\admin-api\.venv\Scripts\python.exe`。

- `--env-file` 让共享变量（`MILVUS_URI`、`MONGO_URL`、`DASHSCOPE_*`、`BGE_M3_PATH` …）与
  管理端保持一致；`KB_*` 变量可写在 `apps/kb-api/.env`（参考 `.env.example`）。
- 首次请求会加载 BGE-M3（约 2.2 GB，CPU 上数秒），provider 在进程内缓存，只加载一次。
- 运行前需保证 Milvus / Mongo / MinIO 可达（配置见 `apps/admin-api/.env`），见仓库根 README。

## 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `KB_API_KEY` | 空 | 非空时校验 `X-API-Key`；为空只在开发环境使用 |
| `KB_DEFAULT_TENANT` | `tnt_yxp` | 请求未带 `tenant_id` 时使用 |
| `KB_TOP_K` | 20 | 单路检索条数 |
| `KB_RRF_K` | 60 | RRF 平滑常数 |
| `KB_QUESTION_WEIGHT` | 1.0 | 题目两路在 RRF 中的权重 |
| `KB_QUESTION_QUOTA` | 3 | 融合窗口中预留给题目的槽位 |
| `KB_FUSED_TOP_N` | 10 | 融合后进入 rerank 的候选数 |
| `KB_RERANK_TOP_N` | 5 | 最终引用条数 |
| `KB_MAX_CONTEXT_CHARS` | 6000 | 提示词中教材片段的总字符上限 |
| `KB_HISTORY_TURNS` | 4 | 保留的历史轮数 |
| `KB_ANSWER_MAX_TOKENS` | 2048 | 回答长度上限 |
| `KB_RECORD_ANSWERS` | true | 是否把问答写入 Mongo `answer_records` |

## 调用示例

```bash
curl -s http://127.0.0.1:8001/v1/health

curl -s -X POST http://127.0.0.1:8001/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <KB_API_KEY>' \
  -d '{"query":"圆锥的体积公式是什么？","tenant_id":"tnt_yxp"}'

curl -sN -X POST http://127.0.0.1:8001/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"query":"圆锥的体积公式是什么？"}'
```

`/v1/chat` 返回：

```json
{
  "request_id": "req_...",
  "answer": "圆锥的体积公式是 ... [1]",
  "citations": [
    {
      "citation_id": "c1",
      "source": "chunk",
      "book_title": "...",
      "page_start": 51,
      "score": 1.0,
      "quote": "..."
    },
    {
      "citation_id": "c2",
      "source": "question",
      "question_id": "qseed_math_018",
      "question_type": "solution",
      "difficulty": "hard",
      "score": 0.92,
      "quote": "题目：...\n答案：...\n解析：..."
    }
  ],
  "confidence": "high",
  "model": "qwen/qwen3.7-flash",
  "rewritten_query": "圆锥的体积公式是什么？",
  "retrieval": {"dense": 20, "sparse": 20, "questions": 20, "fused": 10, "reranked": 5},
  "timings_ms": {"confirm": 0, "retrieve": 2610, "rrf": 0, "rerank": 1694, "generate": 5567},
  "latency_ms": 10941
}
```

## 题目测试数据

题库尚未走完发布流程时，可用管理端脚本把 30 道确定性练习题（数学 20 / 物理 10）写入
Mongo `questions` 并重建线上索引（stage → commit 切别名）：

```bash
cd apps/admin-api
set -a && . ./.env.remote && set +a
./.venv/Scripts/python.exe tools/seed_live_questions.py --tenant tnt_yxp
```

脚本幂等（按 `question_id` upsert，`review_status=approved`），随后 `build_version_index`
会把 `questions_active` / `chunks_active` 一起切到新版本。只改数据不重建索引时加 `--skip-index`。

## 已知限制

1. **题目覆盖有限**：`questions_active` 目前是 30 道种子题（`source=seed`），只覆盖数学 /
   物理部分知识点；正式题库发布后应删除种子题并重新切别名。
2. **没有相关性阈值**：检索固定返回 `KB_TOP_K` 条，离题问题也会带着低分片段交给模型；
   模型按提示词拒答，但更经济的做法是在 rerank 后加 `min_score` 截断。
3. **rerank 依赖 LLM**：默认 `RERANK_PROVIDER=qwen_llm`，每题多一次模型调用；网关限流
   （HTTP 429）时节点自动退化为 RRF 顺序，`rerank_provider` 返回 `rrf-fallback`。
4. **生成失败返回 503**：LLM 网关 429/超时会抛 `ProviderUnavailable`；流式接口改为
   发送 `error` 事件，不中断已发出的 delta。
5. **API Key 未做租户绑定**：目前是单 key 校验，还没有 `api_keys` 签发与配额流程。
