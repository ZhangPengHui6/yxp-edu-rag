from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pymupdf

from .config import CACHE_DIR, settings
from .registry import DocSpec

BLANK_RATIO = 0.003


@dataclass
class PageInfo:
    page_no: int
    width: float
    height: float
    text_chars: int
    nonwhite_ratio: float
    is_blank: bool
    image: str | None = None


def pages_path(doc: DocSpec) -> Path:
    return doc.out_dir / "pages.jsonl"


def load_pages(doc: DocSpec) -> list[PageInfo]:
    path = pages_path(doc)
    if not path.is_file():
        return []
    return [PageInfo(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_pages(doc: DocSpec, pages: list[PageInfo]) -> None:
    doc.out_dir.mkdir(parents=True, exist_ok=True)
    with pages_path(doc).open("w", encoding="utf-8") as f:
        for p in pages:
            f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")


def _nonwhite_ratio(page: pymupdf.Page) -> float:
    pix = page.get_pixmap(dpi=72, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    gray = arr[..., :3].mean(axis=2) if arr.shape[2] >= 3 else arr[..., 0]
    return float((gray < 240).mean())


def render_doc(doc: DocSpec, dpi: int | None = None, force: bool = False) -> list[PageInfo]:
    dpi = dpi or settings.render_dpi
    img_dir = CACHE_DIR / doc.doc_id / "pages"
    img_dir.mkdir(parents=True, exist_ok=True)

    pages: list[PageInfo] = []
    with pymupdf.open(doc.path) as pdf:
        for i, page in enumerate(pdf):
            no = i + 1
            ratio = _nonwhite_ratio(page)
            img = img_dir / f"p{no:03d}.png"
            if force or not img.exists():
                page.get_pixmap(dpi=dpi).save(img)
            pages.append(
                PageInfo(
                    page_no=no,
                    width=round(page.rect.width, 1),
                    height=round(page.rect.height, 1),
                    text_chars=len(page.get_text().strip()),
                    nonwhite_ratio=round(ratio, 5),
                    is_blank=ratio < BLANK_RATIO,
                    image=str(img),
                )
            )
    write_pages(doc, pages)
    return pages
