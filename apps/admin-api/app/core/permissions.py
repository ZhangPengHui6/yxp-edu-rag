"""Permission catalogue and role matrix.

This is the authoritative backend copy of ``front/src/constants/permissions.ts``.
Keep both sides in sync; the permission manual is docs/front/权限分配手册.md.
"""

from __future__ import annotations

PERMISSIONS: list[dict[str, str]] = [
    {"code": "dashboard.view", "module": "工作台", "action": "view", "label": "查看工作台"},
    {"code": "ingestion.view", "module": "内容接入", "action": "view", "label": "查看入库任务"},
    {"code": "ingestion.create", "module": "内容接入", "action": "create", "label": "上传/创建任务"},
    {"code": "ingestion.retry", "module": "内容接入", "action": "retry", "label": "重试任务"},
    {"code": "ingestion.cancel", "module": "内容接入", "action": "cancel", "label": "终止任务"},
    {"code": "textbook.view", "module": "教材", "action": "view", "label": "查看教材"},
    {"code": "textbook.create", "module": "教材", "action": "create", "label": "录入教材"},
    {"code": "textbook.edit", "module": "教材", "action": "edit", "label": "编辑教材"},
    {"code": "textbook.delete", "module": "教材", "action": "delete", "label": "下架教材"},
    {"code": "textbook.license", "module": "教材", "action": "license", "label": "绑定授权"},
    {"code": "question.view", "module": "题库", "action": "view", "label": "查看题目"},
    {"code": "question.import", "module": "题库", "action": "import", "label": "导入题目"},
    {"code": "question.edit", "module": "题库", "action": "edit", "label": "编辑题目"},
    {"code": "question.variant", "module": "题库", "action": "variant", "label": "生成变式"},
    {"code": "question.dedupe", "module": "题库", "action": "dedupe", "label": "处理重复题"},
    {"code": "knowledge.view", "module": "知识点", "action": "view", "label": "查看知识点"},
    {"code": "knowledge.create", "module": "知识点", "action": "create", "label": "新建知识点"},
    {"code": "knowledge.edit", "module": "知识点", "action": "edit", "label": "编辑知识点"},
    {"code": "knowledge.merge", "module": "知识点", "action": "merge", "label": "合并知识点"},
    {"code": "knowledge.coverage", "module": "知识点", "action": "coverage", "label": "查看覆盖分析"},
    {"code": "annotation.view", "module": "校对", "action": "view", "label": "进入校对队列"},
    {"code": "annotation.resolve", "module": "校对", "action": "resolve", "label": "提交校对结果"},
    {"code": "review.view", "module": "审核", "action": "view", "label": "查看待审任务"},
    {"code": "review.decide", "module": "审核", "action": "decide", "label": "通过/驳回"},
    {"code": "review.batch", "module": "审核", "action": "batch", "label": "批量审核"},
    {"code": "publish.view", "module": "发布", "action": "view", "label": "查看发布单"},
    {"code": "publish.create", "module": "发布", "action": "create", "label": "创建发布单"},
    {"code": "publish.commit", "module": "发布", "action": "commit", "label": "执行提交/暂存"},
    {"code": "publish.rollback", "module": "发布", "action": "rollback", "label": "回滚版本"},
    {"code": "evaluation.view", "module": "评测", "action": "view", "label": "查看评测报告"},
    {"code": "evaluation.run", "module": "评测", "action": "run", "label": "触发评测"},
    {"code": "evaluation.dataset", "module": "评测", "action": "dataset", "label": "管理数据集"},
    {"code": "iam.user", "module": "系统", "action": "user", "label": "用户管理"},
    {"code": "iam.role", "module": "系统", "action": "role", "label": "角色与权限"},
    {"code": "iam.dict", "module": "系统", "action": "dict", "label": "字典维护"},
    {"code": "iam.tenant", "module": "系统", "action": "tenant", "label": "租户管理"},
    {"code": "iam.audit", "module": "系统", "action": "audit", "label": "审计日志"},
]

ALL_CODES = [item["code"] for item in PERMISSIONS]
VIEW_CODES = [item["code"] for item in PERMISSIONS if item["action"] == "view"]

ROLES: list[dict] = [
    {
        "code": "super_admin",
        "name": "超级管理员",
        "description": "平台全部权限，含租户与审计；用于初始化和应急处置",
        "permissions": ["*"],
    },
    {
        "code": "kb_admin",
        "name": "教研管理员",
        "description": "负责教材、题库、知识点与发布全流程，不涉及租户与审计",
        "permissions": [
            "dashboard.view",
            "ingestion.view", "ingestion.create", "ingestion.retry", "ingestion.cancel",
            "textbook.view", "textbook.create", "textbook.edit", "textbook.delete",
            "textbook.license",
            "question.view", "question.import", "question.edit", "question.variant",
            "question.dedupe",
            "knowledge.view", "knowledge.create", "knowledge.edit", "knowledge.merge",
            "knowledge.coverage",
            "annotation.view", "annotation.resolve",
            "review.view", "review.decide", "review.batch",
            "publish.view", "publish.create", "publish.commit", "publish.rollback",
            "evaluation.view", "evaluation.run", "evaluation.dataset",
            "iam.user", "iam.role", "iam.dict",
        ],
    },
    {
        "code": "annotator",
        "name": "校对员",
        "description": "处理低置信度识别结果，只读教材与题库",
        "permissions": [
            "dashboard.view", "ingestion.view", "textbook.view", "question.view",
            "knowledge.view", "annotation.view", "annotation.resolve",
        ],
    },
    {
        "code": "reviewer",
        "name": "审核员",
        "description": "审核题目、知识点与教材变更，查看发布单但不可提交",
        "permissions": [
            "dashboard.view", "ingestion.view", "textbook.view", "question.view",
            "knowledge.view", "annotation.view", "review.view", "review.decide",
            "review.batch", "publish.view",
        ],
    },
    {
        "code": "publisher",
        "name": "发布管理员",
        "description": "负责索引构建、提交与回滚，可触发发布前评测",
        "permissions": [
            "dashboard.view", "ingestion.view", "question.view", "knowledge.view",
            "publish.view", "publish.create", "publish.commit", "publish.rollback",
            "evaluation.view", "evaluation.run",
        ],
    },
    {
        "code": "viewer",
        "name": "只读访客",
        "description": "仅查看，用于教研旁听与外部评审",
        "permissions": VIEW_CODES,
    },
]

ROLE_BY_CODE = {role["code"]: role for role in ROLES}


def permissions_of(role_code: str) -> list[str]:
    role = ROLE_BY_CODE.get(role_code)
    if role is None:
        return []
    if "*" in role["permissions"]:
        return list(ALL_CODES)
    return list(role["permissions"])
