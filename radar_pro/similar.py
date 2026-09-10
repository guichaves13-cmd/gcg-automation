# -*- coding: utf-8 -*-
"""
================================================================================
 RADAR PRO — Motor de CANAIS SIMILARES  (funcionalidade nova nº2)
================================================================================
A API do YouTube removeu o campo "related channels". Então reconstruímos o
conceito por CO-OCORRÊNCIA temática:

  1. Pega os títulos dos vídeos recentes do canal-alvo.
  2. Extrai as palavras-chave que definem o nicho (substantivos fortes).
  3. Busca essas palavras no YouTube → os OUTROS canais que aparecem repetidamente
     nas mesmas buscas são os "vizinhos de lane" (similares).
  4. Rankeia por frequência de co-ocorrência + mesmo idioma/categoria.

Dois modos:
  - similar_from_db : instantâneo, custo ZERO de quota (usa o que já mapeamos).
  - similar_live    : busca ao vivo (acha canais que ainda não estão no banco).
================================================================================
"""
import re
from collections import Counter

import yt

_STOP = set("""the a an of to in on and or for de la el los las un una y o en por con que del al como
is are was were do da dos das no na para se su sus lo le e é than this that with without into inside
outside how why what when who your you my his her their our it its as at be been being más muy tan aqui
documental documentary history historia video full completo completa official channel canal parte hd 4k
so we they them then there here about over under after before into out off down up all any""".split())


def _keywords(titles, k=6):
    """Top-k palavras temáticas dos títulos (ignora stopwords/adjetivos vazios)."""
    c = Counter()
    for t in titles:
        for w in re.findall(r"[\wÀ-ÿ]{4,}", (t or "").lower()):
            if w not in _STOP and not w.isdigit():
                c[w] += 1
    return [w for w, _ in c.most_common(k)]


def similar_from_db(con, channel_id, limit=12):
    """Similares INSTANTÂNEOS a partir do banco: mesma categoria+idioma,
    priorizando quem compartilha palavras-chave no melhor título / nome."""
    base = con.execute("SELECT * FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
    if not base:
        return []
    titles = [r["title"] for r in con.execute(
        "SELECT title FROM videos WHERE channel_id=?", (channel_id,)).fetchall()]
    titles.append(base["best_title"] or "")
    kws = set(_keywords(titles, 8))

    rows = con.execute(
        "SELECT * FROM channels WHERE channel_id != ? AND category = ?",
        (channel_id, base["category"]),
    ).fetchall()
    scored = []
    for r in rows:
        overlap = 0
        blob = ((r["best_title"] or "") + " " + (r["name"] or "")).lower()
        for kw in kws:
            if kw in blob:
                overlap += 1
        same_lang = 1 if r["language"] == base["language"] else 0
        # score: sobreposição de tema domina; idioma e viral_score desempatam
        s = overlap * 10 + same_lang * 4 + (r["viral_score"] or 0) / 20
        scored.append((s, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [dict(r) for _, r in scored[:limit]]


def similar_live(channel_id, seed_titles=None, budget=None, limit=15):
    """Descobre canais similares AO VIVO por co-ocorrência de busca.
    Retorna lista de (channel_id, hits) — quantas buscas do nicho o canal apareceu."""
    titles = seed_titles or []
    if not titles:
        vids = yt.recent_videos(channel_id, max_results=10, budget=budget)
        titles = [v["title"] for v in vids]
    if not titles:
        return []
    kws = _keywords(titles, 6)
    counter = Counter()
    for kw in kws[:5]:  # limita p/ orçamento de quota (5 buscas * 100 = 500 unidades)
        if budget is not None and budget.get("units", 1) <= 120:
            break
        try:
            ch_ids, _ = yt.search_channel_ids(kw, max_results=25, order="viewCount", budget=budget)
        except Exception:
            continue
        for cid in ch_ids:
            if cid != channel_id:
                counter[cid] += 1
    ranked = counter.most_common(limit)
    return ranked  # [(channel_id, co-ocorrências), ...]
