#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Accesso hardware: I2C (ADS1115), Modbus RTU (inverter), GPIO/relè.
Estratto da inverter_api.py (refactor 2026-06-06) — nessuna logica cambiata.

Possiede lo stato mutabile letto dalle route: LAST_ERR, LAST_OK, RELAY_STATE.
Vanno letti come hardware.LAST_ERR / hardware.RELAY_STATE (non importati per nome).
"""
import time
import contextlib
from typing import Dict, Any, Optional, Tuple, List

from config import (
    I2C_ENABLED, I2C_BUS, I2C_DEVICES,
    MB_PORT, MB_BAUD, MB_PARITY, MB_STOP, MB_BYTES, MB_TIMEOUT, UNIT_ID,
    REGS, SIGNED, CONF, now_str,
)

# ---------------------------------------------------------------------------
# Optional I2C (SMBus) support
# ---------------------------------------------------------------------------
try:
    from smbus2 import SMBus, i2c_msg  # type: ignore
except Exception:
    SMBus = None  # type: ignore
    i2c_msg = None  # type: ignore

# ---------------------------------------------------------------------------
# Optional Modbus client (pymodbus 3.x or legacy 2.x)
# ---------------------------------------------------------------------------
_PYMB_V3 = False  # True = pymodbus 3.x API (no `method=`, uses `slave=`)
try:
    from pymodbus.client import ModbusSerialClient  # pymodbus >= 3
    _PYMB_V3 = True
except Exception:
    try:
        from pymodbus.client.sync import ModbusSerialClient  # legacy 2.x (`method=`/`unit=`)
        _PYMB_V3 = False
    except Exception:
        ModbusSerialClient = None  # type: ignore

try:
    import minimalmodbus as _MINIMODBUS  # type: ignore
except Exception:
    _MINIMODBUS = None  # type: ignore

# pymodbus ha rinominato il kwarg dell'unità tra le versioni:
#   2.x = unit  ·  3.0–3.6 = slave  ·  3.7+ = device_id
# Rileviamo quello giusto dalla firma reale del metodo.
_UNIT_KW = "unit"
if _PYMB_V3 and ModbusSerialClient is not None:
    try:
        import inspect as _inspect
        _p = _inspect.signature(ModbusSerialClient.read_holding_registers).parameters
        _UNIT_KW = "device_id" if "device_id" in _p else "slave"
    except Exception:
        _UNIT_KW = "slave"

# ---------------------------------------------------------------------------
# Stato modbus (mutabile, letto dalle route come hardware.LAST_ERR/LAST_OK)
# ---------------------------------------------------------------------------
LAST_ERR: Optional[str] = None
LAST_OK:  Optional[str] = None


# ---------------------------------------------------------------------------
# I2C reader (byte/word/block + ADS1115)
# ---------------------------------------------------------------------------
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
                        # Config words per channel, 100ms wait, 4.096V scale
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
                                bus.write_i2c_block_data(addr, 0x01, [(cfg >> 8) & 0xFF, cfg & 0xFF])
                                time.sleep(0.1)
                                data = bus.read_i2c_block_data(addr, 0x00, 2)
                                raw = (int(data[0]) << 8) | int(data[1])
                                if raw > 32767:
                                    raw -= 65535
                                volts = raw * (4.096 / 32768.0)
                                mv = volts * 1000.0
                                amp_per_mv = ch.get("amp_per_mv")
                                mv_per_amp = ch.get("mv_per_amp")
                                zero_offset_mv = ch.get("zero_offset_mv")
                                voltage_scale = ch.get("voltage_scale")
                                display_unit = ch.get("display_unit")
                                divider_top = ch.get("divider_top_ohm")
                                divider_bottom = ch.get("divider_bottom_ohm")
                                subtract_channel = ch.get("subtract_channel")
                                display_value: Optional[float] = None
                                display_unit_val: Optional[str] = display_unit

                                # Sensori a effetto Hall (es. WCS1800): a 0 A l'uscita e' VCC/2,
                                # quindi va sottratto l'offset di zero prima di ricavare la corrente.
                                # Lo shunt non ha offset (0 A = 0 V) e resta sui volt grezzi.
                                mv_corr = mv
                                if zero_offset_mv not in (None, ""):
                                    try:
                                        mv_corr = mv - float(zero_offset_mv)
                                    except Exception:
                                        mv_corr = mv

                                current_a: Optional[float] = None
                                if amp_per_mv not in (None, ""):
                                    try:
                                        amp_factor = float(amp_per_mv)
                                        current_a = mv_corr * amp_factor
                                    except Exception:
                                        current_a = None
                                elif mv_per_amp not in (None, ""):
                                    try:
                                        mv_per_amp_val = float(mv_per_amp)
                                        if mv_per_amp_val != 0:
                                            current_a = mv_corr / mv_per_amp_val
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
                        # Post-process subtract_channel (es. SERIE2 - SERIE1)
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
# Modbus
# ---------------------------------------------------------------------------
def _to_signed16(x: int) -> int:
    return x - 0x10000 if x >= 0x8000 else x


def _blocks(max_gap=1, max_len=16):
    items = sorted(REGS, key=lambda r: r[1])
    out: List[Tuple[int, List[Tuple[str, int, float]]]] = []
    cur: List[Tuple[str, int, float]] = []
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


def read_regs() -> Optional[Dict[str, Any]]:
    """Read inverter registers via pymodbus, falling back to minimalmodbus.

    Hardened for pymodbus 2.x AND 3.x: any pymodbus failure (incl.
    constructor/signature errors on 3.x) falls through to minimalmodbus.
    """
    global LAST_ERR, LAST_OK
    if ModbusSerialClient is not None:
        out = _read_regs_pymodbus()
        if out is not None:
            LAST_ERR = None; LAST_OK = now_str()
            return out
    if _MINIMODBUS is not None:
        out = _read_regs_minimalmodbus()
        if out is not None:
            LAST_ERR = None; LAST_OK = now_str()
            return out
    if ModbusSerialClient is None and _MINIMODBUS is None:
        LAST_ERR = "no modbus library available (install pymodbus or minimalmodbus)"
    return None


def _read_regs_pymodbus() -> Optional[Dict[str, Any]]:
    """pymodbus read path, version-agnostic (2.x `method=`/`unit=` vs 3.x `slave=`)."""
    global LAST_ERR
    cli = None
    try:
        if _PYMB_V3:
            cli = ModbusSerialClient(port=MB_PORT, baudrate=MB_BAUD, parity=MB_PARITY,
                                     stopbits=MB_STOP, bytesize=MB_BYTES, timeout=MB_TIMEOUT)
        else:
            cli = ModbusSerialClient(method="rtu", port=MB_PORT, baudrate=MB_BAUD,
                                     parity=MB_PARITY, stopbits=MB_STOP, bytesize=MB_BYTES,
                                     timeout=MB_TIMEOUT)
        if not cli.connect():
            LAST_ERR = f"serial connection failed on {MB_PORT}"
            return None
        out: Dict[str, Any] = {}
        for start, block in _blocks():
            count = block[-1][1] - start + 1
            if _PYMB_V3:
                rr = cli.read_holding_registers(start, count=count, **{_UNIT_KW: UNIT_ID})
            else:
                rr = cli.read_holding_registers(start, count, unit=UNIT_ID)
            if hasattr(rr, "isError") and rr.isError():
                raise RuntimeError(f"Read error at {start}")
            regs = rr.registers
            for name, addr, scale in block:
                raw = int(regs[addr - start])
                if name in SIGNED:
                    raw = _to_signed16(raw)
                out[name] = float(raw) * scale
        return out
    except Exception as e:
        LAST_ERR = str(e)
        return None
    finally:
        with contextlib.suppress(Exception):
            if cli is not None:
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
        inst.serial.timeout  = float(MB_TIMEOUT)
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
            lw  = float(out.get("load_w") or 0.0)
            lva = float(out.get("load_va") or 0.0)
            pf  = out.get("load_pf")
            if (pf is None) or (float(pf or 0.0) <= 0.0):
                val = (abs(lw) / abs(lva)) if abs(lva) > 1e-6 else None
                out["load_pf"] = None if val is None else max(0.0, min(1.0, val))
        except Exception:
            pass
        return out
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GPIO (astrazione su RPi.GPIO e lgpio)
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

_GPIO_CTX = {"h": None, "pin": None}


def _gpio_setup_output(pin: int, initial_high: bool) -> bool:
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
# Relay control (GPIO 17 / physical pin 11). Stato letto come hardware.RELAY_STATE
# ---------------------------------------------------------------------------
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
    off_level_high = False if active_high else True
    ok = _gpio_setup_output(pin, off_level_high)

    global RELAY_STATE, RELAY_LAST_TOGGLE
    RELAY_STATE = False
    RELAY_LAST_TOGGLE = time.monotonic()
    rb = _gpio_read(pin)
    print(f"[relay] SETUP: backend={GPIO_BACKEND} pin={pin} active_high={active_high} "
          f"-> OFF (initial level_high={off_level_high}, setup_ok={ok}, readback={rb})", flush=True)


# ---------------------------------------------------------------------------
# Bilanciamento banchi (schema definitivo 2026-07): 2 CARICATORI INDIPENDENTI isolati, un rele' per
# banco (rele1=GPIO17=caricatore Banco1, rele2=GPIO27=caricatore Banco2 - vedi balance_set).
# Misura A IMPULSI (duty-cycle): lo switching del caricatore perturba l'ADC SERIE mentre carica,
# quindi si carica a impulsi e si MISURA solo a caricatore SPENTO (lettura pulita) - vedi balance_step.
# Default DISATTIVATO (balance.enabled=false): abilitare dopo aver verificato il cablaggio.
# ---------------------------------------------------------------------------
BALANCE_STATE: int = 0          # 0=nessuno, 1=banco1 (SERIE1), 2=banco2 (SERIE2)
BALANCE_SINCE: float = 0.0      # monotonic: inizio carica del banco corrente
BALANCE_LAST_TOGGLE: float = 0.0
_balance_manual_until: float = 0.0   # override test cablaggio: tiene il banco fino a questo istante
_balance_manual_bank: int = 0
_bal_phase: str = "idle"        # duty-cycle: "idle"(misura) / "charging"(impulso) / "settle"(attesa dopo off)
_bal_phase_until: float = 0.0   # monotonic: fine della fase corrente
_bal_session: bool = False      # True mentre un ciclo di bilanciamento e' in corso (isteresi start/stop)

def balance_setup():
    """Init GPIO dei 2 rele' di bilanciamento -> entrambi OFF (sicuro al boot)."""
    global BALANCE_STATE, BALANCE_LAST_TOGGLE
    cfg = CONF.get("balance", {})
    if GPIO_BACKEND is None:
        print("[balance] Setup skip: GPIO non disponibile", flush=True)
        return
    active_high = bool(cfg.get("active_high", True))
    off_level = (not active_high)
    p1 = int(cfg.get("gpio_pin_bank1", 23))
    p2 = int(cfg.get("gpio_pin_bank2", 24))
    _gpio_setup_output(p1, off_level)
    _gpio_setup_output(p2, off_level)
    BALANCE_STATE = 0
    BALANCE_LAST_TOGGLE = time.monotonic()
    print(f"[balance] SETUP pins=({p1},{p2}) active_high={active_high} -> entrambi OFF", flush=True)

def balance_set(bank: int):
    """Schema definitivo (2026-06, 2 CARICATORI INDIPENDENTI isolati, UN rele' per banco):
    rele1 = gpio_pin_bank1 = ingresso 230V del caricatore del Banco1;
    rele2 = gpio_pin_bank2 = ingresso 230V del caricatore del Banco2.
    bank 1 -> carica Banco1 (rele1 ON, rele2 OFF); bank 2 -> carica Banco2 (rele2 ON, rele1 OFF);
    0 -> entrambi OFF. I caricatori sono isolati e indipendenti: nessuno stato intermedio pericoloso.
    Si accende al piu' un caricatore per volta (l'altro viene spento per primo)."""
    global BALANCE_STATE, BALANCE_SINCE, BALANCE_LAST_TOGGLE
    bank = int(bank)
    if bank == BALANCE_STATE:
        return
    cfg = CONF.get("balance", {})
    if GPIO_BACKEND is None:
        BALANCE_STATE = bank
        return
    active_high = bool(cfg.get("active_high", True))
    on_level = active_high
    off_level = (not active_high)
    relay1 = int(cfg.get("gpio_pin_bank1", 17))   # caricatore Banco1
    relay2 = int(cfg.get("gpio_pin_bank2", 27))   # caricatore Banco2
    if bank == 1:
        _gpio_write(relay2, off_level)            # spegni l'altro caricatore per primo
        _gpio_write(relay1, on_level)
    elif bank == 2:
        _gpio_write(relay1, off_level)
        _gpio_write(relay2, on_level)
    else:
        _gpio_write(relay1, off_level)
        _gpio_write(relay2, off_level)
    BALANCE_SINCE = time.monotonic() if bank in (1, 2) else 0.0
    BALANCE_STATE = bank
    BALANCE_LAST_TOGGLE = time.monotonic()
    _lbl = f"carica Banco{bank}" if bank in (1, 2) else "OFF (nessuna carica)"
    print(f"[balance] SET -> {_lbl} (rele1=GPIO{relay1}, rele2=GPIO{relay2})", flush=True)

def balance_manual(bank, seconds: float = 30.0):
    """Test cablaggio: forza un banco per 'seconds', poi torna all'automatico."""
    global _balance_manual_until, _balance_manual_bank
    bank = int(bank)
    _balance_manual_bank = bank if bank in (1, 2) else 0
    _balance_manual_until = (time.monotonic() + float(seconds)) if bank in (1, 2) else 0.0
    balance_set(_balance_manual_bank)

def _bank_voltages(i2c):
    cfg = CONF.get("balance", {})
    dev = cfg.get("source_device", "adc_mod2")
    mod = (i2c or {}).get(dev, {}) if isinstance(i2c, dict) else {}
    def _v(ch):
        c = mod.get(ch) if isinstance(mod, dict) else None
        return c.get("value") if isinstance(c, dict) else c
    return _v(cfg.get("bank1_channel", "SERIE1")), _v(cfg.get("bank2_channel", "SERIE2"))

def balance_step(i2c):
    """Bilanciamento a IMPULSI (duty-cycle): lo switching del caricatore perturba l'ADC SERIE mentre
    carica, quindi si MISURA solo a caricatore SPENTO (lettura pulita), poi si carica il banco piu'
    basso per un impulso, si spegne, si assesta e si rimisura. Isteresi start/stop; OFF se disabilitato."""
    global BALANCE_STATE, _bal_phase, _bal_phase_until, _bal_session
    cfg = CONF.get("balance", {})
    now = time.monotonic()
    # Override manuale (test cablaggio): tiene il banco scelto per N secondi
    if now < _balance_manual_until:
        if BALANCE_STATE != _balance_manual_bank:
            balance_set(_balance_manual_bank)
        return
    if not bool(cfg.get("enabled", False)) or GPIO_BACKEND is None:
        if BALANCE_STATE != 0:
            balance_set(0)
        _bal_phase = "idle"; _bal_session = False
        return

    pulse_s = float(cfg.get("pulse_seconds", 60))
    settle_s = float(cfg.get("settle_seconds", 8))
    start_diff = float(cfg.get("start_diff_v", 0.3))
    stop_diff = float(cfg.get("stop_diff_v", 0.1))
    max_bank_v = float(cfg.get("max_bank_v", 28.0))

    # Fase CARICA: impulso in corso -> NON leggere (lettura perturbata), tieni acceso
    if _bal_phase == "charging":
        if now < _bal_phase_until:
            return
        balance_set(0)                          # fine impulso -> spegni il caricatore
        _bal_phase = "settle"; _bal_phase_until = now + settle_s
        return
    # Fase SETTLE: caricatore spento, aspetta che la lettura si pulisca
    if _bal_phase == "settle":
        if now < _bal_phase_until:
            return
        _bal_phase = "idle"                     # pronto a misurare

    # Fase MISURA/DECIDI: caricatore SPENTO -> lettura pulita e affidabile
    s1, s2 = _bank_voltages(i2c)
    if s1 is None or s2 is None:
        if BALANCE_STATE != 0:
            balance_set(0)
        return
    s1 = float(s1); s2 = float(s2)
    diff = s1 - s2
    thr = stop_diff if _bal_session else start_diff   # isteresi: parte a start_diff, continua fino a stop_diff
    if abs(diff) >= thr:
        lower = 1 if s1 < s2 else 2
        lower_v = s1 if lower == 1 else s2
        if lower_v < max_bank_v:
            _bal_session = True
            balance_set(lower)                  # accendi il caricatore del banco piu' basso
            _bal_phase = "charging"; _bal_phase_until = now + pulse_s
            return
    # bilanciato o banco pieno -> chiudi il ciclo, resta spento
    _bal_session = False
    if BALANCE_STATE != 0:
        balance_set(0)

def balance_status(i2c=None):
    """Stato corrente per UI/endpoint."""
    cfg = CONF.get("balance", {})
    s1, s2 = _bank_voltages(i2c)
    diff = (float(s1) - float(s2)) if (s1 is not None and s2 is not None) else None
    manual_left = max(0.0, _balance_manual_until - time.monotonic())
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "charging_bank": BALANCE_STATE,
        "phase": _bal_phase,
        "serie1_v": s1, "serie2_v": s2,
        "diff_v": round(diff, 3) if diff is not None else None,
        "start_diff_v": float(cfg.get("start_diff_v", 0.3)),
        "stop_diff_v": float(cfg.get("stop_diff_v", 0.1)),
        "max_bank_v": float(cfg.get("max_bank_v", 28.0)),
        "manual_test_sec_left": round(manual_left, 1),
        "gpio": [int(cfg.get("gpio_pin_bank1", 23)), int(cfg.get("gpio_pin_bank2", 24))],
        "active_high": bool(cfg.get("active_high", True)),
        "available": GPIO_BACKEND is not None,
    }

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
# F4: controllo rete ENEL balance-aware (gated da apply.grid). INVARIANTE:
# puo' solo ANTICIPARE l'aggancio ENEL rispetto a relay_auto_step, mai ritardarlo.
# ---------------------------------------------------------------------------
_VBUF = []   # [(monotonic_t, total_v), ...] finestra ~3 min per stimare il rate di scarica
GRID_CTRL = {"f4_on": None, "reason": "", "weaker_v": None, "rate_v_min": None,
             "applied": False, "_last_log": 0.0}

def _bank_v_from_i2c(i2c):
    """SERIE1/SERIE2 (tensioni banchi) dallo snapshot i2c. (None, None) se assenti."""
    cfg = CONF.get("balance", {})
    mod = (i2c or {}).get(cfg.get("source_device", "adc_mod2"), {}) if isinstance(i2c, dict) else {}
    def _v(ch):
        c = mod.get(ch) if isinstance(mod, dict) else None
        return c.get("value") if isinstance(c, dict) else None
    s1, s2 = _v(cfg.get("bank1_channel", "SERIE1")), _v(cfg.get("bank2_channel", "SERIE2"))
    try:
        return (float(s1) if s1 is not None else None, float(s2) if s2 is not None else None)
    except Exception:
        return (None, None)

def _f4_decision(battery_v, i2c):
    """Decisione F4 in tempo reale: ENEL ora se V_tot<=trigger OPPURE il banco piu' debole
    scenderebbe sotto il floor (cutoff+margine) entro enel_sync_s al rate di scarica corrente.
    Ritorna (want_on: bool, reason: str, weaker_v, rate_v_min)."""
    g = CONF.get("grid", {})
    cutoff_v = float(g.get("bank_cutoff_v", 22.2))         # DATOU BOSS over-discharge
    floor_v = cutoff_v + float(g.get("bank_safety_v", 1.3))
    trigger_v = float(g.get("trigger_v", 46.0))
    sync_s = float(g.get("enel_sync_s", 60.0))
    rate = None
    if len(_VBUF) >= 2:
        t0, v0 = _VBUF[0]; t1, v1 = _VBUF[-1]
        dtm = (t1 - t0) / 60.0
        if dtm > 0:
            rate = (v1 - v0) / dtm
    s1, s2 = _bank_v_from_i2c(i2c)
    weaker_v = min(s1, s2) if (s1 is not None and s2 is not None) else None
    reasons = []
    want = False
    if battery_v is not None and battery_v <= trigger_v:
        want = True
        reasons.append(f"V {battery_v:.1f}<={trigger_v:.0f}")
    if weaker_v is not None:
        proj = (weaker_v + (rate / 2.0) * (sync_s / 60.0)) if (rate is not None and rate < 0) else weaker_v
        if proj <= floor_v:
            want = True
            reasons.append(f"banco debole {weaker_v:.1f}->{proj:.1f}<={floor_v:.1f}V/{sync_s:.0f}s")
    elif battery_v is not None and battery_v <= trigger_v + 1.0:
        want = True
        reasons.append("dati banchi assenti vicino soglia (fail-safe)")
    return want, " · ".join(reasons), weaker_v, rate

def grid_control_step(battery_v, i2c=None):
    """apply.grid OFF -> relay_auto_step INVARIATO (+ log dry-run di cosa farebbe la F4).
    apply.grid ON  -> ENEL = relay_auto_step OR decisione F4 (mai ritardo); spegne solo a
    recupero (V>=off_v, bilanciato, nessun rischio F4). Fail-safe + min_toggle anti-flap."""
    now = time.monotonic()
    if battery_v is not None:
        _VBUF.append((now, float(battery_v)))
        while _VBUF and _VBUF[0][0] < now - 180:
            _VBUF.pop(0)
    apply_on = bool(CONF.get("apply", {}).get("grid", False))
    want_f4, reason, weaker_v, rate = _f4_decision(battery_v, i2c)
    GRID_CTRL.update({"f4_on": want_f4, "reason": reason, "weaker_v": weaker_v,
                      "rate_v_min": (round(rate, 3) if rate is not None else None), "applied": apply_on})

    if not apply_on:
        relay_auto_step(battery_v)   # comportamento attuale, INVARIATO
        if want_f4 and RELAY_STATE is not True and (now - GRID_CTRL["_last_log"]) > 60:
            GRID_CTRL["_last_log"] = now
            print(f"[grid][dry-run] F4 avrebbe attivato ENEL ora: {reason}", flush=True)
        return

    cfg = CONF.get("relay", {})
    if not bool(cfg.get("enabled", False)) or battery_v is None:
        return
    on_v = float(cfg.get("on_v", 47.5))
    off_v = float(cfg.get("off_v", 49.0))
    min_gap = max(0, int(cfg.get("min_toggle_sec", 5)))
    s1, s2 = _bank_v_from_i2c(i2c)
    balanced = (abs(s1 - s2) <= 0.10 * ((s1 + s2) / 2.0)) if (s1 is not None and s2 is not None) else True
    want_on = (battery_v <= on_v) or want_f4               # OR -> mai dopo relay_auto_step
    want_off = (battery_v >= off_v) and balanced and (not want_f4)
    cur = RELAY_STATE
    target = True if want_on else (False if want_off else cur)
    if target != cur and (now - RELAY_LAST_TOGGLE) >= min_gap:
        relay_apply(bool(target))
        print(f"[grid][APPLY] ENEL {'ON' if target else 'OFF'}: "
              f"{reason if target else ('V>=%.0f e bilanciato' % off_v)}", flush=True)
