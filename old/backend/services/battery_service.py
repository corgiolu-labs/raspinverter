# -*- coding: utf-8 -*-
"""Persistent battery net energy counter (SQLite)."""
from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import Any, Dict, Optional

from config import CONF, DEFAULT_NET_RESET_V, POLL_S, _get
from db import db, now_str, parse_ts

logger = logging.getLogger(__name__)


def get_current_battery_counter():
    logger.debug("get_current_battery_counter() called")
    try:
        with db() as con:
            row = con.execute(
                """
                SELECT * FROM battery_counters
                WHERE counter_type = 'daily_net'
                ORDER BY start_timestamp DESC
                LIMIT 1
            """
            ).fetchone()

            if row:
                logger.debug("Found existing counter: %s", row)
                return dict(row)

            now = now_str()
            cursor = con.execute(
                """
                INSERT INTO battery_counters
                (counter_type, start_timestamp, start_battery_v, created_at)
                VALUES (?, ?, ?, ?)
            """,
                ("daily_net", now, 0.0, now),
            )
            con.commit()
            new_id = cursor.lastrowid
            logger.info("New battery counter created id=%s", new_id)
            return {
                "id": new_id,
                "counter_type": "daily_net",
                "start_timestamp": now,
                "start_battery_v": 0.0,
                "total_batt_in_Wh": 0.0,
                "total_batt_out_Wh": 0.0,
                "total_batt_net_Wh": 0.0,
                "reset_reason": "initial",
                "created_at": now,
            }
    except Exception as e:
        logger.error("Error in get_current_battery_counter: %s", e)
        traceback.print_exc()
        return None


def reset_battery_counter(reason="manual"):
    now = now_str()
    with db() as con:
        con.execute(
            """
            UPDATE battery_counters
            SET reset_reason = ?
            WHERE counter_type = 'daily_net'
            AND reset_reason IS NULL
        """,
            (reason,),
        )
        cursor = con.execute(
            """
            INSERT INTO battery_counters
            (counter_type, start_timestamp, start_battery_v, created_at)
            VALUES (?, ?, ?, ?)
        """,
            ("daily_net", now, 0.0, now),
        )
        con.commit()
        logger.info("Battery counter reset: %s", reason)
        return cursor.lastrowid


def update_battery_counter(battery_w, battery_v):
    if battery_w is None or battery_v is None:
        return

    counter = get_current_battery_counter()
    if not counter:
        return

    energy_wh = (float(battery_w) * POLL_S) / 3600.0

    with db() as con:
        if battery_w > 0:
            con.execute(
                """
                UPDATE battery_counters
                SET total_batt_in_Wh = total_batt_in_Wh + ?,
                    total_batt_net_Wh = total_batt_in_Wh + ? - total_batt_out_Wh
                WHERE id = ?
            """,
                (energy_wh, energy_wh, counter["id"]),
            )
        elif battery_w < 0:
            energy_wh = abs(energy_wh)
            con.execute(
                """
                UPDATE battery_counters
                SET total_batt_out_Wh = total_batt_out_Wh + ?,
                    total_batt_net_Wh = total_batt_in_Wh - (total_batt_out_Wh + ?)
                WHERE id = ?
            """,
                (energy_wh, energy_wh, counter["id"]),
            )
        con.commit()


def check_battery_reset_condition(battery_v, battery_w) -> bool:
    if battery_v is None or battery_w is None:
        return False

    battery_v = float(battery_v)
    battery_w = float(battery_w)

    try:
        reset_thr = float(_get("battery.net_reset_voltage", DEFAULT_NET_RESET_V))
    except Exception:
        reset_thr = DEFAULT_NET_RESET_V

    if battery_w < 0 and battery_v <= reset_thr:
        with db() as con:
            last_reset = con.execute(
                """
                SELECT start_timestamp FROM battery_counters
                WHERE counter_type = 'daily_net'
                ORDER BY start_timestamp DESC
                LIMIT 1
            """
            ).fetchone()

            if last_reset:
                last_reset_dt = parse_ts(last_reset["start_timestamp"])
                if last_reset_dt:
                    time_diff = datetime.now() - last_reset_dt
                    if time_diff.total_seconds() < 3600:
                        return False

        reset_battery_counter(f"battery_{reset_thr:.1f}v_discharge_{battery_v:.1f}V")
        return True

    return False
