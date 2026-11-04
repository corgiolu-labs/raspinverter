# -*- coding: utf-8 -*-
"""Ultimo campione inverter / stato live."""
from __future__ import annotations

from flask import Flask, jsonify

import poll_state
from db import db
from services import i2c_service, modbus_service
from services.inverter_query_service import build_inverter_payload
from services.relay_service import RELAY_STATE


def register_inverter_routes(app: Flask) -> None:
    @app.route("/api/inverter")
    def inverter():
        with db() as con:
            row = con.execute("SELECT * FROM samples ORDER BY id DESC LIMIT 1").fetchone()
        db_sample = dict(row) if row else None
        payload = build_inverter_payload(
            db_sample=db_sample,
            mem_sample=poll_state.last_sample,
            i2c_snapshot=i2c_service.LAST_I2C,
            last_ok=modbus_service.LAST_OK,
            last_err=modbus_service.LAST_ERR,
            relay_state=RELAY_STATE,
        )
        return jsonify(payload)
