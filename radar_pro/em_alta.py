# -*- coding: utf-8 -*-
"""
================================================================================
 RADAR PRO — 🔥 EM ALTA AGORA (alta extrema: 24 h / 2-3 d / 4-6 d)
================================================================================
Varre a lista de nichos (+ adjacentes) de `nichos_em_alta.json` e ranqueia os
vídeos que ESTOURARAM nos últimos dias, por VPH.

Economia de cota (ordem de preferência):
  1. yt-dlp (scraping da busca "esta semana")   -> custo ZERO de API
  2. YouTube API `videos.list` só para COMPLETAR o que faltou (data/views)
     -> 1 unidade por 50 vídeos (nunca usa `search`, que custa 100)
  3. Cache em disco (`cache_em_alta/`, TTL configurável) -> não repete busca

Também mantém a LISTA VIVA de canais monitorados (tabela `monitored_channels`):
  - auto-adiciona canal novo que viraliza (poucos vídeos + VPH alto)
  - marca como "esfriou" quem não aparece quente há N dias

Uso:
  python em_alta.py scan            # varre, grava last_scan.json, atualiza canais e exporta MD
  python em_alta.py scan --no-api   # só yt-dlp (cota zero)
  python em_alta.py export          # só reexporta o MD do último scan
================================================================================
"""
import hashlib
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
NICHOS_PATH = os.path.join(HERE, "nichos_em_alta.json")
CACHE_DIR = os.path.join(HERE, "cache_em_alta")
LAST_SCAN = os.path.join(CACHE_DIR, "last_scan.json")
EXPORT_MD = os.path.join(REPO, "ESTRATEGIAS_PAINEIS", "radar", "EM_ALTA_HOJE.md")

# janelas em horas desde a publicação
WINDOWS = [("24h", "⚡ Últimas 24 h", 0, 24), ("2-3d", "🔥 2-3 dias", 24, 72), ("4-6d", "📈 4-6 dias", 72, 144)]

# filtro "Esta semana" da busca do YouTube
_SP_THIS_WEEK = "EgIIAw%3D%3D"


# ------------------------------------------------------------------ config
def load_nichos(path=NICHOS_PATH):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cfg = {"por_query": 25, "outlier_mult": 3.0, "cache_horas": 6,
           "auto_add_max_videos": 40, "auto_add_min_vph": 2000, "esfriou_dias": 7}
    cfg.update(data.get("config", {}))
    return data.get("nichos", []), cfg


# ------------------------------------------------------------------ cálculo puro
def _parse_ts(published):
    """Aceita epoch (int/float), ISO ('2026-11-05T10:00:00Z') ou 'YYYYMMDD'."""
    if published in (None, ""):
        return None
    if isinstance(published, (int, float)):
        return float(published)
    s = str(published).strip()
    try:
        if len(s) == 8 and s.isdigit():
            return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def hours_since(published, now=None):
    ts = _parse_ts(published)
    if ts is None:
        return None
    now = now if now is not None else time.time()
    return max((now - ts) / 3600.0, 0.0)


def vph(views, hours):
    if hours is None:
        return 0.0
    return round((views or 0) / max(hours, 1.0), 1)


def window_for(hours):
    if hours is None:
        return None
    for key, _lbl, lo, hi in WINDOWS:
        if lo <= hours < hi or (key == "24h" and hours == 0):
            return key
    return None


def mark_outliers(videos, mult=3.0, channel_medians=None):
    """Marca `outlier` (VPH ≥ mult × mediana). Usa a mediana do CANAL quando
    conhecida (≥3 vídeos), senão a mediana da busca/nicho."""
    channel_medians = channel_medians or {}
    by_query = {}
    for v in videos:
        by_query.setdefault(v.get("nicho"), []).append(v["vph"])
    q_med = {k: statistics.median(x) for k, x in by_query.items() if x}
    for v in videos:
        base = channel_medians.get(v.get("channel_id")) or q_med.get(v.get("nicho")) or 0
        v["mediana_ref"] = round(base, 1)
        v["outlier_x"] = round(v["vph"] / base, 1) if base else 0
        v["outlier"] = bool(base) and v["vph"] >= mult * base
    return videos


def rank(videos, now=None, mult=3.0, channel_medians=None, top=50):
    """Enriquece com horas/VPH/janela, marca outliers e separa por janela (VPH desc)."""
    seen, clean = set(), []
    for v in videos:
        if v.get("video_id") in seen:
            continue
        seen.add(v.get("video_id"))
        h = hours_since(v.get("published"), now)
        v = dict(v, hours=round(h, 1) if h is not None else None)
        v["vph"] = vph(v.get("views"), h)
        v["window"] = window_for(h)
        if v["window"]:
            clean.append(v)
    mark_outliers(clean, mult, channel_medians)
    out = {}
    for key, *_ in WINDOWS:
        w = [v for v in clean if v["window"] == key]
        w.sort(key=lambda v: (v["outlier"], v["vph"]), reverse=True)
        out[key] = w[:top]
    return out


# ------------------------------------------------------------------ cache
def _cache_path(kind, key):
    h = hashlib.sha1(f"{kind}:{key}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{kind}_{h}.json")


def cache_get(kind, key, ttl_hours):
    p = _cache_path(kind, key)
    try:
        if time.time() - os.path.getmtime(p) > ttl_hours * 3600:
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def cache_put(kind, key, value):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(kind, key), "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)


# ------------------------------------------------------------------ coleta
def search_ytdlp(query, n=25):
    """Busca 'esta semana' via yt-dlp (sem cota). Devolve dicts parciais."""
    import yt_dlp  # import tardio: só quem varre precisa
    from urllib.parse import quote_plus
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={_SP_THIS_WEEK}"
    opts = {"quiet": True, "skip_download": True, "extract_flat": True,
            "playlistend": n, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    out = []
    for e in (info.get("entries") or [])[:n]:
        if not e or not e.get("id") or "shorts/" in (e.get("url") or ""):
            continue
        out.append({
            "video_id": e["id"], "title": e.get("title", ""),
            "channel": e.get("channel") or e.get("uploader") or "",
            "channel_id": e.get("channel_id") or "",
            "views": e.get("view_count") or 0,
            "published": e.get("timestamp") or e.get("release_timestamp") or e.get("upload_date"),
            "duration_min": round((e.get("duration") or 0) / 60, 1),
            "thumb": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
        })
    return out


def complete_with_api(videos, budget=None):
    """Completa data/views faltando via videos.list (1 unidade/50). Silencioso se
    a API não estiver disponível."""
    missing = [v["video_id"] for v in videos if not v.get("published") or not v.get("views")]
    if not missing:
        return 0
    try:
        import yt  # noqa: depende do youtube_analyzer local
    except Exception:
        return 0
    stats = {s["video_id"]: s for s in yt.ya.get_video_stats(missing)}
    if budget is not None:
        budget["units"] -= len(missing) // 50 + 1
    for v in videos:
        s = stats.get(v["video_id"])
        if s:
            v["published"] = v.get("published") or s.get("published")
            v["views"] = s.get("views") or v.get("views")
            v.setdefault("channel", s.get("channel_title", ""))
    return len(missing)


def complete_with_ytdlp(videos, limit=30):
    """Sem API: extrai a página do vídeo (sem cota) para os que faltam data."""
    import yt_dlp
    todo = [v for v in videos if not v.get("published")][:limit]
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "no_warnings": True}) as ydl:
        for v in todo:
            try:
                i = ydl.extract_info(f"https://www.youtube.com/watch?v={v['video_id']}", download=False)
            except Exception:
                continue
            v["published"] = i.get("timestamp") or i.get("upload_date")
            v["views"] = i.get("view_count") or v.get("views")
            v["channel_id"] = v.get("channel_id") or i.get("channel_id", "")
            v["channel_video_count"] = i.get("channel_video_count")
    return len(todo)


def scan(use_api=True, nichos=None, cfg=None, log=print):
    """Varre todos os nichos. Devolve o resultado completo (também grava em cache)."""
    if nichos is None or cfg is None:
        nichos, cfg = load_nichos()
    all_v, budget = [], {"units": 0}
    for n in nichos:
        for q in n.get("queries", []):
            vids = cache_get("q", q, cfg["cache_horas"])
            if vids is None:
                try:
                    vids = search_ytdlp(q, cfg["por_query"])
                except Exception as e:  # rede/yt-dlp quebrado: segue
                    log(f"  ! yt-dlp falhou em '{q}': {e}")
                    vids = []
                cache_put("q", q, vids)
            for v in vids:
                all_v.append(dict(v, nicho=n["key"], nicho_label=n.get("label", n["key"]),
                                  adjacente=bool(n.get("adjacente")), query=q))
            log(f"  {n.get('label')} · '{q}': {len(vids)} vídeos")
    if use_api:
        complete_with_api(all_v, budget)
    if any(not v.get("published") for v in all_v):
        try:
            complete_with_ytdlp(all_v)
        except Exception as e:
            log(f"  ! completar via yt-dlp falhou: {e}")
    ranked = rank(all_v, mult=cfg["outlier_mult"])
    result = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "api_units": -budget["units"], "total": len(all_v), "windows": ranked}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(LAST_SCAN, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return result


def load_last_scan():
    try:
        with open(LAST_SCAN, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ canais monitorados
MONITOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS monitored_channels (
    channel_id   TEXT PRIMARY KEY,
    name         TEXT,
    nicho        TEXT,
    origem       TEXT DEFAULT 'auto',   -- auto / manual
    status       TEXT DEFAULT 'quente', -- quente / esfriou
    video_count  INTEGER,
    best_vph     REAL DEFAULT 0,
    best_title   TEXT,
    best_video_id TEXT,
    first_seen   TEXT,
    last_hot     TEXT
);
"""


def update_monitored(con, result, cfg, now=None):
    """Auto-adiciona canais novos explodindo e marca os que esfriaram.
    Devolve a lista de canais NOVOS adicionados neste scan."""
    con.executescript(MONITOR_SCHEMA)
    now = now if now is not None else time.time()
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    known_counts = {}
    try:
        for r in con.execute("SELECT channel_id, video_count FROM channels"):
            known_counts[r[0]] = r[1]
    except Exception:
        pass
    best = {}
    for vids in result.get("windows", {}).values():
        for v in vids:
            cid = v.get("channel_id")
            if cid and v["vph"] > best.get(cid, {}).get("vph", -1):
                best[cid] = v
    added = []
    for cid, v in best.items():
        vc = v.get("channel_video_count") or known_counts.get(cid)
        row = con.execute("SELECT channel_id FROM monitored_channels WHERE channel_id=?", (cid,)).fetchone()
        hot = v["vph"] >= cfg["auto_add_min_vph"]
        if row:
            if hot:
                con.execute("UPDATE monitored_channels SET status='quente', last_hot=?, "
                            "best_vph=MAX(best_vph, ?), video_count=COALESCE(?, video_count) "
                            "WHERE channel_id=?", (stamp, v["vph"], vc, cid))
        elif hot and (vc is None or vc <= cfg["auto_add_max_videos"]):
            con.execute("INSERT INTO monitored_channels (channel_id, name, nicho, origem, status, "
                        "video_count, best_vph, best_title, best_video_id, first_seen, last_hot) "
                        "VALUES (?,?,?,'auto','quente',?,?,?,?,?,?)",
                        (cid, v.get("channel"), v.get("nicho"), vc, v["vph"], v.get("title"),
                         v.get("video_id"), stamp, stamp))
            added.append({"channel_id": cid, "name": v.get("channel"), "nicho": v.get("nicho_label"),
                          "video_count": vc, "vph": v["vph"], "title": v.get("title"),
                          "video_id": v.get("video_id")})
    cutoff = datetime.fromtimestamp(now - cfg["esfriou_dias"] * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("UPDATE monitored_channels SET status='esfriou' WHERE last_hot < ?", (cutoff,))
    con.commit()
    return added


def list_monitored(con):
    con.executescript(MONITOR_SCHEMA)
    rows = con.execute("SELECT * FROM monitored_channels ORDER BY status='esfriou', best_vph DESC").fetchall()
    cols = [d[0] for d in con.execute("SELECT * FROM monitored_channels LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


# ------------------------------------------------------------------ export
def _fmt(n):
    n = float(n or 0)
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{n:.0f}"


def _md_table(vids, n):
    if not vids:
        return "_Nada nesta janela no último scan._\n"
    lines = ["| # | VPH | Views | Idade | Nicho | Vídeo | Canal |",
             "|---|---|---|---|---|---|---|"]
    for i, v in enumerate(vids[:n], 1):
        tag = f" 🚨 {v['outlier_x']}×" if v.get("outlier") else ""
        title = (v.get("title") or "").replace("|", "/")
        lines.append(f"| {i} | **{_fmt(v['vph'])}**{tag} | {_fmt(v.get('views'))} | {v.get('hours', 0):.0f} h | "
                     f"{v.get('nicho_label', '')} | [{title}](https://youtu.be/{v['video_id']}) | "
                     f"{(v.get('channel') or '').replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def export_md(result, novos=None, path=EXPORT_MD, n=20):
    w = result.get("windows", {})
    md = [f"# 🔥 EM ALTA HOJE — {result.get('ts', '')[:10]}", "",
          f"Scan: `{result.get('ts', '')}` · {result.get('total', 0)} vídeos lidos · "
          f"API usada: {result.get('api_units', 0)} unidades · 🚨 = outlier (VPH ≥3× a mediana)", "",
          "## ⚡ Top 24 h", "", _md_table(w.get("24h", []), n),
          "## 🔥 Top 2-3 dias", "", _md_table(w.get("2-3d", []), n),
          "## 📈 Top 4-6 dias", "", _md_table(w.get("4-6d", []), 10),
          "## 🆕 Canais novos explodindo", ""]
    if novos:
        md += ["| Canal | Nicho | Vídeos | VPH | Hit |", "|---|---|---|---|---|"]
        for c in novos:
            md.append(f"| {(c.get('name') or '').replace('|', '/')} | {c.get('nicho')} | {c.get('video_count') or '?'} | "
                      f"{_fmt(c['vph'])} | [{(c.get('title') or '').replace('|', '/')}]"
                      f"(https://youtu.be/{c.get('video_id')}) |")
    else:
        md.append("_Nenhum canal novo entrou na lista hoje._")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    return path


# ------------------------------------------------------------------ CLI
def main(argv):
    import db
    nichos, cfg = load_nichos()
    if argv and argv[0] == "export":
        res = load_last_scan()
        if not res:
            print("Sem scan salvo. Rode: python em_alta.py scan")
            return 1
        print("Exportado:", export_md(res, res.get("novos")))
        return 0
    res = scan(use_api="--no-api" not in argv, nichos=nichos, cfg=cfg)
    db.init_db()
    con = db.connect()
    res["novos"] = update_monitored(con, res, cfg)
    con.close()
    with open(LAST_SCAN, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    print("Exportado:", export_md(res, res["novos"]))
    for k, lbl, *_ in WINDOWS:
        print(f"  {lbl}: {len(res['windows'][k])} vídeos")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
