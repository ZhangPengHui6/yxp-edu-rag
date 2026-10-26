"""LangGraph orchestration for the offline ingestion pipeline.

The pipeline is a linear graph of five nodes:

    START → upload → ocr → split → struct → quality → END

Each edge after a node is conditional on cancellation: when the admin cancels a
job while a node is running, that node's bookkeeping marks the state
``canceled`` and the graph routes straight to ``END`` instead of starting the
next step.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.modules.ingestion.graph.nodes import (
    OcrNode,
    QualityNode,
    SplitNode,
    StructNode,
    UploadNode,
)
from app.modules.ingestion.graph.state import IngestionState

NODES = (UploadNode(), OcrNode(), SplitNode(), StructNode(), QualityNode())


def _continue_to(next_key: str):
    """Route to ``next_key`` unless the job was canceled during this node."""

    def route(state: IngestionState) -> str:
        return END if state.get("canceled") else next_key

    return route


def build_ingestion_graph() -> CompiledStateGraph:
    builder = StateGraph(state_schema=IngestionState)
    for node in NODES:
        builder.add_node(node.key, node.run)

    builder.add_edge(START, NODES[0].key)
    for current, following in zip(NODES, NODES[1:], strict=False):
        builder.add_conditional_edges(
            current.key,
            _continue_to(following.key),
            {following.key: following.key, END: END},
        )
    builder.add_edge(NODES[-1].key, END)
    return builder.compile()


@lru_cache(maxsize=1)
def get_ingestion_graph() -> CompiledStateGraph:
    """Compile once per process; the graph is stateless and safe to reuse."""
    return build_ingestion_graph()


async def run_ingestion_graph(state: IngestionState) -> dict[str, Any]:
    return await get_ingestion_graph().ainvoke(state)
