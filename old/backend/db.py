# -*- coding: utf-8 -*-
"""SQLite connection helpers and archive utilities."""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import DB_PATH

logger = logging.getLogger(__name__)


def db():
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.debug("db: could not mkdir parent of DB_PATH: %s", e)
    con = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA busy_timeout=5000;")
    return con


def db_init():
    logger.info("Initializing database...")
    with db() as con:
        logger.info("Creating samples table...")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS samples(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              timestamp TEXT NOT NULL,
              pv_w REAL, pv_v REAL, pv_a REAL,
              battery_w REAL, battery_v REAL, battery_a REAL,
              grid_w REAL, grid_v REAL, grid_hz REAL, grid_a REAL,
              load_w REAL, load_v REAL, load_hz REAL, load_a REAL, load_va REAL, load_pf REAL, load_percent REAL,
              dc_temp REAL, inverter_temp REAL, heatsink_temp REAL, dc_bus_v REAL
            );
        """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON samples(timestamp);")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS uniq_ts ON samples(timestamp);")

        logger.info("Creating archive table...")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS archive(
              day TEXT PRIMARY KEY,
              pv_Wh REAL, load_Wh REAL, grid_Wh REAL,
              batt_in_Wh REAL, batt_out_Wh REAL
            );
        """
        )

        logger.info("Creating battery_counters table...")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS battery_counters(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              counter_type TEXT NOT NULL,
              start_timestamp TEXT NOT NULL,
              start_battery_v REAL NOT NULL,
              total_batt_in_Wh REAL DEFAULT 0.0,
              total_batt_out_Wh REAL DEFAULT 0.0,
              total_batt_net_Wh REAL DEFAULT 0.0,
              reset_reason TEXT,
              created_at TEXT NOT NULL
            );
        """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_battery_counters_type ON battery_counters(counter_type);"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_battery_counters_timestamp ON battery_counters(start_timestamp);"
        )

        logger.info("Creating i2c_snapshots table...")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS i2c_snapshots(
              timestamp TEXT PRIMARY KEY,
              data TEXT
            );
        """
        )

        con.commit()
    logger.info("Database initialization completed")


def db_trim(days: int = 365):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with db() as con:
        con.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
        con.commit()


def db_archive_and_trim(days: int = 30):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    with db() as con:
        rows = con.execute(
            """
            WITH m AS (
              SELECT strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                     AVG(pv_w)       AS pv_w,
                     AVG(battery_w)  AS battery_w,
                     AVG(load_w)     AS load_w,
                     AVG(grid_w)     AS grid_w
              FROM samples
              WHERE timestamp < ?
              GROUP BY ts_min
            )
            SELECT
              date(ts_min) AS day,
              SUM(pv_w)/60.0        AS pv_Wh,
              SUM(load_w)/60.0      AS load_Wh,
              SUM(grid_w)/60.0      AS grid_Wh,
              SUM(CASE WHEN battery_w>0 THEN battery_w ELSE 0 END)/60.0  AS batt_in_Wh,
              SUM(CASE WHEN battery_w<0 THEN -battery_w ELSE 0 END)/60.0 AS batt_out_Wh
            FROM m
            GROUP BY day
            ORDER BY day ASC;
        """,
            (cutoff,),
        ).fetchall()

        for r in rows:
            con.execute(
                """
                INSERT OR REPLACE INTO archive
                  (day, pv_Wh, load_Wh, grid_Wh, batt_in_Wh, batt_out_Wh)
                VALUES (?,?,?,?,?,?)
            """,
                (r["day"], r["pv_Wh"], r["load_Wh"], r["grid_Wh"], r["batt_in_Wh"], r["batt_out_Wh"]),
            )

        con.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
        con.commit()


def db_archive_upto_today():
    cutoff = datetime.now().strftime("%Y-%m-%d 00:00:00")
    with db() as con:
        rows = con.execute(
            """
            WITH m AS (
              SELECT strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                     AVG(pv_w)       AS pv_w,
                     AVG(battery_w)  AS battery_w,
                     AVG(load_w)     AS load_w,
                     AVG(grid_w)     AS grid_w
              FROM samples
              WHERE timestamp < ?
              GROUP BY ts_min
            )
            SELECT
              date(ts_min) AS day,
              SUM(pv_w)/60.0        AS pv_Wh,
              SUM(load_w)/60.0      AS load_Wh,
              SUM(grid_w)/60.0      AS grid_Wh,
              SUM(CASE WHEN battery_w>0 THEN battery_w ELSE 0 END)/60.0  AS batt_in_Wh,
              SUM(CASE WHEN battery_w<0 THEN -battery_w ELSE 0 END)/60.0 AS batt_out_Wh
            FROM m
            GROUP BY day
            ORDER BY day ASC;
        """,
            (cutoff,),
        ).fetchall()

        for r in rows:
            con.execute(
                """
                INSERT OR REPLACE INTO archive
                  (day, pv_Wh, load_Wh, grid_Wh, batt_in_Wh, batt_out_Wh)
                VALUES (?,?,?,?,?,?)
            """,
                (r["day"], r["pv_Wh"], r["load_Wh"], r["grid_Wh"], r["batt_in_Wh"], r["batt_out_Wh"]),
            )

        con.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
        con.commit()


def db_files_size_bytes() -> int:
    total = 0
    try:
        total += os.path.getsize(str(DB_PATH))
    except Exception:
        pass
    for suf in ("-wal", "-shm"):
        try:
            total += os.path.getsize(str(DB_PATH) + suf)
        except Exception:
            pass
    return total


def archive_compute_and_apply(cutoff: str, apply: bool) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    with db() as con:
        cnt = con.execute("SELECT COUNT(*) FROM samples WHERE timestamp < ?", (cutoff,)).fetchone()[0]
        summary["minutes_to_delete"] = int(cnt or 0)
        rows = con.execute(
            """
            WITH m AS (
              SELECT strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                     AVG(pv_w) AS pv_w,
                     AVG(battery_w) AS battery_w,
                     AVG(load_w) AS load_w,
                     AVG(grid_w) AS grid_w
              FROM samples
              WHERE timestamp < ?
              GROUP BY ts_min
            )
            SELECT
              date(ts_min) AS day,
              SUM(pv_w)/60.0        AS pv_Wh,
              SUM(load_w)/60.0      AS load_Wh,
              SUM(grid_w)/60.0      AS grid_Wh,
              SUM(CASE WHEN battery_w>0 THEN battery_w ELSE 0 END)/60.0  AS batt_in_Wh,
              SUM(CASE WHEN battery_w<0 THEN -battery_w ELSE 0 END)/60.0 AS batt_out_Wh
            FROM m
            GROUP BY day
            ORDER BY day ASC;
        """,
            (cutoff,),
        ).fetchall()
        summary["days_to_archive"] = len(rows)

        if apply and rows:
            for r in rows:
                con.execute(
                    """
                    INSERT OR REPLACE INTO archive
                      (day, pv_Wh, load_Wh, grid_Wh, batt_in_Wh, batt_out_Wh)
                    VALUES (?,?,?,?,?,?)
                """,
                    (r["day"], r["pv_Wh"], r["load_Wh"], r["grid_Wh"], r["batt_in_Wh"], r["batt_out_Wh"]),
                )
            con.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
            con.commit()
    return summary


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
