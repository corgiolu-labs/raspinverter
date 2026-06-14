#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Config, costanti e helper generici per il monitor inverter.
Estratto da inverter_api.py (refactor 2026-06-06) — nessuna logica cambiata.
"""
import os
import json
import contextlib
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR  = BASE_DIR / "web"
CFG_DIR  = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"

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

# Pulizia immediata webhook obsoleti
if "relay" in CONF:
    for _k in ("webhook_on", "webhook_off"):
        if _k in CONF["relay"]:
            del CONF["relay"][_k]
            print(f"[startup] Rimosso {_k} obsoleto", flush=True)

# Default relay (se mancante) — era nel corpo di inverter_api.py
CONF.setdefault("relay", {
    "mode": "gpio",
    "enabled": False,
    "gpio_pin": 17,          # BCM numbering (GPIO 17 == physical pin 11)
    "active_high": True,     # if False, relay is active on GPIO LOW
    "on_v": 47.5,            # turn ON when battery_v <= on_v
    "off_v": 49.0,           # turn OFF when battery_v >= off_v
    "min_toggle_sec": 5
})


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
    if s in {"1", "true", "y", "yes", "on"}:  return True
    if s in {"0", "false", "n", "no", "off"}: return False
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
I2C_DEVICES = _get("i2c.devices", []) or []  # list of {name, address, reads:[...]}

# ---------------------------------------------------------------------------
# Registers map (name, address, scale)
# ---------------------------------------------------------------------------
REGS: Tuple[Tuple[str, int, float], ...] = (
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


# ---------------------------------------------------------------------------
# Time helpers (generici, senza dipendenze)
# ---------------------------------------------------------------------------
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
