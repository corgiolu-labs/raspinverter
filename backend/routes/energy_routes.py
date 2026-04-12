# -*- coding: utf-8 -*-
"""Storico minuti, energie aggregate, totali giornalieri, manutenzione archivio."""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from flask import Flask, jsonify, request

from config import DB_PATH
from db import archive_compute_and_apply, db, db_files_size_bytes
from services.battery_service import get_current_battery_counter

logger = logging.getLogger(__name__)


def register_energy_routes(app: Flask) -> None:
    def _energy_window(gran: str):
        now_dt = datetime.now()
        if gran == "hour":
            base = datetime.strptime(request.args.get("date") or now_dt.strftime("%Y-%m-%d"), "%Y-%m-%d")
            start = base.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now_dt if base.date() == now_dt.date() else start + timedelta(days=1) - timedelta(seconds=1)
            step = "hour"
        elif gran == "day":
            base = datetime.strptime(request.args.get("from") or now_dt.strftime("%Y-%m-%d"), "%Y-%m-%d")
            start = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if base.month == now_dt.month and base.year == now_dt.year:
                end = now_dt
            else:
                month_next = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
                end = month_next - timedelta(seconds=1)
            step = "day"
        elif gran == "month":
            base = datetime.strptime(request.args.get("from") or now_dt.strftime("%Y-%m-%d"), "%Y-%m-%d")
            start = base.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now_dt if base.year == now_dt.year else start.replace(year=start.year + 1) - timedelta(seconds=1)
            step = "month"
        else:
            with db() as con:
                row = con.execute("SELECT MIN(strftime('%Y', timestamp)) AS y0 FROM samples").fetchone()
            y0 = int(row["y0"] or now_dt.year)
            start = datetime(y0, 1, 1)
            end = now_dt
            step = "year"
        return start, end, step

    @app.route("/api/history")
    def history():
        now_dt = datetime.now()
        day0_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        now_s = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        day0_s = day0_dt.strftime("%Y-%m-%d %H:%M:%S")

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

        agg = {r["ts_min"]: {"pv_w": r["pv_w"], "battery_w": r["battery_w"], "load_w": r["load_w"], "grid_w": r["grid_w"]} for r in rows}

        out = []
        t = day0_dt
        while t <= now_dt:
            key = t.strftime("%Y-%m-%d %H:%M:00")
            if key in agg:
                v = agg[key]
                out.append({"timestamp": key, "pv_w": v["pv_w"], "battery_w": v["battery_w"], "load_w": v["load_w"], "grid_w": v["grid_w"]})
            else:
                out.append({"timestamp": key, "pv_w": None, "battery_w": None, "load_w": None, "grid_w": None})
            t += timedelta(minutes=1)

        return jsonify(out)

    @app.route("/api/energy")
    def energy():
        gran = (request.args.get("granularity") or "hour").lower()
        unit = (request.args.get("unit") or "kWh").lower()
        if unit not in {"wh", "kwh"}:
            unit = "kwh"
        scale = 1.0 / 1000.0 if unit == "kwh" else 1.0
        suffix = "_kWh" if unit == "kwh" else "_Wh"

        start, end, step = _energy_window(gran)
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

            have = {r["bucket"]: r for r in rows}
            out = []
            cur = start.replace(minute=0, second=0, microsecond=0)
            while cur <= end:
                b = cur.strftime("%Y-%m-%d %H:00")
                r = have.get(b)
                if r:
                    bi = float(r["batt_in_Wh"] or 0.0)
                    bo = float(r["batt_out_Wh"] or 0.0)
                    out.append({
                        "bucket": b,
                        f"pv{suffix}": float(r["pv_Wh"] or 0.0) * scale,
                        f"load{suffix}": float(r["load_Wh"] or 0.0) * scale,
                        f"grid{suffix}": float(r["grid_Wh"] or 0.0) * scale,
                        f"batt_in{suffix}": bi * scale,
                        f"batt_out{suffix}": bo * scale,
                        f"batt_net{suffix}": (bi - bo) * scale
                    })
                else:
                    out.append({
                        "bucket": b,
                        f"pv{suffix}": 0.0, f"load{suffix}": 0.0, f"grid{suffix}": 0.0,
                        f"batt_in{suffix}": 0.0, f"batt_out{suffix}": 0.0, f"batt_net{suffix}": 0.0
                    })
                cur += timedelta(hours=1)
            return jsonify({"unit": unit, "data": out})

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

        acc = defaultdict(lambda: {"pv": 0.0, "load": 0.0, "grid": 0.0, "bi": 0.0, "bo": 0.0})

        for r in s_rows:
            d = r["day"]
            acc[d]["pv"] += float(r["pv_Wh"] or 0.0)
            acc[d]["load"] += float(r["load_Wh"] or 0.0)
            acc[d]["grid"] += float(r["grid_Wh"] or 0.0)
            acc[d]["bi"] += float(r["batt_in_Wh"] or 0.0)
            acc[d]["bo"] += float(r["batt_out_Wh"] or 0.0)

        for r in a_rows:
            d = r["day"]
            acc[d]["pv"] += float(r["pv_Wh"] or 0.0)
            acc[d]["load"] += float(r["load_Wh"] or 0.0)
            acc[d]["grid"] += float(r["grid_Wh"] or 0.0)
            acc[d]["bi"] += float(r["batt_in_Wh"] or 0.0)
            acc[d]["bo"] += float(r["batt_out_Wh"] or 0.0)

        def rec(bucket, pv, ld, gr, bi, bo):
            return {
                "bucket": bucket,
                f"pv{suffix}": pv * scale,
                f"load{suffix}": ld * scale,
                f"grid{suffix}": gr * scale,
                f"batt_in{suffix}": bi * scale,
                f"batt_out{suffix}": bo * scale,
                f"batt_net{suffix}": (bi - bo) * scale
            }

        out = []
        if step == "day":
            cur = start
            while cur <= end:
                d = cur.strftime("%Y-%m-%d")
                v = acc.get(d)
                if v:
                    out.append(rec(d, v["pv"], v["load"], v["grid"], v["bi"], v["bo"]))
                else:
                    out.append(rec(d, 0.0, 0.0, 0.0, 0.0, 0.0))
                cur += timedelta(days=1)

        elif step == "month":
            bym = defaultdict(lambda: {"pv": 0.0, "load": 0.0, "grid": 0.0, "bi": 0.0, "bo": 0.0})
            for d, v in acc.items():
                m = datetime.strptime(d, "%Y-%m-%d").strftime("%Y-%m")
                for k in bym[m]:
                    bym[m][k] += v[k]
            cur = start.replace(day=1)
            while cur <= end:
                m = cur.strftime("%Y-%m")
                v = bym.get(m)
                if v:
                    out.append(rec(m, v["pv"], v["load"], v["grid"], v["bi"], v["bo"]))
                else:
                    out.append(rec(m, 0.0, 0.0, 0.0, 0.0, 0.0))
                cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)

        else:
            byy = defaultdict(lambda: {"pv": 0.0, "load": 0.0, "grid": 0.0, "bi": 0.0, "bo": 0.0})
            for d, v in acc.items():
                y = datetime.strptime(d, "%Y-%m-%d").strftime("%Y")
                for k in byy[y]:
                    byy[y][k] += v[k]
            cur = start.replace(month=1, day=1)
            while cur <= end:
                y = cur.strftime("%Y")
                v = byy.get(y)
                if v:
                    out.append(rec(y, v["pv"], v["load"], v["grid"], v["bi"], v["bo"]))
                else:
                    out.append(rec(y, 0.0, 0.0, 0.0, 0.0, 0.0))
                cur = cur.replace(year=cur.year + 1)

        return jsonify({"unit": unit, "data": out})

    @app.route("/api/totals/today")
    def totals_today():
        unit = (request.args.get("unit") or "kWh").lower()
        if unit not in {"wh", "kwh"}:
            unit = "kwh"
        scale = 1.0 / 1000.0 if unit == "kwh" else 1.0
        suffix = "_kWh" if unit == "kwh" else "_Wh"

        now_dt = datetime.now()
        day0_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        start_s = day0_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_s = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        battery_counter = get_current_battery_counter()
        battery_net = 0.0
        if battery_counter:
            battery_net = float(battery_counter.get("total_batt_net_Wh", 0.0))

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

        pv = float(row["pv_Wh"] or 0.0) * scale
        ld = float(row["load_Wh"] or 0.0) * scale
        gr = float(row["grid_Wh"] or 0.0) * scale
        bi = float(row["batt_in_Wh"] or 0.0) * scale
        bo = float(row["batt_out_Wh"] or 0.0) * scale

        batt_net = battery_net * scale

        return jsonify({
            "unit": unit,
            f"pv{suffix}": pv,
            f"load{suffix}": ld,
            f"grid{suffix}": gr,
            f"batt_in{suffix}": bi,
            f"batt_out{suffix}": bo,
            f"batt_net{suffix}": batt_net,
            "battery_counter_info": {
                "start_timestamp": battery_counter.get("start_timestamp") if battery_counter else None,
                "reset_reason": battery_counter.get("reset_reason") if battery_counter else None,
                "total_batt_net_Wh": battery_counter.get("total_batt_net_Wh", 0.0) if battery_counter else 0.0
            }
        })

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
