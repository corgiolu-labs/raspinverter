# -*- coding: utf-8 -*-
"""Test client: GET/POST /api/config."""
from __future__ import annotations

import unittest

from tests.helpers import get_test_client


class TestApiConfig(unittest.TestCase):
    def test_config_get_200_and_keys(self):
        c = get_test_client()
        r = c.get("/api/config")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("battery", data)
        self.assertIn("ui", data)
        self.assertIn("relay", data)
        self.assertIn("type", data["battery"])

    def test_config_post_valid_200(self):
        c = get_test_client()
        payload = {
            "battery": {"nominal_voltage": 48.0, "nominal_ah": 100},
            "ui": {"unit": "W"},
        }
        r = c.post("/api/config", json=payload)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        data = r.get_json()
        self.assertTrue(data.get("ok"))

    def test_config_post_invalid_400(self):
        c = get_test_client()
        payload = {"battery": {"nominal_voltage": 999.0}}
        r = c.post("/api/config", json=payload)
        self.assertEqual(r.status_code, 400)
        data = r.get_json()
        self.assertFalse(data.get("ok"))
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
