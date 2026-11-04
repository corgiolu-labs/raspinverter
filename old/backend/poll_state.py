# -*- coding: utf-8 -*-
"""Stato in-memory condiviso tra il thread di polling e le route HTTP.

- ``last_sample``: ultimo campione Modbus arricchito (timestamp + registri).
- ``stop_event`` / ``lock``: coordinamento arresto e accesso concorrente.

Nota: snapshot I2C per le API è in ``i2c_service.LAST_I2C``; esito ultima lettura
Modbus in ``modbus_service.LAST_OK`` / ``LAST_ERR`` (aggiornati da ``read_regs``).
"""
from __future__ import annotations

from threading import Event, Lock
from typing import Any, Dict, Optional

stop_event = Event()
lock = Lock()
last_sample: Optional[Dict[str, Any]] = None
