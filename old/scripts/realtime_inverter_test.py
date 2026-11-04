#!/usr/bin/env python3
"""
Realtime Inverter Test - Lettura registri Modbus in tempo reale (senza venv)

Uso rapido:
  python3 realtime_inverter_test.py --port /dev/serial0 --baud 9600 --unit-id 1 --interval 5
  python3 realtime_inverter_test.py --port /dev/ttyUSB0 --baud 9600 --unit-id 1 --interval 5

Note:
  - Parametri leggibili anche da variabili d'ambiente (vedi argparse defaults).
  - Stampa una riga per lettura con i principali parametri (PV, Batteria, Rete, Casa).
  - Opzione --show-all per stampare tutti i registri disponibili.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Compatibilità pymodbus v3 / legacy
try:
    from pymodbus.client import ModbusSerialClient  # >= 3.x
except Exception:
    try:
        from pymodbus.client.sync import ModbusSerialClient  # legacy
    except Exception:  # pragma: no cover
        ModbusSerialClient = None  # type: ignore

# Mappa registri (allineata a backend/models/register_map.py)
# (nome, indirizzo, scala)
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
SIGNED = {"battery_a", "battery_w"}  # estendibile (es. grid_w) se necessario


def to_signed16(x: int) -> int:
    return x - 0x10000 if x >= 0x8000 else x


def build_blocks(max_gap: int = 1, max_len: int = 16) -> List[Tuple[int, List[Tuple[str, int, float]]]]:
    """
    Raggruppa registri contigui per minimizzare le read_holding_registers.
    """
    items = sorted(REGS, key=lambda r: r[1])
    out: List[Tuple[int, List[Tuple[str, int, float]]]] = []
    cur: List[Tuple[str, int, float]] = []
    start: Optional[int] = None
    for name, addr, scale in items:
        if start is None:
            start = addr
            cur = [(name, addr, scale)]
            continue
        if (addr - cur[-1][1]) <= max_gap and (addr - start + 1) <= max_len:
            cur.append((name, addr, scale))
        else:
            out.append((start, cur))
            start = addr
            cur = [(name, addr, scale)]
    if start is not None:
        out.append((start, cur))
    return out


def read_once(cli: ModbusSerialClient, unit_id: int) -> Optional[Dict[str, Any]]:
    """
    Esegue una lettura completa dei registri definiti in REGS e calcola metriche derivate.
    """
    out: Dict[str, Any] = {}
    try:
        for start, block in build_blocks():
            count = block[-1][1] - start + 1
            rr = cli.read_holding_registers(start, count, unit=unit_id)
            if hasattr(rr, "isError") and rr.isError():
                raise RuntimeError(f"Read error at {start}")
            regs = rr.registers
            for name, addr, scale in block:
                raw = int(regs[addr - start])
                if name in SIGNED:
                    raw = to_signed16(raw)
                out[name] = float(raw) * scale

        # Derivati
        gv = float(out.get("grid_v") or 0.0)
        gw = float(out.get("grid_w") or 0.0)
        out["grid_a"] = (gw / gv) if gv else 0.0
        try:
            lw = float(out.get("load_w") or 0.0)
            lva = float(out.get("load_va") or 0.0)
            pf = out.get("load_pf")  # non letto direttamente
            if (pf is None) or (float(pf or 0.0) <= 0.0):
                val = (abs(lw) / abs(lva)) if abs(lva) > 1e-6 else None
                out["load_pf"] = None if val is None else max(0.0, min(1.0, val))
        except Exception:
            pass
        return out
    except Exception as e:
        print(f"[error] {e}", flush=True)
        return None


def format_summary(ts: str, s: Dict[str, Any]) -> str:
    """
    Riepilogo compatto per riga: PV, Batt, Rete, Casa
    """
    def n(v, d=1):
        try:
            return f"{float(v):.{d}f}"
        except Exception:
            return "-"

    pv_w = n(s.get("pv_w", 0), 0)
    pv_v = n(s.get("pv_v", 0), 1)
    pv_a = n(s.get("pv_a", 0), 1)

    batt_w = n(s.get("battery_w", 0), 0)
    batt_v = n(s.get("battery_v", 0), 1)
    batt_a = n(s.get("battery_a", 0), 1)

    grid_w = n(s.get("grid_w", 0), 0)
    grid_v = n(s.get("grid_v", 0), 1)
    grid_hz = n(s.get("grid_hz", 0), 2)
    grid_a = n(s.get("grid_a", 0), 1)

    load_w = n(s.get("load_w", 0), 0)
    load_v = n(s.get("load_v", 0), 1)
    load_a = n(s.get("load_a", 0), 1)
    load_pf = n(s.get("load_pf", 0), 2)

    return (
        f"{ts} | "
        f"PV {pv_w}W ({pv_v}V {pv_a}A) | "
        f"BAT {batt_w}W ({batt_v}V {batt_a}A) | "
        f"GRID {grid_w}W ({grid_v}V {grid_hz}Hz {grid_a}A) | "
        f"LOAD {load_w}W ({load_v}V {load_a}A PF={load_pf}"
        f")"
    )


def main() -> int:
    if ModbusSerialClient is None:
        print("[fatal] pymodbus non è installato. Installa: sudo pip3 install pymodbus", flush=True)
        return 2

    parser = argparse.ArgumentParser(description="Test lettura Modbus inverter (realtime)")
    parser.add_argument("--port", default=os.getenv("INVERTER_MODBUS_SERIAL_PORT", "/dev/serial0"),
                        help="Porta seriale (es. /dev/serial0, /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=int(os.getenv("INVERTER_MODBUS_BAUDRATE", "9600")),
                        help="Baudrate (default 9600)")
    parser.add_argument("--parity", default=os.getenv("INVERTER_MODBUS_PARITY", "N"),
                        choices=["N", "E", "O"], help="Parità (N/E/O)")
    parser.add_argument("--stopbits", type=int, default=int(os.getenv("INVERTER_MODBUS_STOPBITS", "1")),
                        choices=[1, 2], help="Stop bits")
    parser.add_argument("--bytesize", type=int, default=int(os.getenv("INVERTER_MODBUS_BYTESIZE", "8")),
                        choices=[7, 8], help="Byte size")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("INVERTER_MODBUS_TIMEOUT", "1.0")),
                        help="Timeout secondi")
    parser.add_argument("--unit-id", type=int, default=int(os.getenv("INVERTER_UNIT_ID", "1")),
                        help="Modbus unit id (slave id)")
    parser.add_argument("--interval", type=float, default=float(os.getenv("POLL_INTERVAL_SEC", "5")),
                        help="Intervallo tra letture (s)")
    parser.add_argument("--count", type=int, default=0, help="Numero letture (0 = infinito)")
    parser.add_argument("--show-all", action="store_true", help="Stampa tutti i registri letti")
    args = parser.parse_args()

    print(f"[info] Porta: {args.port}  Baud:{args.baud}  Parity:{args.parity}  Stop:{args.stopbits}  Bytes:{args.bytesize}  Timeout:{args.timeout}  Unit:{args.unit_id}", flush=True)

    cli = ModbusSerialClient(
        method="rtu",
        port=args.port,
        baudrate=args.baud,
        parity=args.parity,
        stopbits=args.stopbits,
        bytesize=args.bytesize,
        timeout=args.timeout,
    )
    if not cli.connect():
        print(f"[fatal] Connessione seriale fallita su {args.port}", flush=True)
        return 1

    print("[info] Connessione OK. Inizio lettura in tempo reale (Ctrl+C per interrompere)...", flush=True)
    reads_done = 0
    try:
        while True:
            s = read_once(cli, args.unit_id)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if s is None:
                print(f"{ts} | [warn] Nessun dato (errore lettura)", flush=True)
            else:
                if args.show_all:
                    # Stampa dizionario completo in ordine alfabetico
                    keys = sorted(s.keys())
                    parts = [f"{k}={s[k]}" for k in keys]
                    print(f"{ts} | " + " ".join(parts), flush=True)
                else:
                    print(format_summary(ts, s), flush=True)

            reads_done += 1
            if args.count > 0 and reads_done >= args.count:
                break
            time.sleep(max(0.0, args.interval))
    except KeyboardInterrupt:
        print("\n[info] Interrotto dall'utente.", flush=True)
    finally:
        try:
            cli.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())


