"""入口：python tools/rag_ingest/run.py <command>"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag_ingest.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
