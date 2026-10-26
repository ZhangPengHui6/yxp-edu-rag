"""Settings specific to the student-facing service.

Shared infrastructure settings (Milvus URI, Mongo URI, model credentials,
BGE-M3 path) stay in ``app.core.config`` so both services read the same env.
Only retrieval/generation knobs are defined here with a ``KB_`` prefix.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class KbSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    kb_api_prefix: str = "/v1"
    # Empty means "no key configured": allowed only for local development and a
    # warning is logged at startup.
    kb_api_key: str = ""
    kb_default_tenant: str = "tnt_yxp"

    kb_top_k: int = 20
    kb_rrf_k: int = 60
    # Question channels are fused with the same weight as chunks, but the final
    # candidate window reserves a small quota so questions cannot be crowded out
    # by the much larger textbook corpus.
    kb_question_weight: float = 1.0
    kb_question_quota: int = 3
    kb_fused_top_n: int = 10
    kb_rerank_top_n: int = 5
    kb_max_question_chars: int = 2000
    kb_max_evidence_chars: int = 1200
    kb_max_context_chars: int = 6000
    kb_history_turns: int = 4
    kb_answer_max_tokens: int = 2048
    kb_record_answers: bool = True

    @property
    def api_key_required(self) -> bool:
        return bool(self.kb_api_key.strip())


@lru_cache(maxsize=1)
def get_kb_settings() -> KbSettings:
    return KbSettings()
