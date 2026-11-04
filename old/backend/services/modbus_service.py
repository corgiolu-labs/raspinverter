# -*- coding: utf-8 -*-
"""Modbus RTU register reads (pymodbus with minimalmodbus fallback)."""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Dict, Optional

from config import (
    MB_BAUD,
    MB_BYTES,
    MB_PARITY,
    MB_PORT,
    MB_STOP,
    MB_TIMEOUT,
    UNIT_ID,
)
from db import now_str
from models.register_map import REGS, SIGNED, blocks, to_signed16

logger = logging.getLogger(__name__)

try:
    from pymodbus.client import ModbusSerialClient  # pymodbus >= 3
except Exception:
    try:
        from pymodbus.client.sync import ModbusSerialClient  # legacy
    except Exception:
        ModbusSerialClient = None  # type: ignore

try:
    import minimalmodbus as _MINIMODBUS  # type: ignore
except Exception:
    _MINIMODBUS = None  # type: ignore

LAST_ERR: Optional[str] = None
LAST_OK: Optional[str] = None


def read_regs() -> Optional[Dict[str, Any]]:
    global LAST_ERR, LAST_OK
    if ModbusSerialClient is None:
        LAST_ERR = "pymodbus not available"
        if _MINIMODBUS is not None:
            out = _read_regs_minimalmodbus()
            if out is not None:
                LAST_ERR = None
                LAST_OK = now_str()
                return out
        return None
    cli = ModbusSerialClient(
        method="rtu",
        port=MB_PORT,
        baudrate=MB_BAUD,
        parity=MB_PARITY,
        stopbits=MB_STOP,
        bytesize=MB_BYTES,
        timeout=MB_TIMEOUT,
    )
    if not cli.connect():
        LAST_ERR = f"serial connection failed on {MB_PORT}"
        if _MINIMODBUS is not None:
            out = _read_regs_minimalmodbus()
            if out is not None:
                LAST_ERR = None
                LAST_OK = now_str()
                return out
        return None
    out: Dict[str, Any] = {}
    try:
        for start, block in blocks():
            count = block[-1][1] - start + 1
            rr = cli.read_holding_registers(start, count, unit=UNIT_ID)
            if hasattr(rr, "isError") and rr.isError():
                raise RuntimeError(f"Read error at {start}")
            regs = rr.registers
            for name, addr, scale in block:
                raw = int(regs[addr - start])
                if name in SIGNED:
                    raw = to_signed16(raw)
                out[name] = float(raw) * scale
        LAST_ERR = None
        LAST_OK = now_str()
        return out
    except Exception as e:
        LAST_ERR = str(e)
        if _MINIMODBUS is not None:
            out = _read_regs_minimalmodbus()
            if out is not None:
                LAST_ERR = None
                LAST_OK = now_str()
                return out
        return None
    finally:
        with contextlib.suppress(Exception):
            cli.close()


def _read_regs_minimalmodbus() -> Optional[Dict[str, Any]]:
    try:
        if _MINIMODBUS is None:
            return None
        inst = _MINIMODBUS.Instrument(str(MB_PORT), int(UNIT_ID))
        inst.serial.baudrate = int(MB_BAUD)
        inst.serial.bytesize = int(MB_BYTES)
        p = str(MB_PARITY).upper()
        import serial  # type: ignore

        if p == "E":
            inst.serial.parity = serial.PARITY_EVEN
        elif p == "O":
            inst.serial.parity = serial.PARITY_ODD
        else:
            inst.serial.parity = serial.PARITY_NONE
        inst.serial.stopbits = int(MB_STOP)
        inst.serial.timeout = float(MB_TIMEOUT)
        inst.mode = _MINIMODBUS.MODE_RTU

        out: Dict[str, Any] = {}
        for name, addr, scale in REGS:
            try:
                val = inst.read_register(int(addr), 0, functioncode=3, signed=(name in SIGNED))
                out[name] = float(val) * float(scale)
            except Exception:
                out[name] = None

        gv = float(out.get("grid_v") or 0.0)
        gw = float(out.get("grid_w") or 0.0)
        out["grid_a"] = (gw / gv) if gv else 0.0
        try:
            lw = float(out.get("load_w") or 0.0)
            lva = float(out.get("load_va") or 0.0)
            pf = out.get("load_pf")
            if (pf is None) or (float(pf or 0.0) <= 0.0):
                val = (abs(lw) / abs(lva)) if abs(lva) > 1e-6 else None
                out["load_pf"] = None if val is None else max(0.0, min(1.0, val))
        except Exception:
            pass
        return out
    except Exception:
        return None
