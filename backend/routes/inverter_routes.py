# -*- coding: utf-8 -*-
"""Ultimo campione inverter / stato live."""
from __future__ import annotations

import logging
from datetime import datetime

from flask import Flask, jsonify

import poll_state
from config import _get
from db import db, now_str, parse_ts
from services import i2c_service, modbus_service
from services.relay_service import RELAY_STATE

logger = logging.getLogger(__name__)


def register_inverter_routes(app: Flask) -> None:
    @app.route("/api/inverter")
    def inverter():
        with db() as con:
            row = con.execute("SELECT * FROM samples ORDER BY id DESC LIMIT 1").fetchone()
        db_sample = dict(row) if row else None
        mem_sample = poll_state.last_sample
        i2c_snapshot = i2c_service.LAST_I2C

        def ts_of(s):
            return parse_ts(s.get("timestamp")) if s and "timestamp" in s else None

        candidate = None
        db_ts = ts_of(db_sample)
        mem_ts = ts_of(mem_sample)
        if db_sample and (not mem_sample or (db_ts and mem_ts and db_ts >= mem_ts)):
            candidate = db_sample
        elif mem_sample:
            candidate = mem_sample

        s = candidate or {"timestamp": now_str()}
        try:
            vmax = float(_get("battery.soc.vmax_v", 58.0))
            vmin = float(_get("battery.soc.vmin_v", 44.0))
            v = float(s.get("battery_v") or 0.0)
            if vmax > vmin:
                s["soc_pct"] = round(max(0.0, min(100.0, 100.0 * (v - vmin) / (vmax - vmin))), 1)
        except Exception as e:
            logger.debug("inverter: soc_pct skip: %s", e)

        latest_dt = ts_of(s)
        if latest_dt:
            s["stale_seconds"] = int((datetime.now() - latest_dt).total_seconds())
        s["last_ok"] = modbus_service.LAST_OK
        s["last_error"] = modbus_service.LAST_ERR

        s["relay"] = {
            "enabled": bool(_get("relay.enabled", False)),
            "state": RELAY_STATE
        }

        if i2c_snapshot is not None:
            s["i2c"] = i2c_snapshot

        try:
            with db() as con:
                row = con.execute(
                    "SELECT total_batt_net_Wh FROM battery_counters ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row and row[0] is not None:
                    s["battery_net_wh"] = float(row[0])
                else:
                    s["battery_net_wh"] = 0.0
        except Exception as e:
            logger.warning("inverter: battery_net_wh DB read failed: %s", e)
            s["battery_net_wh"] = 0.0

        return jsonify(s)
