# -*- coding: utf-8 -*-
"""Storico minuti, energie aggregate, totali giornalieri, manutenzione archivio."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, jsonify, request

from config import DB_PATH
from db import archive_compute_and_apply, db, db_files_size_bytes
from services.battery_service import get_current_battery_counter
from services.energy_query_service import (
    build_energy_hourly_data,
    build_energy_non_hour_series,
    build_minute_history_series,
    build_totals_today_payload,
    merge_energy_day_totals,
    normalize_energy_unit,
    parse_energy_window,
)

logger = logging.getLogger(__name__)


def register_energy_routes(app: Flask) -> None:
    @app.route("/api/history")
    def history():
        now_dt = datetime.now()
        day0_s = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        now_s = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        with db() as con:
            rows = con.execute("""
                SELECT
                  strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                  AVG(pv_w)      AS pv_w,
                  AVG(battery_w) AS battery_w,
                  AVG(load_w)    AS load_w,
                  AVG(grid_w)    AS grid_w
                FROM samples
                WHERE timestamp BETWEEN ? AND ?
                GROUP BY ts_min
                ORDER BY ts_min ASC
            """, (day0_s, now_s)).fetchall()

        return jsonify(build_minute_history_series(now_dt, rows))

    @app.route("/api/energy")
    def energy():
        gran = (request.args.get("granularity") or "hour").lower()
        unit, scale, suffix = normalize_energy_unit(request.args.get("unit") or "kWh")
        now_dt = datetime.now()

        date_arg = request.args.get("date")
        from_arg = request.args.get("from")
        min_year = None
        if gran not in ("hour", "day", "month"):
            with db() as con:
                row = con.execute("SELECT MIN(strftime('%Y', timestamp)) AS y0 FROM samples").fetchone()
            min_year = int(row["y0"] or now_dt.year)

        start, end, step = parse_energy_window(
            gran, now_dt, date_str=date_arg, from_str=from_arg, min_year_from_samples=min_year
        )
        start_s = start.strftime("%Y-%m-%d %H:%M:%S")
        end_s = end.strftime("%Y-%m-%d %H:%M:%S")

        if gran == "hour":
            with db() as con:
                rows = con.execute("""
                    WITH m AS (
                      SELECT strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                             AVG(pv_w) AS pv_w,
                             AVG(battery_w) AS battery_w,
                             AVG(load_w) AS load_w,
                             AVG(grid_w) AS grid_w
                      FROM samples
                      WHERE timestamp BETWEEN ? AND ?
                      GROUP BY ts_min
                    )
                    SELECT strftime('%Y-%m-%d %H:00', ts_min) AS bucket,
                           SUM(pv_w)/60.0  AS pv_Wh,
                           SUM(load_w)/60.0 AS load_Wh,
                           SUM(grid_w)/60.0 AS grid_Wh,
                           SUM(CASE WHEN battery_w>0 THEN battery_w ELSE 0 END)/60.0  AS batt_in_Wh,
                           SUM(CASE WHEN battery_w<0 THEN -battery_w ELSE 0 END)/60.0 AS batt_out_Wh
                    FROM m
                    GROUP BY bucket
                    ORDER BY bucket ASC
                """, (start_s, end_s)).fetchall()
            data = build_energy_hourly_data(start, end, rows, scale, suffix)
            return jsonify({"unit": unit, "data": data})

        with db() as con:
            s_rows = con.execute("""
                WITH m AS (
                  SELECT strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                         AVG(pv_w) AS pv_w,
                         AVG(battery_w) AS battery_w,
                         AVG(load_w) AS load_w,
                         AVG(grid_w) AS grid_w
                  FROM samples
                  WHERE timestamp BETWEEN ? AND ?
                  GROUP BY ts_min
                )
                SELECT date(ts_min) AS day,
                       SUM(pv_w)/60.0        AS pv_Wh,
                       SUM(load_w)/60.0      AS load_Wh,
                       SUM(grid_w)/60.0      AS grid_Wh,
                       SUM(CASE WHEN battery_w>0 THEN battery_w ELSE 0 END)/60.0  AS batt_in_Wh,
                       SUM(CASE WHEN battery_w<0 THEN -battery_w ELSE 0 END)/60.0 AS batt_out_Wh
                FROM m
                GROUP BY day
                ORDER BY day ASC
            """, (start_s, end_s)).fetchall()

            a_rows = con.execute("""
                SELECT day, pv_Wh, load_Wh, grid_Wh, batt_in_Wh, batt_out_Wh
                FROM archive
                WHERE day BETWEEN date(?) AND date(?)
                ORDER BY day ASC
            """, (start_s, end_s)).fetchall()

        acc = merge_energy_day_totals(s_rows, a_rows)
        data = build_energy_non_hour_series(step, start, end, acc, scale, suffix)
        return jsonify({"unit": unit, "data": data})

    @app.route("/api/totals/today")
    def totals_today():
        unit, scale, suffix = normalize_energy_unit(request.args.get("unit") or "kWh")
        now_dt = datetime.now()
        day0_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        start_s = day0_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_s = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        battery_counter = get_current_battery_counter()

        with db() as con:
            row = con.execute("""
            WITH m AS (
              SELECT strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                     AVG(pv_w) AS pv_w,
                     AVG(battery_w) AS battery_w,
                     AVG(load_w) AS load_w,
                     AVG(grid_w) AS grid_w
              FROM samples
              WHERE timestamp BETWEEN ? AND ?
              GROUP BY ts_min
            )
            SELECT
              SUM(pv_w)/60.0        AS pv_Wh,
              SUM(load_w)/60.0      AS load_Wh,
              SUM(grid_w)/60.0      AS grid_Wh,
              SUM(CASE WHEN battery_w>0 THEN battery_w ELSE 0 END)/60.0  AS batt_in_Wh,
              SUM(CASE WHEN battery_w<0 THEN -battery_w ELSE 0 END)/60.0 AS batt_out_Wh
            FROM m
            """, (start_s, end_s)).fetchone()

        payload = build_totals_today_payload(row, battery_counter, unit, scale, suffix)
        return jsonify(payload)

    @app.route("/api/maintenance/archive", methods=["POST"])
    def maintenance_archive():
        scope = (request.args.get("scope") or "").lower()
        dry_run = str(request.args.get("dry_run", "")).lower() in {"1", "true", "yes", "y"}
        vacuum = str(request.args.get("vacuum", "")).lower() in {"1", "true", "yes", "y"}
        try:
            size_before = db_files_size_bytes()
            if scope == "upto_today":
                cutoff = datetime.now().strftime("%Y-%m-%d 00:00:00")
                summary = archive_compute_and_apply(cutoff, apply=not dry_run)
                result = {"ok": True, "scope": "upto_today", **summary, "dry_run": dry_run}
            else:
                days = max(1, min(3650, int(request.args.get("days", "30"))))
                cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
                summary = archive_compute_and_apply(cutoff, apply=not dry_run)
                result = {"ok": True, "archived_days": days, **summary, "dry_run": dry_run}

            if (not dry_run) and vacuum:
                with db() as con:
                    try:
                        con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    except Exception as e:
                        logger.debug("maintenance archive: wal_checkpoint failed: %s", e)
                try:
                    with sqlite3.connect(str(DB_PATH)) as c2:
                        c2.execute("VACUUM;")
                except Exception as e:
                    logger.warning("maintenance archive: VACUUM failed: %s", e)

            size_after = db_files_size_bytes() if not dry_run else size_before
            result["size_before_bytes"] = size_before
            result["size_after_bytes"] = size_after
            result["size_delta_bytes"] = size_after - size_before
            return jsonify(result)
        except Exception as e:
            logger.warning("maintenance archive error: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500
