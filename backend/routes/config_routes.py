# -*- coding: utf-8 -*-
"""Lettura e persistenza configurazione JSON."""
from __future__ import annotations

import json
import logging
import os

from flask import Flask, jsonify, request

from config import CONF, CONFIG_PATH, DEFAULT_NET_RESET_V, _get, validate_config
from services.relay_service import relay_setup

logger = logging.getLogger(__name__)


def register_config_routes(app: Flask) -> None:
    @app.route("/api/config", methods=["GET", "POST"])
    def config():
        if request.method == "GET":
            try:
                has_i2c = "i2c" in CONF and isinstance(CONF.get("i2c"), dict)
                logger.debug("GET /api/config i2c present=%s", has_i2c)
            except Exception as e:
                logger.debug("GET /api/config i2c inspect skipped: %s", e)
            return jsonify({
                "battery": {
                    "type": _get("battery.type", "lifepo4"),
                    "nominal_voltage": float(_get("battery.nominal_voltage", 51.2)),
                    "nominal_ah": int(_get("battery.nominal_ah", 400)),
                    "net_reset_voltage": float(_get("battery.net_reset_voltage", DEFAULT_NET_RESET_V)),
                    "soc": {
                        "method": _get("battery.soc.method", "voltage_based"),
                        "vmax_v": float(_get("battery.soc.vmax_v", 58.0)) if _get("battery.soc.method", "voltage_based") == "voltage_based" else None,
                        "vmin_v": float(_get("battery.soc.vmin_v", 44.0)) if _get("battery.soc.method", "voltage_based") == "voltage_based" else None,
                        "reset_voltage": float(_get("battery.soc.reset_voltage", 44.0)) if _get("battery.soc.method", "voltage_based") == "energy_balance" else None
                    }
                },
                "ui": {"unit": _get("ui.unit", "W")},
                "relay": {
                    "mode": _get("relay.mode", "gpio"),
                    "enabled": bool(_get("relay.enabled", False)),
                    "gpio_pin": int(_get("relay.gpio_pin", 17)),
                    "active_high": bool(_get("relay.active_high", True)),
                    "on_v": float(_get("relay.on_v", 47.5)),
                    "off_v": float(_get("relay.off_v", 49.0)),
                    "min_toggle_sec": int(_get("relay.min_toggle_sec", 5))
                },
                "i2c": CONF.get("i2c")
            })

        data = request.get_json(silent=True) or {}
        ok, err = validate_config(data)
        if not ok:
            return jsonify({"ok": False, "error": err}), 400

        changed = False
        CONF.setdefault("battery", {})
        CONF.setdefault("ui", {})
        CONF.setdefault("relay", {})
        soc = CONF["battery"].setdefault("soc", {})

        b = data.get("battery") or {}
        if "type" in b:
            CONF["battery"]["type"] = str(b["type"]).lower()
            changed = True
        if "nominal_voltage" in b:
            CONF["battery"]["nominal_voltage"] = float(b["nominal_voltage"])
            changed = True
        if "nominal_ah" in b:
            CONF["battery"]["nominal_ah"] = int(b["nominal_ah"])
            changed = True
        if "soc" in b and isinstance(b["soc"], dict):
            if "method" in b["soc"]:
                soc["method"] = str(b["soc"]["method"])
                changed = True

            if b["soc"].get("method") == "energy_balance":
                if "reset_voltage" in b["soc"]:
                    soc["reset_voltage"] = float(b["soc"]["reset_voltage"])
                    changed = True
                if "vmax_v" in soc:
                    del soc["vmax_v"]
                if "vmin_v" in soc:
                    del soc["vmin_v"]
            elif b["soc"].get("method") == "voltage_based":
                if "vmax_v" in b["soc"]:
                    soc["vmax_v"] = float(b["soc"]["vmax_v"])
                    changed = True
                if "vmin_v" in b["soc"]:
                    soc["vmin_v"] = float(b["soc"]["vmin_v"])
                    changed = True
                if "reset_voltage" in soc:
                    del soc["reset_voltage"]

        if "net_reset_voltage" in b:
            try:
                CONF["battery"]["net_reset_voltage"] = float(b["net_reset_voltage"])
                changed = True
            except Exception as e:
                logger.warning("config POST: ignored invalid net_reset_voltage: %s", e)

        ui = data.get("ui") or {}
        if "unit" in ui:
            CONF["ui"]["unit"] = "kW" if str(ui["unit"]).upper() == "KW" else "W"
            changed = True

        r = data.get("relay") or {}
        if r:
            for k in ["mode", "enabled", "gpio_pin", "active_high", "on_v", "off_v", "min_toggle_sec"]:
                if k in r:
                    CONF["relay"][k] = r[k]
                    changed = True

        if data.get("persist") and changed:
            if "relay" in CONF and "soc" in CONF.get("battery", {}):
                if "webhook_on" in CONF["relay"]:
                    del CONF["relay"]["webhook_on"]
                    changed = True
                if "webhook_off" in CONF["relay"]:
                    del CONF["relay"]["webhook_off"]
                    changed = True

            tmp = str(CONFIG_PATH) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(CONF, f, indent=2, ensure_ascii=False)
            os.replace(tmp, str(CONFIG_PATH))
            try:
                relay_setup()
            except Exception as e:
                logger.warning("relay_setup after config persist failed: %s", e)

        return jsonify({"ok": True, "changed": changed})
