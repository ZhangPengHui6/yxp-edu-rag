"""FastAPI application factory and lifecycle."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.context import client_ip_ctx, request_id_ctx, user_agent_ctx
from app.core.envelope import fail, ok
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.infra import mongo, pg
from app.shared.ids import new_id

configure_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.ensure_dirs()
    await pg.init_models()
    await mongo.init_indexes()
    from app.seed import bootstrap_vector_aliases, seed_all

    if settings.seed_on_start:
        await seed_all()
    await bootstrap_vector_aliases()
    log.info(
        "api_started",
        env=settings.app_env,
        db=settings.database_url.split("@")[-1],
        mongo="external" if settings.mongo_url else "mongomock",
        task_mode=settings.task_mode,
    )
    yield
    await mongo.close()
    await pg.dispose_engine()
    log.info("api_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="YXP Admin API",
        version="0.1.0",
        description="K12 知识库管理平台后端；管理端契约前缀 /admin/v1，学员知识库 /v1 为独立服务。",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=None,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or new_id("req")
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = (
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else "-")
        )
        token_id = request_id_ctx.set(request_id)
        token_ip = client_ip_ctx.set(ip)
        token_ua = user_agent_ctx.set(request.headers.get("user-agent", "-"))
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token_id)
            client_ip_ctx.reset(token_ip)
            user_agent_ctx.reset(token_ua)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.http_status,
            content=fail(
                code=exc.code,
                message=exc.message,
                error_code=exc.error_code,
                details=exc.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content=fail(
                code=40001,
                message="请求参数校验失败",
                error_code="INVALID_ARGUMENT",
                details={"errors": exc.errors()},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException):
        mapping = {
            401: (40101, "UNAUTHORIZED", "登录已失效"),
            403: (40301, "FORBIDDEN", "没有操作权限"),
            404: (40401, "NOT_FOUND", "接口不存在"),
            405: (40501, "METHOD_NOT_ALLOWED", "请求方法不允许"),
        }
        code, error_code, message = mapping.get(
            exc.status_code, (50001, "INTERNAL_ERROR", str(exc.detail))
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=fail(code=code, message=message, error_code=error_code),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception):
        log.exception("unhandled_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=fail(
                code=50001,
                message="服务内部错误，请稍后重试",
                error_code="INTERNAL_ERROR",
            ),
        )

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health", include_in_schema=False)
    async def root_health():
        return ok({"status": "ok"})

    return app


app = create_app()


def run() -> None:
    import uvicorn

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=not settings.is_production,
    )


if __name__ == "__main__":
    run()
