# rag_ingest — 高中知识手册入库流水线

把 `docs/rag_data/raw_data` 里的两份 PDF 转成可检索的 chunk。离线工具，后续并入
`apps/admin-api/app/workers/ocr_tasks.py` 与 `index_tasks.py`。

## 依赖

Python 3.12，已装：`pymupdf`、`numpy`、`httpx`。无需 Docker、无需本地 GPU。

## 配置

```powershell
Copy-Item .env.example .env
```

然后二选一填写：

| Provider | 需要的变量 | 说明 |
|---|---|---|
| `mineru`（默认） | `MINERU_TOKEN`，或 `MINERU_ACCESS_KEY_ID` + `MINERU_SECRET_ACCESS_KEY` | 整份 PDF 上传，返回版面结构 + 公式/表格，页码可回溯，推荐。二选一：直接给 token，或用 OpenXLab AK/SK 自动换 JWT（`MINERU_AUTH_URL` 一般用默认值）；`status` 会显示当前用哪种鉴权 |
| `qwen_vl` | `DASHSCOPE_API_KEY` | 逐页调用 Qwen2.5-VL-7B，按 PRD 选型；无版面结构，但链路最接近生产 |

## 命令

```powershell
python tools/rag_ingest/run.py status                 # 查看配置与入库进度
python tools/rag_ingest/run.py render --doc math_hs   # 渲染页面 + 空白页检测
python tools/rag_ingest/run.py ocr --doc math_hs      # VLM 解析（需 token/key）
python tools/rag_ingest/run.py chunk --doc math_hs    # 切分为 chunk
python tools/rag_ingest/run.py pipeline --doc math_hs # 三步串联
python tools/rag_ingest/run.py status                 # 复核
```

`--doc` 可重复；不传则处理注册表内全部文档。

## 产物

```
docs/rag_data/processed/<doc_id>/
├─ pages.jsonl        # 每页：尺寸、文本层字符数、非白像素比、是否空白
├─ ocr/book.md        # 带 <!-- page: N --> 锚点的 Markdown + LaTeX
├─ ocr/content_list.json  # MinerU 结构化块（仅 mineru provider）
└─ chunks.jsonl       # 检索单元
```

页面 PNG 属于可再生的中间产物，放在 `.cache/rag_ingest/<doc_id>/`，不进 `docs`。

## chunk 结构

```json
{
  "chunk_id": "math_hs:0000",
  "doc_id": "math_hs",
  "doc_title": "高中数学知识手册",
  "doc_type": "knowledge_handbook",
  "subject": "math",
  "stage": "high_school",
  "chapter_no": 1,
  "chapter": "集合与常用逻辑用语",
  "section": "一、集合的含义与表示",
  "heading_path": "第1章 集合与常用逻辑用语 / 一、集合的含义与表示",
  "page_start": 21,
  "page_end": 21,
  "printed_page_start": 18,
  "source_file": "学而思-希望学-高中数学知识手册.pdf",
  "char_count": 54,
  "text": "正文……",
  "embed_text": "标题路径 + 正文，用于向量化"
}
```

- `page_start/page_end` 是 PDF 页，可直接回链到 `.cache` 里的页图做溯源。
- `embed_text` 带标题路径，避免「性质」「定义」这类独立段落失去上下文。
- `stage=high_school` 是硬过滤条件：PRD 正式库是初中，检索时必须按学段隔离。

## 语义约定

- 空白页在 `render` 阶段标记，`ocr` 阶段跳过。
- 章节以 `docs/rag_data/docs.json` 为准，不依赖 OCR 出来的标题（识别可能抖）。
- 切分保持公式块、表格、代码块完整，不对块内做字符级截断。
- 同一节过长时按段落分组，块级重叠，不做字符重叠，避免公式被切断。

## 下一步

1. 填 token/key，跑 `pipeline`。
2. 抽检 5–10 页：公式 LaTeX 能否渲染、表格是否错位。
3. `chunks.jsonl` → BGE-M3 向量 → Milvus（`chunks_active` 集合）。
4. 检索侧加 `stage=high_school` + `subject` 过滤。
