# -*- coding: utf-8 -*-
"""
================================================================================
 RADAR PRO — Ponte para a YouTube Data API v3
================================================================================
Reaproveita o cliente com ROTAÇÃO DE CHAVES que você já tem
(.gemini/.../youtube_analyzer.py): quota "nunca acaba", VPH pronto, etc.
Aqui só adicionamos o que o Radar Pro precisa: buscar canais, puxar snippet
completo (avatar, handle, país, criação, topicDetails) e montar dicts de vídeo.
================================================================================
"""
import os
import re
import sys

sys.path.insert(0, r"C:\Users\Guilherme\.gemini\antigravity\scratch\youtube_analyzer")
import youtube_analyzer as ya  # noqa: E402

# custo aproximado em unidades de quota (para o orçamento por varredura)
COST = {"search": 100, "channels": 1, "videos": 1, "playlistItems": 1}


def dur_min(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return round(h * 60 + mi + s / 60, 1)


def thumb_url(video_id):
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def search_channel_ids(query, max_results=50, order="viewCount", published_after=None,
                       video_duration=None, budget=None):
    """Busca vídeos e devolve os channel_ids únicos que apareceram (+ os vídeos).
    `budget` é um dict {'units': N} decrementado a cada chamada de API."""
    params = {"part": "snippet", "q": query, "type": "video",
              "maxResults": min(max_results, 50), "order": order}
    if published_after:
        params["publishedAfter"] = published_after
    if video_duration:
        params["videoDuration"] = video_duration
    if budget is not None:
        budget["units"] -= COST["search"]
    resp = ya._exec(lambda: ya.youtube.search().list(**params))
    ch_ids, vids = [], []
    for it in resp.get("items", []):
        ch_ids.append(it["snippet"]["channelId"])
        vids.append(it["id"]["videoId"])
    # dedup preservando ordem
    seen = set()
    ch_ids = [c for c in ch_ids if not (c in seen or seen.add(c))]
    return ch_ids, vids


def fetch_channels(channel_ids, budget=None):
    """Snippet completo + estatísticas + topicDetails de até N canais (batch de 50)."""
    out = {}
    ids = list(channel_ids)
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        if budget is not None:
            budget["units"] -= COST["channels"]
        resp = ya._exec(lambda b=batch: ya.youtube.channels().list(
            part="snippet,statistics,topicDetails,contentDetails", id=",".join(b)))
        for it in resp.get("items", []):
            sn = it["snippet"]
            st = it.get("statistics", {})
            td = [u.rsplit("/", 1)[-1].lower().replace("_", " ")
                  for u in it.get("topicDetails", {}).get("topicCategories", [])]
            thumbs = sn.get("thumbnails", {})
            avatar = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            uploads = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
            out[it["id"]] = {
                "channel_id": it["id"],
                "name": sn.get("title", ""),
                "handle": sn.get("customUrl", ""),
                "avatar": avatar,
                "description": sn.get("description", ""),
                "country": sn.get("country", ""),
                "created_at": sn.get("publishedAt", "")[:10],
                "subs": int(st.get("subscriberCount", 0)),
                "total_views": int(st.get("viewCount", 0)),
                "video_count": int(st.get("videoCount", 0)),
                "hidden_subs": st.get("hiddenSubscriberCount", False),
                "topic_cats": td,
                "uploads_playlist": uploads,
            }
    return out


def recent_videos(channel_id, uploads_playlist=None, max_results=12, budget=None):
    """Puxa os N vídeos mais recentes do canal (via playlist de uploads) com VPH.
    Devolve dicts prontos para o banco (com duration_min/is_long/thumb)."""
    if not uploads_playlist:
        if budget is not None:
            budget["units"] -= COST["channels"]
        r = ya._exec(lambda: ya.youtube.channels().list(part="contentDetails", id=channel_id))
        items = r.get("items", [])
        if not items:
            return []
        uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    if budget is not None:
        budget["units"] -= COST["playlistItems"]
    pl = ya._exec(lambda: ya.youtube.playlistItems().list(
        part="snippet", playlistId=uploads_playlist, maxResults=min(max_results, 50)))
    vids = [it["snippet"]["resourceId"]["videoId"] for it in pl.get("items", [])]
    if not vids:
        return []
    if budget is not None:
        budget["units"] -= COST["videos"] * (len(vids) // 50 + 1)
    stats = ya.get_video_stats(vids)
    out = []
    for v in stats:
        dm = dur_min(v["duration"])
        out.append({
            "video_id": v["video_id"], "title": v["title"], "views": v["views"],
            "vph": v["vph"], "duration_min": dm, "is_long": 1 if dm >= 20 else 0,
            "published": v["published"], "days_since": v["days_since_upload"],
            "thumb": thumb_url(v["video_id"]),
        })
    return out


def resolve_channel_id(url_or_handle, budget=None):
    """Extrai/resolve um channel_id de uma URL, @handle ou id cru."""
    s = (url_or_handle or "").strip()
    m = re.search(r"/channel/(UC[\w-]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"UC[\w-]{20,}", s):
        return s
    handle = None
    m = re.search(r"@([\w.\-]+)", s)
    if m:
        handle = m.group(1)
    try:
        if handle:
            if budget is not None:
                budget["units"] -= COST["channels"]
            r = ya._exec(lambda: ya.youtube.channels().list(part="id", forHandle=handle))
            items = r.get("items", [])
            if items:
                return items[0]["id"]
        # último recurso: busca por nome (custa 100)
        if budget is not None:
            budget["units"] -= COST["search"]
        r = ya._exec(lambda: ya.youtube.search().list(
            part="snippet", q=re.sub(r"https?://\S+/", "", s), type="channel", maxResults=1))
        if r.get("items"):
            return r["items"][0]["snippet"]["channelId"]
    except Exception:
        return None
    return None
