# -*- coding: utf-8 -*-
"""Testes do 🔥 Em alta agora (sem rede, sem API)."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import em_alta  # noqa: E402

NOW = 1_800_000_000.0
H = 3600


def v(vid, hours, views, cid="UC1", nicho="guitarra"):
    return {"video_id": vid, "title": vid, "channel": cid, "channel_id": cid,
            "views": views, "published": NOW - hours * H, "nicho": nicho, "nicho_label": nicho}


class TestCalculo(unittest.TestCase):
    def test_vph_e_janelas(self):
        self.assertEqual(em_alta.vph(1000, 10), 100.0)
        self.assertEqual(em_alta.vph(500, 0.2), 500.0)  # mínimo 1 h
        self.assertEqual(em_alta.window_for(5), "24h")
        self.assertEqual(em_alta.window_for(30), "2-3d")
        self.assertEqual(em_alta.window_for(100), "4-6d")
        self.assertIsNone(em_alta.window_for(200))
        self.assertIsNone(em_alta.window_for(None))

    def test_parse_formatos(self):
        self.assertAlmostEqual(em_alta.hours_since("2026-11-05T00:00:00Z",
                               now=em_alta._parse_ts("2026-11-05T12:00:00Z")), 12)
        self.assertIsNotNone(em_alta._parse_ts("20261105"))
        self.assertIsNone(em_alta._parse_ts("lixo"))

    def test_rank_ordena_dedup_e_outlier(self):
        vids = [v("a", 10, 1000), v("b", 10, 1200), v("c", 10, 900), v("d", 10, 50000),
                v("a", 10, 1000), v("velho", 300, 9e9), v("e", 40, 4000)]
        r = em_alta.rank(vids, now=NOW)
        ids = [x["video_id"] for x in r["24h"]]
        self.assertEqual(ids[0], "d")
        self.assertEqual(len(ids), 4)  # dedup de "a"
        self.assertTrue(r["24h"][0]["outlier"])
        self.assertFalse(any(x["outlier"] for x in r["24h"][1:]))
        self.assertEqual([x["video_id"] for x in r["2-3d"]], ["e"])
        self.assertNotIn("velho", [x["video_id"] for w in r.values() for x in w])

    def test_outlier_usa_mediana_do_canal(self):
        vids = [dict(v("x", 10, 10000), vph=1000.0), dict(v("y", 10, 10000), vph=1000.0)]
        em_alta.mark_outliers(vids, 3.0, channel_medians={"UC1": 200})
        self.assertTrue(vids[0]["outlier"])
        self.assertEqual(vids[0]["outlier_x"], 5.0)


class TestMonitorados(unittest.TestCase):
    CFG = {"auto_add_min_vph": 1000, "auto_add_max_videos": 40, "esfriou_dias": 7}

    def test_auto_add_e_esfriou(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE channels (channel_id TEXT, video_count INTEGER)")
        con.execute("INSERT INTO channels VALUES ('UCbig', 900)")
        res = {"windows": {"24h": em_alta.rank(
            [v("n1", 5, 50000, "UCnew"), v("b1", 5, 50000, "UCbig"), v("f1", 5, 100, "UCfrio")],
            now=NOW)["24h"]}}
        novos = em_alta.update_monitored(con, res, self.CFG, now=NOW)
        self.assertEqual([c["channel_id"] for c in novos], ["UCnew"])  # grande e frio ficam de fora
        # 10 dias depois sem aparecer: esfriou
        em_alta.update_monitored(con, {"windows": {}}, self.CFG, now=NOW + 10 * 86400)
        mon = em_alta.list_monitored(con)
        self.assertEqual(mon[0]["status"], "esfriou")
        # não duplica
        self.assertEqual(em_alta.update_monitored(con, res, self.CFG, now=NOW + 11 * 86400), [])
        self.assertEqual(em_alta.list_monitored(con)[0]["status"], "quente")
        con.close()


class TestConfigCacheExport(unittest.TestCase):
    def test_nichos_json(self):
        nichos, cfg = em_alta.load_nichos()
        keys = {n["key"] for n in nichos}
        self.assertTrue({"guitarra", "hifi_uk", "moda_60", "custo_vida", "luxo_relogios"} <= keys)
        self.assertTrue(all(n["queries"] for n in nichos))
        self.assertEqual(cfg["outlier_mult"], 3.0)

    def test_cache(self):
        with tempfile.TemporaryDirectory() as d:
            old, em_alta.CACHE_DIR = em_alta.CACHE_DIR, d
            try:
                em_alta.cache_put("q", "guitar", [{"a": 1}])
                self.assertEqual(em_alta.cache_get("q", "guitar", 6), [{"a": 1}])
                self.assertIsNone(em_alta.cache_get("q", "outra", 6))
                self.assertIsNone(em_alta.cache_get("q", "guitar", -1))
            finally:
                em_alta.CACHE_DIR = old

    def test_scan_usa_cache_sem_rede(self):
        with tempfile.TemporaryDirectory() as d:
            old = em_alta.CACHE_DIR, em_alta.LAST_SCAN
            em_alta.CACHE_DIR, em_alta.LAST_SCAN = d, os.path.join(d, "last.json")
            try:
                ts = em_alta.time.time() - 3 * H
                em_alta.cache_put("q", "q1", [{"video_id": "z", "title": "t", "views": 9000,
                                               "published": ts, "channel_id": "UC9"}])
                res = em_alta.scan(use_api=False, nichos=[{"key": "k", "label": "K", "queries": ["q1"]}],
                                   cfg={"cache_horas": 6, "por_query": 5, "outlier_mult": 3.0},
                                   log=lambda *a: None)
                self.assertEqual(res["windows"]["24h"][0]["video_id"], "z")
                self.assertEqual(em_alta.load_last_scan()["total"], 1)
            finally:
                em_alta.CACHE_DIR, em_alta.LAST_SCAN = old

    def test_export_md(self):
        res = {"ts": "2026-11-05T09:00:00Z", "total": 2,
               "windows": em_alta.rank([v("a", 5, 9000), v("b", 50, 9000)], now=NOW)}
        with tempfile.TemporaryDirectory() as d:
            p = em_alta.export_md(res, [{"name": "Novo|Canal", "nicho": "G", "video_count": 3,
                                         "vph": 5000, "title": "hit", "video_id": "a"}],
                                  path=os.path.join(d, "x", "EM_ALTA_HOJE.md"))
            md = open(p, encoding="utf-8").read()
        self.assertIn("## ⚡ Top 24 h", md)
        self.assertIn("## 🔥 Top 2-3 dias", md)
        self.assertIn("## 🆕 Canais novos explodindo", md)
        self.assertIn("https://youtu.be/a", md)
        self.assertIn("Novo/Canal", md)


if __name__ == "__main__":
    unittest.main()
