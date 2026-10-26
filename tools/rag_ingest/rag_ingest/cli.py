from __future__ import annotations

import argparse
import sys

from .chunker import build_chunks, write_chunks
from .config import RAW_DIR, settings
from .ocr import get_provider
from .pdf_render import load_pages, render_doc
from .registry import DocSpec, load_docs


def _select(args) -> list[DocSpec]:
    docs = load_docs()
    if args.doc:
        wanted = set(args.doc)
        docs = [d for d in docs if d.doc_id in wanted]
        missing = wanted - {d.doc_id for d in docs}
        if missing:
            raise SystemExit(f"未知 doc_id：{', '.join(sorted(missing))}")
    if not docs:
        raise SystemExit("没有匹配的文档，请用 --doc 指定")
    return docs


def cmd_render(args) -> None:
    for doc in _select(args):
        pages = render_doc(doc, dpi=args.dpi, force=args.force)
        blank = sum(1 for p in pages if p.is_blank)
        print(f"[render] {doc.doc_id}: {len(pages)} 页，空白 {blank} 页 -> {doc.out_dir / 'pages.jsonl'}")


def cmd_ocr(args) -> None:
    provider = get_provider(args.provider or settings.ocr_provider)
    for doc in _select(args):
        result = provider.run(doc, force=args.force)
        print(f"[ocr:{result.provider}] {doc.doc_id} -> {result.book_md}")


def cmd_chunk(args) -> None:
    for doc in _select(args):
        book = doc.out_dir / "ocr" / "book.md"
        if not book.is_file():
            raise SystemExit(f"缺少 OCR 结果：{book}，请先执行 ocr")
        chunks = build_chunks(doc, book.read_text(encoding="utf-8"))
        path = write_chunks(doc, chunks)
        print(f"[chunk] {doc.doc_id}: {len(chunks)} chunks -> {path}")


def cmd_pipeline(args) -> None:
    cmd_render(args)
    cmd_ocr(args)
    cmd_chunk(args)


def cmd_status(args) -> None:
    print(f"项目根目录 : {RAW_DIR.parents[2]}")
    print(f"OCR provider: {settings.ocr_provider}")
    if settings.mineru_token:
        mineru_auth = "MINERU_TOKEN"
    elif settings.mineru_access_key_id and settings.mineru_secret_access_key:
        mineru_auth = "OpenXLab AK/SK"
    else:
        mineru_auth = "未配置"
    print(f"MINERU 鉴权  : {mineru_auth}")
    print(f"DASHSCOPE_API_KEY: {'已配置' if settings.dashscope_api_key else '未配置'}")
    print()
    for doc in load_docs():
        pages = load_pages(doc)
        blank = sum(1 for p in pages if p.is_blank)
        book = doc.out_dir / "ocr" / "book.md"
        chunks = doc.out_dir / "chunks.jsonl"
        chunk_n = sum(1 for _ in chunks.open(encoding="utf-8")) if chunks.is_file() else 0
        print(f"{doc.doc_id}  ({doc.subject}, {doc.stage})")
        print(f"  原始文件 : {doc.path.name}  {'OK' if doc.path.is_file() else '缺失'}")
        print(f"  页面清单 : {len(pages)} 页，空白 {blank} 页")
        print(f"  OCR      : {'已完成 %.1f KB' % (book.stat().st_size / 1024) if book.is_file() else '未完成'}")
        print(f"  chunks   : {chunk_n}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag_ingest", description="高中知识手册 RAG 入库")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--doc", action="append", default=[], help="doc_id，可重复")
        p.add_argument("--force", action="store_true", help="忽略已有结果重新执行")

    p = sub.add_parser("render", help="渲染页面并生成 pages.jsonl")
    add_common(p)
    p.add_argument("--dpi", type=int, default=None)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("ocr", help="调用 VLM 解析 PDF")
    add_common(p)
    p.add_argument("--provider", choices=["mineru", "qwen_vl"], default=None)
    p.set_defaults(func=cmd_ocr)

    p = sub.add_parser("chunk", help="切分为检索 chunk")
    add_common(p)
    p.set_defaults(func=cmd_chunk)

    p = sub.add_parser("pipeline", help="render -> ocr -> chunk")
    add_common(p)
    p.add_argument("--dpi", type=int, default=None)
    p.add_argument("--provider", choices=["mineru", "qwen_vl"], default=None)
    p.set_defaults(func=cmd_pipeline)

    p = sub.add_parser("status", help="查看入库状态")
    p.set_defaults(func=cmd_status, doc=[], force=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
