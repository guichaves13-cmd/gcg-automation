# -*- coding: utf-8 -*-
"""
Importa os canais do Radar de Nichos Club (JSON exportado pelo agente) para o
nosso Radar Pro (radar.db). Reaproveita o pipeline do crawler: puxa stats +
vídeos recentes da YouTube API, calcula scores (fogo / ×inscritos / VPH) e grava.
Fonte marcada como 'radar_club'. NÃO expande similares (economia de quota).

Uso: python import_radar_club.py [caminho_do_json]
"""
import json
import os
import re
import sys

import db
import crawler

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.join(HERE, "import_radar_club_2026-09-04.json")

UC_RE = re.compile(r"(UC[0-9A-Za-z_-]{22})")


def extract_id(url):
    if not url:
        return None
    m = UC_RE.search(url)
    return m.group(1) if m else None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_JSON
    data = json.load(open(path, encoding="utf-8"))

    ids, skipped = [], []
    for c in data:
        cid = extract_id(c.get("url"))
        if cid:
            ids.append(cid)
        else:
            skipped.append(c.get("name"))

    # dedup preservando ordem
    seen = set()
    ids = [c for c in ids if not (c in seen or seen.add(c))]

    print(f"JSON: {len(data)} canais | {len(ids)} com channel_id | {len(skipped)} sem url", flush=True)
    if skipped:
        print("  sem url:", ", ".join(str(s) for s in skipped[:30]), flush=True)

    db.init_db()
    con = db.connect()
    before = con.execute("SELECT COUNT(*) c FROM channels").fetchone()["c"]

    budget = {"units": 8000}
    found, new = crawler._process_channel_ids(
        con, ids, seed_source="radar_club", budget=budget, log_prefix="radar_club"
    )
    db.log_crawl(con, "import", "radar_club", found, new, 8000 - budget["units"])
    con.commit()

    after = con.execute("SELECT COUNT(*) c FROM channels").fetchone()["c"]
    con.close()
    print(f"\n== IMPORT FIM · processados {found} · novos {new} · "
          f"banco {before} -> {after} · ~{8000 - budget['units']} unidades ==", flush=True)


if __name__ == "__main__":
    main()
