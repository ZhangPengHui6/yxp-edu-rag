from __future__ import annotations

import base64
import hmac
import io
import json
import shutil
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from ..config import settings
from ..registry import DocSpec
from .base import OcrError, OcrResult

CHINA_TZ = timezone(timedelta(hours=8))
TOKEN_EXPIRY_BUFFER = timedelta(seconds=60)


class MineruProvider:
    """MinerU v4 批量解析（model_version=vlm，公式和表格保留为 Markdown）。

    鉴权二选一：``MINERU_TOKEN`` 直接作为 Bearer；或 OpenXLab 的
    ``MINERU_ACCESS_KEY_ID`` + ``MINERU_SECRET_ACCESS_KEY``，经 auth →
    HmacSHA1 签名 → getJwt 换取 JWT，内存缓存并用 refresh_token 续期。
    """

    name = "mineru"

    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        self.token = (token or settings.mineru_token).strip()
        self.base_url = (base_url or settings.mineru_base_url).rstrip("/")
        self.auth_url = settings.mineru_auth_url.rstrip("/")
        self.access_key_id = settings.mineru_access_key_id
        self.secret_access_key = settings.mineru_secret_access_key
        self._jwt = ""
        self._jwt_expiration = ""
        self._refresh_token = ""
        self._refresh_expiration = ""
        if not self.token and not (self.access_key_id and self.secret_access_key):
            raise OcrError(
                "缺少 MinerU 凭据：请在项目根目录 .env 中配置 MINERU_TOKEN，"
                "或同时配置 MINERU_ACCESS_KEY_ID / MINERU_SECRET_ACCESS_KEY"
            )

    # ---------- public ----------

    def run(self, doc: DocSpec, force: bool = False) -> OcrResult:
        out_dir = doc.out_dir / "ocr"
        book_md = out_dir / "book.md"
        if book_md.is_file() and not force:
            return OcrResult(self.name, book_md, self._find_content_list(out_dir), out_dir / "images")

        out_dir.mkdir(parents=True, exist_ok=True)
        batch_id = self._submit(doc)
        zip_url = self._wait(batch_id)
        raw_dir = self._download(zip_url, out_dir)

        content_list = self._find_content_list(raw_dir)
        if content_list is None:
            raise OcrError(f"MinerU 结果中没有 content_list.json：{raw_dir}")

        data = json.loads(content_list.read_text(encoding="utf-8"))
        book_md.write_text(_content_list_to_markdown(data), encoding="utf-8")
        return OcrResult(self.name, book_md, content_list, raw_dir)

    # ---------- steps ----------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._authorization(), "Content-Type": "application/json"}

    # ---------- auth ----------

    def _authorization(self) -> str:
        if self.token:
            return self.token if self.token.lower().startswith("bearer ") else f"Bearer {self.token}"
        if self._jwt_valid():
            return self._jwt
        if self._can_refresh():
            try:
                self._refresh_jwt()
                return self._jwt
            except OcrError:
                pass
        self._login()
        return self._jwt

    def _jwt_valid(self) -> bool:
        if not self._jwt:
            return False
        return _parse_auth_time(self._jwt_expiration) > datetime.now(CHINA_TZ) + TOKEN_EXPIRY_BUFFER

    def _can_refresh(self) -> bool:
        if not self._refresh_token:
            return False
        return (
            _parse_auth_time(self._refresh_expiration)
            > datetime.now(CHINA_TZ) + TOKEN_EXPIRY_BUFFER
        )

    def _login(self) -> None:
        if not (self.access_key_id and self.secret_access_key):
            raise OcrError("缺少 MinerU Access Key / Secret Key")
        with httpx.Client(timeout=30) as client:
            auth = self._auth_post(client, "auth", {"ak": self.access_key_id})
            nonce = str(auth.get("nonce") or "")
            algorithm = str(auth.get("algorithm") or "HmacSHA1")
            if not nonce:
                raise OcrError("OpenXLab 鉴权失败：未返回 nonce")
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
                raise OcrError(f"OpenXLab 鉴权签名失败：{exc}") from exc
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
            raise OcrError(f"OpenXLab 鉴权请求失败（{action}）：{exc}") from exc
        return _auth_payload(body, action)

    def _store_token(self, data: dict[str, Any]) -> None:
        jwt = str(data.get("jwt") or "").strip()
        if not jwt:
            raise OcrError("OpenXLab 鉴权失败：未返回 jwt")
        self._jwt = jwt if jwt.lower().startswith("bearer ") else f"Bearer {jwt}"
        self._jwt_expiration = str(data.get("expiration") or "")
        self._refresh_token = str(data.get("refresh_token") or "")
        self._refresh_expiration = str(data.get("refresh_expiration") or "")

    def _submit(self, doc: DocSpec) -> str:
        payload = {
            "enable_formula": True,
            "enable_table": True,
            "language": "ch",
            "model_version": "vlm",
            "files": [{"name": doc.file, "is_ocr": True, "data_id": doc.doc_id}],
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{self.base_url}/file-urls/batch", headers=self._headers(), json=payload)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                raise OcrError(f"申请上传地址失败：{body}")
            data = body["data"]
            upload_url = data["file_urls"][0]
            with doc.path.open("rb") as fh:
                up = client.put(upload_url, content=fh)
                up.raise_for_status()
            return data["batch_id"]

    def _wait(self, batch_id: str, timeout: int = 1800) -> str:
        deadline = time.time() + timeout
        url = f"{self.base_url}/extract-results/batch/{batch_id}"
        with httpx.Client(timeout=60) as client:
            while True:
                resp = client.get(url, headers=self._headers())
                resp.raise_for_status()
                body = resp.json()
                if body.get("code") != 0:
                    raise OcrError(f"查询解析结果失败：{body}")
                results = (body.get("data") or {}).get("extract_result") or []
                if results:
                    item = results[0]
                    state = item.get("state")
                    if state == "done":
                        return item["full_zip_url"]
                    if state == "failed":
                        raise OcrError(f"MinerU 解析失败：{item.get('err_msg')}")
                if time.time() > deadline:
                    raise OcrError("MinerU 解析超时（30 分钟）")
                time.sleep(5)

    def _download(self, zip_url: str, out_dir: Path) -> Path:
        raw_dir = out_dir / "mineru_raw"
        if raw_dir.exists():
            shutil.rmtree(raw_dir)
        raw_dir.mkdir(parents=True)
        with httpx.Client(timeout=300, follow_redirects=True) as client:
            resp = client.get(zip_url)
            resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            _safe_extract(zf, raw_dir)
        return raw_dir

    @staticmethod
    def _find_content_list(root: Path) -> Path | None:
        hits = sorted(root.rglob("*content_list.json"))
        return hits[0] if hits else None


def _auth_payload(body: Any, action: str) -> dict[str, Any]:
    """Unwrap OpenXLab's nested ``{msgCode, msg, data}`` envelope."""
    if not isinstance(body, dict):
        raise OcrError(f"OpenXLab 鉴权返回格式异常（{action}）")
    code = body.get("msgCode")
    if code not in (None, "10000", 10000):
        raise OcrError(f"OpenXLab 鉴权失败（{action}）：{body.get('msg') or code}")
    payload = body.get("data")
    if isinstance(payload, dict) and "msgCode" in payload:
        inner_code = payload.get("msgCode")
        if inner_code not in ("10000", 10000):
            raise OcrError(
                f"OpenXLab 鉴权失败（{action}）：{payload.get('msg') or inner_code}"
            )
        payload = payload.get("data")
    if not isinstance(payload, dict) or not payload:
        raise OcrError(f"OpenXLab 鉴权返回缺少数据（{action}）")
    return payload


def _parse_auth_time(value: str) -> datetime:
    """OpenXLab returns Asia/Shanghai wall-clock strings (UTC+8, no DST)."""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA_TZ)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=CHINA_TZ)


def _safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    target = target.resolve()
    for member in zf.infolist():
        name = PurePosixPath(member.filename)
        if name.is_absolute() or ".." in name.parts:
            raise OcrError(f"压缩包包含非法路径：{member.filename}")
    zf.extractall(target)


PAGE_MARK = "<!-- page: {n} -->"


def _content_list_to_markdown(items: list[dict]) -> str:
    """把 MinerU content_list 转成带页码锚点的 Markdown。"""
    lines: list[str] = []
    current_page: int | None = None

    for item in items:
        page = int(item.get("page_idx", 0)) + 1
        if page != current_page:
            lines.append(PAGE_MARK.format(n=page))
            current_page = page

        kind = item.get("type")
        if kind == "text":
            text = (item.get("text") or "").strip()
            if not text:
                continue
            level = item.get("text_level") or 0
            if isinstance(level, int) and 1 <= level <= 6:
                lines.append(f"{'#' * level} {text}")
            else:
                lines.append(text)
        elif kind == "equation":
            text = (item.get("text") or "").strip()
            if text:
                lines.append(text if text.startswith("$$") else f"$$\n{text}\n$$")
        elif kind == "table":
            body = (item.get("table_body") or "").strip()
            if body:
                lines.append(body)
        elif kind == "image":
            path = item.get("img_path") or ""
            caption = item.get("img_caption") or []
            caption = " ".join(caption) if isinstance(caption, list) else str(caption)
            if path:
                lines.append(f"![{caption}]({path})")
            elif caption:
                lines.append(f"<!-- image: {caption} -->")

    return "\n\n".join(lines) + "\n"
