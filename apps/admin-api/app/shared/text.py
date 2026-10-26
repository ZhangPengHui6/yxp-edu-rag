"""Text normalisation used by dedupe and search."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_SPACES = re.compile(r"\s+")
_PUNCT = re.compile(r"[\s，。；：！？、,.!?;:'\"“”‘’（）()\[\]【】<>《》·—\-_/\\]+")


def normalize_stem(text: str) -> str:
    """Normalise a question stem for exact/near-duplicate comparison.

    Keeps LaTeX letters/digits, drops punctuation and whitespace, applies NFKC.
    """
    value = unicodedata.normalize("NFKC", text or "").lower()
    value = value.replace("　", " ")
    value = _PUNCT.sub("", value)
    return _SPACES.sub("", value)


def simhash64(text: str) -> str:
    """A deterministic 64-bit SimHash rendered as 16 hex chars.

    Tokenisation is character bigrams, which works acceptably for Chinese stems.
    Production should use the embedding-based dedupe worker; this is the POC
    pre-filter described in 数据对象 §questions.
    """
    normalized = normalize_stem(text)
    if not normalized:
        return "0" * 16
    tokens = (
        [normalized[i : i + 2] for i in range(len(normalized) - 1)]
        if len(normalized) > 1
        else [normalized]
    )
    buckets = [0] * 64
    for token in tokens:
        digest = int.from_bytes(
            hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big"
        )
        for bit in range(64):
            buckets[bit] += 1 if (digest >> bit) & 1 else -1
    fingerprint = 0
    for bit, weight in enumerate(buckets):
        if weight > 0:
            fingerprint |= 1 << bit
    return f"{fingerprint:016x}"


def hamming64(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return 64


def short_excerpt(text: str, limit: int = 120) -> str:
    text = _SPACES.sub(" ", (text or "").strip())
    return text if len(text) <= limit else f"{text[:limit]}…"
