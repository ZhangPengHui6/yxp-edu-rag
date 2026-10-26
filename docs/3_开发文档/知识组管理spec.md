# YXP 教育 RAG 系统 — 技术规格说明书（Spec）

> 版本：v1.0　状态：待评审　范围：P0
> 关联文档：`docs/1_PRD/yxp_prd.md`（v0.4）、`docs/2_概要设计总纲/yxp_概要设计总纲.md`、`docs/3_开发文档/数据对象.md`
> 文件名沿用既定路径 `知识组管理spec.md`，内容为**全项目**规格。如需更名，见第 13 节。

---

## 1. 目标与边界

### 1.1 目标

给 K12 学生提供一套**有教材依据、可溯源、能分步讲解**的答疑与解题服务，而不是一个通用聊天框。

三条产品结论决定技术形态：

1. **题库优先**：能命中原题就返回标准答案，命中不了才生成。生成必须带引用与置信度。
2. **OCR 是主入口**：学生拍题远多于打字，图片解析必须是 P0 能力。
3. **答案必须可追溯**：每条结论要能指回教材页码或题目 ID，否则不返回。

### 1.2 P0 范围

| 能力 | P0 内容 |
|---|---|
| 学科 | 初中数学（正式）；高中数学/物理（当前语料，独立学段库） |
| 题库 | 10 套试卷规模，跑通导入 → 校对 → 审核 → 发布 |
| 教材 | 知识手册 PDF 入库，OCR 结构化 |
| 解题 | L1 精确 / L2 相似 / L3 RAG 生成三级路由 |
| OCR | 印刷体 + 手写 + 公式 + 图形/表格 |
| 管理端 | 工作台、接入、教材、题库、知识点、校对、审核、发布、评测、系统 |
| 部署 | 模块化单体 + 独立 Worker，单机可跑 |

### 1.3 非目标（P0 不做）

- 支付、营销、家长端、直播课
- 第三方视频讲解
- 复杂 BPM 工作流引擎
- 多模态生成（图生题、语音）
- 生产级合规体系（先内部 POC）
- 微服务拆分、Kafka

### 1.4 成功标准

| 指标 | 目标 |
|---|---|
| 印刷体 OCR 准确率 | ≥ 95% |
| 公式识别准确率 | ≥ 90% |
| L1 命中时答案正确率 | ≥ 98% |
| L1/L2 答案准确率 | ≥ 95% |
| L3 答案准确率（人工抽检） | ≥ 75% |
| 首字延迟 P95 | ≤ 2s |
| 解题 P95（L1/L2） | ≤ 2s |
| 解题 P95（L3） | ≤ 15s |

> POC 只有 10 套试卷，命中率只记录、不设门槛；核心看「命中时是否正确」「未命中时 L3 是否可用」。

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| 资产（asset） | 教材、章节、切片、题目、知识点的统称 |
| 切片（chunk） | 教材切分后的检索单元，对应一条 Milvus 向量 |
| L1 / L2 / L3 | 原题命中 / 相似题复用 / RAG 生成 |
| 公式签名 | 归一化后题干与公式的哈希，用于 L1 精确匹配 |
| SimHash | 64 位近似指纹，汉明距离 ≤3 视为近似重复 |
| 别名（alias） | Milvus 的 `chunks_active` / `questions_active`，指向当前线上集合 |
| 发布单（publish order） | 一次版本切换的记录，含三版本与状态 |
| 学段（stage） | `primary` / `middle` / `high` |

---

## 3. 系统上下文

```
                    ┌──────────────────────────┐
   运营 / 教研 ────▶│  管理平台 admin-web       │
                    │  Vue3 + Element Plus      │
                    └────────────┬─────────────┘
                                 │ /admin/v1
                    ┌────────────▼─────────────┐
                    │  admin-api (FastAPI)      │
                    │  权限 / 任务 / 发布编排    │
                    └──┬──────┬──────┬─────────┘
                       │      │      │
        ┌──────────────▼┐ ┌───▼───┐ ┌▼──────────────┐
        │ PostgreSQL    │ │ Mongo │ │ MinIO         │
        │ 流程与事务     │ │ 资产  │ │ 文件与图片     │
        └───────────────┘ └───┬───┘ └───────────────┘
                              │ 共享存储（唯一契约）
                    ┌─────────▼─────────────────┐
   学生 App ───────▶│  kb-api (FastAPI + LangGraph) │
                    │  检索 / OCR / 解题 / 生成     │
                    └──┬──────────┬──────────────┘
                       │          │
                 ┌─────▼────┐ ┌───▼─────────────┐
                 │ Milvus   │ │ 模型服务         │
                 │ 向量检索  │ │ Qwen-VL / LLM    │
                 └──────────┘ └─────────────────┘
```

**边界原则**：管理平台与知识库服务不互相 RPC，只通过共享存储 + 版本别名协作。管理链路故障不影响线上答题。

### 3.1 外部依赖

| 依赖 | 用途 | 失败影响 | 降级 |
|---|---|---|---|
| Qwen2.5-VL-7B（DashScope） | OCR / 图片理解 | 拍题不可用 | 切 72B；提示「稍后重试」 |
| LLM（解题/生成） | L3 解题、答疑 | 只能返回 L1/L2 | 降级小模型；超时返回检索原文 |
| MinerU | 教材解析 | 入库阻塞 | 重试；切 Qwen-VL 逐页 |
| BGE-M3 | 向量化 | 入库阻塞 | 重试；批量降并发 |
| Rerank 模型 | 重排 | 召回质量下降 | 跳过重排，用 RRF 分数 |

---

## 4. 总体设计

### 4.1 分层

| 层 | 职责 | 禁止 |
|---|---|---|
| 接入层 | 鉴权、参数校验、限流、路由、统一错误 | 业务规则、直接查库 |
| 应用层 | 用例编排、事务边界、权限判断、发事件 | 拼 SQL、调 SDK、写解析逻辑 |
| 领域层 | 状态机、规则、值对象、领域服务 | 依赖 HTTP/DB/云 SDK |
| 基础设施层 | 数据访问、外部 API、任务队列 | 业务判断 |

依赖方向：`api → modules/service → domain + repository/provider`。

### 4.2 部署单元

| 单元 | 说明 | 扩缩 |
|---|---|---|
| `admin-api` | 管理端 REST，`/admin/v1` | 无状态，水平扩展 |
| `admin-worker` | Celery：解析、OCR、索引、评测 | 按队列扩 |
| `kb-api` | 学生端 REST + SSE，`/v1` | 无状态，水平扩展 |
| `kb-worker` | 异步解题、批量 OCR | 按队列扩 |
| `admin-web` | Vue 3 静态资源 | CDN |

POC 部署拓扑：单机 Docker Compose，PG / Mongo / Milvus Standalone / MinIO / Redis 各一实例。

### 4.3 技术栈与实现约束

| 项 | 选型 |
|---|---|
| 语言 | Python 3.12 |
| Web | FastAPI + Uvicorn |
| 校验 | Pydantic v2 |
| 关系库 | SQLAlchemy 2.0 async + asyncpg + Alembic |
| 文档库 | Motor |
| 向量库 | pymilvus |
| 对象存储 | minio-py |
| 任务 | Celery 5 + Redis |
| 编排 | LangGraph（知识库侧） |
| 日志 | structlog |
| 追踪 | OpenTelemetry |
| 测试 | pytest + pytest-asyncio + testcontainers |
| 质量 | ruff + mypy + pre-commit |

**约束**：

- 领域层不 import FastAPI / SQLAlchemy / pymilvus。
- 所有外部调用经 `infra/providers/`，provider 内不写业务规则。
- 所有写接口必须支持 `Idempotency-Key`。
- 时间统一 UTC，API 输出 ISO 8601。

> 若团队改用 Java/Spring Boot，模块边界、接口契约、状态机、表结构均不变，仅把 `modules/*` 映射为 Spring 的 `@Service` 包，`infra/providers` 映射为 `@Component`。**不要另造一套领域模型。**

### 4.4 关键决策

| # | 决策 | 理由 |
|---|---|---|
| 1 | 管理平台与知识库分离部署 | 发布操作不能影响答题链路 |
| 2 | 共享存储作为唯一契约 | POC 不引入消息总线；版本/别名足够 |
| 3 | 模块化单体 + 独立 Worker | 团队小，唯一强耦合点是发布 |
| 4 | PG + Mongo 双存储 | PG 管流程事务，Mongo 管资产并共享 |
| 5 | 发布两阶段 + 别名 | 跨库无事务，必须可重入、可回滚 |
| 6 | 管理 API 前缀 `/admin/v1` | 与知识库 `/v1` 契约隔离 |
| 7 | L1 只认归一化完全一致 | 相似度 0.90 也可能答案不同，宁可不命中 |

> **口径冲突说明**：PRD §11.3 中的 `/v1/admin/ingest`、`/v1/admin/textbooks/{id}` 按 ADR-6 统一迁到 `/admin/v1/ingestion/jobs`、`/admin/v1/textbooks/{id}`。PRD 不再作为接口命名的最终依据。

---

## 5. 领域模型与状态

### 5.1 核心实体

| 实体 | 存储 | 说明 |
|---|---|---|
| Tenant | Mongo | 租户与配额 |
| User / Role / Permission | PG | 管理端账号体系 |
| Textbook / Chapter | Mongo | 教材资产 |
| Question / QuestionVariant | Mongo | 题库 |
| KnowledgePoint / KpRelation | Mongo | 知识图谱 |
| License | Mongo | 授权 |
| IngestionJob / JobStep | PG | 入库任务 |
| AnnotationTask | PG | 校对队列 |
| ReviewTask | PG | 审核任务 |
| PublishOrder / PublishItem | PG | 发布 |
| EvalDataset / EvalCase / EvalRun | PG | 评测 |
| AuditLog / OutboxEvent | PG | 审计与事件 |
| Chunk | Milvus `chunks_live` | 片段正文 + 过滤字段 + dense/sparse 向量 |
| QuestionVector | Milvus | 题目向量（发布版本集合） |

字段级定义见 `docs/3_开发文档/数据对象.md`，本文不重复。

### 5.2 状态机

**资产**

```
draft → parsing → parsed → annotated → reviewing → approved → published → deprecated
              ↘ failed（可重试）
```

**入库任务**：`pending → running → succeeded | failed | canceled`

**审核任务**：`pending → in_review → approved | rejected | transferred`

**发布单**：`draft → staged → published`；失败 `failed`；`published → rolled_back`

**OCR 任务**：`queued → running → succeeded | failed`

### 5.3 不变量

1. `tenant_id` 在所有查询中都存在，缺失即 500（视为代码缺陷）。
2. 只有 `published` 且 `license_status != restricted` 的题目/切片进入检索。
3. 同一 `(asset_type, asset_id, asset_version)` 同时最多一个未完结 `review_task`。
4. 同一租户同时最多一个 `staged` 状态发布单。
5. `publish_order` 的 `index_version` 必须与 Milvus 集合后缀一致。
6. 已发布资产的 `version` 只增不减。
7. 任何状态变更必须写 `audit_log`，且带 `trace_id`。

---

## 6. 模块规格

### 6.1 工作台

**职责**：把「今天该处理什么」收敛到一屏。

**用例**

| 用例 | 说明 |
|---|---|
| 查看待办 | 待校对、待审核、待发布、失败任务 |
| 查看任务进度 | 进行中的入库任务与百分比 |
| 查看质量告警 | 低置信度积压、覆盖率缺口、发布失败 |
| 跳转处理 | 一键进入对应模块并带筛选条件 |

**接口**：`GET /admin/v1/dashboard/summary`、`GET /admin/v1/dashboard/todos`

**规则**

- 待办数量为实时聚合，不做缓存；单次查询 ≤ 300ms。
- 质量告警阈值可配置：低置信度 `< 0.85`、覆盖率缺口 `< 5 题/知识点`。

**验收**：各卡片数量与对应模块列表条数一致；点击跳转后筛选条件保留。

---

### 6.2 内容接入

**职责**：把文件变成可校对的结构化资产。

**用例**

| 用例 | 说明 |
|---|---|
| 上传文件 | 前端直传 MinIO 预签名 URL，API 只建任务 |
| 查看任务列表 | 按状态/时间/类型筛选 |
| 查看任务详情 | 每步骤耗时、入参出参摘要、错误 |
| 重试 | 按步骤重试，不从头再来 |
| 终止 | 仅 `pending` / `running` 可终止 |

**接口**

| Method | Path | 说明 |
|---|---|---|
| POST | `/admin/v1/ingestion/uploads` | 申请预签名上传地址 |
| POST | `/admin/v1/ingestion/jobs` | 创建任务 |
| GET | `/admin/v1/ingestion/jobs` | 任务列表 |
| GET | `/admin/v1/ingestion/jobs/{job_id}` | 任务详情（含步骤） |
| POST | `/admin/v1/ingestion/jobs/{job_id}/retry` | 重试 |
| POST | `/admin/v1/ingestion/jobs/{job_id}/cancel` | 终止 |

**规则**

- 支持 `pdf` / `docx` / `epub`；单文件 ≤ 500MB；单租户并发任务 ≤ 3。
- 幂等键 = `sha256(tenant_id + file_sha256 + type)`；重复提交返回原任务，HTTP 200。
- 步骤：`upload → parse → ocr → segment → normalize → dedup → classify → index`。
- 单步骤失败重试 ≤ 3 次，指数退避（5s / 30s / 120s）。
- 任务失败不删除已产出的中间产物，便于排查。

**异常**

| 场景 | 处理 |
|---|---|
| 文件类型不支持 | 400 `UNSUPPORTED_FILE_TYPE` |
| 文件超限 | 413 `PAYLOAD_TOO_LARGE` |
| 并发超限 | 429 `TENANT_CONCURRENCY_EXCEEDED` |
| 解析失败 | 步骤置 `failed`，任务 `failed`，保留 `error_message` |
| 任务已结束再终止 | 409 `JOB_NOT_CANCELABLE` |

**验收**：10 套试卷全部导入成功；任一步骤失败可单独重试并继续；任务详情能看到每步耗时。

---

### 6.3 教材管理

**职责**：维护教材元数据、章节树、授权绑定与上下架。

**用例**：书目 CRUD、章节树编辑、版本查看、绑定授权、下架。

**接口**：`/admin/v1/textbooks`、`/admin/v1/textbooks/{id}/chapters`、`/admin/v1/textbooks/{id}/license`

**规则**

- 教材必须绑定有效授权才能进入审核；`scope=internal_poc` 只能用于内部环境。
- 章节树最多 3 层；同级 `order` 唯一。
- 下架走状态机 `published → deprecated`，同时删除对应向量（保留 Mongo 记录与审计）。
- `page_start/page_end` 为 PDF 物理页；`printed_page_start` 用于前端展示「第 18 页」。

**验收**：章节树拖拽排序后刷新保持；授权过期教材在发布时被拦截。

---

### 6.4 题库管理

**职责**：题目录入、去重、质量管控、变式生成。

**用例**

| 用例 | 说明 |
|---|---|
| 列表与筛选 | 学科、学段、章节、知识点、题型、难度、状态 |
| 题目详情 | 题干、选项、答案、解析、引用来源 |
| 批量导入 | 走内容接入任务 |
| 去重结果 | 展示精确/近似/语义三档重复组 |
| 生成变式 | 基于原题生成并校验，落 `question_variants` |
| 状态操作 | 提交审核、下架 |

**接口**

| Method | Path |
|---|---|
| GET | `/admin/v1/questions` |
| GET | `/admin/v1/questions/{question_id}` |
| PATCH | `/admin/v1/questions/{question_id}` |
| POST | `/admin/v1/questions/{question_id}/variants` |
| GET | `/admin/v1/questions/duplicates` |

**规则**

- **L1 键**：`formula_signature = sha256(stem_normalized + options + answer_type)`。
- **近似去重**：SimHash 汉明距离 ≤ 3 归入同一 `dup_group_id`。
- **语义去重**：题干向量余弦 ≥ 0.95 进人工确认队列，不自动删除。
- 同题多解保留为 `variant_of`，不物理删除。
- 质量门槛：答案非空、解析含「思路 + 步骤 + 结论」、公式可渲染、选择题选项 ≥ 2 且答案在选项内。
- 只有 `review_status=published` 才写入 `questions_v1`。

**验收**：导入 100 题，精确重复全部识别；L1 命中抽检 100 题答案全对。

---

### 6.5 知识点管理

**职责**：维护知识点树、别名、关系图谱与覆盖统计。

**用例**：新建/编辑、移动层级、合并、绑定教材与题目、查看关系图、查看覆盖。

**接口**

| Method | Path | 说明 |
|---|---|---|
| GET | `/admin/v1/knowledge-points/tree` | 树 |
| POST | `/admin/v1/knowledge-points` | 新建 |
| PATCH | `/admin/v1/knowledge-points/{kp_id}` | 编辑 |
| POST | `/admin/v1/knowledge-points/{kp_id}/merge` | 合并 |
| GET | `/admin/v1/knowledge-points/{kp_id}/relations` | 关系 |
| GET | `/admin/v1/knowledge-points/coverage` | 覆盖统计 |

**规则**

- 同一 `(tenant_id, subject, stage, grade, parent_id)` 下 `name` 唯一；别名冲突给出提示但不阻断。
- 删除前检查引用：被题目/切片引用时只能停用（`status=disabled`），不可删除。
- 合并：目标 KP 吸收源 KP 的 `aliases`，重写题目与切片的 `kp_ids`，写审计；操作不可逆，需二次确认。
- `coverage_stats` 由定时任务每小时刷新，不实时计算。
- 跨学段严格隔离，不做跨 `stage` 的父子关系。

**验收**：合并后无悬挂引用；覆盖统计与题目实际数量一致；树接口在 1000 节点下 P95 ≤ 200ms。

---

### 6.6 校对标注

**职责**：处理 OCR 低置信度内容，保证进入审核的资产可用。

**用例**

| 用例 | 说明 |
|---|---|
| 低置信度队列 | 按置信度升序，支持学科/来源筛选 |
| 题块编辑 | 修正题干、公式、选项、答案 |
| 合并 / 拆分 | 处理「一题被切成两段」「两题被合并」 |
| 通过 / 跳过 | 通过后资产进入 `annotated` |

**接口**：`/admin/v1/annotation/tasks`、`/admin/v1/annotation/tasks/{task_id}/resolve`

**规则**

- 进入队列条件：`confidence < 0.85`、公式无法渲染、表格结构损坏、图片缺失。
- 修正后重算 `hash` / `formula_signature` / `simhash`。
- 每个修正动作写 `audit_log`，保留 `before/after`。
- 跳过必须填写原因。

**验收**：修正后重新检索能命中新内容；合并/拆分不产生孤儿题目。

---

### 6.7 审核

**职责**：对资产做人工把关，决定是否可发布。

**用例**：待审列表、抽检、通过、驳回、转交、发起发布单。

**接口**：`/admin/v1/review/tasks`、`/admin/v1/review/tasks/{task_id}/decision`

**规则**

- 只有 `annotated` 资产可提交审核。
- 驳回必须写 `comment`，资产回到 `annotated`。
- 转交生成新任务，原任务 `transferred` 关闭。
- 抽检比例可配（默认 10%），抽检不通过则整批退回。
- 审核通过只改 `approved`，**不直接发布**。

**验收**：同一资产同版本不能重复建审核任务；驳回后修改再提交可正常流转。

---

### 6.8 发布

**职责**：把通过审核的资产切到线上，且可回滚。

**用例**：创建发布单、预检、执行、查看进度、回滚、下架。

**接口**

| Method | Path | 说明 |
|---|---|---|
| POST | `/admin/v1/publish/orders` | 创建发布单 |
| POST | `/admin/v1/publish/orders/{id}/stage` | 构建新索引 |
| POST | `/admin/v1/publish/orders/{id}/commit` | 切别名 |
| POST | `/admin/v1/publish/orders/{id}/rollback` | 回滚 |
| GET | `/admin/v1/publish/orders/{id}` | 详情 |

**规则**

- 预检项：无未完结审核、授权有效、索引构建成功、目标集合非空。
- 两阶段：
  1. Stage：从 `chunks_live` 重建 `chunks_<index_version>` / `questions_<index_version>`，`status=staged`，不影响线上。
  2. Commit：Mongo 批量改 `published` → 切别名 → `status=published`。
- 同一租户同时只允许一个 `staged` 发布单。
- 别名切换失败：回滚别名，Mongo 状态回 `approved`，发布单 `failed`。
- 旧集合保留 2 个发布周期。
- 回滚目标：上一个 `published` 发布单；P95 ≤ 2 分钟。

**验收**：完整发布 → 检索到新内容 → 回滚 → 检索回旧内容，全流程 ≤ 5 分钟；人为中断 Commit 后 reconciler 能重放到一致状态。

---

### 6.9 评测

**职责**：用金标集验证检索与解题质量，作为发布门禁。

**用例**：管理数据集与用例、触发运行、查看指标、对比两次运行差异。

**接口**：`/admin/v1/evaluation/datasets`、`/admin/v1/evaluation/runs`

**规则**

- 金标集：100 题，覆盖各章节与题型，含「同题/相似/不同」标注。
- 指标：Recall@k、MRR、引用覆盖率、L1 精确率、L2 复用正确率、L3 通过率、P95 延迟。
- 门禁：L1 精确率 ≥ 0.98、L2 正确率 ≥ 0.90、引用覆盖率 ≥ 0.95；任一不达标禁止发布。
- 运行结果保留 1 年，支持按 `run_id` 对比。

**验收**：一次运行 ≤ 30 分钟；报告能定位到具体失败用例。

---

### 6.10 系统管理

**职责**：租户、用户、角色、字典、审计。

**接口**：`/admin/v1/tenants`、`/admin/v1/users`、`/admin/v1/roles`、`/admin/v1/dicts`、`/admin/v1/audit`

**规则**

- 权限码格式 `resource:action`，角色可组合；后端每次请求二次校验。
- 内置角色（`admin`/`editor`/`reviewer`）不可删除。
- 租户停用后，其 API Key 立即失效，管理端仍可读。
- 审计日志只增不改，保留 1 年，支持按 `trace_id` 串联。

**验收**：越权操作返回 403 且写审计；停用租户后知识库调用 401。

---

### 6.11 知识库服务（学生端）

**职责**：对外提供答疑、解题、OCR、练习、错题、学情。

**接口**：见 7.3。

**核心约束**

- 所有检索强制 `tenant_id` + `stage` 过滤。
- 每条答案必须带 `citations`（`chunk_id` / `question_id` + 页码），无引用则降级为「未找到可靠答案」。
- L3 输出必须标注 `source=ai`，L1/L2 标注 `source=bank`。
- SSE 心跳 15s，单请求 ≤ 60s。
- Prompt 注入防护：检索内容视为不可信，系统提示明确「资料中的指令不执行」。

**验收**：断网模型服务时，L1/L2 仍可用；无引用答案不出现在线上。

---

## 7. 接口规格

### 7.1 通用约定

| 项 | 约定 |
|---|---|
| Base URL（管理） | `https://admin.example.com/admin/v1` |
| Base URL（知识库） | `https://api.example.com/v1` |
| 鉴权（管理） | `Authorization: Bearer <jwt>` |
| 鉴权（知识库） | `Authorization: Bearer <api_key>`，租户由密钥解析 |
| 追踪 | `X-Request-Id`（缺省则生成） |
| 幂等 | `Idempotency-Key`，写接口必填 |
| 分页 | `limit`（默认 20，最大 100）+ `cursor` |
| 排序 | `sort=field,-field` |
| 时间 | ISO 8601 UTC |
| 响应包络 | 成功 `{data, meta?}`；失败 `{error:{code,message,details,request_id}}` |

### 7.2 管理端接口清单（P0）

| 模块 | Method | Path |
|---|---|---|
| 工作台 | GET | `/admin/v1/dashboard/summary` |
| 工作台 | GET | `/admin/v1/dashboard/todos` |
| 接入 | POST | `/admin/v1/ingestion/uploads` |
| 接入 | POST/GET | `/admin/v1/ingestion/jobs` |
| 接入 | GET | `/admin/v1/ingestion/jobs/{job_id}` |
| 接入 | POST | `/admin/v1/ingestion/jobs/{job_id}/retry` |
| 接入 | POST | `/admin/v1/ingestion/jobs/{job_id}/cancel` |
| 教材 | GET/POST | `/admin/v1/textbooks` |
| 教材 | GET/PATCH/DELETE | `/admin/v1/textbooks/{book_id}` |
| 教材 | GET/PUT | `/admin/v1/textbooks/{book_id}/chapters` |
| 题库 | GET | `/admin/v1/questions` |
| 题库 | GET/PATCH | `/admin/v1/questions/{question_id}` |
| 题库 | POST | `/admin/v1/questions/{question_id}/variants` |
| 知识点 | GET | `/admin/v1/knowledge-points/tree` |
| 知识点 | POST/PATCH | `/admin/v1/knowledge-points` |
| 知识点 | POST | `/admin/v1/knowledge-points/{kp_id}/merge` |
| 知识点 | GET | `/admin/v1/knowledge-points/coverage` |
| 校对 | GET | `/admin/v1/annotation/tasks` |
| 校对 | POST | `/admin/v1/annotation/tasks/{task_id}/resolve` |
| 审核 | GET | `/admin/v1/review/tasks` |
| 审核 | POST | `/admin/v1/review/tasks/{task_id}/decision` |
| 发布 | POST | `/admin/v1/publish/orders` |
| 发布 | POST | `/admin/v1/publish/orders/{id}/stage` |
| 发布 | POST | `/admin/v1/publish/orders/{id}/commit` |
| 发布 | POST | `/admin/v1/publish/orders/{id}/rollback` |
| 发布 | GET | `/admin/v1/publish/orders/{id}` |
| 评测 | GET/POST | `/admin/v1/evaluation/datasets` |
| 评测 | POST/GET | `/admin/v1/evaluation/runs` |
| 系统 | GET/POST | `/admin/v1/users`、`/roles`、`/tenants` |
| 系统 | GET | `/admin/v1/audit` |

### 7.3 知识库接口清单（P0）

| Method | Path | 用途 |
|---|---|---|
| POST | `/v1/chat/stream` | 流式答疑 |
| POST | `/v1/chat` | 非流式答疑 |
| POST | `/v1/ocr` | 图片识别，返回结构化题目 |
| POST | `/v1/solve` | 单题解析（文本 / LaTeX / 图片） |
| POST | `/v1/solve/batch` | 多题解析 |
| POST | `/v1/questions/search` | 题库检索（L1/L2） |
| GET | `/v1/questions/{question_id}` | 题目详情 |
| POST | `/v1/questions/{question_id}/variants` | 变式题 |
| GET | `/v1/questions/coverage` | 题库覆盖 |
| POST | `/v1/practice/generate` | 生成练习 |
| POST | `/v1/practice/submit` | 提交判分（幂等） |
| GET | `/v1/practice/{practice_id}` | 查询练习 |
| GET | `/v1/wrong-questions` | 错题列表 |
| POST | `/v1/wrong-questions/{wrong_id}/review` | 错题复习 |
| GET | `/v1/mastery` | 掌握度 |
| GET | `/v1/textbooks` | 可用教材 |
| GET | `/v1/knowledge-points` | 知识点查询 |
| POST | `/v1/feedback` | 反馈 |
| GET | `/v1/health` | 健康检查 |

### 7.4 关键契约

#### 7.4.1 创建入库任务

`POST /admin/v1/ingestion/jobs`

```json
{
  "type": "textbook_pdf",
  "source_uri": "minio://raw/tnt_x/textbook/book_x/source.pdf",
  "source_name": "高中数学知识手册.pdf",
  "subject": "math",
  "stage": "high",
  "grade": "10"
}
```

响应 `201`：

```json
{
  "data": {
    "job_id": "job_01J9Z...",
    "status": "pending",
    "step": "upload",
    "created_at": "2026-10-24T08:00:00Z"
  }
}
```

重复提交（同 `Idempotency-Key`）返回 `200` + 原任务。

#### 7.4.2 任务详情

`GET /admin/v1/ingestion/jobs/{job_id}`

```json
{
  "data": {
    "job_id": "job_01J9Z...",
    "status": "running",
    "step": "ocr",
    "progress": 62,
    "stats": {"pages": 88, "questions": 0, "low_confidence": 14},
    "steps": [
      {"step": "upload", "status": "succeeded", "duration_ms": 1200},
      {"step": "parse",  "status": "succeeded", "duration_ms": 8300},
      {"step": "ocr",    "status": "running",   "attempt": 1}
    ],
    "error": null
  }
}
```

#### 7.4.3 发布单

`POST /admin/v1/publish/orders`

```json
{
  "scope": "full",
  "versions": {"book_version": "book_20261024_01", "bank_version": "bank_20261024_01"},
  "alias": "chunks_active"
}
```

`POST /admin/v1/publish/orders/{id}/commit` 响应：

```json
{
  "data": {
    "publish_id": "pub_01J9Z...",
    "status": "published",
    "versions": {
      "book_version": "book_20261024_01",
      "bank_version": "bank_20261024_01",
      "index_version": "idx_20261024_01"
    },
    "published_at": "2026-10-24T09:10:00Z"
  }
}
```

#### 7.4.4 题库检索

`POST /v1/questions/search`

```json
{
  "query": "解方程 x^2-7x+12=0",
  "stem_latex": "x^2-7x+12=0",
  "subject": "math",
  "stage": "middle",
  "grade": "9",
  "top_k": 5
}
```

响应：

```json
{
  "data": {
    "hit_level": "L2",
    "items": [
      {
        "question_id": "q_01J9Z...",
        "score": 0.87,
        "type": "calculation",
        "difficulty": "easy",
        "kp_ids": ["kp_..."],
        "stem": "解方程 x^2-5x+6=0",
        "answer": "x=2 或 x=3",
        "source": "bank"
      }
    ],
    "elapsed_ms": 180
  }
}
```

规则：`hit_level` 由服务端判定，客户端不得自行推断；L1 未命中但 L2 命中时 `hit_level=L2` 且必须重算答案。

#### 7.4.5 单题解析

`POST /v1/solve`

```json
{
  "input_type": "text",
  "content": "已知 x^2-5x+6=0，求两根之和",
  "subject": "math",
  "stage": "middle",
  "grade": "9",
  "prefer": "auto"
}
```

响应关键字段：

```json
{
  "data": {
    "solve_id": "sol_...",
    "source": "bank",
    "hit_level": "L1",
    "answer": "5",
    "steps": [{"step_no": 1, "title": "识别", "content": "..."}],
    "citations": [{"type": "question", "id": "q_...", "page": null}],
    "confidence": 0.98,
    "warnings": []
  }
}
```

#### 7.4.6 OCR

`POST /v1/ocr`

```json
{"image_url": "https://.../p1.jpg", "subject_hint": "math"}
```

响应字段严格遵循 PRD §19.4：`ocr_id`、`image`、`questions[]`、`raw_text`、`warnings`，`confidence` 至少覆盖题干/公式/选项。

### 7.5 SSE 事件

| event | 说明 |
|---|---|
| `meta` | `request_id`、`scene`、`hit_level` |
| `delta` | 增量文本 |
| `citation` | 引用块，可多次 |
| `warning` | 低置信度、超纲、安全提示 |
| `done` | 结束，含 `usage` |
| `error` | 错误码与可重试标记 |

心跳：每 15s 一条注释行 `: ping`。

### 7.6 错误码

| HTTP | code | 场景 |
|---|---|---|
| 400 | `INVALID_ARGUMENT` | 参数错误 |
| 400 | `UNSUPPORTED_FILE_TYPE` | 文件类型不支持 |
| 401 | `UNAUTHORIZED` | 密钥无效/过期 |
| 403 | `FORBIDDEN` | 权限不足 |
| 403 | `OUT_OF_LICENSE_SCOPE` | 教材不在授权范围 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 409 | `CONFLICT` | 幂等冲突 |
| 409 | `JOB_NOT_CANCELABLE` | 任务已结束 |
| 409 | `PUBLISH_IN_PROGRESS` | 已有发布单进行中 |
| 409 | `ASSET_STATE_INVALID` | 状态机不允许该跃迁 |
| 413 | `PAYLOAD_TOO_LARGE` | 文件超限 |
| 422 | `NEEDS_CLARIFICATION` | 范围不明确 |
| 422 | `CONTENT_UNSAFE` | 内容安全拦截 |
| 429 | `RATE_LIMITED` | 超配额 |
| 429 | `TENANT_CONCURRENCY_EXCEEDED` | 租户并发超限 |
| 500 | `INTERNAL` | 服务内部错误 |
| 503 | `MODEL_UNAVAILABLE` | 模型不可用 |
| 504 | `UPSTREAM_TIMEOUT` | 上游超时 |

---

## 8. 关键流程

### 8.1 导入 → 发布

```
运营上传
  → 预签名直传 MinIO
  → 创建 ingestion_job(pending)
  → Worker: parse → ocr → segment → normalize → dedup → classify
  → 写 draft 资产（Mongo）
  → 低置信度进 annotation_task
  → 教研校对 → annotated
  → 提交审核 → review_task
  → 通过 → approved
  → 创建 publish_order → stage（建新 Milvus 集合）
  → commit（Mongo published + 切别名）
  → audit_log + outbox
  → 知识库 ≤ 60s 感知新版本
```

**失败处理**：任一步失败，任务停在当前步骤，可单步重试；已写入的 draft 资产保留，重试时按 `hash` 幂等 upsert。

### 8.2 查询 → 生成

```
请求 → 鉴权/限流 → 意图识别（答疑/解题/练习）
  → 范围解析（学科/学段/年级/教材；缺失则 NEEDS_CLARIFICATION）
  → 混合检索（dense + sparse）→ RRF → rerank
  → 生成约束（只依据检索内容，标注引用）
  → 引用校验（无引用则降级）
  → SSE 输出
```

### 8.3 OCR → 解题路由

```
图片 → OCR/VLM → 结构化题目 → 归一化
  → L1：formula_signature 精确命中 → 返回标准答案（< 100ms）
  → L2：SimHash/向量 ≥ 0.80 且同 KP/题型 → 复用方法 + 重算校验
  → L3：教材/知识点 RAG + LLM → 符号校验 → 标注 AI 解析
  → 校验失败重试 1 次 → 仍失败返回低置信度提示
```

**原则**：宁可返回「未找到可靠答案」，也不编造。

### 8.4 发布回滚

```
POST rollback
  → 校验上一 published 发布单存在
  → 别名切回旧集合
  → Mongo 资产状态回滚到对应版本
  → publish_order.status=rolled_back
  → 写 audit + outbox
```

回滚期间线上读旧集合，无需停服。

### 8.5 失败恢复

| 场景 | 恢复方式 |
|---|---|
| Commit 中途进程崩溃 | reconciler 扫描 `staged` 超时发布单，重放 commit |
| 索引构建失败 | 删除半成品集合，发布单 `failed`，线上不受影响 |
| Worker 宕机 | Celery `acks_late` + 可见性超时，任务重新投递 |
| 模型超时 | 重试 1 次 → 降级模型 → 返回检索原文 |

---

## 9. 非功能需求

### 9.1 性能与容量（POC）

| 指标 | 目标 |
|---|---|
| 并发请求 | 50 |
| 日活 | < 500 |
| 检索 P95 | ≤ 300ms |
| 首字延迟 P95 | ≤ 2s |
| 完整回答 P95 | ≤ 15s |
| OCR P95 | ≤ 3s |
| 错误率 | < 1% |
| 可用性 | 99.5% |
| 单次问答成本 | ≤ ¥0.1 |
| 单题成本 | ≤ ¥0.05 |

### 9.2 安全

- 所有数据带 `tenant_id`，检索/练习/错题强制过滤。
- API Key 存 Argon2 哈希，可轮换、可吊销。
- 传输 TLS 1.2+；内部服务 TLS 或 mTLS。
- 不存姓名、手机号、身份证号、精确地址。
- 检索内容与用户输入均视为不可信，防 Prompt 注入。
- 输入/输出双向内容安全审核。
- 审计：谁、何时、问了什么范围、引用了什么。

### 9.3 可观测

- `request_id` 贯穿 API → 检索 → 模型。
- 指标：QPS、成功率、错误码分布、TTFT、总耗时、token 用量、检索命中率、引用覆盖率、低置信度比例、配额使用。
- 日志：结构化 JSON，保留 30 天；审计 1 年。
- 告警：错误率、P95 延迟、模型不可用、入库失败、配额超限。

### 9.4 数据保留

| 数据 | 保留 |
|---|---|
| 会话 / 消息 | 180 天 |
| 回答审计 | 1 年 |
| 检索日志 | 90 天 |
| 错题 / 掌握度 | 学生存在期间 |
| 入库原始文件 | 授权期内 |
| 审计日志 | 1 年 |

### 9.5 成本控制

- 上下文裁剪：只保留高相关 chunk。
- Embedding 缓存：相同文本不重复向量化。
- 模型分级：简单意图小模型，复杂解题大模型。
- 输出限制：答疑 ≤ 800 字，解题 ≤ 1500 字。

---

## 10. 测试与验收

### 10.1 测试分层

| 层 | 范围 | 工具 |
|---|---|---|
| 单元 | 领域规则、状态机、归一化、去重 | pytest |
| 集成 | PG / Mongo / Milvus / MinIO | testcontainers |
| 契约 | OpenAPI 与实际响应一致 | schemathesis |
| E2E | 导入 → 发布 → 检索 → 回滚 | Playwright + pytest |
| 评测 | 金标集回归 | 自建 runner |

### 10.2 发布门禁

| 门禁 | 阈值 |
|---|---|
| 单元测试 | 全通过，核心模块覆盖率 ≥ 80% |
| 集成测试 | 全通过 |
| L1 精确率 | ≥ 0.98 |
| L2 复用正确率 | ≥ 0.90 |
| 引用覆盖率 | ≥ 0.95 |
| 检索 P95 | ≤ 300ms |

任一不达标禁止发布。

### 10.3 验收清单

- [ ] 10 套试卷可完整导入，失败可重试
- [ ] 低置信度内容可校对、合并、拆分
- [ ] 审核通过后能生成发布单并成功切换别名
- [ ] 回滚后检索结果回到旧版本
- [ ] 拍照识题 → L1/L2/L3 路由正确
- [ ] 无引用答案不出现在线上
- [ ] 断网模型服务时 L1/L2 仍可用
- [ ] 越权操作返回 403 并写审计

---

## 11. 交付计划

| 阶段 | 周期 | 交付 | 退出标准 |
|---|---|---|---|
| M0 | 1–2 周 | 工程骨架、PG/Mongo/MinIO、登录 RBAC、教材与题库 schema | 能登录，能建教材/题目，审计可查 |
| M1 | 3–4 周 | 上传 → 解析 → 校对 → 题库入库；任务中心 | 10 套试卷可导入，失败可重试 |
| M2 | 5–6 周 | 审核 → 发布 → 别名切换 → 回滚 | 一次完整发布/回滚演练通过 |
| M3 | 7–8 周 | 评测中心最小版、覆盖统计、反馈闭环 | 100 题金标集可跑回归并出报告 |
| M4 | 9–10 周 | 权限细化、运营看板、压测 | 20 并发导入/发布稳定，无 P0 缺陷 |

---

## 12. 风险与待决

### 12.1 风险

| 风险 | 影响 | 应对 |
|---|---|---|
| 语料是高中，PRD 是初中 | 学段错配 | 用 `stage` 隔离；正式库另行找初中语料 |
| 知识手册无题目 | L1/L2 无法验证 | 题库单独接入 10 套试卷 |
| 学而思版权 | 不可对外 | 仅内部 POC，演示前替换 |
| OCR 公式准确率不达标 | 解题链路断 | 7B 不达标切 72B，不下调门槛 |
| 发布跨库不一致 | 线上脏读 | 两阶段 + reconciler + 别名回滚 |

### 12.2 待决

| # | 事项 | 建议 |
|---|---|---|
| 1 | 题库与知识手册是否共用 `textbooks` | 共用，用 `doc_type` 区分 |
| 2 | L2 阈值是否分学科校准 | P0 全局 0.80，P1 分学科 |
| 3 | 变式题是否入 Milvus | P0 不入，走 `variant_of` |
| 4 | 是否引入 ES 做中文分词 | P0 用 sparse，P1 评估 |
| 5 | 发布粒度：整库 vs 单教材 | P0 整库，P1 单教材灰度 |

---

## 13. 附录

### 13.1 文档命名

本文内容为全项目规格，文件名仍按既定要求保存为 `知识组管理spec.md`。若确认不再按模块拆分，建议更名为 `项目spec.md`，避免误导。

### 13.2 与 Java 技术栈的映射（仅在团队改用 Java 时参考）

| 本项目（Python） | Java 等价 |
|---|---|
| `app/api/v1/*.py` | `@RestController` |
| `modules/*/service.py` | `@Service` |
| `modules/*/repository.py` | `@Repository`（MyBatis / Spring Data） |
| `domain/*.py` | 纯领域类，无框架注解 |
| `infra/providers/*.py` | `@Component` + 接口 |
| Celery | Spring Batch / RabbitMQ |
| Pydantic | Bean Validation + DTO |

**不变**：模块边界、接口路径、状态机、表结构、错误码。
