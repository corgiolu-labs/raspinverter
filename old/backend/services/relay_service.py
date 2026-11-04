# -*- coding: utf-8 -*-
"""GPIO relay control (RPi.GPIO / lgpio)."""
from __future__ import annotations

import logging
import time
from typing import Optional

from config import CONF

logger = logging.getLogger(__name__)

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

logger.info("GPIO backend selected: %s", GPIO_BACKEND)
logger.info(
    "GPIO DEBUG backend=%s RGPIO_loaded=%s LGPIO_loaded=%s",
    GPIO_BACKEND,
    RGPIO is not None,
    LGPIO is not None,
)

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
            logger.warning("RPi.GPIO setup error: %s", e)
            return False
    elif GPIO_BACKEND == "lgpio":
        try:
            h = _GPIO_CTX.get("h") or LGPIO.gpiochip_open(0)
            _GPIO_CTX["h"] = h
            LGPIO.gpio_claim_output(h, pin, LGPIO.SET_HIGH if initial_high else LGPIO.SET_LOW)
            _GPIO_CTX["pin"] = pin
            return True
        except Exception as e:
            logger.warning("lgpio setup error: %s", e)
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
            logger.warning("RPi.GPIO write error: %s", e)
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
            logger.warning("lgpio write error: %s", e)
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
        logger.info(
            "relay APPLY hw_on=%s active_high=%s level_high=%s write_ok=%s readback=%s",
            hw_on,
            active_high,
            level_high,
            ok,
            rb,
        )
    else:
        logger.info("relay GPIO not available or mode!=gpio, skip")

    RELAY_STATE = hw_on
    RELAY_LAST_TOGGLE = time.monotonic()

def relay_setup():
    """Init GPIO (if available) and set relay to logical OFF."""
    cfg = CONF.get("relay", {})
    if GPIO_BACKEND is None or str(cfg.get("mode", "gpio")).lower() != "gpio":
        logger.info("relay Setup skipped: GPIO not available or mode!=gpio")
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

    logger.info(
        "relay SETUP backend=%s pin=%s active_high=%s OFF initial_level_high=%s setup_ok=%s readback=%s",
        GPIO_BACKEND,
        pin,
        active_high,
        off_level_high,
        ok,
        rb,
    )



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
