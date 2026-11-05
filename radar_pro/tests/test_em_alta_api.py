# -*- coding: utf-8 -*-
"""Endpoints /em-alta e /api/em_alta (stub do youtube_analyzer local)."""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("youtube_analyzer", types.ModuleType("youtube_analyzer"))
try:
    import flask  # noqa: F401
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask não instalado")
class TestEndpoints(unittest.TestCase):
    def setUp(self):
        import app
        import db
        import em_alta
        self.tmp = tempfile.TemporaryDirectory()
        self.old = (db.DB_PATH, em_alta.LAST_SCAN)
        db.DB_PATH = os.path.join(self.tmp.name, "r.db")
        em_alta.LAST_SCAN = os.path.join(self.tmp.name, "last.json")
        db.init_db()
        self.db, self.em = db, em_alta
        self.c = app.app.test_client()

    def tearDown(self):
        self.db.DB_PATH, self.em.LAST_SCAN = self.old
        self.tmp.cleanup()

    def test_pagina_e_api_vazia(self):
        self.assertEqual(self.c.get("/em-alta").status_code, 200)
        d = self.c.get("/api/em_alta").get_json()
        self.assertEqual(d["windows"], {})
        self.assertEqual([k for k, _ in d["window_labels"]], ["24h", "2-3d", "4-6d"])
        self.assertTrue(any(n["adjacente"] for n in d["nichos"]))

    def test_filtros(self):
        import json
        res = {"ts": "x", "total": 2, "windows": {"24h": [
            {"video_id": "a", "nicho": "guitarra", "outlier": True, "vph": 9},
            {"video_id": "b", "nicho": "rural", "outlier": False, "vph": 1}]}}
        with open(self.em.LAST_SCAN, "w") as f:
            json.dump(res, f)
        d = self.c.get("/api/em_alta?nicho=rural").get_json()
        self.assertEqual([v["video_id"] for v in d["windows"]["24h"]], ["b"])
        d = self.c.get("/api/em_alta?outliers=1").get_json()
        self.assertEqual([v["video_id"] for v in d["windows"]["24h"]], ["a"])


if __name__ == "__main__":
    unittest.main()
