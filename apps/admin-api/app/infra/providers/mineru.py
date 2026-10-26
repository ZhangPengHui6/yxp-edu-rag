"""MinerU v4 cloud OCR provider.

The admin API hands the uploaded PDF to MinerU (``model_version=vlm``), waits for
the batch result and turns ``content_list.json`` into page-anchored Markdown plus
typed blocks. Network calls are synchronous and executed in a worker thread.

Authentication supports two forms:

* ``MINERU_TOKEN`` — a MinerU API token used directly as ``Authorization: Bearer``;
* ``MINERU_ACCESS_KEY_ID`` + ``MINERU_SECRET_ACCESS_KEY`` — OpenXLab credentials
  exchanged for a short-lived JWT (auth -> HMAC-SHA1 nonce -> getJwt). The JWT is
  cached in memory and refreshed with the refresh token; no token is written to
  disk.
"""

from __future__ import annotations

import base64
import hmac
import io
import json
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.infra.providers import OcrBlock, OcrDocument

PAGE_MARK = "<!-- page: {n} -->"
CHINA_TZ = timezone(timedelta(hours=8))
TOKEN_EXPIRY_BUFFER = timedelta(seconds=60)
# The batch result endpoint briefly answers `task not found or expire` while the
# batch is being registered; that is transient, not a failed extraction.
RESULT_NOT_FOUND_CODE = -60012
RESULT_NOT_FOUND_GRACE = 180.0

_TYPE_MAP = {
    "text": "正文",
    "equation": "公式",
    "table": "表格",
    "image": "图注",
}


class MineruOcrProvider:
    name = "mineru"

    def __init__(self) -> None:
        self.token = settings.mineru_token.strip()
        self.base_url = settings.mineru_base_url.rstrip("/")
        self.auth_url = settings.mineru_auth_url.rstrip("/")
        self.access_key_id = settings.mineru_access_key_id.strip()
        self.secret_access_key = settings.mineru_secret_access_key.strip()
        self._lock = threading.Lock()
        self._jwt = ""
        self._jwt_expiration = ""
        self._refresh_token = ""
        self._refresh_expiration = ""
        if not self.token and not (self.access_key_id and self.secret_access_key):
            raise ProviderUnavailable(
                "MinerU 未配置：请设置 MINERU_TOKEN，或同时设置 "
                "MINERU_ACCESS_KEY_ID / MINERU_SECRET_ACCESS_KEY",
                details={"provider": "mineru"},
            )

    async def parse(self, *, content: bytes, filename: str) -> OcrDocument:
        return await run_in_threadpool(self._parse_sync, content, filename)

    # ---------- synchronous pipeline ----------

    def _parse_sync(self, content: bytes, filename: str) -> OcrDocument:
        try:
            batch_id = self._submit(content, filename)
            zip_url = self._wait(batch_id)
            with tempfile.TemporaryDirectory(prefix="mineru_") as tmp:
                root = self._download(zip_url, Path(tmp))
                content_list = self._find_content_list(root)
                if content_list is None:
                    raise ProviderUnavailable("MinerU 结果中没有 content_list.json")
                items = json.loads(content_list.read_text(encoding="utf-8"))
        except ProviderUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"MinerU 请求失败：{exc}") from exc
        except zipfile.BadZipFile as exc:
            raise ProviderUnavailable("MinerU 返回的压缩包无效") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderUnavailable("MinerU content_list.json 解析失败") from exc

        blocks, markdown, page_count = _content_list_to_document(items)
        return OcrDocument(
            markdown=markdown,
            page_count=page_count,
            blocks=blocks,
            raw={"provider": "mineru", "batchId": batch_id, "filename": filename},
        )

    # ---------- auth ----------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._authorization(), "Content-Type": "application/json"}

    def _authorization(self) -> str:
        if self.token:
            return self.token if self.token.lower().startswith("bearer ") else f"Bearer {self.token}"
        with self._lock:
            if self._jwt_valid():
                return self._jwt
            if self._can_refresh():
                try:
                    self._refresh_jwt()
                    return self._jwt
                except ProviderUnavailable:
                    pass
            self._login()
            return self._jwt

    def _jwt_valid(self) -> bool:
        if not self._jwt:
            return False
        expires = _parse_auth_time(self._jwt_expiration)
        return expires > datetime.now(CHINA_TZ) + TOKEN_EXPIRY_BUFFER

    def _can_refresh(self) -> bool:
        if not self._refresh_token:
            return False
        expires = _parse_auth_time(self._refresh_expiration)
        return expires > datetime.now(CHINA_TZ) + TOKEN_EXPIRY_BUFFER

    def _login(self) -> None:
        if not (self.access_key_id and self.secret_access_key):
            raise ProviderUnavailable("MinerU Access Key / Secret Key 未配置")
        with httpx.Client(timeout=30) as client:
            auth = self._auth_post(client, "auth", {"ak": self.access_key_id})
            nonce = str(auth.get("nonce") or "")
            algorithm = str(auth.get("algorithm") or "HmacSHA1")
            if not nonce:
                raise ProviderUnavailable("OpenXLab 鉴权失败：未返回 nonce")
            digest = algorithm[4:] if algorithm.startswith("Hmac") else algorithm
            try:
                signature = base64.b64encode(
                    hmac.new(
                        self.secret_access_key.encode("utf-8"),
                        nonce.encode("utf-8"),
                        digest,
                    ).digest()
                ).decode("utf-8")
            except (ValueError, TypeError) as exc:
                raise ProviderUnavailable(f"OpenXLab 鉴权签名失败：{exc}") from exc
            token = self._auth_post(
                client, "getJwt", {"ak": self.access_key_id, "d": signature}
            )
        self._store_token(token)

    def _refresh_jwt(self) -> None:
        with httpx.Client(timeout=30) as client:
            token = self._auth_post(
                client,
                "refreshJwt",
                {"ak": self.access_key_id, "refresh_token": self._refresh_token},
            )
        self._store_token(token)

    def _auth_post(
        self, client: httpx.Client, action: str, payload: dict[str, str]
    ) -> dict[str, Any]:
        try:
            response = client.post(f"{self.auth_url}/{action}", json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable(f"OpenXLab 鉴权请求失败（{action}）：{exc}") from exc
        return _auth_payload(body, action)

    def _store_token(self, data: dict[str, Any]) -> None:
        jwt = str(data.get("jwt") or "").strip()
        if not jwt:
            raise ProviderUnavailable("OpenXLab 鉴权失败：未返回 jwt")
        self._jwt = jwt if jwt.lower().startswith("bearer ") else f"Bearer {jwt}"
        self._jwt_expiration = str(data.get("expiration") or "")
        self._refresh_token = str(data.get("refresh_token") or "")
        self._refresh_expiration = str(data.get("refresh_expiration") or "")

    def _submit(self, content: bytes, filename: str) -> str:
        payload = {
            "enable_formula": True,
            "enable_table": True,
            "language": "ch",
            "model_version": settings.mineru_model_version,
            "files": [{"name": filename, "is_ocr": True, "data_id": filename}],
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.base_url}/file-urls/batch", headers=self._headers(), json=payload
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                raise ProviderUnavailable(f"MinerU 申请上传地址失败：{body}")
            data = body["data"]
            upload_url = data["file_urls"][0]
            suffix = PurePosixPath(filename).suffix.lower()
            content_type = {
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }.get(suffix, "application/octet-stream")
            up = client.put(
                upload_url,
                content=content,
                headers={"Content-Type": content_type},
            )
            up.raise_for_status()
            return str(data["batch_id"])

    def _wait(self, batch_id: str) -> str:
        deadline = time.time() + settings.ocr_timeout_seconds
        url = f"{self.base_url}/extract-results/batch/{batch_id}"
        not_found_since: float | None = None
        with httpx.Client(timeout=60) as client:
            while True:
                try:
                    resp = client.get(url, headers=self._headers())
                    resp.raise_for_status()
                    body = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    if time.time() > deadline:
                        raise ProviderUnavailable(f"MinerU 查询解析结果失败：{exc}") from exc
                    time.sleep(settings.ocr_poll_seconds)
                    continue

                code = body.get("code")
                if code == RESULT_NOT_FOUND_CODE:
                    if not_found_since is None:
                        not_found_since = time.time()
                    if time.time() - not_found_since > RESULT_NOT_FOUND_GRACE:
                        raise ProviderUnavailable(f"MinerU 查询解析结果失败：{body}")
                elif code != 0:
                    raise ProviderUnavailable(f"MinerU 查询解析结果失败：{body}")
                else:
                    not_found_since = None
                    results = (body.get("data") or {}).get("extract_result") or []
                    if results:
                        item = results[0]
                        state = item.get("state")
                        if state == "done":
                            return str(item["full_zip_url"])
                        if state == "failed":
                            raise ProviderUnavailable(f"MinerU 解析失败：{item.get('err_msg')}")
                if time.time() > deadline:
                    raise ProviderUnavailable(
                        f"MinerU 解析超时（{settings.ocr_timeout_seconds} 秒）"
                    )
                time.sleep(settings.ocr_poll_seconds)

    @staticmethod
    def _download(zip_url: str, target: Path) -> Path:
        with httpx.Client(timeout=300, follow_redirects=True) as client:
            resp = client.get(zip_url)
            resp.raise_for_status()
        root = target / "mineru_raw"
        root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            _safe_extract(zf, root)
        return root

    @staticmethod
    def _find_content_list(root: Path) -> Path | None:
        hits = sorted(root.rglob("*content_list.json"))
        return hits[0] if hits else None


def _auth_payload(body: Any, action: str) -> dict[str, Any]:
    """Unwrap OpenXLab's nested ``{msgCode, msg, data}`` envelope."""
    if not isinstance(body, dict):
        raise ProviderUnavailable(f"OpenXLab 鉴权返回格式异常（{action}）")
    code = body.get("msgCode")
    if code not in (None, "10000", 10000):
        raise ProviderUnavailable(
            f"OpenXLab 鉴权失败（{action}）：{body.get('msg') or code}"
        )
    payload = body.get("data")
    if isinstance(payload, dict) and "msgCode" in payload:
        inner_code = payload.get("msgCode")
        if inner_code not in ("10000", 10000):
            raise ProviderUnavailable(
                f"OpenXLab 鉴权失败（{action}）：{payload.get('msg') or inner_code}"
            )
        payload = payload.get("data")
    if not isinstance(payload, dict) or not payload:
        raise ProviderUnavailable(f"OpenXLab 鉴权返回缺少数据（{action}）")
    return payload


def _parse_auth_time(value: str) -> datetime:
    """OpenXLab returns Asia/Shanghai wall-clock strings (UTC+8, no DST)."""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA_TZ)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=CHINA_TZ)


def _safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    resolved = target.resolve()
    for member in zf.infolist():
        name = PurePosixPath(member.filename)
        if name.is_absolute() or ".." in name.parts:
            raise ProviderUnavailable(f"MinerU 压缩包包含非法路径：{member.filename}")
    zf.extractall(resolved)


def _content_list_to_document(items: list[dict[str, Any]]) -> tuple[list[OcrBlock], str, int]:
    """Convert MinerU content_list into blocks, Markdown and page count."""
    blocks: list[OcrBlock] = []
    lines: list[str] = []
    current_page: int | None = None
    max_page = 0

    for index, item in enumerate(items):
        page = int(item.get("page_idx", 0)) + 1
        max_page = max(max_page, page)
        if page != current_page:
            lines.append(PAGE_MARK.format(n=page))
            current_page = page

        kind = item.get("type")
        text = (item.get("text") or "").strip()
        if kind == "text":
            if not text:
                continue
            level = item.get("text_level") or 0
            if isinstance(level, int) and 1 <= level <= 6:
                lines.append(f"{'#' * level} {text}")
                block_type = "标题"
            else:
                lines.append(text)
                block_type = "正文"
            blocks.append(OcrBlock(index=index, type=block_type, content=text, confidence=1.0, page=page))
        elif kind == "equation":
            if not text:
                continue
            lines.append(text if text.startswith("$$") else f"$$\n{text}\n$$")
            blocks.append(OcrBlock(index=index, type="公式", content=text, confidence=1.0, page=page))
        elif kind == "table":
            body = (item.get("table_body") or "").strip()
            if not body:
                continue
            lines.append(body)
            blocks.append(OcrBlock(index=index, type="表格", content=body, confidence=1.0, page=page))
        elif kind == "image":
            caption = item.get("img_caption") or []
            caption_text = " ".join(caption) if isinstance(caption, list) else str(caption)
            path = item.get("img_path") or ""
            if path:
                lines.append(f"![{caption_text}]({path})")
            elif caption_text:
                lines.append(f"<!-- image: {caption_text} -->")
            if caption_text:
                blocks.append(
                    OcrBlock(
                        index=index,
                        type="图注",
                        content=caption_text,
                        confidence=1.0,
                        page=page,
                    )
                )

    return blocks, "\n\n".join(lines) + "\n", max_page
