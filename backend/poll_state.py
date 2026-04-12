# -*- coding: utf-8 -*-
"""Shared in-memory state between the Modbus poll thread and HTTP handlers."""
from __future__ import annotations

from threading import Event, Lock
from typing import Any, Dict, Optional

stop_event = Event()
lock = Lock()
last_sample: Optional[Dict[str, Any]] = None
