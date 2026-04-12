# -*- coding: utf-8 -*-
"""Helper per test Flask e dati SQLite di esempio."""
from __future__ import annotations

from typing import Any, Optional

import tests._path_setup  # noqa: F401 — imposta INVERTER_DB_PATH prima degli import backend

_client: Any = None


def get_test_client():
    """Test client Flask singleton (db_init una volta)."""
    global _client
    if _client is None:
        from app import create_app
        from db import db_init

        db_init()
        _client = create_app().test_client()
    return _client


def clear_samples_and_counters() -> None:
    """Svuota campioni e contatori (stesso file DB temporaneo della suite)."""
    from db import db

    with db() as con:
        con.execute("DELETE FROM samples")
        con.execute("DELETE FROM battery_counters")
        con.execute("DELETE FROM i2c_snapshots")
        con.commit()


def insert_sample(
    timestamp: str,
    *,
    pv_w: Optional[float] = 0.0,
    battery_w: Optional[float] = 0.0,
    load_w: Optional[float] = 0.0,
    grid_w: Optional[float] = 0.0,
    battery_v: Optional[float] = 51.0,
) -> None:
    """Inserisce una riga in `samples` (timestamp univoco)."""
    from db import db

    with db() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO samples(
              timestamp,
              pv_w, pv_v, pv_a,
              battery_w, battery_v, battery_a,
              grid_w, grid_v, grid_hz, grid_a,
              load_w, load_v, load_hz, load_a, load_va, load_pf, load_percent,
              dc_temp, inverter_temp, heatsink_temp, dc_bus_v
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                timestamp,
                pv_w,
                None,
                None,
                battery_w,
                battery_v,
                None,
                grid_w,
                None,
                None,
                None,
                load_w,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ),
        )
        con.commit()
