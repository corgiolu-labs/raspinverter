# -*- coding: utf-8 -*-
"""Flask HTTP and JSON API routes (same paths/behavior as legacy monolith)."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict

from flask import Flask, jsonify, request, send_from_directory

import poll_state
from config import (
    CONF,
    CONFIG_PATH,
    DEFAULT_NET_RESET_V,
    MB_BAUD,
    MB_BYTES,
    MB_PARITY,
    MB_PORT,
    MB_STOP,
    MB_TIMEOUT,
    POLL_S,
    UNIT_ID,
    WEB_DIR,
    _get,
    validate_config,
)
from db import (
    archive_compute_and_apply,
    db,
    db_files_size_bytes,
    now_str,
    parse_ts,
)
from db import DB_PATH
from services import modbus_service
from services import i2c_service
from services.battery_service import (
    get_current_battery_counter,
    reset_battery_counter,
)
from services.relay_service import RELAY_STATE, _gpio_read, relay_apply, relay_setup

logger = logging.getLogger(__name__)

daily_analyzer = None  # set in register_routes


def register_routes(app: Flask) -> None:
    global daily_analyzer
    from daily_analyzer import DailyAnalyzer

    daily_analyzer = DailyAnalyzer()

    @app.route("/api/analysis/daily/<date>")
    def get_daily_analysis(date):
        """Ottiene analisi giornaliera per una data specifica"""
        try:
            analysis = daily_analyzer.analyze_daily_data(date)
            if analysis:
                return jsonify(analysis)
            else:
                return jsonify({"error": "Nessun dato trovato per questa data"}), 404
        except Exception as e:
            return jsonify({"error": f"Errore analisi: {str(e)}"}), 500

    @app.route("/api/analysis/cleanup/<date>", methods=["POST"])
    def cleanup_daily_data(date):
        """Pulisce i campioni giornalieri dopo aver salvato l'analisi"""
        try:
            # Prima analizza i dati
            analysis = daily_analyzer.analyze_daily_data(date)
            if not analysis:
                return jsonify({"error": "Nessun dato da analizzare"}), 404
        
            # Poi cancella i campioni mantenendo l'analisi
            daily_analyzer.cleanup_old_samples(date, keep_analysis=True)
        
            return jsonify({
                "success": True,
                "message": f"Analisi salvata e campioni cancellati per {date}",
                "analysis_summary": {
                    "total_samples": analysis.get("total_samples", 0),
                    "pv_energy": analysis.get("pv_analysis", {}).get("total_energy_kwh", 0),
                    "battery_energy": analysis.get("battery_analysis", {}).get("total_energy_kwh", 0),
                    "anomalies": analysis.get("anomaly_detection", {}).get("total_anomalies", 0)
                }
            })
        
        except Exception as e:
            return jsonify({"error": f"Errore pulizia: {str(e)}"}), 500

    @app.route("/api/analysis/seasonal")
    def get_seasonal_insights():
        """Ottiene insights stagionali dalle analisi salvate"""
        try:
            with db() as con:
                # Ottieni ultimi 30 giorni di analisi
                rows = con.execute("""
                    SELECT date, analysis_data FROM daily_analysis 
                    WHERE date >= DATE('now', '-30 days')
                    ORDER BY date DESC
                """).fetchall()
            
                seasonal_data = []
                for row in rows:
                    try:
                        analysis = json.loads(row[1])
                        seasonal = analysis.get("seasonal_insights", {})
                        if seasonal:
                            seasonal_data.append({
                                "date": row[0],
                                "daylight_hours": seasonal.get("daylight_hours", 0),
                                "season": seasonal.get("season", "unknown"),
                                "pv_energy": analysis.get("pv_analysis", {}).get("total_energy_kwh", 0)
                            })
                    except:
                        continue
            
                return jsonify({
                    "period": "30_days",
                    "data_points": len(seasonal_data),
                    "seasonal_data": seasonal_data
                })
            
        except Exception as e:
            return jsonify({"error": f"Errore insights stagionali: {str(e)}"}), 500
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

    # ---------------------------------------------------------------------------
    # API
    # ---------------------------------------------------------------------------
    @app.route("/api/health")
    def health():
        with db() as con:
            row = con.execute("SELECT MAX(timestamp) AS last_ts FROM samples").fetchone()
        db_last = row["last_ts"] if row else None
        last_dt = parse_ts(db_last) or parse_ts(modbus_service.LAST_OK)
        stale_seconds = None
        if last_dt:
            stale_seconds = int((datetime.now() - last_dt).total_seconds())

        relay_cfg = CONF.get("relay", {})
        return jsonify({
            "status": "ok",
            "last_ok": modbus_service.LAST_OK,
            "last_error": modbus_service.LAST_ERR,
            "db_path": str(DB_PATH),
            "config_path": str(CONFIG_PATH),
            "serial": {"port": MB_PORT, "baud": MB_BAUD, "parity": MB_PARITY, "stop": MB_STOP, "bytes": MB_BYTES, "timeout": MB_TIMEOUT},
            "polling_interval_s": POLL_S,
            "db_last_sample": db_last,
            "stale_seconds": stale_seconds,
            "relay": {
                "enabled": bool(relay_cfg.get("enabled", False)),
                "mode": str(relay_cfg.get("mode", "gpio")),
                "gpio_pin": int(relay_cfg.get("gpio_pin", 17)),
                "state": RELAY_STATE
            }
        })

    @app.route("/api/config", methods=["GET","POST"])
    def config():
        if request.method=="GET":
            try:
                has_i2c = "i2c" in CONF and isinstance(CONF.get("i2c"), dict)
                logger.info("GET /api/config -> i2c present=%s", has_i2c)
            except Exception:
                pass
            return jsonify({
                "battery":{
                    "type":_get("battery.type","lifepo4"),
                    "nominal_voltage": float(_get("battery.nominal_voltage",51.2)),
                    "nominal_ah": int(_get("battery.nominal_ah",400)),
                    "net_reset_voltage": float(_get("battery.net_reset_voltage", DEFAULT_NET_RESET_V)),
                    "soc":{
                        "method": _get("battery.soc.method", "voltage_based"),
                        "vmax_v": float(_get("battery.soc.vmax_v",58.0)) if _get("battery.soc.method", "voltage_based") == "voltage_based" else None,
                        "vmin_v": float(_get("battery.soc.vmin_v",44.0)) if _get("battery.soc.method", "voltage_based") == "voltage_based" else None,
                        "reset_voltage": float(_get("battery.soc.reset_voltage",44.0)) if _get("battery.soc.method", "voltage_based") == "energy_balance" else None
                    }
                },
                "ui":{"unit": _get("ui.unit","W")},
                "relay":{
                    "mode": _get("relay.mode","gpio"),
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
            return jsonify({"ok":False,"error":err}), 400

        changed=False
        CONF.setdefault("battery",{}); CONF.setdefault("ui",{}); CONF.setdefault("relay",{})
        soc = CONF["battery"].setdefault("soc",{})

        b = data.get("battery") or {}
        if "type" in b:            CONF["battery"]["type"] = str(b["type"]).lower(); changed=True
        if "nominal_voltage" in b: CONF["battery"]["nominal_voltage"] = float(b["nominal_voltage"]); changed=True
        if "nominal_ah" in b:      CONF["battery"]["nominal_ah"] = int(b["nominal_ah"]); changed=True
        if "soc" in b and isinstance(b["soc"],dict):
            # Salva il metodo SOC
            if "method" in b["soc"]: 
                soc["method"] = str(b["soc"]["method"]); changed=True
        
            # Salva i campi appropriati in base al metodo
            if b["soc"].get("method") == "energy_balance":
                if "reset_voltage" in b["soc"]: 
                    soc["reset_voltage"] = float(b["soc"]["reset_voltage"]); changed=True
                # Rimuovi i campi voltage_based se esistono
                if "vmax_v" in soc: del soc["vmax_v"]
                if "vmin_v" in soc: del soc["vmin_v"]
            elif b["soc"].get("method") == "voltage_based":
                if "vmax_v" in b["soc"]: soc["vmax_v"] = float(b["soc"]["vmax_v"]); changed=True
                if "vmin_v" in b["soc"]: soc["vmin_v"] = float(b["soc"]["vmin_v"]); changed=True
                # Rimuovi i campi energy_balance se esistono
                if "reset_voltage" in soc: del soc["reset_voltage"]

        # Soglia reset net battery (configurabile)
        if "net_reset_voltage" in b:
            try:
                CONF["battery"]["net_reset_voltage"] = float(b["net_reset_voltage"])
                changed = True
            except Exception:
                pass

        ui = data.get("ui") or {}
        if "unit" in ui:
            CONF["ui"]["unit"] = "kW" if str(ui["unit"]).upper()=="KW" else "W"; changed=True

        r = data.get("relay") or {}
        if r:
            for k in ["mode","enabled","gpio_pin","active_high","on_v","off_v","min_toggle_sec"]:
                if k in r:
                    CONF["relay"][k] = r[k]; changed=True

        if data.get("persist") and changed:
            # FORZA RIMOZIONE WEBHOOK DALLA CONFIGURAZIONE
            if "relay" in CONF and "soc" in CONF.get("battery", {}):
                # Rimuovi webhook se esistono
                if "webhook_on" in CONF["relay"]:
                    del CONF["relay"]["webhook_on"]
                    changed = True
                if "webhook_off" in CONF["relay"]:
                    del CONF["relay"]["webhook_off"]
                    changed = True
        
            tmp = str(CONFIG_PATH) + ".tmp"
            with open(tmp,"w",encoding="utf-8") as f: json.dump(CONF,f,indent=2,ensure_ascii=False)
            os.replace(tmp, str(CONFIG_PATH))
            try:
                relay_setup()
            except Exception:
                pass

        return jsonify({"ok":True,"changed":changed})

    @app.route("/api/inverter")
    def inverter():
        with db() as con:
            row = con.execute("SELECT * FROM samples ORDER BY id DESC LIMIT 1").fetchone()
        db_sample = dict(row) if row else None
        mem_sample = poll_state.last_sample
        i2c_snapshot = i2c_service.LAST_I2C

        def ts_of(s):
            return parse_ts(s.get("timestamp")) if s and "timestamp" in s else None

        candidate = None
        db_ts = ts_of(db_sample)
        mem_ts = ts_of(mem_sample)
        if db_sample and (not mem_sample or (db_ts and mem_ts and db_ts >= mem_ts)):
            candidate = db_sample
        elif mem_sample:
            candidate = mem_sample

        s = candidate or {"timestamp": now_str()}
        try:
            vmax = float(_get("battery.soc.vmax_v", 58.0))
            vmin = float(_get("battery.soc.vmin_v", 44.0))
            v = float(s.get("battery_v") or 0.0)
            if vmax > vmin:
                s["soc_pct"] = round(max(0.0, min(100.0, 100.0 * (v - vmin) / (vmax - vmin))), 1)
        except Exception:
            pass

        latest_dt = ts_of(s)
        if latest_dt:
            s["stale_seconds"] = int((datetime.now() - latest_dt).total_seconds())
        s["last_ok"] = modbus_service.LAST_OK
        s["last_error"] = modbus_service.LAST_ERR

        s["relay"] = {
            "enabled": bool(_get("relay.enabled", False)),
            "state": RELAY_STATE
        }
    
        # Include last I2C snapshot if available
        if i2c_snapshot is not None:
            s["i2c"] = i2c_snapshot
    
            # Aggiungi energia netta della batteria per calcolo SOC
        try:
            # Leggi energia netta direttamente dal database
            with db() as con:
                row = con.execute("SELECT total_batt_net_Wh FROM battery_counters ORDER BY id DESC LIMIT 1").fetchone()
                if row and row[0] is not None:
                    s["battery_net_wh"] = float(row[0])
                else:
                    s["battery_net_wh"] = 0.0
        except Exception:
            s["battery_net_wh"] = 0.0

        return jsonify(s)

    # ---------------------------------------------------------------------------
    # I2C endpoints
    # ---------------------------------------------------------------------------
    @app.route("/api/i2c/latest")
    def i2c_latest():
        """Return latest I2C snapshot persisted in DB."""
        try:
            with db() as con:
                row = con.execute("SELECT timestamp, data FROM i2c_snapshots ORDER BY timestamp DESC LIMIT 1").fetchone()
            if not row:
                return jsonify({"ok": False, "error": "No I2C data"}), 404
            ts = row["timestamp"] if isinstance(row, sqlite3.Row) else row[0]
            data_txt = row["data"] if isinstance(row, sqlite3.Row) else row[1]
            try:
                payload = json.loads(data_txt) if data_txt else {}
            except Exception:
                payload = {}
            return jsonify({"ok": True, "timestamp": ts, "i2c": payload})
        except Exception as e:
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
        
            now_dt  = datetime.now()
            base_str = request.args.get("date") or now_dt.strftime("%Y-%m-%d")
            try:
                base_dt = datetime.strptime(base_str, "%Y-%m-%d")
            except Exception:
                return jsonify({"ok": False, "error": "Invalid date format"}), 400
            start = base_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end   = now_dt if base_dt.date() == now_dt.date() else start + timedelta(days=1) - timedelta(seconds=1)
            start_s = start.strftime("%Y-%m-%d %H:%M:%S")
            end_s   = end.strftime("%Y-%m-%d %H:%M:%S")

            with db() as con:
                rows = con.execute("""
                    SELECT timestamp, data FROM i2c_snapshots
                    WHERE timestamp BETWEEN ? AND ?
                    ORDER BY timestamp ASC
                """, (start_s, end_s)).fetchall()
        
            out = []
            for r in rows:
                ts  = r["timestamp"] if isinstance(r, sqlite3.Row) else r[0]
                txt = r["data"] if isinstance(r, sqlite3.Row) else r[1]
                try:
                    obj = json.loads(txt) if txt else {}
                except Exception:
                    obj = {}
                dev_map = obj.get(device)
                if not isinstance(dev_map, dict):
                    continue
                val = dev_map.get(channel)
                if val is None:
                    continue
                # Normalize value by metric
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
            return jsonify({"ok": False, "error": str(e)}), 500

    # ---------------------------------------------------------------------------
    # History / Energy / Totals
    # ---------------------------------------------------------------------------
    @app.route("/api/history")
    def history():
        now_dt  = datetime.now()
        day0_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        now_s   = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        day0_s  = day0_dt.strftime("%Y-%m-%d %H:%M:%S")

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

        agg = { r["ts_min"]: {"pv_w":r["pv_w"],"battery_w":r["battery_w"],"load_w":r["load_w"],"grid_w":r["grid_w"]} for r in rows }

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

    def _energy_window(gran: str):
        now_dt = datetime.now()
        if gran == "hour":
            base  = datetime.strptime(request.args.get("date") or now_dt.strftime("%Y-%m-%d"), "%Y-%m-%d")
            start = base.replace(hour=0, minute=0, second=0, microsecond=0)
            end   = now_dt if base.date() == now_dt.date() else start + timedelta(days=1) - timedelta(seconds=1)
            step  = "hour"
        elif gran == "day":
            base  = datetime.strptime(request.args.get("from") or now_dt.strftime("%Y-%m-%d"), "%Y-%m-%d")
            start = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if base.month == now_dt.month and base.year == now_dt.year:
                end = now_dt
            else:
                month_next = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
                end = month_next - timedelta(seconds=1)
            step  = "day"
        elif gran == "month":
            base  = datetime.strptime(request.args.get("from") or now_dt.strftime("%Y-%m-%d"), "%Y-%m-%d")
            start = base.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end   = now_dt if base.year == now_dt.year else start.replace(year=start.year+1) - timedelta(seconds=1)
            step  = "month"
        else:
            with db() as con:
                row = con.execute("SELECT MIN(strftime('%Y', timestamp)) AS y0 FROM samples").fetchone()
            y0 = int(row["y0"] or now_dt.year)
            start = datetime(y0, 1, 1)
            end   = now_dt
            step  = "year"
        return start, end, step

    @app.route("/api/energy")
    def energy():
        gran = (request.args.get("granularity") or "hour").lower()
        unit = (request.args.get("unit") or "kWh").lower()
        if unit not in {"wh","kwh"}:
            unit = "kwh"
        scale = 1.0/1000.0 if unit == "kwh" else 1.0
        suffix = "_kWh" if unit == "kwh" else "_Wh"

        start, end, step = _energy_window(gran)
        start_s = start.strftime("%Y-%m-%d %H:%M:%S")
        end_s   = end.strftime("%Y-%m-%d %H:%M:%S")

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
                        f"pv{suffix}"      : float(r["pv_Wh"] or 0.0)   * scale,
                        f"load{suffix}"    : float(r["load_Wh"] or 0.0) * scale,
                        f"grid{suffix}"    : float(r["grid_Wh"] or 0.0) * scale,
                        f"batt_in{suffix}" : bi * scale,
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

        from collections import defaultdict
        acc = defaultdict(lambda: {"pv":0.0,"load":0.0,"grid":0.0,"bi":0.0,"bo":0.0})

        for r in s_rows:
            d = r["day"]
            acc[d]["pv"]   += float(r["pv_Wh"] or 0.0)
            acc[d]["load"] += float(r["load_Wh"] or 0.0)
            acc[d]["grid"] += float(r["grid_Wh"] or 0.0)
            acc[d]["bi"]   += float(r["batt_in_Wh"] or 0.0)
            acc[d]["bo"]   += float(r["batt_out_Wh"] or 0.0)

        for r in a_rows:
            d = r["day"]
            acc[d]["pv"]   += float(r["pv_Wh"] or 0.0)
            acc[d]["load"] += float(r["load_Wh"] or 0.0)
            acc[d]["grid"] += float(r["grid_Wh"] or 0.0)
            acc[d]["bi"]   += float(r["batt_in_Wh"] or 0.0)
            acc[d]["bo"]   += float(r["batt_out_Wh"] or 0.0)

        def rec(bucket, pv, ld, gr, bi, bo):
            return {
                "bucket": bucket,
                f"pv{suffix}"      : pv * scale,
                f"load{suffix}"    : ld * scale,
                f"grid{suffix}"    : gr * scale,
                f"batt_in{suffix}" : bi * scale,
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
            from collections import defaultdict as dd
            bym = dd(lambda: {"pv":0.0,"load":0.0,"grid":0.0,"bi":0.0,"bo":0.0})
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

        else:  # year
            from collections import defaultdict as dd
            byy = dd(lambda: {"pv":0.0,"load":0.0,"grid":0.0,"bi":0.0,"bo":0.0})
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
        if unit not in {"wh","kwh"}: unit = "kwh"
        scale = 1.0/1000.0 if unit == "kwh" else 1.0
        suffix = "_kWh" if unit == "kwh" else "_Wh"

        now_dt  = datetime.now()
        day0_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        start_s = day0_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_s   = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    
        # Ottieni i contatori persistenti della batteria
        battery_counter = get_current_battery_counter()
        battery_net = 0.0
        if battery_counter:
            battery_net = float(battery_counter.get('total_batt_net_Wh', 0.0))
    
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
    
        # Usa il contatore persistente per la batteria netta
        batt_net = battery_net * scale
    
        return jsonify({
            "unit": unit,
            f"pv{suffix}"      : pv,
            f"load{suffix}"    : ld,
            f"grid{suffix}"    : gr,
            f"batt_in{suffix}" : bi,
            f"batt_out{suffix}": bo,
            f"batt_net{suffix}": batt_net,
            "battery_counter_info": {
                "start_timestamp": battery_counter.get('start_timestamp') if battery_counter else None,
                "reset_reason": battery_counter.get('reset_reason') if battery_counter else None,
                "total_batt_net_Wh": battery_counter.get('total_batt_net_Wh', 0.0) if battery_counter else 0.0
            }
        })

    @app.route("/api/maintenance/archive", methods=["POST"])
    def maintenance_archive():
        scope = (request.args.get("scope") or "").lower()
        dry_run = str(request.args.get("dry_run","")).lower() in {"1","true","yes","y"}
        vacuum  = str(request.args.get("vacuum","")).lower() in {"1","true","yes","y"}
        try:
            size_before = db_files_size_bytes()
            if scope == "upto_today":
                cutoff = datetime.now().strftime("%Y-%m-%d 00:00:00")
                summary = archive_compute_and_apply(cutoff, apply=not dry_run)
                result = {"ok": True, "scope": "upto_today", **summary, "dry_run": dry_run}
            else:
                days = max(1, min(3650, int(request.args.get("days","30"))))
                cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
                summary = archive_compute_and_apply(cutoff, apply=not dry_run)
                result = {"ok": True, "archived_days": days, **summary, "dry_run": dry_run}

            if (not dry_run) and vacuum:
                with db() as con:
                    try:
                        con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    except Exception:
                        pass
                # VACUUM deve essere eseguito fuori dalla connessione aperta
                try:
                    with sqlite3.connect(str(DB_PATH)) as c2:
                        c2.execute("VACUUM;")
                except Exception:
                    pass

            size_after = db_files_size_bytes() if not dry_run else size_before
            result["size_before_bytes"] = size_before
            result["size_after_bytes"]  = size_after
            result["size_delta_bytes"]  = size_after - size_before
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

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
            import traceback
            traceback.print_exc()
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
                    "id": counter.get('id'),
                    "start_timestamp": counter.get('start_timestamp'),
                    "start_battery_v": counter.get('start_battery_v'),
                    "total_batt_in_Wh": counter.get('total_batt_in_Wh', 0.0),
                    "total_batt_out_Wh": counter.get('total_batt_out_Wh', 0.0),
                    "total_batt_net_Wh": counter.get('total_batt_net_Wh', 0.0),
                    "reset_reason": counter.get('reset_reason'),
                    "created_at": counter.get('created_at')
                }
            })
        except Exception as e:
            logger.exception("battery error in battery_status: %s", e)
            import traceback
            traceback.print_exc()
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

    @app.route("/api/test")
    def test_endpoint():
        """Endpoint di test generale per verificare se Flask funziona"""
        logger.info("general test endpoint called")
        return jsonify({
            "ok": True,
            "message": "General test endpoint working",
            "timestamp": now_str(),
            "flask_version": "working"
        })

    # ---------------------------------------------------------------------------
    # Relay manual endpoints
    # ---------------------------------------------------------------------------
    @app.route("/api/relay/on", methods=["POST"])
    def relay_on():
        cfg = CONF.get("relay", {})
        relay_apply(True)
        return jsonify({"ok": True, "relay": "on"})

    @app.route("/api/relay/off", methods=["POST"])
    def relay_off():
        cfg = CONF.get("relay", {})
        relay_apply(False)
        return jsonify({"ok": True, "relay": "off"})

    @app.route("/api/relay/state", methods=["GET", "POST"])
    def relay_state():
        try:
            cfg = CONF.get("relay", {})
            pin = int(cfg.get("gpio_pin", 17))
        
            # Debug logging
            logger.info(
                "relay STATE request enabled=%s mode=%s pin=%s RELAY_STATE=%s",
                cfg.get("enabled"),
                cfg.get("mode"),
                pin,
                RELAY_STATE,
            )
        
            # Verifica se il relay e' abilitato
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
        
            # Leggi stato GPIO
            try:
                level = _gpio_read(pin)
                logger.info("relay GPIO read pin %s level=%s", pin, level)
            except Exception as e:
                level = None
                logger.warning("relay GPIO read error pin %s: %s", pin, e)
        
            # Se RELAY_STATE e' None, prova a determinarlo dal GPIO
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
    logger.info("API routes registered")
