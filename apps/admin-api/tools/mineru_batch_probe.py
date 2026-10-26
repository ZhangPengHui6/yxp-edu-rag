"""Diagnostic: query MinerU batch state for the two handbooks uploaded today.

Read-only; prints only batch codes/states, never credentials.
"""

from __future__ import annotations

import json

import httpx

from app.infra.providers.mineru import MineruOcrProvider

BATCHES = (
    "2bd33065-c142-4ad1-a337-04a74fe7feaa",
    "f660f112-c38b-4599-abaa-5a1ab6e55585",
)


def main() -> None:
    provider = MineruOcrProvider()
    for batch_id in BATCHES:
        with httpx.Client(timeout=60) as client:
            try:
                response = client.get(
                    f"{provider.base_url}/extract-results/batch/{batch_id}",
                    headers=provider._headers(),
                )
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                print(batch_id, "probe failed:", type(exc).__name__)
                continue
        data = body.get("data") or {}
        results = data.get("extract_result") or []
        summary = [
            {
                "state": item.get("state"),
                "err_msg": item.get("err_msg"),
                "full_zip_url": bool(item.get("full_zip_url")),
            }
            for item in results
        ]
        print(batch_id, "code=", body.get("code"), "msg=", body.get("msg"))
        print("   ", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
