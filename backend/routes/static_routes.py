# -*- coding: utf-8 -*-
"""Route per asset statici e pagine HTML in `backend/web/`."""
from __future__ import annotations

from flask import Flask, send_from_directory

from config import WEB_DIR


def register_static_routes(app: Flask) -> None:
    @app.route("/")
    def root():
        return send_from_directory(str(WEB_DIR), "index.html")

    @app.route("/settings")
    def settings_page():
        return send_from_directory(str(WEB_DIR), "settings.html")

    @app.route("/analysis")
    def analysis_page():
        return send_from_directory(str(WEB_DIR), "analysis_dashboard.html")

    @app.route("/main.css")
    def main_css():
        return send_from_directory(str(WEB_DIR), "main.css", mimetype="text/css")

    @app.route("/app.mod.js")
    def app_js():
        return send_from_directory(str(WEB_DIR), "app.mod.js", mimetype="text/javascript")

    @app.route("/settings.mod.js")
    def settings_js():
        return send_from_directory(str(WEB_DIR), "settings.mod.js", mimetype="text/javascript")

    @app.route("/manifest.webmanifest")
    def manifest():
        return send_from_directory(str(WEB_DIR), "manifest.webmanifest", mimetype="application/manifest+json")

    @app.route("/sw.js")
    def service_worker():
        return send_from_directory(str(WEB_DIR), "sw.js", mimetype="application/javascript")

    @app.route("/icons/<path:fname>")
    def icons(fname):
        return send_from_directory(str(WEB_DIR / "icons"), fname)

    @app.route("/offline.html")
    def offline_page():
        return send_from_directory(str(WEB_DIR), "offline.html")
