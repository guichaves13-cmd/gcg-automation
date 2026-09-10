# -*- coding: utf-8 -*-
"""
================================================================================
 RADAR PRO — Banco de dados (SQLite)
================================================================================
Persiste TODOS os canais mapeados do YouTube + seus vídeos recentes.
É o coração da ferramenta: enquanto o radar_viral antigo fazia buscas sob demanda,
aqui a gente CONSTRÓI e MANTÉM um banco crescente (igual ao Radar Club, mas nosso).

Tabelas:
  channels  -> 1 linha por canal, com scores já calculados
  videos    -> vídeos recentes por canal (com VPH)
  crawl_log -> histórico de varreduras (para "novos hoje / 7 dias")
================================================================================
"""
import os
import sqlite3
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "radar.db")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id      TEXT PRIMARY KEY,
    name            TEXT,
    handle          TEXT,
    avatar          TEXT,
    description     TEXT,
    country         TEXT,
    subs            INTEGER DEFAULT 0,
    total_views     INTEGER DEFAULT 0,
    video_count     INTEGER DEFAULT 0,
    created_at      TEXT,              -- data de criação do canal (YouTube)
    category        TEXT DEFAULT 'Outros',
    language        TEXT DEFAULT '?',
    is_music        INTEGER DEFAULT 0,
    has_long        INTEGER DEFAULT 0, -- possui vídeos longos (>20min, horizontal)
    has_short       INTEGER DEFAULT 0, -- possui Shorts (<=1.1min, vertical)
    viral_score     REAL DEFAULT 0,    -- darkScore 0-100 (o "fogo")
    subs_multiplier REAL DEFAULT 0,    -- views_per_sub (o "132x inscritos")
    score_potencial REAL DEFAULT 0,    -- potencial relativo ao nicho
    best_views      INTEGER DEFAULT 0, -- melhor vídeo recente
    best_vph        REAL DEFAULT 0,    -- MELHORIA NOSSA: VPH do hit
    best_video_id   TEXT,
    best_title      TEXT,
    avg_vph         REAL DEFAULT 0,    -- MELHORIA NOSSA: VPH médio
    monetized       INTEGER DEFAULT 0, -- o "$"
    faceless        INTEGER DEFAULT 0, -- % estimada faceless (0-100)
    badge           TEXT DEFAULT '',   -- Explosivo / Em Alta / ...
    crescimento     TEXT DEFAULT '',   -- frase explicativa
    alinhamento     INTEGER DEFAULT 0, -- passou no filtro de qualidade dark
    fonte           TEXT DEFAULT 'acervo', -- acervo / comunidade / seed
    vids_per_month  REAL DEFAULT 0,
    views_per_month INTEGER DEFAULT 0,
    newest_upload   TEXT,              -- data do vídeo mais recente
    first_seen      TEXT,              -- quando ENTROU no nosso radar
    last_updated    TEXT,
    favorite        INTEGER DEFAULT 0,
    notes           TEXT DEFAULT '',
    seed_source     TEXT DEFAULT ''    -- query/canal que descobriu este
);
CREATE INDEX IF NOT EXISTS idx_ch_score    ON channels(viral_score);
CREATE INDEX IF NOT EXISTS idx_ch_cat      ON channels(category);
CREATE INDEX IF NOT EXISTS idx_ch_lang     ON channels(language);
CREATE INDEX IF NOT EXISTS idx_ch_first    ON channels(first_seen);
CREATE INDEX IF NOT EXISTS idx_ch_newest   ON channels(newest_upload);

CREATE TABLE IF NOT EXISTS videos (
    video_id     TEXT PRIMARY KEY,
    channel_id   TEXT,
    title        TEXT,
    views        INTEGER DEFAULT 0,
    vph          REAL DEFAULT 0,
    duration_min REAL DEFAULT 0,
    is_long      INTEGER DEFAULT 0,
    published    TEXT,
    days_since   REAL DEFAULT 0,
    thumb        TEXT,
    updated      TEXT
);
CREATE INDEX IF NOT EXISTS idx_v_ch   ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_v_vph  ON videos(vph);

CREATE TABLE IF NOT EXISTS crawl_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT,
    mode      TEXT,
    seeds     TEXT,
    found     INTEGER,
    new_ch    INTEGER,
    api_units INTEGER
);

-- Banco de Referências: coleções nomeadas de canais (igual ao Radar Club)
CREATE TABLE IF NOT EXISTS collections (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT,
    created  TEXT
);
CREATE TABLE IF NOT EXISTS collection_items (
    collection_id INTEGER,
    channel_id    TEXT,
    added         TEXT,
    PRIMARY KEY (collection_id, channel_id)
);
"""


_NEW_COLS = [
    ("score_potencial", "REAL DEFAULT 0"), ("monetized", "INTEGER DEFAULT 0"),
    ("faceless", "INTEGER DEFAULT 0"), ("badge", "TEXT DEFAULT ''"),
    ("crescimento", "TEXT DEFAULT ''"), ("alinhamento", "INTEGER DEFAULT 0"),
    ("fonte", "TEXT DEFAULT 'acervo'"), ("vids_per_month", "REAL DEFAULT 0"),
    ("views_per_month", "INTEGER DEFAULT 0"), ("has_short", "INTEGER DEFAULT 0"),
    ("created_days", "INTEGER DEFAULT 0"),
]


def init_db():
    con = connect()
    con.executescript(SCHEMA)
    # migração: adiciona colunas novas em bancos antigos
    have = {r["name"] for r in con.execute("PRAGMA table_info(channels)").fetchall()}
    for col, decl in _NEW_COLS:
        if col not in have:
            con.execute(f"ALTER TABLE channels ADD COLUMN {col} {decl}")
    con.commit()
    con.close()


def upsert_channel(con, ch):
    """Insere/atualiza um canal. `ch` é um dict com as chaves da tabela channels.
    Preserva first_seen e favorite se o canal já existir."""
    existing = con.execute(
        "SELECT first_seen, favorite, notes FROM channels WHERE channel_id=?",
        (ch["channel_id"],),
    ).fetchone()
    if existing:
        ch.setdefault("first_seen", existing["first_seen"])
        ch.setdefault("favorite", existing["favorite"])
        if not ch.get("notes"):
            ch["notes"] = existing["notes"]
    else:
        ch.setdefault("first_seen", now_iso())
        ch.setdefault("favorite", 0)
    ch["last_updated"] = now_iso()

    cols = ["channel_id", "name", "handle", "avatar", "description", "country",
            "subs", "total_views", "video_count", "created_at", "category",
            "language", "is_music", "has_long", "has_short", "viral_score", "subs_multiplier",
            "score_potencial", "best_views", "best_vph", "best_video_id", "best_title",
            "avg_vph", "monetized", "faceless", "badge", "crescimento", "alinhamento",
            "fonte", "vids_per_month", "views_per_month", "created_days",
            "newest_upload", "first_seen", "last_updated", "favorite", "notes",
            "seed_source"]
    vals = [ch.get(c) for c in cols]
    placeholders = ",".join("?" * len(cols))
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("channel_id", "first_seen", "favorite", "notes"))
    con.execute(
        f"INSERT INTO channels ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(channel_id) DO UPDATE SET {updates}",
        vals,
    )
    return existing is None  # True se é canal novo


def replace_channel_videos(con, channel_id, videos):
    """Substitui os vídeos recentes de um canal (mantém o banco enxuto)."""
    con.execute("DELETE FROM videos WHERE channel_id=?", (channel_id,))
    for v in videos:
        con.execute(
            "INSERT OR REPLACE INTO videos "
            "(video_id, channel_id, title, views, vph, duration_min, is_long, published, days_since, thumb, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (v["video_id"], channel_id, v["title"], v["views"], v["vph"],
             v.get("duration_min", 0), int(v.get("is_long", 0)), v.get("published"),
             v.get("days_since", 0), v.get("thumb", ""), now_iso()),
        )


def log_crawl(con, mode, seeds, found, new_ch, api_units):
    con.execute(
        "INSERT INTO crawl_log (ts, mode, seeds, found, new_ch, api_units) VALUES (?,?,?,?,?,?)",
        (now_iso(), mode, seeds, found, new_ch, api_units),
    )


def get_channel_videos(con, channel_id):
    rows = con.execute(
        "SELECT * FROM videos WHERE channel_id=? ORDER BY published DESC", (channel_id,)
    ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    con = connect()
    n = con.execute("SELECT COUNT(*) c FROM channels").fetchone()["c"]
    v = con.execute("SELECT COUNT(*) c FROM videos").fetchone()["c"]
    print(f"Banco OK em {DB_PATH}")
    print(f"  canais: {n} | videos: {v}")
    con.close()
