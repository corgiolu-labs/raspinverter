"""Path backend + (opzionale) DB SQLite isolato per la suite unittest.

Senza `RASPINVERTER_USE_PRODUCTION_DB=1`, imposta `INVERTER_DB_PATH` su un file
temporaneo così i test non leggono/scrivono `data/inverter_history.db` del repo.
"""
from __future__ import annotations

import atexit
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BACKEND = _REPO / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_use_prod_db = os.getenv("RASPINVERTER_USE_PRODUCTION_DB", "").lower() in ("1", "true", "yes")
if not _use_prod_db and "INVERTER_DB_PATH" not in os.environ:
    _fd, _TEST_DB = tempfile.mkstemp(prefix="raspinv_test_", suffix=".db")
    os.close(_fd)
    os.environ["INVERTER_DB_PATH"] = _TEST_DB

    def _cleanup_test_db() -> None:
        for suffix in ("", "-wal", "-shm"):
            p = _TEST_DB + suffix
            try:
                os.remove(p)
            except OSError:
                pass

    atexit.register(_cleanup_test_db)
