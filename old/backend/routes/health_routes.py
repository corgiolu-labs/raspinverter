# -*- coding: utf-8 -*-
"""Health check e endpoint di test leggero."""
from __future__ import annotations

import logging
from datetime import datetime

from flask import Flask, jsonify

from config import (
    CONF,
    CONFIG_PATH,
    DB_PATH,
    MB_BAUD,
    MB_BYTES,
    MB_PARITY,
    MB_PORT,
    MB_STOP,
    MB_TIMEOUT,
    POLL_S,
    UNIT_ID,
)
from db import db, now_str, parse_ts
from services import modbus_service
from services.relay_service import RELAY_STATE

logger = logging.getLogger(__name__)


def register_health_routes(app: Flask) -> None:
    @app.route("/api/health")
    def health():
        with db() as con:
            row = con.execute("SELECT MAX(timestamp) AS last_ts FROM samples").fetchone()
        db_last = row["last_ts"] if row else None
        last_dt = parse_ts(db_last) or parse_ts(modbus_service.LAST_OK)
        stale_seconds = None
        if last_dt:
            stale_seconds = int((datetime.now() - last_dt).total_seconds())

        relay_cfg = CONF.get("relay", {})
        return jsonify({
            "status": "ok",
            "last_ok": modbus_service.LAST_OK,
            "last_error": modbus_service.LAST_ERR,
            "db_path": str(DB_PATH),
            "config_path": str(CONFIG_PATH),
            "serial": {"port": MB_PORT, "baud": MB_BAUD, "parity": MB_PARITY, "stop": MB_STOP, "bytes": MB_BYTES, "timeout": MB_TIMEOUT},
            "polling_interval_s": POLL_S,
            "db_last_sample": db_last,
            "stale_seconds": stale_seconds,
            "relay": {
                "enabled": bool(relay_cfg.get("enabled", False)),
                "mode": str(relay_cfg.get("mode", "gpio")),
                "gpio_pin": int(relay_cfg.get("gpio_pin", 17)),
                "state": RELAY_STATE
            }
        })

    @app.route("/api/test")
    def test_endpoint():
        logger.debug("GET /api/test")
        return jsonify({
            "ok": True,
            "message": "General test endpoint working",
            "timestamp": now_str(),
            "flask_version": "working"
        })
