from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.schemas import CamelModel


class KnowledgeNodeOut(CamelModel):
    id: str
    name: str
    level: int
    question_count: int
    coverage: int
    status: Literal["published", "draft"]
    children: list[KnowledgeNodeOut] | None = None


class EvidenceOut(CamelModel):
    book: str
    location: str
    excerpt: str
    edition: str


class DistributionOut(CamelModel):
    label: str
    count: int
    percent: int


class RelationOut(CamelModel):
    type: Literal["前置", "后继"]
    items: list[str]


class DetailStatsOut(CamelModel):
    questions: int
    paragraphs: int
    coverage: int
    prerequisites: int


class KnowledgeDetailOut(CamelModel):
    node: KnowledgeNodeOut
    code: str
    path: str
    stats: DetailStatsOut
    evidences: list[EvidenceOut]
    distribution: list[DistributionOut]
    relations: list[RelationOut]
    pitfalls: list[str]


class CoverageGapOut(CamelModel):
    module: str
    rate: float
    missing: int


class SubjectCoverageOut(CamelModel):
    subject: str
    total: int
    covered: int
    rate: float


class CoverageReportOut(CamelModel):
    total: int
    covered: int
    rate: float
    gaps: list[CoverageGapOut]
    by_subject: list[SubjectCoverageOut]


class MergeIn(CamelModel):
    target_id: str = Field(min_length=1)


class MergeResultOut(CamelModel):
    source: str
    target: str
    moved_questions: int
