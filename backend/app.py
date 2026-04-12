# -*- coding: utf-8 -*-
"""Flask application factory."""
from __future__ import annotations

import logging

from flask import Flask, request

from import_paths import ensure_src_path

try:
    from flask_compress import Compress
except Exception:
    Compress = None  # type: ignore

from routes.api_routes import register_routes

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    ensure_src_path()

    logger.info("Creating Flask application...")
    app = Flask(__name__, static_folder=None)
    if Compress:
        try:
            Compress(app)
            logger.info("Flask-Compress enabled")
        except Exception as e:
            logger.warning("Flask-Compress not enabled: %s", e)

    @app.after_request
    def set_cache_headers(resp):
        """
        Apply no-cache only for API responses.
        Allow long-lived caching for static assets (handled by routes serving /web/* files).
        """
        try:
            path = request.path or ""
        except Exception as e:
            logger.debug("after_request: request.path unavailable: %s", e)
            path = ""

        if path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        else:
            static_exts = (
                ".js",
                ".css",
                ".png",
                ".jpg",
                ".jpeg",
                ".svg",
                ".webp",
                ".gif",
                ".ico",
                ".webmanifest",
            )
            if any(path.endswith(ext) for ext in static_exts):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.after_request
    def ensure_charset(resp):
        ctype = resp.headers.get("Content-Type", "")
        if ctype.startswith("text/") and "charset=" not in ctype.lower():
            resp.headers["Content-Type"] = f"{ctype}; charset=utf-8"
        return resp

    register_routes(app)
    logger.info("Flask app ready (routes bound)")
    return app
