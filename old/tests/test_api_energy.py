# -*- coding: utf-8 -*-
"""Test client: history, energy, totals/today."""
from __future__ import annotations

import unittest
from datetime import datetime

from tests.helpers import clear_samples_and_counters, get_test_client, insert_sample


class TestApiEnergy(unittest.TestCase):
    def setUp(self) -> None:
        clear_samples_and_counters()

    def test_history_returns_list_with_expected_keys(self):
        now = datetime.now()
        ts = now.replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        insert_sample(ts, pv_w=10.0, battery_w=20.0, load_w=30.0, grid_w=40.0)
        c = get_test_client()
        r = c.get("/api/history")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        row = data[0]
        self.assertIn("timestamp", row)
        self.assertIn("pv_w", row)

    def test_energy_hour_has_unit_and_data(self):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        insert_sample(ts, pv_w=100.0, battery_w=0.0, load_w=0.0, grid_w=0.0)
        c = get_test_client()
        r = c.get("/api/energy?granularity=hour")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("unit", data)
        self.assertIn("data", data)
        self.assertIsInstance(data["data"], list)

    def test_totals_today_expected_keys(self):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        insert_sample(ts, pv_w=50.0, battery_w=10.0, load_w=5.0, grid_w=2.0)
        c = get_test_client()
        r = c.get("/api/totals/today")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("unit", data)
        self.assertIn("battery_counter_info", data)
        self.assertIn("pv_kWh", data)


if __name__ == "__main__":
    unittest.main()
