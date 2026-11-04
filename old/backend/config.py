# -*- coding: utf-8 -*-
"""Paths, environment, and JSON configuration for the inverter backend."""
from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = _BACKEND_DIR.parent
WEB_DIR = _BACKEND_DIR / "web"
CFG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
# SQLite path: default sotto data/; override con env per test o installazioni custom (path assoluto consigliato).
_db_path_env = os.getenv("INVERTER_DB_PATH", "").strip()
if _db_path_env:
    DB_PATH = Path(_db_path_env).expanduser().resolve()
else:
    DB_PATH = DATA_DIR / "inverter_history.db"

PORT = int(os.getenv("PORT", "8000"))


def _load_json(path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _resolve_config_path() -> Path:
    """Path del JSON di configurazione: priorità INVERTER_CONFIG_PATH, poi INVERTER_CONFIG, poi default repo."""
    path_explicit = os.getenv("INVERTER_CONFIG_PATH", "").strip()
    if path_explicit:
        return Path(path_explicit).expanduser().resolve()
    raw = Path(os.getenv("INVERTER_CONFIG", str(CFG_DIR / "inverter_config.json")))
    if not raw.exists():
        try:
            CFG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        with contextlib.suppress(Exception):
            default_cfg = CFG_DIR / "inverter_config.json"
            if not default_cfg.exists():
                default_cfg.write_text("{}", encoding="utf-8")
        return CFG_DIR / "inverter_config.json"
    return raw


CONF: Dict[str, Any] = {}


def _strip_relay_webhooks() -> None:
    if "relay" not in CONF:
        return
    if "webhook_on" in CONF["relay"]:
        del CONF["relay"]["webhook_on"]
        logger.info("Removed obsolete webhook_on from relay config")
    if "webhook_off" in CONF["relay"]:
        del CONF["relay"]["webhook_off"]
        logger.info("Removed obsolete webhook_off from relay config")


def _apply_loaded_conf(raw: Dict[str, Any]) -> None:
    """Aggiorna il dizionario globale CONF da un dict JSON (stesso effetto del bootstrap iniziale)."""
    CONF.clear()
    CONF.update(raw if isinstance(raw, dict) else {})
    _strip_relay_webhooks()
    CONF.setdefault(
        "relay",
        {
            "mode": "gpio",
            "enabled": False,
            "gpio_pin": 17,
            "active_high": True,
            "on_v": 47.5,
            "off_v": 49.0,
            "min_toggle_sec": 5,
        },
    )


def _log_config_loaded() -> None:
    logger.info("Loaded config from %s keys=%s", CONFIG_PATH, list(CONF.keys()))
    if "i2c" in CONF:
        try:
            devices = CONF.get("i2c", {}).get("devices", [])
            logger.info(
                "I2C enabled=%s devices=%s",
                CONF.get("i2c", {}).get("enabled"),
                len(devices),
            )
        except Exception as e:
            logger.warning("Error inspecting i2c config: %s", e)


CONFIG_PATH = _resolve_config_path()
_apply_loaded_conf(_load_json(CONFIG_PATH))
_log_config_loaded()


def reload_runtime_config() -> None:
    """
    Rilegge CONFIG_PATH e riscrive CONF (webhook legacy + default relay come all'avvio).

    Utile nei test dopo POST /api/config senza `persist`, o per ripristinare lo stato da disco.
    Non ricalcola MB_* / POLL_S / I2C_* derivati all'import: restano i valori del primo caricamento modulo.
    """
    _apply_loaded_conf(_load_json(CONFIG_PATH))
    _log_config_loaded()


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
    if s in {"1", "true", "y", "yes", "on"}:
        return True
    if s in {"0", "false", "n", "no", "off"}:
        return False
    return default


def ev(env: str, path: str, default, typ):
    if env in os.environ:
        return typ(os.environ[env]) if typ is not bool else _bool(os.environ[env], default)
    v = _get(path, default)
    return typ(v) if typ is not bool else _bool(v, default)


MB_PORT = ev("INVERTER_MODBUS_SERIAL_PORT", "serial.port", "/dev/serial0", str)
MB_BAUD = ev("INVERTER_MODBUS_BAUDRATE", "serial.baudrate", 9600, int)
MB_PARITY = ev("INVERTER_MODBUS_PARITY", "serial.parity", "N", str)
MB_STOP = ev("INVERTER_MODBUS_STOPBITS", "serial.stopbits", 1, int)
MB_BYTES = ev("INVERTER_MODBUS_BYTESIZE", "serial.bytesize", 8, int)
MB_TIMEOUT = ev("INVERTER_MODBUS_TIMEOUT", "serial.timeout", 1.0, float)
UNIT_ID = ev("INVERTER_UNIT_ID", "serial.unit_id", 1, int)
POLL_S = ev("POLL_INTERVAL_SEC", "polling.interval_sec", 5, float)

DEFAULT_NET_RESET_V = 46.0

I2C_ENABLED: bool = ev("I2C_ENABLED", "i2c.enabled", False, bool)
I2C_BUS: int = ev("I2C_BUS", "i2c.bus", 1, int)
I2C_DEVICES = _get("i2c.devices", []) or []


def validate_config(data: dict) -> Tuple[bool, str]:
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
                reset_voltage = b["soc"].get("reset_voltage")
                if reset_voltage is None:
                    return False, "SOC reset_voltage required for energy_balance method"

                nominal_v = float(b.get("nominal_voltage", 48))
                min_reset = nominal_v * 0.8
                max_reset = nominal_v * 0.9

                if not (min_reset <= float(reset_voltage) <= max_reset):
                    return (
                        False,
                        f"SOC reset_voltage must be between {min_reset:.1f}V and {max_reset:.1f}V (80-90% of nominal voltage)",
                    )

            elif soc_method == "voltage_based":
                vmin = float(b["soc"].get("vmin_v", 0))
                vmax = float(b["soc"].get("vmax_v", 0))
                if vmax <= vmin:
                    return False, "SOC vmax must be > vmin"
            else:
                return False, f"Unknown SOC method: {soc_method}"

        ui = data.get("ui", {})
        if "unit" in ui and str(ui["unit"]).upper() not in {"W", "KW"}:
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
                on_v = float(r["on_v"])
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
