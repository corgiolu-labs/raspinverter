# -*- coding: utf-8 -*-
"""Registrazione aggregata delle route Flask (stessi path del monolite)."""
from __future__ import annotations

import logging

from flask import Flask

from routes.analysis_routes import register_analysis_routes
from routes.battery_routes import register_battery_routes
from routes.config_routes import register_config_routes
from routes.energy_routes import register_energy_routes
from routes.health_routes import register_health_routes
from routes.i2c_routes import register_i2c_routes
from routes.inverter_routes import register_inverter_routes
from routes.relay_routes import register_relay_routes
from routes.static_routes import register_static_routes

logger = logging.getLogger(__name__)


def register_routes(app: Flask) -> None:
    from daily_analyzer import DailyAnalyzer

    daily_analyzer = DailyAnalyzer()

    register_static_routes(app)
    register_health_routes(app)
    register_analysis_routes(app, daily_analyzer)
    register_config_routes(app)
    register_inverter_routes(app)
    register_i2c_routes(app)
    register_energy_routes(app)
    register_battery_routes(app)
    register_relay_routes(app)

    logger.info("All route modules registered")
