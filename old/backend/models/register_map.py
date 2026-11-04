# -*- coding: utf-8 -*-
"""Modbus holding-register map (names, addresses, scales)."""
from __future__ import annotations

from typing import List, Tuple

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


def to_signed16(x: int) -> int:
    return x - 0x10000 if x >= 0x8000 else x


def blocks(max_gap: int = 1, max_len: int = 16):
    """Group contiguous registers for batched Modbus reads."""
    items = sorted(REGS, key=lambda r: r[1])
    out: List[Tuple[int, List[Tuple[str, int, float]]]] = []
    cur: List[Tuple[str, int, float]] = []
    start = None
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
