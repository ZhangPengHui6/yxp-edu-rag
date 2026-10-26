"""LangGraph assembly for the student query pipeline.

    START -> confirm -> retrieve -> rrf -> rerank -> generate -> END

``/v1/chat/stream`` runs the same graph without ``generate`` and streams the
answer itself so the SSE connection can emit deltas as they arrive.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, StateGraph

from kb_app.query.nodes.confirm import confirm_node
from kb_app.query.nodes.generate import generate_node
from kb_app.query.nodes.rerank import rerank_node
from kb_app.query.nodes.retrieve import retrieve_node
from kb_app.query.nodes.rrf import rrf_node
from kb_app.query.state import QueryState


def _build(*, with_generate: bool):
    builder = StateGraph(QueryState)
    builder.add_node("confirm", confirm_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("rrf", rrf_node)
    builder.add_node("rerank", rerank_node)

    builder.set_entry_point("confirm")
    builder.add_edge("confirm", "retrieve")
    builder.add_edge("retrieve", "rrf")
    builder.add_edge("rrf", "rerank")

    if with_generate:
        builder.add_node("generate", generate_node)
        builder.add_edge("rerank", "generate")
        builder.add_edge("generate", END)
    else:
        builder.add_edge("rerank", END)
    return builder.compile()


@lru_cache(maxsize=1)
def get_query_graph():
    return _build(with_generate=True)


@lru_cache(maxsize=1)
def get_retrieval_graph():
    return _build(with_generate=False)


async def run_query(state: QueryState) -> QueryState:
    return await get_query_graph().ainvoke(state)


async def run_retrieval(state: QueryState) -> QueryState:
    return await get_retrieval_graph().ainvoke(state)
