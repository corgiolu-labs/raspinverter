# -*- coding: utf-8 -*-
"""Test client: /api/inverter con DB vuoto o con campioni."""
from __future__ import annotations

import unittest
from datetime import datetime

from tests.helpers import clear_samples_and_counters, get_test_client, insert_sample


class TestApiInverter(unittest.TestCase):
    def test_inverter_200_json_empty_db(self):
        clear_samples_and_counters()
        c = get_test_client()
        r = c.get("/api/inverter")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIsInstance(data, dict)
        self.assertIn("timestamp", data)
        self.assertIn("relay", data)
        self.assertIn("last_ok", data)

    def test_inverter_includes_battery_sample_fields(self):
        clear_samples_and_counters()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        insert_sample(ts, pv_w=500.0, battery_v=52.0, battery_w=-100.0)
        c = get_test_client()
        r = c.get("/api/inverter")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data.get("pv_w"), 500.0)
        self.assertEqual(data.get("battery_v"), 52.0)


if __name__ == "__main__":
    unittest.main()
