"""Structural tests for the LangGraph ingestion orchestration."""

from __future__ import annotations

from langgraph.graph import END

from app.modules.ingestion.graph.builder import (
    NODES,
    _continue_to,
    get_ingestion_graph,
)


def test_nodes_are_ordered_pipeline_steps() -> None:
    assert [node.key for node in NODES] == ["upload", "ocr", "split", "struct", "quality"]
    assert [node.position for node in NODES] == list(range(len(NODES)))


def test_graph_compiles_with_expected_nodes() -> None:
    graph = get_ingestion_graph().get_graph()
    assert {"upload", "ocr", "split", "struct", "quality"} <= set(graph.nodes)


def test_router_stops_on_cancel() -> None:
    route = _continue_to("ocr")
    assert route({"canceled": True}) == END
    assert route({"canceled": False}) == "ocr"
    assert route({}) == "ocr"
