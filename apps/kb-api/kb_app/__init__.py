"""Student-facing knowledge-base service (``/v1``).

The admin API lives in ``apps/admin-api`` and owns ingestion/publish. This
service only reads the published Milvus aliases and MongoDB, so it is deployed
as its own process.

For local development both apps share one virtualenv, so make the sibling
``admin-api`` package importable before importing ``app.*``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ADMIN_API = Path(__file__).resolve().parents[2] / "admin-api"
if _ADMIN_API.is_dir() and str(_ADMIN_API) not in sys.path:
    sys.path.insert(0, str(_ADMIN_API))
