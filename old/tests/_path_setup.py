"""Path backend + isolamento DB e config per la suite unittest.

- Senza `RASPINVERTER_USE_PRODUCTION_DB=1`: `INVERTER_DB_PATH` → file SQLite temporaneo.
- Senza `RASPINVERTER_USE_PRODUCTION_CONFIG=1`: `INVERTER_CONFIG_PATH` → JSON temporaneo minimale (`{}`),
  così non si legge/scrive `config/inverter_config.json` del repo.
"""
from __future__ import annotations

import atexit
import json
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

_use_prod_cfg = os.getenv("RASPINVERTER_USE_PRODUCTION_CONFIG", "").lower() in ("1", "true", "yes")
if not _use_prod_cfg and "INVERTER_CONFIG_PATH" not in os.environ:
    _cfg_fd, _TEST_CFG = tempfile.mkstemp(prefix="raspinv_test_", suffix=".json")
    os.close(_cfg_fd)
    with open(_TEST_CFG, "w", encoding="utf-8") as f:
        json.dump({}, f)
    os.environ["INVERTER_CONFIG_PATH"] = _TEST_CFG

    def _cleanup_test_cfg() -> None:
        try:
            os.remove(_TEST_CFG)
        except OSError:
            pass

    atexit.register(_cleanup_test_cfg)
