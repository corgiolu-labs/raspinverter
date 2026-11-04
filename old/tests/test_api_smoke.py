# -*- coding: utf-8 -*-
"""Test client: health e endpoint test."""
from __future__ import annotations

import unittest

from tests.helpers import get_test_client


class TestApiSmoke(unittest.TestCase):
    def test_health_200_and_status(self):
        c = get_test_client()
        r = c.get("/api/health")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIsNotNone(data)
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("db_path", data)
        self.assertIn("serial", data)

    def test_api_test_200(self):
        c = get_test_client()
        r = c.get("/api/test")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data.get("ok"))


if __name__ == "__main__":
    unittest.main()
