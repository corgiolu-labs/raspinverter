# -*- coding: utf-8 -*-
"""Costruzione del payload JSON per `/api/inverter` (sample DB vs memoria, SOC, relay, I2C)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from config import _get
from db import db, now_str, parse_ts

logger = logging.getLogger(__name__)


def _ts_of(s: Optional[Dict[str, Any]]) -> Optional[datetime]:
    return parse_ts(s.get("timestamp")) if s and "timestamp" in s else None


def _pick_sample(
    db_sample: Optional[Dict[str, Any]],
    mem_sample: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    db_ts = _ts_of(db_sample)
    mem_ts = _ts_of(mem_sample)
    if db_sample and (not mem_sample or (db_ts and mem_ts and db_ts >= mem_ts)):
        return dict(db_sample)
    if mem_sample:
        return dict(mem_sample)
    return {"timestamp": now_str()}


def _apply_soc_pct(s: Dict[str, Any]) -> None:
    try:
        vmax = float(_get("battery.soc.vmax_v", 58.0))
        vmin = float(_get("battery.soc.vmin_v", 44.0))
        v = float(s.get("battery_v") or 0.0)
        if vmax > vmin:
            s["soc_pct"] = round(max(0.0, min(100.0, 100.0 * (v - vmin) / (vmax - vmin))), 1)
    except Exception as e:
        logger.debug("inverter payload: soc_pct skip: %s", e)


def _fetch_battery_net_wh() -> float:
    try:
        with db() as con:
            row = con.execute(
                "SELECT total_batt_net_Wh FROM battery_counters ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return 0.0
    except Exception as e:
        logger.warning("inverter payload: battery_net_wh DB read failed: %s", e)
        return 0.0


def build_inverter_payload(
    *,
    db_sample: Optional[Dict[str, Any]],
    mem_sample: Optional[Dict[str, Any]],
    i2c_snapshot: Any,
    last_ok: Optional[str],
    last_err: Optional[str],
    relay_state: Any,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Assembla il dict restituito da `/api/inverter` (pronto per `jsonify`)."""
    now_dt = now or datetime.now()
    s = _pick_sample(db_sample, mem_sample)
    _apply_soc_pct(s)

    latest_dt = _ts_of(s)
    if latest_dt:
        s["stale_seconds"] = int((now_dt - latest_dt).total_seconds())
    s["last_ok"] = last_ok
    s["last_error"] = last_err

    s["relay"] = {
        "enabled": bool(_get("relay.enabled", False)),
        "state": relay_state,
    }

    if i2c_snapshot is not None:
        s["i2c"] = i2c_snapshot

    s["battery_net_wh"] = _fetch_battery_net_wh()
    return s
