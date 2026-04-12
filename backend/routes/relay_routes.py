# -*- coding: utf-8 -*-
"""Comando manuale relay GPIO."""
from __future__ import annotations

import logging

from flask import Flask, jsonify

from config import CONF
from services.relay_service import RELAY_STATE, _gpio_read, relay_apply

logger = logging.getLogger(__name__)


def register_relay_routes(app: Flask) -> None:
    @app.route("/api/relay/on", methods=["POST"])
    def relay_on():
        relay_apply(True)
        return jsonify({"ok": True, "relay": "on"})

    @app.route("/api/relay/off", methods=["POST"])
    def relay_off():
        relay_apply(False)
        return jsonify({"ok": True, "relay": "off"})

    @app.route("/api/relay/state", methods=["GET", "POST"])
    def relay_state():
        try:
            cfg = CONF.get("relay", {})
            pin = int(cfg.get("gpio_pin", 17))

            logger.info(
                "relay STATE request enabled=%s mode=%s pin=%s RELAY_STATE=%s",
                cfg.get("enabled"),
                cfg.get("mode"),
                pin,
                RELAY_STATE,
            )

            if not cfg.get("enabled", False):
                return jsonify({
                    "ok": True,
                    "enabled": False,
                    "mode": str(cfg.get("mode", "gpio")),
                    "gpio_pin": pin,
                    "active_high": bool(cfg.get("active_high", True)),
                    "state": RELAY_STATE,
                    "gpio_level": None,
                    "message": "Relay disabilitato"
                })

            try:
                level = _gpio_read(pin)
                logger.info("relay GPIO read pin %s level=%s", pin, level)
            except Exception as e:
                level = None
                logger.warning("relay GPIO read error pin %s: %s", pin, e)

            current_state = RELAY_STATE
            if current_state is None and level is not None:
                active_high = bool(cfg.get("active_high", True))
                current_state = (level == 1) if active_high else (level == 0)
                logger.info(
                    "relay RELAY_STATE inferred from GPIO level=%s active_high=%s state=%s",
                    level,
                    active_high,
                    current_state,
                )

            return jsonify({
                "ok": True,
                "enabled": bool(cfg.get("enabled", False)),
                "mode": str(cfg.get("mode", "gpio")),
                "gpio_pin": pin,
                "active_high": bool(cfg.get("active_high", True)),
                "state": current_state,
                "gpio_level": level,
                "message": "Stato relay letto correttamente"
            })

        except Exception as e:
            logger.exception("relay error in relay_state: %s", e)
            return jsonify({
                "ok": False,
                "error": f"Errore lettura stato relay: {str(e)}"
            }), 500
