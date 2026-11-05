# -*- coding: utf-8 -*-
"""
================================================================================
 RADAR PRO — Servidor web (Flask)   ·   porta 5051
================================================================================
Serve a interface (igual ao Radar Club) lendo do banco radar.db.
Endpoints:
  GET  /                       -> UI
  GET  /api/stats              -> totais, novos hoje/7d, contagens de aba e categoria
  GET  /api/channels           -> lista filtrada/ordenada/paginada
  GET  /api/channel/<id>       -> detalhe + vídeos (com VPH) + similares
  GET  /api/similar/<id>?live= -> canais similares (banco ou ao vivo)
  POST /api/favorite           -> marca/desmarca favorito
  POST /api/crawl              -> dispara varredura (SSE de log)
  GET  /em-alta                -> aba 🔥 Em alta agora (24 h / 2-3 d / 4-6 d)
  GET  /api/em_alta            -> último scan + canais monitorados
  POST /api/em_alta/scan       -> roda o scan (yt-dlp primeiro, API só completa)
================================================================================
"""
import json
import os
import subprocess
import sys

from flask import Flask, Response, jsonify, render_template, request

import db
import em_alta
import scoring
import similar as similar_mod

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------------ helpers
def _row_public(r):
    d = dict(r)
    d["tiers"] = scoring.tiers_for(d)
    return d


def _base_filtered(con, args):
    """Aplica os filtros que NÃO são aba nem categoria (para contar bem).
    Inclui: idioma, formato (long/short/all), tamanho, score, busca +
    os 3 FILTROS ADICIONAIS do Radar Club (client-side lá, aqui no servidor)."""
    q = (args.get("q") or "").strip().lower()
    lang = args.get("lang") or ""
    fmt = args.get("format") or ("long" if args.get("long") == "1" else "")
    min_subs = int(args.get("min_subs") or 0)
    max_subs = int(args.get("max_subs") or 0)
    min_score = float(args.get("min_score") or 0)

    where, params = ["1=1"], []
    if lang:
        where.append("language = ?"); params.append(lang)
    if fmt == "long":
        where.append("has_long = 1")
    elif fmt == "short":
        where.append("has_short = 1")
    if min_subs:
        where.append("subs >= ?"); params.append(min_subs)
    if max_subs:
        where.append("subs <= ?"); params.append(max_subs)
    if min_score:
        where.append("viral_score >= ?"); params.append(min_score)
    if q:
        where.append("(LOWER(name) LIKE ? OR LOWER(best_title) LIKE ? OR channel_id = ?)")
        params += [f"%{q}%", f"%{q}%", args.get("q").strip()]
    rows = [dict(r) for r in con.execute(
        f"SELECT * FROM channels WHERE {' AND '.join(where)}", params).fetchall()]

    # --- FILTROS ADICIONAIS (os 3 chips) ---
    if args.get("opp") == "1":       # 💎 Menos inscritos, muitas views
        rows = [r for r in rows if (r["subs"] or 0) < 100_000
                and (r["subs_multiplier"] or 0) >= 10 and (r["best_views"] or 0) >= 20_000]
    if args.get("novos3m") == "1":   # 🆕 Novos (≤3 meses)
        rows = [r for r in rows if 0 < (r["created_days"] or 0) <= 92]
    if args.get("postando") == "1":  # ⚡ Postando agora (upload ≤7d)
        rows = [r for r in rows if scoring._days_since(r["newest_upload"]) <= 7]
    return rows


SORTS = {
    "views":      lambda c: c.get("best_views") or 0,   # "Mais views (vídeo top)" — padrão do Radar Club
    "recent":     lambda c: c.get("newest_upload") or "",
    "score":      lambda c: c.get("viral_score") or 0,
    "multiplier": lambda c: c.get("subs_multiplier") or 0,
    "potencial":  lambda c: c.get("score_potencial") or 0,
    "vph":        lambda c: c.get("best_vph") or 0,       # MELHORIA NOSSA
    "best_views": lambda c: c.get("best_views") or 0,
    "subs_desc":  lambda c: c.get("subs") or 0,
    "subs_asc":   lambda c: -(c.get("subs") or 0),
    "new":        lambda c: c.get("first_seen") or "",
}


# ------------------------------------------------------------------ rotas
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stats")
def api_stats():
    con = db.connect()
    total = con.execute("SELECT COUNT(*) c FROM channels").fetchone()["c"]
    new_today = con.execute(
        "SELECT COUNT(*) c FROM channels WHERE first_seen >= datetime('now','-1 day')").fetchone()["c"]
    new_7d = con.execute(
        "SELECT COUNT(*) c FROM channels WHERE first_seen >= datetime('now','-7 day')").fetchone()["c"]

    base = _base_filtered(con, request.args)
    category = request.args.get("category") or ""
    cat_counts = {}
    for c in base:
        cat_counts[c["category"]] = cat_counts.get(c["category"], 0) + 1
    scoped = [c for c in base if not category or c["category"] == category]
    tier_counts = {k: 0 for k, _ in scoring.TIER_LABELS}
    for c in scoped:
        for t in scoring.tiers_for(c):
            tier_counts[t] = tier_counts.get(t, 0) + 1
    con.close()
    return jsonify({
        "total": total, "new_today": new_today, "new_7d": new_7d,
        "tier_counts": tier_counts, "category_counts": cat_counts,
        "tier_labels": scoring.TIER_LABELS,
        "categories": scoring.CATEGORY_LIST,   # [(key, label, emoji), ...]
        "languages": scoring.LANGUAGES,        # [(code, label), ...]
    })


@app.route("/api/channels")
def api_channels():
    con = db.connect()
    base = _base_filtered(con, request.args)
    category = request.args.get("category") or ""
    tier = request.args.get("tier") or "todos"
    sort = request.args.get("sort") or "recent"
    page = int(request.args.get("page") or 1)
    per = int(request.args.get("per") or 60)

    rows = [c for c in base if not category or c["category"] == category]
    rows = [c for c in rows if tier in scoring.tiers_for(c)]
    rows.sort(key=SORTS.get(sort, SORTS["recent"]), reverse=(sort != "subs_asc"))
    total = len(rows)
    start = (page - 1) * per
    page_rows = rows[start:start + per]
    # anexa os 2 melhores vídeos (por VPH) de cada card -> 2 thumbs com VPH, igual ao original
    ids = [c["channel_id"] for c in page_rows]
    vids_by_ch = {}
    if ids:
        for v in con.execute(
            f"SELECT * FROM videos WHERE channel_id IN ({','.join('?' * len(ids))})", ids
        ).fetchall():
            vids_by_ch.setdefault(v["channel_id"], []).append(dict(v))
    for c in page_rows:
        c["tiers"] = scoring.tiers_for(c)
        vs = sorted(vids_by_ch.get(c["channel_id"], []), key=lambda x: x.get("vph", 0), reverse=True)
        c["videos"] = vs[:2]
    con.close()
    return jsonify({"total": total, "page": page, "per": per, "channels": page_rows})


@app.route("/api/channel/<cid>")
def api_channel(cid):
    con = db.connect()
    r = con.execute("SELECT * FROM channels WHERE channel_id=?", (cid,)).fetchone()
    if not r:
        con.close()
        return jsonify({"error": "não encontrado"}), 404
    ch = _row_public(r)
    ch["videos"] = sorted(db.get_channel_videos(con, cid),
                          key=lambda v: v.get("vph", 0), reverse=True)
    ch["similar"] = similar_mod.similar_from_db(con, cid, limit=12)
    con.close()
    return jsonify(ch)


@app.route("/api/similar/<cid>")
def api_similar(cid):
    con = db.connect()
    if request.args.get("live") == "1":
        vids = db.get_channel_videos(con, cid)
        ranked = similar_mod.similar_live(cid, seed_titles=[v["title"] for v in vids],
                                          budget={"units": 3000})
        ids = [c for c, _ in ranked]
        found = con.execute(
            f"SELECT * FROM channels WHERE channel_id IN ({','.join('?' * len(ids))})", ids
        ).fetchall() if ids else []
        known = {r["channel_id"] for r in found}
        result = [_row_public(r) for r in found]
        result += [{"channel_id": c, "name": "(fora do banco — rode crawler channel)",
                    "in_db": False} for c in ids if c not in known]
        con.close()
        return jsonify({"similar": result, "note": "ao vivo (co-ocorrência de busca)"})
    result = similar_mod.similar_from_db(con, cid, limit=15)
    con.close()
    return jsonify({"similar": result, "note": "do banco (instantâneo)"})


@app.route("/api/niche/<category>")
def api_niche(category):
    """MELHORIA NOSSA: raio-x agregado de um nicho (não existe no Radar Club)."""
    import re as _re
    from collections import Counter
    con = db.connect()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM channels WHERE category=?", (category,)).fetchall()]
    if not rows:
        con.close()
        return jsonify({"error": "nicho vazio"}), 404
    n = len(rows)
    avg_score = round(sum(r["viral_score"] for r in rows) / n, 1)
    langs = Counter(r["language"] for r in rows)
    top_channels = sorted(rows, key=lambda r: r["viral_score"], reverse=True)[:10]
    # oportunidades = canal pequeno batendo acima do peso
    opps = sorted([r for r in rows if r["subs"] < 100_000 and r["subs_multiplier"] >= 10],
                  key=lambda r: r["subs_multiplier"], reverse=True)[:10]
    # estruturas de título: pega as primeiras palavras dos melhores títulos
    ids = [r["channel_id"] for r in rows]
    titles = []
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        titles += [v["title"] for v in con.execute(
            f"SELECT title FROM videos WHERE channel_id IN ({','.join('?' * len(chunk))}) "
            f"ORDER BY views DESC", chunk).fetchall()]
    openers = Counter()
    for t in titles[:400]:
        words = _re.findall(r"[\wÀ-ÿ']+", t)
        if len(words) >= 3:
            openers[" ".join(words[:3]).title()] += 1
    con.close()
    return jsonify({
        "category": category, "total": n, "avg_score": avg_score,
        "langs": dict(langs.most_common()),
        "monetized_pct": round(100 * sum(r["monetized"] for r in rows) / n),
        "faceless_avg": round(sum(r["faceless"] for r in rows) / n),
        "top_channels": [{"channel_id": r["channel_id"], "name": r["name"],
                          "viral_score": r["viral_score"], "subs": r["subs"],
                          "subs_multiplier": r["subs_multiplier"],
                          "best_title": r["best_title"], "best_vph": r["best_vph"]} for r in top_channels],
        "opportunities": [{"channel_id": r["channel_id"], "name": r["name"],
                           "subs": r["subs"], "subs_multiplier": r["subs_multiplier"],
                           "viral_score": r["viral_score"]} for r in opps],
        "top_structures": openers.most_common(12),
    })


@app.route("/api/favorite", methods=["POST"])
def api_favorite():
    cid = (request.json or {}).get("channel_id")
    if not cid:
        return jsonify({"error": "channel_id ausente"}), 400
    con = db.connect()
    cur = con.execute("SELECT favorite FROM channels WHERE channel_id=?", (cid,)).fetchone()
    if not cur:
        con.close()
        return jsonify({"error": "não encontrado"}), 404
    newv = 0 if cur["favorite"] else 1
    con.execute("UPDATE channels SET favorite=? WHERE channel_id=?", (newv, cid))
    con.commit()
    con.close()
    return jsonify({"favorite": newv})


@app.route("/api/collections", methods=["GET", "POST"])
def api_collections():
    """Banco de Referências: lista/cria coleções nomeadas."""
    con = db.connect()
    if request.method == "POST":
        name = (request.json or {}).get("name", "").strip()
        if not name:
            con.close()
            return jsonify({"error": "nome vazio"}), 400
        cur = con.execute("INSERT INTO collections (name, created) VALUES (?,?)",
                          (name, db.now_iso()))
        con.commit()
        cid = cur.lastrowid
        con.close()
        return jsonify({"id": cid, "name": name, "count": 0})
    rows = con.execute(
        "SELECT c.id, c.name, c.created, COUNT(i.channel_id) AS count "
        "FROM collections c LEFT JOIN collection_items i ON i.collection_id=c.id "
        "GROUP BY c.id ORDER BY c.created DESC").fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/collections/<int:cid>", methods=["GET", "DELETE"])
def api_collection(cid):
    con = db.connect()
    if request.method == "DELETE":
        con.execute("DELETE FROM collections WHERE id=?", (cid,))
        con.execute("DELETE FROM collection_items WHERE collection_id=?", (cid,))
        con.commit()
        con.close()
        return jsonify({"ok": True})
    meta = con.execute("SELECT * FROM collections WHERE id=?", (cid,)).fetchone()
    if not meta:
        con.close()
        return jsonify({"error": "não encontrada"}), 404
    chans = con.execute(
        "SELECT ch.* FROM collection_items i JOIN channels ch ON ch.channel_id=i.channel_id "
        "WHERE i.collection_id=? ORDER BY ch.viral_score DESC", (cid,)).fetchall()
    con.close()
    return jsonify({"id": cid, "name": meta["name"], "channels": [_row_public(r) for r in chans]})


@app.route("/api/collections/<int:cid>/toggle", methods=["POST"])
def api_collection_toggle(cid):
    """Adiciona/remove um canal da coleção (o bookmark do card)."""
    channel_id = (request.json or {}).get("channel_id")
    if not channel_id:
        return jsonify({"error": "channel_id ausente"}), 400
    con = db.connect()
    exists = con.execute(
        "SELECT 1 FROM collection_items WHERE collection_id=? AND channel_id=?",
        (cid, channel_id)).fetchone()
    if exists:
        con.execute("DELETE FROM collection_items WHERE collection_id=? AND channel_id=?",
                    (cid, channel_id))
        state = False
    else:
        con.execute("INSERT OR IGNORE INTO collection_items (collection_id, channel_id, added) VALUES (?,?,?)",
                    (cid, channel_id, db.now_iso()))
        state = True
    con.commit()
    con.close()
    return jsonify({"in_collection": state})


@app.route("/api/channel_collections/<cid>")
def api_channel_collections(cid):
    """Em quais coleções este canal está (para marcar o bookmark)."""
    con = db.connect()
    rows = con.execute("SELECT collection_id FROM collection_items WHERE channel_id=?", (cid,)).fetchall()
    con.close()
    return jsonify([r["collection_id"] for r in rows])


@app.route("/api/crawl", methods=["POST"])
def api_crawl():
    data = request.json or {}
    mode = data.get("mode", "refresh")
    py = r"C:\Python314\python.exe"
    if not os.path.exists(py):
        py = sys.executable or "python"
    cmd = [py, os.path.join(HERE, "crawler.py")]
    if mode == "discover":
        cmd += ["discover", str(data.get("budget", 4000))]
    elif mode == "category":
        cmd += ["category", data.get("category", "História"), str(data.get("budget", 2000))]
    elif mode == "channel":
        cmd += ["channel", data.get("channel", "")]
    elif mode == "seed":
        cmd += ["seed"]
    else:
        cmd += ["refresh", str(data.get("n", 50))]

    def gen():
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, cwd=HERE, encoding="utf-8", env=env)
        for line in iter(p.stdout.readline, ""):
            line = line.rstrip()
            if line:
                yield f"data: {json.dumps({'text': line})}\n\n"
        p.wait()
        yield f"data: {json.dumps({'text': '__DONE__'})}\n\n"

    return Response(gen(), mimetype="text/event-stream")


# ------------------------------------------------------------------ 🔥 Em alta agora
@app.route("/em-alta")
def em_alta_page():
    return render_template("em_alta.html")


@app.route("/api/em_alta")
def api_em_alta():
    res = em_alta.load_last_scan() or {"ts": None, "total": 0, "windows": {}, "novos": []}
    nicho = request.args.get("nicho")
    only_out = request.args.get("outliers") == "1"
    for k, vids in list(res.get("windows", {}).items()):
        res["windows"][k] = [v for v in vids if (not nicho or v.get("nicho") == nicho)
                             and (not only_out or v.get("outlier"))]
    con = db.connect()
    res["monitored"] = em_alta.list_monitored(con)
    con.close()
    nichos, _cfg = em_alta.load_nichos()
    res["nichos"] = [{"key": n["key"], "label": n.get("label", n["key"]),
                      "adjacente": bool(n.get("adjacente"))} for n in nichos]
    res["window_labels"] = [[k, lbl] for k, lbl, *_ in em_alta.WINDOWS]
    return jsonify(res)


@app.route("/api/em_alta/scan", methods=["POST"])
def api_em_alta_scan():
    data = request.json or {}
    cmd = [sys.executable or "python", os.path.join(HERE, "em_alta.py"), "scan"]
    if data.get("no_api"):
        cmd.append("--no-api")

    def gen():
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, cwd=HERE, encoding="utf-8", env=env)
        for line in iter(p.stdout.readline, ""):
            if line.strip():
                yield f"data: {json.dumps({'text': line.rstrip()})}\n\n"
        p.wait()
        yield f"data: {json.dumps({'text': '__DONE__'})}\n\n"

    return Response(gen(), mimetype="text/event-stream")


if __name__ == "__main__":
    db.init_db()
    print("Radar Pro rodando em http://127.0.0.1:5051", flush=True)
    app.run(host="127.0.0.1", port=5051, debug=False, use_reloader=False)
