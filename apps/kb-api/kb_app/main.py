"""Student-facing knowledge-base API (``/v1``).

Separate process from the admin API: it only reads the published Milvus chunk
alias and writes answer audits to MongoDB. It never touches PostgreSQL.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import Any

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.infra import mongo
from app.shared.ids import new_id
from app.shared.time import utcnow
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from kb_app.auth import require_api_key
from kb_app.config import get_kb_settings
from kb_app.infra.llm import stream_answer
from kb_app.query.graph import run_query, run_retrieval
from kb_app.query.nodes.generate import (
    SYSTEM_PROMPT,
    build_citations,
    build_context,
    confidence_for,
    history_messages,
)
from kb_app.schemas import ChatRequest, ChatResponse, Citation, HealthResponse, RetrievalStats

configure_logging()
log = get_logger("kb.main")
kb_settings = get_kb_settings()

NO_EVIDENCE_ANSWER = (
    "当前知识库中没有检索到与该问题相关的内容，请换个问法，"
    "或确认对应教材是否已发布。"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not kb_settings.api_key_required:
        log.warning(
            "kb_api_key_missing",
            detail="KB_API_KEY 未配置，/v1 接口仅适合本地开发",
        )
    mongo_ok = await mongo.ping()
    log.info(
        "kb_api_started",
        tenant=kb_settings.kb_default_tenant,
        chunks_alias=settings.chunks_alias,
        mongo_ok=mongo_ok,
    )
    yield
    await mongo.close()
    log.info("kb_api_stopped")


def _alias_stats(alias: str) -> dict[str, Any]:
    from pymilvus import MilvusClient

    client = MilvusClient(uri=settings.milvus_uri)
    collection: str | None = None
    try:
        info = client.describe_alias(alias)
        if isinstance(info, dict):
            collection = info.get("collection") or info.get("collection_name")
        else:  # pragma: no cover - older pymilvus object form
            collection = getattr(info, "collection", None)
    except Exception:  # noqa: BLE001 - missing alias is a health detail
        collection = None

    rows = 0
    if collection:
        try:
            stats = client.get_collection_stats(alias)
            rows = int(stats.get("row_count", 0))
        except Exception:  # noqa: BLE001
            rows = 0
    return {"collection": collection, "rows": rows}


def _milvus_health() -> dict[str, Any]:
    return {
        "chunks": _alias_stats(settings.chunks_alias),
        "questions": _alias_stats(settings.questions_alias),
    }


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or new_id("req")


def _initial_state(body: ChatRequest, request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "query": body.query,
        "messages": [item.model_dump() for item in body.messages],
        "tenant_id": (body.tenant_id or kb_settings.kb_default_tenant).strip(),
        "subject": body.subject,
        "stage": body.stage,
        "grade": body.grade,
        "book_id": body.book_id,
        "top_k": body.top_k or kb_settings.kb_top_k,
    }


async def _record_answer(
    *,
    request_id: str,
    tenant_id: str,
    query: str,
    answer: str,
    citations: list[dict[str, Any]],
    confidence: str,
    model: str,
    latency_ms: int,
    retrieval: dict[str, int],
) -> None:
    if not kb_settings.kb_record_answers:
        return
    try:
        await mongo.col("answer_records").insert_one(
            {
                "_id": new_id("ansr"),
                "request_id": request_id,
                "tenant_id": tenant_id,
                "student_id": "",
                "scene": "kb_chat",
                "query": query,
                "answer": answer,
                "citations": citations,
                "confidence": confidence,
                "latency_ms": latency_ms,
                "model": model,
                "retrieval": retrieval,
                "created_at": utcnow(),
            }
        )
    except Exception as exc:  # noqa: BLE001 - audit must not break answers
        log.warning("answer_record_failed", error=str(exc))


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


app = FastAPI(
    title="YXP Knowledge Base API",
    version="0.1.0",
    description="K12 学员侧检索问答 `/v1`，只读已发布索引与 MongoDB。",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or new_id("req")
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
            "request_id": _request_id(request),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": "INVALID_ARGUMENT",
            "message": "请求参数校验失败",
            "details": exc.errors(),
            "request_id": _request_id(request),
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
            "message": str(exc.detail),
            "details": {},
            "request_id": _request_id(request),
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    log.error("kb_unhandled_error", error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL",
            "message": "服务内部错误",
            "details": {},
            "request_id": _request_id(request),
        },
    )


router = APIRouter(prefix=kb_settings.kb_api_prefix)


@router.get("/health", response_model=HealthResponse)
async def health():
    milvus = await run_in_threadpool(_milvus_health)
    return HealthResponse(
        status="ok" if milvus["chunks"]["collection"] else "degraded",
        tenant=kb_settings.kb_default_tenant,
        chunks_alias=settings.chunks_alias,
        chunks_alias_collection=milvus["chunks"]["collection"],
        chunks=milvus["chunks"]["rows"],
        questions_alias=settings.questions_alias,
        questions=milvus["questions"]["rows"],
        embedding_provider=settings.embedding_provider,
        llm_configured=bool(settings.dashscope_api_key.strip()),
        mongo_ok=await mongo.ping(),
    )


@router.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_api_key)],
)
async def chat(body: ChatRequest, request: Request):
    started = time.perf_counter()
    request_id = _request_id(request)
    state = await run_query(_initial_state(body, request_id))

    latency_ms = int((time.perf_counter() - started) * 1000)
    citations = [Citation(**item) for item in state.get("citations") or []]
    retrieval = RetrievalStats(
        dense=len(state.get("dense_hits") or []),
        sparse=len(state.get("sparse_hits") or []),
        questions=len(state.get("question_dense_hits") or []),
        fused=len(state.get("fused_hits") or []),
        reranked=len(state.get("reranked_hits") or []),
    )
    await _record_answer(
        request_id=request_id,
        tenant_id=state["tenant_id"],
        query=state["rewritten_query"],
        answer=state.get("answer") or "",
        citations=[item.model_dump() for item in citations],
        confidence=state.get("confidence") or "low",
        model=state.get("model") or "",
        latency_ms=latency_ms,
        retrieval=retrieval.model_dump(),
    )
    return ChatResponse(
        request_id=request_id,
        answer=state.get("answer") or "",
        citations=citations,
        confidence=state.get("confidence") or "low",
        model=state.get("model") or "",
        rewritten_query=state.get("rewritten_query") or "",
        retrieval=retrieval,
        timings_ms=state.get("timings") or {},
        latency_ms=latency_ms,
    )


@router.post("/chat/stream", dependencies=[Depends(require_api_key)])
async def chat_stream(body: ChatRequest, request: Request):
    request_id = _request_id(request)
    state = await run_retrieval(_initial_state(body, request_id))
    hits = state.get("reranked_hits") or []
    citations = build_citations(hits)
    provider = str(state.get("rerank_provider") or "")
    confidence = confidence_for(hits, provider)
    retrieval = RetrievalStats(
        dense=len(state.get("dense_hits") or []),
        sparse=len(state.get("sparse_hits") or []),
        questions=len(state.get("question_dense_hits") or []),
        fused=len(state.get("fused_hits") or []),
        reranked=len(hits),
    )

    async def event_stream():
        started = time.perf_counter()
        answer_parts: list[str] = []
        model = settings.llm_default_model if hits else ""
        try:
            yield _sse(
                "meta",
                {
                    "request_id": request_id,
                    "scene": "kb_chat",
                    "rewritten_query": state["rewritten_query"],
                    "retrieval": retrieval.model_dump(),
                    "rerank_provider": provider,
                },
            )
            for citation in citations:
                yield _sse("citation", citation)

            if not hits:
                answer_parts.append(NO_EVIDENCE_ANSWER)
                yield _sse("delta", {"delta": NO_EVIDENCE_ANSWER})
            else:
                async for delta in stream_answer(
                    state["rewritten_query"],
                    context=build_context(hits),
                    history=history_messages(
                        state.get("messages") or [],
                        current_query=state["rewritten_query"],
                    ),
                    system_prompt=SYSTEM_PROMPT,
                ):
                    answer_parts.append(delta)
                    yield _sse("delta", {"delta": delta})

            latency_ms = int((time.perf_counter() - started) * 1000)
            yield _sse(
                "done",
                {
                    "request_id": request_id,
                    "latency_ms": latency_ms,
                    "model": model,
                    "confidence": confidence,
                    "retrieval": retrieval.model_dump(),
                },
            )
        except Exception as exc:  # noqa: BLE001 - report upstream failures over SSE
            log.warning("kb_stream_failed", error=str(exc))
            yield _sse(
                "error",
                {
                    "code": "MODEL_UNAVAILABLE",
                    "message": str(exc),
                    "retryable": True,
                    "request_id": request_id,
                },
            )
        finally:
            await _record_answer(
                request_id=request_id,
                tenant_id=state["tenant_id"],
                query=state["rewritten_query"],
                answer="".join(answer_parts),
                citations=citations,
                confidence=confidence,
                model=model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                retrieval=retrieval.model_dump(),
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


app.include_router(router)
