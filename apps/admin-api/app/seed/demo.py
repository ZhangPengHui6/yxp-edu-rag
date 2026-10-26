"""Demo data matching the admin UI (idempotent, one-time).

Seeded only when the corresponding store is empty. Demo password: ``123456``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.permissions import PERMISSIONS, ROLES
from app.core.security import hash_password
from app.domain.publish_rules import build_checks
from app.infra import mongo
from app.infra.chunk_store import get_chunk_store
from app.infra.milvus import get_vector_index
from app.infra.pg import SessionLocal
from app.infra.providers import get_embedding_provider
from app.infra.vectors import sparse_to_document
from app.models import (
    AnnotationTask,
    AuditLog,
    EvalCase,
    EvalDataset,
    EvalResult,
    EvalRun,
    IngestionJob,
    IngestionJobStep,
    OutboxEvent,
    Permission,
    PublishOrder,
    ReviewTask,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.modules.ingestion.rules import STEP_DEFS
from app.shared.ids import new_id
from app.shared.text import simhash64
from app.shared.time import days_ago

log = get_logger("seed")

DEMO_PASSWORD = "123456"
MAIN_TENANT = "tnt_yxp"

# --------------------------------------------------------------------------- #
# PG
# --------------------------------------------------------------------------- #

USERS = [
    ("usr_admin", "admin", "平台管理员", MAIN_TENANT, "super_admin", 90),
    ("usr_zhangwei", "zhangwei", "张伟", MAIN_TENANT, "kb_admin", 80),
    ("usr_liyuan", "liyuan", "李媛", MAIN_TENANT, "reviewer", 60),
    ("usr_chenxi", "chenxi", "陈曦", MAIN_TENANT, "annotator", 45),
    ("usr_zhaomin", "zhaomin", "赵敏", "tnt_bj", "viewer", 30),
    ("usr_sunqi", "sunqi", "孙琦", "tnt_gz", "publisher", 20),
]

JOBS: list[dict[str, Any]] = [
    {
        "id": "job_math",
        "name": "高中数学知识手册.pdf",
        "subject": "数学",
        "pages": 88,
        "size": 18.6,
        "status": "succeeded",
        "days": 6,
        "progress": 100,
        "creator": "usr_zhangwei",
    },
    {
        "id": "job_physics",
        "name": "高中物理知识手册.pdf",
        "subject": "物理",
        "pages": 77,
        "size": 16.2,
        "status": "succeeded",
        "days": 5,
        "progress": 100,
        "creator": "usr_zhangwei",
    },
    {
        "id": "job_math_types",
        "name": "高中数学题型清单.pdf",
        "subject": "数学",
        "pages": 132,
        "size": 24.8,
        "status": "running",
        "days": 0,
        "progress": 50,
        "creator": "usr_zhangwei",
    },
    {
        "id": "job_physics_lab",
        "name": "高中物理实验手册.pdf",
        "subject": "物理",
        "pages": 96,
        "size": 19.4,
        "status": "failed",
        "days": 3,
        "progress": 40,
        "creator": "usr_zhangwei",
        "error": "OCR 服务返回 502，已自动重试 2 次，请稍后重试。",
    },
    {
        "id": "job_conic",
        "name": "圆锥曲线专题.pdf",
        "subject": "数学",
        "pages": 54,
        "size": 11.3,
        "status": "succeeded",
        "days": 2,
        "progress": 100,
        "creator": "usr_admin",
    },
    {
        "id": "job_chem",
        "name": "初中化学方程式.docx",
        "subject": "化学",
        "pages": 40,
        "size": 6.4,
        "status": "pending",
        "days": 1,
        "progress": 0,
        "creator": "usr_admin",
    },
]

STEP_DETAILS = {
    "upload": "文件已接收",
    "ocr": "版面与公式识别完成",
    "split": "题目切分完成",
    "struct": "结构化与公式规整完成",
    "quality": "质量检查通过",
}

REVIEWS: list[dict[str, Any]] = [
    {
        "id": "rev_001",
        "asset_type": "question",
        "asset_id": "q_math_003",
        "kind": "题目",
        "title": "已知函数 f(x)=x³-3x²+2，求其单调区间与极值。",
        "submitter": "张伟",
        "priority": "high",
        "payload": {
            "stem": "已知函数 f(x)=x³-3x²+2，求其单调区间与极值。",
            "answer": "增区间 (-∞,0) 与 (2,+∞)，减区间 (0,2)；极大值 f(0)=2，极小值 f(2)=-2。",
            "analysis": "求导 f'(x)=3x²-6x=3x(x-2)，按导数符号划分区间。",
            "knowledgePoints": ["导数的应用"],
            "source": "高中数学知识手册 · 第三章",
            "confidence": 92,
        },
        "days": 1,
    },
    {
        "id": "rev_002",
        "asset_type": "question",
        "asset_id": "q_math_004",
        "kind": "题目",
        "title": "已知椭圆 x²/4 + y² = 1，求过点 (1,0) 的弦中点轨迹。",
        "submitter": "陈曦",
        "priority": "high",
        "payload": {
            "stem": "已知椭圆 x²/4 + y² = 1，求过点 (1,0) 的弦中点轨迹。",
            "answer": "轨迹为椭圆 x²/4 + y² = 1 内部的一段曲线，需排除端点。",
            "analysis": "设中点坐标，利用点差法与中点弦斜率关系。",
            "knowledgePoints": ["圆锥曲线"],
            "source": "圆锥曲线专题 · 第 12 页",
            "confidence": 74,
        },
        "days": 2,
    },
    {
        "id": "rev_003",
        "asset_type": "knowledge_point",
        "asset_id": "kp_m_geo_param",
        "kind": "知识点",
        "title": "知识点「参数方程」定义与考法变更",
        "submitter": "张伟",
        "priority": "normal",
        "payload": {
            "changeNote": "新增极坐标与参数方程互化的考法说明，补充 2 道例题。",
            "knowledgePoints": ["参数方程"],
            "source": "高中数学知识手册 · 选修 4-4",
        },
        "days": 3,
    },
    {
        "id": "rev_004",
        "asset_type": "textbook",
        "asset_id": "tb_math_high_2",
        "kind": "教材",
        "title": "《高中数学题型清单》章节与题号校验",
        "submitter": "赵敏",
        "priority": "normal",
        "payload": {
            "changeNote": "新增 18 道圆锥曲线变式题，需确认章节归属与页码。",
            "source": "高中数学题型清单 · 通用版",
        },
        "days": 4,
    },
    {
        "id": "rev_005",
        "asset_type": "question",
        "asset_id": "q_phys_003",
        "kind": "题目",
        "title": "一带电粒子垂直进入匀强电场，求其偏转位移。",
        "submitter": "张伟",
        "priority": "normal",
        "payload": {
            "stem": "一带电粒子垂直进入匀强电场，求其偏转位移。",
            "answer": "y = qUL²/(2mdv₀²)。",
            "analysis": "类平抛运动：沿初速方向匀速，沿电场方向匀加速。",
            "knowledgePoints": ["电场"],
            "source": "高中物理知识手册 · 第六章",
            "confidence": 78,
        },
        "days": 4,
    },
]

EVAL_RUNS: list[dict[str, Any]] = [
    {
        "id": "eval_001",
        "dataset": "eval_ds_math",
        "name": "高中数学核心题集",
        "model": "qwen2.5-vl-7b-instruct",
        "recall": 0.92,
        "accuracy": 0.88,
        "citation": 0.90,
        "status": "passed",
        "days": 14,
    },
    {
        "id": "eval_002",
        "dataset": "eval_ds_physics",
        "name": "高中物理力电综合",
        "model": "qwen2.5-vl-7b-instruct",
        "recall": 0.89,
        "accuracy": 0.86,
        "citation": 0.87,
        "status": "passed",
        "days": 7,
    },
    {
        "id": "eval_003",
        "dataset": "eval_ds_math",
        "name": "高中数学核心题集",
        "model": "qwen2.5-vl-7b-instruct",
        "recall": 0.74,
        "accuracy": 0.69,
        "citation": 0.70,
        "status": "failed",
        "days": 30,
    },
]

CHECK_PASS = build_checks(
    reference_ok=True,
    reference_detail="全部题目与知识点引用有效",
    vector_ok=True,
    vector_detail="156 条向量已写入暂存集合",
    alias_ok=True,
    alias_detail="别名可正常解析到 chunks_20261010_090000",
    eval_ok=True,
    eval_detail="Recall@10 0.89 · 阈值 0.85",
)

PUBLISH_ORDERS: list[dict[str, Any]] = [
    {
        "id": "pub_001",
        "status": "published",
        "version": "20261010_090000",
        "old": "20261001_090000",
        "target": "production",
        "creator": "usr_zhangwei",
        "creator_name": "张伟",
        "days": 14,
        "vector_count": 156,
        "note": "知识库月度发布：新增函数与电磁学题目 12 道。",
    },
    {
        "id": "pub_002",
        "status": "rolled_back",
        "version": "20261015_140000",
        "old": "20261010_090000",
        "target": "production",
        "creator": "usr_zhangwei",
        "creator_name": "张伟",
        "days": 9,
        "vector_count": 168,
        "note": "已回滚至 20261010_090000；原发布内容保留，可重新发布。",
    },
    {
        "id": "pub_003",
        "status": "draft",
        "version": "20261023_180000",
        "old": "20261010_090000",
        "target": "production",
        "creator": "usr_zhangwei",
        "creator_name": "张伟",
        "days": 1,
        "vector_count": 183,
        "note": (
            "提交后将把 chunks_active 与 questions_active 从 20261010_090000 "
            "原子切换到 20261023_180000；支持一键回滚。"
        ),
    },
]

CHANGES = [
    {"label": "新增题目", "value": "+12", "tone": "success"},
    {"label": "更新知识点", "value": "+6", "tone": "info"},
    {"label": "新增教材", "value": "+1", "tone": "accent"},
    {"label": "下线内容", "value": "−2", "tone": "danger"},
    {"label": "向量条目", "value": "183", "tone": "muted"},
]

AUDITS = [
    ("audit_1", "usr_zhangwei", "张伟", "publish.create", "publish_order", "pub_003", 0),
    ("audit_2", "usr_liyuan", "李媛", "review.decide", "question", "q_math_001", 1),
    ("audit_3", "usr_chenxi", "陈曦", "annotation.resolve", "annotation_task", "ant_001", 1),
    ("audit_4", "usr_zhangwei", "张伟", "question.update", "question", "q_math_003", 2),
    ("audit_5", "usr_admin", "平台管理员", "iam.role.update", "role", "reviewer", 4),
    ("audit_6", "usr_zhangwei", "张伟", "textbook.license.update", "textbook", "tb_physics_high_1", 5),
    ("audit_7", "usr_liyuan", "李媛", "review.decide", "question", "q_phys_001", 6),
    ("audit_8", "usr_zhangwei", "张伟", "ingestion.retry", "ingestion_job", "job_physics_lab", 7),
]


def _metrics(recall: float, accuracy: float, citation: float) -> list[dict[str, Any]]:
    return [
        {"label": "Recall@10", "value": recall, "display": f"{recall * 100:.1f}%"},
        {"label": "答案准确率", "value": accuracy, "display": f"{accuracy * 100:.1f}%"},
        {"label": "引用准确率", "value": citation, "display": f"{citation * 100:.1f}%"},
    ]


async def _seed_pg() -> bool:
    async with SessionLocal() as session:
        existing = int(
            (await session.execute(select(func.count()).select_from(User))).scalar_one()
        )
        if existing:
            return False

        password_hash = hash_password(DEMO_PASSWORD)

        role_rows: dict[str, Role] = {}
        for role in ROLES:
            row = Role(
                role_id=f"role_{role['code']}",
                tenant_id=None,
                code=role["code"],
                name=role["name"],
                description=role["description"],
                builtin=True,
                created_at=days_ago(120),
            )
            session.add(row)
            role_rows[role["code"]] = row

        all_permission = Permission(
            permission_id="perm_all",
            code="*",
            module="全部",
            action="all",
            label="全部权限",
            description="超级管理员的通配权限",
        )
        session.add(all_permission)
        permission_rows: dict[str, Permission] = {"*": all_permission}
        for item in PERMISSIONS:
            perm_row = Permission(
                permission_id=f"perm_{item['code'].replace('.', '_')}",
                code=item["code"],
                module=item["module"],
                action=item["action"],
                label=item["label"],
                description=item["label"],
            )
            session.add(perm_row)
            permission_rows[item["code"]] = perm_row

        for role in ROLES:
            codes = ["*"] if role["permissions"] == ["*"] else role["permissions"]
            for code in codes:
                session.add(
                    RolePermission(
                        role_id=role_rows[role["code"]].role_id,
                        permission_id=permission_rows[code].permission_id,
                    )
                )

        for index, (user_id, username, name, tenant_id, role_code, age) in enumerate(USERS):
            session.add(
                User(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    username=username,
                    password_hash=password_hash,
                    display_name=name,
                    email=f"{username}@yxp.example.com",
                    phone=f"1380000{index:04d}",
                    status="active",
                    last_login_at=days_ago(index),
                    created_at=days_ago(age),
                )
            )
            session.add(
                UserRole(
                    user_id=user_id,
                    role_id=role_rows[role_code].role_id,
                )
            )

        for job_spec in JOBS:
            job_id = job_spec["id"]
            created = days_ago(int(job_spec["days"])) - timedelta(hours=2)
            status = str(job_spec["status"])
            session.add(
                IngestionJob(
                    job_id=job_id,
                    tenant_id=MAIN_TENANT,
                    job_type="textbook",
                    source_type="DOCX" if job_id == "job_chem" else "PDF",
                    source_name=str(job_spec["name"]),
                    source_uri=f"uploads/2026/10/{job_id}.pdf",
                    subject=str(job_spec["subject"]),
                    stage="middle" if job_id == "job_chem" else "high",
                    pages=int(job_spec["pages"]),
                    size_mb=float(job_spec["size"]),
                    progress=int(job_spec["progress"]),
                    status=status,
                    current_step=_current_step(status),
                    stats={"mode": "poc-pipeline"},
                    error_code="OCR_PROVIDER_ERROR" if status == "failed" else "",
                    error_message=str(job_spec.get("error", "")),
                    created_by=str(job_spec["creator"]),
                    created_at=created,
                    updated_at=created,
                    started_at=created + timedelta(minutes=1)
                    if status in {"running", "succeeded", "failed"}
                    else None,
                    finished_at=created + timedelta(minutes=18)
                    if status in {"succeeded", "failed"}
                    else None,
                )
            )
            for position, step in enumerate(STEP_DEFS):
                step_status = _step_status(status, position)
                session.add(
                    IngestionJobStep(
                        step_id=f"stp_{job_id}_{step['key']}",
                        job_id=job_id,
                        tenant_id=MAIN_TENANT,
                        step=step["key"],
                        position=position,
                        label=step["label"],
                        status=step_status,
                        detail=STEP_DETAILS.get(step["key"], "") if step_status == "succeeded" else "",
                        started_at=created + timedelta(minutes=position * 3)
                        if step_status != "pending"
                        else None,
                        finished_at=created + timedelta(minutes=position * 3 + 2)
                        if step_status in {"succeeded", "failed"}
                        else None,
                    )
                )

        session.add_all(
            [
                AnnotationTask(
                    task_id="ant_001",
                    tenant_id=MAIN_TENANT,
                    book_id="tb_math_high_1",
                    book_title="高中数学知识手册",
                    chapter_title="第二章 函数与导数",
                    page=23,
                    page_total=88,
                    cursor=1,
                    total=4,
                    reason="low_confidence",
                    confidence=0.68,
                    status="pending",
                    assignee_id="usr_chenxi",
                    created_at=days_ago(2),
                ),
                AnnotationTask(
                    task_id="ant_002",
                    tenant_id=MAIN_TENANT,
                    book_id="tb_physics_high_1",
                    book_title="高中物理知识手册",
                    chapter_title="第五章 机械能",
                    page=41,
                    page_total=77,
                    cursor=1,
                    total=3,
                    reason="formula_mismatch",
                    confidence=0.72,
                    status="in_review",
                    assignee_id="usr_chenxi",
                    created_at=days_ago(3),
                ),
            ]
        )

        for spec in REVIEWS:
            session.add(
                ReviewTask(
                    task_id=spec["id"],
                    tenant_id=MAIN_TENANT,
                    asset_type=spec["asset_type"],
                    asset_id=spec["asset_id"],
                    asset_version=1,
                    kind=spec["kind"],
                    title=spec["title"],
                    submitter_name=spec["submitter"],
                    priority=spec["priority"],
                    status="in_review" if spec["id"] == "rev_004" else "pending",
                    payload=spec["payload"],
                    created_by="usr_zhangwei",
                    created_at=days_ago(int(spec["days"])),
                )
            )

        session.add_all(
            [
                EvalDataset(
                    dataset_id="eval_ds_math",
                    tenant_id=MAIN_TENANT,
                    name="高中数学核心题集",
                    subject="数学",
                    question_count=4,
                    created_at=days_ago(60),
                ),
                EvalDataset(
                    dataset_id="eval_ds_physics",
                    tenant_id=MAIN_TENANT,
                    name="高中物理力电综合",
                    subject="物理",
                    question_count=4,
                    created_at=days_ago(45),
                ),
            ]
        )

        math_cases = [
            ("eval_case_m1", "判断函数 f(x)=x³-3x 的单调性。", "增区间 (-∞,-1) 与 (1,+∞)，减区间 (-1,1)。", ["kp_m_func_prop"]),
            ("eval_case_m2", "等差数列中 a₁=2，d=3，求 a₁₀。", "a₁₀=29。", ["kp_m_seq_arith"]),
            ("eval_case_m3", "求数列 1/(n(n+1)) 的前 n 项和。", "Sₙ=n/(n+1)。", ["kp_m_seq_sum"]),
            ("eval_case_m4", "正方体中求异面直线所成角。", "利用空间向量夹角公式求解。", ["kp_m_solid_angle"]),
        ]
        physics_cases = [
            ("eval_case_p1", "匀加速直线运动 3 s 内的位移是多少？", "由 x=v₀t+½at² 计算。", ["kp_p_mech_motion"]),
            ("eval_case_p2", "物块在斜面上滑动的加速度如何求解？", "沿斜面方向列牛顿第二定律。", ["kp_p_mech_newton"]),
            ("eval_case_p3", "带电粒子在匀强电场中的偏转位移。", "y=qUL²/(2mdv₀²)。", ["kp_p_em_field"]),
            ("eval_case_p4", "闭合电路中增大外电阻，路端电压如何变化？", "路端电压增大。", ["kp_p_em_circuit"]),
        ]
        for dataset_id, cases in (("eval_ds_math", math_cases), ("eval_ds_physics", physics_cases)):
            for index, (case_id, question, answer, kps) in enumerate(cases):
                session.add(
                    EvalCase(
                        case_id=case_id,
                        dataset_id=dataset_id,
                        question=question,
                        expected_answer=answer,
                        expected_kp_ids=kps,
                        difficulty=("easy", "medium", "hard")[index % 3],
                        created_at=days_ago(30),
                    )
                )

        for spec in EVAL_RUNS:
            created = days_ago(int(spec["days"]))
            session.add(
                EvalRun(
                    run_id=spec["id"],
                    tenant_id=MAIN_TENANT,
                    dataset_id=spec["dataset"],
                    dataset_name=spec["name"],
                    model=spec["model"],
                    recall=float(spec["recall"]),
                    accuracy=float(spec["accuracy"]),
                    citation_accuracy=float(spec["citation"]),
                    status=spec["status"],
                    metrics=_metrics(
                        float(spec["recall"]), float(spec["accuracy"]), float(spec["citation"])
                    ),
                    created_by="usr_zhangwei",
                    created_at=created,
                    finished_at=created + timedelta(minutes=6),
                )
            )

        session.add_all(
            [
                EvalResult(
                    result_id="eval_res_001",
                    run_id="eval_001",
                    case_id="eval_case_m4",
                    question="正方体中求异面直线所成角。",
                    reason="引用段落命中，但答案步骤不完整。",
                    passed=False,
                    score=0.58,
                ),
                EvalResult(
                    result_id="eval_res_002",
                    run_id="eval_003",
                    case_id="eval_case_m3",
                    question="求数列 1/(n(n+1)) 的前 n 项和。",
                    reason="未召回关联知识点对应的已发布题目。",
                    passed=False,
                    score=0.0,
                ),
                EvalResult(
                    result_id="eval_res_003",
                    run_id="eval_003",
                    case_id="eval_case_p3",
                    question="带电粒子在匀强电场中的偏转位移。",
                    reason="命中题目质量分偏低（0.52）。",
                    passed=False,
                    score=0.52,
                ),
            ]
        )

        for spec in PUBLISH_ORDERS:
            created = days_ago(int(spec["days"]))
            status = str(spec["status"])
            published_at = (
                created + timedelta(minutes=20)
                if status in {"published", "rolled_back"}
                else None
            )
            session.add(
                PublishOrder(
                    publish_id=spec["id"],
                    tenant_id=MAIN_TENANT,
                    target=spec["target"],
                    scope="full",
                    status=status,
                    alias_chunks=f"chunks_{spec['version']}",
                    alias_questions=f"questions_{spec['version']}",
                    index_version_old=spec["old"],
                    index_version_new=spec["version"],
                    versions={
                        "vectorCount": spec["vector_count"],
                        "chunkCount": 68,
                        "questionCount": 8,
                    },
                    checks=CHECK_PASS,
                    changes=CHANGES,
                    note=spec["note"],
                    created_by=spec["creator"],
                    created_by_name=spec["creator_name"],
                    created_at=created,
                    staged_at=created + timedelta(minutes=8)
                    if status in {"published", "rolled_back"}
                    else None,
                    published_at=published_at,
                    rolled_back_at=created + timedelta(days=1)
                    if status == "rolled_back"
                    else None,
                )
            )

        for audit_id, actor_id, actor_name, action, resource_type, resource_id, days in AUDITS:
            session.add(
                AuditLog(
                    tenant_id=MAIN_TENANT,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    before={},
                    after={"source": "seed"},
                    ip="127.0.0.1",
                    request_id=f"req_{audit_id}",
                    created_at=days_ago(days),
                )
            )

        session.add_all(
            [
                OutboxEvent(
                    tenant_id=MAIN_TENANT,
                    event_type="asset.published",
                    aggregate_type="publish_order",
                    aggregate_id="pub_001",
                    payload={"version": "20261010_090000"},
                    status="published",
                    created_at=days_ago(14),
                    published_at=days_ago(14),
                ),
                OutboxEvent(
                    tenant_id=MAIN_TENANT,
                    event_type="knowledge.merged",
                    aggregate_type="knowledge_point",
                    aggregate_id="kp_m_func_prop",
                    payload={"source": "kp_m_func_prop_old", "target": "kp_m_func_prop"},
                    status="pending",
                    created_at=days_ago(2),
                ),
            ]
        )

        await session.commit()
    log.info("seed_pg_done")
    return True


def _current_step(status: str) -> str:
    return {
        "pending": "upload",
        "running": "split",
        "succeeded": "quality",
        "failed": "ocr",
        "canceled": "upload",
    }.get(status, "upload")


def _step_status(job_status: str, position: int) -> str:
    if job_status == "succeeded":
        return "succeeded"
    if job_status == "running":
        return "succeeded" if position < 2 else ("running" if position == 2 else "pending")
    if job_status == "failed":
        return "succeeded" if position < 2 else ("failed" if position == 2 else "pending")
    return "pending"


# --------------------------------------------------------------------------- #
# Mongo
# --------------------------------------------------------------------------- #

TENANTS = [
    {
        "_id": "tnt_yxp",
        "name": "上海优学培",
        "plan": "旗舰版",
        "status": "active",
        "created_at": days_ago(180),
    },
    {
        "_id": "tnt_bj",
        "name": "北京智学",
        "plan": "标准版",
        "status": "active",
        "created_at": days_ago(150),
    },
    {
        "_id": "tnt_gz",
        "name": "广州启明",
        "plan": "标准版",
        "status": "active",
        "created_at": days_ago(120),
    },
    {
        "_id": "tnt_cd",
        "name": "成都博文",
        "plan": "标准版",
        "status": "suspended",
        "created_at": days_ago(90),
    },
]

TEXTBOOKS: list[dict[str, Any]] = [
    {
        "_id": "tb_math_high_1",
        "title": "高中数学知识手册",
        "edition": "人教 A 版",
        "subject": "数学",
        "stage": "high",
        "page_count": 88,
        "status": "published",
        "license": "lic_math_high_1",
        "updated_at": days_ago(1),
    },
    {
        "_id": "tb_physics_high_1",
        "title": "高中物理知识手册",
        "edition": "人教版",
        "subject": "物理",
        "stage": "high",
        "page_count": 77,
        "status": "published",
        "license": "lic_physics_high_1",
        "updated_at": days_ago(2),
    },
    {
        "_id": "tb_math_high_2",
        "title": "高中数学题型清单",
        "edition": "通用版",
        "subject": "数学",
        "stage": "high",
        "page_count": 132,
        "status": "reviewing",
        "license": "lic_math_high_2",
        "updated_at": days_ago(3),
    },
    {
        "_id": "tb_physics_high_2",
        "title": "高中物理实验手册",
        "edition": "人教版",
        "subject": "物理",
        "stage": "high",
        "page_count": 96,
        "status": "draft",
        "license": "lic_physics_high_2",
        "updated_at": days_ago(4),
    },
]

CHAPTERS: dict[str, list[tuple[str, int, int]]] = {
    "tb_math_high_1": [
        ("第一章 集合与常用逻辑", 1, 18),
        ("第二章 函数与导数", 19, 24),
        ("第三章 数列", 43, 16),
        ("第四章 解析几何", 59, 30),
    ],
    "tb_physics_high_1": [
        ("第一章 运动的描述", 1, 12),
        ("第三章 牛顿运动定律", 25, 18),
        ("第五章 机械能", 39, 20),
        ("第六章 电场与电路", 59, 27),
    ],
    "tb_math_high_2": [
        ("专题一 函数与导数", 1, 28),
        ("专题二 数列与不等式", 31, 26),
        ("专题三 圆锥曲线", 57, 34),
        ("专题四 立体几何", 91, 24),
    ],
    "tb_physics_high_2": [
        ("实验一 运动学实验", 1, 16),
        ("实验二 力学实验", 17, 22),
        ("实验三 电学实验", 39, 32),
        ("实验四 光学实验", 71, 18),
    ],
}

LICENSES = [
    ("lic_math_high_1", "tb_math_high_1", "上海优学培", "内部教研使用", 420),
    ("lic_physics_high_1", "tb_physics_high_1", "上海优学培", "内部教研使用", 45),
    ("lic_math_high_2", "tb_math_high_2", "上海优学培", "内部教研使用", 540),
]


def _node(
    node_id: str,
    name: str,
    level: int,
    parent_id: str | None,
    order: int,
    code: str,
    path: str,
    *,
    subject: str,
    coverage: int = 0,
    status: str = "published",
    pitfalls: list[str] | None = None,
    paragraph_count: int = 0,
) -> dict[str, Any]:
    return {
        "_id": node_id,
        "tenant_id": MAIN_TENANT,
        "subject": subject,
        "stage": "high",
        "name": name,
        "level": level,
        "parent_id": parent_id,
        "order": order,
        "code": code,
        "path": path,
        "coverage": coverage,
        "status": status,
        "pitfalls": pitfalls or [],
        "paragraph_count": paragraph_count,
        "question_count": 0,
        "updated_at": days_ago(2),
    }


KNOWLEDGE_NODES = [
    _node("kp_m_root", "高中数学", 0, None, 0, "MATH", "高中数学", subject="数学", coverage=62, paragraph_count=88),
    _node("kp_m_func", "函数与导数", 1, "kp_m_root", 0, "MATH-FUNC", "高中数学 / 函数与导数", subject="数学", coverage=86),
    _node("kp_m_func_prop", "函数的性质", 2, "kp_m_func", 0, "MATH-FUNC-01", "高中数学 / 函数与导数 / 函数的性质", subject="数学", coverage=92, paragraph_count=14, pitfalls=["忽略定义域限制", "单调区间端点是否闭合"]),
    _node("kp_m_func_deriv", "导数的应用", 2, "kp_m_func", 1, "MATH-FUNC-02", "高中数学 / 函数与导数 / 导数的应用", subject="数学", coverage=84, paragraph_count=11, pitfalls=["求导后未检查驻点", "极值与最值混淆"]),
    _node("kp_m_func_zero", "函数零点", 2, "kp_m_func", 2, "MATH-FUNC-03", "高中数学 / 函数与导数 / 函数零点", subject="数学", coverage=70, paragraph_count=8),
    _node("kp_m_seq", "数列", 1, "kp_m_root", 1, "MATH-SEQ", "高中数学 / 数列", subject="数学", coverage=74),
    _node("kp_m_seq_arith", "等差数列", 2, "kp_m_seq", 0, "MATH-SEQ-01", "高中数学 / 数列 / 等差数列", subject="数学", coverage=88, paragraph_count=9, pitfalls=["项数计算错误"]),
    _node("kp_m_seq_geo", "等比数列", 2, "kp_m_seq", 1, "MATH-SEQ-02", "高中数学 / 数列 / 等比数列", subject="数学", coverage=66, paragraph_count=8),
    _node("kp_m_seq_sum", "数列求和", 2, "kp_m_seq", 2, "MATH-SEQ-03", "高中数学 / 数列 / 数列求和", subject="数学", coverage=79, paragraph_count=10, pitfalls=["裂项后未检查相消项"]),
    _node("kp_m_geo", "解析几何", 1, "kp_m_root", 2, "MATH-GEO", "高中数学 / 解析几何", subject="数学", coverage=80),
    _node("kp_m_geo_line", "直线与圆", 2, "kp_m_geo", 0, "MATH-GEO-01", "高中数学 / 解析几何 / 直线与圆", subject="数学", coverage=82, paragraph_count=12, pitfalls=["遗漏斜率不存在的情况"]),
    _node("kp_m_geo_conic", "圆锥曲线", 2, "kp_m_geo", 1, "MATH-GEO-02", "高中数学 / 解析几何 / 圆锥曲线", subject="数学", coverage=75, paragraph_count=15, pitfalls=["联立方程后判别式遗漏", "中点弦斜率公式方向错误"]),
    _node("kp_m_geo_param", "参数方程", 2, "kp_m_geo", 2, "MATH-GEO-03", "高中数学 / 解析几何 / 参数方程", subject="数学", coverage=0, status="draft", paragraph_count=3),
    _node("kp_m_solid", "立体几何", 1, "kp_m_root", 3, "MATH-SOLID", "高中数学 / 立体几何", subject="数学", coverage=58),
    _node("kp_m_solid_vec", "空间向量", 2, "kp_m_solid", 0, "MATH-SOLID-01", "高中数学 / 立体几何 / 空间向量", subject="数学", coverage=0, paragraph_count=4),
    _node("kp_m_solid_angle", "空间角与距离", 2, "kp_m_solid", 1, "MATH-SOLID-02", "高中数学 / 立体几何 / 空间角与距离", subject="数学", coverage=86, paragraph_count=7, pitfalls=["法向量方向取反导致角度互补"]),
    _node("kp_p_root", "高中物理", 0, None, 1, "PHYS", "高中物理", subject="物理", coverage=71, paragraph_count=77),
    _node("kp_p_mech", "力学", 1, "kp_p_root", 0, "PHYS-MECH", "高中物理 / 力学", subject="物理", coverage=78),
    _node("kp_p_mech_motion", "运动学", 2, "kp_p_mech", 0, "PHYS-MECH-01", "高中物理 / 力学 / 运动学", subject="物理", coverage=90, paragraph_count=10, pitfalls=["未规定正方向", "平均速度与瞬时速度混淆"]),
    _node("kp_p_mech_newton", "牛顿运动定律", 2, "kp_p_mech", 1, "PHYS-MECH-02", "高中物理 / 力学 / 牛顿运动定律", subject="物理", coverage=87, paragraph_count=12, pitfalls=["受力分析漏力", "整体法与隔离法混用"]),
    _node("kp_p_mech_energy", "机械能守恒", 2, "kp_p_mech", 2, "PHYS-MECH-03", "高中物理 / 力学 / 机械能守恒", subject="物理", coverage=0, paragraph_count=5),
    _node("kp_p_em", "电磁学", 1, "kp_p_root", 1, "PHYS-EM", "高中物理 / 电磁学", subject="物理", coverage=70),
    _node("kp_p_em_field", "电场", 2, "kp_p_em", 0, "PHYS-EM-01", "高中物理 / 电磁学 / 电场", subject="物理", coverage=83, paragraph_count=11, pitfalls=["场强方向与受力方向混淆"]),
    _node("kp_p_em_circuit", "恒定电流", 2, "kp_p_em", 1, "PHYS-EM-02", "高中物理 / 电磁学 / 恒定电流", subject="物理", coverage=81, paragraph_count=9),
    _node("kp_p_em_induction", "电磁感应", 2, "kp_p_em", 2, "PHYS-EM-03", "高中物理 / 电磁学 / 电磁感应", subject="物理", coverage=0, status="draft", paragraph_count=3),
    _node("kp_p_optics", "热学与光学", 1, "kp_p_root", 2, "PHYS-OPT", "高中物理 / 热学与光学", subject="物理", coverage=46),
    _node("kp_p_optics_wave", "机械波", 2, "kp_p_optics", 0, "PHYS-OPT-01", "高中物理 / 热学与光学 / 机械波", subject="物理", coverage=76, paragraph_count=7),
    _node("kp_p_optics_light", "光的折射", 2, "kp_p_optics", 1, "PHYS-OPT-02", "高中物理 / 热学与光学 / 光的折射", subject="物理", coverage=0, paragraph_count=2),
]

RELATIONS = [
    ("kp_m_func_prop", "kp_m_func_deriv", "prerequisite"),
    ("kp_m_func_deriv", "kp_m_func_zero", "prerequisite"),
    ("kp_m_seq_arith", "kp_m_seq_sum", "prerequisite"),
    ("kp_m_seq_geo", "kp_m_seq_sum", "prerequisite"),
    ("kp_m_geo_line", "kp_m_geo_conic", "prerequisite"),
    ("kp_m_solid_vec", "kp_m_solid_angle", "prerequisite"),
    ("kp_p_mech_motion", "kp_p_mech_newton", "prerequisite"),
    ("kp_p_mech_newton", "kp_p_mech_energy", "prerequisite"),
    ("kp_p_em_field", "kp_p_em_circuit", "prerequisite"),
    ("kp_p_em_circuit", "kp_p_em_induction", "prerequisite"),
    ("kp_p_optics_wave", "kp_p_optics_light", "prerequisite"),
]

QUESTIONS: list[dict[str, Any]] = [
    {
        "_id": "q_math_001",
        "type": "single_choice",
        "difficulty": "easy",
        "stem": "函数 f(x)=x³-3x 的单调递增区间是（ ）。",
        "options": [
            {"key": "A", "text": "(-1,1)"},
            {"key": "B", "text": "(-∞,-1) 和 (1,+∞)"},
            {"key": "C", "text": "(-∞,1)"},
            {"key": "D", "text": "(0,+∞)"},
        ],
        "answer": "B",
        "analysis": "f'(x)=3x²-3=3(x-1)(x+1)，在 (-∞,-1) 与 (1,+∞) 上大于 0。",
        "knowledge_point_ids": ["kp_m_func_prop"],
        "knowledge_points": ["函数的性质"],
        "source": "高中数学知识手册 · 第二章",
        "quality": 0.92,
        "status": "published",
        "age": 6,
    },
    {
        "_id": "q_math_002",
        "type": "fill_blank",
        "difficulty": "easy",
        "stem": "等差数列 {aₙ} 中，a₁=2，公差 d=3，则 a₁₀=______。",
        "options": None,
        "answer": "29",
        "analysis": "a₁₀=a₁+9d=2+27=29。",
        "knowledge_point_ids": ["kp_m_seq_arith"],
        "knowledge_points": ["等差数列"],
        "source": "高中数学知识手册 · 第三章",
        "quality": 0.88,
        "status": "published",
        "age": 5,
    },
    {
        "_id": "q_math_003",
        "type": "calculation",
        "difficulty": "medium",
        "stem": "已知函数 f(x)=x³-3x²+2，求其单调区间与极值。",
        "options": None,
        "answer": "增区间 (-∞,0) 与 (2,+∞)，减区间 (0,2)；极大值 f(0)=2，极小值 f(2)=-2。",
        "analysis": "f'(x)=3x²-6x=3x(x-2)，按导数符号分区间讨论。",
        "knowledge_point_ids": ["kp_m_func_deriv"],
        "knowledge_points": ["导数的应用"],
        "source": "高中数学知识手册 · 第二章",
        "quality": 0.82,
        "status": "reviewing",
        "age": 1,
    },
    {
        "_id": "q_math_004",
        "type": "single_choice",
        "difficulty": "hard",
        "stem": "已知椭圆 x²/4 + y² = 1，过点 (1,0) 的弦的中点的轨迹是（ ）。",
        "options": [
            {"key": "A", "text": "圆"},
            {"key": "B", "text": "椭圆"},
            {"key": "C", "text": "抛物线"},
            {"key": "D", "text": "双曲线"},
        ],
        "answer": "B",
        "analysis": "设中点坐标，利用点差法与中点弦斜率关系消参。",
        "knowledge_point_ids": ["kp_m_geo_conic"],
        "knowledge_points": ["圆锥曲线"],
        "source": "圆锥曲线专题 · 第 12 页",
        "quality": 0.76,
        "status": "reviewing",
        "age": 2,
    },
    {
        "_id": "q_math_005",
        "type": "calculation",
        "difficulty": "medium",
        "stem": "求数列 {1/[n(n+1)]} 的前 n 项和 Sₙ。",
        "options": None,
        "answer": "Sₙ=n/(n+1)。",
        "analysis": "裂项相消：1/[n(n+1)]=1/n-1/(n+1)。",
        "knowledge_point_ids": ["kp_m_seq_sum"],
        "knowledge_points": ["数列求和"],
        "source": "高中数学知识手册 · 第三章",
        "quality": 0.85,
        "status": "published",
        "age": 4,
    },
    {
        "_id": "q_math_006",
        "type": "fill_blank",
        "difficulty": "medium",
        "stem": "圆 x²+y²-4x+2y=0 的圆心坐标是______。",
        "options": None,
        "answer": "(2,-1)",
        "analysis": "配方得 (x-2)²+(y+1)²=5。",
        "knowledge_point_ids": ["kp_m_geo_line"],
        "knowledge_points": ["直线与圆"],
        "source": "高中数学知识手册 · 第四章",
        "quality": 0.79,
        "status": "approved",
        "age": 1,
    },
    {
        "_id": "q_math_007",
        "type": "single_choice",
        "difficulty": "hard",
        "stem": "在棱长为 2 的正方体 ABCD-A₁B₁C₁D₁ 中，异面直线 AC 与 BD₁ 所成角的余弦值是（ ）。",
        "options": [
            {"key": "A", "text": "√3/3"},
            {"key": "B", "text": "√6/3"},
            {"key": "C", "text": "1/3"},
            {"key": "D", "text": "√2/2"},
        ],
        "answer": "B",
        "analysis": "建立空间直角坐标系，用向量夹角公式计算。",
        "knowledge_point_ids": ["kp_m_solid_angle"],
        "knowledge_points": ["空间角与距离"],
        "source": "高中数学知识手册 · 第四章",
        "quality": 0.90,
        "status": "published",
        "age": 8,
    },
    {
        "_id": "q_math_008",
        "type": "single_choice",
        "difficulty": "easy",
        "stem": "函数 f（x）＝x³－3x 的单调递增区间是（ ）",
        "options": [
            {"key": "A", "text": "(-1,1)"},
            {"key": "B", "text": "(-∞,-1) 和 (1,+∞)"},
            {"key": "C", "text": "(-∞,1)"},
            {"key": "D", "text": "(0,+∞)"},
        ],
        "answer": "B",
        "analysis": "同题库原题，题干仅标点不同。",
        "knowledge_point_ids": ["kp_m_func_prop"],
        "knowledge_points": ["函数的性质"],
        "source": "高中数学题型清单 · 专题一",
        "quality": 0.70,
        "status": "draft",
        "age": 3,
    },
    {
        "_id": "q_math_009",
        "type": "calculation",
        "difficulty": "hard",
        "stem": "将参数方程 x=2cosθ, y=sinθ 化为普通方程。",
        "options": None,
        "answer": "x²/4+y²=1。",
        "analysis": "利用 cos²θ+sin²θ=1 消参。",
        "knowledge_point_ids": ["kp_m_geo_param"],
        "knowledge_points": ["参数方程"],
        "source": "高中数学知识手册 · 选修 4-4",
        "quality": 0.60,
        "status": "deprecated",
        "age": 20,
    },
    {
        "_id": "q_phys_001",
        "type": "single_choice",
        "difficulty": "easy",
        "stem": "物体做匀加速直线运动，初速度 2 m/s，加速度 1 m/s²，3 s 内的位移是（ ）。",
        "options": [
            {"key": "A", "text": "9 m"},
            {"key": "B", "text": "10.5 m"},
            {"key": "C", "text": "12 m"},
            {"key": "D", "text": "15 m"},
        ],
        "answer": "B",
        "analysis": "x=v₀t+½at²=2×3+0.5×1×9=10.5 m。",
        "knowledge_point_ids": ["kp_p_mech_motion"],
        "knowledge_points": ["运动学"],
        "source": "高中物理知识手册 · 第一章",
        "quality": 0.89,
        "status": "published",
        "age": 6,
    },
    {
        "_id": "q_phys_002",
        "type": "calculation",
        "difficulty": "medium",
        "stem": "质量 2 kg 的物块在倾角 30° 的斜面上由静止下滑，动摩擦因数 0.2，求加速度。",
        "options": None,
        "answer": "a=g(sin30°-μcos30°)≈3.2 m/s²。",
        "analysis": "沿斜面方向列牛顿第二定律，垂直方向求支持力。",
        "knowledge_point_ids": ["kp_p_mech_newton"],
        "knowledge_points": ["牛顿运动定律"],
        "source": "高中物理知识手册 · 第三章",
        "quality": 0.91,
        "status": "published",
        "age": 5,
    },
    {
        "_id": "q_phys_003",
        "type": "fill_blank",
        "difficulty": "hard",
        "stem": "一带电粒子以速度 v₀ 垂直进入匀强电场，板长 L、板间距 d、电压 U，则出射时的偏转位移为______。",
        "options": None,
        "answer": "y=qUL²/(2mdv₀²)",
        "analysis": "类平抛运动，沿电场方向做匀加速直线运动。",
        "knowledge_point_ids": ["kp_p_em_field"],
        "knowledge_points": ["电场"],
        "source": "高中物理知识手册 · 第六章",
        "quality": 0.78,
        "status": "reviewing",
        "age": 4,
    },
    {
        "_id": "q_phys_004",
        "type": "single_choice",
        "difficulty": "medium",
        "stem": "闭合电路中，电源电动势不变，增大外电阻 R，路端电压将（ ）。",
        "options": [
            {"key": "A", "text": "增大"},
            {"key": "B", "text": "减小"},
            {"key": "C", "text": "不变"},
            {"key": "D", "text": "先增大后减小"},
        ],
        "answer": "A",
        "analysis": "U=E-Ir，R 增大时 I 减小，U 增大。",
        "knowledge_point_ids": ["kp_p_em_circuit"],
        "knowledge_points": ["恒定电流"],
        "source": "高中物理知识手册 · 第六章",
        "quality": 0.87,
        "status": "published",
        "age": 7,
    },
    {
        "_id": "q_phys_005",
        "type": "calculation",
        "difficulty": "medium",
        "stem": "一列简谐横波沿 x 轴正方向传播，波长 4 m，周期 0.5 s，求波速。",
        "options": None,
        "answer": "v=λ/T=8 m/s。",
        "analysis": "直接使用波速公式 v=λ/T。",
        "knowledge_point_ids": ["kp_p_optics_wave"],
        "knowledge_points": ["机械波"],
        "source": "高中物理知识手册 · 第八章",
        "quality": 0.83,
        "status": "published",
        "age": 9,
    },
    {
        "_id": "q_phys_006",
        "type": "single_choice",
        "difficulty": "hard",
        "stem": "光滑水平面上，两小球发生弹性正碰，碰后速度关系满足（ ）。",
        "options": [
            {"key": "A", "text": "动量守恒，动能减少"},
            {"key": "B", "text": "动量守恒，动能守恒"},
            {"key": "C", "text": "动量不守恒，动能守恒"},
            {"key": "D", "text": "动量和动能都不守恒"},
        ],
        "answer": "B",
        "analysis": "弹性碰撞同时满足动量守恒与动能守恒。",
        "knowledge_point_ids": ["kp_p_mech_energy"],
        "knowledge_points": ["机械能守恒"],
        "source": "高中物理知识手册 · 第五章",
        "quality": 0.86,
        "status": "published",
        "age": 10,
    },
]

VARIANTS = [
    ("var_math_001", "q_math_001", "函数 f(x)=x³-6x 的单调递减区间是（ ）。", "medium", "改变一次项系数"),
    ("var_math_005", "q_math_005", "求数列 {1/[(2n-1)(2n+1)]} 的前 n 项和。", "hard", "裂项分母改为连续奇数"),
    ("var_phys_001", "q_phys_001", "物体以 3 m/s 初速度匀减速运动，加速度大小 1 m/s²，4 s 内位移是多少？", "medium", "改为匀减速并调整数据"),
]

EVIDENCE = [
    ("chk_ev_01", "kp_m_func_prop", "tb_math_high_1", "高中数学知识手册", "第二章 2.2 节 · 第 21 页", "人教 A 版", "设函数 f(x) 在区间 I 上有定义，若对任意 x₁<x₂ 都有 f(x₁)<f(x₂)，则称 f(x) 在 I 上单调递增。"),
    ("chk_ev_02", "kp_m_seq_arith", "tb_math_high_1", "高中数学知识手册", "第三章 3.1 节 · 第 45 页", "人教 A 版", "等差数列的通项公式为 aₙ=a₁+(n-1)d，前 n 项和为 Sₙ=n(a₁+aₙ)/2。"),
    ("chk_ev_03", "kp_m_seq_sum", "tb_math_high_1", "高中数学知识手册", "第三章 3.4 节 · 第 55 页", "人教 A 版", "裂项相消法适用于通项可拆分为两项之差形式的数列求和。"),
    ("chk_ev_04", "kp_m_solid_angle", "tb_math_high_1", "高中数学知识手册", "第四章 4.3 节 · 第 68 页", "人教 A 版", "异面直线所成角可通过平移转化为相交直线夹角，或建立空间向量求解。"),
    ("chk_ev_05", "kp_p_mech_motion", "tb_physics_high_1", "高中物理知识手册", "第一章 1.4 节 · 第 12 页", "人教版", "匀变速直线运动的位移公式为 x=v₀t+½at²。"),
    ("chk_ev_06", "kp_p_mech_newton", "tb_physics_high_1", "高中物理知识手册", "第三章 3.2 节 · 第 28 页", "人教版", "牛顿第二定律 F=ma，应用时需先进行受力分析并规定正方向。"),
    ("chk_ev_07", "kp_p_em_field", "tb_physics_high_1", "高中物理知识手册", "第六章 6.3 节 · 第 62 页", "人教版", "带电粒子垂直进入匀强电场时做类平抛运动，偏转位移 y=qUL²/(2mdv₀²)。"),
    ("chk_ev_08", "kp_p_em_circuit", "tb_physics_high_1", "高中物理知识手册", "第六章 6.5 节 · 第 70 页", "人教版", "闭合电路欧姆定律 I=E/(R+r)，路端电压 U=E-Ir。"),
]

ANNOTATION_BLOCKS = [
    ("blk_ant1_1", "ant_001", 0, "标题", "2.3 函数的单调性", 98, "resolved"),
    ("blk_ant1_2", "ant_001", 1, "正文", "设函数 f(x) 在区间 I 上有定义，若对任意 x₁<x₂ 都有 f(x₁)<f(x₂)，则称 f(x) 在 I 上单调递增。", 71, "pending"),
    ("blk_ant1_3", "ant_001", 2, "公式", "f'(x) = 3x² - 6x + 2", 64, "pending"),
    ("blk_ant1_4", "ant_001", 3, "图注", "图 2-7 函数图像与切线示意", 58, "pending"),
    ("blk_ant2_1", "ant_002", 0, "正文", "物体在只有重力做功的情况下，机械能保持不变。", 82, "resolved"),
    ("blk_ant2_2", "ant_002", 1, "公式", "E_k + E_p = ½mv² + mgh = 常量", 67, "pending"),
    ("blk_ant2_3", "ant_002", 2, "表格", "表 5-2 不同高度处的重力势能", 55, "pending"),
]


async def _seed_evidence_chunks(docs: list[dict[str, Any]]) -> None:
    """Seed evidence chunks into Milvus (vectors computed when the embedder is on)."""
    if not docs:
        return
    embedder = get_embedding_provider()
    if embedder.name != "unconfigured":
        batch = await embedder.embed_documents([str(doc["content"]) for doc in docs])
        for doc, dense, sparse in zip(docs, batch.dense, batch.sparse, strict=True):
            doc["dense_vector"] = [float(value) for value in dense]
            doc["sparse_vector"] = sparse_to_document(sparse)
            doc["embedding_model"] = embedder.name
    elif settings.milvus_uri:
        log.warning("seed_chunks_skipped", reason="EMBEDDING_PROVIDER 未启用，无法写入 Milvus")
        return
    await get_chunk_store().upsert(docs)


async def _seed_mongo() -> bool:
    if await mongo.col("textbooks").count_documents({}):
        return False

    for tenant in TENANTS:
        await mongo.col("tenants").insert_one(dict(tenant))

    for book in TEXTBOOKS:
        doc = {
            **book,
            "tenant_id": MAIN_TENANT,
            "license_id": book.get("license", ""),
            "created_at": days_ago(30),
        }
        await mongo.col("textbooks").insert_one(doc)
        for index, (title, page, count) in enumerate(CHAPTERS[book["_id"]], start=1):
            await mongo.col("chapters").insert_one(
                {
                    "_id": f"ch_{book['_id']}_{index}",
                    "tenant_id": MAIN_TENANT,
                    "book_id": book["_id"],
                    "no": index,
                    "title": title,
                    "page": page,
                    "question_count": count,
                }
            )

    for license_id, book_id, holder, scope, days in LICENSES:
        await mongo.col("licenses").insert_one(
            {
                "_id": license_id,
                "tenant_id": MAIN_TENANT,
                "book_id": book_id,
                "holder": holder,
                "scope": scope,
                "start_at": days_ago(365),
                "end_at": datetime.now(UTC) + timedelta(days=days),
                "updated_at": days_ago(5),
            }
        )

    for node in KNOWLEDGE_NODES:
        await mongo.col("knowledge_points").insert_one(dict(node))

    for source_id, target_id, relation_type in RELATIONS:
        await mongo.col("kp_relations").insert_one(
            {
                "tenant_id": MAIN_TENANT,
                "source_id": source_id,
                "target_id": target_id,
                "type": relation_type,
                "created_at": days_ago(20),
            }
        )

    duplicate_hash = simhash64(QUESTIONS[0]["stem"])
    for spec in QUESTIONS:
        created = days_ago(int(spec["age"]))
        published_at = created + timedelta(days=1) if spec["status"] == "published" else None
        doc = {
            "_id": spec["_id"],
            "tenant_id": MAIN_TENANT,
            "subject": "物理" if spec["_id"].startswith("q_phys") else "数学",
            "stage": "high",
            "type": spec["type"],
            "difficulty": spec["difficulty"],
            "stem": spec["stem"],
            "options": spec["options"],
            "answer": spec["answer"],
            "analysis": spec["analysis"],
            "knowledge_point_ids": spec["knowledge_point_ids"],
            "knowledge_points": spec["knowledge_points"],
            "source": spec["source"],
            "review_status": spec["status"],
            "quality_score": spec["quality"],
            "simhash": duplicate_hash if spec["_id"] == "q_math_008" else simhash64(spec["stem"]),
            "version": 1,
            "created_at": created,
            "updated_at": created + timedelta(hours=3),
            "published_at": published_at,
        }
        await mongo.col("questions").insert_one(doc)

    for variant_id, question_id, stem, difficulty, transform in VARIANTS:
        await mongo.col("question_variants").insert_one(
            {
                "_id": variant_id,
                "question_id": question_id,
                "stem": stem,
                "difficulty": difficulty,
                "transform": transform,
                "created_at": days_ago(3),
            }
        )

    evidence_docs = [
        {
            "_id": chunk_id,
            "tenant_id": MAIN_TENANT,
            "book_id": book_id,
            "book_title": book_title,
            "edition": edition,
            "location": location,
            "page": int(location.split("第 ")[-1].replace(" 页", "")) if "第 " in location else 0,
            "content": evidence,
            "evidence": evidence,
            "knowledge_point_ids": [kp_id],
            "status": "indexed",
            "created_at": days_ago(10),
        }
        for chunk_id, kp_id, book_id, book_title, location, edition, evidence in EVIDENCE
    ]
    await _seed_evidence_chunks(evidence_docs)

    for block_id, task_id, index, block_type, content, confidence, status in ANNOTATION_BLOCKS:
        await mongo.col("annotation_blocks").insert_one(
            {
                "_id": block_id,
                "tenant_id": MAIN_TENANT,
                "annotation_task_id": task_id,
                "block_index": index,
                "block_type": block_type,
                "content": content,
                "confidence": confidence,
                "status": status,
                "updated_at": days_ago(1),
            }
        )

    log.info("seed_mongo_done")
    return True


async def seed_all() -> None:
    """Seed PG and Mongo independently, then rebuild demo vector aliases."""
    await _seed_pg()
    await _seed_mongo()


async def bootstrap_vector_aliases() -> None:
    """Point the in-process alias manager at the latest published version.

    The memory adapter resets on every process start; production Milvus keeps
    aliases, so this is a no-op there (it re-points to the same collection).
    """
    async with SessionLocal() as session:
        order = (
            await session.execute(
                select(PublishOrder)
                .where(PublishOrder.status == "published")
                .order_by(PublishOrder.published_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if order is None:
        return
    vector = get_vector_index()
    try:
        await vector.commit(settings.chunks_alias, f"chunks_{order.index_version_new}")
        await vector.commit(settings.questions_alias, f"questions_{order.index_version_new}")
    except Exception as exc:  # noqa: BLE001 - admin reads must survive a cold index
        # On a fresh Milvus the collection produced by the indexing worker may not
        # exist yet. Startup should not fail: the admin UI stays readable and the
        # publish checks will report the missing index later.
        log.warning(
            "vector_alias_bootstrap_skipped",
            version=order.index_version_new,
            error=str(exc),
        )
        return
    log.info("vector_aliases_bootstrapped", version=order.index_version_new)


def new_seed_id(prefix: str) -> str:
    return new_id(prefix)
