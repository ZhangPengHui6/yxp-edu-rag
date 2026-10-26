"""Node contract and shared step bookkeeping for the ingestion graph.

Every node implements :meth:`IngestionNode.process`, which does the real work and
returns the state update plus the human-readable detail shown in the admin UI.
The base class wraps ``process`` with the same lifecycle the previous imperative
pipeline used:

* mark ``ingestion_job_step`` running before the work;
* mark it succeeded and bump ``job.progress`` afterwards;
* detect a cancel that arrived while the node was running and route the graph
  to ``END`` instead of starting the next step.

``mode="simulated"`` also lives here: tests and the placeholder provider keep the
old behaviour (burn ``PIPELINE_STEP_DELAY`` per step) without waking ``process``.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import IngestionJob, IngestionJobStep
from app.modules.ingestion.graph.state import IngestionRuntime, IngestionState
from app.modules.ingestion.rules import STEP_DEFS, progress_at
from app.shared.time import utcnow


@dataclass
class NodeResult:
    """What a node hands back to the graph runtime."""

    update: dict[str, Any]
    detail: str


async def start_step(
    session: AsyncSession, job: IngestionJob, step: IngestionJobStep
) -> None:
    step.status = "running"
    step.started_at = step.started_at or utcnow()
    job.current_step = step.step
    job.status = "running"
    await session.commit()


async def finish_step(
    session: AsyncSession,
    job: IngestionJob,
    step: IngestionJobStep,
    position: int,
    detail: str,
) -> bool:
    """Mark a step done; return ``False`` when the job was canceled meanwhile."""
    await session.refresh(job)
    if job.status == "canceled":
        return False
    step.status = "succeeded"
    step.detail = detail
    step.finished_at = utcnow()
    job.progress = progress_at(position)
    job.current_step = (
        STEP_DEFS[position + 1]["key"] if position + 1 < len(STEP_DEFS) else "quality"
    )
    await session.commit()
    return True


class IngestionNode(ABC):
    """One step of the pipeline; subclasses only implement ``process``."""

    key = ""
    position = 0
    simulated_detail = ""

    def __init__(self) -> None:
        if not self.key:
            raise ValueError(f"{type(self).__name__} 必须定义 key")

    async def run(self, state: IngestionState) -> dict[str, Any]:
        runtime = state["runtime"]
        step = runtime.steps[self.position]
        await start_step(runtime.session, runtime.job, step)

        if state.get("mode") == "simulated":
            await asyncio.sleep(settings.pipeline_step_delay)
            update: dict[str, Any] = {}
            detail = self.simulated_detail or f"{self.key} 完成（模拟流水线）"
        else:
            result = await self.process(state)
            update = result.update
            detail = result.detail

        if not await finish_step(runtime.session, runtime.job, step, self.position, detail):
            update["canceled"] = True
        return update

    @abstractmethod
    async def process(self, state: IngestionState) -> NodeResult:
        """Run the step and return its state update plus UI detail."""
        raise NotImplementedError


def runtime_of(state: IngestionState) -> IngestionRuntime:
    return state["runtime"]
