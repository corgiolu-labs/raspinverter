# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import tests._path_setup  # noqa: F401


class TestAppSmoke(unittest.TestCase):
    def test_create_app_does_not_crash(self):
        from app import create_app

        app = create_app()
        self.assertIsNotNone(app)
        self.assertTrue(any(r.rule == "/api/health" for r in app.url_map.iter_rules()))


if __name__ == "__main__":
    unittest.main()
