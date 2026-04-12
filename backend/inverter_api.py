#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Flask API for inverter with:
# - Modbus read and SQLite storage
# - /api/inverter (latest sample with SOC and PF)
# - /api/history  (today minute-avg)
# - /api/energy   (hour/day/month/year aggregates)
# - /api/totals/today (daily totals)
# - Config validation + unique index on timestamp + archive/trim
# - Relay control on Raspberry Pi GPIO 17 (physical pin 11) with hysteresis
#   and manual endpoints /api/relay/on, /api/relay/off, /api/relay/state
# - Force utf-8 charset on text responses

from pathlib import Path
import os, json, time, sqlite3, contextlib, signal, sys
from datetime import datetime, timedelta
from threading import Thread, Event, Lock
from typing import Dict, Any, Optional, Tuple, List
from flask import Flask, jsonify, send_from_directory, request
try:
    from flask_compress import Compress  # Optional response compression
except Exception:
    Compress = None  # type: ignore

# Optional I2C (SMBus) support
try:
    from smbus2 import SMBus, i2c_msg  # type: ignore
except Exception:
    SMBus = None  # type: ignore
    i2c_msg = None  # type: ignore
print(f"[startup] Flask imported successfully", flush=True)

# ---------------------------------------------------------------------------
# Optional Modbus client (pymodbus >=3 or legacy)
# ---------------------------------------------------------------------------
try:
    from pymodbus.client import ModbusSerialClient  # pymodbus >= 3
except Exception:
    try:
        from pymodbus.client.sync import ModbusSerialClient  # legacy
    except Exception:
        ModbusSerialClient = None  # type: ignore

# Optional minimalmodbus fallback
try:
    import minimalmodbus as _MINIMODBUS  # type: ignore
except Exception:
    _MINIMODBUS = None  # type: ignore

# ---------------------------------------------------------------------------
# Paths (repo root = parent of backend/)
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent
BASE_DIR     = _BACKEND_DIR.parent
WEB_DIR      = _BACKEND_DIR / "web"
CFG_DIR      = BASE_DIR / "config"
DATA_DIR     = BASE_DIR / "data"

PORT = int(os.getenv("PORT", "8000"))

CONFIG_PATH = Path(os.getenv("INVERTER_CONFIG", CFG_DIR / "inverter_config.json"))
if not CONFIG_PATH.exists():
    # Ensure default config exists in config directory (cross-platform)
    try:
        CFG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    with contextlib.suppress(Exception):
        default_cfg = CFG_DIR / "inverter_config.json"
        if not default_cfg.exists():
            default_cfg.write_text("{}", encoding="utf-8")
    CONFIG_PATH = CFG_DIR / "inverter_config.json"

DB_PATH = DATA_DIR / "inverter_history.db"

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def _load_json(path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

CONF: Dict[str, Any] = _load_json(CONFIG_PATH)
print(f"[config] Loaded from {CONFIG_PATH}", flush=True)
print(f"[config] Keys: {list(CONF.keys())}", flush=True)
if "i2c" in CONF:
    try:
        devices = CONF.get("i2c", {}).get("devices", [])
        print(f"[config] I2C enabled={CONF.get('i2c', {}).get('enabled')} devices={len(devices)}", flush=True)
    except Exception as e:
        print(f"[config] Error inspecting i2c config: {e}", flush=True)

# PULIZIA IMMEDIATA WEBHOOK OBSOLETI
if "relay" in CONF:
    if "webhook_on" in CONF["relay"]:
        del CONF["relay"]["webhook_on"]
        print("[startup] Rimosso webhook_on obsoleto", flush=True)
    if "webhook_off" in CONF["relay"]:
        del CONF["relay"]["webhook_off"]
        print("[startup] Rimosso webhook_off obsoleto", flush=True)

def _get(path: str, default=None):
    cur = CONF
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur

def _bool(x, default=False):
    if isinstance(x, bool):
        return x
    s = str(x).lower()
    if s in {"1","true","y","yes","on"}:  return True
    if s in {"0","false","n","no","off"}: return False
    return default

def ev(env: str, path: str, default, typ):
    if env in os.environ:
        return typ(os.environ[env]) if typ is not bool else _bool(os.environ[env], default)
    v = _get(path, default)
    return typ(v) if typ is not bool else _bool(v, default)

# ---------------------------------------------------------------------------
# Serial / Modbus config
# ---------------------------------------------------------------------------
MB_PORT    = ev("INVERTER_MODBUS_SERIAL_PORT", "serial.port", "/dev/serial0", str)
MB_BAUD    = ev("INVERTER_MODBUS_BAUDRATE",    "serial.baudrate", 9600, int)
MB_PARITY  = ev("INVERTER_MODBUS_PARITY",      "serial.parity", "N", str)
MB_STOP    = ev("INVERTER_MODBUS_STOPBITS",    "serial.stopbits", 1, int)
MB_BYTES   = ev("INVERTER_MODBUS_BYTESIZE",    "serial.bytesize", 8, int)
MB_TIMEOUT = ev("INVERTER_MODBUS_TIMEOUT",     "serial.timeout", 1.0, float)
UNIT_ID    = ev("INVERTER_UNIT_ID",            "serial.unit_id", 1, int)
POLL_S     = ev("POLL_INTERVAL_SEC",           "polling.interval_sec", 5, float)

# Battery net counter reset default voltage (configurable)
DEFAULT_NET_RESET_V = 46.0

# ---------------------------------------------------------------------------
# I2C config
# ---------------------------------------------------------------------------
I2C_ENABLED: bool = ev("I2C_ENABLED", "i2c.enabled", False, bool)
I2C_BUS: int = ev("I2C_BUS", "i2c.bus", 1, int)
I2C_DEVICES = _get("i2c.devices", []) or []  # list of {name, address, reads:[{name, reg, len?, type?}]}

# Last I2C snapshot (not persisted)
LAST_I2C: Optional[Dict[str, Any]] = None

# Basic I2C reader supporting byte/word/block reads
def i2c_read_all() -> Optional[Dict[str, Any]]:
    if not I2C_ENABLED:
        return None
    if SMBus is None:
        return {"error": "smbus2 not available"}
    if not isinstance(I2C_DEVICES, list) or not I2C_DEVICES:
        return {}
    try:
        out: Dict[str, Any] = {}
        with SMBus(int(I2C_BUS)) as bus:
            for dev in I2C_DEVICES:
                try:
                    device_name = str(dev.get("name") or f"dev_{dev.get('address')}")
                    addr = int(dev.get("address"))
                    dev_type = str(dev.get("type") or "").lower()
                    vals: Dict[str, Any] = {}
                    if dev_type == "ads1115":
                        # Script-based approach: fixed config words per channel, 100ms wait, 4.096V scale
                        channels_cfg = {
                            0: 0xC183,  # A0
                            1: 0xD383,  # A1
                            2: 0xE383,  # A2
                            3: 0xF383   # A3
                        }
                        channels = dev.get("channels") or [{"index":0,"name":"A0"},{"index":1,"name":"A1"},{"index":2,"name":"A2"},{"index":3,"name":"A3"}]
                        tmp_measurements: Dict[str, Dict[str, Any]] = {}
                        for ch in channels:
                            try:
                                ch_idx = int(ch.get("index") if "index" in ch else ch.get("mux", 0))
                                ch_name = str(ch.get("name") or f"A{ch_idx}")
                                shunt = ch.get("shunt_ohms")
                                cfg = int(channels_cfg.get(ch_idx, 0xC183))
                                # Write config, wait conversion complete
                                bus.write_i2c_block_data(addr, 0x01, [(cfg >> 8) & 0xFF, cfg & 0xFF])
                                time.sleep(0.1)
                                # Read conversion register
                                data = bus.read_i2c_block_data(addr, 0x00, 2)
                                raw = (int(data[0]) << 8) | int(data[1])
                                if raw > 32767:
                                    raw -= 65535
                                volts = raw * (4.096 / 32768.0)  # tested script scale
                                mv = volts * 1000.0
                                amp_per_mv = ch.get("amp_per_mv")
                                mv_per_amp = ch.get("mv_per_amp")
                                voltage_scale = ch.get("voltage_scale")
                                display_unit = ch.get("display_unit")
                                divider_top = ch.get("divider_top_ohm")
                                divider_bottom = ch.get("divider_bottom_ohm")
                                subtract_channel = ch.get("subtract_channel")
                                display_value: Optional[float] = None
                                display_unit_val: Optional[str] = display_unit

                                current_a: Optional[float] = None
                                if amp_per_mv not in (None, ""):
                                    try:
                                        amp_factor = float(amp_per_mv)
                                        current_a = mv * amp_factor
                                    except Exception:
                                        current_a = None
                                elif mv_per_amp not in (None, ""):
                                    try:
                                        mv_per_amp_val = float(mv_per_amp)
                                        if mv_per_amp_val != 0:
                                            current_a = mv / mv_per_amp_val
                                    except Exception:
                                        current_a = None
                                elif shunt is not None:
                                    try:
                                        sh = float(shunt)
                                        current_a = volts / sh if sh > 0 else None
                                    except Exception:
                                        current_a = None

                                scaled_v: Optional[float] = None
                                if voltage_scale not in (None, ""):
                                    try:
                                        factor = float(voltage_scale)
                                        scaled_v = volts * factor
                                    except Exception:
                                        scaled_v = None
                                elif divider_top not in (None, "") and divider_bottom not in (None, ""):
                                    try:
                                        top_val = float(divider_top)
                                        bottom_val = float(divider_bottom)
                                        if bottom_val > 0:
                                            ratio = (top_val + bottom_val) / bottom_val
                                            scaled_v = volts * ratio
                                    except Exception:
                                        scaled_v = None

                                if current_a is not None:
                                    display_value = current_a
                                    display_unit_val = display_unit_val or "A"
                                elif scaled_v is not None:
                                    display_value = scaled_v
                                    display_unit_val = display_unit_val or "V"
                                else:
                                    display_value = mv
                                    display_unit_val = display_unit_val or "mV"

                                entry: Dict[str, Any] = {
                                    "raw_v": round(volts, 6),
                                    "raw_mv": round(mv, 3),
                                    "value": round(display_value, 3) if display_value is not None else None,
                                    "unit": display_unit_val,
                                    "mv": round(mv, 3)
                                }
                                if current_a is not None:
                                    entry["current_a"] = round(current_a, 3)
                                if scaled_v is not None:
                                    entry["scaled_v"] = round(scaled_v, 3)
                                if subtract_channel:
                                    entry["subtract_channel"] = subtract_channel
                                tmp_measurements[ch_name] = entry
                            except Exception:
                                vals[str(ch.get("name") or f"A{ch.get('index',0)}")] = None
                        # Post-process subtract_channel dependencies (es. SERIE2 - SERIE1)
                        for ch_name, entry in tmp_measurements.items():
                            subtract_name = entry.get("subtract_channel")
                            if not subtract_name:
                                vals[ch_name] = entry
                                continue

                            ref = tmp_measurements.get(str(subtract_name))
                            if not ref:
                                vals[ch_name] = entry
                                continue

                            try:
                                base_v = entry.get("scaled_v")
                                ref_v  = ref.get("scaled_v")
                                if base_v is None or ref_v is None:
                                    vals[ch_name] = entry
                                    continue

                                diff = round(float(base_v) - float(ref_v), 3)
                                entry["scaled_v"] = diff
                                entry["value"] = diff
                                entry["unit"] = entry.get("unit") or "V"
                            except Exception:
                                pass

                            entry.pop("subtract_channel", None)
                            vals[ch_name] = entry

                        device_vals = vals
                    else:
                        reads = dev.get("reads") or []
                        for r in reads:
                            try:
                                key = str(r.get("name") or f"reg_{r.get('reg')}")
                                reg = int(r.get("reg"))
                                typ = str(r.get("type") or "byte").lower()
                                ln  = int(r.get("len") or 1)
                                if typ == "byte":
                                    vals[key] = int(bus.read_byte_data(addr, reg))
                                elif typ == "word":
                                    data = bus.read_i2c_block_data(addr, reg, 2)
                                    vals[key] = (int(data[0]) << 8) | int(data[1])
                                elif typ == "block":
                                    ln = max(1, min(32, ln))
                                    data = bus.read_i2c_block_data(addr, reg, ln)
                                    vals[key] = list(map(int, data))
                                else:
                                    ln = max(1, min(32, ln))
                                    data = bus.read_i2c_block_data(addr, reg, ln)
                                    vals[key] = list(map(int, data))
                            except Exception:
                                vals[str(r.get("name") or f"reg_{r.get('reg')}")] = None
                        device_vals = vals
                    out[device_name] = device_vals
                except Exception as e:
                    out[str(dev.get("name") or f"dev_{dev.get('address')}")] = {"error": str(e)}
        return out
    except Exception as e:
        return {"error": str(e)}

# ---------------------------------------------------------------------------
# Registers map (name, address, scale)
# ---------------------------------------------------------------------------
REGS: Tuple[Tuple[str,int,float], ...] = (
    ("battery_a", 216, 0.1),
    ("battery_v", 215, 0.1),
    ("battery_w", 217, 1),
    ("dc_temp", 226, 1),
    ("grid_hz", 203, 0.01),
    ("grid_v", 202, 0.1),
    ("grid_w", 204, 1),
    ("heatsink_temp", 228, 1),
    ("inverter_temp", 227, 1),
    ("dc_bus_v", 218, 0.1),
    ("load_v", 210, 0.1),
    ("load_a", 211, 0.1),
    ("load_hz", 212, 0.01),
    ("load_w", 213, 1),
    ("load_va", 214, 1),
    ("load_percent", 225, 1),
    ("pv_a", 220, 0.1),
    ("pv_v", 219, 0.1),
    ("pv_w", 223, 1),
)
SIGNED = {"battery_a", "battery_w"}  # add "grid_w" if needed

def _to_signed16(x: int) -> int:
    return x - 0x10000 if x >= 0x8000 else x

def _blocks(max_gap=1, max_len=16):
    items = sorted(REGS, key=lambda r: r[1])
    out: List[Tuple[int, List[Tuple[str,int,float]]]] = []
    cur: List[Tuple[str,int,float]] = []
    start = None
    for name, addr, scale in items:
        if start is None:
            start = addr; cur = [(name, addr, scale)]; continue
        if (addr - cur[-1][1]) <= max_gap and (addr - start + 1) <= max_len:
            cur.append((name, addr, scale))
        else:
            out.append((start, cur))
            start = addr; cur = [(name, addr, scale)]
    if start is not None:
        out.append((start, cur))
    return out

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app   = Flask(__name__, static_folder=None)
if Compress:
    try:
        Compress(app)
        print(f"[startup] Flask-Compress enabled", flush=True)
    except Exception as _e:
        print(f"[startup] Flask-Compress not enabled: {_e}", flush=True)
print(f"[startup] Flask app created successfully", flush=True)
_stop = Event()
_lock = Lock()
_last: Optional[Dict[str, Any]] = None
LAST_ERR: Optional[str] = None
LAST_OK:  Optional[str] = None

@app.after_request
def set_cache_headers(resp):
    """
    Apply no-cache only for API responses.
    Allow long-lived caching for static assets (handled by routes serving /web/* files).
    """
    try:
        path = request.path or ""
    except Exception:
        path = ""

    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    else:
        # Immutable cache for common static assets; conservative for HTML
        static_exts = (".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico", ".webmanifest")
        if any(path.endswith(ext) for ext in static_exts):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # HTML or others: allow revalidation
            resp.headers["Cache-Control"] = "no-cache"
    return resp

# Ensure utf-8 charset for text/*
@app.after_request
def ensure_charset(resp):
    ctype = resp.headers.get("Content-Type", "")
    if ctype.startswith("text/") and "charset=" not in ctype.lower():
        resp.headers["Content-Type"] = f"{ctype}; charset=utf-8"
    return resp

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def db():
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    con = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA busy_timeout=5000;")
    return con

def db_init():
    print(f"[db] Initializing database...", flush=True)
    with db() as con:
        print(f"[db] Creating samples table...", flush=True)
        con.execute("""
            CREATE TABLE IF NOT EXISTS samples(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              timestamp TEXT NOT NULL,
              pv_w REAL, pv_v REAL, pv_a REAL,
              battery_w REAL, battery_v REAL, battery_a REAL,
              grid_w REAL, grid_v REAL, grid_hz REAL, grid_a REAL,
              load_w REAL, load_v REAL, load_hz REAL, load_a REAL, load_va REAL, load_pf REAL, load_percent REAL,
              dc_temp REAL, inverter_temp REAL, heatsink_temp REAL, dc_bus_v REAL
            );
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON samples(timestamp);")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS uniq_ts ON samples(timestamp);")
        
        print(f"[db] Creating archive table...", flush=True)
        con.execute("""
            CREATE TABLE IF NOT EXISTS archive(
              day TEXT PRIMARY KEY,
              pv_Wh REAL, load_Wh REAL, grid_Wh REAL,
              batt_in_Wh REAL, batt_out_Wh REAL
            );
        """)
        
        print(f"[db] Creating battery_counters table...", flush=True)
        con.execute("""
            CREATE TABLE IF NOT EXISTS battery_counters(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              counter_type TEXT NOT NULL,
              start_timestamp TEXT NOT NULL,
              start_battery_v REAL NOT NULL,
              total_batt_in_Wh REAL DEFAULT 0.0,
              total_batt_out_Wh REAL DEFAULT 0.0,
              total_batt_net_Wh REAL DEFAULT 0.0,
              reset_reason TEXT,
              created_at TEXT NOT NULL
            );
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_battery_counters_type ON battery_counters(counter_type);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_battery_counters_timestamp ON battery_counters(start_timestamp);")
        
        print(f"[db] Creating i2c_snapshots table...", flush=True)
        con.execute("""
            CREATE TABLE IF NOT EXISTS i2c_snapshots(
              timestamp TEXT PRIMARY KEY,
              data TEXT
            );
        """)
        
        print(f"[db] Committing changes...", flush=True)
        con.commit()
        print(f"[db] Database initialization completed", flush=True)

def db_trim(days: int = 365):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with db() as con:
        con.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
        con.commit()

def db_archive_and_trim(days:int=30):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    with db() as con:
        rows = con.execute("""
            WITH m AS (
              SELECT strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                     AVG(pv_w)       AS pv_w,
                     AVG(battery_w)  AS battery_w,
                     AVG(load_w)     AS load_w,
                     AVG(grid_w)     AS grid_w
              FROM samples
              WHERE timestamp < ?
              GROUP BY ts_min
            )
            SELECT
              date(ts_min) AS day,
              SUM(pv_w)/60.0        AS pv_Wh,
              SUM(load_w)/60.0      AS load_Wh,
              SUM(grid_w)/60.0      AS grid_Wh,
              SUM(CASE WHEN battery_w>0 THEN battery_w ELSE 0 END)/60.0  AS batt_in_Wh,
              SUM(CASE WHEN battery_w<0 THEN -battery_w ELSE 0 END)/60.0 AS batt_out_Wh
            FROM m
            GROUP BY day
            ORDER BY day ASC;
        """, (cutoff,)).fetchall()

        for r in rows:
            con.execute("""
                INSERT OR REPLACE INTO archive
                  (day, pv_Wh, load_Wh, grid_Wh, batt_in_Wh, batt_out_Wh)
                VALUES (?,?,?,?,?,?)
            """, (r["day"], r["pv_Wh"], r["load_Wh"], r["grid_Wh"], r["batt_in_Wh"], r["batt_out_Wh"]))

        con.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
        con.commit()

def db_archive_upto_today():
    """
    Archivia e pulisce TUTTI i campioni fino a ieri (esclude SEMPRE il giorno corrente).
    """
    cutoff = datetime.now().strftime("%Y-%m-%d 00:00:00")
    with db() as con:
        rows = con.execute("""
            WITH m AS (
              SELECT strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                     AVG(pv_w)       AS pv_w,
                     AVG(battery_w)  AS battery_w,
                     AVG(load_w)     AS load_w,
                     AVG(grid_w)     AS grid_w
              FROM samples
              WHERE timestamp < ?
              GROUP BY ts_min
            )
            SELECT
              date(ts_min) AS day,
              SUM(pv_w)/60.0        AS pv_Wh,
              SUM(load_w)/60.0      AS load_Wh,
              SUM(grid_w)/60.0      AS grid_Wh,
              SUM(CASE WHEN battery_w>0 THEN battery_w ELSE 0 END)/60.0  AS batt_in_Wh,
              SUM(CASE WHEN battery_w<0 THEN -battery_w ELSE 0 END)/60.0 AS batt_out_Wh
            FROM m
            GROUP BY day
            ORDER BY day ASC;
        """, (cutoff,)).fetchall()

        for r in rows:
            con.execute("""
                INSERT OR REPLACE INTO archive
                  (day, pv_Wh, load_Wh, grid_Wh, batt_in_Wh, batt_out_Wh)
                VALUES (?,?,?,?,?,?)
            """, (r["day"], r["pv_Wh"], r["load_Wh"], r["grid_Wh"], r["batt_in_Wh"], r["batt_out_Wh"]))

        con.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
        con.commit()

# ---------------------------------------------------------------------------
# Archive helpers: dry-run, sizes, vacuum
# ---------------------------------------------------------------------------
def _db_files_size_bytes() -> int:
    total = 0
    try:
        total += os.path.getsize(str(DB_PATH))
    except Exception:
        pass
    for suf in ("-wal", "-shm"):
        try:
            total += os.path.getsize(str(DB_PATH) + suf)
        except Exception:
            pass
    return total

def _archive_compute_and_apply(cutoff: str, apply: bool) -> Dict[str, Any]:
    """
    Calcola righe da archiviare e da cancellare. Se apply=True, esegue archiviazione e cancellazione.
    Ritorna un riepilogo.
    """
    summary: Dict[str, Any] = {}
    with db() as con:
        # Quante righe dei samples verrebbero cancellate?
        cnt = con.execute("SELECT COUNT(*) FROM samples WHERE timestamp < ?", (cutoff,)).fetchone()[0]
        summary["minutes_to_delete"] = int(cnt or 0)
        # Quanti giorni finirebbero in archive?
        rows = con.execute("""
            WITH m AS (
              SELECT strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                     AVG(pv_w) AS pv_w,
                     AVG(battery_w) AS battery_w,
                     AVG(load_w) AS load_w,
                     AVG(grid_w) AS grid_w
              FROM samples
              WHERE timestamp < ?
              GROUP BY ts_min
            )
            SELECT
              date(ts_min) AS day,
              SUM(pv_w)/60.0        AS pv_Wh,
              SUM(load_w)/60.0      AS load_Wh,
              SUM(grid_w)/60.0      AS grid_Wh,
              SUM(CASE WHEN battery_w>0 THEN battery_w ELSE 0 END)/60.0  AS batt_in_Wh,
              SUM(CASE WHEN battery_w<0 THEN -battery_w ELSE 0 END)/60.0 AS batt_out_Wh
            FROM m
            GROUP BY day
            ORDER BY day ASC;
        """, (cutoff,)).fetchall()
        summary["days_to_archive"] = len(rows)

        if apply and rows:
            for r in rows:
                con.execute("""
                    INSERT OR REPLACE INTO archive
                      (day, pv_Wh, load_Wh, grid_Wh, batt_in_Wh, batt_out_Wh)
                    VALUES (?,?,?,?,?,?)
                """, (r["day"], r["pv_Wh"], r["load_Wh"], r["grid_Wh"], r["batt_in_Wh"], r["batt_out_Wh"]))
            con.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
            con.commit()
    return summary

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_current_battery_counter():
    """Ottiene il contatore corrente della batteria netta o ne crea uno nuovo"""
    print(f"[battery] get_current_battery_counter() called", flush=True)
    
    try:
        with db() as con:
            print(f"[battery] Database connection established", flush=True)
            
        # Cerca il contatore attivo piu' recente
            print(f"[battery] Searching for existing counter...", flush=True)
            row = con.execute("""
                SELECT * FROM battery_counters 
                WHERE counter_type = 'daily_net' 
                ORDER BY start_timestamp DESC 
                LIMIT 1
            """).fetchone()
            
            if row:
                print(f"[battery] Found existing counter: {row}", flush=True)
                return dict(row)
            else:
                print(f"[battery] No existing counter found, creating new one...", flush=True)
                # Crea un nuovo contatore
                now = now_str()
                print(f"[battery] Current time: {now}", flush=True)
                
                cursor = con.execute("""
                    INSERT INTO battery_counters 
                    (counter_type, start_timestamp, start_battery_v, created_at)
                    VALUES (?, ?, ?, ?)
                """, ('daily_net', now, 0.0, now))
                con.commit()
                
                new_id = cursor.lastrowid
                print(f"[battery] New counter created with ID: {new_id}", flush=True)
                
                # Ritorna il contatore appena creato
                return {
                    'id': new_id,
                    'counter_type': 'daily_net',
                    'start_timestamp': now,
                    'start_battery_v': 0.0,
                    'total_batt_in_Wh': 0.0,
                    'total_batt_out_Wh': 0.0,
                    'total_batt_net_Wh': 0.0,
                    'reset_reason': 'initial',
                    'created_at': now
                }
    except Exception as e:
        print(f"[battery] Error in get_current_battery_counter: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return None

def reset_battery_counter(reason="manual"):
    """Azzera il contatore della batteria netta e ne crea uno nuovo"""
    now = now_str()
    with db() as con:
        # Chiudi il contatore corrente
        con.execute("""
            UPDATE battery_counters 
            SET reset_reason = ? 
            WHERE counter_type = 'daily_net' 
            AND reset_reason IS NULL
        """, (reason,))
        
        # Crea un nuovo contatore
        cursor = con.execute("""
            INSERT INTO battery_counters 
            (counter_type, start_timestamp, start_battery_v, created_at)
            VALUES (?, ?, ?, ?)
        """, ('daily_net', now, 0.0, now))
        con.commit()
        
        print(f"[battery] Contatore azzerato: {reason}", flush=True)
        return cursor.lastrowid

def update_battery_counter(battery_w, battery_v):
    """Aggiorna il contatore della batteria con i nuovi dati"""
    if battery_w is None or battery_v is None:
        return
    
    counter = get_current_battery_counter()
    if not counter:
        return
    
    # Calcola l'energia in Wh (battery_w e' in W, campionamento ogni POLL_S secondi)
    energy_wh = (float(battery_w) * POLL_S) / 3600.0
    
    with db() as con:
        if battery_w > 0:  # Carica
            con.execute("""
                UPDATE battery_counters 
                SET total_batt_in_Wh = total_batt_in_Wh + ?,
                    total_batt_net_Wh = total_batt_in_Wh + ? - total_batt_out_Wh
                WHERE id = ?
            """, (energy_wh, energy_wh, counter['id']))
        elif battery_w < 0:  # Scarica
            energy_wh = abs(energy_wh)
            con.execute("""
                UPDATE battery_counters 
                SET total_batt_out_Wh = total_batt_out_Wh + ?,
                    total_batt_net_Wh = total_batt_in_Wh - (total_batt_out_Wh + ?)
                WHERE id = ?
            """, (energy_wh, energy_wh, counter['id']))
        
        con.commit()

def check_battery_reset_condition(battery_v, battery_w):
    """Controlla se e' necessario azzerare il contatore della batteria"""
    if battery_v is None or battery_w is None:
        return False
    
    battery_v = float(battery_v)
    battery_w = float(battery_w)
    
    # Threshold configurabile (default 46V) in CONF["battery"]["net_reset_voltage"]
    try:
        reset_thr = float(_get("battery.net_reset_voltage", DEFAULT_NET_RESET_V))
    except Exception:
        reset_thr = DEFAULT_NET_RESET_V

    # Azzera solo se la batteria e' in scarica (battery_w < 0) e raggiunge la soglia
    if battery_w < 0 and battery_v <= reset_thr:
        # Verifica che non sia gia' stato azzerato recentemente (evita azzeramenti multipli)
        with db() as con:
            last_reset = con.execute("""
                SELECT start_timestamp FROM battery_counters 
                WHERE counter_type = 'daily_net' 
                ORDER BY start_timestamp DESC 
                LIMIT 1
            """).fetchone()
            
            if last_reset:
                last_reset_dt = parse_ts(last_reset['start_timestamp'])
                if last_reset_dt:
                    time_diff = datetime.now() - last_reset_dt
                    # Evita azzeramenti multipli entro 1 ora
                    if time_diff.total_seconds() < 3600:
                        return False
        
        # Azzera il contatore
        reset_battery_counter(f"battery_{reset_thr:.1f}v_discharge_{battery_v:.1f}V")
        return True
    
    return False

def parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

# ---------------------------------------------------------------------------
# GPIO helpers (abstract over RPi.GPIO and lgpio)
# ---------------------------------------------------------------------------
GPIO_BACKEND: Optional[str] = None
RGPIO = None
LGPIO = None

try:
    import RPi.GPIO as RGPIO  # type: ignore
    GPIO_BACKEND = "rpi"
except Exception:
    try:
        import lgpio as LGPIO  # type: ignore
        GPIO_BACKEND = "lgpio"
    except Exception:
        GPIO_BACKEND = None

print(f"[relay] GPIO backend selected: {GPIO_BACKEND}", flush=True)
print(f"[relay] DEBUG: backend={GPIO_BACKEND} RGPIO_loaded={RGPIO is not None} LGPIO_loaded={LGPIO is not None}", flush=True)

# Stato interno per lgpio
_GPIO_CTX = {"h": None, "pin": None}

def _gpio_setup_output(pin: int, initial_high: bool) -> bool:
    """Configura il pin come output con livello iniziale HIGH/LOW."""
    if GPIO_BACKEND == "rpi":
        try:
            RGPIO.setwarnings(False)
            RGPIO.setmode(RGPIO.BCM)
            RGPIO.setup(pin, RGPIO.OUT, initial=RGPIO.HIGH if initial_high else RGPIO.LOW)
            return True
        except Exception as e:
            print(f"[gpio] RPi.GPIO setup error: {e}", flush=True)
            return False
    elif GPIO_BACKEND == "lgpio":
        try:
            h = _GPIO_CTX.get("h") or LGPIO.gpiochip_open(0)
            _GPIO_CTX["h"] = h
            LGPIO.gpio_claim_output(h, pin, LGPIO.SET_HIGH if initial_high else LGPIO.SET_LOW)
            _GPIO_CTX["pin"] = pin
            return True
        except Exception as e:
            print(f"[gpio] lgpio setup error: {e}", flush=True)
            return False
    else:
        return False

def _gpio_write(pin: int, level_high: bool) -> bool:
    """Scrive HIGH/LOW sul pin. Ritorna True se ok."""
    if GPIO_BACKEND == "rpi":
        try:
            RGPIO.output(pin, RGPIO.HIGH if level_high else RGPIO.LOW)
            return True
        except Exception as e:
            print(f"[gpio] RPi.GPIO write error: {e}", flush=True)
            return False
    elif GPIO_BACKEND == "lgpio":
        try:
            h = _GPIO_CTX.get("h")
            if h is None:
                if not _gpio_setup_output(pin, level_high):
                    return False
                h = _GPIO_CTX.get("h")
            LGPIO.gpio_write(h, pin, 1 if level_high else 0)
            return True
        except Exception as e:
            print(f"[gpio] lgpio write error: {e}", flush=True)
            return False
    else:
        return False

def _gpio_read(pin: int) -> Optional[int]:
    """Legge il livello (0/1) se possibile, altrimenti None."""
    if GPIO_BACKEND == "rpi":
        try:
            return int(RGPIO.input(pin))
        except Exception:
            return None
    elif GPIO_BACKEND == "lgpio":
        try:
            h = _GPIO_CTX.get("h")
            if h is None:
                return None
            return int(LGPIO.gpio_read(h, pin))
        except Exception:
            return None
    else:
        return None

def _gpio_cleanup():
    """Rilascia risorse a fine esecuzione."""
    if GPIO_BACKEND == "rpi":
        try:
            RGPIO.cleanup()
        except Exception:
            pass
    elif GPIO_BACKEND == "lgpio":
        try:
            h = _GPIO_CTX.get("h")
            pin = _GPIO_CTX.get("pin")
            if h is not None and pin is not None:
                try:
                    LGPIO.gpio_free(h, pin)
                except Exception:
                    pass
            if h is not None:
                try:
                    LGPIO.gpiochip_close(h)
                except Exception:
                    pass
            _GPIO_CTX["h"] = None
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Relay control (GPIO 17 / physical pin 11)
# ---------------------------------------------------------------------------
CONF.setdefault("relay", {
    "mode": "gpio",
    "enabled": False,
    "gpio_pin": 17,          # BCM numbering (GPIO 17 == physical pin 11)
    "active_high": True,     # if False, relay is active on GPIO LOW
    "on_v": 47.5,            # turn ON when battery_v <= on_v
    "off_v": 49.0,           # turn OFF when battery_v >= off_v
    "min_toggle_sec": 5
})

RELAY_STATE: Optional[bool] = None  # None=unknown, True=on, False=off
RELAY_LAST_TOGGLE: float = 0.0

def relay_apply(hw_on: bool):
    """Set relay output according to active_high."""
    global RELAY_STATE, RELAY_LAST_TOGGLE
    cfg = CONF.get("relay", {})
    pin = int(cfg.get("gpio_pin", 17))
    active_high = bool(cfg.get("active_high", True))

    level_high = hw_on if active_high else (not hw_on)

    if GPIO_BACKEND is not None and str(cfg.get("mode", "gpio")).lower() == "gpio":
        ok = _gpio_write(pin, level_high)
        rb = _gpio_read(pin)
        print(f"[relay] APPLY: hw_on={hw_on} active_high={active_high} -> level_high={level_high} "
              f"(write_ok={ok}, readback={rb})", flush=True)
    else:
        print("[relay] GPIO not available or mode!=gpio, skip", flush=True)

    RELAY_STATE = hw_on
    RELAY_LAST_TOGGLE = time.monotonic()

def relay_setup():
    """Init GPIO (if available) and set relay to logical OFF."""
    cfg = CONF.get("relay", {})
    if GPIO_BACKEND is None or str(cfg.get("mode", "gpio")).lower() != "gpio":
        print("[relay] Setup skipped: GPIO not available or mode!=gpio", flush=True)
        return
    pin = int(cfg.get("gpio_pin", 17))
    active_high = bool(cfg.get("active_high", True))

    # Logical OFF => level:
    # active_high True  => LOW
    # active_high False => HIGH
    off_level_high = False if active_high else True
    ok = _gpio_setup_output(pin, off_level_high)

    global RELAY_STATE, RELAY_LAST_TOGGLE
    RELAY_STATE = False
    RELAY_LAST_TOGGLE = time.monotonic()
    rb = _gpio_read(pin)

    print(f"[relay] SETUP: backend={GPIO_BACKEND} pin={pin} active_high={active_high} "
          f"-> OFF (initial level_high={off_level_high}, setup_ok={ok}, readback={rb})", flush=True)



def relay_auto_step(batt_v: Optional[float]):
    """Hysteresis: on when batt_v <= on_v; off when batt_v >= off_v."""
    global RELAY_STATE
    cfg = CONF.get("relay", {})
    if not bool(cfg.get("enabled", False)):
        return
    if batt_v is None:
        return
    on_v  = float(cfg.get("on_v", 47.5))
    off_v = float(cfg.get("off_v", 49.0))
    min_gap = max(0, int(cfg.get("min_toggle_sec", 5)))
    now = time.monotonic()

    cur = RELAY_STATE
    want = cur
    if cur is None:
        if batt_v <= on_v:
            want = True
        elif batt_v >= off_v:
            want = False
        else:
            return
    else:
        if (not cur) and (batt_v <= on_v):
            want = True
        elif cur and (batt_v >= off_v):
            want = False
        else:
            want = cur

    if want != cur and (now - RELAY_LAST_TOGGLE) >= min_gap:
        relay_apply(bool(want))

# ---------------------------------------------------------------------------
# Modbus read
# ---------------------------------------------------------------------------
def read_regs() -> Optional[Dict[str, Any]]:
    global LAST_ERR, LAST_OK
    if ModbusSerialClient is None:
        LAST_ERR = "pymodbus not available"
        # fallback to minimalmodbus if available
        if _MINIMODBUS is not None:
            out = _read_regs_minimalmodbus()
            if out is not None:
                LAST_ERR=None; LAST_OK=now_str()
                return out
        return None
    cli = ModbusSerialClient(method="rtu", port=MB_PORT, baudrate=MB_BAUD,
                             parity=MB_PARITY, stopbits=MB_STOP, bytesize=MB_BYTES,
                             timeout=MB_TIMEOUT)
    if not cli.connect():
        LAST_ERR = f"serial connection failed on {MB_PORT}"
        # fallback to minimalmodbus if available
        if _MINIMODBUS is not None:
            out = _read_regs_minimalmodbus()
            if out is not None:
                LAST_ERR=None; LAST_OK=now_str()
                return out
        return None
    out: Dict[str,Any] = {}
    try:
        for start, block in _blocks():
            count = block[-1][1] - start + 1
            rr = cli.read_holding_registers(start, count, unit=UNIT_ID)
            if hasattr(rr, "isError") and rr.isError():
                raise RuntimeError(f"Read error at {start}")
            regs = rr.registers
            for name, addr, scale in block:
                raw = int(regs[addr - start])
                if name in SIGNED: raw = _to_signed16(raw)
                out[name] = float(raw)*scale
        LAST_ERR=None; LAST_OK=now_str()
        return out
    except Exception as e:
        LAST_ERR=str(e)
        # fallback to minimalmodbus if available
        if _MINIMODBUS is not None:
            out = _read_regs_minimalmodbus()
            if out is not None:
                LAST_ERR=None; LAST_OK=now_str()
                return out
        return None
    finally:
        with contextlib.suppress(Exception): cli.close()

# ---------------------------------------------------------------------------
# minimalmodbus fallback (se disponibile)
# ---------------------------------------------------------------------------
def _read_regs_minimalmodbus() -> Optional[Dict[str, Any]]:
    try:
        if _MINIMODBUS is None:
            return None
        inst = _MINIMODBUS.Instrument(str(MB_PORT), int(UNIT_ID))
        # Config seriale
        inst.serial.baudrate = int(MB_BAUD)
        inst.serial.bytesize = int(MB_BYTES)
        # Parita'
        p = str(MB_PARITY).upper()
        import serial  # type: ignore
        if p == "E":
            inst.serial.parity = serial.PARITY_EVEN
        elif p == "O":
            inst.serial.parity = serial.PARITY_ODD
        else:
            inst.serial.parity = serial.PARITY_NONE
        inst.serial.stopbits = int(MB_STOP)
        inst.serial.timeout  = float(MB_TIMEOUT)
        inst.mode = _MINIMODBUS.MODE_RTU

        out: Dict[str, Any] = {}
        for name, addr, scale in REGS:
            try:
                # Leggi 1 registro holding (FC3), nessuna scala interna (decimali=0)
                val = inst.read_register(int(addr), 0, functioncode=3, signed=(name in SIGNED))
                out[name] = float(val) * float(scale)
            except Exception:
                out[name] = None

        # Derivati allineati a pymodbus
        gv = float(out.get("grid_v") or 0.0)
        gw = float(out.get("grid_w") or 0.0)
        out["grid_a"] = (gw / gv) if gv else 0.0
        try:
            lw  = float(out.get("load_w") or 0.0)
            lva = float(out.get("load_va") or 0.0)
            pf  = out.get("load_pf")
            if (pf is None) or (float(pf or 0.0) <= 0.0):
                val = (abs(lw)/abs(lva)) if abs(lva) > 1e-6 else None
                out["load_pf"] = None if val is None else max(0.0, min(1.0, val))
        except Exception:
            pass
        return out
    except Exception:
        return None

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
# Daily Analysis System (module in src/)
# ---------------------------------------------------------------------------
_SRC_DIR = BASE_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
from daily_analyzer import DailyAnalyzer

# Inizializza analizzatore giornaliero
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

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
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
    last_dt = parse_ts(db_last) or parse_ts(LAST_OK)
    stale_seconds = None
    if last_dt:
        stale_seconds = int((datetime.now() - last_dt).total_seconds())

    relay_cfg = CONF.get("relay", {})
    return jsonify({
        "status": "ok",
        "last_ok": LAST_OK,
        "last_error": LAST_ERR,
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
                if reset_voltage is None:
                    return False, "SOC reset_voltage required for energy_balance method"
                
                # Valida che reset_voltage sia nel range 80-90% della tensione nominale
                nominal_v = float(b.get("nominal_voltage", 48))
                min_reset = nominal_v * 0.8
                max_reset = nominal_v * 0.9
                
                if not (min_reset <= float(reset_voltage) <= max_reset):
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
    s["last_ok"] = LAST_OK
    s["last_error"] = LAST_ERR

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

@app.route("/api/battery/test")
def battery_test():
    """Endpoint di test semplice per verificare se il routing funziona"""
    print(f"[battery] TEST endpoint called", flush=True)
    return jsonify({
        "ok": True,
        "message": "Battery test endpoint working",
        "timestamp": now_str()
    })

print(f"[startup] Battery test endpoint registered", flush=True)

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
        print(f"[relay] STATE request: enabled={cfg.get('enabled')}, mode={cfg.get('mode')}, pin={pin}, RELAY_STATE={RELAY_STATE}", flush=True)
        
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
            print(f"[relay] GPIO read pin {pin}: level={level}", flush=True)
        except Exception as e:
            level = None
            print(f"[relay] Errore lettura GPIO pin {pin}: {e}", flush=True)
        
        # Se RELAY_STATE e' None, prova a determinarlo dal GPIO
        current_state = RELAY_STATE
        if current_state is None and level is not None:
            active_high = bool(cfg.get("active_high", True))
            current_state = (level == 1) if active_high else (level == 0)
            print(f"[relay] RELAY_STATE inferito da GPIO: level={level}, active_high={active_high} -> state={current_state}", flush=True)
        
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
