# -*- coding: utf-8 -*-
"""Aggiunta idempotente di `src/` al sys.path (modulo `daily_analyzer`).

L'entrypoint deve prima assicurare che la directory `backend/` sia in sys.path
(vedi `inverter_api.py`); poi questo modulo centralizza solo il path verso `src/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
SRC_DIR = REPO_ROOT / "src"


def ensure_src_path() -> None:
    s = str(SRC_DIR)
    if s not in sys.path:
        sys.path.insert(0, s)
