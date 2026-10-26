# YXP Admin API

K12 知识库管理平台后端（管理端 `/admin/v1`）。同仓库的 `apps/kb-api` 提供学员端 `/v1`，两个服务共享 Provider 与存储适配层。

## 本地运行

```powershell
cd apps/admin-api
uv sync --extra dev
Copy-Item .env.example .env
uv run uvicorn app.main:app --reload --port 8000
```

默认使用 SQLite + mongomock + 内存向量别名 + 本地签名上传 + 内联任务，**无需任何外部依赖**即可启动。
首次启动会自动写入演示数据（`SEED_ON_START=true`）。

- 接口文档：<http://127.0.0.1:8000/admin/v1/docs>
- 健康检查：<http://127.0.0.1:8000/admin/v1/health>
- 前端代理：`/admin/v1` → `http://127.0.0.1:8000`

## 演示账号

本地演示账号密码统一为 `123456`，仅用于开发种子数据；部署前必须修改密码或关闭 `SEED_ON_START`。

| 用户名 | 姓名 | 角色 | 租户 |
| --- | --- | --- | --- |
| `admin` | 平台管理员 | 超级管理员 | 上海优学培 |
| `zhangwei` | 张伟 | 教研管理员 | 上海优学培 |
| `liyuan` | 李媛 | 审核员 | 上海优学培 |
| `chenxi` | 陈曦 | 校对员 | 上海优学培 |
| `zhaomin` | 赵敏 | 只读访客 | 北京智学 |
| `sunqi` | 孙琦 | 发布管理员 | 广州启明 |

## 测试

```powershell
uv run pytest -q
uv run ruff check app tests
```

测试使用临时 SQLite 与 mongomock，不依赖外部服务。

## 生产依赖

设置以下环境变量即可切换为生产适配器（见 `.env.example`）：

- `DATABASE_URL=postgresql+asyncpg://...`（PostgreSQL）
- `MONGO_URL=mongodb://...`（MongoDB）
- `MILVUS_URI=http://...` + `pip install .[milvus]`
- `EMBEDDING_PROVIDER=bge_m3` + `pip install .[embeddings]`，并把
  `BGE_M3_PATH` 指向本地 `BAAI/bge-m3` 快照目录（避免运行时联网下载）
- `OBJECT_STORAGE=minio` + MinIO 配置
- `TASK_MODE=celery` + `REDIS_URL`，并启动 `celery -A app.workers.celery_app worker -l info`
- `OCR_PROVIDER=mineru`：给 `MINERU_TOKEN`，或 `MINERU_ACCESS_KEY_ID` +
  `MINERU_SECRET_ACCESS_KEY`（OpenXLab，自动换 JWT 并续期）；都没配时 `auto`
  会降级到 `qwen_vl`（`DASHSCOPE_API_KEY` + `DASHSCOPE_BASE_URL`）

数据库迁移：`uv run alembic upgrade head`。POC 初始迁移直接使用 SQLAlchemy metadata 建表；
后续变更请使用 `alembic revision --autogenerate`。

## 向量化与发布索引

- 解析任务：`EMBEDDING_PROVIDER=bge_m3` 时，`struct` 步骤用本地 BGE-M3 为片段
  生成 1024 维 dense + sparse 向量，为题目生成题干/解答向量；片段连同向量写入
  Milvus 活动集合 `MILVUS_CHUNKS_COLLECTION`（默认 `chunks_live`），题目与知识点写入 Mongo；
  `off`（默认）时 Milvus 模式下无法写片段，任务会以 `PROVIDER_UNAVAILABLE` 失败。
- 发布暂存：`build_version_index()` 读取 `chunks_live` 的全部片段与 Mongo 中
  `approved` / `published` 题目，补齐缺失向量，重建 `chunks_<version>` / `questions_<version>`
  （dense HNSW COSINE + sparse inverted IP），再执行 `stage` 校验与别名切换。
- 首次使用：`uv sync --extra embeddings --extra milvus --extra dev`；
  本地模型路径见 `.env.example` 的 `BGE_M3_PATH`（填入 ModelScope / Hugging Face 快照目录）。

## 解析流程编排（LangGraph）

`modules/ingestion/pipeline.py` 只负责加载任务、选择模式、调用图和落成功/失败状态；
5 个步骤由 `modules/ingestion/graph/` 的 LangGraph 状态机编排：

```
START → upload → ocr → split → struct → quality → END
```

节点基类统一维护 `ingestion_job_step` 状态与 `job.progress`，取消时通过条件边直接到
`END`。节点职责、状态字段和验证记录见
`docs/3_开发文档/langgraph节点编排.md`。

## 清除演示数据

`SEED_ON_START=true` 只用于空库初始化；如果演示数据已经写进过数据库，可用：

```powershell
uv run --env-file .env.remote python -m app.seed.purge
```

只删除 `app/seed/demo.py` 定义的教学业务演示行（教材/章节/知识点/题目/片段、入库任务、
发布单、审核、标注、评测、seed 审计与 outbox）以及两个本地流水线测试任务；
用户、角色、权限、租户保留，登录不受影响。操作不可恢复。

## 目录

```
app/
  api/v1/        HTTP 路由（仅参数校验与依赖注入）
  core/          配置、鉴权、错误、响应封装、权限矩阵
  domain/        纯规则：状态机、题目去重、发布校验
  infra/         PG / Mongo / MinIO / Milvus / 队列 / Provider 契约
  models/        SQLAlchemy ORM（按域拆分）
  modules/       业务用例（service + repository + schemas）
  seed/          演示数据
  workers/       Celery 任务
```

完整设计说明见仓库根目录 `docs/3_开发文档/后端架构设计.md`。
