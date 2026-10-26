"""Environment-backed settings. See ``.env.example``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "yxp-admin-api"
    app_env: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    api_prefix: str = "/admin/v1"

    database_url: str = "sqlite+aiosqlite:///./data/admin.db"
    mongo_url: str = ""
    mongo_db: str = "yxp"
    mongo_timeout_ms: int = 3000

    jwt_secret: str = "dev-secret-change-me-0123456789abcdef0123456789abcdef"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    object_storage: str = "local"  # local | minio
    upload_dir: str = "./data/uploads"
    public_base_url: str = "http://127.0.0.1:8000"
    upload_url_ttl: int = 900
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "yxp"
    minio_secure: bool = False

    milvus_uri: str = ""
    milvus_chunks_collection: str = "chunks_live"
    milvus_questions_collection: str = "questions"
    chunks_alias: str = "chunks_active"
    questions_alias: str = "questions_active"

    task_mode: str = "inline"  # inline | celery
    redis_url: str = "redis://127.0.0.1:6379/0"
    pipeline_step_delay: float = 0.4

    seed_on_start: bool = True
    eval_recall_threshold: float = 0.85

    ocr_provider: str = "auto"  # auto | mineru | qwen_vl | placeholder
    mineru_token: str = ""
    mineru_access_key_id: str = ""
    mineru_secret_access_key: str = ""
    mineru_auth_url: str = "https://openapi.openxlab.org.cn/api/v1/sso-be/api/v1/open"
    mineru_base_url: str = "https://mineru.net/api/v4"
    mineru_model_version: str = "vlm"
    ocr_poll_seconds: int = 5
    ocr_timeout_seconds: int = 1800
    ocr_dpi: int = 200
    qwen_vl_concurrency: int = 4
    qwen_vl_max_tokens: int = 8192
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_vl_model: str = "qwen2.5-vl-7b-instruct"
    llm_default_model: str = "qwen-flash"
    llm_default_temperature: float = 0.1
    llm_structuring: bool = True
    llm_enable_thinking: bool = False
    llm_max_tokens: int = 8192
    llm_max_chars: int = 8000
    llm_concurrency: int = 4
    llm_timeout_seconds: int = 120
    rerank_provider: str = "auto"  # auto | qwen_llm | dashscope | off
    rerank_base_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = "qwen3-rerank"
    rerank_timeout_seconds: int = 60
    rerank_concurrency: int = 4
    rerank_max_questions: int = 50
    rerank_candidates: int = 6
    rerank_max_chars: int = 800
    rerank_min_score: float = 0.0
    rerank_top_evidence: int = 2
    embedding_provider: str = "off"  # off | bge_m3
    embedding_batch_size: int = 8
    embedding_max_length: int = 1024
    bge_m3_path: str = ""
    bge_m3: str = "BAAI/bge-m3"
    bge_device: str = "cpu"
    bge_fp16: bool = True
    chunk_size: int = 600
    chunk_overlap: int = 80

    max_upload_mb: int = 200
    page_size_max: int = 100

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def upload_path(self) -> Path:
        return Path(self.upload_dir).expanduser().resolve()

    @property
    def provider_configured(self) -> bool:
        has_mineru_auth = bool(
            self.mineru_token
            or (self.mineru_access_key_id and self.mineru_secret_access_key)
        )
        return bool(self.mineru_base_url and has_mineru_auth)

    def ensure_dirs(self) -> None:
        Path("data").mkdir(parents=True, exist_ok=True)
        if self.object_storage == "local":
            self.upload_path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
