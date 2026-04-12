#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry point compatibile: init DB/relay, thread di polling Modbus, server Flask."""
from __future__ import annotations

import atexit
import json
import logging
import signal
import sys
import time
from pathlib import Path
from threading import Thread

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent
_SRC_DIR = _REPO_ROOT / "src"
for _p in (_BACKEND_DIR, _SRC_DIR):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("inverter_api")

import poll_state
from app import create_app
from config import PORT, POLL_S
from db import db, db_init, now_str
from services import i2c_service, modbus_service
from services.battery_service import (
    check_battery_reset_condition,
    update_battery_counter,
)
from services.relay_service import _gpio_cleanup, relay_auto_step, relay_setup


def poll_loop() -> None:
    next_t = time.monotonic()
    while not poll_state.stop_event.is_set():
        try:
            with poll_state.lock:
                regs = modbus_service.read_regs()
                ts_now = now_str()

                i2c_snapshot = None
                try:
                    i2c_snapshot = i2c_service.i2c_read_all()
                    i2c_service.LAST_I2C = i2c_snapshot
                except Exception:
                    i2c_service.LAST_I2C = None

                if regs:
                    s = {"timestamp": ts_now, **regs}
                    gv = float(s.get("grid_v") or 0.0)
                    gw = float(s.get("grid_w") or 0.0)
                    s["grid_a"] = (gw / gv) if gv else 0.0
                    try:
                        lw = float(s.get("load_w") or 0.0)
                        lva = float(s.get("load_va") or 0.0)
                        pf = s.get("load_pf")
                        if (pf is None) or (float(pf or 0.0) <= 0.0):
                            val = (abs(lw) / abs(lva)) if abs(lva) > 1e-6 else None
                            s["load_pf"] = None if val is None else max(0.0, min(1.0, val))
                    except Exception:
                        pass
                    poll_state.last_sample = s

                    with db() as con:
                        con.execute(
                            """
                            INSERT OR IGNORE INTO samples(timestamp,
                              pv_w,pv_v,pv_a,
                              battery_w,battery_v,battery_a,
                              grid_w,grid_v,grid_hz,grid_a,
                              load_w,load_v,load_hz,load_a,load_va,load_pf,load_percent,
                              dc_temp,inverter_temp,heatsink_temp,dc_bus_v)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                            (
                                s["timestamp"],
                                s.get("pv_w"),
                                s.get("pv_v"),
                                s.get("pv_a"),
                                s.get("battery_w"),
                                s.get("battery_v"),
                                s.get("battery_a"),
                                s.get("grid_w"),
                                s.get("grid_v"),
                                s.get("grid_hz"),
                                s.get("grid_a"),
                                s.get("load_w"),
                                s.get("load_v"),
                                s.get("load_hz"),
                                s.get("load_a"),
                                s.get("load_va"),
                                s.get("load_pf"),
                                s.get("load_percent"),
                                s.get("dc_temp"),
                                s.get("inverter_temp"),
                                s.get("heatsink_temp"),
                                s.get("dc_bus_v"),
                            ),
                        )
                        if i2c_snapshot is not None:
                            con.execute(
                                "INSERT OR REPLACE INTO i2c_snapshots(timestamp, data) VALUES (?, ?)",
                                (ts_now, json.dumps(i2c_snapshot, ensure_ascii=False)),
                            )
                        con.commit()

                    try:
                        batt_v = None
                        if "battery_v" in s and s["battery_v"] is not None:
                            batt_v = float(s["battery_v"])
                        relay_auto_step(batt_v)
                    except Exception:
                        pass

                    try:
                        battery_w = s.get("battery_w")
                        battery_v = s.get("battery_v")

                        if check_battery_reset_condition(battery_v, battery_w):
                            pass

                        update_battery_counter(battery_w, battery_v)
                    except Exception as e:
                        logger.warning("battery counter update error: %s", e)
                else:
                    if i2c_snapshot is not None:
                        try:
                            with db() as con:
                                con.execute(
                                    "INSERT OR REPLACE INTO i2c_snapshots(timestamp, data) VALUES (?, ?)",
                                    (ts_now, json.dumps(i2c_snapshot, ensure_ascii=False)),
                                )
                                con.commit()
                        except Exception:
                            pass
                    if poll_state.last_sample is None:
                        poll_state.last_sample = {"timestamp": ts_now}
        except Exception:
            pass
        next_t += POLL_S
        time.sleep(max(0.0, next_t - time.monotonic()))


def main() -> None:
    logger.info("Starting inverter service...")

    try:
        logger.info("Calling db_init()...")
        db_init()
        logger.info("db_init() completed")
    except Exception:
        logger.exception("ERROR in db_init()")
        return

    try:
        logger.info("Calling relay_setup()...")
        relay_setup()
        logger.info("relay_setup() completed")
    except Exception:
        logger.exception("ERROR in relay_setup()")
        return

    try:
        logger.info("Starting poll_loop thread...")
        t = Thread(target=poll_loop, daemon=True)
        t.start()
        logger.info("poll_loop thread started")
    except Exception:
        logger.exception("ERROR starting poll_loop")
        return

    try:
        logger.info("Setting up signal handlers...")

        def _stop_sig(*_a):
            poll_state.stop_event.set()

        signal.signal(signal.SIGTERM, _stop_sig)
        signal.signal(signal.SIGINT, _stop_sig)
        atexit.register(_gpio_cleanup)
        logger.info("Signal handlers configured")
    except Exception:
        logger.exception("ERROR setting up signal handlers")
        return

    app = create_app()
    try:
        logger.info("Starting Flask app on %s...", PORT)
        app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)
    except Exception:
        logger.exception("ERROR starting Flask")


if __name__ == "__main__":
    logger.info("Script started, calling main()...")
    main()
    logger.info("main() returned")
