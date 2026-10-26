from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    """向上找到包含 docs/rag_data 的项目根目录。"""
    p = (start or Path(__file__)).resolve()
    for cand in (p, *p.parents):
        if (cand / "docs" / "rag_data").is_dir():
            return cand
    raise RuntimeError("找不到项目根目录（需包含 docs/rag_data）")


ROOT = find_repo_root()
RAW_DIR = ROOT / "docs" / "rag_data" / "raw_data"
PROCESSED_DIR = ROOT / "docs" / "rag_data" / "processed"
REGISTRY_PATH = ROOT / "docs" / "rag_data" / "docs.json"
CACHE_DIR = ROOT / ".cache" / "rag_ingest"


def load_dotenv(path: Path | None = None) -> None:
    """极简 .env 读取，已有环境变量优先，不覆盖。"""
    path = path or ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_dotenv()


@dataclass(frozen=True)
class Settings:
    ocr_provider: str = os.getenv("OCR_PROVIDER", "mineru").strip().lower()
    mineru_token: str = os.getenv("MINERU_TOKEN", "").strip()
    mineru_access_key_id: str = os.getenv("MINERU_ACCESS_KEY_ID", "").strip()
    mineru_secret_access_key: str = os.getenv("MINERU_SECRET_ACCESS_KEY", "").strip()
    mineru_auth_url: str = os.getenv(
        "MINERU_AUTH_URL",
        "https://openapi.openxlab.org.cn/api/v1/sso-be/api/v1/open",
    ).rstrip("/")
    mineru_base_url: str = os.getenv("MINERU_BASE_URL", "https://mineru.net/api/v4").rstrip("/")
    dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "").strip()
    dashscope_base_url: str = os.getenv(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    qwen_vl_model: str = os.getenv("QWEN_VL_MODEL", "qwen2.5-vl-7b-instruct").strip()
    render_dpi: int = int(os.getenv("RENDER_DPI", "300"))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "600"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "80"))


settings = Settings()
