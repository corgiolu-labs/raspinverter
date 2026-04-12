# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import tests._path_setup  # noqa: F401


class TestConfigValidation(unittest.TestCase):
    def test_valid_minimal_battery_and_ui(self):
        from config import validate_config

        data = {
            "battery": {"nominal_voltage": 51.2, "nominal_ah": 400},
            "ui": {"unit": "W"},
        }
        ok, err = validate_config(data)
        self.assertTrue(ok, err)
        self.assertEqual(err, "")

    def test_invalid_nominal_voltage_too_high(self):
        from config import validate_config

        data = {"battery": {"nominal_voltage": 200.0}}
        ok, err = validate_config(data)
        self.assertFalse(ok)
        self.assertIn("Voltage", err)

    def test_invalid_relay_off_v_not_greater_than_on_v(self):
        from config import validate_config

        data = {"relay": {"on_v": 50.0, "off_v": 48.0}}
        ok, err = validate_config(data)
        self.assertFalse(ok)
        self.assertIn("off_v", err)


if __name__ == "__main__":
    unittest.main()
