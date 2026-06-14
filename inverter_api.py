#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flask API entrypoint per il monitor inverter.
Refactor 2026-06-06: config/DB/hardware estratti in config.py, database.py, hardware.py.
Qui restano: app Flask, hook after_request, poll_loop, route, main().
Lo stato modbus/relè vive in hardware (hardware.LAST_ERR / hardware.LAST_OK / hardware.RELAY_STATE).
"""
from pathlib import Path
import os, json, time, sqlite3, contextlib, signal
from datetime import datetime, timedelta
from threading import Thread, Event, Lock
from typing import Dict, Any, Optional, Tuple, List
from flask import Flask, jsonify, send_from_directory, request, render_template

try:
    from flask_compress import Compress  # Optional response compression
except Exception:
    Compress = None  # type: ignore

# --- moduli estratti dal monolite ---
import config
import database
import hardware
from config import (
    BASE_DIR, WEB_DIR, CFG_DIR, DATA_DIR, CONFIG_PATH, DB_PATH, PORT, CONF,
    MB_PORT, MB_BAUD, MB_PARITY, MB_STOP, MB_BYTES, MB_TIMEOUT, UNIT_ID,
    POLL_S, DEFAULT_NET_RESET_V, I2C_ENABLED, I2C_BUS, I2C_DEVICES,
    REGS, SIGNED, _load_json, _get, _bool, ev, now_str, parse_ts,
)
from database import (
    db, db_init,
    _db_files_size_bytes, _archive_compute_and_apply,
    get_current_battery_counter, reset_battery_counter, set_battery_full,
    update_battery_counter, check_battery_reset_condition, check_battery_full_condition,
)
from hardware import (
    i2c_read_all, read_regs, _blocks, _to_signed16,
    relay_apply, relay_setup, relay_auto_step,
    _gpio_setup_output, _gpio_write, _gpio_read, _gpio_cleanup, GPIO_BACKEND,
    balance_setup, balance_set, balance_manual, balance_step, balance_status,
)

print(f"[startup] Flask imported successfully", flush=True)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=None, template_folder=str(WEB_DIR))
if Compress:
    try:
        Compress(app)
        print(f"[startup] Flask-Compress enabled", flush=True)
    except Exception as _e:
        print(f"[startup] Flask-Compress not enabled: {_e}", flush=True)
print(f"[startup] Flask app created successfully", flush=True)

# Stato runtime: snapshot ultimo campione (scritto da poll_loop, letto dalle route)
_stop = Event()
_lock = Lock()
_last: Optional[Dict[str, Any]] = None
LAST_I2C: Optional[Dict[str, Any]] = None


@app.after_request
def set_cache_headers(resp):
    """No-cache per /api/, cache lunga per asset statici."""
    try:
        path = request.path or ""
    except Exception:
        path = ""
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    else:
        static_exts = (".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico", ".webmanifest")
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


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------
def poll_loop():
    global _last, LAST_I2C
    next_t = time.monotonic()
    while not _stop.is_set():
        try:
            with _lock:
                regs = read_regs()
                ts_now = now_str()

                # Read I2C snapshot regardless of Modbus success
                i2c_snapshot = None
                try:
                    i2c_snapshot = i2c_read_all()
                    LAST_I2C = i2c_snapshot
                except Exception:
                    LAST_I2C = None

                # Bilanciamento banchi (SERIE1/SERIE2 da I2C; gira ogni ciclo, indip. dal Modbus)
                try:
                    balance_step(LAST_I2C)
                except Exception as _be:
                    print(f"[balance] step error: {_be}", flush=True)

                if regs:
                    s = {"timestamp": ts_now, **regs}
                    # grid_a
                    gv = float(s.get("grid_v") or 0.0)
                    gw = float(s.get("grid_w") or 0.0)
                    s["grid_a"] = (gw / gv) if gv else 0.0
                    # load_pf
                    try:
                        lw  = float(s.get("load_w") or 0.0)
                        lva = float(s.get("load_va") or 0.0)
                        pf  = s.get("load_pf")
                        if (pf is None) or (float(pf or 0.0) <= 0.0):
                            val = (abs(lw)/abs(lva)) if abs(lva) > 1e-6 else None
                            s["load_pf"] = None if val is None else max(0.0, min(1.0, val))
                    except Exception:
                        pass
                    _last = s
                    
                    with db() as con:
                        con.execute("""
                            INSERT OR IGNORE INTO samples(timestamp,
                              pv_w,pv_v,pv_a,
                              battery_w,battery_v,battery_a,
                              grid_w,grid_v,grid_hz,grid_a,
                              load_w,load_v,load_hz,load_a,load_va,load_pf,load_percent,
                              dc_temp,inverter_temp,heatsink_temp,dc_bus_v)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (
                            s["timestamp"],
                            s.get("pv_w"), s.get("pv_v"), s.get("pv_a"),
                            s.get("battery_w"), s.get("battery_v"), s.get("battery_a"),
                            s.get("grid_w"), s.get("grid_v"), s.get("grid_hz"), s.get("grid_a"),
                            s.get("load_w"), s.get("load_v"), s.get("load_hz"), s.get("load_a"), s.get("load_va"), s.get("load_pf"), s.get("load_percent"),
                            s.get("dc_temp"), s.get("inverter_temp"), s.get("heatsink_temp"), s.get("dc_bus_v")
                        ))
                        if i2c_snapshot is not None:
                            con.execute(
                                "INSERT OR REPLACE INTO i2c_snapshots(timestamp, data) VALUES (?, ?)",
                                (ts_now, json.dumps(i2c_snapshot, ensure_ascii=False))
                            )
                        con.commit()

                    # Relay auto control
                    try:
                        batt_v = None
                        if "battery_v" in s and s["battery_v"] is not None:
                            batt_v = float(s["battery_v"])
                        relay_auto_step(batt_v)
                    except Exception:
                        pass
                    
                    # Battery counter management
                    try:
                        battery_w = s.get("battery_w")
                        battery_v = s.get("battery_v")
                        
                        # Controlla se e' necessario azzerare il contatore
                        if check_battery_reset_condition(battery_v, battery_w):
                            pass  # Reset automatico silenzioso
                        
                        # Aggiorna sempre il contatore corrente
                        update_battery_counter(battery_w, battery_v)

                        # Auto-calibrazione PIENO (regola fisica + plausibilita' PV/giorno)
                        check_battery_full_condition(battery_v, s.get("battery_a"), s.get("pv_w"))
                    except Exception as e:
                        print(f"[battery] Errore aggiornamento contatore: {e}", flush=True)
                else:
                    if i2c_snapshot is not None:
                        try:
                            with db() as con:
                                con.execute(
                                    "INSERT OR REPLACE INTO i2c_snapshots(timestamp, data) VALUES (?, ?)",
                                    (ts_now, json.dumps(i2c_snapshot, ensure_ascii=False))
                                )
                                con.commit()
                        except Exception:
                            pass
                    if _last is None:
                        _last = {"timestamp": ts_now}
        except Exception:
            pass
        next_t += POLL_S
        time.sleep(max(0.0, next_t - time.monotonic()))

# ---------------------------------------------------------------------------
# Daily Analysis System
# ---------------------------------------------------------------------------
from daily_analyzer import DailyAnalyzer

# Inizializza analizzatore giornaliero
daily_analyzer = DailyAnalyzer(str(DB_PATH))  # pin al DB assoluto (indipendente dal CWD)

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
                "autonomy_pct": analysis.get("summary", {}).get("autonomy_pct"),
                "pv_energy": analysis.get("summary", {}).get("pv_production_kwh", 0),
                "grid_import": analysis.get("summary", {}).get("grid_import_kwh", 0),
                "anomalies": analysis.get("diagnostics", {}).get("total_anomalies", 0)
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
                            "pv_energy": analysis.get("summary", {}).get("pv_production_kwh", 0)
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

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
@app.route("/")
def root():
    return render_template("index.html")

@app.route("/settings")
def settings_page():
    return render_template("settings.html")

@app.route("/analysis")
def analysis_page():
    return render_template("analysis_dashboard.html")

@app.route("/battery")
def battery_page():
    return render_template("battery.html")

@app.route("/solar")
def solar_page():
    return render_template("solar.html")

@app.route("/grid-home")
def grid_home_page():
    return render_template("grid_home.html")

@app.route("/history")
def history_page():
    return render_template("history.html")

@app.route("/diagnostics")
def diagnostics_page():
    return render_template("diagnostics.html")

@app.route("/main.css")
def main_css():
    return send_from_directory(str(WEB_DIR), "main.css", mimetype="text/css")

@app.route("/app.mod.js")
def app_js():
    return send_from_directory(str(WEB_DIR), "app.mod.js", mimetype="text/javascript")

@app.route("/chart.umd.min.js")
def chart_js():
    # Chart.js vendored locally so the dashboard renders offline (no CDN needed)
    return send_from_directory(str(WEB_DIR), "chart.umd.min.js", mimetype="text/javascript")

@app.route("/settings.mod.js")
def settings_js():
    return send_from_directory(str(WEB_DIR), "settings.mod.js", mimetype="text/javascript")

@app.route("/status.mod.js")
def status_js():
    return send_from_directory(str(WEB_DIR), "status.mod.js", mimetype="text/javascript")

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
    last_dt = parse_ts(db_last) or parse_ts(hardware.LAST_OK)
    stale_seconds = None
    if last_dt:
        stale_seconds = int((datetime.now() - last_dt).total_seconds())

    relay_cfg = CONF.get("relay", {})
    return jsonify({
        "status": "ok",
        "last_ok": hardware.LAST_OK,
        "last_error": hardware.LAST_ERR,
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
            "state": hardware.RELAY_STATE
        }
    })

def validate_config(data: dict) -> Tuple[bool,str]:
    try:
        b = data.get("battery", {})
        if "nominal_voltage" in b:
            v = float(b["nominal_voltage"])
            if not (10.0 <= v <= 100.0):
                return False, "Voltage out of range (10-100 V)"
        if "nominal_ah" in b:
            a = int(b["nominal_ah"])
            if not (1 <= a <= 2000):
                return False, "Capacity out of range (1-2000 Ah)"
        # Validazione soglia reset net battery (opzionale)
        if "net_reset_voltage" in b:
            try:
                rv = float(b["net_reset_voltage"])
            except Exception:
                return False, "net_reset_voltage must be a number"
            if not (30.0 <= rv <= 70.0):
                return False, "net_reset_voltage out of range (30-70 V)"
        if "soc" in b and isinstance(b["soc"], dict):
            soc_method = b["soc"].get("method", "voltage_based")
            
            if soc_method == "energy_balance":
                # Per il metodo energetico, valida solo reset_voltage
                reset_voltage = b["soc"].get("reset_voltage")
                # reset_voltage opzionale per energy_balance (SOC = energia netta/capacita'; soglia a vuoto = net_reset_voltage)
                
                # Valida che reset_voltage sia nel range 80-90% della tensione nominale
                nominal_v = float(b.get("nominal_voltage", 48))
                min_reset = nominal_v * 0.8
                max_reset = nominal_v * 0.9
                
                if reset_voltage is not None and not (min_reset <= float(reset_voltage) <= max_reset):
                    return False, f"SOC reset_voltage must be between {min_reset:.1f}V and {max_reset:.1f}V (80-90% of nominal voltage)"
                    
            elif soc_method == "voltage_based":
                # Per il metodo basato su tensione, valida vmax > vmin
                vmin = float(b["soc"].get("vmin_v", 0))
                vmax = float(b["soc"].get("vmax_v", 0))
                if vmax <= vmin:
                    return False, "SOC vmax must be > vmin"
            else:
                return False, f"Unknown SOC method: {soc_method}"

        ui = data.get("ui", {})
        if "unit" in ui and str(ui["unit"]).upper() not in {"W","KW"}:
            return False, "Unit must be W or kW"

        r = data.get("relay", {})
        if r:
            if "mode" in r and str(r["mode"]).lower() not in {"gpio"}:
                return False, "Relay mode must be 'gpio'"
            if "gpio_pin" in r:
                pin = int(r["gpio_pin"])
                if pin < 0 or pin > 27:
                    return False, "Relay gpio_pin must be a valid BCM pin (0..27)"
            if "on_v" in r and "off_v" in r:
                on_v  = float(r["on_v"])
                off_v = float(r["off_v"])
                if off_v <= on_v:
                    return False, "Relay off_v must be > on_v"
            if "min_toggle_sec" in r:
                mts = int(r["min_toggle_sec"])
                if mts < 0 or mts > 86400:
                    return False, "Relay min_toggle_sec out of range"
        return True, ""
    except Exception as e:
        return False, f"Invalid JSON or types: {e}"

@app.route("/api/config", methods=["GET","POST"])
def config():
    if request.method=="GET":
        try:
            has_i2c = "i2c" in CONF and isinstance(CONF.get("i2c"), dict)
            print(f"[config] GET /api/config -> i2c present={has_i2c}", flush=True)
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
    mem_sample = _last
    i2c_snapshot = LAST_I2C

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

    # Energia netta batteria (Wh) dal contatore — base del SOC a bilancio energetico
    try:
        with db() as con:
            row = con.execute("SELECT total_batt_net_Wh FROM battery_counters ORDER BY id DESC LIMIT 1").fetchone()
        s["battery_net_wh"] = float(row[0]) if (row and row[0] is not None) else 0.0
    except Exception:
        s["battery_net_wh"] = 0.0

    # SOC: bilancio energetico (coulomb) se configurato, altrimenti basato su tensione
    try:
        method = str(_get("battery.soc.method", "voltage_based"))
        if method == "energy_balance":
            cap_wh = float(_get("battery.nominal_ah", 500)) * float(_get("battery.nominal_voltage", 51.2))
            if cap_wh > 0:
                s["soc_pct"] = round(max(0.0, min(100.0, 100.0 * s["battery_net_wh"] / cap_wh)), 1)
        else:
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
    s["last_ok"] = hardware.LAST_OK
    s["last_error"] = hardware.LAST_ERR

    s["relay"] = {
        "enabled": bool(_get("relay.enabled", False)),
        "state": hardware.RELAY_STATE
    }
    
    # Include last I2C snapshot if available
    if i2c_snapshot is not None:
        s["i2c"] = i2c_snapshot
    
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
        size_before = _db_files_size_bytes()
        if scope == "upto_today":
            cutoff = datetime.now().strftime("%Y-%m-%d 00:00:00")
            summary = _archive_compute_and_apply(cutoff, apply=not dry_run)
            result = {"ok": True, "scope": "upto_today", **summary, "dry_run": dry_run}
        else:
            days = max(1, min(3650, int(request.args.get("days","30"))))
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
            summary = _archive_compute_and_apply(cutoff, apply=not dry_run)
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

        size_after = _db_files_size_bytes() if not dry_run else size_before
        result["size_before_bytes"] = size_before
        result["size_after_bytes"]  = size_after
        result["size_delta_bytes"]  = size_after - size_before
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/battery/reset", methods=["POST"])
def battery_reset():
    """Endpoint per resettare manualmente il contatore della batteria"""
    print(f"[battery] RESET request received", flush=True)
    try:
        print(f"[battery] Request JSON: {request.json}", flush=True)
        reason = request.json.get("reason", "manual") if request.json else "manual"
        print(f"[battery] Reset reason: {reason}", flush=True)
        
        print(f"[battery] Calling reset_battery_counter()", flush=True)
        counter_id = reset_battery_counter(reason)
        print(f"[battery] Reset completed, new counter ID: {counter_id}", flush=True)
        
        return jsonify({
            "ok": True,
            "message": "Contatore batteria azzerato",
            "new_counter_id": counter_id,
            "reason": reason
        })
    except Exception as e:
        print(f"[battery] Error in battery_reset: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/battery/full", methods=["POST"])
def battery_full():
    """Calibra la batteria al 100% (SOC pieno): contatore netto = capacita' nominale."""
    try:
        counter_id = set_battery_full("full_charge")
        return jsonify({"ok": True, "message": "Batteria segnata come carica (100%)", "new_counter_id": counter_id})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/battery/autocal", methods=["GET", "POST"])
def battery_autocal():
    """Get/Set abilitazione auto-calibrazione SOC al pieno (+ soglie correnti)."""
    ac = CONF.setdefault("battery", {}).setdefault("autocal", {})
    if request.method == "POST":
        try:
            data = request.json or {}
            if "enabled" in data:
                ac["enabled"] = bool(data["enabled"])
                tmp = str(CONFIG_PATH) + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(CONF, f, indent=2, ensure_ascii=False)
                os.replace(tmp, str(CONFIG_PATH))
            return jsonify({"ok": True, "enabled": bool(ac.get("enabled", True))})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({
        "ok": True,
        "enabled": _bool(ac.get("enabled", True), True),
        "full_voltage": float(_get("battery.autocal.full_voltage", _get("battery.soc.vmax_v", 54.0)) or 54.0),
        "tail_current_a": float(_get("battery.autocal.tail_current_a", 5.0)),
        "hold_seconds": float(_get("battery.autocal.hold_seconds", 600)),
        "min_pv_w": float(_get("battery.autocal.min_pv_w", 50.0)),
    })

@app.route("/api/balance", methods=["GET", "POST"])
def balance_api():
    """Stato e controllo del bilanciamento banchi (toggle abilita + test manuale relè)."""
    bc = CONF.setdefault("balance", {})
    if request.method == "POST":
        try:
            data = request.json or {}
            persist = False
            if "enabled" in data:
                bc["enabled"] = bool(data["enabled"]); persist = True
                if not bc["enabled"]:
                    balance_set(0)
            if "manual_bank" in data:
                mb = int(data["manual_bank"])
                if mb in (0, 1, 2):
                    balance_manual(mb, float(data.get("seconds", 30)))
            if persist:
                tmp = str(CONFIG_PATH) + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(CONF, f, indent=2, ensure_ascii=False)
                os.replace(tmp, str(CONFIG_PATH))
            return jsonify({"ok": True, **balance_status(LAST_I2C)})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, **balance_status(LAST_I2C)})

@app.route("/api/battery/status")
def battery_status():
    """Endpoint per ottenere lo stato del contatore della batteria"""
    print(f"[battery] STATUS request received", flush=True)
    try:
        print(f"[battery] Calling get_current_battery_counter()", flush=True)
        counter = get_current_battery_counter()
        print(f"[battery] Counter result: {counter}", flush=True)
        
        if not counter:
            print(f"[battery] No counter found, returning 404", flush=True)
            return jsonify({"ok": False, "error": "Contatore non trovato"}), 404
        
        print(f"[battery] Returning counter data", flush=True)
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
        print(f"[battery] Error in battery_status: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/test")
def test_endpoint():
    """Endpoint di test generale per verificare se Flask funziona"""
    print(f"[startup] General test endpoint called", flush=True)
    return jsonify({
        "ok": True,
        "message": "General test endpoint working",
        "timestamp": now_str(),
        "flask_version": "working"
    })

print(f"[startup] General test endpoint registered", flush=True)

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
        print(f"[relay] STATE request: enabled={cfg.get('enabled')}, mode={cfg.get('mode')}, pin={pin}, hardware.RELAY_STATE={hardware.RELAY_STATE}", flush=True)
        
        # Verifica se il relay e' abilitato
        if not cfg.get("enabled", False):
            return jsonify({
                "ok": True,
                "enabled": False,
                "mode": str(cfg.get("mode", "gpio")),
                "gpio_pin": pin,
                "active_high": bool(cfg.get("active_high", True)),
                "state": hardware.RELAY_STATE,
                "gpio_level": None,
                "message": "Relay disabilitato"
            })
        
        # Leggi stato GPIO
        try:
            level = _gpio_read(pin)
            print(f"[relay] GPIO read pin {pin}: level={level}", flush=True)
        except Exception as e:
            level = None
            print(f"[relay] Errore lettura GPIO pin {pin}: {e}", flush=True)
        
        # Se hardware.RELAY_STATE e' None, prova a determinarlo dal GPIO
        current_state = hardware.RELAY_STATE
        if current_state is None and level is not None:
            active_high = bool(cfg.get("active_high", True))
            current_state = (level == 1) if active_high else (level == 0)
            print(f"[relay] hardware.RELAY_STATE inferito da GPIO: level={level}, active_high={active_high} -> state={current_state}", flush=True)
        
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
        print(f"[relay] Errore endpoint relay_state: {e}", flush=True)
        return jsonify({
            "ok": False,
            "error": f"Errore lettura stato relay: {str(e)}"
        }), 500

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    print(f"[main] Starting inverter service...", flush=True)
    
    try:
        print(f"[main] Calling db_init()...", flush=True)
        db_init()
        print(f"[main] db_init() completed", flush=True)
    except Exception as e:
        print(f"[main] ERROR in db_init(): {e}", flush=True)
        import traceback
        traceback.print_exc()
        return
    
    try:
        print(f"[main] Calling relay_setup()...", flush=True)
        relay_setup()
        print(f"[main] relay_setup() completed", flush=True)
        try:
            balance_setup()
            print(f"[main] balance_setup() completed", flush=True)
        except Exception as _be:
            print(f"[main] ERROR in balance_setup(): {_be}", flush=True)
    except Exception as e:
        print(f"[main] ERROR in relay_setup(): {e}", flush=True)
        import traceback
        traceback.print_exc()
        return
    
    try:
        print(f"[main] Starting poll_loop thread...", flush=True)
        t = Thread(target=poll_loop, daemon=True)
        t.start()
        print(f"[main] poll_loop thread started", flush=True)
    except Exception as e:
        print(f"[main] ERROR starting poll_loop: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return
    
    try:
        print(f"[main] Setting up signal handlers...", flush=True)
        def _stop_sig(*_a): _stop.set()
        signal.signal(signal.SIGTERM, _stop_sig)
        signal.signal(signal.SIGINT,  _stop_sig)
        import atexit
        atexit.register(_gpio_cleanup)
        print(f"[main] Signal handlers configured", flush=True)
    except Exception as e:
        print(f"[main] ERROR setting up signal handlers: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return
    
    try:
        print(f"[main] Starting Flask app on {PORT}...", flush=True)
        app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)
    except Exception as e:
        print(f"[main] ERROR starting Flask: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return

if __name__ == "__main__":
    print(f"[startup] Script started, calling main()...", flush=True)
    main()
    print(f"[startup] main() returned", flush=True)
