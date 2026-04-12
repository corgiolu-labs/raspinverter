# -*- coding: utf-8 -*-
"""Lettura e persistenza configurazione JSON."""
from __future__ import annotations

import logging

from flask import Flask, jsonify, request

from config import CONF
from services.config_service import (
    build_config_get_payload,
    finalize_config_persist,
    merge_config_from_post,
)

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
            return jsonify(build_config_get_payload())

        data = request.get_json(silent=True) or {}
        ok, err, changed = merge_config_from_post(data)
        if not ok:
            return jsonify({"ok": False, "error": err}), 400

        changed = finalize_config_persist(data, changed)
        return jsonify({"ok": True, "changed": changed})
