# -*- coding: utf-8 -*-
"""API contatore energia batteria."""
from __future__ import annotations

import logging

from flask import Flask, jsonify, request

from db import now_str
from services.battery_service import get_current_battery_counter, reset_battery_counter

logger = logging.getLogger(__name__)


def register_battery_routes(app: Flask) -> None:
    @app.route("/api/battery/reset", methods=["POST"])
    def battery_reset():
        """Endpoint per resettare manualmente il contatore della batteria"""
        logger.info("battery RESET request received")
        try:
            logger.info("battery request JSON: %s", request.json)
            reason = request.json.get("reason", "manual") if request.json else "manual"
            logger.info("battery reset reason: %s", reason)

            counter_id = reset_battery_counter(reason)
            logger.info("battery reset completed, new counter ID: %s", counter_id)

            return jsonify({
                "ok": True,
                "message": "Contatore batteria azzerato",
                "new_counter_id": counter_id,
                "reason": reason
            })
        except Exception as e:
            logger.exception("battery error in battery_reset: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/battery/status")
    def battery_status():
        """Endpoint per ottenere lo stato del contatore della batteria"""
        logger.info("battery STATUS request received")
        try:
            counter = get_current_battery_counter()
            logger.info("battery counter result: %s", counter)

            if not counter:
                logger.info("battery no counter found, returning 404")
                return jsonify({"ok": False, "error": "Contatore non trovato"}), 404

            logger.debug("battery returning counter data")
            return jsonify({
                "ok": True,
                "counter": {
                    "id": counter.get("id"),
                    "start_timestamp": counter.get("start_timestamp"),
                    "start_battery_v": counter.get("start_battery_v"),
                    "total_batt_in_Wh": counter.get("total_batt_in_Wh", 0.0),
                    "total_batt_out_Wh": counter.get("total_batt_out_Wh", 0.0),
                    "total_batt_net_Wh": counter.get("total_batt_net_Wh", 0.0),
                    "reset_reason": counter.get("reset_reason"),
                    "created_at": counter.get("created_at")
                }
            })
        except Exception as e:
            logger.exception("battery error in battery_status: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/battery/test")
    def battery_test():
        """Endpoint di test semplice per verificare se il routing funziona"""
        logger.info("battery TEST endpoint called")
        return jsonify({
            "ok": True,
            "message": "Battery test endpoint working",
            "timestamp": now_str()
        })
