# -*- coding: utf-8 -*-
"""Optional I2C / ADS1115 reads (smbus2)."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from config import I2C_BUS, I2C_DEVICES, I2C_ENABLED

logger = logging.getLogger(__name__)

try:
    from smbus2 import SMBus, i2c_msg  # type: ignore
except Exception:
    SMBus = None  # type: ignore
    i2c_msg = None  # type: ignore

LAST_I2C: Optional[Dict[str, Any]] = None

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
