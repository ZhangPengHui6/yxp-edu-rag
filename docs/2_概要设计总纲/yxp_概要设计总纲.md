# YXP 知识管理平台 — 概要设计总纲

> 版本：v0.1  
> 日期：2026-10-24  
> 需求依据：`docs/1_PRD/yxp_prd.md`（v0.4）  
> 本文范围：知识管理平台的模块、架构、存储、前后端工程约定；不展开接口字段级设计

---

## 1. 项目背景

### 1.1 现状

PRD 已经定义了面向学生的 RAG 服务（OCR → 题库检索 → 解题/答疑），但内容生产侧仍是空白：

- 教材、试卷、题库靠脚本导入，字段不规范，失败无重试、无进度。
- 题目去重、公式归一化、知识点标注靠人工和 Excel。
- 审核没有状态机，谁改了什么、能不能发布，无法追溯。
- 发布靠手工重建 Milvus，切错了只能停机回滚。
- 题库版本、教材版本、索引版本对不上，出问题无法定位是哪一版。
- 授权到期、教材下架没有系统动作，只能靠人记。

### 1.2 建设目标

知识管理平台不是"再做一个后台"，而是把知识资产变成**可生产、可审核、可发布、可回滚、可度量**的工业化流水线：

1. 统一入口：教材、试卷、题库、知识点、授权都在一个平台管理。
2. 流水线：上传 → 解析 → 校对 → 标注 → 审核 → 发布 → 索引，全链路有状态、可重试。
3. 版本化：每次发布生成不可变快照，支持灰度与一键回滚。
4. 质量闭环：金标集回归、人工抽检、用户反馈都进入同一套质量账本。
5. 权限与审计：按租户/学科/角色隔离，所有写操作留痕。

### 1.3 P0 边界

做：

- 教材/试卷/题库的接入、解析、校对、审核、发布。
- 知识点树与题目/教材绑定。
- 索引构建、别名切换、回滚。
- 最小评测能力（100 题金标集 + 回归报告）。
- 租户、用户、角色、审计的基础能力。

不做（P1 及以后）：

- 多人实时协同编辑、评论、@提醒。
- 复杂 BPM 审批流（先用固定状态机）。
- 自动命题、自动标注的规模化生产。
- 面向学生的任何页面；学生侧仍由知识库项目/客户前端承接。
- 计费、结算、发票。

---

## 2. 知识管理平台与知识库项目的关系

### 2.1 两个项目的定义

| 项目 | 定位 | 核心职责 | 典型使用者 |
|---|---|---|---|
| **知识管理平台（本项目）** | 知识生产与控制面 | 内容接入、校对、审核、发布、索引、评测、授权、审计 | 内容运营、教研、平台管理员 |
| **知识库项目（PRD）** | 知识消费与数据面 | OCR、题库检索、解题、答疑、练习、学情 | 学生（经客户前端）、客户系统 |

一句话：**管理平台负责"把知识做对、管住、发出去"，知识库项目负责"把知识用起来"。**

### 2.2 关系图

```
┌──────────────────────────┐          ┌──────────────────────────┐
│      知识管理平台         │          │       知识库项目          │
│  内容接入 / 校对 / 审核   │          │  OCR / 检索 / 解题 / 答疑 │
│  发布 / 索引 / 评测       │          │  练习 / 错题 / 掌握度     │
└────────────┬─────────────┘          └────────────▲─────────────┘
             │ 写（唯一写入方）                       │ 只读
             ▼                                      │
┌────────────────────────────────────────────────────────────────┐
│                     共享知识资产层                              │
│  MongoDB：教材/章节/题目/知识点/授权/租户                       │
│  Milvus：chunks_live / chunks_<v> / questions_<v>（别名切换）  │
│  MinIO：原始文件/解析产物/图片                                  │
└────────────────────────────────────────────────────────────────┘
                                                      ▲
                                                      │ 在线请求
                                              学生 / 客户系统
```

### 2.3 职责边界

| 事项 | 管理平台 | 知识库项目 |
|---|---|---|
| 教材/题库/知识点的增删改 | ✅ 唯一写入方 | ❌ 只读 |
| 解析、OCR、校对、标注 | ✅ | ❌ |
| 审核、发布、回滚、下架 | ✅ | ❌ 只感知版本/别名 |
| 向量索引构建 | ✅ | ❌ 只查询 |
| 在线检索、解题、答疑 | ❌ | ✅ |
| 学情、会话、错题、掌握度 | ❌ 不写；只读脱敏聚合 | ✅ 唯一写入方 |
| 租户与 API Key 元数据 | ✅ 管理 | ✅ 校验/使用 |
| 学生侧页面 | ❌ | 由客户前端/知识库项目承接 |

**硬规则：**

1. 知识库项目不得写教材、题库、知识点、索引。
2. 管理平台不得写学情、会话、错题、掌握度。
3. 两端不共享数据库连接串之外的服务调用；在线链路不经过管理平台。
4. 唯一契约是**共享存储 + 版本号 + Milvus 别名**，不是 RPC。

### 2.4 发布如何被知识库感知

不引入消息总线也能解耦，POC 用两级机制：

1. **版本过滤**：资产带 `publish_status=published` 与 `version`；知识库查询只读已发布版本。
2. **别名切换**：Milvus 使用 `chunks_active`、`questions_active` 两个别名；管理平台构建新集合成功后切别名。
3. **短缓存**：知识库对别名解析结果缓存 60s，发布后最迟 60s 生效；需要秒级生效时再订阅 Redis Stream。
4. **回滚**：把别名切回上一版本即可，不删数据。

### 2.5 仓库与部署关系

POC 建议同仓多应用，减少契约漂移；规模化后可拆仓。

```
repo/
├─ apps/admin-api/          # 管理平台后端（FastAPI）
├─ apps/admin-web/          # 管理平台前端（Vue 3）
├─ services/knowledge-base/ # 知识库项目（已有/另仓）
└─ packages/contracts/      # OpenAPI、枚举、错误码、JSON Schema
```

部署上是两个独立单元：管理平台故障不影响在线答题；知识库故障不影响内容生产。

---

## 3. 模块划分

### 3.1 模块总览

```
┌──────────────────────────────────────────────────────────────┐
│                         管理端 Web                            │
└───────────────────────────┬──────────────────────────────────┘
                            │ /admin/v1
┌───────────────────────────▼──────────────────────────────────┐
│  接入与权限：认证 / RBAC / 数据范围 / 限流 / 审计前置          │
└───────────────────────────┬──────────────────────────────────┘
        ┌───────────┬───────┼────────┬───────────┬───────────┐
        ▼           ▼       ▼        ▼           ▼           ▼
   内容接入      教材管理  题库管理  知识点管理   校对标注    审核发布
        │           │       │        │           │           │
        └───────────┴───────┴────────┴───────────┴─────┬─────┘
                                                        ▼
                                                   索引管理
                                                        │
                                                   评测中心
                                                        │
                                             租户权限 / 运营审计
```

### 3.2 模块职责

| # | 模块 | 职责 | 关键对象 | P0 |
|---|---|---|---|---|
| 1 | 内容接入 | 上传/拉取、格式校验、解析编排、OCR、题块切分、失败重试、进度 | `ingestion_job`、`parse_artifact` | ✅ |
| 2 | 教材管理 | 书目元数据、章节树、页码映射、版本、授权绑定、下架 | `textbooks`、`chapters`、`licenses` | ✅ |
| 3 | 题库管理 | 题目 CRUD、批量导入、归一化、去重、变式、答案解析、质量分 | `questions`、`question_variants` | ✅ |
| 4 | 知识点管理 | 知识点树、别名、关系图谱、教材/题目绑定、覆盖统计 | `knowledge_points`、`kp_relations`、`coverage_stats` | ✅ |
| 5 | 校对标注 | 低置信度队列、公式/图片/表格修正、题块合并拆分 | `annotation_task`、`ocr_result` | ✅ |
| 6 | 审核发布 | 审核流、抽检、发布单、快照、灰度、回滚、下架 | `review_task`、`publish_order` | ✅ |
| 7 | 索引管理 | 向量化、collection/别名、重建、增量、索引健康 | `index_job`、`index_alias` | ✅ |
| 8 | 评测中心 | 金标集、回归、指标、A/B、反馈闭环 | `eval_dataset`、`eval_run`、`feedback` | ✅ 最小 |
| 9 | 租户权限 | 租户、用户、角色、数据范围、API Key 元数据、配额 | `tenants`、`sys_user`、`sys_role` | ✅ 简化 |
| 10 | 运营审计 | 工作台、任务中心、用量/成本、审计、告警 | `audit_log`、`metric_snapshot` | ✅ 基础 |

### 3.3 模块依赖规则

- 依赖方向：接入 → 教材/题库/知识点；校对 → 接入；审核发布 → 教材/题库/知识点；索引 → 审核发布；评测 → 索引/题库。
- 模块之间只通过 **service 接口** 或 **领域事件** 调用，禁止跨模块直接查表/跨模块 repository。
- 所有资产写入先落 `draft`，只有发布模块能把状态改为 `published`。
- 所有写操作必须经过审计模块，审计日志 append-only。

### 3.4 资产状态机

```
draft → parsing → parsed → annotated → reviewing → approved → published → deprecated
             ↘ failed（可重试）
```

- `reviewing` 可驳回回 `annotated`。
- `published` 只能通过下架进入 `deprecated`。
- 状态跃迁由发布模块统一校验，其他模块只提交动作，不直接改状态。

---

## 4. 分层架构图

### 4.1 分层

```
┌─────────────────────────────────────────────────────────────────────┐
│ 接入层                                                              │
│ 管理端 Web（Vue3） │ OpenAPI（内部系统/脚本） │ Webhook/回调          │
│ Nginx/Ingress → FastAPI：JWT/API Key、RBAC、限流、请求追踪           │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│ 应用层（用例编排）                                                   │
│ 内容接入 │ 教材 │ 题库 │ 知识点 │ 校对 │ 审核发布 │ 索引 │ 评测 │ IAM │
│ 只做流程编排、事务边界、权限校验；不写 SQL/不拼查询                   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│ 领域层                                                               │
│ 资产模型 │ 状态机 │ 版本/快照 │ 归一化/去重规则 │ 发布策略 │ 校验规则 │
│ 纯 Python，不依赖 FastAPI/数据库驱动，可单测                          │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│ 基础设施层                                                           │
│ Repository：PG / Mongo / Milvus / MinIO                              │
│ Provider：OCR / LLM / Embedding / Rerank                             │
│ Queue：Celery + Redis；Cache：Redis；Telemetry：OTel                  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
        ┌──────────┬────────────┼──────────┬──────────┬──────────┐
        ▼          ▼            ▼          ▼          ▼          ▼
   PostgreSQL   MongoDB      Milvus      MinIO      Redis    Celery Worker
   平台事务     知识资产      向量索引     文件       缓存/队列   解析/索引/评测
```

### 4.2 各层职责

| 层 | 允许做 | 禁止做 |
|---|---|---|
| 接入层 | 鉴权、参数校验、限流、路由、统一错误 | 业务规则、直接查库 |
| 应用层 | 用例编排、事务边界、权限判断、发事件 | 拼 SQL、调 SDK、写解析逻辑 |
| 领域层 | 状态机、规则、值对象、领域服务 | 依赖 HTTP/DB/云 SDK |
| 基础设施层 | 数据访问、外部 API、任务队列 | 业务判断 |

### 4.3 一次"导入 → 发布"的时序

```
运营上传文件
  → 接入层校验类型/大小/租户
  → 创建 ingestion_job(status=pending)，文件直传 MinIO
  → Celery 解析任务：MinerU/OCR → 结构化题目 → 归一化 → 去重
  → 写 draft 资产（Mongo），低置信度进 annotation_task
  → 教研校对/标注 → 提交审核 → review_task
  → 审核通过 → 创建 publish_order
  → 索引任务：构建新 Milvus collection/partition
  → 发布：Mongo 标记 published + 切别名（两阶段，失败可重入）
  → 写 audit_log + outbox
  → 知识库最迟 60s 感知新版本
```

### 4.4 发布的两阶段一致性

Mongo 与 Milvus 无法共享事务，发布必须两阶段：

1. **Stage**：资产写 `publish_status=staged`；构建新索引集合；失败可整批丢弃。
2. **Commit**：Mongo 批量改 `published`；切换 Milvus 别名；写 `publish_order`。
3. **补偿**：提交中途失败，后台 reconciler 按 `publish_id` 幂等重试；超过阈值告警。
4. **回滚**：别名切回旧版本 + Mongo 改回旧版本状态；不物理删除。

---

## 5. 数据存储与中间件

### 5.1 选型

| 组件 | 版本 | 用途 | 是否与知识库共享 |
|---|---|---|---|
| PostgreSQL | 16 | 平台事务：用户、角色、审核、发布单、任务、审计、评测运行 | ❌ 平台独占 |
| MongoDB | 7 | 知识资产：教材、章节、题目、知识点、授权、租户、API Key | ✅ 共享 |
| Milvus | 2.5 | `chunks_live`、`chunks_<version>`、`questions_<version>` 向量与稀疏索引 | ✅ 共享 |
| MinIO | 最新稳定版 | 原始文件、解析产物、图片、评测集 | ✅ 共享 |
| Redis | 7 | 缓存、分布式锁、Celery broker、限流 | ❌ 平台独占（与知识库分实例） |
| Celery | 5 | 解析、OCR、索引、评测异步任务 | ❌ 平台独占 |
| OpenTelemetry + Prometheus + Grafana | — | 追踪、指标、告警 | ❌ |
| Nginx / Ingress | — | 静态资源、反向代理、TLS | ❌ |

### 5.2 为什么是 PostgreSQL + MongoDB

| 方案 | 结论 | 原因 |
|---|---|---|
| 全 PostgreSQL | 不选 | 教材/题目嵌套结构多，模式变更频繁；与知识库共享时驱动/模型不一致 |
| 全 MongoDB | 不选 | 审核、发布、权限需要多文档事务与唯一约束，Mongo 做起来别扭 |
| **PG + Mongo** | **选** | PG 管流程与事务，Mongo 管资产与共享；边界清晰 |

知识库项目只读 Mongo/Milvus/MinIO，不读 PG。这样管理平台的表结构怎么改，都不会影响在线服务。

### 5.3 存储边界

| 数据 | 存储 | 写入方 | 读取方 |
|---|---|---|---|
| 用户/角色/权限 | PG | 管理平台 | 管理平台 |
| 审核任务/发布单/任务/审计 | PG | 管理平台 | 管理平台 |
| 评测集/评测运行 | PG + MinIO | 管理平台 | 管理平台 |
| 教材/章节/题目/知识点/授权 | MongoDB | 管理平台 | 管理平台、知识库 |
| 租户/API Key 元数据 | MongoDB | 管理平台 | 管理平台、知识库 |
| 向量索引 | Milvus | 管理平台 | 知识库 |
| 原始文件/图片/解析产物 | MinIO | 管理平台 | 管理平台、知识库 |
| 会话/学情/错题/掌握度 | MongoDB | 知识库 | 知识库；管理平台只读脱敏聚合 |

### 5.4 核心表/集合（最小集）

**PostgreSQL**

| 表 | 关键字段 |
|---|---|
| `sys_user` | `user_id`, `username`, `password_hash`, `status`, `tenant_id` |
| `sys_role` / `sys_user_role` | `role_id`, `code`, `name`; `user_id`, `role_id` |
| `sys_permission` | `code`, `resource`, `action` |
| `ingestion_job` | `job_id`, `tenant_id`, `type`, `source_uri`, `status`, `step`, `error`, `created_by` |
| `review_task` | `task_id`, `asset_type`, `asset_id`, `version`, `status`, `assignee`, `decision`, `comment` |
| `publish_order` | `publish_id`, `scope`, `versions`, `status`, `alias`, `created_by`, `approved_by` |
| `eval_run` | `run_id`, `dataset_id`, `metrics`, `status`, `started_at`, `finished_at` |
| `audit_log` | `id`, `actor`, `action`, `resource`, `before`, `after`, `trace_id`, `created_at` |
| `outbox_event` | `id`, `topic`, `payload`, `status`, `retry_count` |

**MongoDB**

| 集合 | 关键字段 |
|---|---|
| `textbooks` | `book_id`, `title`, `subject`, `grade`, `edition`, `license_id`, `version`, `publish_status` |
| `chapters` | `chapter_id`, `book_id`, `parent_id`, `level`, `title`, `page_start`, `page_end` |
| `questions` | `question_id`, `stem`, `stem_normalized`, `formula_signature`, `simhash`, `answer`, `analysis`, `kp_ids`, `review_status`, `bank_version` |
| `question_variants` | `variant_id`, `parent_question_id`, `changes`, `answer`, `verified` |
| `knowledge_points` | `kp_id`, `name`, `subject`, `grade`, `parent_id`, `aliases` |
| `kp_relations` | `from_kp`, `to_kp`, `type`, `weight` |
| `licenses` | `license_id`, `publisher`, `scope`, `start_at`, `end_at`, `status` |
| `tenants` / `api_keys` | `tenant_id`, `name`, `status`; `key_id`, `key_hash`, `scopes`, `rate_limit` |
| `coverage_stats` | `subject`, `grade`, `kp_id`, `count`, `difficulty_dist` |

**Milvus**

| Collection | 内容 | 别名 |
|---|---|---|
| `chunks_live` | 入库切片（活动集合）：`chunk_id`, `tenant_id`, `job_id`, `book_id`, `kp_ids`, `content`, `page_start/page_end`, `dense_vector`, `sparse_vector` | — |
| `chunks_<version>` | 发布快照：检索字段为 `chunk_id`, `tenant_id`, `book_id`, `chapter_id`, `subject`, `stage`, `grade`, `kp_ids`, `content_type`, `page_start/page_end`, `dense_vector`, `sparse_vector`（不含正文与书名，正文只留在 `chunks_live`） | `chunks_active` |
| `questions_<version>` | 题库：`question_id`, `tenant_id`, `subject`, `grade`, `kp_ids`, `stem_vector`, `solution_vector`, `sparse_vector` | `questions_active` |

### 5.5 索引与版本

- PG：所有业务表带 `tenant_id` 索引；`ingestion_job(status, created_at)`、`review_task(assignee, status)`、`publish_order(created_at)`、`audit_log(created_at)`。
- Mongo：`questions(question_id)` 唯一、`questions(kp_ids, difficulty)`、`questions(simhash)`、`textbooks(book_id)` 唯一、`chapters(book_id, parent_id)`。
- Milvus：稠密 HNSW（M=16，efConstruction=200）；稀疏倒排；标量字段建过滤索引。
- 版本：`book_version`、`bank_version`、`index_version` 三者写入同一条 `publish_order`，出问题可按发布单定位。
- 别名：只切别名，不直接改集合名；旧版本保留至少 2 个发布周期。

### 5.6 中间件与任务

- Celery 队列拆分：`parse`（CPU/IO 混合）、`ocr`（外部 API）、`index`（Milvus）、`eval`（离线）。
- 任务必须幂等：以 `job_id + step` 为幂等键；失败重试 3 次，进入死信队列并告警。
- 长任务（整本教材解析）拆分成分片任务，父任务聚合状态。
- 分布式锁用 Redis `SET NX EX`，锁粒度：`publish:{publish_id}`、`index:{collection}`。
- 禁止在 Web 进程里跑超过 5s 的解析/索引任务。

### 5.7 可观测

- `trace_id` 从 HTTP 头贯穿到 Celery 任务；任务参数携带 `trace_id`。
- 指标：任务成功率、各步骤耗时、OCR 置信度分布、发布成功率、索引构建耗时、审核积压。
- 日志：结构化 JSON，按 `trace_id` / `job_id` / `publish_id` 检索；不记录密钥、原文全文。
- 告警：任务失败率 > 5%、审核积压 > 200、发布失败、Milvus 别名缺失、Mongo/PG 延迟异常。

### 5.8 POC 部署拓扑

```
                 ┌──────────────┐
                 │  Nginx       │
                 └───┬──────┬───┘
         静态资源        │      │ /admin/v1
                 ┌──────▼──┐  ┌▼─────────────┐
                 │admin-web│  │ admin-api ×2 │
                 └─────────┘  └──┬───────────┘
                                 │
        ┌──────────┬─────────────┼──────────┬──────────┐
        ▼          ▼             ▼          ▼          ▼
   PostgreSQL   MongoDB       Milvus      MinIO      Redis
                                 ▲
                          ┌──────┴───────┐
                          │ Celery Worker│
                          │ + Beat       │
                          └──────────────┘
```

- POC：单机 Docker Compose，8C16G 起步。
- 规模化：API/Worker 无状态水平扩展；Milvus 升 Cluster；PG 主从；Mongo 副本集。
- 容量预估（POC）：题目 < 1 万、切片 < 5 万、MinIO < 50GB、PG < 5GB。

---

## 6. 后端开发框架与目录结构

### 6.1 技术栈

| 项 | 选型 |
|---|---|
| 语言 | Python 3.12 |
| Web | FastAPI + Uvicorn |
| 校验 | Pydantic v2 |
| 关系库 | SQLAlchemy 2.0 async + asyncpg + Alembic |
| 文档库 | Motor（异步 PyMongo） |
| 向量库 | pymilvus |
| 对象存储 | minio-py |
| 任务 | Celery 5 + Redis |
| 日志 | structlog |
| 追踪 | OpenTelemetry |
| 测试 | pytest + pytest-asyncio + testcontainers |
| 代码质量 | ruff + mypy + pre-commit |

**不选 Django 的原因**：后台脚手架不是主要价值，平台页面高度定制；异步与 LangGraph/模型 SDK 更顺；与知识库项目技术栈一致，减少切换成本。  
**不拆微服务的原因**：POC 团队小、模块耦合在"发布"这一个动作上；先模块化单体 + 独立 Worker，等发布/索引成为瓶颈再拆。

### 6.2 目录结构

```
apps/admin-api/
├─ pyproject.toml
├─ alembic.ini
├─ alembic/
│  └─ versions/
├─ app/
│  ├─ main.py
│  ├─ core/
│  │  ├─ config.py          # 环境变量、Provider 配置
│  │  ├─ security.py        # JWT、密码、API Key
│  │  ├─ errors.py          # 统一错误码与异常
│  │  ├─ logging.py
│  │  ├─ pagination.py
│  │  └─ deps.py            # 当前用户、租户、权限依赖
│  ├─ api/
│  │  └─ v1/
│  │     ├─ router.py
│  │     ├─ ingestion.py
│  │     ├─ textbooks.py
│  │     ├─ questions.py
│  │     ├─ knowledge_points.py
│  │     ├─ annotation.py
│  │     ├─ review.py
│  │     ├─ publish.py
│  │     ├─ index.py
│  │     ├─ evaluation.py
│  │     ├─ tenants.py
│  │     └─ audit.py
│  ├─ modules/
│  │  ├─ ingestion/
│  │  │  ├─ service.py
│  │  │  ├─ rules.py
│  │  │  └─ schemas.py
│  │  ├─ textbook/
│  │  │  ├─ service.py
│  │  │  ├─ repository.py   # Mongo
│  │  │  └─ schemas.py
│  │  ├─ question/
│  │  ├─ knowledge/
│  │  ├─ annotation/
│  │  ├─ review/
│  │  ├─ publish/
│  │  ├─ index/
│  │  ├─ evaluation/
│  │  ├─ iam/
│  │  └─ audit/
│  ├─ domain/
│  │  ├─ asset.py           # 资产状态机、版本
│  │  ├─ question_rules.py  # 归一化/去重/质量门槛
│  │  └─ publish_rules.py   # 发布校验、快照
│  ├─ infra/
│  │  ├─ pg.py
│  │  ├─ mongo.py
│  │  ├─ milvus.py
│  │  ├─ minio.py
│  │  ├─ redis.py
│  │  ├─ queue.py
│  │  └─ providers/
│  │     ├─ ocr.py
│  │     ├─ llm.py
│  │     ├─ embedding.py
│  │     └─ rerank.py
│  ├─ workers/
│  │  ├─ celery_app.py
│  │  ├─ parse_tasks.py
│  │  ├─ ocr_tasks.py
│  │  ├─ index_tasks.py
│  │  └─ eval_tasks.py
│  └─ shared/
│     ├─ ids.py
│     ├─ time.py
│     └─ text.py
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  └─ fixtures/
└─ scripts/
```

### 6.3 分层与依赖

```
api (router) → modules/*/service → domain/* + repository/provider
                      │
                      └─ 事务边界在 service，不在 router
```

- `api/*` 只做参数解析、权限依赖、调用 service、返回 schema。
- `service` 负责用例、事务、幂等、事件；一个用例一个方法。
- `repository` 只做数据访问，不写业务判断；返回领域对象或 dict。
- `domain` 纯函数/纯类，不 import FastAPI、数据库驱动、云 SDK。
- 禁止：router 直接调 repository；service 调另一个模块的 repository；provider 里写业务规则。

### 6.4 数据与事务约定

- PG 事务边界在 service；跨模块写操作要么同事务，要么走 outbox。
- Mongo 不支持跨文档事务时，用 `version` 乐观锁 + 幂等 upsert；批量发布用 `bulk_write`。
- 不跨 PG/Mongo 做分布式事务：先写 PG 状态 + outbox，再由任务推进 Mongo/Milvus。
- 所有时间存 UTC；API 输出 ISO 8601。
- 金额/成本用 `Decimal`，禁止 float。
- 软删除：`deleted_at`；查询默认过滤；审计保留。
- 乐观锁：资产表带 `version`，更新必须带旧版本号。
- 幂等：导入、发布、索引重建必须支持 `Idempotency-Key`。

### 6.5 命名与接口约定

- API 前缀 `/admin/v1`，与知识库 `/v1` 明确分开。
- 资源用复数：`/admin/v1/questions`、`/admin/v1/textbooks`。
- 动作型接口用子资源：`POST /questions/{id}/publish`、`POST /publish-orders/{id}/rollback`。
- ID：`{prefix}_{ulid}`，如 `q_01J...`、`pub_01J...`；不暴露自增 ID。
- 错误响应沿用 PRD：
  ```json
  {"error":{"code":"REVIEW_REQUIRED","message":"...","details":{},"request_id":"..."}}
  ```
- 列表统一 `limit` + `cursor`；禁止 offset 深分页。
- OpenAPI 是唯一契约；前端类型由 OpenAPI 生成，不手写 DTO。

### 6.6 安全与审计

- 管理端用 JWT；机器调用用 API Key；两者分离。
- RBAC + 数据范围：角色决定动作，数据范围决定能看哪些租户/学科。
- 密码 Argon2；密钥只存哈希；日志脱敏。
- 所有写操作写 `audit_log`（谁、何时、对什么、改前/改后、trace_id）。
- 上传文件做类型/大小/病毒扫描；MinIO 用预签名 URL，文件不经过 API 进程转发。
- 下载/预览同样用短期签名 URL，不暴露永久地址。

### 6.7 测试

- 单元测试覆盖 domain（状态机、归一化、去重、发布规则）。
- 集成测试用 testcontainers 起 PG/Mongo/MinIO；Milvus 可用 mock 或本地 Lite。
- 每个模块至少一条"正常流 + 一条失败重试"用例。
- 发布流程必须有端到端测试：导入 → 审核 → 发布 → 别名切换 → 回滚。

---

## 7. 前端开发框架与目录结构

### 7.1 技术栈

| 项 | 选型 | 说明 |
|---|---|---|
| 框架 | Vue 3.5 + TypeScript 5 | 管理端以表格/表单为主，Vue 开发效率高 |
| 构建 | Vite 6 | 快、配置少 |
| 路由 | Vue Router 4 | 动态路由 + 权限 meta |
| 状态 | Pinia | 只放会话、字典、权限；列表状态放 URL |
| UI | Element Plus | 表格、表单、弹窗、上传成熟 |
| 请求 | Axios + 统一拦截器 | 错误码、trace_id、登录过期统一处理 |
| 图表 | ECharts | 覆盖率、任务量、质量趋势 |
| 类型 | openapi-typescript 生成 | 禁止手写接口类型 |
| 测试 | Vitest + Playwright | 关键流程 E2E |

> 若团队已有 React 技术栈，可替换为 React 18 + Ant Design 5，目录约定与分层不变。

### 7.2 目录结构

```
apps/admin-web/
├─ package.json
├─ vite.config.ts
├─ src/
│  ├─ main.ts
│  ├─ App.vue
│  ├─ api/
│  │  ├─ client.ts          # Axios 实例、错误拦截
│  │  ├─ generated/         # OpenAPI 生成，禁止手改
│  │  └─ modules/           # 按模块薄封装
│  ├─ router/
│  │  ├─ index.ts
│  │  ├─ routes.ts
│  │  └─ guards.ts          # 登录、权限、租户
│  ├─ stores/
│  │  ├─ auth.ts
│  │  ├─ tenant.ts
│  │  └─ dict.ts
│  ├─ layouts/
│  │  ├─ AdminLayout.vue
│  │  └─ components/
│  ├─ components/
│  │  ├─ DataTable/
│  │  ├─ FormDrawer/
│  │  ├─ StatusTag/
│  │  ├─ UploadDropzone/
│  │  └─ MetricCard/
│  ├─ modules/
│  │  ├─ dashboard/
│  │  ├─ ingestion/
│  │  ├─ textbook/
│  │  ├─ question/
│  │  ├─ knowledge/
│  │  ├─ annotation/
│  │  ├─ review/
│  │  ├─ publish/
│  │  ├─ evaluation/
│  │  └─ iam/
│  ├─ styles/
│  │  ├─ tokens.css
│  │  └─ index.css
│  ├─ types/
│  └─ utils/
├─ tests/
└─ e2e/
```

每个业务模块内部固定：

```
modules/question/
├─ pages/
│  ├─ QuestionList.vue
│  └─ QuestionDetail.vue
├─ components/
│  ├─ QuestionForm.vue
│  └─ QuestionFilters.vue
├─ api.ts
├─ types.ts
└─ composables/
   └─ useQuestionQuery.ts
```

### 7.3 页面清单（P0）

| 模块 | 页面 | 核心动作 |
|---|---|---|
| 工作台 | 待办、任务、质量告警 | 跳转处理 |
| 内容接入 | 上传、任务列表、任务详情 | 上传、重试、终止 |
| 教材 | 书目列表、章节树、版本、授权 | 新建、编辑、绑定授权、下架 |
| 题库 | 列表、详情、批量导入、去重结果 | 编辑、审核、生成变式 |
| 知识点 | 知识点树、关系图、覆盖统计 | 新建、合并、绑定 |
| 校对 | 低置信度队列、题块编辑 | 修正、合并、拆分、通过 |
| 审核 | 待审、抽检、发布单 | 通过、驳回、批量审核 |
| 发布 | 版本列表、发布详情、回滚 | 发布、灰度、回滚、下架 |
| 评测 | 数据集、运行记录、指标报告 | 触发回归、查看差异 |
| 系统 | 租户、用户、角色、字典、审计 | CRUD、授权 |

### 7.4 前端约定

- 页面只做展示与交互；业务规则（能否发布、状态跃迁）由后端返回，前端不复制。
- 列表查询条件同步到 URL，支持刷新/分享/返回保留。
- 表格统一服务端分页、排序、筛选；禁止前端全量拉取再过滤。
- 所有接口类型来自 `api/generated`；业务模块只做薄封装。
- 权限：路由 `meta.permission` + `v-permission` 指令；按钮级控制，后端仍二次校验。
- 上传：前端直传 MinIO 预签名 URL，API 只创建任务记录；大文件分片。
- 异步任务：提交后轮询任务详情（3s 起，指数退避）；不做 WebSocket。
- 表单：长表单用 schema 驱动；校验规则与后端共用字段定义。
- 错误：拦截器统一弹错，保留 `request_id` 便于排查。
- 主题：CSS tokens 统一颜色/字号/间距；不散落魔法值。
- 组件：通用表格、抽屉、状态标签、上传、指标卡沉到 `components/`；模块私有组件留在模块内。

---

## 8. 关键设计决策（ADR 摘要）

| # | 决策 | 备选 | 结论与理由 |
|---|---|---|---|
| 1 | 管理平台与知识库分离部署 | 合并成一个服务 | 分离。在线链路与管理链路故障隔离；发布操作不能影响答题 |
| 2 | 共享存储作为唯一契约 | RPC/消息总线 | 共享存储 + 版本/别名。POC 不引入 Kafka；秒级需求再上 Redis Stream |
| 3 | 模块化单体 + 独立 Worker | 微服务 | 单体。团队小，发布是唯一强耦合点；Worker 先拆出来解决长任务 |
| 4 | PG + Mongo 双存储 | 单一数据库 | 双存储。PG 管流程事务，Mongo 管资产并共享给知识库 |
| 5 | 发布两阶段 + 别名 | 直接覆盖写 | 两阶段。Mongo/Milvus 无跨库事务，必须可重入、可回滚 |
| 6 | 管理 API 前缀 `/admin/v1` | 复用 `/v1` | 分开。避免与知识库契约混淆，权限模型也不同 |
| 7 | Vue 3 + Element Plus | React + AntD | Vue。管理端表格/表单密集；团队若已用 React 可等价替换 |
| 8 | 不做复杂 BPM | 引入工作流引擎 | 固定状态机。P0 审核只有通过/驳回/转交，引擎是过度设计 |

---

## 9. P0 交付顺序

| 阶段 | 交付 | 退出标准 |
|---|---|---|
| M0（1–2 周） | 工程骨架、PG/Mongo/MinIO 接入、登录/RBAC、教材与题库 schema | 能登录，能建教材/题目，审计可查 |
| M1（3–4 周） | 上传 → 解析 → 校对 → 题库入库；任务中心 | 10 套试卷可导入，失败可重试 |
| M2（5–6 周） | 审核 → 发布 → 索引别名切换 → 回滚 | 一次完整发布/回滚演练通过 |
| M3（7–8 周） | 评测中心最小版、覆盖统计、反馈闭环 | 100 题金标集可跑回归并出报告 |
| M4（9–10 周） | 权限细化、运营看板、压测 | 20 并发导入/发布稳定，无 P0 缺陷 |

---

## 10. 主要风险

| 风险 | 影响 | 应对 |
|---|---|---|
| 解析/OCR 质量不稳定 | 人工校对量大，发布慢 | 低置信度队列 + 分片重试 + 双通道 OCR |
| Mongo 与 Milvus 发布不一致 | 知识库读到半成品 | 两阶段发布 + reconciler + 别名回滚 |
| 双存储边界失控 | 平台表被知识库依赖，无法演进 | 知识库只读 Mongo/Milvus/MinIO；禁止读 PG |
| 审核成为瓶颈 | 内容积压 | 批量审核、抽检规则、按租户分配 |
| 权限越权 | 跨租户数据泄漏 | 数据范围强制过滤 + 后端二次校验 + 审计 |
| 发布误操作 | 线上答案错误 | 发布单双人确认、灰度、一键回滚、发布窗口 |
