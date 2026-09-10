# -*- coding: utf-8 -*-
"""
================================================================================
 RADAR PRO — Crawler (o que MAPEIA o YouTube)
================================================================================
Uso:
  python crawler.py seed                 -> semeia a partir dos seus canais salvos
  python crawler.py discover [budget]    -> varre TODAS as categorias (padrão 8000 un.)
  python crawler.py category "História" [budget]
  python crawler.py channel <url/@/id>   -> adiciona/atualiza 1 canal + similares
  python crawler.py refresh [n]          -> reatualiza os N canais mais desatualizados

Quota: cada varredura recebe um ORÇAMENTO de unidades (search=100, channels=1).
Com rotação de chaves (várias keys no .env_youtube_keys.txt) o orçamento é por dia
somado entre todas as chaves. `discover` gasta ~100 un por query de categoria +
poucas por canal enriquecido.
================================================================================
"""
import json
import os
import re
import sys
from datetime import datetime

import db
import scoring
import similar
import yt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Queries-semente por categoria (o "mapa" de entrada em cada nicho, EN+ES+PT).
SEED_QUERIES = {
    "História": ["history documentary", "documental historia", "ancient empire", "imperio antiguo",
                 "história documentário", "medieval history", "revolución mexicana", "roman empire"],
    "Guerra": ["ww2 documentary", "segunda guerra mundial", "battle documentary", "batalla historia",
               "military history", "guerra documental", "cold war documentary"],
    "True Crime": ["true crime documentary", "caso criminal", "crimen real", "unsolved mystery case",
                   "asesino serial documental", "true crime historia"],
    "Mistério & Terror": ["unexplained mystery", "misterio sin resolver", "creepy documentary",
                          "paranormal documental", "historias de terror", "conspiracy documentary"],
    "Religião & Bíblia": ["bible documentary", "historia biblica", "biblical mystery",
                          "documental biblia", "profetas biblia", "jesus documentary"],
    "Finanças & Riqueza": ["how money works", "por que es tan caro", "dark money documentary",
                           "riqueza documental", "business collapse documentary", "economia oculta"],
    "Motivação & Estoicismo": ["stoicism motivation", "estoicismo disciplina", "marcus aurelius stoic",
                               "self discipline motivation", "sabiduría estoica"],
    "Ciência & Espaço": ["space documentary", "universo documental", "prehistoric earth",
                        "documental prehistoria", "black hole documentary", "dinosaurios documental",
                        "when the earth was", "así era la tierra"],
    "Música": ["sleep music", "lofi hip hop", "musica para dormir", "432hz healing",
               "relaxing music", "ambient music", "musica relajante"],
    "Relax": ["deep sleep music", "musica para dormir profundo", "rain sounds sleep",
              "meditation music", "asmr relax", "432hz 528hz healing", "sonidos relajantes"],
    "Curiosidades & Fatos": ["amazing manufacturing process", "how it's made factory",
                             "satisfying factory process", "curiosidades increíbles", "amazing facts",
                             "woodworking satisfying", "recycling process"],
    "Natureza & Animais": ["nature animal documentary", "wildlife predators documentary",
                           "documental animales salvajes", "deadliest predators", "dog facts",
                           "sea creatures documentary", "depredadores naturaleza"],
    "Geopolítica": ["geopolitics explained", "war footage combat", "russia ukraine war",
                    "china geopolitics", "conflicto geopolítico", "combat footage documentary",
                    "world conflict explained"],
}


def _created_days(created_at):
    try:
        d = datetime.strptime((created_at or "")[:10], "%Y-%m-%d")
        return max((datetime.now() - d).days, 1)
    except Exception:
        return 365


def _build_row(cid, meta, vids, seed_source, fonte="acervo"):
    """Monta a linha do banco a partir de meta + vídeos (usado no crawl E no rescore)."""
    titles = [v["title"] for v in vids]
    subs = meta.get("subs", 0)
    vc = meta.get("video_count", 0)
    cdays = _created_days(meta.get("created_at", ""))
    category = scoring.detect_category(meta["name"], meta.get("description", ""),
                                       titles, meta.get("topic_cats"))
    language = scoring.detect_language(titles)
    m = scoring.channel_metrics(subs, vc, cdays, vids)
    score = scoring.dark_score(subs, vc, cdays, vids, m)
    faceless = scoring.faceless_estimate(category, meta["name"], titles)
    monetized = scoring.monetized_estimate(subs, vc, cdays, m["top_views"])
    best = m["best"]
    return {
        "channel_id": cid,
        "name": meta["name"],
        "handle": meta.get("handle", ""),
        "avatar": meta.get("avatar", ""),
        "description": (meta.get("description", "") or "")[:600],
        "country": meta.get("country", ""),
        "subs": subs,
        "total_views": meta.get("total_views", 0),
        "video_count": vc,
        "created_at": meta.get("created_at", ""),
        "category": category,
        "language": language,
        "is_music": 1 if category in ("musica", "relax") else 0,
        "has_long": 1 if any(v["is_long"] for v in vids) else 0,
        "has_short": 1 if any(v.get("duration_min", 99) <= 1.1 for v in vids) else 0,
        "created_days": cdays,
        "viral_score": score,
        "subs_multiplier": m["views_per_sub"],
        "score_potencial": scoring.score_potencial(subs, vids),
        "best_views": m["top_views"],
        "best_vph": round(max(v["vph"] for v in vids), 1),
        "best_video_id": best["video_id"] if best else "",
        "best_title": best["title"] if best else "",
        "avg_vph": round(sum(v["vph"] for v in vids) / len(vids), 1),
        "monetized": 1 if monetized else 0,
        "faceless": faceless,
        "badge": scoring.badge_for(score, m, vc),
        "crescimento": scoring.growth_text(subs, vc, cdays, vids, m),
        "alinhamento": 1 if scoring.alinhamento(category, faceless, subs, vids) else 0,
        "fonte": fonte,
        "vids_per_month": m["vids_per_month"],
        "views_per_month": m["views_per_month"],
        "newest_upload": max((v["published"] for v in vids), default=""),
        "seed_source": seed_source,
    }


def enrich_and_store(con, channel_meta, seed_source="", max_videos=12, budget=None):
    """Dado o snippet+stats de um canal, puxa vídeos recentes, calcula scores e grava."""
    cid = channel_meta["channel_id"]
    if scoring.is_noise(channel_meta["name"], channel_meta.get("description", "")):
        return None
    vids = yt.recent_videos(cid, channel_meta.get("uploads_playlist"),
                            max_results=max_videos, budget=budget)
    if not vids:
        return None
    fonte = "seed" if seed_source.startswith("seed") else "acervo"
    row = _build_row(cid, channel_meta, vids, seed_source, fonte)
    is_new = db.upsert_channel(con, row)
    db.replace_channel_videos(con, cid, vids)
    return is_new


def cmd_rescore():
    """Recalcula TODOS os scores a partir do que já está no banco — quota ZERO.
    Use após mudar o scoring.py."""
    db.init_db()
    con = db.connect()
    rows = con.execute("SELECT * FROM channels").fetchall()
    n = 0
    for r in rows:
        vids = db.get_channel_videos(con, r["channel_id"])
        if not vids:
            continue
        meta = {"name": r["name"], "handle": r["handle"], "avatar": r["avatar"],
                "description": r["description"], "country": r["country"], "subs": r["subs"],
                "total_views": r["total_views"], "video_count": r["video_count"],
                "created_at": r["created_at"], "topic_cats": []}
        row = _build_row(r["channel_id"], meta, vids, r["seed_source"] or "", r["fonte"] or "acervo")
        db.upsert_channel(con, row)
        n += 1
    con.commit()
    con.close()
    print(f"== RESCORE FIM · {n} canais recalculados (quota zero) ==", flush=True)


def _process_channel_ids(con, ids, seed_source, budget, log_prefix=""):
    """Enriquece uma lista de channel_ids (pula os já frescos)."""
    ids = [c for c in ids if c]
    metas = yt.fetch_channels(ids, budget=budget)
    found = new = 0
    for cid, meta in metas.items():
        if meta.get("hidden_subs") and meta["subs"] == 0:
            meta["subs"] = 1  # evita divisão por zero em canais com subs ocultos
        try:
            res = enrich_and_store(con, meta, seed_source=seed_source, budget=budget)
        except Exception as e:
            print(f"    ! erro em {meta['name'][:30]}: {str(e)[:60]}", flush=True)
            continue
        if res is None:
            continue
        found += 1
        new += 1 if res else 0
        tag = "🆕" if res else "↻"
        print(f"    {tag} {meta['name'][:38]:38} · {meta['subs']:>9,} subs", flush=True)
        con.commit()
    if log_prefix:
        print(f"  {log_prefix}: {found} canais ({new} novos)", flush=True)
    return found, new


def discover_category(con, category, budget):
    print(f"\n== categoria: {category} ==", flush=True)
    total_found = total_new = 0
    all_ids = []
    for q in SEED_QUERIES.get(category, []):
        if budget["units"] <= 200:
            print("  ! orçamento de quota esgotado", flush=True)
            break
        try:
            ch_ids, _ = yt.search_channel_ids(q, max_results=40, order="viewCount", budget=budget)
        except Exception as e:
            print(f"  ! busca '{q}' falhou: {str(e)[:60]}", flush=True)
            continue
        print(f"  buscando '{q}' -> {len(ch_ids)} canais (orçamento: {budget['units']})", flush=True)
        all_ids.extend(ch_ids)
    # dedup
    seen = set()
    all_ids = [c for c in all_ids if not (c in seen or seen.add(c))]
    f, n = _process_channel_ids(con, all_ids, seed_source=f"discover:{category}", budget=budget)
    total_found += f
    total_new += n
    db.log_crawl(con, "discover", category, total_found, total_new, 8000 - budget["units"])
    con.commit()
    return total_found, total_new


def cmd_discover(budget_units=8000, only_category=None):
    db.init_db()
    con = db.connect()
    budget = {"units": budget_units}
    cats = [only_category] if only_category else list(SEED_QUERIES.keys())
    grand_f = grand_n = 0
    for cat in cats:
        if budget["units"] <= 200:
            break
        f, n = discover_category(con, cat, budget)
        grand_f += f
        grand_n += n
    print(f"\n== FIM · {grand_f} canais processados, {grand_n} novos · "
          f"~{budget_units - budget['units']} unidades usadas ==", flush=True)
    con.close()


def cmd_seed():
    """Semeia o banco a partir dos seus arquivos de canais salvos (custo baixo)."""
    db.init_db()
    con = db.connect()
    budget = {"units": 8000}
    files = ["radar_saved_channels.json", "CANAIS_OTOMANOS_SALVOS.json", "CANAIS_SALVOS.json"]
    urls = []
    for fn in files:
        p = os.path.join(ROOT, fn)
        if not os.path.exists(p):
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for item in (data if isinstance(data, list) else []):
            u = item.get("url") or item.get("channel_url") or ""
            if u:
                urls.append(u)
    print(f"Semeando com {len(urls)} canais salvos...", flush=True)
    ids = []
    for u in urls:
        cid = yt.resolve_channel_id(u, budget=budget)
        if cid:
            ids.append(cid)
    seen = set()
    ids = [c for c in ids if not (c in seen or seen.add(c))]
    f, n = _process_channel_ids(con, ids, seed_source="seed:salvos", budget=budget)
    db.log_crawl(con, "seed", f"{len(files)} arquivos", f, n, 8000 - budget["units"])
    con.commit()
    print(f"\n== SEED FIM · {f} canais ({n} novos) ==", flush=True)
    con.close()


def cmd_channel(url_or_id, expand_similar=True):
    """Adiciona/atualiza 1 canal e (opcional) descobre seus similares ao vivo."""
    db.init_db()
    con = db.connect()
    budget = {"units": 6000}
    cid = yt.resolve_channel_id(url_or_id, budget=budget)
    if not cid:
        print("Canal não resolvido.", flush=True)
        return
    _process_channel_ids(con, [cid], seed_source="manual", budget=budget)
    con.commit()
    if expand_similar:
        print("\n  descobrindo similares ao vivo...", flush=True)
        vids = db.get_channel_videos(con, cid)
        ranked = similar.similar_live(cid, seed_titles=[v["title"] for v in vids], budget=budget)
        sim_ids = [c for c, _ in ranked]
        print(f"  {len(sim_ids)} similares encontrados, enriquecendo...", flush=True)
        _process_channel_ids(con, sim_ids, seed_source=f"similar:{cid}", budget=budget)
        con.commit()
    con.close()
    print("== canal + similares OK ==", flush=True)


def cmd_refresh(n=50):
    """Reatualiza os N canais com last_updated mais antigo (mantém scores frescos)."""
    db.init_db()
    con = db.connect()
    budget = {"units": 8000}
    rows = con.execute("SELECT channel_id FROM channels ORDER BY last_updated ASC LIMIT ?", (n,)).fetchall()
    ids = [r["channel_id"] for r in rows]
    print(f"Reatualizando {len(ids)} canais...", flush=True)
    _process_channel_ids(con, ids, seed_source="refresh", budget=budget)
    con.commit()
    con.close()
    print("== refresh OK ==", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0].lower() if args else "discover"
    if cmd == "seed":
        cmd_seed()
    elif cmd == "discover":
        cmd_discover(int(args[1]) if len(args) > 1 else 8000)
    elif cmd == "category" and len(args) > 1:
        cmd_discover(int(args[2]) if len(args) > 2 else 3000, only_category=args[1])
    elif cmd == "channel" and len(args) > 1:
        cmd_channel(args[1])
    elif cmd == "refresh":
        cmd_refresh(int(args[1]) if len(args) > 1 else 50)
    elif cmd == "rescore":
        cmd_rescore()
    else:
        print(__doc__)
