# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime

import tests._path_setup  # noqa: F401


class TestEnergyHelpers(unittest.TestCase):
    def test_normalize_energy_unit_defaults_to_kwh(self):
        from services.energy_query_service import normalize_energy_unit

        u, scale, suffix = normalize_energy_unit("bogus")
        self.assertEqual(u, "kwh")
        self.assertEqual(scale, 1.0 / 1000.0)
        self.assertEqual(suffix, "_kWh")

    def test_parse_energy_window_hour_same_day(self):
        from services.energy_query_service import parse_energy_window

        now = datetime(2026, 6, 15, 14, 30, 0)
        start, end, step = parse_energy_window(
            "hour", now, date_str="2026-06-15", from_str=None, min_year_from_samples=None
        )
        self.assertEqual(step, "hour")
        self.assertEqual(start, datetime(2026, 6, 15, 0, 0, 0))
        self.assertEqual(end, now)

    def test_build_minute_history_series_fills_gaps(self):
        from services.energy_query_service import build_minute_history_series

        now = datetime(2026, 1, 1, 0, 2, 0)
        rows = [
            {
                "ts_min": "2026-01-01 00:00:00",
                "pv_w": 1.0,
                "battery_w": 2.0,
                "load_w": 3.0,
                "grid_w": 4.0,
            }
        ]
        out = build_minute_history_series(now, rows)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["timestamp"], "2026-01-01 00:00:00")
        self.assertEqual(out[0]["pv_w"], 1.0)
        self.assertIsNone(out[1]["pv_w"])


if __name__ == "__main__":
    unittest.main()
