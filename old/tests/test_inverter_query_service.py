# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

import tests._path_setup  # noqa: F401


class TestInverterQueryService(unittest.TestCase):
    @patch("services.inverter_query_service._fetch_battery_net_wh", return_value=42.0)
    def test_build_inverter_payload_prefers_fresher_db_sample(self, _mock_net):
        from services.inverter_query_service import build_inverter_payload

        db_sample = {
            "timestamp": "2026-01-01 12:00:00",
            "battery_v": 52.0,
            "id": 1,
        }
        mem_sample = {
            "timestamp": "2026-01-01 11:00:00",
            "battery_v": 50.0,
        }
        payload = build_inverter_payload(
            db_sample=db_sample,
            mem_sample=mem_sample,
            i2c_snapshot=None,
            last_ok="2026-01-01 12:00:00",
            last_err=None,
            relay_state=False,
            now=datetime(2026, 1, 1, 12, 5, 0),
        )
        self.assertEqual(payload["battery_v"], 52.0)
        self.assertEqual(payload["last_ok"], "2026-01-01 12:00:00")
        self.assertEqual(payload["battery_net_wh"], 42.0)
        self.assertIn("relay", payload)
        self.assertFalse(payload["relay"]["state"])

    @patch("services.inverter_query_service._fetch_battery_net_wh", return_value=0.0)
    def test_build_inverter_payload_includes_i2c_when_present(self, _mock_net):
        from services.inverter_query_service import build_inverter_payload

        payload = build_inverter_payload(
            db_sample=None,
            mem_sample={"timestamp": "2026-01-01 10:00:00"},
            i2c_snapshot={"dev": {"A0": 1.2}},
            last_ok=None,
            last_err="serial",
            relay_state=None,
            now=datetime(2026, 1, 1, 10, 0, 0),
        )
        self.assertEqual(payload["i2c"], {"dev": {"A0": 1.2}})
        self.assertEqual(payload["last_error"], "serial")


if __name__ == "__main__":
    unittest.main()
