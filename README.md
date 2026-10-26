# YXP 教育 RAG

面向 K12 的教材 + 题库 RAG 系统。管理端负责内容生产与版本化发布，学员端基于已发布索引提供带引用的检索问答；运营前端在独立仓库 `yxp-rag-front`。

> 状态：POC / 可演示。已跑通「内容接入 → 校对 → 审核 → 发布 → 在线问答」闭环，尚未达到生产交付标准，已知限制见文末。

## 架构

```text
yxp-rag-front :5173
  ├── /admin/v1 ──> admin-api :8000   控制面：唯一写入方，内容 / 审核 / 发布 / 评测
  └── /v1       ──> kb-api    :8001   数据面：只读 Milvus 别名，检索 / 重排 / 生成
                              │
              PostgreSQL · MongoDB · Milvus · MinIO
```

两端不通过 RPC 耦合，唯一契约是共享存储 + 版本号 + Milvus 别名。发布时构建 `chunks_<version>` / `questions_<version>`，commit 原子切换 `chunks_active` / `questions_active`，rollback 切回旧版本。

## 代码单元

| 单元 | 路径 | 端口 | 职责 |
| --- | --- | --- | --- |
| 管理端 | `apps/admin-api` | 8000 | 内容接入、教材 / 题库 / 知识点、校对、审核、发布、评测、IAM、审计 |
| 学员端 | `apps/kb-api` | 8001 | `/v1/chat`、`/v1/chat/stream`（SSE）、`/v1/health` |
| 运营前端 | 独立仓库 `yxp-rag-front` | 5173 | 管理后台 13 个页面 + 学员问答页 |

管理端包含 11 个业务模块；入库管线为 LangGraph 五节点（`upload → ocr → split → struct → quality`），学员端查询图为 `confirm → retrieve → rrf → rerank → generate`。

## 快速开始

### 1. 管理端（零外部依赖）

```bash
cd apps/admin-api
uv sync --extra dev
cp .env.example .env          # Windows: Copy-Item .env.example .env
uv run uvicorn app.main:app --reload --port 8000
```

默认使用 SQLite + mongomock + 内存向量 + 本地签名上传 + 内联任务，无需 PostgreSQL / Mongo / Milvus 即可启动；首次启动自动写入演示数据。

- API 文档：<http://127.0.0.1:8000/admin/v1/docs>
- 健康检查：<http://127.0.0.1:8000/admin/v1/health>

### 2. 学员端

学员端需要可用的 Milvus（以及配置的 MongoDB / 模型网关），并复用管理端的环境变量：

```bash
cd apps/kb-api
../admin-api/.venv/bin/python -m uvicorn kb_app.main:app \
  --host 127.0.0.1 --port 8001 \
  --env-file ../admin-api/.env
```

Windows 下解释器路径为 `..\admin-api\.venv\Scripts\python.exe`。首次请求会加载 BGE-M3（约 2.2 GB，CPU 上数秒），进程内只加载一次。

### 3. 前端

从独立仓库 `yxp-rag-front` 安装运行，开发服务器通过 Vite 代理访问 `/admin/v1` 与 `/v1`：

```bash
npm install
npm run dev        # http://localhost:5173
```

## 配置

所有配置见 `apps/admin-api/.env.example` 与 `apps/kb-api/.env.example`：

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 默认 SQLite；生产用 `postgresql+asyncpg://...` |
| `MONGO_URL` | 留空使用 mongomock；生产用 MongoDB |
| `MILVUS_URI` | 留空使用内存索引；学员端与发布需要真实 Milvus |
| `MINIO_*` | 对象存储；默认本地磁盘 |
| `DASHSCOPE_API_KEY` | OCR / 生成 / 重排模型网关 |
| `BGE_M3_PATH` | BGE-M3 本地快照目录，避免运行时联网下载 |
| `KB_*` | 学员端检索窗口、配额、API Key |

`.env`、`.env.remote` 已被 `.gitignore` 排除，请勿提交真实密钥。演示账号密码统一为 `123456`，仅用于本地种子数据，部署前必须修改或关闭 `SEED_ON_START`。

原始教材 PDF、派生语料与运行数据库未纳入仓库（版权与体积原因）；`docs/rag_data/` 只在本地保留。

## 测试

```bash
cd apps/admin-api
uv run pytest -q
uv run ruff check app tests
```

测试使用临时 SQLite 与 mongomock，不依赖外部服务。前端单独执行 `npm run typecheck` 与 `npm run build`。

## 文档

- [产品与技术方案](docs/1_PRD/yxp_prd.md)
- [概要设计总纲](docs/2_概要设计总纲/yxp_概要设计总纲.md)
- [后端架构设计](docs/3_开发文档/后端架构设计.md)
- [学员检索问答服务设计](docs/3_开发文档/学员检索问答服务设计.md)
- [LangGraph 节点编排](docs/3_开发文档/langgraph节点编排.md)
- [项目现状总结与二期规划](docs/3_开发文档/项目现状总结与二期规划.md)

## 已知限制

- 学员端依赖真实 Milvus 与模型网关；没有外部依赖时只能启动管理端。
- 评测仍以代理指标为主，发布门禁的评测阻断在 commit 阶段。
- 教材 / 题目 / 知识点的部分写接口、API Key 签发与租户配额尚未完成。
- 管理端与学员端之间没有分布式事务，回滚只切别名，不回滚 Mongo 资产状态。
- 前端不做密钥托管；生产环境应由同源网关或 BFF 注入鉴权。

更完整的差距与二期计划见[项目现状总结与二期规划](docs/3_开发文档/项目现状总结与二期规划.md)。
