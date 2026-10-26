"""Knowledge tree, evidence chain, coverage and merge."""

from __future__ import annotations

from collections import Counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, NotFound
from app.domain.question_rules import type_label
from app.modules.audit import service as audit
from app.modules.knowledge import repository as repo
from app.modules.knowledge.schemas import (
    CoverageGapOut,
    CoverageReportOut,
    DetailStatsOut,
    DistributionOut,
    EvidenceOut,
    KnowledgeDetailOut,
    KnowledgeNodeOut,
    MergeResultOut,
    RelationOut,
    SubjectCoverageOut,
)

RELATION_PRE = "prerequisite"


def _children_map(nodes: list[dict]) -> dict[str | None, list[dict]]:
    grouped: dict[str | None, list[dict]] = {}
    for node in nodes:
        parent = node.get("parent_id") or None
        grouped.setdefault(parent, []).append(node)
    return grouped


def _counts_by_node(questions: list[dict]) -> Counter:
    counts: Counter = Counter()
    for question in questions:
        for kp_id in question.get("knowledge_point_ids", []) or []:
            counts[kp_id] += 1
    return counts


def _aggregate_count(node_id: str, children: dict[str | None, list[dict]], direct: Counter) -> int:
    total = int(direct.get(node_id, 0))
    for child in children.get(node_id, []):
        total += _aggregate_count(child["_id"], children, direct)
    return total


def _node_out(
    node: dict,
    children: dict[str | None, list[dict]],
    direct: Counter,
    *,
    include_children: bool = True,
) -> KnowledgeNodeOut:
    child_nodes = children.get(node["_id"], [])
    return KnowledgeNodeOut(
        id=node["_id"],
        name=node.get("name", ""),
        level=int(node.get("level", 0)),
        question_count=_aggregate_count(node["_id"], children, direct),
        coverage=int(node.get("coverage") or 0),
        status=node.get("status", "draft"),  # type: ignore[arg-type]
        children=(
            [_node_out(child, children, direct) for child in child_nodes]
            if include_children and child_nodes
            else ([] if include_children and int(node.get("level", 0)) == 0 else None)
        ),
    )


async def get_tree(tenant_id: str) -> list[KnowledgeNodeOut]:
    nodes = await repo.list_nodes(tenant_id)
    active = [node for node in nodes if node.get("status") != "deprecated"]
    children = _children_map(active)
    direct = _counts_by_node(await repo.question_kp_projection(tenant_id))
    return [_node_out(node, children, direct) for node in children.get(None, [])]


async def get_detail(tenant_id: str, node_id: str) -> KnowledgeDetailOut:
    node = await repo.get_node(tenant_id, node_id)
    if node is None:
        raise NotFound("知识点不存在")
    nodes = await repo.list_nodes(tenant_id)
    active = [item for item in nodes if item.get("status") != "deprecated"]
    children = _children_map(active)
    questions = await repo.question_kp_projection(tenant_id)
    direct = _counts_by_node(questions)
    node_out = _node_out(node, children, direct, include_children=False)
    node_out.children = None

    distribution_counter: Counter = Counter()
    for question in questions:
        if node_id in (question.get("knowledge_point_ids") or []):
            distribution_counter[type_label(question.get("type", ""))] += 1
    total = sum(distribution_counter.values())
    distribution = [
        DistributionOut(
            label=label,
            count=count,
            percent=round(count / total * 100) if total else 0,
        )
        for label, count in distribution_counter.most_common()
    ]

    relations_docs = await repo.relations_for(tenant_id, node_id)
    related_ids: set[str] = set()
    for relation in relations_docs:
        related_ids.add(relation.get("source_id", ""))
        related_ids.add(relation.get("target_id", ""))
    related_ids.discard("")
    related_ids.discard(node_id)
    names = await repo.names_by_ids(tenant_id, list(related_ids))
    prerequisites = [
        names.get(relation.get("source_id", ""), "")
        for relation in relations_docs
        if relation.get("target_id") == node_id and relation.get("type") == RELATION_PRE
    ]
    successors = [
        names.get(relation.get("target_id", ""), "")
        for relation in relations_docs
        if relation.get("source_id") == node_id and relation.get("type") != RELATION_PRE
    ]
    prerequisites = [name for name in prerequisites if name]
    successors = [name for name in successors if name]

    evidences = [
        EvidenceOut(
            book=chunk.get("book_title", ""),
            location=chunk.get("location", ""),
            excerpt=chunk.get("evidence", ""),
            edition=chunk.get("edition", ""),
        )
        for chunk in await repo.evidence_chunks(tenant_id, node_id)
    ]

    return KnowledgeDetailOut(
        node=node_out,
        code=node.get("code") or node_id,
        path=node.get("path") or node.get("name", ""),
        stats=DetailStatsOut(
            questions=int(node_out.question_count),
            paragraphs=int(node.get("paragraph_count") or len(evidences)),
            coverage=int(node.get("coverage") or 0),
            prerequisites=len(prerequisites),
        ),
        evidences=evidences,
        distribution=distribution,
        relations=[
            RelationOut(type="前置", items=prerequisites),
            RelationOut(type="后继", items=successors),
        ],
        pitfalls=list(node.get("pitfalls") or []),
    )


async def get_coverage(tenant_id: str) -> CoverageReportOut:
    nodes = [node for node in await repo.list_nodes(tenant_id) if node.get("status") != "deprecated"]
    children = _children_map(nodes)
    direct = _counts_by_node(await repo.question_kp_projection(tenant_id))
    leaves = [node for node in nodes if not children.get(node["_id"])]
    parent_of = {node["_id"]: node.get("parent_id") for node in nodes}
    node_by_id = {node["_id"]: node for node in nodes}

    def ancestor_at(node_id: str, level: int) -> str | None:
        current: str | None = node_id
        while current:
            node = node_by_id.get(current)
            if node is None:
                return None
            if int(node.get("level", 0)) == level:
                return current
            current = parent_of.get(current)
        return None

    covered = sum(1 for leaf in leaves if _aggregate_count(leaf["_id"], children, direct) > 0)
    total = len(leaves)
    per_module: dict[str, list[dict]] = {}
    for leaf in leaves:
        module_id = ancestor_at(leaf["_id"], 1) or leaf["_id"]
        per_module.setdefault(module_id, []).append(leaf)
    gaps: list[CoverageGapOut] = []
    for module_id, module_leaves in per_module.items():
        module_covered = sum(
            1 for leaf in module_leaves if _aggregate_count(leaf["_id"], children, direct) > 0
        )
        missing = len(module_leaves) - module_covered
        rate = module_covered / len(module_leaves) if module_leaves else 0
        if missing:
            gaps.append(
                CoverageGapOut(
                    module=node_by_id.get(module_id, {}).get("name", module_id),
                    rate=round(rate, 4),
                    missing=missing,
                )
            )
    gaps.sort(key=lambda gap: (gap.rate, -gap.missing))

    per_subject: dict[str, list[dict]] = {}
    for leaf in leaves:
        subject = leaf.get("subject") or "未分类"
        per_subject.setdefault(subject, []).append(leaf)
    by_subject: list[SubjectCoverageOut] = []
    for subject, subject_leaves in per_subject.items():
        subject_covered = sum(
            1 for leaf in subject_leaves if _aggregate_count(leaf["_id"], children, direct) > 0
        )
        by_subject.append(
            SubjectCoverageOut(
                subject=subject,
                total=len(subject_leaves),
                covered=subject_covered,
                rate=round(subject_covered / len(subject_leaves), 4) if subject_leaves else 0,
            )
        )

    return CoverageReportOut(
        total=total,
        covered=covered,
        rate=round(covered / total, 4) if total else 0,
        gaps=gaps,
        by_subject=by_subject,
    )


async def merge_nodes(
    session: AsyncSession,
    *,
    user: CurrentUser,
    source_id: str,
    target_id: str,
) -> MergeResultOut:
    if source_id == target_id:
        raise AppError("不能合并到自身")
    source = await repo.get_node(user.tenant_id, source_id)
    target = await repo.get_node(user.tenant_id, target_id)
    if source is None or target is None:
        raise NotFound("知识点不存在")
    if source.get("status") == "deprecated":
        raise AppError("源知识点已停用")

    nodes = await repo.list_nodes(user.tenant_id)
    parent_of = {node["_id"]: node.get("parent_id") for node in nodes}
    cursor: str | None = target_id
    while cursor:
        if cursor == source_id:
            raise AppError("目标知识点是源知识点的子节点，无法合并")
        cursor = parent_of.get(cursor)

    moved = await repo.merge_into(user.tenant_id, source, target)
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="knowledge.merge",
        resource_type="knowledge_point",
        resource_id=source_id,
        before={"source": source.get("name"), "target": target.get("name")},
        after={"movedQuestions": moved},
    )
    audit.emit(
        session,
        tenant_id=user.tenant_id,
        event_type="knowledge.merged",
        aggregate_type="knowledge_point",
        aggregate_id=target_id,
        payload={"source": source_id, "target": target_id, "movedQuestions": moved},
    )
    await session.commit()
    return MergeResultOut(source=source_id, target=target_id, moved_questions=moved)
