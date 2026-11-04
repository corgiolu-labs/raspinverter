# -*- coding: utf-8 -*-
"""API snapshot e storico I2C."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, jsonify, request

from db import db

logger = logging.getLogger(__name__)


def register_i2c_routes(app: Flask) -> None:
    @app.route("/api/i2c/latest")
    def i2c_latest():
        """Return latest I2C snapshot persisted in DB."""
        try:
            with db() as con:
                row = con.execute(
                    "SELECT timestamp, data FROM i2c_snapshots ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "No I2C data"}), 404
            ts = row["timestamp"] if isinstance(row, sqlite3.Row) else row[0]
            data_txt = row["data"] if isinstance(row, sqlite3.Row) else row[1]
            try:
                payload = json.loads(data_txt) if data_txt else {}
            except Exception as e:
                logger.debug("i2c/latest JSON parse failed: %s", e)
                payload = {}
            return jsonify({"ok": True, "timestamp": ts, "i2c": payload})
        except Exception as e:
            logger.warning("i2c/latest error: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/i2c/history")
    def i2c_history():
        """
        Returns time series for a specific I2C device/channel and metric for a given day.
        Query params:
          - date: YYYY-MM-DD (default today)
          - device: device name (required)
          - channel: channel name (required)
          - metric: 'mv' or 'current_a' (default 'mv')
        """
        try:
            metric = (request.args.get("metric") or "mv").lower()
            device = request.args.get("device") or ""
            channel = request.args.get("channel") or ""
            if not device or not channel:
                return jsonify({"ok": False, "error": "Missing device or channel"}), 400

            now_dt = datetime.now()
            base_str = request.args.get("date") or now_dt.strftime("%Y-%m-%d")
            try:
                base_dt = datetime.strptime(base_str, "%Y-%m-%d")
            except Exception:
                return jsonify({"ok": False, "error": "Invalid date format"}), 400
            start = base_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now_dt if base_dt.date() == now_dt.date() else start + timedelta(days=1) - timedelta(seconds=1)
            start_s = start.strftime("%Y-%m-%d %H:%M:%S")
            end_s = end.strftime("%Y-%m-%d %H:%M:%S")

            with db() as con:
                rows = con.execute("""
                    SELECT timestamp, data FROM i2c_snapshots
                    WHERE timestamp BETWEEN ? AND ?
                    ORDER BY timestamp ASC
                """, (start_s, end_s)).fetchall()

            out = []
            for r in rows:
                ts = r["timestamp"] if isinstance(r, sqlite3.Row) else r[0]
                txt = r["data"] if isinstance(r, sqlite3.Row) else r[1]
                try:
                    obj = json.loads(txt) if txt else {}
                except Exception:
                    logger.debug("i2c/history row JSON skip ts=%s", ts)
                    obj = {}
                dev_map = obj.get(device)
                if not isinstance(dev_map, dict):
                    continue
                val = dev_map.get(channel)
                if val is None:
                    continue
                if isinstance(val, dict):
                    v = val.get(metric)
                else:
                    v = val if metric == "mv" else None
                if v is None:
                    continue
                try:
                    vnum = float(v)
                except Exception:
                    continue
                out.append({"timestamp": ts, "value": vnum})

            return jsonify({"ok": True, "metric": metric, "device": device, "channel": channel, "data": out})
        except Exception as e:
            logger.warning("i2c/history error: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500
