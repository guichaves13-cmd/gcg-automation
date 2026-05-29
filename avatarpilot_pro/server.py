# -*- coding: utf-8 -*-
"""
AvatarPilot Pro — AI Talking Avatar Generator v3
Port: 5052
Features: SadTalker lip sync (chunked for 30min-1h), Edge-TTS/ElevenLabs,
          voice cloning, AI image generation (Pollinations.ai), background compositing,
          history, batch processing, AI script generation, templates, dashboard,
          plan-based duration limits (30min / 1h).
"""
import os, sys, json, time, uuid, asyncio, subprocess, re, shutil, hashlib, threading, math, sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory, Response
import threading
from threading import Thread

# ── Fix Windows encoding ────────────────────────────────────────────────────
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
os.environ['PYTHONIOENCODING'] = 'utf-8'

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR  = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR  = os.path.join(BASE_DIR, "outputs")
BG_DIR      = os.path.join(BASE_DIR, "backgrounds")
MODELS_DIR  = os.path.join(BASE_DIR, "models")
DATA_DIR    = os.path.join(BASE_DIR, "data")

for d in [UPLOAD_DIR, OUTPUT_DIR, BG_DIR, MODELS_DIR, DATA_DIR]:
    os.makedirs(d, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB

# ── Persistence files ────────────────────────────────────────────────────────
SETTINGS_FILE  = os.path.join(BASE_DIR, "settings.json")
HISTORY_FILE   = os.path.join(DATA_DIR, "history.json")
TEMPLATES_FILE = os.path.join(DATA_DIR, "templates.json")
STATS_FILE     = os.path.join(DATA_DIR, "stats.json")

# ── Plan limits (seconds of audio/video duration) ───────────────────────────
PLAN_LIMITS = {
    "free":     5 * 60,       # 5 minutes (demo)
    "starter":  30 * 60,      # 30 minutes
    "pro":      60 * 60,      # 1 hour
    "unlimited": None,         # no limit
}
DEFAULT_PLAN = "unlimited"    # change per user/tenant as needed

# ── Jobs + Batch ─────────────────────────────────────────────────────────────
jobs = {}         # {job_id: status dict}
batch_queue = []  # list of job_ids waiting
batch_lock    = threading.Lock()
jobs_lock     = threading.Lock()
history_lock  = threading.Lock()  # serializa leitura/escrita do history.json

# ── SQLite path ───────────────────────────────────────────────────────────────
DB_PATH = os.path.join(DATA_DIR, "avatarpilot.db")

# ── Webhook registry {job_id: [urls]} and global webhooks ────────────────────
webhook_registry = {}   # {job_id: [url, ...]}
global_webhooks  = []   # URLs notified on every completed job

# ── Cloud GPU max workers ─────────────────────────────────────────────────────
def _detect_safe_max_workers() -> int:
    """
    Auto-detect safe concurrency based on GPU VRAM.
    MuseTalk needs ~4-5GB, SadTalker ~3GB, GFPGAN ~2GB per job.
    Conservative limits prevent OOM deadlocks:
      <=10GB VRAM  -> 1 worker (RTX 4060/3060, integrated)
      <=16GB VRAM  -> 2 workers (RTX 4070, A4000)
      <=24GB VRAM  -> 3 workers (RTX 4080/3090, A5000)
       >24GB VRAM  -> 4 workers (RTX 4090, A6000, A100)
    Override via env var AVP_MAX_WORKERS.
    """
    env_override = os.environ.get("AVP_MAX_WORKERS", "").strip()
    if env_override.isdigit() and int(env_override) > 0:
        return int(env_override)
    try:
        import torch as _td
        if _td.cuda.is_available():
            total_gb = _td.cuda.get_device_properties(0).total_memory / 1024**3
            if total_gb <= 10:  return 1
            if total_gb <= 16:  return 2
            if total_gb <= 24:  return 3
            return 4
    except Exception:
        pass
    return 1  # safe default for unknown hardware

MAX_WORKERS = _detect_safe_max_workers()
active_workers = 0
def _reset_active_workers():
    global active_workers
    with workers_lock:
        active_workers = 0
# Semaphore enforces real concurrency limit (prevents VRAM/CPU oversubscription
# when many users submit simultaneously). active_workers stays for UI display.
_pipeline_semaphore = threading.Semaphore(MAX_WORKERS)
workers_lock   = threading.Lock()

# ── Simple in-memory rate limiter (no external deps) ─────────────────────────
_rate_limit_lock  = threading.Lock()
_rate_limit_store = {}   # {ip: [timestamp, ...]}
_RATE_LIMIT_MAX   = 10   # max jobs per IP per window
_RATE_LIMIT_WIN   = 60   # window in seconds

def _check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed; False if rate-limited."""
    import time as _time
    now = _time.time()
    with _rate_limit_lock:
        timestamps = _rate_limit_store.get(ip, [])
        timestamps = [t for t in timestamps if now - t < _RATE_LIMIT_WIN]
        if len(timestamps) >= _RATE_LIMIT_MAX:
            _rate_limit_store[ip] = timestamps
            return False
        timestamps.append(now)
        _rate_limit_store[ip] = timestamps
        # Evict old IPs periodically to prevent unbounded growth
        if len(_rate_limit_store) > 5000:
            cutoff = now - _RATE_LIMIT_WIN
            _rate_limit_store.update({k: [t for t in v if t > cutoff] for k, v in list(_rate_limit_store.items())})
            for k in [k for k, v in _rate_limit_store.items() if not v]:
                del _rate_limit_store[k]
        return True

# ── Anti-error system ──────────────────────────────────────────────────────────
DISK_WARN_MB    = 500    # warn when free disk < 500MB
DISK_BLOCK_MB   = 100    # block new jobs when < 100MB free
VRAM_MIN_GB     = 1.5    # minimum free VRAM to start MuseTalk
# Cap de script (TTS). ~14 chars/s → 15000 chars ≈ 18min de vídeo. Configurável via
# env p/ vídeos mais longos: AVP_MAX_SCRIPT_CHARS=50000 (~60min) com enhance_face=false
# + AVP_STUCK_TIMEOUT_MIN alto. Em 8GB VRAM, vídeos muito longos levam horas — prefira
# RTX 4090/cloud p/ produção de longa duração.
MAX_SCRIPT_CHARS = int(os.environ.get("AVP_MAX_SCRIPT_CHARS", "15000"))

# ── Sistema de licenças desktop (hardware-bound Ed25519) ─────────────────────
try:
    import license_system as _lic
    _LICENSE_AVAILABLE = True
except Exception as _lic_e:
    _LICENSE_AVAILABLE = False
    print(f"  [License] módulo indisponível ({_lic_e}) — rodando sem licenciamento", flush=True)
_LICENSE_STATE = {"active": False, "plan": "trial"}  # preenchido no boot
_temp_prefixes  = ("mst_avp_", "mst_chunk_", "avp_loop_", "avp_hd_", "sway_",
                   "avp_sad_", "wav2lip_", "fswap_vid_",
                   "echo_avp_", "codeformer_avp_", "gfpgan_avp_",
                   "gfpgan_chunk_", "w2l_chunks_", "st_chunks_", "chroma_")

def _free_disk_mb() -> float:
    """Return free disk MB on the output drive."""
    try:
        import shutil as _sh
        return _sh.disk_usage(OUTPUT_DIR).free / 1024 / 1024
    except Exception:
        return 9999.0

def _free_vram_gb() -> float:
    """Return free VRAM in GB, 99 if no GPU."""
    try:
        import torch as _t
        if _t.cuda.is_available():
            return round(_t.cuda.mem_get_info(0)[0] / 1024**3, 2)
    except Exception:
        pass
    return 99.0

def _cleanup_orphan_tmps():
    """Remove leftover temp dirs from crashed jobs."""
    import tempfile as _tmlib, glob as _gl
    tmp_root = _tmlib.gettempdir()
    cleaned = 0
    for prefix in _temp_prefixes:
        for d in _gl.glob(os.path.join(tmp_root, prefix + "*")):
            try:
                shutil.rmtree(d, ignore_errors=True)
                cleaned += 1
            except Exception:
                pass
    if cleaned:
        print(f"  [Cleanup] Removed {cleaned} orphaned temp dir(s) from previous crashes")

def _check_cancel(job_id: str):
    """Raise if the job has been cancelled by the user."""
    if job_id and jobs.get(job_id, {}).get("_cancel"):
        jobs[job_id]["status"]   = "cancelled"
        jobs[job_id]["progress"] = 0
        jobs[job_id]["message"]  = "Job cancelado pelo usuário."
        raise Exception("Job cancelado pelo usuário.")

# ── VOICE PRESETS ─────────────────────────────────────────────────────────────
VOICE_PRESETS = {
    # pitch uses Hz (Edge-TTS format: "+5Hz", "-10Hz", "+0Hz")
    # rate uses % ("+10%", "-10%", "+0%")
    "documentary_narrator": {
        "edge_voice": "en-US-GuyNeural", "rate": "-10%", "pitch": "-10Hz",
        "description": "Deep, authoritative narrator",
        "best_for": ["documentary", "history", "science", "nature"],
        "category": "English",
    },
    "news_anchor": {
        "edge_voice": "en-US-JennyNeural", "rate": "+5%", "pitch": "+0Hz",
        "description": "Professional news presenter, clear and confident",
        "best_for": ["news", "breaking", "reports", "briefings"],
        "category": "English",
    },
    "sales_pitch": {
        "edge_voice": "en-US-AndrewNeural", "rate": "+15%", "pitch": "+5Hz",
        "description": "Energetic, persuasive sales voice",
        "best_for": ["sales", "marketing", "ads", "promos"],
        "category": "English",
    },
    "corporate_trainer": {
        "edge_voice": "en-US-EricNeural", "rate": "-5%", "pitch": "+0Hz",
        "description": "Clear, professional e-learning narration",
        "best_for": ["e-learning", "training", "tutorial", "courses"],
        "category": "English",
    },
    "friendly_explainer": {
        "edge_voice": "en-US-AriaNeural", "rate": "+0%", "pitch": "+5Hz",
        "description": "Warm, engaging explainer voice",
        "best_for": ["explainer", "education", "product demo"],
        "category": "English",
    },
    "podcast_host": {
        "edge_voice": "en-US-GuyNeural", "rate": "-3%", "pitch": "+3Hz",
        "description": "Conversational, warm podcast tone",
        "best_for": ["podcast", "interview", "storytelling"],
        "category": "English",
    },
    "br_apresentador": {
        "edge_voice": "pt-BR-AntonioNeural", "rate": "-5%", "pitch": "+0Hz",
        "description": "Apresentador brasileiro profissional",
        "best_for": ["apresentação", "notícias", "documentário"],
        "category": "Português",
    },
    "br_educacional": {
        "edge_voice": "pt-BR-FranciscaNeural", "rate": "-8%", "pitch": "+5Hz",
        "description": "Voz educacional feminina, clara e didática",
        "best_for": ["aulas", "cursos", "e-learning"],
        "category": "Português",
    },
    "es_noticias": {
        "edge_voice": "es-MX-JorgeNeural", "rate": "+0%", "pitch": "+0Hz",
        "description": "Locutor de noticias profesional español",
        "best_for": ["noticias", "presentación", "documental"],
        "category": "Español",
    },
    "fr_narrateur": {
        "edge_voice": "fr-FR-HenriNeural", "rate": "-5%", "pitch": "+0Hz",
        "description": "Narrateur professionnel français",
        "best_for": ["documentaire", "présentation", "e-learning"],
        "category": "Français",
    },
    "de_sprecher": {
        "edge_voice": "de-DE-ConradNeural", "rate": "-5%", "pitch": "+0Hz",
        "description": "Professioneller deutscher Sprecher",
        "best_for": ["Dokumentation", "Präsentation", "Schulung"],
        "category": "Deutsch",
    },
    "asmr_calm": {
        "edge_voice": "en-US-JennyNeural", "rate": "-20%", "pitch": "-15Hz",
        "description": "Soft, calm, soothing whisper-like delivery",
        "best_for": ["relaxation", "wellness", "meditation", "asmr"],
        "category": "Special",
    },
    "energetic_host": {
        "edge_voice": "en-US-BrianNeural", "rate": "+20%", "pitch": "+15Hz",
        "description": "High-energy game show / event host",
        "best_for": ["events", "gaming", "entertainment", "promo"],
        "category": "Special",
    },
}

# ============================================================================
# SETTINGS
# ============================================================================
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"elevenlabs_key": "", "tts_engine": "edge-tts",
            "default_voice": "en-US-GuyNeural", "plan": DEFAULT_PLAN}

def save_settings(s):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)

# ============================================================================
# SQLITE — PERSISTENT JOB QUEUE
# ============================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    # WAL mode for concurrent reader/writer safety (essential for hundreds of users).
    # Default 'rollback journal' mode blocks readers during writes — would cause stalls.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, much faster
        conn.execute("PRAGMA busy_timeout=5000")   # wait 5s before SQLITE_BUSY error
    except Exception as _pe:
        print(f"  [SQLite] WAL pragma failed (non-fatal): {_pe}")
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs_db (
        id TEXT PRIMARY KEY,
        status TEXT DEFAULT 'queued',
        progress INTEGER DEFAULT 0,
        config TEXT,
        output_path TEXT DEFAULT '',
        output_filename TEXT DEFAULT '',
        error TEXT DEFAULT '',
        message TEXT DEFAULT '',
        created_at TEXT,
        completed_at TEXT,
        duration REAL DEFAULT 0,
        thumbnail TEXT DEFAULT '',
        audio_duration REAL DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS webhooks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT DEFAULT '',
        url TEXT NOT NULL,
        global_hook INTEGER DEFAULT 0,
        created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS api_keys (
        key TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        plan TEXT DEFAULT 'starter',
        active INTEGER DEFAULT 1,
        jobs_generated INTEGER DEFAULT 0,
        seconds_generated REAL DEFAULT 0,
        created_at TEXT,
        last_used TEXT
    )""")
    conn.commit()
    conn.close()

# ============================================================================
# API KEY MANAGEMENT
# ============================================================================
def _api_key_db(write_fn=None, read_fn=None):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        if write_fn:
            write_fn(conn)
            conn.commit()
        if read_fn:
            return read_fn(conn)
    except Exception as _e:
        print(f"  [KeyDB] error: {_e}")
        return None
    finally:
        if conn:
            conn.close()

def apikey_create(name: str, plan: str = "starter") -> str:
    import secrets as _sec
    key = "avp_" + _sec.token_hex(24)
    _api_key_db(write_fn=lambda c: c.execute(
        "INSERT INTO api_keys (key, name, plan, active, created_at) VALUES (?,?,?,1,?)",
        (key, name[:100], plan, datetime.now().isoformat())
    ))
    return key

def apikey_validate(key: str) -> dict | None:
    """Return key record if valid and active, else None."""
    if not key or not key.startswith("avp_"):
        return None
    row = _api_key_db(read_fn=lambda c: c.execute(
        "SELECT key, name, plan, active, jobs_generated, seconds_generated FROM api_keys WHERE key=?", (key,)
    ).fetchone())
    if row and row["active"]:
        _api_key_db(write_fn=lambda c: c.execute(
            "UPDATE api_keys SET last_used=? WHERE key=?", (datetime.now().isoformat(), key)
        ))
        return dict(row)
    return None

def apikey_increment(key: str, duration: float):
    _api_key_db(write_fn=lambda c: c.execute(
        "UPDATE api_keys SET jobs_generated=jobs_generated+1, seconds_generated=seconds_generated+? WHERE key=?",
        (duration, key)
    ))

def apikey_list() -> list:
    rows = _api_key_db(read_fn=lambda c: c.execute(
        "SELECT key, name, plan, active, jobs_generated, seconds_generated, created_at, last_used FROM api_keys ORDER BY created_at DESC"
    ).fetchall())
    return [dict(r) for r in rows] if rows else []

def apikey_revoke(key: str):
    _api_key_db(write_fn=lambda c: c.execute(
        "UPDATE api_keys SET active=0 WHERE key=?", (key,)
    ))

def apikey_delete(key: str):
    _api_key_db(write_fn=lambda c: c.execute(
        "DELETE FROM api_keys WHERE key=?", (key,)
    ))

# Admin password (env var or fallback to a one-time generated key at startup)
_ADMIN_TOKEN: str = ""

_TOKEN_FILE = os.path.join(BASE_DIR, ".admin_token")

def _get_admin_token() -> str:
    global _ADMIN_TOKEN
    if _ADMIN_TOKEN:
        return _ADMIN_TOKEN
    import secrets as _sec
    env_token = os.environ.get("AVP_ADMIN_TOKEN", "")
    if env_token:
        _ADMIN_TOKEN = env_token
    else:
        # Try to reuse existing token from file (survives restarts)
        if os.path.exists(_TOKEN_FILE):
            try:
                _ADMIN_TOKEN = open(_TOKEN_FILE).read().strip()
            except Exception:
                pass
        if not _ADMIN_TOKEN:
            _ADMIN_TOKEN = "admin_" + _sec.token_hex(16)
        # Save token to file for easy retrieval
        try:
            with open(_TOKEN_FILE, "w") as _f:
                _f.write(_ADMIN_TOKEN)
        except Exception:
            pass
        print(f"\n  ╔══════════════════════════════════════════╗", flush=True)
        print(f"  ║  ADMIN TOKEN (guarde este token!)        ║", flush=True)
        print(f"  ║  {_ADMIN_TOKEN}  ║", flush=True)
        print(f"  ╚══════════════════════════════════════════╝\n", flush=True)
    return _ADMIN_TOKEN

def _init_admin_token():
    """Called at startup so the token is ready and printed before any request."""
    _get_admin_token()

def _require_admin(request) -> bool:
    """Return True if request has valid admin token."""
    token = request.headers.get("X-Admin-Token") or request.args.get("admin_token", "")
    return token == _get_admin_token()

AUTH_REQUIRED = False  # Set True to require API keys on all generate routes

def db_save_job(job_id, data: dict):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""INSERT OR REPLACE INTO jobs_db
            (id, status, progress, config, output_path, output_filename, error, message,
             created_at, completed_at, duration, thumbnail, audio_duration)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            job_id,
            data.get("status", "queued"),
            data.get("progress", 0),
            json.dumps(data.get("_config", {})),
            data.get("output_path", ""),
            data.get("output_filename", ""),
            data.get("error", ""),
            data.get("message", ""),
            data.get("created", ""),
            data.get("completed_at", ""),
            data.get("duration", 0),
            data.get("thumbnail", ""),
            data.get("audio_duration", 0),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [DB] save_job error: {e}")

def db_load_incomplete_jobs():
    """On startup, mark any jobs that were 'processing' as failed (server restarted)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT id, config, created_at FROM jobs_db WHERE status NOT IN ('done','error','cancelled')"
        ).fetchall()
        for row in rows:
            conn.execute("UPDATE jobs_db SET status='error', error='Server restarted' WHERE id=?", (row[0],))
        conn.commit()
        conn.close()
        if rows:
            print(f"  [DB] Marked {len(rows)} interrupted jobs as failed on startup")
        # Reset in-memory state: all worker threads died on restart
        global active_workers
        with workers_lock:
            active_workers = 0
        with jobs_lock:
            _orphans = [jid for jid, j in jobs.items() if j.get('status') not in ('done', 'error', 'cancelled')]
            for _oj in _orphans:
                jobs[_oj]['status'] = 'error'
                jobs[_oj]['error'] = 'Server restarted'
            if _orphans:
                print(f"  [DB] Cleaned {len(_orphans)} orphaned in-memory jobs")
    except Exception as e:
        print(f"  [DB] load_incomplete_jobs error: {e}")

def db_get_webhooks(job_id=""):
    try:
        conn = sqlite3.connect(DB_PATH)
        if job_id:
            rows = conn.execute(
                "SELECT url FROM webhooks WHERE job_id=? OR global_hook=1", (job_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT url FROM webhooks WHERE global_hook=1").fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []

def db_register_webhook(url, job_id="", is_global=False):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO webhooks (job_id, url, global_hook, created_at) VALUES (?,?,?,?)",
            (job_id, url, 1 if is_global else 0, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"  [DB] register_webhook error: {e}")
        return False

# ============================================================================
# HISTORY (persistent JSON)
# ============================================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(records):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

def add_to_history(record: dict):
    with history_lock:
        history = load_history()
        history.insert(0, record)
        history = history[:200]  # keep last 200
        save_history(history)

# ============================================================================
# STATS
# ============================================================================
def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"total_generated": 0, "total_seconds": 0, "start_date": datetime.now().isoformat()}

def save_stats(s):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)

def increment_stats(duration_seconds=0):
    s = load_stats()
    s["total_generated"] = s.get("total_generated", 0) + 1
    s["total_seconds"]   = s.get("total_seconds", 0) + duration_seconds
    save_stats(s)

# ============================================================================
# TEMPLATES
# ============================================================================
def load_templates():
    if os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_templates(templates):
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        json.dump(templates, f, indent=2, ensure_ascii=False)

# ============================================================================
# TTS ENGINES
# ============================================================================
def _validate_tts_input(text: str) -> str:
    """Validate and sanitize text before sending to TTS. Raises ValueError if invalid."""
    if text is None:
        raise ValueError("Roteiro vazio: digite o texto que o avatar deve falar.")
    t = str(text).strip()
    if not t:
        raise ValueError("Roteiro vazio: digite o texto que o avatar deve falar.")
    # Remove control characters that break Edge-TTS
    t = "".join(ch for ch in t if ch.isprintable() or ch in " \n\t")
    # If after sanitization only punctuation remains, that also fails Edge-TTS
    if not any(ch.isalnum() for ch in t):
        raise ValueError("Roteiro precisa conter palavras (apenas pontuação não funciona).")
    return t


async def _edge_tts_generate(text, voice, output_path):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def _edge_with_retry(coro_factory, voice: str, output_path: str, label: str = "edge-tts"):
    """
    Run an edge-tts coroutine with up to 3 retries and clearer error messages.
    Handles transient `NoAudioReceived` from the Microsoft service (very common).
    """
    import time as _t
    last_err = None
    for attempt in range(3):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro_factory())
            # Verify output is non-trivial
            if os.path.exists(output_path) and os.path.getsize(output_path) > 200:
                return output_path
            last_err = Exception("Edge-TTS retornou áudio vazio")
        except Exception as e:
            last_err = e
            name = type(e).__name__
            print(f"  [Edge-TTS] {label} attempt {attempt+1}/3 voice={voice} -> {name}: {e}")
        finally:
            loop.close()
        _t.sleep(1.5 * (attempt + 1))  # backoff: 1.5s, 3s, 4.5s

    # All retries failed — raise a clear PT-BR error
    msg = str(last_err) if last_err else "unknown"
    if "No audio was received" in msg or "NoAudioReceived" in msg:
        raise Exception(
            f"Voz '{voice}' não conseguiu gerar áudio (servidor Microsoft Edge-TTS). "
            f"Soluções: 1) Tente outra voz; 2) Reduza o tamanho do texto; "
            f"3) Verifique conexão de internet; 4) Tente em alguns minutos (rate limit)."
        )
    raise Exception(f"Edge-TTS falhou após 3 tentativas (voz={voice}): {msg}")


def edge_tts_generate(text, voice, output_path):
    text = _validate_tts_input(text)
    return _edge_with_retry(
        lambda: _edge_tts_generate(text, voice, output_path),
        voice=voice, output_path=output_path, label="basic"
    )


async def _edge_tts_advanced(text, voice, output_path, rate="+0%", pitch="+0Hz"):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(output_path)


def edge_tts_generate_advanced(text, voice, output_path, rate="+0%", pitch="+0Hz"):
    text = _validate_tts_input(text)
    return _edge_with_retry(
        lambda: _edge_tts_advanced(text, voice, output_path, rate, pitch),
        voice=voice, output_path=output_path, label=f"advanced(rate={rate},pitch={pitch})"
    )


# ─── F5-TTS — SOTA voice cloning (5s reference audio → cloned voice) ───────
_f5_model_cache = None

def f5_tts_generate(text: str, ref_audio: str, output_path: str,
                    ref_text: str = "", model: str = "F5TTS_v1_Base") -> str:
    """
    Clone a voice from a 5-30s reference audio file using F5-TTS.
    Produces dramatically more natural speech than Edge-TTS with the cloned voice.

    ref_audio: path to clean 5-30s WAV/MP3 of the target voice
    ref_text:  what the reference audio says (improves quality if provided;
               auto-transcribed via Whisper if empty)
    model:     F5TTS_v1_Base (default) or F5TTS_Base / E2TTS_Base
    """
    global _f5_model_cache
    from f5_tts.api import F5TTS

    if _f5_model_cache is None:
        print(f"  [F5-TTS] Loading model '{model}' (first call ~30s)...")
        _f5_model_cache = F5TTS(model=model)

    # F5-TTS requires reference text. If absent, auto-transcribe with Whisper.
    if not ref_text:
        try:
            print("  [F5-TTS] Auto-transcribing reference audio with Whisper...")
            ref_text = transcribe_to_srt(ref_audio, model_size="base")
            # Extract pure text (no timestamps) from SRT
            lines = []
            for ln in ref_text.split("\n"):
                ln = ln.strip()
                if ln and not ln.isdigit() and "-->" not in ln:
                    lines.append(ln)
            ref_text = " ".join(lines)
        except Exception as _te:
            print(f"  [F5-TTS] Auto-transcribe falhou ({_te}); usando ref_text vazio")
            ref_text = ""

    print(f"  [F5-TTS] Synthesizing {len(text)} chars from ref ({len(ref_text)} ref chars)")
    wav, sr, _ = _f5_model_cache.infer(
        ref_file=ref_audio,
        ref_text=ref_text,
        gen_text=text,
        file_wave=output_path,
        seed=None,
    )
    print(f"  [F5-TTS] Done -> {os.path.basename(output_path)} ({sr}Hz)")
    return output_path


def elevenlabs_generate(text, voice_id, api_key, output_path):
    """ElevenLabs TTS via REST API (no SDK — avoids Windows long path issues)."""
    import requests
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": api_key, "Content-Type": "application/json",
                     "Accept": "audio/mpeg"},
            json={"text": text, "model_id": "eleven_multilingual_v2",
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=120
        )
        if r.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(r.content)
            return
        raise Exception(f"ElevenLabs API error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        raise Exception(f"ElevenLabs error: {e}")

async def _get_edge_voices():
    import edge_tts
    return await edge_tts.list_voices()

def get_edge_voices():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_get_edge_voices())
    finally:
        loop.close()

# ============================================================================
# DURATION UTILITIES (FFprobe)
# ============================================================================
def _ffmpeg_path():
    project_root = os.path.dirname(BASE_DIR)
    bundled = os.path.join(project_root, "ffmpeg", "ffmpeg.exe")
    if os.path.isfile(bundled):
        return bundled
    return shutil.which("ffmpeg") or "ffmpeg"

def _ffprobe_path():
    ffmpeg = _ffmpeg_path()
    # Replace only the filename, not directory names
    dirname  = os.path.dirname(ffmpeg)
    basename = os.path.basename(ffmpeg)
    probe_name = basename.replace("ffmpeg", "ffprobe")
    probe = os.path.join(dirname, probe_name)
    if os.path.isfile(probe):
        return probe
    return shutil.which("ffprobe") or "ffprobe"

# Real-ESRGAN cached upsampler (lazy loaded — shared across HD encode and other features)
_realesrgan_cache = {"x2": None, "x4": None}

def _get_realesrgan(scale: int = 2):
    """Lazy-load Real-ESRGAN upsampler. Returns None on failure."""
    key = f"x{scale}"
    if _realesrgan_cache[key] is not None:
        return _realesrgan_cache[key]
    try:
        import torch as _t
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer
        if scale == 4:
            model_name = "RealESRGAN_x4plus.pth"
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                            num_block=23, num_grow_ch=32, scale=4)
        else:
            model_name = "RealESRGAN_x2plus.pth"
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                            num_block=23, num_grow_ch=32, scale=2)
        candidates = [
            os.path.join(MODELS_DIR, model_name),
            os.path.join(BASE_DIR, "venv311", "Lib", "site-packages", "gfpgan", "weights", model_name),
        ]
        path = next((p for p in candidates if os.path.exists(p)), None)
        if not path:
            print(f"  [Real-ESRGAN] {model_name} not found locally")
            return None
        upsampler = RealESRGANer(
            scale=scale, model_path=path, model=model,
            tile=512, tile_pad=10, pre_pad=0,
            half=_t.cuda.is_available()  # half precision on GPU
        )
        _realesrgan_cache[key] = upsampler
        print(f"  [Real-ESRGAN] Loaded x{scale} model from {path}")
        return upsampler
    except Exception as e:
        print(f"  [Real-ESRGAN] init failed: {e}")
        return None


def realesrgan_upscale_video(input_path: str, output_path: str,
                              scale: int = 2, job_id: str = None) -> bool:
    """
    AI-upscale a video using Real-ESRGAN x2 or x4.
    Returns True on success (output written). False = fallback needed.
    NOTE: Slow! ~0.5-1s per frame on RTX 4060. Use only for HD final pass.
    """
    upsampler = _get_realesrgan(scale)
    if upsampler is None:
        return False
    try:
        import tempfile as _tre
        import cv2 as _cvr
        import numpy as _npr
        ff  = _ffmpeg_path()
        ffp = _ffprobe_path()
        _tmp = _tre.mkdtemp(prefix="realesrgan_")
        try:
            cap = _cvr.VideoCapture(input_path)
            fps = cap.get(_cvr.CAP_PROP_FPS) or 25
            total = int(cap.get(_cvr.CAP_PROP_FRAME_COUNT))
            print(f"  [Real-ESRGAN] Upscaling x{scale}: {total} frames @ {fps:.0f}fps")
            frames_dir = os.path.join(_tmp, "frames")
            os.makedirs(frames_dir, exist_ok=True)
            idx = 0
            while True:
                ok, frame = cap.read()
                if not ok: break
                try:
                    output_img, _ = upsampler.enhance(frame, outscale=scale)
                except Exception as _fe:
                    print(f"  [Real-ESRGAN] frame {idx} failed ({_fe}) — using bicubic")
                    h, w = frame.shape[:2]
                    output_img = _cvr.resize(frame, (w*scale, h*scale),
                                             interpolation=_cvr.INTER_LANCZOS4)
                _cvr.imwrite(os.path.join(frames_dir, f"f_{idx:06d}.png"), output_img)
                idx += 1
                if job_id and idx % 10 == 0 and job_id in jobs:
                    pct = int(idx * 100 / max(total, 1))
                    jobs[job_id]["message"] = f"Real-ESRGAN x{scale}: {pct}% ({idx}/{total})"
            cap.release()
            # Reassemble video preserving original audio
            _v_only = os.path.join(_tmp, "v.mp4")
            subprocess.run([ff, "-y", "-framerate", str(int(fps)),
                            "-i", os.path.join(frames_dir, "f_%06d.png"),
                            "-c:v", "libx264", "-preset", "slow", "-crf", "16",
                            "-pix_fmt", "yuv420p", _v_only],
                           capture_output=True, timeout=max(600, total))
            subprocess.run([ff, "-y", "-i", _v_only, "-i", input_path,
                            "-map", "0:v", "-map", "1:a?", "-c:v", "copy", "-c:a", "copy",
                            output_path], capture_output=True, timeout=300)
            return os.path.exists(output_path) and os.path.getsize(output_path) > 10000
        finally:
            shutil.rmtree(_tmp, ignore_errors=True)
    except Exception as e:
        print(f"  [Real-ESRGAN] upscale failed: {e}")
        return False


def _ensure_max_dim(image_path: str, max_dim: int = 720, prefix: str = "rsz") -> str:
    """
    If image is larger than max_dim on either side, downscale to fit max_dim while preserving aspect.
    Returns path to resized image (in UPLOAD_DIR) or original path if no resize needed.
    Prevents downstream slowness in Wav2Lip/GFPGAN on high-res inputs.
    """
    try:
        import cv2 as _cvr, numpy as _npr
        raw = open(image_path, 'rb').read()
        im = _cvr.imdecode(_npr.frombuffer(raw, dtype=_npr.uint8), _cvr.IMREAD_COLOR)
        if im is None:
            return image_path
        h, w = im.shape[:2]
        if max(h, w) <= max_dim:
            return image_path
        sc = max_dim / max(h, w)
        new_w, new_h = int(w * sc), int(h * sc)
        # Ensure even dimensions (H.264 requirement)
        new_w -= new_w % 2
        new_h -= new_h % 2
        im = _cvr.resize(im, (new_w, new_h), interpolation=_cvr.INTER_LANCZOS4)
        out_path = os.path.join(UPLOAD_DIR, f"{prefix}_{uuid.uuid4().hex[:8]}.jpg")
        ok, enc = _cvr.imencode('.jpg', im, [_cvr.IMWRITE_JPEG_QUALITY, 95])
        if ok:
            with open(out_path, 'wb') as f: f.write(enc.tobytes())
            print(f"  [Resize] {w}x{h} -> {new_w}x{new_h} ({os.path.basename(out_path)})")
            return out_path
    except Exception as e:
        print(f"  [Resize] failed: {e}")
    return image_path


def get_media_duration(path: str) -> float:
    """Return duration in seconds via ffprobe. Handles non-ASCII paths on Windows."""
    import tempfile as _tempfile, shutil as _shutil2
    tmp_copy = None
    try:
        work_path = path
        try:
            path.encode('ascii')
        except UnicodeEncodeError:
            ext      = os.path.splitext(path)[1]
            tmp_copy = os.path.join(_tempfile.gettempdir(), f"apro_dur_{uuid.uuid4().hex[:8]}{ext}")
            _shutil2.copy2(path, tmp_copy)
            work_path = tmp_copy
        if not os.path.exists(work_path):
            return 0.0
        r   = subprocess.run(
            [_ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", work_path],
            capture_output=True, timeout=15
        )
        out = r.stdout.decode("utf-8", errors="replace").strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0
    finally:
        if tmp_copy and os.path.exists(tmp_copy):
            try: os.remove(tmp_copy)
            except: pass

def check_plan_limit(audio_path: str, plan: str) -> tuple:
    """Returns (ok: bool, duration: float, limit: float|None)."""
    limit = PLAN_LIMITS.get(plan, PLAN_LIMITS[DEFAULT_PLAN])
    if limit is None:
        dur = get_media_duration(audio_path)
        return True, dur, None
    dur = get_media_duration(audio_path)
    if dur > limit:
        return False, dur, limit
    return True, dur, limit

# ============================================================================
# SADTALKER
# ============================================================================
def check_sadtalker():
    st_path = os.path.join(MODELS_DIR, "SadTalker")
    ckpt    = os.path.join(st_path, "checkpoints")
    return (
        os.path.exists(os.path.join(st_path, "inference.py")) and
        os.path.exists(os.path.join(ckpt, "SadTalker_V0.0.2_256.safetensors"))
    )

def check_sadtalker_detailed():
    st_path = os.path.join(MODELS_DIR, "SadTalker")
    ckpt    = os.path.join(st_path, "checkpoints")
    return {
        "inference_py": os.path.exists(os.path.join(st_path, "inference.py")),
        "ckpt_256": os.path.exists(os.path.join(ckpt, "SadTalker_V0.0.2_256.safetensors")),
        "ckpt_512": os.path.exists(os.path.join(ckpt, "SadTalker_V0.0.2_512.safetensors")),
        "epoch_20": os.path.exists(os.path.join(ckpt, "epoch_20.pth")),
        "bfm":      os.path.exists(os.path.join(ckpt, "BFM_Fitting")),
        "shape":    os.path.exists(os.path.join(ckpt, "shape_predictor_68_face_landmarks.dat")),
    }

def validate_face_in_image(image_path: str) -> tuple:
    """
    Quick face detection check using OpenCV Haar cascade.
    Returns (ok: bool, message: str).
    Falls back to True (skip check) if cv2 or cascade not available.
    Handles non-ASCII paths by copying cascade to a temp ASCII dir.
    """
    try:
        import cv2 as _cv2
        import tempfile as _tf2

        # Copy cascade to ASCII temp path (OpenCV XML parser fails on non-ASCII paths)
        cascade_src = os.path.join(_cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        cascade_tmp = os.path.join(_tf2.gettempdir(), "haarcascade_frontalface_default.xml")
        if not os.path.exists(cascade_tmp):
            shutil.copy2(cascade_src, cascade_tmp)
        cascade = _cv2.CascadeClassifier(cascade_tmp)
        if cascade.empty():
            return True, "validation skipped (cascade load failed)"

        # Read image — also needs ASCII path on Windows
        img = _cv2.imread(image_path)
        if img is None:
            # Try via numpy for non-ASCII paths
            img = _cv2.imdecode(
                __import__('numpy').frombuffer(open(image_path, 'rb').read(), dtype=__import__('numpy').uint8),
                _cv2.IMREAD_COLOR
            )
        if img is None:
            return False, "Não foi possível ler a imagem. Verifique o formato (JPG/PNG)."

        # Downscale very large images for faster detection (keep aspect ratio)
        h, w = img.shape[:2]
        if max(h, w) > 1920:
            scale = 1920 / max(h, w)
            img = _cv2.resize(img, (int(w * scale), int(h * scale)))

        gray = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
        if len(faces) == 0:
            faces = cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=2, minSize=(30, 30))
        if len(faces) == 0:
            return False, (
                "Nenhum rosto detectado na imagem. "
                "Use uma foto com rosto frontal, boa iluminação e sem óculos escuros. "
                "Resolução mínima recomendada: 256×256 px."
            )
        return True, f"{len(faces)} face(s) detectada(s)"
    except Exception as ex:
        print(f"  [FaceValidation] skipped: {ex}")
        return True, "validation skipped"


def run_sadtalker(image_path, audio_path, output_path, settings=None):
    """Run SadTalker inference with the venv311 Python."""
    import tempfile
    st_path = os.path.join(MODELS_DIR, "SadTalker")
    if not check_sadtalker():
        raise Exception("SadTalker not installed. Check Settings.")

    settings   = settings or {}
    still_mode = settings.get("still", False)
    preprocess = settings.get("preprocess", "crop")
    size       = int(settings.get("size", 256))
    exp_scale  = float(settings.get("expression_scale", 1.0))

    # Validate GFPGAN model exists before using it — missing model causes 30min hang
    ckpt_dir = os.path.join(st_path, "checkpoints")
    gfpgan_ok = os.path.isfile(os.path.join(ckpt_dir, "GFPGANv1.4.pth"))
    requested_enhancer = settings.get("enhancer", "gfpgan")
    if requested_enhancer == "gfpgan" and not gfpgan_ok:
        print("  [SadTalker] GFPGANv1.4.pth not found — disabling enhancer to avoid hang")
        enhancer = "none"
    else:
        enhancer = requested_enhancer

    # SadTalker's cv2.imread() breaks on non-ASCII Windows paths (e.g. ç).
    # Copy all inputs to a clean ASCII temp directory before running.
    # Also resize image to max 1024px here — SadTalker face detector hangs on large images.
    tmp_root   = tempfile.mkdtemp(prefix="avatarpilot_")
    safe_img   = os.path.join(tmp_root, "src.jpg")   # always .jpg in temp
    safe_audio = os.path.join(tmp_root, "aud" + os.path.splitext(audio_path)[1])
    shutil.copy2(audio_path, safe_audio)

    try:
        import cv2 as _cv2s
        import numpy as _nps
        _raw = open(image_path, 'rb').read()
        _im  = _cv2s.imdecode(_nps.frombuffer(_raw, dtype=_nps.uint8), _cv2s.IMREAD_COLOR)
        _ih, _iw = _im.shape[:2]

        # ── Auto-crop to dominant face (helps with group photos / small faces) ──
        _cascade_tmp = os.path.join(__import__('tempfile').gettempdir(), "haarcascade_frontalface_default.xml")
        if not os.path.exists(_cascade_tmp):
            shutil.copy2(os.path.join(_cv2s.data.haarcascades, "haarcascade_frontalface_default.xml"), _cascade_tmp)
        _casc = _cv2s.CascadeClassifier(_cascade_tmp)
        _gray = _cv2s.cvtColor(_im, _cv2s.COLOR_BGR2GRAY)
        _faces = _casc.detectMultiScale(_gray, 1.1, 4, minSize=(30, 30))
        if len(_faces) == 0:
            _faces = _casc.detectMultiScale(_gray, 1.05, 2, minSize=(20, 20))
        if len(_faces) > 0:
            # Pick the largest face
            _fx, _fy, _fw, _fh = max(_faces, key=lambda f: f[2] * f[3])
            # Add generous padding (80%) so SadTalker has head+neck context
            _pad = int(max(_fw, _fh) * 0.8)
            _x1 = max(0, _fx - _pad)
            _y1 = max(0, _fy - _pad)
            _x2 = min(_iw, _fx + _fw + _pad)
            _y2 = min(_ih, _fy + _fh + _pad)
            _im = _im[_y1:_y2, _x1:_x2]
            print(f"  [SadTalker] Face crop: {_fw}×{_fh}px at ({_fx},{_fy}) → region {_x2-_x1}×{_y2-_y1}")

        # Ensure image is large enough for face detection (min 512px) and not too large (max 1024px)
        _ih2, _iw2 = _im.shape[:2]
        if max(_ih2, _iw2) < 512:
            _sc = 512 / max(_ih2, _iw2)
            _im = _cv2s.resize(_im, (int(_iw2 * _sc), int(_ih2 * _sc)), interpolation=_cv2s.INTER_LANCZOS4)
            print(f"  [SadTalker] Upscaled to {int(_iw2*_sc)}x{int(_ih2*_sc)} for detection")
        elif max(_ih2, _iw2) > 1024:
            _sc = 1024 / max(_ih2, _iw2)
            _im = _cv2s.resize(_im, (int(_iw2 * _sc), int(_ih2 * _sc)), interpolation=_cv2s.INTER_LANCZOS4)

        _ok, _enc = _cv2s.imencode('.jpg', _im, [_cv2s.IMWRITE_JPEG_QUALITY, 95])
        if _ok:
            with open(safe_img, 'wb') as _f: _f.write(_enc.tobytes())
        else:
            shutil.copy2(image_path, safe_img)
    except Exception as _ie:
        print(f"  [SadTalker] Image prep fallback: {_ie}")
        shutil.copy2(image_path, safe_img)

    image_path = safe_img
    audio_path = safe_audio

    result_dir = os.path.join(tmp_root, "result")
    os.makedirs(result_dir, exist_ok=True)

    def _build_cmd(preproc, enh):
        c = [
            sys.executable,
            os.path.join(st_path, "inference.py"),
            "--driven_audio", audio_path,
            "--source_image", image_path,
            "--result_dir", result_dir,
            "--checkpoint_dir", ckpt_dir,
            "--size", str(size),
            "--expression_scale", str(exp_scale),
            "--preprocess", preproc,
        ]
        if enh in ("gfpgan", "RestoreFormer"):
            c += ["--enhancer", enh]
        if still_mode:
            c.append("--still")
        return c

    cmd = _build_cmd(preprocess, enhancer)

    print(f"  [SadTalker] size={size}, preprocess={preprocess}, enhancer={enhancer}")
    # Scale timeout: audio_dur * processing_ratio + overhead. Min 300s for concurrent tolerance.
    _aud_dur = max(1.0, get_media_duration(audio_path))
    _base = int(_aud_dur * (4.0 if size >= 512 or enhancer not in ("none", "") else 2.5)) + 120
    # Floor 600s gives breathing room when GPU is warm from previous job (avoids cascade failures).
    # No upper cap — long audio legitimately needs time on RTX 4060.
    timeout_s = max(600, _base)

    def _run_cmd(run_cmd, tout=None):
        return subprocess.run(
            run_cmd, capture_output=True,
            encoding="utf-8", errors="replace",
            cwd=st_path, timeout=tout or timeout_s,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}
        )

    try:
        proc = _run_cmd(cmd)
        proc_stdout = proc.stdout or ""
        proc_stderr = proc.stderr or ""
    except subprocess.TimeoutExpired:
        # ── Timeout: retry without enhancer (GFPGAN can hang on some images) ──
        print(f"  [SadTalker] Timeout ({timeout_s}s) — retrying without enhancer...")
        cmd_no_enh = _build_cmd(preprocess, "none")
        try:
            _retry_tout = max(600, int(_aud_dur * 2.5) + 120)  # floor 600s for warm-GPU breathing room
            proc = _run_cmd(cmd_no_enh, tout=_retry_tout)
            proc_stdout = proc.stdout or ""
            proc_stderr = proc.stderr or ""
        except subprocess.TimeoutExpired:
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise Exception(f"SadTalker timeout even without enhancer. Tente uma imagem menor ou modo 'resize'.")

    if proc.returncode != 0:
        err = (proc_stderr or proc_stdout)[:1200]

        # ── Auto-retry: no face detected → fallback to resize ──
        no_face_signals = ("NO_FACE_DETECTED", "can not detect", "landmark", "No face", "detect_faces")
        if any(s in err for s in no_face_signals) and preprocess != "resize":
            print("  [SadTalker] No face detected — retrying with preprocess='resize'...")
            cmd_resize = _build_cmd("resize", enhancer)
            try:
                proc = _run_cmd(cmd_resize)
            except subprocess.TimeoutExpired:
                shutil.rmtree(tmp_root, ignore_errors=True)
                raise Exception("Rosto não detectado e timeout no modo resize. Use foto frontal com rosto visível.")
            proc_stdout = proc.stdout or ""
            proc_stderr = proc.stderr or ""
            if proc.returncode != 0:
                shutil.rmtree(tmp_root, ignore_errors=True)
                raise Exception(
                    "Rosto não detectado na imagem. Use uma foto com rosto frontal e bem iluminado. "
                    f"(Detalhe: {(proc_stderr or proc_stdout)[:300]})"
                )

        # ── Auto-retry: VRAM OOM → clear cache and retry ──
        elif "CUDA out of memory" in err or "OutOfMemoryError" in err:
            print("  [SadTalker] VRAM OOM — clearing cache and retrying...")
            try:
                import torch; torch.cuda.empty_cache()
            except Exception:
                pass
            try:
                proc = _run_cmd(cmd)
            except subprocess.TimeoutExpired:
                shutil.rmtree(tmp_root, ignore_errors=True)
                raise Exception("SadTalker OOM retry also timed out.")
            proc_stdout = proc.stdout or ""
            proc_stderr = proc.stderr or ""
            if proc.returncode != 0:
                shutil.rmtree(tmp_root, ignore_errors=True)
                raise Exception(f"SadTalker failed after OOM retry: {proc_stderr[:500]}")

        else:
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise Exception(f"SadTalker failed (code {proc.returncode}): {err}")

    # Find output video (deepest .mp4 inside result_dir)
    found = []
    for root, _, files in os.walk(result_dir):
        for f in files:
            if f.endswith(".mp4"):
                found.append(os.path.join(root, f))
    if not found:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise Exception("SadTalker produced no output video. Check GPU/CUDA.")

    shutil.copy2(max(found, key=os.path.getmtime), output_path)
    shutil.rmtree(tmp_root, ignore_errors=True)
    return output_path


def run_sadtalker_chunked(image_path, audio_path, output_path, settings=None,
                          chunk_duration=270, job_id=None):
    """
    Smart dispatcher: uses Cloud GPU (Replicate A100) if configured,
    otherwise uses local GPU with chunked processing for long audio.
    Cloud GPU: no VRAM limit, ~5x faster, supports 300 concurrent clients.
    """
    # ── Cloud GPU path ────────────────────────────────────────────────────────
    s        = load_settings()
    executor = s.get("executor", "local")

    if executor == "replicate" and s.get("replicate_key", ""):
        print("  [SadTalker] Using Cloud GPU (Replicate A100)")
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = "Using Cloud GPU (Replicate A100)..."
        return run_sadtalker_cloud_replicate(image_path, audio_path, output_path, settings, job_id)

    if executor == "huggingface":
        print("  [SadTalker] Using HuggingFace ZeroGPU (free A100)")
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = "Using HuggingFace Cloud GPU (free)..."
        return run_sadtalker_huggingface(image_path, audio_path, output_path, settings, job_id)

    # ── Local GPU: VRAM check ─────────────────────────────────────────────────
    if not vram_is_sufficient(min_gb=2.0):
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = "Waiting for VRAM to free up..."
        for _ in range(60):   # wait up to 5 min
            time.sleep(5)
            release_vram()
            if vram_is_sufficient(min_gb=2.0):
                break
        else:
            print("  [SadTalker] VRAM insufficient after 5min wait — proceeding anyway")

    total_dur = get_media_duration(audio_path)
    ffmpeg    = _ffmpeg_path()

    # Auto-disable GFPGAN for long audio (>3 min) — too many frames, would take hours
    settings = dict(settings or {})
    if total_dur > 180 and settings.get("enhancer") == "gfpgan":
        print(f"  [SadTalker] Audio {total_dur:.0f}s > 3min — disabling GFPGAN to avoid multi-hour processing")
        settings["enhancer"] = "none"
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"Long audio ({total_dur/60:.1f}min) — GFPGAN disabled for speed..."

    # Auto-disable GFPGAN if insufficient VRAM (needs ~2GB; OOM kills the process)
    if settings.get("enhancer") in ("gfpgan", "RestoreFormer"):
        try:
            import torch as _t
            if _t.cuda.is_available():
                _vf = _t.cuda.mem_get_info()[0] / (1024**3)
                if _vf < 2.5:
                    print(f"  [SadTalker] {_vf:.1f}GB VRAM free - disabling GFPGAN (OOM protection)", flush=True)
                    settings["enhancer"] = "none"
                    if job_id and job_id in jobs:
                        jobs[job_id]["message"] = f"GFPGAN desativado ({_vf:.1f}GB VRAM insuf.)"
        except Exception:
            pass

    # Short audio: direct processing
    if total_dur <= chunk_duration + 30:
        return run_sadtalker(image_path, audio_path, output_path, settings)

    # For very long audio, use smaller chunks so each finishes within timeout
    if total_dur > 600:  # > 10 min: use 120s chunks
        chunk_duration = 120
    elif total_dur > 300:  # > 5 min: use 180s chunks
        chunk_duration = 180

    n_chunks  = math.ceil(total_dur / chunk_duration)
    import tempfile as _st_ctmp
    chunk_dir = _st_ctmp.mkdtemp(prefix="st_chunks_")  # ASCII path avoids ç issue
    print(f"  [SadTalker] Chunked mode: {total_dur:.0f}s audio → {n_chunks} chunks of {chunk_duration}s")

    # Pre-process face image ONCE and reuse across all chunks.
    # This fixes "No face detected" errors on later chunks (face crop result cached).
    _cached_face_img = os.path.join(chunk_dir, "cached_face.jpg")
    try:
        import cv2 as _cvsad; import numpy as _npsad
        _raw = open(image_path, 'rb').read()
        _im  = _cvsad.imdecode(_npsad.frombuffer(_raw, dtype=_npsad.uint8), _cvsad.IMREAD_COLOR)
        _ih, _iw = _im.shape[:2]
        _cascade_tmp = os.path.join(__import__('tempfile').gettempdir(), "haarcascade_frontalface_default.xml")
        if not os.path.exists(_cascade_tmp):
            shutil.copy2(os.path.join(_cvsad.data.haarcascades, "haarcascade_frontalface_default.xml"), _cascade_tmp)
        _casc = _cvsad.CascadeClassifier(_cascade_tmp)
        _gray = _cvsad.cvtColor(_im, _cvsad.COLOR_BGR2GRAY)
        _faces = _casc.detectMultiScale(_gray, 1.1, 4, minSize=(30, 30))
        if len(_faces) == 0:
            _faces = _casc.detectMultiScale(_gray, 1.05, 2, minSize=(20, 20))
        if len(_faces) > 0:
            _fx, _fy, _fw, _fh = max(_faces, key=lambda f: f[2]*f[3])
            _pad = int(max(_fw, _fh) * 0.8)
            _x1 = max(0, _fx-_pad); _y1 = max(0, _fy-_pad)
            _x2 = min(_iw, _fx+_fw+_pad); _y2 = min(_ih, _fy+_fh+_pad)
            # If boundary clipped, shift opposite side to keep face centered
            _ov_r = max(0, (_fx + _fw + _pad) - _iw)
            if _ov_r > 0: _x1 = max(0, _x1 - _ov_r)
            _ov_b = max(0, (_fy + _fh + _pad) - _ih)
            if _ov_b > 0: _y1 = max(0, _y1 - _ov_b)
            _im = _im[_y1:_y2, _x1:_x2]
        # Ensure optimal size: 512–1024px
        _ih2, _iw2 = _im.shape[:2]
        if max(_ih2, _iw2) < 512:
            _sc = 512/max(_ih2, _iw2)
            _im = _cvsad.resize(_im, (int(_iw2*_sc), int(_ih2*_sc)), interpolation=_cvsad.INTER_LANCZOS4)
        elif max(_ih2, _iw2) > 1024:
            _sc = 1024/max(_ih2, _iw2)
            _im = _cvsad.resize(_im, (int(_iw2*_sc), int(_ih2*_sc)), interpolation=_cvsad.INTER_LANCZOS4)
        _ok, _enc = _cvsad.imencode('.jpg', _im, [_cvsad.IMWRITE_JPEG_QUALITY, 95])
        if _ok:
            with open(_cached_face_img, 'wb') as _f: _f.write(_enc.tobytes())
            print(f"  [SadTalker] Face pre-processed once → {_im.shape[1]}×{_im.shape[0]} (all chunks will reuse)")
    except Exception as _fce:
        print(f"  [SadTalker] Face pre-process failed ({_fce}) — using original for all chunks")
        shutil.copy2(image_path, _cached_face_img)

    _chunk_image = _cached_face_img if os.path.exists(_cached_face_img) else image_path

    chunk_videos = []
    _st_face_ok  = True   # se False, pula SadTalker em todos chunks restantes
    try:
        for i in range(n_chunks):
            start = i * chunk_duration
            dur   = min(chunk_duration, total_dur - start)
            if dur < 2.0:
                break

            # Progress: 35% → 68% distribuído pelos chunks
            if job_id and job_id in jobs:
                pct = 35 + int(33 * i / n_chunks)
                jobs[job_id]["progress"] = pct
                jobs[job_id]["message"]  = f"Lip sync chunk {i+1}/{n_chunks} ({start:.0f}s–{start+dur:.0f}s)..."

            # Extract audio chunk (WAV 16kHz mono para SadTalker/Wav2Lip)
            chunk_audio = os.path.join(chunk_dir, f"chunk_{i:03d}.wav")
            r = subprocess.run([
                ffmpeg, "-y", "-i", audio_path,
                "-ss", str(round(start, 3)), "-t", str(round(dur, 3)),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                chunk_audio
            ], capture_output=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(chunk_audio):
                raise Exception(f"Audio chunk {i} extraction failed")

            chunk_video = os.path.join(chunk_dir, f"chunk_{i:03d}.mp4")

            # Se SadTalker já falhou em chunk anterior → vai direto pro Wav2Lip (economiza 60s/chunk)
            if not _st_face_ok:
                print(f"  [SadTalker] Chunk {i+1}: pulando SadTalker (face não detectada antes) → Wav2Lip direto")
                run_wav2lip(_chunk_image, chunk_audio, chunk_video, {}, job_id)
            else:
                try:
                    run_sadtalker(_chunk_image, chunk_audio, chunk_video, settings)
                except Exception as _ce:
                    _err_str = str(_ce).lower()
                    # Se falhou por face não detectada → não tentar SadTalker nos chunks seguintes
                    if any(k in _err_str for k in ("rosto", "face", "detected", "landmark", "no_face")):
                        _st_face_ok = False
                        print(f"  [SadTalker] Chunk {i+1}: face não detectada — todos chunks restantes usarão Wav2Lip")
                    else:
                        print(f"  [SadTalker] Chunk {i+1} falhou ({_ce}) — fallback Wav2Lip para este chunk")
                    run_wav2lip(_chunk_image, chunk_audio, chunk_video, {}, job_id)

            if not os.path.exists(chunk_video) or os.path.getsize(chunk_video) < 5000:
                print(f"  [Chunk {i+1}] Sem output — pulando")
                continue
            chunk_videos.append(chunk_video)

            # Progress após chunk concluído: acima de 68% para ticker não sobrescrever
            if job_id and job_id in jobs:
                pct_done = 68 + int(12 * (i + 1) / n_chunks)  # 68→80% conforme chunks concluem
                jobs[job_id]["progress"] = pct_done
                jobs[job_id]["message"]  = f"Chunk {i+1}/{n_chunks} OK — aguardando próximo..."

            # Liberar VRAM entre chunks
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            print(f"  [SadTalker] Chunk {i+1}/{n_chunks} done — VRAM cleared")

        if not chunk_videos:
            raise Exception("No chunks were processed successfully")

        # Concatenate — ASCII temp dir to avoid non-ASCII path (ç) in OUTPUT_DIR
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"Concatenating {len(chunk_videos)} video chunks..."

        import tempfile as _sctmp
        _ascii_tmp = _sctmp.mkdtemp(prefix="st_concat_")
        try:
            _safe_chunks = []
            for ci, v in enumerate(chunk_videos):
                _safe_c = os.path.join(_ascii_tmp, f"chunk_{ci:03d}.mp4")
                shutil.copy2(v, _safe_c)
                _safe_chunks.append(_safe_c)

            concat_txt = os.path.join(_ascii_tmp, "concat.txt")
            with open(concat_txt, "w", encoding="utf-8") as f:
                for v in _safe_chunks:
                    f.write(f"file '{v.replace(os.sep, '/')}'\n")

            safe_out = os.path.join(_ascii_tmp, "merged.mp4")
            r = subprocess.run([
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", concat_txt,
                "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k",
                safe_out
            ], capture_output=True, timeout=600)

            if r.returncode != 0:
                err_msg = r.stderr.decode("utf-8", errors="replace")[:500] if isinstance(r.stderr, bytes) else (r.stderr or "")[:500]
                raise Exception(f"Concat failed: {err_msg}")

            shutil.copy2(safe_out, output_path)
        finally:
            shutil.rmtree(_ascii_tmp, ignore_errors=True)

        print(f"  [SadTalker] Concat done: {len(chunk_videos)} chunks → {output_path}")
        return output_path

    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)


# ============================================================================
# WAV2LIP — Primary lip-sync engine (replaces SadTalker for reliability)
# ============================================================================
def check_wav2lip() -> bool:
    """Check if Wav2Lip model and repo are available."""
    w2l_dir  = os.path.join(MODELS_DIR, "Wav2Lip")
    ckpt     = os.path.join(w2l_dir, "checkpoints", "wav2lip_gan.pth")
    script   = os.path.join(w2l_dir, "win_inference.py")
    return os.path.isdir(w2l_dir) and os.path.isfile(ckpt) and os.path.isfile(script)


def _is_video_file(path: str) -> bool:
    """Return True if path is a video file (not an image)."""
    return os.path.splitext(path)[1].lower() in {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}


def _make_seamless_loop_source(video_path: str, out_path: str, crossfade: float = 0.5) -> str:
    """
    Cria uma versão seamless do vídeo que pode ser loop-ado sem corte visível.
    O final do vídeo é dissolvido (crossfade) com o início, eliminando o salto.
    crossfade: duração em segundos do dissolve (0.3–0.8s funciona bem).
    """
    ff   = _ffmpeg_path()
    ffp  = _ffprobe_path()
    vdur = float(subprocess.run(
        [ffp, "-v","error","-show_entries","format=duration",
         "-of","default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, timeout=15).stdout.decode().strip() or "0")

    if vdur <= crossfade * 2 + 0.5:
        # Vídeo muito curto para crossfade — retorna como está
        shutil.copy2(video_path, out_path)
        return out_path

    # Crossfade: blend último crossfade_s com o primeiro crossfade_s
    # [main] = 0 até (dur - crossfade)
    # [end]  = (dur - crossfade) até dur
    # [start] = 0 até crossfade
    # blend: [end][start] → resultado = A*(1-t/xf) + B*(t/xf)
    # concat [main][blend]
    xf   = crossfade
    main_end = vdur - xf

    filter_complex = (
        f"[0:v]trim=0:{main_end:.3f},setpts=PTS-STARTPTS[main];"
        f"[0:v]trim={main_end:.3f}:{vdur:.3f},setpts=PTS-STARTPTS[endpart];"
        f"[0:v]trim=0:{xf:.3f},setpts=PTS-STARTPTS[startpart];"
        f"[endpart][startpart]blend=all_expr='A*(1-T/{xf:.3f})+B*(T/{xf:.3f})'[blended];"
        f"[main][blended]concat=n=2:v=1:a=0[out]"
    )

    r = subprocess.run(
        [ff, "-y", "-i", video_path,
         "-filter_complex", filter_complex,
         "-map", "[out]",
         "-c:v", "libx264", "-preset", "slow", "-crf", "17",
         "-pix_fmt", "yuv420p", out_path],
        capture_output=True, timeout=max(300, int(vdur * 5))
    )

    if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
        print(f"  [VideoLoop] Seamless source criado: {vdur:.1f}s com {xf}s crossfade")
        return out_path

    # Fallback: retorna original se o filtro falhar
    print(f"  [VideoLoop] Crossfade filter falhou — usando original")
    shutil.copy2(video_path, out_path)
    return out_path


def _loop_video_to_duration(video_path: str, audio_path: str, out_path: str) -> str:
    """
    Loga o vídeo de forma SEAMLESS para cobrir a duração do áudio.
    Usa crossfade no ponto de loop para eliminar saltos visuais.
    Retorna out_path (vídeo sem áudio, 1280p máx).
    """
    import tempfile as _tlvt
    ff    = _ffmpeg_path()
    ffp   = _ffprobe_path()
    vdur  = float(subprocess.run(
        [ffp, "-v","error","-show_entries","format=duration",
         "-of","default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, timeout=15).stdout.decode().strip() or "30")
    adur  = _get_duration_safe(audio_path)

    # Get original resolution
    _pr   = subprocess.run([ffp, "-v","error","-select_streams","v:0",
                            "-show_entries","stream=width,height","-of","csv=p=0", video_path],
                           capture_output=True, timeout=10)
    _dims = _pr.stdout.decode().strip().split(",")
    orig_w, orig_h = (int(_dims[0]), int(_dims[1])) if len(_dims) == 2 else (1280, 720)

    # Aumentado de 960→1280px — mais detalhe corporal preservado
    _target = 1280
    if max(orig_w, orig_h) > _target:
        _sc   = _target / max(orig_w, orig_h)
        out_w = int(orig_w * _sc) & ~1
        out_h = int(orig_h * _sc) & ~1
        vf    = f"scale={out_w}:{out_h}"
    else:
        out_w, out_h = orig_w, orig_h
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"

    loops = max(0, math.ceil(adur / max(vdur, 0.1)))
    print(f"  [VideoLoop] {vdur:.1f}s × {loops}x → {adur:.1f}s | {out_w}×{out_h} (seamless crossfade)")

    _loop_tmp = _tlvt.mkdtemp(prefix="vloop_")
    try:
        # 1. Escalar vídeo fonte para resolução alvo
        scaled_src = os.path.join(_loop_tmp, "scaled.mp4")
        subprocess.run([ff, "-y", "-i", video_path, "-an", "-vf", vf,
                        "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                        "-pix_fmt", "yuv420p", scaled_src],
                       capture_output=True, timeout=max(300, int(vdur * 4)))

        if not os.path.exists(scaled_src) or os.path.getsize(scaled_src) < 1000:
            scaled_src = video_path  # fallback

        # 2. Criar versão seamless do vídeo fonte
        seamless_src = os.path.join(_loop_tmp, "seamless.mp4")
        _make_seamless_loop_source(scaled_src, seamless_src, crossfade=0.5)

        # 3. Stream-loop o vídeo seamless e cortar no tamanho do áudio
        r = subprocess.run([
            ff, "-y",
            "-stream_loop", str(loops + 1),
            "-i", seamless_src,
            "-t", str(adur),
            "-an",
            "-c:v", "libx264", "-preset", "slow", "-crf", "17",
            "-pix_fmt", "yuv420p",
            out_path
        ], capture_output=True, timeout=max(600, int(adur * 4)))

        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            return out_path

        # Fallback: loop simples sem crossfade
        print("  [VideoLoop] Fallback: stream_loop simples")
        r2 = subprocess.run([
            ff, "-y", "-stream_loop", str(loops), "-i", video_path,
            "-t", str(adur), "-an", "-vf", vf,
            "-c:v", "libx264", "-preset", "slow", "-crf", "16",
            "-pix_fmt", "yuv420p", out_path
        ], capture_output=True, timeout=max(600, int(adur * 4)))

        if not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
            err = (r2.stderr or b"").decode("utf-8", errors="replace")[-300:]
            raise Exception(f"Video loop failed: {err}")
        return out_path

    finally:
        shutil.rmtree(_loop_tmp, ignore_errors=True)


MUSETALK_DIR = os.path.join(MODELS_DIR, "MuseTalk")

def check_face_swap_ready() -> bool:
    """Returns True if InsightFace + inswapper_128.onnx are available locally."""
    try:
        import insightface  # noqa: F401
    except ImportError:
        return False
    candidates = [
        os.path.join(MODELS_DIR, "inswapper_128.onnx"),
        os.path.join(MODELS_DIR, "SadTalker", "inswapper_128.onnx"),
    ]
    return any(os.path.exists(p) for p in candidates)


def check_musetalk() -> bool:
    """Check if MuseTalk 1.5 is installed and core models are present."""
    unet    = os.path.join(MUSETALK_DIR, "models", "musetalkV15", "unet.pth")
    vae_cfg = os.path.join(MUSETALK_DIR, "models", "sd-vae", "config.json")
    vae_bin = os.path.join(MUSETALK_DIR, "models", "sd-vae", "diffusion_pytorch_model.bin")
    script  = os.path.join(MUSETALK_DIR, "scripts", "inference.py")
    venv_py = os.path.join(MUSETALK_DIR, "venv", "Scripts", "python.exe")
    return all(os.path.exists(p) for p in [unet, vae_cfg, vae_bin, script, venv_py])


def run_musetalk(image_path: str, audio_path: str, output_path: str,
                 settings: dict = None, job_id: str = None) -> str:
    """
    Run MuseTalk 1.5 lip sync — latent diffusion approach, no crop/composite artifacts.
    Produces HeyGen-quality results on static images.
    All I/O uses ASCII temp paths to avoid Windows ç issues.
    """
    import tempfile as _tmpmod
    import yaml as _yaml

    settings = settings or {}
    venv_py = os.path.join(MUSETALK_DIR, "venv", "Scripts", "python.exe")
    script  = os.path.join(MUSETALK_DIR, "scripts", "inference.py")

    if not check_musetalk():
        raise Exception("MuseTalk not ready. Models missing — run download_weights.bat.")

    # GUARD: mesma proteção do run_musetalk_chunked — MuseTalk trava em 8GB VRAM com
    # áudio longo. Acima do limite, lança p/ acionar o fallback Wav2Lip do chamador.
    _mst_dur = _get_duration_safe(audio_path)
    _MUSETALK_MAX_DUR = float(os.environ.get("AVP_MUSETALK_MAX_DUR", "130"))
    if _mst_dur > _MUSETALK_MAX_DUR:
        raise Exception(
            f"MuseTalk pulado: áudio {_mst_dur:.0f}s > {_MUSETALK_MAX_DUR:.0f}s "
            f"(limite seguro p/ VRAM) — usar Wav2Lip")

    _vram_free = _free_vram_gb()
    if _vram_free < VRAM_MIN_GB:
        raise Exception(f"VRAM insuficiente para MuseTalk: {_vram_free:.1f}GB livres (mínimo {VRAM_MIN_GB}GB). Feche outros programas que usam GPU.")

    tmp = _tmpmod.mkdtemp(prefix="mst_avp_")
    try:
        # Copy inputs to ASCII paths
        ext_img = os.path.splitext(image_path)[1] or ".png"
        ext_aud = os.path.splitext(audio_path)[1] or ".wav"
        safe_img = os.path.join(tmp, "face" + ext_img)
        safe_aud = os.path.join(tmp, "audio" + ext_aud)
        safe_out = os.path.join(tmp, "result.mp4")
        shutil.copy2(image_path, safe_img)
        shutil.copy2(audio_path, safe_aud)

        # Build inference YAML config
        config_path = os.path.join(tmp, "config.yaml")
        result_dir  = os.path.join(tmp, "results")
        os.makedirs(result_dir, exist_ok=True)

        cfg = {
            "task_001": {
                "video_path": safe_img.replace("\\", "/"),
                "audio_path": safe_aud.replace("\\", "/"),
                "result_name": "result.mp4",
            }
        }
        with open(config_path, "w") as _f:
            _yaml.dump(cfg, _f)

        # Model paths
        unet_path   = os.path.join(MUSETALK_DIR, "models", "musetalkV15", "unet.pth")
        unet_cfg    = os.path.join(MUSETALK_DIR, "models", "musetalk", "musetalk.json")
        vae_type    = "sd-vae"
        whisper_local = os.path.join(MUSETALK_DIR, "models", "whisper")
        # Fall back to HuggingFace ID if local whisper not yet downloaded
        whisper_dir = whisper_local if os.path.exists(os.path.join(whisper_local, "config.json")) else "openai/whisper-tiny"
        batch_size  = settings.get("batch_size", 8)

        cmd = [
            venv_py, script,
            "--inference_config", config_path,
            "--unet_model_path",  unet_path,
            "--unet_config",      unet_cfg,
            "--vae_type",         vae_type,
            "--whisper_dir",      whisper_dir,
            "--result_dir",       result_dir,
            "--version",          "v15",
            "--batch_size",       str(batch_size),
            "--use_float16",
        ]

        if job_id:
            jobs[job_id]["message"] = "MuseTalk: sincronizando lábios (difusão latente)..."

        print(f"  [MuseTalk] Starting inference on {os.path.basename(image_path)}")
        # MuseTalk on RTX 4060: ~4x audio for static image, can be 8-10x on video face (long videos).
        # Be VERY generous — fallback to Wav2Lip produces visibly worse quality (240p look)
        # so it's better to wait for MuseTalk to finish than to time out and fallback.
        timeout = max(2400, int(_get_duration_safe(audio_path) * 10) + 600)
        env = os.environ.copy()
        env["PYTHONPATH"] = MUSETALK_DIR + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_DATASETS_OFFLINE"] = "1"
        env["TORCH_COMPILE_DISABLE"] = "1"
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=MUSETALK_DIR,
            env=env,
        )

        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        if stdout: print("  [MuseTalk stdout]", stdout[-800:])
        if stderr: print("  [MuseTalk stderr]", stderr[-800:])

        # MuseTalk writes to results/{version}/result.mp4
        mt_out = os.path.join(result_dir, "v15", "result.mp4")
        if not os.path.exists(mt_out) or os.path.getsize(mt_out) < 100_000:
            raise Exception(f"MuseTalk output missing or too small (rc={result.returncode}). stderr: {stderr[-400:]}")

        shutil.copy2(mt_out, output_path)
        print(f"  [MuseTalk] Done → {os.path.getsize(output_path)//1024}KB")
        return output_path

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_musetalk_chunked(image_path: str, audio_path: str, output_path: str,
                         settings: dict = None, chunk_duration: float = 300,
                         job_id: str = None) -> str:
    """
    Run MuseTalk on long audio in chunks, then concatenate.
    Each chunk is at most chunk_duration seconds.
    """
    import tempfile as _tmpmod

    dur = _get_duration_safe(audio_path)
    # GUARD CENTRAL: MuseTalk (difusão latente) TRAVA/OOM em 8GB VRAM com áudio longo.
    # Acima de ~130s ele estagna sem progresso e o watchdog mata o job após 60min
    # (visto no teste M3.4 de 180s). Lança aqui p/ que o fallback Wav2Lip de CADA
    # call site dispare automaticamente — cobre todos os caminhos do pipeline.
    _MUSETALK_MAX_DUR = float(os.environ.get("AVP_MUSETALK_MAX_DUR", "130"))
    if dur > _MUSETALK_MAX_DUR:
        raise Exception(
            f"MuseTalk pulado: áudio {dur:.0f}s > {_MUSETALK_MAX_DUR:.0f}s "
            f"(limite seguro p/ VRAM) — usar Wav2Lip")
    if dur <= chunk_duration:
        return run_musetalk(image_path, audio_path, output_path, settings, job_id)

    ffmpeg = _ffmpeg_path()
    tmp = _tmpmod.mkdtemp(prefix="mst_chunk_")
    # If face source is a video, chunk it too so each audio segment uses the matching
    # video segment (otherwise every chunk would reuse the same first N seconds of motion).
    _is_video_face = _is_video_file(image_path)
    try:
        n_chunks = math.ceil(dur / chunk_duration)
        chunk_videos = []
        _prog_start = jobs[job_id].get("progress", 35) if job_id and jobs.get(job_id) else 35
        _prog_end   = 70

        for i in range(n_chunks):
            start = i * chunk_duration
            length = min(chunk_duration, dur - start)
            chunk_aud = os.path.join(tmp, f"aud_{i:03d}.wav")
            chunk_vid = os.path.join(tmp, f"vid_{i:03d}.mp4")

            # Extract audio chunk
            _aud_r = subprocess.run([
                ffmpeg, "-y", "-i", audio_path,
                "-ss", str(round(start, 3)), "-t", str(round(length, 3)),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", chunk_aud
            ], capture_output=True, timeout=60)
            if _aud_r.returncode != 0 or not os.path.exists(chunk_aud):
                raise Exception(f"ffmpeg: extração de chunk de áudio {i+1}/{n_chunks} falhou (rc={_aud_r.returncode})")

            # If face source is a video, extract matching video segment for this chunk
            chunk_face = image_path
            if _is_video_face:
                chunk_face = os.path.join(tmp, f"face_{i:03d}.mp4")
                _fac_r = subprocess.run([
                    ffmpeg, "-y", "-i", image_path,
                    "-ss", str(round(start, 3)), "-t", str(round(length, 3)),
                    "-c:v", "copy", "-an", chunk_face
                ], capture_output=True, timeout=180)
                if _fac_r.returncode != 0 or not os.path.exists(chunk_face):
                    # Fallback to full video if segment extraction fails
                    print(f"  [MuseTalk] Video chunk extract falhou — usando vídeo completo")
                    chunk_face = image_path

            if job_id and jobs.get(job_id):
                jobs[job_id]["message"]  = f"MuseTalk chunk {i+1}/{n_chunks} ({start:.0f}s–{start+length:.0f}s)..."
                jobs[job_id]["progress"] = int(_prog_start + (_prog_end - _prog_start) * i / n_chunks)
            print(f"  [MuseTalk] Chunk {i+1}/{n_chunks} ({start:.0f}s–{start+length:.0f}s)")

            run_musetalk(chunk_face, chunk_aud, chunk_vid, settings, job_id=None)
            chunk_videos.append(chunk_vid)
            if job_id and jobs.get(job_id):
                jobs[job_id]["progress"] = int(_prog_start + (_prog_end - _prog_start) * (i + 1) / n_chunks)

        # Concatenate all chunks
        concat_list = os.path.join(tmp, "concat.txt")
        with open(concat_list, "w") as f:
            for v in chunk_videos:
                f.write(f"file '{v}'\n")

        _concat_r = subprocess.run([
            ffmpeg, "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-c", "copy", output_path
        ], capture_output=True, timeout=max(300, n_chunks * 20))
        if _concat_r.returncode != 0:
            _cerr = (_concat_r.stderr or b"").decode("utf-8", errors="replace")[-300:]
            raise Exception(f"MuseTalk chunked concat falhou (rc={_concat_r.returncode}): {_cerr}")

        if not os.path.exists(output_path) or os.path.getsize(output_path) < 100_000:
            raise Exception("MuseTalk chunked concat failed")

        print(f"  [MuseTalk] Chunked done: {n_chunks} chunks → {os.path.getsize(output_path)//1024}KB")
        return output_path

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_wav2lip(image_path: str, audio_path: str, output_path: str,
                settings: dict = None, job_id: str = None) -> str:
    """
    Run Wav2Lip lip sync.
    • Static image input: repeats the same frame, only mouth moves (fast)
    • Video input: PRESERVES all body/hand movement, only mouth is replaced (HeyGen-style)
      If the video is shorter than the audio, it is looped seamlessly.
    All paths go through an ASCII temp dir to avoid Windows ç path issues.
    """
    import tempfile as _tmpmod
    settings  = settings or {}
    w2l_dir   = os.path.join(MODELS_DIR, "Wav2Lip")
    ckpt_path = os.path.join(w2l_dir, "checkpoints", "wav2lip_gan.pth")
    script    = os.path.join(w2l_dir, "win_inference.py")
    ffmpeg_bin = _ffprobe_path().replace("ffprobe", "ffmpeg")

    if not check_wav2lip():
        raise Exception("Wav2Lip not installed. Run setup or check models/Wav2Lip/.")

    tmp_root   = _tmpmod.mkdtemp(prefix="w2l_avp_")
    safe_audio = os.path.join(tmp_root, "aud" + os.path.splitext(audio_path)[1])
    safe_out   = os.path.join(tmp_root, "out.mp4")

    try:
        shutil.copy2(audio_path, safe_audio)
        is_video_input = _is_video_file(image_path)
        _crop_applied = False
        _orig_w = _orig_h = 0
        _crop_x1 = _crop_y1 = _crop_x2 = _crop_y2 = 0

        if is_video_input:
            # ── VIDEO AVATAR MODE — HeyGen-style ────────────────────────────
            # Preserve all body/hand movement from the source video.
            # Loop if shorter than audio, then only replace mouth with Wav2Lip.
            audio_dur = _get_duration_safe(audio_path)
            vid_dur   = float(subprocess.run(
                [_ffprobe_path(), "-v","error","-show_entries","format=duration",
                 "-of","default=noprint_wrappers=1:nokey=1", image_path],
                capture_output=True, timeout=15).stdout.decode().strip() or "30")

            if vid_dur < audio_dur - 0.5:
                # Loop video to match audio length
                print(f"  [Wav2Lip] VIDEO input {vid_dur:.1f}s < audio {audio_dur:.1f}s → looping")
                if job_id and job_id in jobs:
                    jobs[job_id]["message"] = f"Preparando vídeo-avatar (loop {math.ceil(audio_dur/vid_dur)}x)..."
                looped_vid = os.path.join(tmp_root, "face_looped.mp4")
                _loop_video_to_duration(image_path, audio_path, looped_vid)
                safe_face = looped_vid
            else:
                # Video is long enough — just copy to ASCII path (scale to 960px max)
                ff  = _ffmpeg_path()
                ffp = _ffprobe_path()
                _pr = subprocess.run([ffp, "-v","error","-select_streams","v:0",
                                      "-show_entries","stream=width,height","-of","csv=p=0", image_path],
                                     capture_output=True, timeout=10)
                _dims = _pr.stdout.decode().strip().split(",")
                orig_w, orig_h = (int(_dims[0]), int(_dims[1])) if len(_dims) == 2 else (1280, 720)
                _target = 1280  # aumentado 960→1280 para mais detalhe corporal
                if max(orig_w, orig_h) > _target:
                    _sc  = _target / max(orig_w, orig_h)
                    out_w = int(orig_w * _sc) & ~1
                    out_h = int(orig_h * _sc) & ~1
                    vf = f"scale={out_w}:{out_h}"
                else:
                    vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
                safe_face = os.path.join(tmp_root, "face.mp4")
                subprocess.run([ff, "-y", "-i", image_path, "-an", "-vf", vf,
                                "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                                "-pix_fmt", "yuv420p", safe_face],
                               capture_output=True, timeout=max(600, int(vid_dur * 3)))
            print(f"  [Wav2Lip] VIDEO mode — body/hand movement preserved from source")
        else:
            # ── STATIC IMAGE MODE ────────────────────────────────────────────
            # Face-crop and scale to optimal size for Wav2Lip quality.
            safe_face = os.path.join(tmp_root, "face" + os.path.splitext(image_path)[1])
            import cv2 as _cv2w
            import numpy as _npw
            _raw = open(image_path, 'rb').read()
            _im  = _cv2w.imdecode(_npw.frombuffer(_raw, _npw.uint8), _cv2w.IMREAD_COLOR)
            if _im is None:
                shutil.copy2(image_path, safe_face)
            else:
                _ih, _iw = _im.shape[:2]
                _orig_w, _orig_h = _iw, _ih
                import tempfile as _tf2
                _casc_src = os.path.join(_cv2w.data.haarcascades, "haarcascade_frontalface_default.xml")
                _casc_tmp = os.path.join(_tf2.gettempdir(), "haarcascade_frontalface_default.xml")
                if not os.path.exists(_casc_tmp):
                    shutil.copy2(_casc_src, _casc_tmp)
                _casc  = _cv2w.CascadeClassifier(_casc_tmp)
                _gray  = _cv2w.cvtColor(_im, _cv2w.COLOR_BGR2GRAY)
                _dets  = _casc.detectMultiScale(_gray, 1.1, 4, minSize=(30, 30))
                if len(_dets) == 0:
                    _dets = _casc.detectMultiScale(_gray, 1.05, 2, minSize=(20, 20))
                if len(_dets) > 0:
                    _fx, _fy, _fw, _fh = max(_dets, key=lambda d: d[2]*d[3])
                    _face_pct = _fw / _iw
                    if _face_pct < 0.42:
                        # HeyGen-style: centered crop so face is ~28% of width.
                        # Large crop (wider context) = seam at edges, less visible.
                        # After Wav2Lip, composite crop back into full original frame.
                        _cx = _fx + _fw // 2
                        _cy = _fy + _fh // 2
                        _cw = int(_fw / 0.28)           # face fills 28% of crop width
                        # If 28% ratio exceeds image width (face 28-42%), zoom in with 50% ratio
                        if _cw >= _iw:
                            _cw = int(_fw / 0.50)
                        _ch = int(_cw * 1.3)            # portrait aspect
                        _x1 = _cx - _cw // 2
                        _y1 = _cy - int(_ch * 0.38)    # face at 38% from top
                        _x2 = _x1 + _cw
                        _y2 = _y1 + _ch
                        # Clamp & shift to stay within image boundaries
                        if _x2 > _iw: _x1 -= (_x2 - _iw); _x2 = _iw
                        if _x1 < 0:   _x2 = min(_iw, _x2 - _x1); _x1 = 0
                        if _y2 > _ih: _y1 -= (_y2 - _ih); _y2 = _ih
                        if _y1 < 0:   _y2 = min(_ih, _y2 - _y1); _y1 = 0
                        # Only apply crop if we actually reduced the image
                        if (_x2 - _x1) < _iw or (_y2 - _y1) < _ih:
                            _crop_x1, _crop_y1, _crop_x2, _crop_y2 = _x1, _y1, _x2, _y2
                            _im = _im[_y1:_y2, _x1:_x2]
                            _crop_applied = True
                            _new_pct = _fw / max(_x2 - _x1, 1) * 100
                            print(f"  [Wav2Lip] Face {_face_pct*100:.0f}% → centered crop {_x2-_x1}×{_y2-_y1} (face {_new_pct:.0f}%) → composite back")
                        else:
                            print(f"  [Wav2Lip] Face {_face_pct*100:.0f}% → crop = full image, no crop needed")
                    else:
                        print(f"  [Wav2Lip] Face OK ({_face_pct*100:.0f}%) — keeping {_iw}×{_ih}")
                else:
                    print(f"  [Wav2Lip] No face detected → using original {_iw}×{_ih}")
                _ih2, _iw2 = _im.shape[:2]
                # Scale to 400–720px: upscale if tiny, downscale if huge
                _max_dim = max(_ih2, _iw2)
                if _max_dim < 400:
                    _sc = 400 / _max_dim
                    _nw = int(_iw2 * _sc) & ~1
                    _nh = int(_ih2 * _sc) & ~1
                    _im = _cv2w.resize(_im, (_nw, _nh), interpolation=_cv2w.INTER_LANCZOS4)
                    print(f"  [Wav2Lip] Upscaled to {_nw}×{_nh}")
                elif _max_dim > 720:
                    _sc = 720 / _max_dim
                    _nw = int(_iw2 * _sc) & ~1
                    _nh = int(_ih2 * _sc) & ~1
                    _im = _cv2w.resize(_im, (_nw, _nh), interpolation=_cv2w.INTER_LANCZOS4)
                    print(f"  [Wav2Lip] Scaled to {_nw}×{_nh}")
                else:
                    # Ensure even dimensions even without resize
                    _ih2e, _iw2e = _im.shape[:2]
                    if _iw2e % 2 or _ih2e % 2:
                        _im = _im[:_ih2e & ~1, :_iw2e & ~1]
                _ok, _enc = _cv2w.imencode('.jpg', _im, [_cv2w.IMWRITE_JPEG_QUALITY, 98])
                if _ok:
                    with open(safe_face, 'wb') as _f: _f.write(_enc.tobytes())
                else:
                    shutil.copy2(image_path, safe_face)

        # ── Run Wav2Lip ───────────────────────────────────────────────────
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"Wav2Lip: sincronizando lábios {'(vídeo)' if is_video_input else '(imagem)'}..."

        audio_dur_s = _get_duration_safe(audio_path)
        timeout_s   = max(3600, int(audio_dur_s * 10))

        def _w2l_run(pads_top=0, pads_bottom=15, pads_left=0, pads_right=0, resize_factor=1):
            _cmd = [
                sys.executable, script,
                "--checkpoint_path", ckpt_path,
                "--face",            safe_face,
                "--audio",           safe_audio,
                "--outfile",         safe_out,
                "--fps",             "25",
                "--pads",            str(pads_top), str(pads_bottom), str(pads_left), str(pads_right),
                "--face_det_batch",  "4" if is_video_input else "8",
                "--wav2lip_batch",   "64" if is_video_input else "128",
                "--resize_factor",   str(resize_factor),
                "--ffmpeg",          ffmpeg_bin,
            ]
            print(f"  [Wav2Lip] Starting {'VIDEO' if is_video_input else 'IMAGE'} inference "
                  f"(pads={pads_top}/{pads_bottom}/{pads_left}/{pads_right}, resize={resize_factor})")
            return subprocess.run(
                _cmd, capture_output=True,
                encoding="utf-8", errors="replace",
                cwd=w2l_dir, timeout=timeout_s,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"}
            )

        # pads_bottom=15 captura mais região do queixo → melhor sync labial
        proc = _w2l_run(pads_bottom=15, resize_factor=1)

        # Retry automático se falhar por face não detectada
        if proc.returncode != 0:
            err1 = (proc.stderr or proc.stdout or "")
            _face_errors = ("No face detected", "face not detected", "Face not found",
                            "no faces", "Couldn't detect faces")
            if any(s.lower() in err1.lower() for s in _face_errors):
                print("  [Wav2Lip] Face não detectada — retry com resize_factor=2 (zoom-in)")
                if os.path.exists(safe_out): os.remove(safe_out)
                proc = _w2l_run(pads_bottom=15, resize_factor=2)
            if proc.returncode != 0:
                # Último recurso: pads maiores para face parcialmente visível
                err2 = (proc.stderr or proc.stdout or "")
                if any(s.lower() in err2.lower() for s in _face_errors):
                    print("  [Wav2Lip] Retry 2: pads=20, resize=1")
                    if os.path.exists(safe_out): os.remove(safe_out)
                    proc = _w2l_run(pads_bottom=20, resize_factor=1)

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "")[:2000]
            raise Exception(f"Wav2Lip failed (code {proc.returncode}): {err}")

        if not os.path.isfile(safe_out):
            raise Exception("Wav2Lip produced no output video.")

        # ── HeyGen-style composite: paste lip-synced crop back into full frame ──
        if not is_video_input and _crop_applied and _orig_w > 0:
            _comp_out = os.path.join(tmp_root, "composited.mp4")
            _ff2 = _ffmpeg_path()
            _crop_w = (_crop_x2 - _crop_x1) & ~1
            _crop_h = (_crop_y2 - _crop_y1) & ~1
            _bg_w   = _orig_w & ~1
            _bg_h   = _orig_h & ~1
            # Ensure background image is in ASCII-safe path (Windows/FFmpeg issue with ç)
            _bg_src = image_path
            if any(ord(c) > 127 for c in image_path):
                _bg_ext = os.path.splitext(image_path)[1] or ".jpg"
                _bg_src = os.path.join(tmp_root, f"bg_frame{_bg_ext}")
                shutil.copy2(image_path, _bg_src)
            _comp_r = subprocess.run([
                _ff2, "-y",
                "-loop", "1", "-framerate", "25", "-i", _bg_src,
                "-i", safe_out,
                "-filter_complex",
                f"[0:v]scale={_bg_w}:{_bg_h}:flags=lanczos[bg];"
                f"[1:v]scale={_crop_w}:{_crop_h}:flags=lanczos[fg];"
                f"[bg][fg]overlay={_crop_x1}:{_crop_y1}:shortest=1[out]",
                "-map", "[out]", "-map", "1:a",
                "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                "-pix_fmt", "yuv420p", "-t", str(audio_dur_s),
                _comp_out
            ], capture_output=True, timeout=600)
            if _comp_r.returncode == 0 and os.path.exists(_comp_out) and os.path.getsize(_comp_out) > 50000:
                safe_out = _comp_out
                print(f"  [Wav2Lip] Composited → {_bg_w}×{_bg_h} full frame (lip-sync overlay at {_crop_x1},{_crop_y1} size {_crop_w}×{_crop_h})")
            else:
                _ce = _comp_r.stderr
                if isinstance(_ce, bytes): _ce = _ce.decode("utf-8", errors="replace")
                print(f"  [Wav2Lip] Composite failed (rc={_comp_r.returncode}) — keeping crop video. {(_ce or '')[:300]}")

        # Fix A/V sync antes de copiar para output
        _synced = os.path.join(tmp_root, "out_sync.mp4")
        if _fix_av_sync(safe_out, _synced):
            shutil.copy2(_synced, output_path)
        else:
            shutil.copy2(safe_out, output_path)

        ok, reason = _validate_video_output(output_path, expected_dur=audio_dur_s, min_kb=50)
        if not ok:
            raise Exception(f"Wav2Lip output inválido: {reason}")

        print(f"  [Wav2Lip] Done → {output_path} ({os.path.getsize(output_path)//1024}KB) | validação: {reason}")
        return output_path

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# ============================================================================
# ============================================================================
# CODEFORMER FACE RESTORATION — SOTA alternative to GFPGAN (better identity)
# ============================================================================
_codeformer_cache = {"net": None, "device": None}

def _codeformer_init():
    """Lazy-init CodeFormer model + face restoration helper. Returns dict or None."""
    if _codeformer_cache["net"] is not None:
        return _codeformer_cache
    try:
        import torch as _t
        from codeformer.basicsr.utils.registry import ARCH_REGISTRY
        device = _t.device("cuda" if _t.cuda.is_available() else "cpu")
        net = ARCH_REGISTRY.get("CodeFormer")(
            dim_embd=512, codebook_size=1024, n_head=8, n_layers=9,
            connect_list=["32", "64", "128", "256"],
        ).to(device)
        ckpt = os.path.join(MODELS_DIR, "CodeFormer", "weights", "CodeFormer", "codeformer.pth")
        if not os.path.exists(ckpt):
            print(f"  [CodeFormer] checkpoint missing at {ckpt}")
            return None
        state = _t.load(ckpt, map_location=device)["params_ema"]
        net.load_state_dict(state)
        net.eval()
        _codeformer_cache.update({"net": net, "device": device})
        print("  [CodeFormer] Model loaded (CUDA)" if device.type == "cuda" else "  [CodeFormer] Model loaded (CPU)")
        return _codeformer_cache
    except Exception as e:
        print(f"  [CodeFormer] init failed: {e}")
        return None


def apply_codeformer_to_video(input_path: str, output_path: str, job_id: str = None,
                              fidelity: float = 0.7, max_process_frames: int = 0) -> str:
    """
    CodeFormer face restoration — produces sharper identity-preserving results than GFPGAN.
    fidelity 0.0 (max quality, may change identity) to 1.0 (max identity, may be blurry).
    Auto-falls back to apply_gfpgan_to_video if CodeFormer init fails.
    """
    cache = _codeformer_init()
    if cache is None:
        print("  [CodeFormer] unavailable — falling back to GFPGAN")
        return apply_gfpgan_to_video(input_path, output_path, job_id=job_id,
                                     max_process_frames=max_process_frames)

    import tempfile as _tf, cv2 as _cv, numpy as _np, torch as _t
    from torchvision.transforms.functional import normalize as _tnorm
    from codeformer.basicsr.utils import img2tensor, tensor2img
    from codeformer.facelib.utils.face_restoration_helper import FaceRestoreHelper

    net    = cache["net"]
    device = cache["device"]
    _ff    = _ffmpeg_path()
    _ffp   = _ffprobe_path()

    # Point facelib to our local weights dir so it doesn't try to re-download
    os.environ["TORCH_HOME"] = os.path.join(MODELS_DIR, "CodeFormer")

    _tmp = _tf.mkdtemp(prefix="codeformer_avp_")
    try:
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = "CodeFormer: restaurando qualidade facial (SOTA)..."

        cap   = _cv.VideoCapture(input_path)
        fps   = cap.get(_cv.CAP_PROP_FPS) or 25
        w     = int(cap.get(_cv.CAP_PROP_FRAME_WIDTH))
        h     = int(cap.get(_cv.CAP_PROP_FRAME_HEIGHT))
        total = int(cap.get(_cv.CAP_PROP_FRAME_COUNT))
        if total < 10:
            dur = _get_duration_safe(input_path)
            total = int(dur * fps)
        print(f"  [CodeFormer] {total} frames @ {fps:.0f}fps | {w}x{h}")

        # Adaptive frame budget like GFPGAN does
        if max_process_frames <= 0:
            _sec = total / max(fps, 1)
            if   _sec <= 60:   max_process_frames = total
            elif _sec <= 300:  max_process_frames = 3000
            elif _sec <= 1800: max_process_frames = 2000
            else:              max_process_frames = 1000
            print(f"  [CodeFormer] Auto max_frames={max_process_frames} for {_sec:.0f}s video")
        skip = max(1, int(_np.ceil(total / max_process_frames)))

        # Output frames to disk, then concat with ffmpeg
        out_frames_dir = os.path.join(_tmp, "frames")
        os.makedirs(out_frames_dir, exist_ok=True)
        last_restored = None
        idx = 0
        processed = 0
        while True:
            ok, frame = cap.read()
            if not ok: break
            if idx % skip == 0:
                helper = FaceRestoreHelper(
                    1, face_size=512, crop_ratio=(1, 1),
                    det_model="retinaface_resnet50", save_ext="png", use_parse=True,
                    device=device,
                )
                try:
                    helper.read_image(frame)
                    helper.get_face_landmarks_5(only_center_face=True, resize=640, eye_dist_threshold=5)
                    helper.align_warp_face()
                    for cropped in helper.cropped_faces:
                        ct = img2tensor(cropped / 255.0, bgr2rgb=True, float32=True)
                        _tnorm(ct, (0.5,0.5,0.5), (0.5,0.5,0.5), inplace=True)
                        ct = ct.unsqueeze(0).to(device)
                        with _t.no_grad():
                            out = net(ct, w=fidelity, adain=True)[0]
                            restored = tensor2img(out, rgb2bgr=True, min_max=(-1, 1))
                        del out
                        helper.add_restored_face(restored.astype(_np.uint8))
                    helper.get_inverse_affine(None)
                    final = helper.paste_faces_to_input_image(upsample_img=None)
                    last_restored = final
                except Exception as _fe:
                    last_restored = frame  # face detect failed, use raw frame
                if device.type == "cuda":
                    _t.cuda.empty_cache()
                processed += 1
                if job_id and job_id in jobs and processed % 5 == 0:
                    pct = int(processed * 100 / max_process_frames)
                    jobs[job_id]["message"] = f"CodeFormer: {pct}% — {w}x{h}"

            out_frame = last_restored if last_restored is not None else frame
            _cv.imwrite(os.path.join(out_frames_dir, f"f_{idx:06d}.png"), out_frame)
            idx += 1
        cap.release()

        # Reassemble video with original audio
        _safe_v = os.path.join(_tmp, "video.mp4")
        subprocess.run([_ff, "-y", "-framerate", str(int(fps)),
                        "-i", os.path.join(out_frames_dir, "f_%06d.png"),
                        "-c:v", "libx264", "-preset", "slow", "-crf", "16",
                        "-pix_fmt", "yuv420p", _safe_v],
                       capture_output=True, timeout=max(600, total // 2))

        subprocess.run([_ff, "-y", "-i", _safe_v, "-i", input_path,
                        "-map", "0:v", "-map", "1:a?",
                        "-c:v", "copy", "-c:a", "copy",
                        output_path],
                       capture_output=True, timeout=300)

        if not os.path.exists(output_path) or os.path.getsize(output_path) < 10000:
            print("  [CodeFormer] output invalid, falling back to copy")
            shutil.copy2(input_path, output_path)
        else:
            print(f"  [CodeFormer] Done -> {os.path.getsize(output_path)//1024}KB")
        return output_path
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)


# ============================================================================
# GFPGAN FACE RESTORATION — post-Wav2Lip quality enhancement
# ============================================================================
def apply_gfpgan_to_video(input_path: str, output_path: str, job_id: str = None,
                          max_process_frames: int = 0) -> str:
    """
    Apply GFPGAN v1.4 face restoration via isolated subprocess.
    Runs gfpgan_worker.py in a separate process so OOM cannot kill Flask.
    If GFPGAN fails or OOM occurs, falls back to copying input unchanged.
    """
    # Find model
    _gfpgan_candidates = [
        os.path.join(BASE_DIR, "venv311", "Lib", "site-packages", "gfpgan", "weights", "GFPGANv1.4.pth"),
        os.path.join(MODELS_DIR, "SadTalker", "checkpoints", "GFPGANv1.4.pth"),
        os.path.join(BASE_DIR, "gfpgan", "weights", "GFPGANv1.4.pth"),
    ]
    _gfpgan_model = next((p for p in _gfpgan_candidates if os.path.exists(p)), None)
    if not _gfpgan_model:
        print("  [GFPGAN] model not found - skipping", flush=True)
        shutil.copy2(input_path, output_path)
        return output_path

    worker_script = os.path.join(BASE_DIR, "gfpgan_worker.py")
    if not os.path.exists(worker_script):
        print("  [GFPGAN] worker script not found - skipping", flush=True)
        shutil.copy2(input_path, output_path)
        return output_path

    python_exe = os.path.join(BASE_DIR, "venv311", "Scripts", "python.exe")
    if not os.path.exists(python_exe):
        python_exe = sys.executable

    if job_id and job_id in jobs:
        jobs[job_id]["message"] = "GFPGAN: restaurando qualidade facial..."
    print(f"  [GFPGAN] Running in subprocess (isolated from Flask)", flush=True)
    import json as _gj
    try:
        proc = subprocess.Popen(
            [python_exe, worker_script, input_path, output_path, _gfpgan_model, str(max_process_frames)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=BASE_DIR, text=True, encoding="utf-8", errors="replace",
        )
        _gfpgan_start = time.time()
        for _gline in proc.stdout:
            _gline = _gline.strip()
            if not _gline: continue
            try:
                _gdata = _gj.loads(_gline)
                if _gdata.get("progress") is not None and job_id and job_id in jobs:
                    _gp = int(_gdata["progress"] * 100 / max(1, _gdata.get("total", 1)))
                    jobs[job_id]["message"] = f"GFPGAN: restaurando qualidade ({_gp}%)..."
                    jobs[job_id]["progress"] = 70 + int(_gp * 0.1)
                elif _gdata.get("success"):
                    print(f"  [GFPGAN] Done: {_gdata.get('frames_processed',0)} frames", flush=True)
                elif _gdata.get("error"):
                    print(f"  [GFPGAN] Worker: {_gdata['error']}", flush=True)
            except Exception:
                if _gline: print(f"  [GFPGAN] {_gline[:100]}", flush=True)
            if time.time() - _gfpgan_start > 600:
                proc.kill(); print("  [GFPGAN] 10min timeout", flush=True); break
        proc.wait(timeout=30)
        if proc.returncode != 0 and not os.path.exists(output_path):
            shutil.copy2(input_path, output_path)
        elif not os.path.exists(output_path):
            shutil.copy2(input_path, output_path)

    except subprocess.TimeoutExpired:
        print("  [GFPGAN] Timeout - using original", flush=True)
        if not os.path.exists(output_path): shutil.copy2(input_path, output_path)
    except Exception as e:
        print(f"  [GFPGAN] error: {e}", flush=True)
        if not os.path.exists(output_path): shutil.copy2(input_path, output_path)

    return output_path

    return output_path


def apply_gfpgan_chunked(input_path: str, output_path: str,
                         job_id: str = None, max_gfpgan_seconds: int = 600,
                         enhance_face: bool = True) -> str:
    """
    GFPGAN for any duration:
    - ≤ max_gfpgan_seconds: full GFPGAN frame-by-frame
    - > max_gfpgan_seconds: GFPGAN on first max_gfpgan_seconds, FFmpeg unsharp on rest, then concat
    This keeps processing time reasonable for long videos while still delivering
    sharp lip area for the most-watched portion.

    enhance_face=False: PULA o GFPGAN (passo mais lento — restaura cada frame).
    Essencial p/ habilitar vídeos longos: desligar o GFPGAN deixa o pipeline 2-3x
    mais rápido, viabilizando durações que de outra forma estouram o watchdog.
    """
    import tempfile as _gch_tmp
    if not enhance_face:
        # Skip face restoration — apenas copia o vídeo (muito mais rápido p/ vídeos longos)
        try:
            shutil.copy2(input_path, output_path)
            if job_id and jobs.get(job_id):
                jobs[job_id]["message"] = "GFPGAN pulado (enhance_face=false) — modo rápido p/ vídeo longo"
            print(f"  [GFPGAN] Pulado (enhance_face=false) — modo rápido", flush=True)
            return output_path
        except Exception as _ge:
            print(f"  [GFPGAN] copy skip falhou ({_ge}) — seguindo com GFPGAN normal", flush=True)
    _ff  = _ffmpeg_path()
    _ffp = _ffprobe_path()

    dur_str = subprocess.run(
        [_ffp, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True, timeout=15
    ).stdout.decode().strip()
    dur = float(dur_str) if dur_str else 0.0

    if dur <= max_gfpgan_seconds:
        # Short enough — full GFPGAN
        return apply_gfpgan_to_video(input_path, output_path, job_id=job_id)

    # Long video: GFPGAN on first part, FFmpeg sharpen on rest
    _tmp = _gch_tmp.mkdtemp(prefix="gfpgan_chunk_")
    try:
        part1 = os.path.join(_tmp, "part1_raw.mp4")
        part2 = os.path.join(_tmp, "part2_raw.mp4")
        part1_enh = os.path.join(_tmp, "part1_gfpgan.mp4")
        part2_enh = os.path.join(_tmp, "part2_sharp.mp4")

        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"Extraindo segmentos para melhoria de qualidade..."

        # Extract part1 (first max_gfpgan_seconds)
        subprocess.run([
            _ff, "-y", "-i", input_path,
            "-t", str(max_gfpgan_seconds),
            "-c:v", "copy", "-c:a", "copy", part1
        ], capture_output=True, timeout=120)

        # Extract part2 (rest)
        subprocess.run([
            _ff, "-y", "-i", input_path,
            "-ss", str(max_gfpgan_seconds),
            "-c:v", "copy", "-c:a", "copy", part2
        ], capture_output=True, timeout=120)

        # GFPGAN on part1
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"GFPGAN restauração facial (primeiros {max_gfpgan_seconds}s)..."
        apply_gfpgan_to_video(part1, part1_enh, job_id=job_id)

        # Get part1_enh resolution for part2 scaling
        _pr = subprocess.run(
            [_ffp, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", part1_enh],
            capture_output=True, timeout=10
        )
        _dims = _pr.stdout.decode().strip().split(",")
        if len(_dims) == 2:
            out_w, out_h = int(_dims[0]), int(_dims[1])
        else:
            out_w, out_h = 1280, 720

        # FFmpeg sharpen + scale on part2 (fast, no GFPGAN)
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"Nitidez FFmpeg no restante do vídeo..."
        subprocess.run([
            _ff, "-y", "-i", part2,
            "-vf", f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                   f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,"
                   f"unsharp=5:5:1.5:5:5:0.0",
            "-c:v", "libx264", "-crf", "16", "-preset", "slow",
            "-b:v", "2500k", "-maxrate", "4000k", "-bufsize", "8000k",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy", part2_enh
        ], capture_output=True, timeout=max(600, int((dur - max_gfpgan_seconds) * 3)))

        if not os.path.exists(part2_enh) or os.path.getsize(part2_enh) < 10000:
            # Fallback: just copy part2 as-is
            shutil.copy2(part2, part2_enh)

        # Concat part1 + part2
        concat_txt = os.path.join(_tmp, "concat.txt")
        with open(concat_txt, "w") as _cf:
            _cf.write(f"file '{part1_enh}'\n")
            _cf.write(f"file '{part2_enh}'\n")

        if job_id and job_id in jobs:
            jobs[job_id]["message"] = "Concatenando partes com qualidade melhorada..."

        subprocess.run([
            _ff, "-y", "-f", "concat", "-safe", "0",
            "-i", concat_txt,
            "-c", "copy", output_path
        ], capture_output=True, timeout=300)

        if not os.path.exists(output_path) or os.path.getsize(output_path) < 10000:
            print("  [GFPGAN-chunked] concat failed — using original")
            shutil.copy2(input_path, output_path)
        else:
            print(f"  [GFPGAN-chunked] Done → {os.path.getsize(output_path)//1024//1024}MB")

    except Exception as _e:
        print(f"  [GFPGAN-chunked] Error (non-fatal): {_e}")
        shutil.copy2(input_path, output_path)
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)

    return output_path


def run_wav2lip_chunked(image_path: str, audio_path: str, output_path: str,
                        settings: dict = None, chunk_duration: int = 300,
                        job_id: str = None) -> str:
    """
    Chunked Wav2Lip for long audio. When image_path is a video (SadTalker base looped),
    extracts matching video segments per chunk so Wav2Lip processes moving face — not static.
    """
    total_dur = _get_duration_safe(audio_path)
    print(f"  [Wav2Lip] Audio duration: {total_dur:.0f}s")

    _is_video_face = image_path.lower().endswith(('.mp4', '.avi', '.mov', '.webm'))
    _face_video_dur = _get_duration_safe(image_path) if _is_video_face else 0.0

    # Chunk sizes: larger chunks for video face (less seam issues, better tracking)
    if _is_video_face:
        if total_dur > 600:  chunk_duration = 180
        else:                chunk_duration = min(300, chunk_duration)
    else:
        if total_dur > 600:  chunk_duration = 120
        elif total_dur > 300: chunk_duration = 180

    # Short audio: process directly
    if total_dur <= chunk_duration + 30:
        return run_wav2lip(image_path, audio_path, output_path, settings, job_id)

    n_chunks  = math.ceil(total_dur / chunk_duration)
    import tempfile as _w2l_ctmp
    chunk_dir = _w2l_ctmp.mkdtemp(prefix="w2l_chunks_")
    ffmpeg = _ffprobe_path().replace("ffprobe", "ffmpeg")

    print(f"  [Wav2Lip] Chunked mode: {total_dur:.0f}s → {n_chunks} chunks of {chunk_duration}s"
          + (" (video face)" if _is_video_face else ""))
    chunk_videos = []

    try:
        for i in range(n_chunks):
            start = i * chunk_duration
            dur   = min(chunk_duration, total_dur - start)
            if dur < 2.0:
                break

            if job_id and job_id in jobs:
                pct = 35 + int(45 * i / n_chunks)
                jobs[job_id]["progress"] = pct
                jobs[job_id]["message"]  = f"Wav2Lip chunk {i+1}/{n_chunks} ({start:.0f}s–{start+dur:.0f}s)..."

            # Extract audio chunk
            chunk_audio = os.path.join(chunk_dir, f"chunk_{i:03d}.wav")
            r = subprocess.run([
                ffmpeg, "-y", "-i", audio_path,
                "-ss", str(round(start, 3)), "-t", str(round(dur, 3)),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                chunk_audio
            ], capture_output=True, timeout=120)
            if r.returncode != 0 or not os.path.isfile(chunk_audio):
                raise Exception(f"Audio chunk {i} extraction failed")

            # For video face input: extract matching video segment (looped if needed)
            if _is_video_face and _face_video_dur > 0:
                chunk_face = os.path.join(chunk_dir, f"face_{i:03d}.mp4")
                _face_seek = start % _face_video_dur
                r2 = subprocess.run([
                    ffmpeg, "-y",
                    "-stream_loop", "-1", "-ss", str(round(_face_seek, 3)),
                    "-i", image_path,
                    "-t", str(round(dur, 3)),
                    "-c:v", "copy", "-an", chunk_face
                ], capture_output=True, timeout=120)
                face_input = chunk_face if (r2.returncode == 0 and os.path.isfile(chunk_face)) else image_path
            else:
                face_input = image_path

            chunk_video = os.path.join(chunk_dir, f"chunk_{i:03d}.mp4")
            run_wav2lip(face_input, chunk_audio, chunk_video, settings, job_id)
            chunk_videos.append(chunk_video)

            # Free VRAM between chunks
            try:
                import torch; torch.cuda.empty_cache()
            except Exception:
                pass
            print(f"  [Wav2Lip] Chunk {i+1}/{n_chunks} done")

        if not chunk_videos:
            raise Exception("No chunks were processed")

        # Concatenate — must use ASCII temp dir to avoid non-ASCII path issues (ç in OUTPUT_DIR)
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"Concatenating {len(chunk_videos)} chunks..."

        import tempfile as _ctmp
        _ascii_tmp = _ctmp.mkdtemp(prefix="w2l_concat_")
        try:
            # Copy chunks to ASCII temp dir
            _safe_chunks = []
            for ci, v in enumerate(chunk_videos):
                _safe_c = os.path.join(_ascii_tmp, f"chunk_{ci:03d}.mp4")
                shutil.copy2(v, _safe_c)
                _safe_chunks.append(_safe_c)

            concat_txt = os.path.join(_ascii_tmp, "concat.txt")
            with open(concat_txt, "w", encoding="utf-8") as f:
                for v in _safe_chunks:
                    f.write(f"file '{v.replace(os.sep, '/')}'\n")

            safe_out = os.path.join(_ascii_tmp, "merged.mp4")
            r = subprocess.run([
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", concat_txt, "-c", "copy", safe_out
            ], capture_output=True, timeout=600)
            if r.returncode != 0:
                err = (r.stderr or b"").decode("utf-8", errors="replace")[:500] if isinstance(r.stderr, bytes) else (r.stderr or "")[:500]
                raise Exception(f"Concat failed: {err}")

            shutil.copy2(safe_out, output_path)
        finally:
            shutil.rmtree(_ascii_tmp, ignore_errors=True)

        print(f"  [Wav2Lip] Concat done: {len(chunk_videos)} chunks → {output_path}")
        return output_path

    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)


# ============================================================================
# BACKGROUND REMOVAL (rembg GPU)
# ============================================================================
_rembg_session = None

def _get_rembg_session():
    global _rembg_session
    if _rembg_session is None:
        try:
            from rembg import new_session
            _rembg_session = new_session("u2net")
            print("  [rembg] Session initialized (u2net)")
        except Exception as e:
            print(f"  [rembg] Init failed: {e}")
            _rembg_session = "failed"
    return _rembg_session if _rembg_session != "failed" else None


def remove_background(image_path: str, output_path: str = None,
                      bg_color: tuple = None) -> str:
    """
    Remove background from image using rembg.
    Returns PNG with alpha channel (transparent BG) or solid color BG.
    bg_color: None = transparent, (R,G,B) = solid color, (R,G,B,A) = semi-transparent
    """
    from rembg import remove as rembg_remove
    import numpy as _np_rb
    from PIL import Image as _PILrb

    if output_path is None:
        base = os.path.splitext(image_path)[0]
        output_path = base + "_nobg.png"

    # Read via binary (handles non-ASCII paths)
    raw = open(image_path, 'rb').read()
    result_bytes = rembg_remove(raw)

    img = _PILrb.open(__import__('io').BytesIO(result_bytes)).convert("RGBA")

    if bg_color is not None:
        bg = _PILrb.new("RGBA", img.size, bg_color + (255,) if len(bg_color) == 3 else bg_color)
        bg.paste(img, mask=img.split()[3])
        bg.convert("RGB").save(output_path, "JPEG", quality=95)
    else:
        img.save(output_path, "PNG")

    print(f"  [rembg] Background removed → {os.path.basename(output_path)}")
    return output_path


def remove_background_from_video(video_path: str, bg_image: str, output_path: str,
                                  job_id: str = None) -> str:
    """
    Remove background from each frame of a video and composite on a new background.
    Uses rembg + FFmpeg for efficient frame processing.
    """
    import tempfile as _tmpvid
    from rembg import new_session as _ns, remove as _rm
    from PIL import Image as _PILV

    session   = _ns("u2net_human_seg")  # human segmentation model
    ffmpeg    = _ffprobe_path().replace("ffprobe", "ffmpeg")
    tmp_root  = _tmpvid.mkdtemp(prefix="rmbg_vid_")

    try:
        # Extract frames
        frames_dir = os.path.join(tmp_root, "frames")
        out_dir    = os.path.join(tmp_root, "comp")
        os.makedirs(frames_dir); os.makedirs(out_dir)

        # Get video FPS
        r = subprocess.run([_ffprobe_path(), "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=r_frame_rate",
                            "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                           capture_output=True, timeout=15)
        fps_str = r.stdout.decode().strip()
        fps = eval(fps_str) if fps_str and "/" in fps_str else 25.0

        # Extract frames as PNG
        subprocess.run([ffmpeg, "-y", "-i", video_path,
                        os.path.join(frames_dir, "frame_%05d.png")],
                       capture_output=True, timeout=600)

        bg_img = _PILV.open(bg_image).convert("RGB")
        frame_files = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))

        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"Removing background: 0/{len(frame_files)} frames..."

        for idx, fname in enumerate(frame_files):
            fpath = os.path.join(frames_dir, fname)
            raw   = open(fpath, 'rb').read()
            fg    = _PILV.open(__import__('io').BytesIO(_rm(raw, session=session))).convert("RGBA")
            bg    = bg_img.copy().resize(fg.size).convert("RGBA")
            bg.paste(fg, mask=fg.split()[3])
            bg.convert("RGB").save(os.path.join(out_dir, fname), "PNG")
            if idx % 50 == 0 and job_id and job_id in jobs:
                jobs[job_id]["message"] = f"Removing background: {idx}/{len(frame_files)} frames..."

        # Reassemble video with audio
        subprocess.run([ffmpeg, "-y", "-framerate", str(fps),
                        "-i", os.path.join(out_dir, "frame_%05d.png"),
                        "-i", video_path, "-map", "0:v", "-map", "1:a?",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "16",
                        "-c:a", "aac", "-shortest", output_path],
                       capture_output=True, timeout=600)
        print(f"  [rembg video] Done → {output_path}")
        return output_path
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# ============================================================================
# AUTO-CAPTIONS (faster-whisper + FFmpeg subtitle burn)
# ============================================================================
def transcribe_to_srt(audio_path: str, language: str = None,
                      model_size: str = "base") -> str:
    """
    Transcribe audio to SRT format using faster-whisper.
    Returns SRT content as string.
    model_size: tiny, base, small, medium, large-v3
    """
    from faster_whisper import WhisperModel

    print(f"  [Whisper] Loading model '{model_size}'...")
    model = WhisperModel(model_size, device="cuda", compute_type="float16")

    # Copy to temp ASCII path
    import tempfile as _tmpw
    tmp = _tmpw.mktemp(suffix=os.path.splitext(audio_path)[1])
    shutil.copy2(audio_path, tmp)

    try:
        segments, info = model.transcribe(tmp, language=language,
                                          beam_size=5, word_timestamps=False)
        print(f"  [Whisper] Detected language: {info.language} ({info.language_probability:.0%})")

        srt_lines = []
        for i, seg in enumerate(segments, 1):
            def _fmt(t):
                h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60); ms = int((t % 1) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            srt_lines.append(f"{i}\n{_fmt(seg.start)} --> {_fmt(seg.end)}\n{seg.text.strip()}\n")

        return "\n".join(srt_lines)
    finally:
        try: os.remove(tmp)
        except: pass


def burn_captions(video_path: str, srt_content: str, output_path: str,
                  style: dict = None) -> str:
    """
    Burn SRT captions into video using FFmpeg subtitles filter.
    style: {font_size, color, bg_alpha, position}
    """
    import tempfile as _tmpc
    style = style or {}
    font_size = style.get("font_size", 22)
    color     = style.get("color", "white")
    bg_alpha  = style.get("bg_alpha", 0.5)
    position  = style.get("position", "bottom")  # bottom, top, middle

    # Write SRT to temp ASCII file
    srt_tmp = _tmpc.mktemp(suffix=".srt")
    with open(srt_tmp, "w", encoding="utf-8") as f:
        f.write(srt_content)

    ffmpeg = _ffprobe_path().replace("ffprobe", "ffmpeg")
    alignment = {"bottom": 2, "top": 6, "middle": 5}.get(position, 2)

    # Build ASS style inline — more control than default SRT
    style_str = (
        f"FontName=Arial,FontSize={font_size},PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,BackColour=&H80000000,"
        f"Bold=1,Alignment={alignment},MarginV=20"
    )
    srt_escaped = srt_tmp.replace("\\", "/").replace(":", "\\:")

    try:
        r = subprocess.run([
            ffmpeg, "-y", "-i", video_path,
            "-vf", f"subtitles='{srt_escaped}':force_style='{style_str}'",
            "-c:v", "libx264", "-preset", "slow", "-crf", "16",
            "-c:a", "copy", output_path
        ], capture_output=True, timeout=600)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="replace")[:500]
            raise Exception(f"FFmpeg subtitles failed: {err}")
        print(f"  [Captions] Burned into {os.path.basename(output_path)}")
        return output_path
    finally:
        try: os.remove(srt_tmp)
        except: pass


# ============================================================================
# AVATAR CREATOR + CLOTHING CHANGE (AI-powered)
# ============================================================================
AVATAR_LIBRARY_FILE = os.path.join(DATA_DIR, "avatar_library.json")

def _load_avatar_library() -> list:
    if os.path.exists(AVATAR_LIBRARY_FILE):
        try:
            with open(AVATAR_LIBRARY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _save_avatar_library(lib: list):
    with open(AVATAR_LIBRARY_FILE, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)


def generate_avatar_person(description: str, clothing: str = "",
                            style: str = "photorealistic",
                            gender: str = "", age: str = "",
                            background_desc: str = "studio white background",
                            width: int = 512, height: int = 512) -> str:
    """
    Generate a photorealistic avatar person using Pollinations.ai.
    Returns path to saved image.
    """
    import urllib.request, urllib.parse

    style_map = {
        "photorealistic":  "photorealistic, professional portrait photography, studio lighting, 4K, sharp focus,",
        "cinematic":       "cinematic portrait, dramatic lighting, film grain, professional photography,",
        "corporate":       "corporate headshot, professional, neutral background, business attire,",
        "influencer":      "social media influencer style, modern, trendy, ring light, selfie-quality,",
        "animated":        "3D CGI character, Pixar style, high quality render, clean background,",
    }
    style_prefix = style_map.get(style, style_map["photorealistic"])

    parts = [style_prefix]
    if gender: parts.append(gender)
    if age:    parts.append(f"{age} years old")
    if description: parts.append(description)
    if clothing: parts.append(f"wearing {clothing}")
    parts.append(background_desc)
    parts.append("front-facing, looking at camera, high quality, professional")

    prompt   = ", ".join(p.strip() for p in parts if p.strip())
    negative = "blurry, low quality, deformed, cartoon, anime, multiple people, crowd, text, watermark"
    encoded  = urllib.parse.quote(prompt)
    seed     = int(time.time()) % 99999

    url = (f"https://image.pollinations.ai/prompt/{encoded}"
           f"?nologo=true&width={width}&height={height}&model=flux"
           f"&seed={seed}&negative={urllib.parse.quote(negative)}")

    out_path = os.path.join(UPLOAD_DIR, f"avatar_gen_{uuid.uuid4().hex[:8]}.jpg")
    req = urllib.request.Request(url, headers={"User-Agent": "AvatarPilot/3.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if len(data) < 5000:
        raise Exception(f"Generated image too small ({len(data)}B) — API issue")
    with open(out_path, "wb") as f:
        f.write(data)
    print(f"  [Avatar Creator] Generated: {len(data)//1024}KB → {os.path.basename(out_path)}")
    return out_path


def change_avatar_clothing(image_path: str, new_clothing: str,
                            person_description: str = "",
                            style: str = "photorealistic") -> str:
    """
    Change avatar clothing by using img2img via Pollinations.ai with reference image.
    For consistent results, regenerates with the same person description + new clothing.
    """
    import urllib.request, urllib.parse, base64 as _b64, json as _json

    # Approach: use Pollinations img2img endpoint (image-conditioned generation)
    style_map = {
        "photorealistic": "photorealistic, professional photography, studio lighting, sharp, 4K,",
        "cinematic":      "cinematic, dramatic lighting, film photography,",
        "corporate":      "corporate professional, studio, neutral background,",
    }
    style_prefix = style_map.get(style, style_map["photorealistic"])

    parts = [style_prefix]
    if person_description: parts.append(person_description)
    parts.append(f"wearing {new_clothing}")
    parts.append("front-facing, same person, consistent identity, high quality portrait")
    prompt = ", ".join(p.strip() for p in parts if p.strip())

    # Encode source image as base64 for img2img
    raw   = open(image_path, "rb").read()
    b64   = _b64.b64encode(raw).decode("utf-8")

    encoded = urllib.parse.quote(prompt)
    seed    = int(time.time()) % 99999
    # Pollinations img2img: append image as query param
    url = (f"https://image.pollinations.ai/prompt/{encoded}"
           f"?nologo=true&width=512&height=512&model=flux"
           f"&seed={seed}&image={urllib.parse.quote(b64[:500])}")

    # Fallback: if image param doesn't work, just generate without it
    out_path = os.path.join(UPLOAD_DIR, f"avatar_outfit_{uuid.uuid4().hex[:8]}.jpg")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AvatarPilot/3.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        if len(data) < 5000:
            raise Exception("Small image")
        with open(out_path, "wb") as f:
            f.write(data)
    except Exception:
        # Fallback: generate without image reference (prompt-only)
        url_fallback = (f"https://image.pollinations.ai/prompt/{encoded}"
                        f"?nologo=true&width=512&height=512&model=flux&seed={seed}")
        req = urllib.request.Request(url_fallback, headers={"User-Agent": "AvatarPilot/3.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        with open(out_path, "wb") as f:
            f.write(data)

    print(f"  [Clothing Change] Generated: {len(data)//1024}KB → {os.path.basename(out_path)}")
    return out_path


# ============================================================================
# AI IMAGE GENERATION (Pollinations.ai — free, no key required)
# ============================================================================
def generate_avatar_image_pollinations(prompt: str, style: str = "realistic",
                                       width: int = 512, height: int = 512) -> str:
    """
    Generate avatar image using Pollinations.ai (completely free, no key).
    Returns path to saved PNG file.
    """
    import urllib.request, urllib.parse

    style_prefix = {
        "realistic":   "photorealistic portrait, studio lighting, 4K, professional headshot,",
        "anime":       "anime style portrait, detailed, high quality,",
        "illustration":"digital illustration portrait, professional, detailed,",
        "oil_painting":"oil painting portrait, classical, detailed, museum quality,",
        "3d_render":   "3D render portrait, CGI, professional, Unreal Engine quality,",
    }.get(style, "photorealistic portrait, studio lighting,")

    full_prompt = f"{style_prefix} {prompt}, suitable for talking avatar video, neutral background, front-facing"
    encoded = urllib.parse.quote(full_prompt)

    # Pollinations.ai free image generation
    url = f"https://image.pollinations.ai/prompt/{encoded}?nologo=true&width={width}&height={height}&model=flux&seed={int(time.time())}"

    out_path = os.path.join(OUTPUT_DIR, f"ai_img_{uuid.uuid4().hex[:8]}.png")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AvatarPilot/3.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        if len(data) < 5000:
            raise Exception(f"Generated image too small ({len(data)} bytes) — likely API error")
        with open(out_path, "wb") as f:
            f.write(data)
        print(f"  [AI Image] Generated: {len(data)//1024}KB")
        return out_path
    except Exception as e:
        raise Exception(f"Image generation failed: {e}")


# ============================================================================
# VOICE CLONING (ElevenLabs REST API — no SDK needed)
# ============================================================================
def elevenlabs_list_voices(api_key: str) -> list:
    """List all voices (preset + cloned) from ElevenLabs account."""
    import requests
    try:
        r = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": api_key, "Accept": "application/json"},
            timeout=15
        )
        if r.status_code == 200:
            voices = r.json().get("voices", [])
            return [{"id": v["voice_id"], "name": v["name"],
                     "category": v.get("category", "premade"),
                     "description": v.get("description", "")} for v in voices]
        return []
    except Exception as e:
        print(f"  [ElevenLabs] List voices error: {e}")
        return []

def elevenlabs_clone_voice(name: str, description: str, audio_paths: list, api_key: str) -> str:
    """Clone a voice using ElevenLabs Instant Voice Cloning API. Returns voice_id."""
    import requests
    files = []
    file_handles = []
    try:
        for i, path in enumerate(audio_paths[:5]):  # max 5 samples
            fh = open(path, "rb")
            file_handles.append(fh)
            files.append(("files", (os.path.basename(path), fh, "audio/mpeg")))

        r = requests.post(
            "https://api.elevenlabs.io/v1/voices/add",
            headers={"xi-api-key": api_key, "Accept": "application/json"},
            data={"name": name, "description": description},
            files=files,
            timeout=120
        )
        if r.status_code == 200:
            return r.json()["voice_id"]
        raise Exception(f"ElevenLabs clone error {r.status_code}: {r.text[:300]}")
    finally:
        for fh in file_handles:
            try: fh.close()
            except: pass

def elevenlabs_generate_v2(text: str, voice_id: str, api_key: str, output_path: str,
                            model: str = "eleven_multilingual_v2",
                            stability: float = 0.5, similarity: float = 0.75) -> str:
    """ElevenLabs TTS with voice settings (stability, similarity)."""
    import requests
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": api_key, "Content-Type": "application/json",
                 "Accept": "audio/mpeg"},
        json={
            "text": text,
            "model_id": model,
            "voice_settings": {"stability": stability, "similarity_boost": similarity}
        },
        timeout=120
    )
    if r.status_code == 200:
        with open(output_path, "wb") as f:
            f.write(r.content)
        return output_path
    raise Exception(f"ElevenLabs TTS error {r.status_code}: {r.text[:200]}")


# ============================================================================
# CLOUD GPU — REPLICATE A100 (serves 300 clients, no local GPU required)
# ============================================================================
def run_sadtalker_cloud_replicate(image_path, audio_path, output_path, settings=None, job_id=None):
    """
    Run SadTalker on Replicate A100/A40 cloud GPU.
    Clients need ZERO local GPU — processing happens in the cloud.
    Cost: ~$0.05-0.15 per video minute. No VRAM limits (80GB A100).
    """
    import requests as _req, base64 as _b64

    s       = load_settings()
    api_key = s.get("replicate_key", "")
    if not api_key:
        raise Exception("Replicate API key not configured. Set it in Settings → Cloud GPU.")

    settings = settings or {}

    # Read and base64-encode files for Replicate data-URI upload
    with open(image_path, "rb") as f:
        img_ext  = os.path.splitext(image_path)[1].lower().lstrip(".") or "png"
        img_mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(img_ext, "image/png")
        img_data = f"data:{img_mime};base64," + _b64.b64encode(f.read()).decode()
    with open(audio_path, "rb") as f:
        aud_ext  = os.path.splitext(audio_path)[1].lower().lstrip(".") or "mp3"
        aud_mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg"}.get(aud_ext, "audio/mpeg")
        aud_data = f"data:{aud_mime};base64," + _b64.b64encode(f.read()).decode()

    if job_id and job_id in jobs:
        jobs[job_id]["message"] = "Submitting to Cloud GPU (Replicate A100)..."

    # Submit prediction — uses Replicate's SadTalker model (A100/A40 GPU)
    resp = _req.post(
        "https://api.replicate.com/v1/models/cjwbw/sadtalker/predictions",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json",
                 "Prefer": "wait"},  # Prefer:wait enables synchronous mode (up to 60s)
        json={"input": {
            "source_image":  img_data,
            "driven_audio":  aud_data,
            "preprocess":    settings.get("preprocess", "crop"),
            "still_mode":    bool(settings.get("still", False)),
            "use_enhancer":  settings.get("enhancer", "gfpgan") in ("gfpgan", "RestoreFormer"),
            "size_of_image": int(settings.get("size", 256)),
            "pose_style":    0,
            "exp_scale":     float(settings.get("expression_scale", 1.0)),
        }},
        timeout=120
    )

    if resp.status_code not in (200, 201):
        raise Exception(f"Replicate API error {resp.status_code}: {resp.text[:300]}")

    pred = resp.json()
    # Synchronous (Prefer:wait) may return result immediately
    if pred.get("status") == "succeeded" and pred.get("output"):
        output_url = pred["output"]
    else:
        pred_id  = pred["id"]
        poll_url = pred.get("urls", {}).get("get", f"https://api.replicate.com/v1/predictions/{pred_id}")

        # Poll until done (A100 is fast — usually 30-120s per minute of audio)
        print(f"  [Cloud GPU] Prediction {pred_id} — polling for result...")
        for attempt in range(720):   # max 1 hour (720 × 5s)
            time.sleep(5)
            r    = _req.get(poll_url, headers={"Authorization": f"Token {api_key}"}, timeout=15)
            pred = r.json()
            st   = pred.get("status")

            if job_id and job_id in jobs:
                pmap = {"starting": 38, "processing": 55, "succeeded": 88}
                jobs[job_id]["progress"] = pmap.get(st, jobs[job_id].get("progress", 35))
                jobs[job_id]["message"]  = f"Cloud GPU — {st} ({attempt*5}s)..."

            print(f"  [Cloud GPU] {st} ({attempt*5}s)")
            if st == "succeeded":
                output_url = pred.get("output")
                if not output_url:
                    raise Exception("Replicate returned no output URL")
                break
            elif st == "failed":
                raise Exception(f"Cloud GPU job failed: {pred.get('error', 'unknown')}")
        else:
            raise Exception("Cloud GPU timeout after 1 hour")

    # Download result video
    if job_id and job_id in jobs:
        jobs[job_id]["message"] = "Downloading result from cloud..."
    r2 = _req.get(output_url, timeout=300, stream=True)
    r2.raise_for_status()
    with open(output_path, "wb") as f:
        for chunk in r2.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"  [Cloud GPU] Done — {os.path.getsize(output_path)//1024}KB saved")
    return output_path


# ============================================================================
# LOCAL ECHOMIMIC V2 — HeyGen-class half-body w/ HAND GESTURES (100% free)
# ============================================================================
ECHOMIMIC_DIR = os.path.join(MODELS_DIR, "EchoMimicV2")

def check_echomimic_v2() -> bool:
    """Check if EchoMimic V2 is installed locally (venv + key model weights)."""
    venv_py = os.path.join(ECHOMIMIC_DIR, "venv", "Scripts", "python.exe")
    weights = os.path.join(ECHOMIMIC_DIR, "pretrained_weights")
    return all(os.path.exists(p) for p in [
        venv_py,
        os.path.join(ECHOMIMIC_DIR, "run_local.py"),
        os.path.join(weights, "denoising_unet.pth"),
        os.path.join(weights, "motion_module.pth"),
        os.path.join(weights, "reference_unet.pth"),
        os.path.join(weights, "pose_encoder.pth"),
    ])


def run_echomimic_v2_local(image_path, audio_path, output_path, settings=None, job_id=None):
    """
    Run EchoMimic V2 locally (no cloud cost). Generates the avatar speaking
    WITH HAND GESTURES from a single photo + audio using one of 8 pose templates.

    settings options:
      pose (str): pose template name (01-04, fight, good, salute, ultraman) — default "01"
      width (int): output width (default 768)
      height (int): output height (default 768)
      steps (int): diffusion steps 10-30 (default 20)
      cfg (float): guidance scale (default 2.5)
      seed (int): random seed (default 3407)
      fps (int): output fps (default 24)
    """
    if not check_echomimic_v2():
        raise Exception(
            "EchoMimic V2 not installed locally. Required: "
            "models/EchoMimicV2/venv/ + models/EchoMimicV2/pretrained_weights/*.pth"
        )
    settings = settings or {}
    venv_py = os.path.join(ECHOMIMIC_DIR, "venv", "Scripts", "python.exe")
    runner  = os.path.join(ECHOMIMIC_DIR, "run_local.py")

    # Copy inputs to ASCII paths (avoid Windows ç issue)
    import tempfile as _tfe
    tmp = _tfe.mkdtemp(prefix="echo_avp_")
    try:
        safe_img = os.path.join(tmp, "ref" + (os.path.splitext(image_path)[1] or ".jpg"))
        safe_aud = os.path.join(tmp, "audio" + (os.path.splitext(audio_path)[1] or ".wav"))
        safe_out = os.path.join(tmp, "out.mp4")
        shutil.copy2(image_path, safe_img)
        shutil.copy2(audio_path, safe_aud)

        # Audio must be 16kHz for the wav2vec2 audio encoder
        if not safe_aud.endswith(".wav") or _get_duration_safe(safe_aud) > 0:
            wav_path = os.path.join(tmp, "audio16k.wav")
            subprocess.run([_ffmpeg_path(), "-y", "-i", safe_aud,
                            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path],
                           capture_output=True, timeout=120)
            if os.path.exists(wav_path) and os.path.getsize(wav_path) > 1000:
                safe_aud = wav_path

        pose   = str(settings.get("pose", "01"))
        width  = int(settings.get("width", 768))
        height = int(settings.get("height", 768))
        steps  = int(settings.get("steps", 20))
        cfg    = float(settings.get("cfg", 2.5))
        seed   = int(settings.get("seed", 3407))
        fps    = int(settings.get("fps", 24))

        # Max length capped by audio dur in run_local.py — but expose budget here
        aud_dur = _get_duration_safe(safe_aud)
        length  = min(int(aud_dur * fps) + 10, 1200)  # safety cap ~50s per call

        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"EchoMimic V2 (local): gerando gestos com pose '{pose}'..."

        cmd = [venv_py, runner,
               "--ref",    safe_img,
               "--audio",  safe_aud,
               "--pose",   pose,
               "--output", safe_out,
               "-W", str(width), "-H", str(height),
               "-L", str(length),
               "--steps", str(steps),
               "--cfg",   str(cfg),
               "--seed",  str(seed),
               "--fps",   str(fps)]

        # Timeout: each diffusion step ~3-5s/frame on RTX 4060; budget conservatively
        timeout_s = max(900, int(aud_dur * fps * 5) + 600)
        print(f"  [EchoMimic V2 local] inferring (timeout={timeout_s}s)")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["FFMPEG_PATH"] = os.path.dirname(_ffmpeg_path())

        result = subprocess.run(cmd, capture_output=True, timeout=timeout_s,
                                cwd=ECHOMIMIC_DIR, env=env)
        so = (result.stdout or b"").decode("utf-8", errors="replace")
        se = (result.stderr or b"").decode("utf-8", errors="replace")
        if so: print(so[-1000:])
        if se: print(se[-1000:])

        if not os.path.exists(safe_out) or os.path.getsize(safe_out) < 10000:
            raise Exception(f"EchoMimic V2 produced no output (rc={result.returncode})")

        shutil.copy2(safe_out, output_path)
        print(f"  [EchoMimic V2 local] Done -> {os.path.getsize(output_path)//1024}KB")
        return output_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================
# CLOUD GPU — ECHOMIMIC V2 via Replicate (fallback when no local GPU)
# ============================================================================
def run_echomimic_v2_replicate(image_path, audio_path, output_path, settings=None, job_id=None):
    """
    Run EchoMimic v2 on Replicate (Ant Group's SOTA half-body model).
    Generates the avatar speaking WITH HAND GESTURES from a single photo + audio.
    No gesture-template video required — model synthesizes natural body motion.
    This is the local equivalent of HeyGen's gesture engine.

    Cost: ~$0.30/minute of generated video on Replicate A100.
    Requires: settings["replicate_key"] OR replicate_key in server settings.
    """
    import requests as _req, base64 as _b64

    s       = load_settings()
    api_key = s.get("replicate_key", "")
    if not api_key:
        raise Exception("Replicate API key not configured. Set it in Settings → Cloud GPU.")

    settings = settings or {}

    with open(image_path, "rb") as f:
        img_ext  = os.path.splitext(image_path)[1].lower().lstrip(".") or "png"
        img_mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(img_ext, "image/png")
        img_data = f"data:{img_mime};base64," + _b64.b64encode(f.read()).decode()
    with open(audio_path, "rb") as f:
        aud_ext  = os.path.splitext(audio_path)[1].lower().lstrip(".") or "wav"
        aud_mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4"}.get(aud_ext, "audio/wav")
        aud_data = f"data:{aud_mime};base64," + _b64.b64encode(f.read()).decode()

    if job_id and job_id in jobs:
        jobs[job_id]["message"] = "EchoMimic v2: enviando para Cloud GPU (HeyGen-class gestos)..."

    # EchoMimic v2 on Replicate — multiple community models available
    # Primary: jd7h/echomimic-v2 (most stable)
    resp = _req.post(
        "https://api.replicate.com/v1/models/jd7h/echomimic-v2/predictions",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json",
                 "Prefer": "wait"},
        json={"input": {
            "input_image":  img_data,
            "input_audio":  aud_data,
            "width":  int(settings.get("width", 768)),
            "height": int(settings.get("height", 768)),
            "guidance_scale":  float(settings.get("guidance_scale", 2.5)),
            "num_inference_steps": int(settings.get("steps", 20)),
            "seed": int(settings.get("seed", 42)),
        }},
        timeout=120
    )

    if resp.status_code not in (200, 201):
        raise Exception(f"Replicate EchoMimic v2 error {resp.status_code}: {resp.text[:300]}")

    pred = resp.json()
    if pred.get("status") == "succeeded" and pred.get("output"):
        output_url = pred["output"]
    else:
        pred_id  = pred["id"]
        poll_url = pred.get("urls", {}).get("get", f"https://api.replicate.com/v1/predictions/{pred_id}")
        print(f"  [EchoMimic v2] Prediction {pred_id} — polling...")
        for attempt in range(1440):  # up to 2h (1440 * 5s)
            time.sleep(5)
            r    = _req.get(poll_url, headers={"Authorization": f"Token {api_key}"}, timeout=15)
            pred = r.json()
            st   = pred.get("status")

            if job_id and job_id in jobs:
                pmap = {"starting": 40, "processing": 60, "succeeded": 88}
                jobs[job_id]["progress"] = pmap.get(st, jobs[job_id].get("progress", 50))
                jobs[job_id]["message"]  = f"EchoMimic v2 — {st} ({attempt*5}s)..."

            if st == "succeeded":
                output_url = pred.get("output")
                if not output_url:
                    raise Exception("EchoMimic v2 returned no output URL")
                break
            elif st == "failed":
                raise Exception(f"EchoMimic v2 failed: {pred.get('error', 'unknown')}")
        else:
            raise Exception("EchoMimic v2 timeout after 2h")

    if isinstance(output_url, list): output_url = output_url[0]
    if job_id and job_id in jobs:
        jobs[job_id]["message"] = "Baixando resultado HeyGen-class..."
    r2 = _req.get(output_url, timeout=600, stream=True)
    r2.raise_for_status()
    with open(output_path, "wb") as f:
        for chunk in r2.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"  [EchoMimic v2] Done — {os.path.getsize(output_path)//1024}KB")
    return output_path


# ============================================================================
# CLOUD GPU — HUGGINGFACE SPACES (FREE, A100 ZeroGPU)
# ============================================================================
def run_sadtalker_huggingface(image_path, audio_path, output_path, settings=None, job_id=None):
    """
    Run SadTalker on HuggingFace Spaces ZeroGPU — 100% FREE, no API key needed.
    Uses gradio_client to call the SadTalker Space (vinthony/SadTalker).
    Clients need ZERO local GPU. Falls back to alternate spaces if primary is down.
    """
    try:
        from gradio_client import Client, handle_file
    except ImportError:
        raise Exception("gradio_client not installed. Run: pip install gradio_client")

    settings = settings or {}
    size       = int(settings.get("size", 256))
    preprocess = settings.get("preprocess", "crop")
    still      = bool(settings.get("still", False))
    enhancer   = settings.get("enhancer", "gfpgan")
    exp_scale  = float(settings.get("expression_scale", 1.0))

    # HuggingFace Space candidates (first working one is used)
    SPACES = [
        "vinthony/SadTalker",
        "fffiloni/SadTalker",
    ]

    # Copy files to ASCII temp dir (HF client may also have issues with non-ASCII)
    import tempfile
    tmp_root   = tempfile.mkdtemp(prefix="avp_hf_")
    safe_img   = os.path.join(tmp_root, "src" + os.path.splitext(image_path)[1])
    safe_audio = os.path.join(tmp_root, "aud" + os.path.splitext(audio_path)[1])
    shutil.copy2(image_path, safe_img)
    shutil.copy2(audio_path, safe_audio)

    last_err = None
    try:
        for space in SPACES:
            try:
                if job_id and job_id in jobs:
                    jobs[job_id]["message"] = f"Connecting to HuggingFace Space ({space})..."

                print(f"  [HF Cloud] Connecting to {space}...")
                client = Client(space)

                if job_id and job_id in jobs:
                    jobs[job_id]["message"] = "HuggingFace: submitting job..."
                    jobs[job_id]["progress"] = 38

                # Discover the API endpoints for this space
                api_info = client.view_api(return_format="dict")
                named_endpoints = api_info.get("named_endpoints", {})
                unnamed_endpoints = api_info.get("unnamed_endpoints", {})
                all_endpoints = list(named_endpoints.keys()) + list(unnamed_endpoints.keys())
                print(f"  [HF Cloud] Available endpoints: {all_endpoints[:10]}")

                # Try the standard SadTalker endpoint names
                result = None
                for ep in ("/test", "/generate", "/predict", "test", "generate", "predict"):
                    if ep in all_endpoints or ep.lstrip("/") in all_endpoints:
                        try:
                            print(f"  [HF Cloud] Trying endpoint: {ep}")
                            result = client.predict(
                                handle_file(safe_img),   # source_image
                                handle_file(safe_audio), # driven_audio
                                preprocess,              # preprocess type
                                still,                   # still_mode
                                False,                   # use_idle_mode
                                exp_scale,               # expression_scale
                                size,                    # size_of_image
                                False,                   # use_blink
                                enhancer if enhancer in ("gfpgan", "RestoreFormer") else "gfpgan",
                                api_name=ep,
                            )
                            break
                        except Exception as ep_err:
                            print(f"  [HF Cloud] Endpoint {ep} failed: {ep_err}")
                            continue

                if result is None:
                    # Fallback: call without api_name (first available endpoint)
                    print("  [HF Cloud] Trying default endpoint...")
                    result = client.predict(
                        handle_file(safe_img),
                        handle_file(safe_audio),
                        preprocess,
                        still,
                        False,
                        exp_scale,
                        size,
                        False,
                        enhancer if enhancer in ("gfpgan", "RestoreFormer") else "gfpgan",
                    )

                if job_id and job_id in jobs:
                    jobs[job_id]["progress"] = 88
                    jobs[job_id]["message"] = "HuggingFace: downloading result..."

                # result is the path to the returned video file (gradio_client downloads it)
                result_path = None
                if isinstance(result, (list, tuple)):
                    # Some spaces return (video_path, ...) or [video_path, ...]
                    for item in result:
                        if isinstance(item, str) and os.path.exists(item):
                            result_path = item
                            break
                        if isinstance(item, dict) and item.get("video"):
                            result_path = item["video"]
                            break
                elif isinstance(result, str) and os.path.exists(result):
                    result_path = result
                elif isinstance(result, dict):
                    result_path = result.get("video") or result.get("output") or result.get("file")

                if not result_path or not os.path.exists(result_path):
                    raise Exception(f"HuggingFace returned no valid video path: {result!r}")

                shutil.copy2(result_path, output_path)
                print(f"  [HF Cloud] Done — {os.path.getsize(output_path)//1024}KB saved")
                return output_path

            except Exception as space_err:
                print(f"  [HF Cloud] Space {space} failed: {space_err}")
                last_err = space_err
                continue

        raise Exception(f"All HuggingFace spaces failed. Last error: {last_err}")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# ============================================================================
# AUTO LANGUAGE DETECTION
# ============================================================================
def auto_detect_voice(text: str) -> str:
    """Detect script language and return the best matching Edge-TTS voice."""
    if not text or len(text) < 20:
        return "en-US-GuyNeural"

    # Simple Unicode-based heuristic — no extra package needed
    text_l = text.lower()
    sample  = text_l[:500]

    # Portuguese (check before Spanish — many common words)
    pt_words = ['não', 'você', 'está', 'são', 'também', 'muito', 'isso', 'para', 'com', 'uma', 'mas']
    if sum(1 for w in pt_words if w in sample) >= 2:
        # Brazil or Portugal?
        if any(w in sample for w in ['ão', 'ão', 'ações', 'vocês', 'então']):
            return "pt-BR-AntonioNeural"
        return "pt-PT-DuarteNeural"

    # Spanish
    es_words = ['que', 'los', 'las', 'una', 'del', 'por', 'con', 'más', 'también', 'está', 'para']
    if sum(1 for w in es_words if f' {w} ' in f' {sample} ') >= 3:
        return "es-MX-JorgeNeural"

    # French
    fr_words = ['les', 'des', 'une', 'est', 'dans', 'sur', 'avec', 'pour', 'vous', 'nous', "d'"]
    if sum(1 for w in fr_words if f' {w} ' in f' {sample} ') >= 3:
        return "fr-FR-HenriNeural"

    # German
    de_words = ['die', 'der', 'das', 'ist', 'und', 'ich', 'sie', 'ein', 'nicht', 'mit', 'auf']
    if sum(1 for w in de_words if f' {w} ' in f' {sample} ') >= 3:
        return "de-DE-ConradNeural"

    # Italian
    it_words = ['della', 'nel', 'con', 'una', 'per', 'sono', 'come', 'più', 'che', 'questo']
    if sum(1 for w in it_words if f' {w} ' in f' {sample} ') >= 3:
        return "it-IT-DiegoNeural"

    # Japanese (CJK block)
    if any('぀' <= c <= 'ヿ' or '一' <= c <= '鿿' for c in text[:200]):
        if any('぀' <= c <= 'ヿ' for c in text[:200]):
            return "ja-JP-KeitaNeural"
        return "zh-CN-YunxiNeural"

    # Arabic
    if any('؀' <= c <= 'ۿ' for c in text[:200]):
        return "ar-SA-HamedNeural"

    # Hindi
    if any('ऀ' <= c <= 'ॿ' for c in text[:200]):
        return "hi-IN-MadhurNeural"

    return "en-US-GuyNeural"


# ============================================================================
# VRAM MANAGEMENT
# ============================================================================
def get_vram_free_gb() -> float:
    """Return free VRAM in GB. Returns 999 if no GPU or PyTorch not available."""
    try:
        import torch
        if torch.cuda.is_available():
            free, _ = torch.cuda.mem_get_info(0)
            return round(free / 1024**3, 2)
    except Exception:
        pass
    return 999.0

def vram_is_sufficient(min_gb: float = 2.0) -> bool:
    """Check if enough VRAM is available to start a new SadTalker job."""
    free = get_vram_free_gb()
    print(f"  [VRAM] Free: {free}GB (need {min_gb}GB)")
    return free >= min_gb

def release_vram():
    """Release unused VRAM after a job completes."""
    try:
        import torch, gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except Exception:
        pass


def _get_duration_safe(p: str) -> float:
    """Get media duration in seconds, safe for non-ASCII paths."""
    import tempfile as _tf, shutil as _sh
    _work = p
    _tmp  = None
    try:
        p.encode('ascii')
    except UnicodeEncodeError:
        _ext = os.path.splitext(p)[1]
        _tmp = os.path.join(_tf.gettempdir(), f"avpdur_{uuid.uuid4().hex[:8]}{_ext}")
        _sh.copy2(p, _tmp)
        _work = _tmp
    try:
        _r = subprocess.run(
            [_ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", _work],
            capture_output=True, timeout=15
        )
        _out = _r.stdout.decode("utf-8", errors="replace").strip()
        return float(_out) if _out else 0.0
    except Exception:
        return 0.0
    finally:
        if _tmp and os.path.exists(_tmp):
            try: os.remove(_tmp)
            except: pass


def _get_base_url() -> str:
    """Return the server base URL from settings (fallback: localhost:5052)."""
    try:
        s = load_settings()
        return s.get("base_url", "http://localhost:5052").rstrip("/")
    except Exception:
        return "http://localhost:5052"


def _validate_video_output(path: str, expected_dur: float = 0, min_kb: int = 50) -> tuple:
    """
    Validates a video output file.
    Returns (ok: bool, reason: str).
    Used after every pipeline step to catch silent failures.
    """
    if not path or not os.path.exists(path):
        return False, "arquivo não existe"
    size_kb = os.path.getsize(path) // 1024
    if size_kb < min_kb:
        return False, f"muito pequeno ({size_kb}KB < {min_kb}KB)"
    try:
        _r = subprocess.run(
            [_ffprobe_path(), "-v", "error",
             "-show_entries", "format=duration,size",
             "-show_entries", "stream=codec_type",
             "-of", "json", path],
            capture_output=True, timeout=15
        )
        _info = json.loads(_r.stdout.decode("utf-8", errors="replace"))
        dur = float(_info.get("format", {}).get("duration", 0) or 0)
        streams = [s.get("codec_type") for s in _info.get("streams", [])]
        if "video" not in streams:
            return False, "sem stream de vídeo"
        if expected_dur > 0 and dur < expected_dur * 0.70:
            return False, f"duração muito curta ({dur:.1f}s esperado ≥{expected_dur*0.70:.1f}s)"
        return True, f"OK ({dur:.1f}s, {size_kb}KB)"
    except Exception as _ve:
        return False, f"ffprobe falhou: {_ve}"


def _fix_av_sync(input_path: str, output_path: str, job_id: str = None) -> bool:
    """
    Fix audio/video sync drift using ffmpeg aresample.
    Runs in-place if output_path == input_path via temp file.
    Returns True on success.
    """
    import tempfile as _avt
    ff = _ffmpeg_path()
    _tmp = None
    try:
        _tmp_dir = _avt.mkdtemp(prefix="avsync_")
        _tmp = os.path.join(_tmp_dir, "synced.mp4")
        _r = subprocess.run([
            ff, "-y", "-i", input_path,
            "-c:v", "copy",
            "-af", "aresample=async=1000",
            "-c:a", "aac", "-b:a", "192k",
            _tmp
        ], capture_output=True, timeout=300)
        if _r.returncode == 0 and os.path.exists(_tmp) and os.path.getsize(_tmp) > 10000:
            shutil.copy2(_tmp, output_path)
            shutil.rmtree(_tmp_dir, ignore_errors=True)
            return True
        shutil.rmtree(_tmp_dir, ignore_errors=True)
        return False
    except Exception as _se:
        print(f"  [AVSync] fix failed: {_se}")
        if _tmp:
            shutil.rmtree(os.path.dirname(_tmp), ignore_errors=True)
        return False


# ============================================================================
# WEBHOOK NOTIFICATIONS
# ============================================================================
def notify_webhooks(job_id: str, job_data: dict):
    """Fire all registered webhooks for this job (runs in background thread)."""
    urls = db_get_webhooks(job_id)
    if not urls:
        return
    base = _get_base_url()
    payload = {
        "event":       "job_complete",
        "job_id":      job_id,
        "status":      job_data.get("status"),
        "duration":    job_data.get("duration"),
        "output_url":  f"{base}/outputs/{job_data.get('output_filename', '')}",
        "thumbnail":   job_data.get("thumbnail", ""),
        "error":       job_data.get("error", ""),
        "timestamp":   datetime.now().isoformat(),
    }
    def _fire():
        import requests as _r
        for url in urls:
            try:
                _r.post(url, json=payload, timeout=10)
                print(f"  [Webhook] Fired: {url} for job {job_id}")
            except Exception as e:
                print(f"  [Webhook] Failed {url}: {e}")
    Thread(target=_fire, daemon=True).start()


# ============================================================================
# THUMBNAIL GENERATION
# ============================================================================
def generate_thumbnail(video_path: str, out_path: str) -> bool:
    """Extract a frame at ~2s as JPEG thumbnail."""
    try:
        cmd = [_ffmpeg_path(), "-y", "-i", video_path,
               "-ss", "00:00:02", "-vframes", "1", "-q:v", "3",
               "-vf", "scale=320:-1", out_path]
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        return r.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False

# ============================================================================
# BACKGROUND COMPOSITING (with position + size options)
# ============================================================================
def normalize_audio(input_path: str, output_path: str) -> str:
    """Normalize audio to -16 LUFS using FFmpeg loudnorm (broadcast standard)."""
    ffmpeg = _ffmpeg_path()
    cmd = [
        ffmpeg, "-y", "-i", input_path,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar", "44100", "-c:a", "libmp3lame", "-q:a", "2",
        output_path
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0 or not os.path.exists(output_path):
        shutil.copy2(input_path, output_path)
    return output_path


def apply_output_format(input_path: str, output_path: str, fmt: str = "landscape") -> str:
    """
    Reformat video to a target aspect ratio.
    landscape = 16:9 (1920x1080), portrait = 9:16 (1080x1920), square = 1:1 (1080x1080)
    """
    if fmt == "landscape" or fmt not in ("portrait", "square"):
        shutil.copy2(input_path, output_path)
        return output_path

    ffmpeg = _ffmpeg_path()
    if fmt == "portrait":
        # Scale to fill 1080x1920, crop excess
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        tw, th = 1080, 1920
    else:  # square
        # Crop largest centered square, then scale
        vf = "crop=min(iw\\,ih):min(iw\\,ih),scale=1080:1080"
        tw, th = 1080, 1080

    cmd = [
        ffmpeg, "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-c:a", "copy",
        output_path
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0 or not os.path.exists(output_path):
        shutil.copy2(input_path, output_path)
    else:
        print(f"  [Format] {fmt} ({tw}x{th}) applied → {os.path.basename(output_path)}")
    return output_path


def add_watermark(input_path: str, output_path: str,
                  text: str, position: str = "bottom_right",
                  font_size: int = 24, color: str = "white",
                  opacity: float = 0.7) -> str:
    """Burn text watermark into video using FFmpeg drawtext."""
    if not text or not text.strip():
        shutil.copy2(input_path, output_path)
        return output_path

    ffmpeg = _ffmpeg_path()

    pos_map = {
        "bottom_right": f"x=w-tw-20:y=h-th-20",
        "bottom_left":  f"x=20:y=h-th-20",
        "top_right":    f"x=w-tw-20:y=20",
        "top_left":     f"x=20:y=20",
        "center":       f"x=(w-tw)/2:y=(h-th)/2",
    }
    pos_expr = pos_map.get(position, "x=w-tw-20:y=h-th-20")

    safe_text = text.replace("'", "\\'").replace(":", "\\:")
    alpha_val = min(max(float(opacity), 0.1), 1.0)

    drawtext = (
        f"drawtext=text='{safe_text}':"
        f"fontsize={font_size}:"
        f"fontcolor={color}@{alpha_val:.1f}:"
        f"box=1:boxcolor=black@{alpha_val*0.4:.1f}:boxborderw=6:"
        f"{pos_expr}"
    )

    cmd = [
        ffmpeg, "-y", "-i", input_path,
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-c:a", "copy",
        output_path
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0 or not os.path.exists(output_path):
        print(f"  [Watermark] FFmpeg failed, skipping watermark")
        shutil.copy2(input_path, output_path)
    else:
        print(f"  [Watermark] Added '{text}' at {position}")
    return output_path


def composite_with_background(avatar_video, bg_image, output_path,
                               position="bottom_right", avatar_size="medium", opacity=1.0):
    """Composite avatar onto background with flexible positioning."""
    if not bg_image or not os.path.exists(bg_image):
        shutil.copy2(avatar_video, output_path)
        return output_path

    ffmpeg = _ffmpeg_path()

    size_map = {
        "small":      320,
        "medium":     480,
        "large":      720,
        "fullscreen": 1920,
    }
    av_w = size_map.get(avatar_size, 480)

    pos_map = {
        "bottom_right": f"W-w-50:H-h-50",
        "bottom_left":  f"50:H-h-50",
        "top_right":    f"W-w-50:50",
        "top_left":     f"50:50",
        "center":       f"(W-w)/2:(H-h)/2",
        "fullscreen":   f"0:0",
    }
    pos_expr = pos_map.get(position, "W-w-50:H-h-50")

    scale_filter = f"scale={av_w}:-1" if avatar_size != "fullscreen" else "scale=1920:1080"
    alpha = min(max(float(opacity), 0.1), 1.0)

    filter_complex = (
        f"[1:v]{scale_filter},format=yuva420p,"
        f"colorchannelmixer=aa={alpha:.2f}[av];"
        f"[0:v]scale=1920:1080[bg];"
        f"[bg][av]overlay={pos_expr}:shortest=1[vout]"
    )

    cmd = [
        ffmpeg, "-y",
        "-loop", "1", "-i", bg_image,
        "-i", avatar_video,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", output_path
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0:
        err_msg = proc.stderr.decode("utf-8", errors="replace")[:300] if proc.stderr else ""
        print(f"  [Composite] FFmpeg failed: {err_msg}, using avatar directly")
        shutil.copy2(avatar_video, output_path)
    return output_path

# ============================================================================
# BACKGROUND MUSIC — mix voice + music track
# ============================================================================
def mix_background_music(video_path: str, music_path: str, output_path: str,
                          music_volume: float = 0.15) -> str:
    """Mix background music into video at reduced volume. Loops if shorter than video."""
    if not music_path or not os.path.exists(music_path):
        shutil.copy2(video_path, output_path)
        return output_path
    ffmpeg = _ffmpeg_path()
    vol = max(0.01, min(1.0, float(music_volume)))
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex",
        f"[1:a]volume={vol:.2f}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", output_path
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0 or not os.path.exists(output_path):
        print(f"  [Music] Mix failed, using original")
        shutil.copy2(video_path, output_path)
    else:
        print(f"  [Music] Mixed background track at vol={vol:.0%}")
    return output_path


# ============================================================================
# FADE IN/OUT TRANSITIONS
# ============================================================================
def apply_fade(video_path: str, output_path: str,
               fade_in: float = 0.5, fade_out: float = 0.5) -> str:
    """Apply fade-in and fade-out to video and audio."""
    dur = _get_duration_safe(video_path)
    if dur <= 0:
        shutil.copy2(video_path, output_path)
        return output_path
    ffmpeg = _ffmpeg_path()
    fi = max(0.0, float(fade_in))
    fo = max(0.0, float(fade_out))
    fo_start = max(0.0, dur - fo)
    vf = f"fade=t=in:st=0:d={fi},fade=t=out:st={fo_start:.3f}:d={fo}"
    af = f"afade=t=in:st=0:d={fi},afade=t=out:st={fo_start:.3f}:d={fo}"
    cmd = [
        ffmpeg, "-y", "-i", video_path,
        "-vf", vf, "-af", af,
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        output_path
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0 or not os.path.exists(output_path):
        shutil.copy2(video_path, output_path)
    else:
        print(f"  [Fade] in={fi}s out={fo}s applied")
    return output_path


# ============================================================================
# EXPORT FORMATS — WebM, MOV, GIF
# ============================================================================
def export_additional_format(video_path: str, output_dir: str,
                              job_id: str, fmt: str) -> str:
    """Convert video to alternate format (webm, mov, gif)."""
    ffmpeg  = _ffmpeg_path()
    dur     = _get_duration_safe(video_path)
    out_path = os.path.join(output_dir, f"{job_id}_export.{fmt}")
    if fmt == "webm":
        cmd = [ffmpeg, "-y", "-i", video_path,
               "-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0",
               "-c:a", "libopus", "-b:a", "128k", out_path]
    elif fmt == "mov":
        cmd = [ffmpeg, "-y", "-i", video_path,
               "-c:v", "libx264", "-preset", "slow", "-crf", "16",
               "-c:a", "aac", "-b:a", "192k",
               "-movflags", "+faststart", out_path]
    elif fmt == "gif":
        # Short GIF (first 8s, 15fps, 480px)
        limit = min(8.0, dur)
        palette = os.path.join(output_dir, f"{job_id}_palette.png")
        cmd_pal = [ffmpeg, "-y", "-t", str(limit), "-i", video_path,
                   "-vf", "fps=15,scale=480:-1:flags=lanczos,palettegen", palette]
        subprocess.run(cmd_pal, capture_output=True, timeout=60)
        cmd = [ffmpeg, "-y", "-t", str(limit), "-i", video_path, "-i", palette,
               "-filter_complex", "fps=15,scale=480:-1:flags=lanczos[x];[x][1:v]paletteuse",
               out_path]
    else:
        return ""
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode == 0 and os.path.exists(out_path):
        print(f"  [Export] {fmt.upper()} → {os.path.basename(out_path)}")
        return out_path
    return ""


# ============================================================================
# IMAGE ENHANCEMENT before lip sync
# ============================================================================
def enhance_image(image_path: str, output_path: str,
                  sharpen: bool = True, denoise: bool = False,
                  brightness: float = 0.0, contrast: float = 0.0) -> str:
    """Auto-enhance face image: sharpen, denoise, brightness/contrast adjust."""
    try:
        import cv2 as _cv2e
        import numpy as _npe
        raw = open(image_path, 'rb').read()
        img = _cv2e.imdecode(_npe.frombuffer(raw, _npe.uint8), _cv2e.IMREAD_COLOR)
        if img is None:
            shutil.copy2(image_path, output_path)
            return output_path

        if denoise:
            img = _cv2e.fastNlMeansDenoisingColored(img, None, 6, 6, 7, 21)

        if sharpen:
            kernel = _npe.array([[0, -0.5, 0], [-0.5, 3, -0.5], [0, -0.5, 0]], dtype=_npe.float32)
            img = _cv2e.filter2D(img, -1, kernel)

        if brightness != 0.0 or contrast != 0.0:
            alpha = 1.0 + float(contrast) / 100.0
            beta  = float(brightness)
            img   = _cv2e.convertScaleAbs(img, alpha=alpha, beta=beta)

        # Auto-balance: CLAHE on luminance
        lab   = _cv2e.cvtColor(img, _cv2e.COLOR_BGR2LAB)
        l, a, b = _cv2e.split(lab)
        clahe = _cv2e.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        l     = clahe.apply(l)
        img   = _cv2e.cvtColor(_cv2e.merge([l, a, b]), _cv2e.COLOR_LAB2BGR)

        ext = os.path.splitext(output_path)[1] or '.jpg'
        ok, enc = _cv2e.imencode(ext, img, [_cv2e.IMWRITE_JPEG_QUALITY, 95])
        if ok:
            with open(output_path, 'wb') as f:
                f.write(enc.tobytes())
            print(f"  [Enhance] sharpen={sharpen} denoise={denoise} brightness={brightness}")
            return output_path
    except Exception as e:
        print(f"  [Enhance] skipped: {e}")
    shutil.copy2(image_path, output_path)
    return output_path


# ============================================================================
# CHROMA KEY — green/blue screen removal
# ============================================================================
def apply_chroma_key(image_path: str, output_path: str,
                     color: str = "green", tolerance: int = 40) -> str:
    """Remove chroma key (green/blue screen) from image, output PNG with transparency."""
    try:
        import cv2 as _cv2k
        import numpy as _npk
        raw = open(image_path, 'rb').read()
        img = _cv2k.imdecode(_npk.frombuffer(raw, _npk.uint8), _cv2k.IMREAD_COLOR)
        if img is None:
            shutil.copy2(image_path, output_path)
            return output_path

        hsv = _cv2k.cvtColor(img, _cv2k.COLOR_BGR2HSV)
        tol = max(10, min(80, int(tolerance)))

        if color == "green":
            lo = _npk.array([35, 50, 50]);  hi = _npk.array([85, 255, 255])
        elif color == "blue":
            lo = _npk.array([90, 50, 50]);  hi = _npk.array([130, 255, 255])
        elif color == "red":
            mask1 = _cv2k.inRange(hsv, _npk.array([0, 50, 50]), _npk.array([10, 255, 255]))
            mask2 = _cv2k.inRange(hsv, _npk.array([160, 50, 50]), _npk.array([180, 255, 255]))
            lo = hi = None
        else:
            lo = _npk.array([35, 50, 50]);  hi = _npk.array([85, 255, 255])

        mask = _cv2k.inRange(hsv, lo, hi) if lo is not None else (mask1 | mask2)

        # Erode slightly to remove fringing
        kernel = _npk.ones((3, 3), _npk.uint8)
        mask = _cv2k.erode(mask, kernel, iterations=1)
        mask = _cv2k.GaussianBlur(mask, (5, 5), 0)

        # BGRA with alpha channel
        bgra = _cv2k.cvtColor(img, _cv2k.COLOR_BGR2BGRA)
        bgra[:, :, 3] = _cv2k.bitwise_not(mask)

        out_png = output_path.rsplit('.', 1)[0] + '.png'
        ok, enc = _cv2k.imencode('.png', bgra)
        if ok:
            with open(out_png, 'wb') as f:
                f.write(enc.tobytes())
            print(f"  [Chroma] {color} key removed → {os.path.basename(out_png)}")
            return out_png
    except Exception as e:
        print(f"  [Chroma] failed: {e}")
    shutil.copy2(image_path, output_path)
    return output_path


# ============================================================================
# SUBTITLE TRANSLATION (Whisper SRT → target language)
# ============================================================================
def translate_srt(srt_content: str, target_lang: str) -> str:
    """Translate SRT subtitles to target language using deep-translator."""
    if not srt_content.strip() or not target_lang or target_lang in ("", "auto"):
        return srt_content
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source="auto", target=target_lang)
        lines  = srt_content.split('\n')
        result = []
        for line in lines:
            # Only translate non-empty, non-timecode, non-index lines
            stripped = line.strip()
            is_index    = stripped.isdigit()
            is_timecode = '-->' in stripped
            if stripped and not is_index and not is_timecode:
                try:
                    translated = translator.translate(stripped)
                    result.append(translated if translated else line)
                except Exception:
                    result.append(line)
            else:
                result.append(line)
        return '\n'.join(result)
    except ImportError:
        print("  [Translate] deep-translator not installed")
        return srt_content
    except Exception as e:
        print(f"  [Translate] failed: {e}")
        return srt_content


# ============================================================================
# TEMPLATE VARIABLES — {nome}, {empresa} etc. in scripts
# ============================================================================
def apply_template_variables(text: str, variables: dict) -> str:
    """Replace {key} placeholders in text with values from variables dict."""
    for key, value in variables.items():
        text = text.replace(f"{{{key}}}", str(value))
        text = text.replace(f"{{{key.upper()}}}", str(value))
    return text


# ============================================================================
# AI HELPER (Groq — own module, independent from core/)
# ============================================================================
def _get_groq_key():
    import base64
    keys_path = os.path.join(BASE_DIR, "..", ".api_keys.json")
    keys_path = os.path.normpath(keys_path)
    if os.path.exists(keys_path):
        try:
            with open(keys_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("groq", "")
            try:
                return base64.b64decode(raw.encode()).decode().strip()
            except Exception:
                return raw.strip()
        except Exception:
            pass
    return ""

def ask_groq(prompt: str, max_tokens: int = 2048, model: str = "llama-3.3-70b-versatile") -> str:
    """Call Groq API (own implementation, independent from core/)."""
    key = _get_groq_key()
    if not key:
        return "[Error: Groq API key not configured]"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": max_tokens,
    })
    # Try requests first (cleaner), fall back to urllib
    try:
        import requests as _req
        resp = _req.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "User-Agent": "AvatarPilot/2.0"},
            data=payload, timeout=30
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        return f"[AI Error {resp.status_code}: {resp.text[:200]}]"
    except ImportError:
        pass
    # urllib fallback
    import urllib.request, urllib.error
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload.encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "User-Agent": "AvatarPilot/2.0"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return f"[AI Error {e.code}: {body}]"
    except Exception as ex:
        return f"[AI Error: {ex}]"

# ============================================================================
# FULL PIPELINE
# ============================================================================

def _safe_pipeline_runner(job_id: str, config: dict):
    """Run pipeline in this thread with full exception isolation.
    Catches ALL exceptions so Flask server never dies from a pipeline crash.
    GPU OOM crashes are caught via the try/except around run_pipeline."""
    global active_workers
    try:
        run_pipeline(job_id, config)
    except SystemExit as e:
        print(f"  [SafeRunner] SystemExit in pipeline {job_id[:8]}: {e}", flush=True)
        try:
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id].update({"status": "error", "error": f"Pipeline aborted: {e}", "progress": 0, "message": "Pipeline aborted"})
            db_save_job(job_id, jobs.get(job_id, {}))
        except Exception: pass
    except MemoryError as e:
        print(f"  [SafeRunner] MemoryError (OOM) in pipeline {job_id[:8]}", flush=True)
        try:
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id].update({"status": "error", "error": "Memoria insuficiente (OOM). Tente um video mais curto.", "progress": 0, "message": "Erro: memoria insuficiente"})
            db_save_job(job_id, jobs.get(job_id, {}))
        except Exception: pass
    except Exception as e:
        print(f"  [SafeRunner] Exception in pipeline {job_id[:8]}: {e}", flush=True)
        try:
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id].update({"status": "error", "error": str(e), "progress": 0, "message": "Erro no pipeline"})
            db_save_job(job_id, jobs.get(job_id, {}))
        except Exception: pass
    finally:
        # Always release worker slot regardless of outcome
        try:
            with workers_lock:
                active_workers = max(0, active_workers - 1)
        except Exception: pass
        # Always release semaphore (run_pipeline may not have done it if it crashed early)
        try: release_vram()
        except Exception: pass


def run_pipeline(job_id: str, config: dict):
    """Run full avatar generation pipeline with plan limits."""
    global active_workers
    # Hard concurrency limit — block here if MAX_WORKERS already busy.
    # Prevents VRAM/CPU oversubscription when many users submit at once.
    # Job stays "queued" in DB; user polling sees "queued" status until slot opens.
    _semaphore_acquired = False
    try:
        # Poll-based concurrency: count ACTUALLY RUNNING jobs from dict.
        # A job is "running" if status is generating_audio/tts/generating_video/compositing.
        # This is immune to orphan active_workers counter issues.
        _wait_start = time.time()
        _RUNNING_STATUSES = {"generating_audio", "tts", "generating_video", "compositing"}
        while True:
            with jobs_lock:
                _running = sum(1 for jid, j in jobs.items()
                              if jid != job_id and j.get("status") in _RUNNING_STATUSES)
            if _running < MAX_WORKERS:
                break  # slot available
            with jobs_lock:
                if job_id in jobs and jobs[job_id].get("status") == "queued":
                    jobs[job_id]["message"] = f"Aguardando vaga (max {MAX_WORKERS} jobs, {_running} rodando)..."
            time.sleep(2)
            # Safety: if waiting > 2min, break anyway (orphan recovery)
            if time.time() - _wait_start > 120:
                print(f"  [Pipeline] Forced start after 2min wait for job {job_id[:8]}", flush=True)
                break
        _semaphore_acquired = True  # kept for compatibility with finally block

        # Check if user cancelled while we were waiting
        with jobs_lock:
            if job_id in jobs and jobs[job_id].get("_cancel"):
                jobs[job_id]["status"]  = "cancelled"
                jobs[job_id]["message"] = "Cancelado pelo usuário antes de iniciar."
                return  # finally releases semaphore

        with workers_lock:
            active_workers += 1
        plan = config.get("plan", DEFAULT_PLAN)

        # ── Pre-flight: disk space guard ───────────────────────────────────────
        _disk_free = _free_disk_mb()
        if _disk_free < DISK_BLOCK_MB:
            raise Exception(f"Disco cheio — apenas {_disk_free:.0f}MB livre. Libere espaço e tente novamente.")
        if _disk_free < DISK_WARN_MB:
            print(f"  [Warning] Disco quase cheio: {_disk_free:.0f}MB livres")

        # ── Step 0: Template variable substitution (before TTS) ────────────
        _check_cancel(job_id)
        tpl_vars = config.get("template_vars", {})
        if tpl_vars and config.get("script"):
            config["script"] = apply_template_variables(config["script"], tpl_vars)

        # ── Step 1: Generate Audio ─────────────────────────────────────────
        jobs[job_id]["status"]   = "generating_audio"
        jobs[job_id]["progress"] = 10
        jobs[job_id]["message"]  = "Generating voice audio..."

        audio_path = os.path.join(OUTPUT_DIR, f"{job_id}_audio.mp3")

        if config.get("audio_upload"):
            audio_path = config["audio_upload"]
            jobs[job_id]["progress"] = 25
        elif config.get("tts_engine") == "elevenlabs":
            settings = load_settings()
            elevenlabs_generate_v2(
                config["script"],
                config.get("voice_id", "21m00Tcm4TlvDq8ikWAM"),
                settings.get("elevenlabs_key", ""),
                audio_path,
            )
            jobs[job_id]["progress"] = 25
        elif config.get("tts_engine") == "f5_tts" and config.get("voice_ref_audio"):
            # F5-TTS: clone voice from a reference 5-30s audio sample (SOTA naturalness)
            jobs[job_id]["message"] = "F5-TTS: clonando voz da amostra..."
            f5_tts_generate(
                config["script"],
                config["voice_ref_audio"],
                audio_path,
                ref_text=config.get("voice_ref_text", ""),
            )
            jobs[job_id]["progress"] = 25
        else:
            voice = config.get("voice", "")
            # Auto-detect language if voice is not specified
            if not voice or voice == "auto":
                voice = auto_detect_voice(config.get("script", ""))
                print(f"  [AutoVoice] Detected voice: {voice}")
            # Apply voice preset rate/pitch if set
            rate  = config.get("voice_rate", "+0%")
            pitch = config.get("voice_pitch", "+0Hz")
            # Voice fallback list: same locale alternatives if primary voice fails
            _voice_fallbacks = {
                "pt-BR": ["pt-BR-AntonioNeural", "pt-BR-FranciscaNeural", "pt-BR-ThalitaNeural"],
                "pt-PT": ["pt-PT-DuarteNeural", "pt-PT-RaquelNeural"],
                "en-US": ["en-US-GuyNeural", "en-US-AriaNeural", "en-US-JennyNeural"],
                "es-MX": ["es-MX-JorgeNeural", "es-MX-DaliaNeural"],
                "es-ES": ["es-ES-AlvaroNeural", "es-ES-ElviraNeural"],
            }
            _locale = voice.rsplit("-", 1)[0] if "-" in voice else "en-US"
            _candidates = [voice] + [v for v in _voice_fallbacks.get(_locale, []) if v != voice]
            _last_err = None
            for _v in _candidates:
                try:
                    edge_tts_generate_advanced(
                        config["script"], _v, audio_path, rate=rate, pitch=pitch
                    )
                    if _v != voice:
                        print(f"  [VoiceFallback] Voz '{voice}' falhou, usando '{_v}' (mesmo idioma)")
                    voice = _v  # store final voice that worked
                    break
                except Exception as _ve:
                    _last_err = _ve
                    print(f"  [VoiceFallback] {_v} falhou: {_ve}")
            else:
                # All voices failed
                raise Exception(
                    f"Nenhuma voz do idioma {_locale} funcionou. Último erro: {_last_err}. "
                    f"Tente reduzir o texto ou verificar sua conexão."
                )
            jobs[job_id]["progress"] = 25

        # ── Step 1b: Audio normalization ───────────────────────────────────
        if config.get("normalize_audio", False):
            jobs[job_id]["message"] = "Normalizing audio levels..."
            norm_path = os.path.join(OUTPUT_DIR, f"{job_id}_audio_norm.mp3")
            normalize_audio(audio_path, norm_path)
            if os.path.exists(norm_path) and os.path.getsize(norm_path) > 1000:
                audio_path = norm_path

        # ── Plan limit check ───────────────────────────────────────────────
        dur   = _get_duration_safe(audio_path)
        limit = PLAN_LIMITS.get(plan, None)
        ok    = (limit is None) or (dur <= limit)
        jobs[job_id]["audio_duration"] = round(dur, 1)
        if not ok:
            limit_min = int(limit // 60)
            dur_min   = round(dur / 60, 1)
            raise Exception(
                f"Audio duration {dur_min} min exceeds your plan limit of {limit_min} min. "
                f"Upgrade your plan or shorten the script."
            )

        # ── Step 1c: Image enhancement ─────────────────────────────────────
        if config.get("enhance_image", False):
            jobs[job_id]["message"] = "Enhancing avatar image..."
            enh_path = os.path.join(UPLOAD_DIR, f"enh_{job_id}.jpg")
            enhance_image(config["image_path"], enh_path)
            if os.path.exists(enh_path) and os.path.getsize(enh_path) > 1000:
                config["image_path"] = enh_path

        # ── Step 1d: Chroma key on source image ────────────────────────────
        if config.get("chroma_key"):
            jobs[job_id]["message"] = "Applying chroma key to avatar..."
            chroma_out = os.path.join(UPLOAD_DIR, f"chroma_{job_id}.png")
            try:
                apply_chroma_key(
                    config["image_path"], chroma_out,
                    color=config["chroma_key"],
                    tolerance=config.get("chroma_tolerance", 40)
                )
                if os.path.exists(chroma_out) and os.path.getsize(chroma_out) > 1000:
                    config["image_path"] = chroma_out
            except Exception as _ce:
                print(f"  [Chroma] warning: {_ce}")

        # ── Step 2: Lip Sync — engine auto-selecionado por tipo de entrada ─────
        # Vídeo → Wav2Lip (preserva corpo/mãos do vídeo original) + GFPGAN
        # Foto  → SadTalker full preprocess (movimento natural de cabeça/pescoço) + GFPGAN interno
        # Premium: EchoMimic v2 via Replicate (gestos reais com mãos, HeyGen-class)
        _check_cancel(job_id)
        jobs[job_id]["status"]   = "generating_video"
        jobs[job_id]["progress"] = 35
        avatar_video = os.path.join(OUTPUT_DIR, f"{job_id}_avatar.mp4")

        _input_is_video = _is_video_file(config["image_path"])
        _gesture_video  = config.get("gesture_video", "")  # path do vídeo de gestos selecionado

        # ── PREMIUM MODE — EchoMimic v2 (HeyGen-class half-body w/ gestures) ──
        # Prefers local install (100% free). Falls back to Replicate cloud if local
        # not installed AND user provided a replicate_key. Falls back to local
        # SadTalker pipeline if both fail.
        _echomimic_done = False
        if config.get("engine") == "echomimic_v2" and not _input_is_video:
            _echo_settings = config.get("echomimic_settings", {})
            _echo_runner   = None
            if check_echomimic_v2():
                _echo_runner = ("local", run_echomimic_v2_local)
                jobs[job_id]["message"] = "EchoMimic V2 (local): gerando gestos com mãos..."
            elif load_settings().get("replicate_key"):
                _echo_runner = ("cloud", run_echomimic_v2_replicate)
                jobs[job_id]["message"] = "EchoMimic V2 (cloud): gerando gestos com mãos..."
            jobs[job_id]["progress"] = 38

            if _echo_runner:
                _ek_label, _ek_fn = _echo_runner
                try:
                    _ek_fn(
                        config["image_path"], audio_path, avatar_video,
                        settings=_echo_settings, job_id=job_id,
                    )
                    if not os.path.exists(avatar_video) or os.path.getsize(avatar_video) < 10000:
                        raise Exception("EchoMimic v2 output missing")
                    _av_ok, _av_reason = _validate_video_output(avatar_video, expected_dur=dur)
                    if not _av_ok:
                        raise Exception(f"EchoMimic v2 output inválido: {_av_reason}")
                    _echomimic_done = True
                    jobs[job_id]["progress"] = 72
                    print(f"  [Pipeline] EchoMimic V2 ({_ek_label}) OK — bypass local lip sync stack")
                except Exception as _echo_err:
                    print(f"  [EchoMimic v2 {_ek_label}] Falhou ({_echo_err}) — fallback pipeline padrão")
                    jobs[job_id]["message"] = "EchoMimic indisponível — pipeline local padrão"
                    if os.path.exists(avatar_video): _safe_rm(avatar_video)
            else:
                print("  [EchoMimic v2] não instalado localmente nem replicate_key — fallback pipeline padrão")

        # Pre-resize: cap input image at 720px max to keep downstream lip sync + GFPGAN fast.
        # High-res images (1080p+) make Wav2Lip and GFPGAN take 5-10x longer with no quality gain
        # since the final HD encode produces 1280x720 anyway.
        if not _input_is_video:
            config["image_path"] = _ensure_max_dim(config["image_path"], 720, prefix="pre")

        # ── MODO GESTURE TEMPLATE — corpo inteiro com gestos reais + face swap ──
        if not _echomimic_done and not _input_is_video and _gesture_video and os.path.exists(_gesture_video):
            jobs[job_id]["message"] = f"Gesture Mode: trocando rosto no vídeo de gestos..."
            jobs[job_id]["progress"] = 38
            try:
                # 1. Face swap do usuário no vídeo de gestos
                _fswap_out = os.path.join(OUTPUT_DIR, f"{job_id}_fswap.mp4")
                swap_face_on_gesture_video(
                    config["image_path"], _gesture_video, _fswap_out, job_id=job_id
                )
                if not os.path.exists(_fswap_out) or os.path.getsize(_fswap_out) < 10000:
                    raise Exception("Face swap no gesture video falhou")

                # 2. Lip sync no vídeo face-swapped (MuseTalk preferido, Wav2Lip fallback)
                jobs[job_id]["progress"] = 60
                if check_musetalk():
                    jobs[job_id]["message"] = f"MuseTalk: lip sync no gesture video ({dur:.0f}s)..."
                    try:
                        run_musetalk_chunked(_fswap_out, audio_path, avatar_video, settings={}, chunk_duration=300, job_id=job_id)
                    except Exception as _g_mst_err:
                        print(f"  [MuseTalk] Gesture falhou ({_g_mst_err}) — fallback para Wav2Lip")
                        run_wav2lip_chunked(_fswap_out, audio_path, avatar_video, settings={}, chunk_duration=300, job_id=job_id)
                else:
                    jobs[job_id]["message"] = f"Wav2Lip: sincronizando lábios no gesture video ({dur:.0f}s)..."
                    run_wav2lip_chunked(
                        _fswap_out, audio_path, avatar_video,
                        settings={}, chunk_duration=300, job_id=job_id
                    )
                _safe_rm(_fswap_out)

                # 3. GFPGAN para qualidade final
                if os.path.exists(avatar_video) and os.path.getsize(avatar_video) > 10000:
                    jobs[job_id]["progress"] = 72
                    gfpgan_out = os.path.join(OUTPUT_DIR, f"{job_id}_gfpgan.mp4")
                    apply_gfpgan_chunked(avatar_video, gfpgan_out, job_id=job_id, max_gfpgan_seconds=300, enhance_face=config.get("enhance_face", True))
                    if os.path.exists(gfpgan_out) and os.path.getsize(gfpgan_out) > 10000:
                        _safe_rm(avatar_video)
                        _safe_rename(gfpgan_out, avatar_video)
                print(f"  [Gesture] Pipeline concluído com gestos reais")
            except Exception as _gest_err:
                print(f"  [Gesture] Falhou ({_gest_err}) — fallback para SadTalker")
                jobs[job_id]["message"] = f"SadTalker (fallback): gerando movimento ({dur:.0f}s)..."
                _input_is_video = False  # cair no path SadTalker abaixo
                _gesture_video  = ""

        if not _echomimic_done and not _gesture_video and not _input_is_video:
            # ── IMAGE input via SadTalker (≤90s) or Wav2Lip (>90s) ──────────
            _sadtalker_ok = check_sadtalker()
            if _sadtalker_ok and dur <= 90:
                jobs[job_id]["message"] = f"SadTalker: gerando movimento natural ({dur:.0f}s)..."
                # size=256 is ~3x faster than 512 with comparable quality for web output.
                # GFPGAN runs separately via apply_gfpgan_chunked — no internal enhancer needed.
                _st_size = 256
                _st_expr = 1.4 if dur <= 90 else 1.2
                st_settings = {
                    "preprocess":       "full",
                    "still":            False,
                    "expression_scale": _st_expr,
                    "enhancer":         "none",
                    "size":             _st_size,
                }
                # ~2.5s per second of audio with size=256, no GFPGAN inside SadTalker
                _st_est_sec = dur * 2.5
                _st_done    = threading.Event()

                def _sadtalker_progress_ticker():
                    """Incrementa progress 35→68% durante SadTalker para não parecer travado."""
                    _t0 = time.time()
                    while not _st_done.is_set():
                        elapsed = time.time() - _t0
                        pct = min(68, 35 + int(33 * elapsed / max(_st_est_sec, 10)))
                        if jobs.get(job_id, {}).get("status") == "generating_video":
                            jobs[job_id]["progress"] = pct
                        _st_done.wait(timeout=3)

                _ticker = Thread(target=_sadtalker_progress_ticker, daemon=True)
                _ticker.start()
                try:
                    run_sadtalker_chunked(
                        config["image_path"], audio_path, avatar_video,
                        settings=st_settings, chunk_duration=270, job_id=job_id
                    )
                    _st_done.set()
                    if not os.path.exists(avatar_video) or os.path.getsize(avatar_video) < 10000:
                        raise Exception("SadTalker output missing or empty")
                    jobs[job_id]["progress"] = 68
                    # MuseTalk: re-run lip sync on SadTalker output for HeyGen-quality result
                    if check_musetalk():
                        try:
                            jobs[job_id]["message"] = "MuseTalk: lip sync de alta qualidade..."
                            _mst_short_out = os.path.join(OUTPUT_DIR, f"{job_id}_musetalk.mp4")
                            run_musetalk(avatar_video, audio_path, _mst_short_out, job_id=None)
                            if os.path.exists(_mst_short_out) and os.path.getsize(_mst_short_out) > 10000:
                                _safe_rm(avatar_video)
                                _safe_rename(_mst_short_out, avatar_video)
                                print("  [MuseTalk] Short-clip lip sync OK")
                        except Exception as _mst_s_err:
                            print(f"  [MuseTalk] Short-clip falhou ({_mst_s_err}) — mantendo SadTalker")
                    jobs[job_id]["progress"] = 70
                    # TEMPORAL CONSISTENCY: eliminate frame-to-frame flicker from AI generation
                    jobs[job_id]["message"] = "Temporal consistency: eliminando flicker..."
                    _tc_out = os.path.join(OUTPUT_DIR, f"{job_id}_temporal.mp4")
                    _tc_vf = "tmix=frames=3:weights='1 2 1',deflicker=size=5:mode=am"
                    _tc_cmd = [_ffmpeg_path(), "-y", "-i", avatar_video,
                               "-vf", _tc_vf,
                               "-c:v", "libx264", "-crf", "17", "-preset", "fast",
                               "-c:a", "copy",
                               _tc_out]
                    try:
                        _tc_r = subprocess.run(_tc_cmd, capture_output=True, timeout=max(120, int(dur*3)))
                        if os.path.exists(_tc_out) and os.path.getsize(_tc_out) > 10000:
                            _safe_rm(avatar_video)
                            _safe_rename(_tc_out, avatar_video)
                            print(f"  [Temporal] Anti-flicker applied (tmix+deflicker)")
                        else:
                            print(f"  [Temporal] Skipped (output invalid)")
                    except Exception as _tc_err:
                        print(f"  [Temporal] Skipped ({_tc_err})")
                    # GFPGAN externo pós-SadTalker (auto-adapta frames pelo tamanho)
                    jobs[job_id]["message"] = "GFPGAN: restaurando qualidade facial..."
                    _st_gfpgan_out = os.path.join(OUTPUT_DIR, f"{job_id}_gfpgan.mp4")
                    apply_gfpgan_chunked(avatar_video, _st_gfpgan_out, job_id=job_id, max_gfpgan_seconds=600, enhance_face=config.get("enhance_face", True))
                    if os.path.exists(_st_gfpgan_out) and os.path.getsize(_st_gfpgan_out) > 10000:
                        _safe_rm(avatar_video)
                        _safe_rename(_st_gfpgan_out, avatar_video)
                    # CODEFORMER PASS: identity-preserving texture refinement after GFPGAN
                    if dur <= 120:  # CodeFormer for clips <= 2min (GPU intensive)
                        try:
                            jobs[job_id]["message"] = "CodeFormer: refinando textura facial..."
                            _cf_out = os.path.join(OUTPUT_DIR, f"{job_id}_codeformer.mp4")
                            apply_codeformer_to_video(avatar_video, _cf_out, job_id=job_id, fidelity_weight=0.7)
                            if os.path.exists(_cf_out) and os.path.getsize(_cf_out) > 10000:
                                _safe_rm(avatar_video)
                                _safe_rename(_cf_out, avatar_video)
                                print("  [CodeFormer] Texture refinement applied (fidelity=0.7)")
                            else:
                                print("  [CodeFormer] Skipped (output invalid)")
                        except Exception as _cf_err:
                            print(f"  [CodeFormer] Skipped ({_cf_err})")
                    # FACE EDGE SOFTENING: smooth face boundaries with smartblur
                    jobs[job_id]["message"] = "Face blending: suavizando bordas faciais..."
                    _fb_out = os.path.join(OUTPUT_DIR, f"{job_id}_faceblend.mp4")
                    _fb_vf = "smartblur=lr=1.0:ls=-0.9:lt=-5:cr=0.8:cs=-0.8:ct=-4"
                    _fb_cmd = [_ffmpeg_path(), "-y", "-i", avatar_video,
                               "-vf", _fb_vf,
                               "-c:v", "libx264", "-crf", "17", "-preset", "fast",
                               "-c:a", "copy",
                               _fb_out]
                    try:
                        _fb_r = subprocess.run(_fb_cmd, capture_output=True, timeout=max(60, int(dur*2)))
                        if os.path.exists(_fb_out) and os.path.getsize(_fb_out) > 10000:
                            _safe_rm(avatar_video)
                            _safe_rename(_fb_out, avatar_video)
                            print("  [FaceBlend] Edge softening applied")
                        else:
                            print("  [FaceBlend] Skipped (output invalid)")
                    except Exception as _fb_err:
                        print(f"  [FaceBlend] Skipped ({_fb_err})")
                    # Validar output do SadTalker+GFPGAN+CodeFormer+FaceBlend antes do body sway
                    _sg_ok, _sg_reason = _validate_video_output(avatar_video, expected_dur=dur)
                    print(f"  [Pipeline] SadTalker+GFPGAN validação: {_sg_reason}")
                    # Fix A/V sync após SadTalker (pode ter pequeno drift)
                    if _sg_ok:
                        _sync_st = os.path.join(OUTPUT_DIR, f"{job_id}_sync.mp4")
                        if _fix_av_sync(avatar_video, _sync_st):
                            _safe_rm(avatar_video)
                            _safe_rename(_sync_st, avatar_video)
                    # Body sway: adiciona movimento natural de respiração/balanço
                    if _sg_ok and os.path.exists(avatar_video) and os.path.getsize(avatar_video) > 10000:
                        jobs[job_id]["message"] = "Body sway: adicionando movimento corporal natural..."
                        _sway_out = os.path.join(OUTPUT_DIR, f"{job_id}_sway.mp4")
                        add_body_sway_to_video(avatar_video, _sway_out, intensity=1.0)
                        if os.path.exists(_sway_out) and os.path.getsize(_sway_out) > 10000:
                            _safe_rm(avatar_video)
                            _safe_rename(_sway_out, avatar_video)
                except Exception as _st_err:
                    _st_done.set()
                    print(f"  [SadTalker] Falhou ({_st_err}) — fallback para Wav2Lip")
                    jobs[job_id]["message"] = f"Wav2Lip (fallback): sincronizando lábios ({dur:.0f}s)..."
                    # Resize image to max 720px before Wav2Lip — high-res input makes GFPGAN extremely slow downstream
                    _fb_img = _ensure_max_dim(config["image_path"], 720, prefix="w2lfb")
                    run_wav2lip_chunked(
                        _fb_img, audio_path, avatar_video,
                        settings={}, chunk_duration=300, job_id=job_id
                    )
                    # Validar output do fallback
                    _fok, _freason = _validate_video_output(avatar_video, expected_dur=dur)
                    if not _fok:
                        raise Exception(f"Wav2Lip fallback falhou: {_freason}")
                    print(f"  [Fallback] Wav2Lip OK: {_freason}")
                    # GFPGAN leve para qualidade do fallback
                    if os.path.exists(avatar_video) and os.path.getsize(avatar_video) > 10000:
                        gfpgan_out = os.path.join(OUTPUT_DIR, f"{job_id}_gfpgan.mp4")
                        apply_gfpgan_chunked(avatar_video, gfpgan_out, job_id=job_id, max_gfpgan_seconds=180, enhance_face=config.get("enhance_face", True))
                        if os.path.exists(gfpgan_out) and os.path.getsize(gfpgan_out) > 10000:
                            _safe_rm(avatar_video); _safe_rename(gfpgan_out, avatar_video)
                    # A/V sync fix
                    _fb_sync = os.path.join(OUTPUT_DIR, f"{job_id}_sync.mp4")
                    if _fix_av_sync(avatar_video, _fb_sync):
                        _safe_rm(avatar_video); _safe_rename(_fb_sync, avatar_video)
                    # Body sway (igual ao caminho SadTalker)
                    if os.path.exists(avatar_video) and os.path.getsize(avatar_video) > 10000:
                        _fb_sway = os.path.join(OUTPUT_DIR, f"{job_id}_sway.mp4")
                        add_body_sway_to_video(avatar_video, _fb_sway, intensity=1.0)
                        if os.path.exists(_fb_sway) and os.path.getsize(_fb_sway) > 10000:
                            _safe_rm(avatar_video); _safe_rename(_fb_sway, avatar_video)

                # Validar output final do SadTalker path
                _av_ok, _av_reason = _validate_video_output(avatar_video, expected_dur=dur)
                print(f"  [Pipeline] Avatar video validação: {_av_reason}")
                if not _av_ok:
                    raise Exception(f"Avatar video inválido após SadTalker/Wav2Lip: {_av_reason}")

            else:
                # Long clips (>90s) — Quality strategy in order of preference:
                # 1. GESTURE PACK: if templates exist in static/gesture_videos/, use them.
                #    Each chunk uses a different template → real human body + hand gestures,
                #    HeyGen-class output. Face swap with InsightFace + lip sync with MuseTalk.
                # 2. SADTALKER LOOP: 60s SadTalker base + bounce loop. Talking head only.
                #    Used when no gesture pack available.
                _ffmpeg_bin = _ffmpeg_path()
                _base_video = ""
                _loop_dir = ""
                _gesture_lipsync_done = False

                # Try gesture pack first (HeyGen-quality path)
                _gesture_pack = _list_gesture_templates()
                _user_gesture = config.get("gesture_video", "")
                if _user_gesture and os.path.exists(_user_gesture):
                    _gesture_pack = [_user_gesture] + _gesture_pack
                if _gesture_pack and check_face_swap_ready():
                    try:
                        import tempfile as _lptmp
                        _loop_dir = _lptmp.mkdtemp(prefix="avp_loop_")
                        jobs[job_id]["message"] = f"Gesture Pack: {len(_gesture_pack)} templates disponíveis. Montando..."
                        _seq_video = os.path.join(_loop_dir, "sequence.mp4")
                        _build_long_gesture_sequence(dur, _gesture_pack, _seq_video, job_id=job_id)
                        # NEW ORDER: lip sync FIRST on original actor face (Wav2Lip detects it fine),
                        # then face swap. This fixes Wav2Lip code-1 failures on swapped faces.
                        jobs[job_id]["message"] = f"MuseTalk/Wav2Lip: sincronizando labios no video original ({dur:.0f}s)..."
                        _lip_synced = os.path.join(_loop_dir, "lip_synced.mp4")
                        _ls_ok = False
                        # Try MuseTalk first (best quality), fallback to Wav2Lip.
                        # IMPORTANTE: em 8GB VRAM o MuseTalk (difusão latente) TRAVA sem
                        # progresso em sequências de gesture longas (>~120s) — o watchdog
                        # mata o job após 60min. Como o hang não lança exceção, o fallback
                        # nunca dispara. Por isso, acima de 120s vamos DIRETO p/ Wav2Lip
                        # (rápido, baixa VRAM) — alinhado com o fix 99bee04.
                        _musetalk_gesture_ok = check_musetalk() and dur <= 120
                        if _musetalk_gesture_ok:
                            try:
                                run_musetalk_chunked(
                                    _seq_video, audio_path, _lip_synced,
                                    settings={}, chunk_duration=300, job_id=job_id
                                )
                                _ls_v, _ls_r = _validate_video_output(_lip_synced, expected_dur=dur)
                                if _ls_v:
                                    _ls_ok = True
                                    print(f"  [Pipeline] MuseTalk gesture lip sync OK", flush=True)
                            except Exception as _mst_gs_e:
                                print(f"  [Pipeline] MuseTalk gesture falhou ({_mst_gs_e}) -> Wav2Lip", flush=True)
                        if not _ls_ok:
                            try:
                                run_wav2lip_chunked(
                                    _seq_video, audio_path, _lip_synced,
                                    settings={}, chunk_duration=300, job_id=job_id
                                )
                                _ls_v2, _ls_r2 = _validate_video_output(_lip_synced, expected_dur=dur)
                                if _ls_v2:
                                    _ls_ok = True
                                    print(f"  [Pipeline] Wav2Lip gesture lip sync OK", flush=True)
                            except Exception as _w2l_gs_e:
                                print(f"  [Pipeline] Wav2Lip gesture falhou ({_w2l_gs_e}) -> usando seq original", flush=True)
                        _face_src_for_swap = _lip_synced if (_ls_ok and os.path.exists(_lip_synced)) else _seq_video
                        # NOW face swap (on lip-synced or original sequence)
                        jobs[job_id]["message"] = "Face Swap: aplicando rosto no video lip-synced..."
                        _swapped = os.path.join(_loop_dir, "swapped.mp4")
                        swap_face_on_gesture_video(
                            config["image_path"], _face_src_for_swap, _swapped, job_id=job_id
                        )
                        if os.path.exists(_swapped) and os.path.getsize(_swapped) > 100_000:
                            _base_video = _swapped
                            _gesture_lipsync_done = True  # skip second lip sync step
                            print(f"  [Pipeline] Gesture pack OK ({len(_gesture_pack)} tpls, lip_sync_first={_ls_ok}): {_swapped}")
                    except Exception as _gpe:
                        print(f"  [Pipeline] Gesture pack falhou ({_gpe}) — fallback SadTalker loop")
                        _base_video = ""

                # Fallback: SadTalker base loop (talking head only)
                if not _base_video and _sadtalker_ok:
                    import tempfile as _ltmp
                    _loop_dir = _ltmp.mkdtemp(prefix="avp_loop_")
                    try:
                        # Step 1: Generate ~60s SadTalker animation for natural body motion
                        _base_dur = min(60.0, dur)
                        jobs[job_id]["message"] = f"SadTalker: gerando animação base ({_base_dur:.0f}s de movimento)..."
                        _base_audio = os.path.join(_loop_dir, "base.wav")
                        _base_out   = os.path.join(_loop_dir, "base.mp4")

                        subprocess.run([
                            _ffmpeg_bin, "-y", "-i", audio_path,
                            "-t", str(round(_base_dur, 3)),
                            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", _base_audio
                        ], capture_output=True, timeout=60)

                        # SadTalker 512 → 4x more detail than 256, no perceptible quality loss
                        # The looped base becomes the source video for Wav2Lip/MuseTalk lip sync.
                        # Higher source = better final output (avoids "240p look" from stretching).
                        _st_base_settings = {
                            "preprocess": "full", "still": False,
                            "expression_scale": 1.4, "enhancer": "none", "size": 512,
                        }
                        run_sadtalker(config["image_path"], _base_audio, _base_out, _st_base_settings)

                        if os.path.exists(_base_out) and os.path.getsize(_base_out) > 10000:
                            # Step 2: Loop with REVERSE on alternate cycles for natural variety.
                            # Plain loop = visible repetition every 60s. Bounce (forward+reverse)
                            # doubles the perceived motion variety to 120s before repeating.
                            jobs[job_id]["message"] = f"Loop: expandindo animação para {dur:.0f}s..."
                            _looped = os.path.join(_loop_dir, "looped.mp4")
                            _reverse = os.path.join(_loop_dir, "reverse.mp4")
                            _bounce  = os.path.join(_loop_dir, "bounce.mp4")
                            # Build reversed clip
                            subprocess.run([
                                _ffmpeg_bin, "-y", "-i", _base_out,
                                "-vf", "reverse", "-an", _reverse
                            ], capture_output=True, timeout=180)
                            # Concat forward + reverse to make bounce loop (no abrupt cut)
                            _concat_txt = os.path.join(_loop_dir, "bc.txt")
                            with open(_concat_txt, "w") as _bf:
                                _bf.write(f"file '{_base_out}'\nfile '{_reverse}'\n")
                            subprocess.run([
                                _ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
                                "-i", _concat_txt, "-c", "copy", _bounce
                            ], capture_output=True, timeout=120)
                            _bounce_src = _bounce if os.path.exists(_bounce) and os.path.getsize(_bounce) > 10000 else _base_out
                            _loop_r = subprocess.run([
                                _ffmpeg_bin, "-y",
                                "-stream_loop", "-1", "-i", _bounce_src,
                                "-t", str(round(dur + 1.0, 3)),
                                "-c:v", "copy", "-an", _looped
                            ], capture_output=True, timeout=120)
                            if _loop_r.returncode == 0 and os.path.exists(_looped):
                                _base_video = _looped
                                print(f"  [Pipeline] Base animation bounced+looped: {_base_dur:.0f}s × loop → {dur:.0f}s")
                    except Exception as _base_err:
                        print(f"  [Pipeline] SadTalker base falhou ({_base_err}) — usando imagem estática")
                        _base_video = ""

                # Step 3: Lip sync on looped video (moving face) OR static image (fallback)
                # MuseTalk (primary, latent diffusion) → Wav2Lip (fallback)
                # For very long videos (>30min) MuseTalk on moving face would take 10-15h
                # on RTX 4060 — auto-skip to Wav2Lip which handles 1h in ~30min with good quality.
                _face_src = _base_video if _base_video else config["image_path"]
                _mode_label = "vídeo animado" if _base_video else "imagem estática"
                # Skip lip sync if already done in gesture-first path
                if _gesture_lipsync_done and _base_video and os.path.exists(_base_video):
                    # Mux audio onto face-swapped+lip-synced video and jump to GFPGAN
                    _mux_fb = os.path.join(OUTPUT_DIR, f"{job_id}_muxed.mp4")
                    _mux_r = subprocess.run([
                        _ffmpeg_path(), "-y", "-i", _base_video, "-i", audio_path,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-c:a", "aac", "-af", "highpass=f=80,lowpass=f=12000,acompressor=threshold=-20dB:ratio=3:attack=5:release=50", "-b:a", "256k", "-shortest", _mux_fb
                    ], capture_output=True, timeout=max(300, int(dur * 2)))
                    if _mux_r.returncode == 0 and os.path.exists(_mux_fb) and os.path.getsize(_mux_fb) > 10000:
                        shutil.copy2(_mux_fb, avatar_video)
                        _safe_rm(_mux_fb)
                    else:
                        shutil.copy2(_base_video, avatar_video)
                    _av_ok3, _av_reason3 = _validate_video_output(avatar_video, expected_dur=dur)
                    print(f"  [Pipeline] Gesture+LipSync+Swap direto: {_av_reason3}", flush=True)
                    if not _av_ok3:
                        raise Exception(f"Gesture pipeline falhou: {_av_reason3}")
                # Downscale face-swapped video to 720p before lip sync (MuseTalk/Wav2Lip OOM at 1080p on 8GB VRAM)
                if _base_video:
                    try:
                        import cv2 as _cvds
                        _ds_cap = _cvds.VideoCapture(_base_video)
                        _ds_w = int(_ds_cap.get(_cvds.CAP_PROP_FRAME_WIDTH))
                        _ds_h = int(_ds_cap.get(_cvds.CAP_PROP_FRAME_HEIGHT))
                        _ds_cap.release()
                        if max(_ds_w, _ds_h) > 720:
                            jobs[job_id]["message"] = f"Otimizando resolução do vídeo de gestos ({_ds_w}x{_ds_h} -> 720p)..."
                            _ds_out = _base_video.replace(".mp4", "_720p.mp4")
                            _ds_cmd = [_ffmpeg_path(), "-y", "-i", _base_video,
                                       "-vf", "scale='if(gt(iw,ih),min(1280,iw),-2)':'if(gt(iw,ih),-2,min(1280,ih))'",
                                       "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                                       "-pix_fmt", "yuv420p", "-an", _ds_out]
                            subprocess.run(_ds_cmd, capture_output=True, timeout=max(300, int(dur * 2)))
                            if os.path.exists(_ds_out) and os.path.getsize(_ds_out) > 10000:
                                _safe_rm(_base_video)
                                _base_video = _ds_out
                                _face_src = _ds_out
                                print(f"  [Pipeline] Downscaled face video to 720p OK")
                    except Exception as _dse:
                        print(f"  [Pipeline] Downscale falhou ({_dse}) — continuando com res original")

                # Skip MuseTalk in gesture-pack path: it OOMs on 8GB GPUs with multi-template
                # face-swapped video (1080p, hundreds of distinct face regions). Wav2Lip is
                # ~5-10x faster, handles arbitrary length, and lip-sync quality difference is
                # marginal on face-swapped footage where the body motion is real human.
                _skip_musetalk = bool(_base_video)  # always skip in long-clip path with video face
                if _skip_musetalk:
                    print(f"  [Pipeline] {dur:.0f}s gesture-pack — usando Wav2Lip (MuseTalk OOM em 8GB com video 1080p)")
                _mst_ok = check_musetalk() and not _skip_musetalk
                if _mst_ok:
                    jobs[job_id]["message"] = f"MuseTalk: lip sync em {_mode_label} ({dur:.0f}s)..."
                    print(f"  [Pipeline] MuseTalk em {_mode_label}")
                    try:
                        run_musetalk_chunked(
                            _face_src, audio_path, avatar_video,
                            settings={}, chunk_duration=300, job_id=job_id
                        )
                        _av_ok, _av_reason = _validate_video_output(avatar_video, expected_dur=dur)
                        if not _av_ok:
                            raise Exception(f"MuseTalk output inválido: {_av_reason}")
                    except Exception as _mst_err:
                        print(f"  [MuseTalk] Falhou ({_mst_err}) — fallback para Wav2Lip")
                        jobs[job_id]["message"] = f"Wav2Lip (fallback): lip sync em {_mode_label} ({dur:.0f}s)..."
                        run_wav2lip_chunked(_face_src, audio_path, avatar_video, settings={}, chunk_duration=300, job_id=job_id)
                        _av_ok, _av_reason = _validate_video_output(avatar_video, expected_dur=dur)
                        if not _av_ok:
                            raise Exception(f"Wav2Lip fallback falhou: {_av_reason}")
                else:
                    jobs[job_id]["message"] = f"Wav2Lip: sincronizando lábios em {_mode_label} ({dur:.0f}s)..."
                    print(f"  [Pipeline] Wav2Lip em {_mode_label}")
                    _w2l_gesture_ok = False
                    try:
                        run_wav2lip_chunked(
                            _face_src, audio_path, avatar_video,
                            settings={}, chunk_duration=300, job_id=job_id
                        )
                        _av_ok, _av_reason = _validate_video_output(avatar_video, expected_dur=dur)
                        if _av_ok:
                            _w2l_gesture_ok = True
                        else:
                            print(f"  [Pipeline] Wav2Lip output inválido ({_av_reason}) — usando face-swap direto", flush=True)
                    except Exception as _w2l_ge:
                        print(f"  [Pipeline] Wav2Lip falhou ({_w2l_ge}) — fallback: muxando áudio no face-swap", flush=True)
                    if not _w2l_gesture_ok:
                        # Graceful fallback: mux the audio onto the face-swapped base video
                        # The lips won't be perfectly synced, but the video is still HeyGen-quality
                        _fb_out = os.path.join(OUTPUT_DIR, f"{job_id}_fb.mp4")
                        _mux_r = subprocess.run([
                            _ffmpeg_path(), "-y", "-i", _face_src, "-i", audio_path,
                            "-map", "0:v:0", "-map", "1:a:0",
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
                            "-shortest", _fb_out
                        ], capture_output=True, timeout=max(300, int(dur * 2)))
                        if _mux_r.returncode == 0 and os.path.exists(_fb_out) and os.path.getsize(_fb_out) > 10000:
                            shutil.copy2(_fb_out, avatar_video)
                            _safe_rm(_fb_out)
                            print(f"  [Pipeline] Fallback mux OK — gesture+face_swap sem lip sync extra", flush=True)
                        else:
                            raise Exception(f"Wav2Lip e fallback mux falharam")
                    _av_ok, _av_reason = _validate_video_output(avatar_video, expected_dur=dur)
                    if not _av_ok:
                        raise Exception(f"Avatar video inválido após lip sync: {_av_reason}")

                # Step 4: GFPGAN (escala com duração)
                if os.path.exists(avatar_video) and os.path.getsize(avatar_video) > 10000:
                    _gfpgan_time = max(300, min(3600, int(dur * 1.5)))
                    jobs[job_id]["message"] = "GFPGAN: restaurando qualidade facial..."
                    gfpgan_out = os.path.join(OUTPUT_DIR, f"{job_id}_gfpgan.mp4")
                    apply_gfpgan_chunked(avatar_video, gfpgan_out, job_id=job_id, max_gfpgan_seconds=_gfpgan_time, enhance_face=config.get("enhance_face", True))
                    if os.path.exists(gfpgan_out) and os.path.getsize(gfpgan_out) > 10000:
                        _safe_rm(avatar_video)
                        _safe_rename(gfpgan_out, avatar_video)
                # A/V sync fix
                _w2l_sync = os.path.join(OUTPUT_DIR, f"{job_id}_sync.mp4")
                if _fix_av_sync(avatar_video, _w2l_sync):
                    _safe_rm(avatar_video); _safe_rename(_w2l_sync, avatar_video)
                # Body sway no topo do SadTalker loop — adiciona variação extra
                if os.path.exists(avatar_video) and os.path.getsize(avatar_video) > 10000:
                    jobs[job_id]["message"] = "Body sway: finalizando movimento corporal..."
                    _w2l_sway = os.path.join(OUTPUT_DIR, f"{job_id}_sway.mp4")
                    add_body_sway_to_video(avatar_video, _w2l_sway, intensity=0.5)
                    if os.path.exists(_w2l_sway) and os.path.getsize(_w2l_sway) > 10000:
                        _safe_rm(avatar_video); _safe_rename(_w2l_sway, avatar_video)
                # Validar output final
                _av_ok2, _av_reason2 = _validate_video_output(avatar_video, expected_dur=dur)
                print(f"  [Pipeline] Long-clip validação: {_av_reason2}")
                if _loop_dir:
                    shutil.rmtree(_loop_dir, ignore_errors=True)
                    _loop_dir = ""
                if not _av_ok2:
                    raise Exception(f"Avatar video inválido: {_av_reason2}")

        # ── VIDEO input — MuseTalk (primary) ou Wav2Lip (fallback) ──
        if not _echomimic_done and _input_is_video:
            if check_musetalk():
                jobs[job_id]["message"] = f"MuseTalk: lip sync no vídeo ({dur:.0f}s)..."
                try:
                    run_musetalk_chunked(
                        config["image_path"], audio_path, avatar_video,
                        settings={}, chunk_duration=300, job_id=job_id
                    )
                    _vid_ok, _vid_reason = _validate_video_output(avatar_video, expected_dur=dur)
                    print(f"  [Pipeline] Vídeo MuseTalk validação: {_vid_reason}")
                    if not _vid_ok:
                        raise Exception(f"MuseTalk inválido: {_vid_reason}")
                except Exception as _mst_vid_err:
                    print(f"  [MuseTalk] Vídeo falhou ({_mst_vid_err}) — fallback para Wav2Lip")
                    jobs[job_id]["message"] = f"Wav2Lip (fallback): sincronizando lábios no vídeo ({dur:.0f}s)..."
                    run_wav2lip_chunked(config["image_path"], audio_path, avatar_video, settings={}, chunk_duration=300, job_id=job_id)
                    _vid_ok, _vid_reason = _validate_video_output(avatar_video, expected_dur=dur)
                    if not _vid_ok:
                        raise Exception(f"Wav2Lip fallback para vídeo falhou: {_vid_reason}")
            else:
                jobs[job_id]["message"] = f"Wav2Lip: sincronizando lábios no vídeo ({dur:.0f}s)..."
                run_wav2lip_chunked(
                    config["image_path"], audio_path, avatar_video,
                    settings={}, chunk_duration=300, job_id=job_id
                )
                _vid_ok, _vid_reason = _validate_video_output(avatar_video, expected_dur=dur)
                print(f"  [Pipeline] Vídeo Wav2Lip validação: {_vid_reason}")
                if not _vid_ok:
                    raise Exception(f"Wav2Lip para vídeo falhou: {_vid_reason}")
            # Fix A/V sync (drift comum após Wav2Lip chunked)
            _sync_out = os.path.join(OUTPUT_DIR, f"{job_id}_sync.mp4")
            if _fix_av_sync(avatar_video, _sync_out):
                _safe_rm(avatar_video)
                _safe_rename(_sync_out, avatar_video)
                print("  [AVSync] Sync fix aplicado ao vídeo")
            # NÃO aplicar GFPGAN — vídeo original já tem boa qualidade
            jobs[job_id]["progress"] = 72

        jobs[job_id]["progress"] = 80
        jobs[job_id]["message"]  = "Compositing..."

        # ── Step 3: Background Compositing / BG Removal ───────────────────
        jobs[job_id]["status"] = "compositing"
        final_output = os.path.join(OUTPUT_DIR, f"{job_id}_final.mp4")
        bg_path      = config.get("background", "")
        remove_bg    = config.get("remove_bg", False)

        try:
            if remove_bg and bg_path and os.path.exists(bg_path):
                # Remove BG from avatar video and composite on new background
                jobs[job_id]["message"] = "Removing background and compositing..."
                remove_background_from_video(avatar_video, bg_path, final_output, job_id=job_id)
                if not os.path.exists(final_output) or os.path.getsize(final_output) < 1000:
                    raise Exception("BG removal produced no output")
            elif bg_path and os.path.exists(bg_path):
                composite_with_background(
                    avatar_video, bg_path, final_output,
                    position=config.get("avatar_position", "bottom_right"),
                    avatar_size=config.get("avatar_size", "medium"),
                    opacity=float(config.get("avatar_opacity", 1.0)),
                )
                if not os.path.exists(final_output) or os.path.getsize(final_output) < 1000:
                    raise Exception("Compositing produced no output")
            else:
                shutil.copy2(avatar_video, final_output)
        except Exception as _comp_err:
            print(f"  [Compositing] Erro: {_comp_err} — usando vídeo sem background")
            jobs[job_id]["message"] = "Background compositing falhou — usando vídeo original"
            shutil.copy2(avatar_video, final_output)

        # ── Step 3b: Auto-captions (Whisper) ──────────────────────────────
        if config.get("captions", False):
            jobs[job_id]["progress"] = 88
            jobs[job_id]["message"]  = "Transcribing audio for captions (Whisper)..."
            try:
                caption_lang  = config.get("caption_lang") or None
                caption_model = config.get("caption_model", "base")
                srt_content   = transcribe_to_srt(audio_path, language=caption_lang,
                                                   model_size=caption_model)
                if srt_content.strip():
                    jobs[job_id]["message"] = "Burning captions into video..."
                    captioned = os.path.join(OUTPUT_DIR, f"{job_id}_captioned.mp4")
                    caption_style = {
                        "font_size": int(config.get("caption_font_size", 22)),
                        "color":     config.get("caption_color", "white"),
                        "position":  config.get("caption_position", "bottom"),
                        "bg_alpha":  float(config.get("caption_bg_alpha", 0.5)),
                    }
                    burn_captions(final_output, srt_content, captioned, style=caption_style)
                    _safe_rm(final_output)
                    _safe_rename(captioned, final_output)
                    # Save SRT alongside output
                    srt_out = os.path.join(OUTPUT_DIR, f"{job_id}.srt")
                    with open(srt_out, "w", encoding="utf-8") as _sf:
                        _sf.write(srt_content)
                    jobs[job_id]["srt_path"] = srt_out
                    print(f"  [Captions] Done — SRT: {srt_out}")
            except Exception as _ce:
                print(f"  [Captions] Failed (non-fatal): {_ce}")

        # ── Step 3b2: Translate subtitles (if caption_translate set) ──────
        if config.get("captions") and config.get("caption_translate"):
            srt_key = jobs[job_id].get("srt_path")
            if srt_key and os.path.exists(srt_key):
                try:
                    with open(srt_key, encoding="utf-8") as _sf:
                        _orig_srt = _sf.read()
                    _xlat_srt = translate_srt(_orig_srt, config["caption_translate"])
                    xlat_path = srt_key.replace(".srt", f"_{config['caption_translate']}.srt")
                    with open(xlat_path, "w", encoding="utf-8") as _sf:
                        _sf.write(_xlat_srt)
                    jobs[job_id]["srt_translated_path"] = xlat_path
                    print(f"  [Translate] SRT → {config['caption_translate']}: {xlat_path}")
                except Exception as _te:
                    print(f"  [Translate] failed: {_te}")

        # ── Step 3c: Output format (portrait/square) ───────────────────────
        output_fmt = config.get("output_format", "landscape")
        if output_fmt in ("portrait", "square"):
            jobs[job_id]["progress"] = 92
            jobs[job_id]["message"]  = f"Applying {output_fmt} format..."
            fmt_out = os.path.join(OUTPUT_DIR, f"{job_id}_fmt.mp4")
            apply_output_format(final_output, fmt_out, fmt=output_fmt)
            if os.path.exists(fmt_out) and os.path.getsize(fmt_out) > 1000:
                _safe_rm(final_output)
                _safe_rename(fmt_out, final_output)

        # ── Step 3d: Watermark ─────────────────────────────────────────────
        wm_settings = load_settings()
        wm_text     = config.get("watermark_text", wm_settings.get("watermark_text", ""))
        wm_pos      = config.get("watermark_pos",  wm_settings.get("watermark_pos",  "bottom_right"))
        if wm_text and wm_text.strip():
            jobs[job_id]["progress"] = 95
            jobs[job_id]["message"]  = "Adding watermark..."
            wm_out = os.path.join(OUTPUT_DIR, f"{job_id}_wm.mp4")
            add_watermark(final_output, wm_out, text=wm_text, position=wm_pos,
                          font_size=int(config.get("watermark_size", 22)),
                          color=config.get("watermark_color", "white"),
                          opacity=float(config.get("watermark_opacity", 0.7)))
            if os.path.exists(wm_out) and os.path.getsize(wm_out) > 1000:
                _safe_rm(final_output)
                _safe_rename(wm_out, final_output)

        # ── Step 3e: Background music ──────────────────────────────────────
        music_src = config.get("music_url", "")
        if music_src:
            # Bloquear URLs externas (SSRF) — só aceita paths locais do servidor
            if music_src.startswith(("http://", "https://", "ftp://", "//")):
                print(f"  [Music] URL externa bloqueada por segurança: {music_src}")
                music_src = ""
            # music_url can be a server path (/static/music/...) or absolute
            elif music_src.startswith("/static/music/"):
                music_src = os.path.join(BASE_DIR, "static", "music",
                                         os.path.basename(music_src))
            if os.path.exists(music_src):
                jobs[job_id]["progress"] = 96
                jobs[job_id]["message"]  = "Mixing background music..."
                music_out = os.path.join(OUTPUT_DIR, f"{job_id}_music.mp4")
                mix_background_music(final_output, music_src, music_out,
                                     music_volume=float(config.get("music_volume", 0.15)))
                if os.path.exists(music_out) and os.path.getsize(music_out) > 1000:
                    _safe_rm(final_output)
                    _safe_rename(music_out, final_output)

        # ── Step 3f: Fade in/out ───────────────────────────────────────────
        if config.get("enable_fade", False):
            jobs[job_id]["progress"] = 97
            jobs[job_id]["message"]  = "Applying fade in/out..."
            fade_out_path = os.path.join(OUTPUT_DIR, f"{job_id}_fade.mp4")
            apply_fade(final_output, fade_out_path,
                       fade_in=float(config.get("fade_in", 0.5)),
                       fade_out=float(config.get("fade_out", 0.5)))
            if os.path.exists(fade_out_path) and os.path.getsize(fade_out_path) > 1000:
                _safe_rm(final_output)
                _safe_rename(fade_out_path, final_output)

        # ── Step 3g: Additional export format ─────────────────────────────
        extra_fmt = config.get("export_format", "")
        if extra_fmt and extra_fmt in ("webm", "mov", "gif"):
            jobs[job_id]["progress"] = 98
            jobs[job_id]["message"]  = f"Exporting {extra_fmt.upper()}..."
            extra_path = export_additional_format(final_output, OUTPUT_DIR, job_id, extra_fmt)
            if extra_path:
                jobs[job_id]["extra_export"] = f"/outputs/{os.path.basename(extra_path)}"

        # ── Step 3h: Final HD re-encode — SEMPRE 1280×720 @ 2.5Mbps HeyGen quality ──
        _check_cancel(job_id)
        # Garante resolução, bitrate e nitidez independente de qualquer etapa anterior.
        # Todos os caminhos passam por ASCII tmpdir (OUTPUT_DIR tem ç).
        _hd_tmp = ""
        try:
            import tempfile as _tmpHD
            _ff2     = _ffmpeg_path()
            _ffprobe = _ffprobe_path()
            _hd_tmp  = _tmpHD.mkdtemp(prefix="avp_hd_")
            _safe_in  = os.path.join(_hd_tmp, "in.mp4")
            _safe_out = os.path.join(_hd_tmp, "out.mp4")

            shutil.copy2(final_output, _safe_in)

            _probe = subprocess.run(
                [_ffprobe, "-v","error","-select_streams","v:0",
                 "-show_entries","stream=width,height,bit_rate","-of","csv=p=0", _safe_in],
                capture_output=True, timeout=15
            )
            _raw = _probe.stdout.decode("utf-8", errors="replace").strip()
            _parts = _raw.split(",")
            _cur_w   = int(_parts[0]) if len(_parts) >= 1 and _parts[0].isdigit() else 0
            _cur_h   = int(_parts[1]) if len(_parts) >= 2 and _parts[1].isdigit() else 0
            _cur_br  = int(_parts[2]) if len(_parts) >= 3 and _parts[2].isdigit() else 0
            print(f"  [HD] probe: {_cur_w}×{_cur_h} @ {_cur_br//1000}kbps")

            # SEMPRE re-encode para garantir resolução, bitrate e nitidez máxima (HeyGen quality)
            # 1080p Full HD output by default — HeyGen-class deliverable.
            # User can override via config["output_resolution"] = "720p" if needed for size.
            _target_res = config.get("output_resolution", "1080p")
            _out_fmt    = config.get("output_format", "landscape")
            # Respect aspect ratio from output_format. This final HD pass is the
            # authoritative encode — if it always forced 1920x1080 it would undo
            # the portrait/square reformat done in Step 3c (the bug that made
            # output_format=portrait/square silently produce landscape video).
            if _out_fmt == "portrait":
                _tw, _th, _vbr, _vmin, _vmax, _vbuf = 1080, 1920, "8000k", "6000k", "12000k", "16000k"
            elif _out_fmt == "square":
                _tw, _th, _vbr, _vmin, _vmax, _vbuf = 1080, 1080, "8000k", "6000k", "12000k", "16000k"
            elif _target_res == "720p":
                _tw, _th, _vbr, _vmin, _vmax, _vbuf = 1280, 720, "2500k", "2000k", "4000k", "5000k"
            else:
                _tw, _th, _vbr, _vmin, _vmax, _vbuf = 1920, 1080, "8000k", "6000k", "12000k", "16000k"

            # AI UPSCALE: if source is small (<720p), use Real-ESRGAN x2 to *generate detail*
            # instead of just stretching (which causes the "240p look" complaint).
            # Disabled by default for clips >5min (too slow ~0.5s/frame). User opt-in via config.
            _use_ai_upscale = config.get("ai_upscale", "auto")
            if _use_ai_upscale == "auto":
                # Auto-enable for short clips with small source
                _use_ai_upscale = (dur <= 600 and max(_cur_w, _cur_h) < 1080)  # AI upscale for <=10min videos
            if _use_ai_upscale:
                jobs[job_id]["message"] = "AI Upscale (Real-ESRGAN x2): aumentando detalhe..."
                _ai_out = os.path.join(_hd_tmp, "ai_upscaled.mp4")
                _ai_ok = realesrgan_upscale_video(_safe_in, _ai_out, scale=2, job_id=job_id)
                if _ai_ok and os.path.getsize(_ai_out) > 10000:
                    _safe_in = _ai_out  # downstream HD encode uses upscaled source
                    # Re-probe new dimensions
                    _probe2 = subprocess.run(
                        [_ffprobe, "-v","error","-select_streams","v:0",
                         "-show_entries","stream=width,height","-of","csv=p=0", _safe_in],
                        capture_output=True, timeout=15
                    )
                    _parts2 = _probe2.stdout.decode("utf-8", errors="replace").strip().split(",")
                    if len(_parts2) >= 2:
                        _cur_w = int(_parts2[0]) if _parts2[0].isdigit() else _cur_w
                        _cur_h = int(_parts2[1]) if _parts2[1].isdigit() else _cur_h
                    print(f"  [AI Upscale] OK: source now {_cur_w}x{_cur_h}")
                else:
                    print(f"  [AI Upscale] Skipped (model unavailable or failed) — falling back to lanczos stretch")

            _needs_hd = True
            if _needs_hd:
                # SMOOTH MOTION: interpolate to 30fps for natural playback.
                # Limitado a clips curtos: minterpolate é MUITO lento (~20min p/ 150s) e
                # em vídeos longos produz output inválido + expõe os temps a uma janela de
                # cleanup/race que fazia o HD encode falhar (visto no M3.4). Curtos só.
                if dur <= 60:  # minterpolate é CPU-intensivo demais p/ clips longos
                    jobs[job_id]["message"] = "Smooth motion: interpolando para 30fps..."
                    _mi_out = os.path.join(_hd_tmp, "smooth_30fps.mp4")
                    _mi_vf = "minterpolate='fps=30:mi_mode=blend:mc_mode=aobmc:me_mode=bidir:vsbmc=1'"
                    _mi_cmd = [_ffmpeg_path(), "-y", "-i", _safe_in,
                               "-vf", _mi_vf,
                               "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                               "-c:a", "copy",
                               _mi_out]
                    try:
                        _mi_r = subprocess.run(_mi_cmd, capture_output=True, timeout=max(300, int(dur*8)))
                        if os.path.exists(_mi_out) and os.path.getsize(_mi_out) > 10000:
                            _safe_in = _mi_out
                            print(f"  [SmoothMotion] 25fps -> 30fps interpolation applied")
                        else:
                            print(f"  [SmoothMotion] Skipped (output invalid)")
                    except Exception as _mi_err:
                        print(f"  [SmoothMotion] Skipped ({_mi_err})")
                jobs[job_id]["message"] = f"HD final: {_cur_w}×{_cur_h}→{_tw}×{_th} @ {_vbr}..."
                _hd_preset  = "slow"  # always slow for maximum quality (2-pass equivalent)
                # Timeout realista: libx264 fast = ~100fps CPU → 4× vídeo + 5min buffer for 1080p
                _hd_timeout = max(300, int(dur * 4) + 300)
                # scale+crop + denoise + sharpen + color grade for HeyGen-class look
                # HeyGen-level professional post-processing pipeline
                _vf = (f"scale={_tw}:{_th}:force_original_aspect_ratio=increase:flags=lanczos,"
                       f"crop={_tw}:{_th},"
                       "hqdn3d=2:1.5:4:3,"              # temporal denoise (anti-flicker)
                       "smartblur=lr=1.0:ls=-0.9:lt=-5:cr=0.8:cs=-0.8:ct=-4,"  # face edge softening
                       "unsharp=5:5:0.6:5:5:0.0,"        # gentle sharpen (HeyGen-level softness)
                       "eq=contrast=1.04:brightness=0.003:saturation=1.08:gamma=1.02,"  # subtle color
                       "colorbalance=rs=0.01:gs=-0.01:bs=-0.02:"  # warm skin tones
                       "rm=0.01:gm=-0.01:bm=-0.01:"
                       "rh=0.005:gh=-0.005:bh=-0.01,"    # warm highlights
                       "curves=m='0/0 0.25/0.23 0.5/0.52 0.75/0.79 1/1'"  # subtle S-curve (cinema)
                       )
                _hd_cmd = [_ff2, "-y", "-i", _safe_in,
                           "-vf", _vf,
                           "-c:v", "libx264", "-preset", _hd_preset, "-tune", "film",
                           "-crf", "16",
                           "-b:v", _vbr, "-minrate", _vmin, "-maxrate", _vmax, "-bufsize", _vbuf,
                           "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
                           "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
                           "-pix_fmt", "yuv420p",
                           "-r", "30",  # 30fps for smooth playback
                           "-movflags", "+faststart",
                           _safe_out]
                # Bitrate-controlled encode com retry automático em caso de timeout
                _hd_ok = False
                for _hd_attempt in range(2):
                    try:
                        import time as _t
                        _t0 = _t.monotonic()
                        _hr = subprocess.run(_hd_cmd, capture_output=True, timeout=_hd_timeout)
                        if os.path.exists(_safe_out) and os.path.getsize(_safe_out) > 10000:
                            shutil.copy2(_safe_out, final_output)
                            _sz = os.path.getsize(final_output) // 1024 // 1024
                            print(f"  [HD] OK → 1280×720 @ 2.5Mbps | {_sz}MB (rc={_hr.returncode})")
                            _hd_ok = True
                        else:
                            _err = (_hr.stderr or b"").decode("utf-8", errors="replace")[-400:]
                            print(f"  [HD] FAILED rc={_hr.returncode}: {_err}")
                        break
                    except subprocess.TimeoutExpired:
                        _elapsed = _t.monotonic() - _t0
                        # Se elapsed << timeout, máquina hibernou e acordou → timeout espúrio
                        if _elapsed < 30 and _hd_attempt == 0:
                            print(f"  [HD] Timeout espúrio ({_elapsed:.0f}s elapsed) — retry após hibernação")
                            if os.path.exists(_safe_out): os.remove(_safe_out)
                            continue
                        # Timeout genuíno ou segundo tentativa — verificar output parcial
                        if os.path.exists(_safe_out) and os.path.getsize(_safe_out) > 10000:
                            shutil.copy2(_safe_out, final_output)
                            _sz = os.path.getsize(final_output) // 1024 // 1024
                            print(f"  [HD] Timeout mas output existe — usando output parcial | {_sz}MB")
                            _hd_ok = True
                        else:
                            print(f"  [HD] Timeout após {_elapsed:.0f}s — sem output (attempt {_hd_attempt+1}/2)")
                        break
                if not _hd_ok:
                    # ÚLTIMO RECURSO: re-encode SIMPLES (preset rápido, sem filtros pesados)
                    # lendo do final_output REAL — o in.mp4 temp pode ter sumido por cleanup
                    # durante encodes longos. Garante 1920x1080 mesmo se o encode "bonito" falhar.
                    try:
                        import tempfile as _tmpSF
                        _sf_dir = _tmpSF.mkdtemp(prefix="avp_hdsimple_")
                        _simple_out = os.path.join(_sf_dir, "simple.mp4")
                        _simple_vf = (f"scale={_tw}:{_th}:force_original_aspect_ratio=increase:flags=lanczos,"
                                      f"crop={_tw}:{_th}")
                        _simple_cmd = [_ff2, "-y", "-i", final_output, "-vf", _simple_vf,
                                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                                       "-b:v", _vbr, "-maxrate", _vmax, "-bufsize", _vbuf,
                                       "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                                       "-pix_fmt", "yuv420p", "-movflags", "+faststart", _simple_out]
                        _sr = subprocess.run(_simple_cmd, capture_output=True,
                                             timeout=max(300, int(dur*3) + 120))
                        if os.path.exists(_simple_out) and os.path.getsize(_simple_out) > 10000:
                            shutil.copy2(_simple_out, final_output)
                            print(f"  [HD] Fallback simples OK → {_tw}×{_th} (preset veryfast)")
                            _hd_ok = True
                        shutil.rmtree(_sf_dir, ignore_errors=True)
                    except Exception as _sfe:
                        print(f"  [HD] Fallback simples exception: {_sfe}")
                    if not _hd_ok:
                        print(f"  [HD] HD encode falhou — mantendo output pré-HD (qualidade inferior)")
            else:
                print(f"  [HD] já está {_cur_w}×{_cur_h} @ {_cur_br//1000}kbps — sem re-encode")

        except Exception as _hde:
            print(f"  [HD] exception: {_hde}")
        finally:
            if _hd_tmp:
                shutil.rmtree(_hd_tmp, ignore_errors=True)

        # ── Step 4: Thumbnail ──────────────────────────────────────────────
        thumb_path = os.path.join(OUTPUT_DIR, f"{job_id}_thumb.jpg")
        generate_thumbnail(final_output, thumb_path)

        final_dur = _get_duration_safe(final_output)
        with jobs_lock:
            jobs[job_id]["progress"]        = 100
            jobs[job_id]["status"]          = "done"
            jobs[job_id]["output_path"]     = final_output
            jobs[job_id]["output_filename"] = os.path.basename(final_output)
            jobs[job_id]["thumbnail"]       = f"/outputs/{job_id}_thumb.jpg" if os.path.exists(thumb_path) else ""
            jobs[job_id]["duration"]        = round(final_dur, 1)
            jobs[job_id]["message"]         = "Done!"

        # ── Persist to history ─────────────────────────────────────────────
        history_record = {
            "id":             job_id,
            "filename":       os.path.basename(final_output),
            "created":        jobs[job_id].get("created", datetime.now().isoformat()),
            "duration":       jobs[job_id]["duration"],
            "size_mb":        round(os.path.getsize(final_output) / 1024 / 1024, 2),
            "script_preview": config.get("script", "")[:120],
            "voice":          config.get("voice", config.get("tts_engine", "edge-tts")),
            "thumbnail":      jobs[job_id]["thumbnail"],
            "status":         "done",
            "plan":           plan,
        }
        add_to_history(history_record)
        increment_stats(jobs[job_id]["duration"])

        # Increment API key usage if job was submitted via API key
        _job_api_key = config.get("_api_key", "")
        if _job_api_key:
            apikey_increment(_job_api_key, jobs[job_id].get("duration", 0))

        # Persist to SQLite + fire webhooks
        db_save_job(job_id, jobs[job_id])
        notify_webhooks(job_id, jobs[job_id])

    except Exception as e:
        with jobs_lock:
            _was_cancelled = jobs[job_id].get("_cancel") or jobs[job_id].get("status") == "cancelled"
            if _was_cancelled:
                jobs[job_id]["status"]  = "cancelled"
                jobs[job_id]["error"]   = "Job cancelado pelo usuário."
                jobs[job_id]["message"] = "Job cancelado pelo usuário."
                print(f"  [Pipeline] job={job_id} cancelado pelo usuário")
            else:
                jobs[job_id]["status"]  = "error"
                jobs[job_id]["error"]   = str(e)
                jobs[job_id]["message"] = f"Error: {e}"
                print(f"  [Pipeline Error] job={job_id}: {e}")
        db_save_job(job_id, jobs[job_id])
        notify_webhooks(job_id, jobs[job_id])

    finally:
        with workers_lock:
            active_workers = max(0, active_workers - 1)
        # Release semaphore slot for next queued job
        if _semaphore_acquired:
            try: _pipeline_semaphore.release()
            except Exception: pass
        # Release GPU memory
        release_vram()
        # Clean up tmp files (keep final output only)
        for _tmp_suffix in ("_avatar.mp4", "_sync.mp4", "_sway.mp4", "_gfpgan.mp4"):
            _safe_rm(os.path.join(OUTPUT_DIR, f"{job_id}{_tmp_suffix}"))
        # Remove _config from completed job so _cleanup_old_jobs can evict it
        jobs.get(job_id, {}).pop("_config", None)
        # Limpar webhook registry do job concluído
        webhook_registry.pop(job_id, None)
        # Limpeza de jobs antigos (>48h) para evitar memory leak em produção
        _cleanup_old_jobs()
        # Advance batch queue
        _advance_batch_queue()

def _cleanup_old_jobs(max_age_hours: int = 48):
    """Remove jobs concluídos há mais de max_age_hours do dict em memória."""
    try:
        _cutoff = datetime.now().timestamp() - max_age_hours * 3600
        with jobs_lock:
            _to_remove = [
                jid for jid, j in jobs.items()
                if j.get("status") in ("done", "error", "cancelled")
                and "_config" not in j  # não remover jobs ainda ativos (active = has _config)
                and datetime.fromisoformat(j.get("created", datetime.now().isoformat())).timestamp() < _cutoff
            ]
            for jid in _to_remove:
                jobs.pop(jid, None)
        if _to_remove:
            print(f"  [Cleanup] Removidos {len(_to_remove)} jobs antigos da memória")
    except Exception:
        pass

def _safe_rm(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

def _safe_rename(src, dst):
    """Windows-safe rename: handles WinError 183 (dest already exists) with retry + fallback.
    On Windows, os.rename raises if dst exists, even after _safe_rm if file was just closed.
    Retry 5x with 300ms sleep before falling back to copy+delete."""
    for attempt in range(5):
        try:
            if os.path.exists(dst):
                os.remove(dst)
            os.rename(src, dst)
            return
        except OSError:
            if attempt < 4:
                time.sleep(0.3)
    # Final fallback: copy then delete
    try:
        shutil.copy2(src, dst)
        _safe_rm(src)
    except Exception as _e:
        print(f"  [_safe_rename] WARN: fallback copy also failed: {_e}", flush=True)

def _advance_batch_queue():
    """Start next batch job if any is waiting."""
    with batch_lock:
        with jobs_lock:
            waiting = [j for j in batch_queue if jobs.get(j, {}).get("status") == "queued"]
            if not waiting:
                return
            next_id = waiting[0]
            cfg = jobs[next_id].get("_config", {})
        t = Thread(target=_safe_pipeline_runner, args=(next_id, cfg), daemon=True)
        t.start()
        with jobs_lock:
            if next_id in jobs:
                jobs[next_id]['_thread'] = t


# ============================================================================
# BODY SWAY — adiciona movimento natural de respiração/balanço ao vídeo
# ============================================================================
def add_body_sway_to_video(input_path: str, output_path: str, intensity: float = 1.0) -> str:
    """
    Aplica movimento sutil de respiração e balanço corporal ao vídeo.
    Usa filtro crop dinâmico com ondas senoidais — sem artifacts, sem distorção.
    intensity: 0.5 (sutil) a 2.0 (mais expressivo). Default 1.0.
    """
    import tempfile as _tsw
    ff   = _ffmpeg_path()
    ffp  = _ffprobe_path()

    # Pegar resolução original
    _pr = subprocess.run(
        [ffp, "-v","error","-select_streams","v:0",
         "-show_entries","stream=width,height","-of","csv=p=0", input_path],
        capture_output=True, timeout=10
    )
    _dims = _pr.stdout.decode().strip().split(",")
    orig_w, orig_h = (int(_dims[0]), int(_dims[1])) if len(_dims) == 2 else (1280, 720)

    # Margem para o crop dinâmico (pixels)
    margin_x = max(4, int(6 * intensity)) & ~1
    margin_y = max(4, int(8 * intensity)) & ~1
    crop_w   = orig_w - margin_x * 2
    crop_h   = orig_h - margin_y * 2
    # Garantir par (exigência H.264)
    crop_w   = crop_w & ~1
    crop_h   = crop_h & ~1

    # Equações de movimento (ondas senoidais em hz de respiração ~0.3Hz):
    # x sway: ±margin_x pixels, frequência ~0.15 Hz (oscilação lenta)
    # y breath: ±margin_y pixels, frequência ~0.3 Hz (respiração)
    sx = margin_x
    sy = margin_y
    # 't' = tempo em segundos (variável nativa do filtro crop do FFmpeg)
    # Frequências calibradas para parecer um humano real respirando e oscilando
    freq_x  = 0.09   # Hz balanço lateral (~1 oscilação a cada 11s — natural)
    freq_y  = 0.22   # Hz respiração vertical (~13 respirações/min — fisiológico)
    freq_x2 = 0.17   # Hz segunda harmônica de balanço (evita movimento mecânico)
    # x oscila em torno do centro (sx), duas frequências para parecer orgânico
    # Range garantido: [0, 2*sx] → nunca negativo, nunca fora da margem
    eq_x = f"{sx}+{sx}*0.7*sin(2*3.14159*{freq_x}*t)+{sx}*0.3*sin(2*3.14159*{freq_x2}*t+0.9)"
    # y oscila em torno do centro (sy), respiração principal + micro-oscilação
    eq_y = f"{sy}+{sy}*0.85*sin(2*3.14159*{freq_y}*t)+{sy}*0.15*sin(2*3.14159*0.45*t+1.2)"

    vf = (
        f"crop={crop_w}:{crop_h}:x='{eq_x}':y='{eq_y}',"
        f"scale={orig_w}:{orig_h}:flags=lanczos"
    )

    _sway_tmp = _tsw.mkdtemp(prefix="sway_")
    _safe_tmp = os.path.join(_sway_tmp, "out.mp4")
    try:
        dur_str = subprocess.run(
            [ffp, "-v","error","-show_entries","format=duration",
             "-of","default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, timeout=10
        ).stdout.decode().strip()
        dur = float(dur_str) if dur_str else 60.0

        _safe_in = os.path.join(_sway_tmp, "in.mp4")
        shutil.copy2(input_path, _safe_in)

        r = subprocess.run(
            [ff, "-y", "-i", _safe_in,
             "-vf", vf,
             "-c:v", "libx264", "-preset", "slow", "-crf", "17",
             "-b:v", "2500k", "-maxrate", "4000k", "-bufsize", "8000k",
             "-c:a", "copy",
             "-pix_fmt", "yuv420p", _safe_tmp],
            capture_output=True, timeout=max(600, int(dur * 5))
        )

        if os.path.exists(_safe_tmp) and os.path.getsize(_safe_tmp) > 10000:
            shutil.copy2(_safe_tmp, output_path)
            print(f"  [BodySway] OK — {crop_w}×{crop_h} crop dinâmico → {orig_w}×{orig_h}")
            return output_path

        print(f"  [BodySway] Falhou — usando vídeo sem sway")
        shutil.copy2(input_path, output_path)
        return output_path
    finally:
        shutil.rmtree(_sway_tmp, ignore_errors=True)


# ============================================================================
# GESTURE FACE SWAP — troca rosto do usuário em vídeo de gestos (InsightFace)
# ============================================================================
GESTURE_VIDEOS_DIR = os.path.join(BASE_DIR, "static", "gesture_videos")
os.makedirs(GESTURE_VIDEOS_DIR, exist_ok=True)


def _list_gesture_templates() -> list:
    """List available gesture template videos for multi-template pipeline."""
    if not os.path.isdir(GESTURE_VIDEOS_DIR):
        return []
    valid = []
    for f in os.listdir(GESTURE_VIDEOS_DIR):
        if f.startswith(".") or f.startswith("_"): continue  # skip manifest, tmps
        if f.lower().endswith((".mp4", ".mov", ".webm", ".mkv")):
            p = os.path.join(GESTURE_VIDEOS_DIR, f)
            if os.path.isfile(p) and os.path.getsize(p) > 100_000:
                valid.append(p)
    return valid


def _build_long_gesture_sequence(audio_dur: float, templates: list,
                                  out_path: str, job_id: str = None) -> str:
    """
    For long videos, build a sequence of gesture templates concatenated to cover
    the audio duration. Uses different templates per chunk to avoid visible repetition.
    Output: silent video of length ~audio_dur using gesture templates.

    Returns path to the assembled video (no audio yet — audio added by lip sync step).
    """
    import tempfile as _tgs, random as _rgs
    ff  = _ffmpeg_path()
    ffp = _ffprobe_path()
    if not templates:
        raise Exception("Nenhum gesture template disponível.")

    # Get duration of each template
    template_info = []
    for tpl in templates:
        try:
            r = subprocess.run(
                [ffp, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", tpl],
                capture_output=True, text=True, timeout=10
            )
            dur = float(r.stdout.strip() or 0)
            if dur >= 5:
                template_info.append((tpl, dur))
        except Exception:
            continue
    if not template_info:
        raise Exception("Templates inválidos (sem duração).")

    tmp = _tgs.mkdtemp(prefix="gesture_seq_")
    try:
        # Build sequence by picking templates in randomized order until audio_dur is covered.
        # Each template can be used multiple times but never consecutively.
        rng = _rgs.Random(42)  # deterministic for reproducibility
        sequence = []
        accumulated = 0.0
        last_pick = None
        while accumulated < audio_dur + 1:
            choices = [t for t in template_info if t[0] != last_pick] or template_info
            pick = rng.choice(choices)
            sequence.append(pick)
            accumulated += pick[1]
            last_pick = pick[0]

        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"Gesture Pack: montando sequência ({len(sequence)} templates)..."
        print(f"  [GesturePack] Sequence: {len(sequence)} templates totaling {accumulated:.0f}s", flush=True)

        # ffmpeg concat demuxer on Windows can't read paths with non-ASCII chars (ç, ã, etc).
        # Workaround: copy each template to ASCII-only tmp dir, build concat list with those paths.
        ascii_tpl_dir = os.path.join(tmp, "tpl")
        os.makedirs(ascii_tpl_dir, exist_ok=True)
        ascii_paths = []
        for i, (tpl, _) in enumerate(sequence):
            ascii_path = os.path.join(ascii_tpl_dir, f"t{i:03d}.mp4")
            shutil.copy2(tpl, ascii_path)
            ascii_paths.append(ascii_path)

        concat_txt = os.path.join(tmp, "concat.txt")
        with open(concat_txt, "w") as f:
            for p in ascii_paths:
                # forward-slash to be safe across ffmpeg builds
                p_safe = p.replace("\\", "/").replace("'", "")
                f.write(f"file '{p_safe}'\n")

        # Concat (re-encode required because templates may differ in fps/codec params).
        # Output also goes to ASCII tmp first, then copied to final out_path.
        ascii_out = os.path.join(tmp, "out.mp4")
        cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", concat_txt,
               "-t", str(round(audio_dur + 0.5, 3)),
               "-c:v", "libx264", "-preset", "slow", "-crf", "20",
               "-pix_fmt", "yuv420p", "-an", ascii_out]
        r = subprocess.run(cmd, capture_output=True,
                           timeout=max(600, int(audio_dur * 2)))
        if r.returncode != 0 or not os.path.exists(ascii_out):
            err = (r.stderr or b"").decode("utf-8", errors="replace")[-300:]
            raise Exception(f"Gesture sequence concat falhou: {err}")
        # Copy from ASCII tmp to final path (which may have non-ASCII chars)
        shutil.copy2(ascii_out, out_path)
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def swap_face_on_gesture_video(source_img: str, gesture_video: str, output_path: str,
                                job_id: str = None) -> str:
    """
    Swap the user face onto every frame of a gesture video using InsightFace.
    NOW runs in an isolated subprocess to prevent OOM from killing Flask server.
    Falls back to original gesture video if swap fails.
    """
    worker_script = os.path.join(BASE_DIR, "face_swap_worker.py")
    if not os.path.exists(worker_script):
        print("  [FaceSwap] worker script not found - skipping swap", flush=True)
        shutil.copy2(gesture_video, output_path)
        return output_path

    # Find inswapper model
    _swap_candidates = [
        os.path.join(MODELS_DIR, "inswapper_128.onnx"),
        os.path.join(MODELS_DIR, "SadTalker", "inswapper_128.onnx"),
    ]
    swap_model = next((p for p in _swap_candidates if os.path.exists(p)), None)
    if not swap_model:
        print("  [FaceSwap] inswapper_128.onnx not found - skipping", flush=True)
        shutil.copy2(gesture_video, output_path)
        return output_path

    python_exe = os.path.join(BASE_DIR, "venv311", "Scripts", "python.exe")
    if not os.path.exists(python_exe):
        python_exe = sys.executable

    if job_id and job_id in jobs:
        jobs[job_id]["message"] = "Face Swap: aplicando rosto (subprocess isolado)..."
    print(f"  [FaceSwap] Running in subprocess (isolated from Flask)", flush=True)

    try:
        import json as _j
        # Use Popen for real-time progress reading (prevents watchdog stall-kill)
        proc = subprocess.Popen(
            [python_exe, worker_script, source_img, gesture_video, output_path, swap_model],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=BASE_DIR, text=True, encoding="utf-8", errors="replace",
        )
        _start_t = time.time()
        _timeout = 3600  # 60 min max for large face swaps
        _last_update = time.time()
        for line in proc.stdout:
            line = line.strip()
            if not line: continue
            try:
                data = _j.loads(line)
                if data.get("progress") is not None and job_id and job_id in jobs:
                    pct = int(data["progress"] * 100 / max(1, data.get("total", 1)))
                    jobs[job_id]["message"] = f"Face Swap: {data['progress']}/{data.get('total',0)} frames ({pct}%)..."
                    jobs[job_id]["progress"] = 35 + int(pct * 0.3)  # 35-65% range
                    _last_update = time.time()
                elif data.get("error"):
                    print(f"  [FaceSwap] Worker: {data['error']}", flush=True)
                elif data.get("success"):
                    print(f"  [FaceSwap] Done: {data.get('frames',0)} frames", flush=True)
            except Exception:
                print(f"  [FaceSwap] {line}", flush=True)
            # Timeout guard
            if time.time() - _start_t > _timeout:
                proc.kill()
                print("  [FaceSwap] Timeout 30min - killed", flush=True)
                break
        proc.wait(timeout=60)

        if proc.returncode != 0 and not os.path.exists(output_path):
            print(f"  [FaceSwap] Worker failed (code {proc.returncode}) - using original", flush=True)
            shutil.copy2(gesture_video, output_path)
        elif not os.path.exists(output_path):
            shutil.copy2(gesture_video, output_path)
        else:
            print(f"  [FaceSwap] Done: {output_path}", flush=True)

    except subprocess.TimeoutExpired:
        proc.kill()
        print("  [FaceSwap] Timeout - using original gesture video", flush=True)
        if not os.path.exists(output_path):
            shutil.copy2(gesture_video, output_path)
    except Exception as e:
        print(f"  [FaceSwap] subprocess error: {e} - using original", flush=True)
        if not os.path.exists(output_path):
            shutil.copy2(gesture_video, output_path)

    return output_path


# ============================================================================
# ROUTES — CORE
# ============================================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/system_health")
def api_system_health():
    """Health check endpoint for production monitoring — returns status of all subsystems."""
    import platform
    _ff   = _ffmpeg_path()
    _ffp  = _ffprobe_path()
    _vram = get_vram_free_gb()

    def _check_ffmpeg():
        try:
            r = subprocess.run([_ff, "-version"], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def _check_ffprobe():
        try:
            r = subprocess.run([_ffp, "-version"], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def _active_job_count():
        with jobs_lock:
            return sum(1 for j in jobs.values() if j.get("status") in ("queued", "tts", "generating_video", "compositing"))

    st_ok  = check_sadtalker()
    w2l_ok = check_wav2lip()
    ff_ok  = _check_ffmpeg()
    ffp_ok = _check_ffprobe()

    try:
        from gfpgan import GFPGANer as _G
        gfpgan_ok = True
    except Exception:
        gfpgan_ok = False

    _active = _active_job_count()
    _q_len  = len(batch_queue)

    status_overall = "ok"
    issues = []
    if not w2l_ok:
        issues.append("Wav2Lip não instalado")
        status_overall = "degraded"
    if not ff_ok:
        issues.append("FFmpeg não encontrado")
        status_overall = "critical"
    if _vram < 1.0 and _active > 0:
        issues.append(f"VRAM baixa: {_vram:.1f}GB")
        status_overall = "degraded"

    return jsonify({
        "status":    status_overall,
        "issues":    issues,
        "engines": {
            "sadtalker": "ready" if st_ok else "unavailable",
            "wav2lip":   "ready" if w2l_ok else "unavailable",
            "gfpgan":    "ready" if gfpgan_ok else "unavailable",
            "ffmpeg":    "ready" if ff_ok else "missing",
        },
        "resources": {
            "vram_free_gb": round(_vram, 1),
            "active_jobs":  _active,
            "queue_length": _q_len,
            "platform":     platform.system(),
        },
        "version": "v3.0",
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/api/voices")
def api_voices():
    try:
        voices  = get_edge_voices()
        grouped = {}
        for v in voices:
            lang = v.get("Locale", "unknown")[:5]
            grouped.setdefault(lang, []).append({
                "name":    v.get("ShortName", ""),
                "display": v.get("FriendlyName", ""),
                "gender":  v.get("Gender", ""),
                "locale":  v.get("Locale", ""),
            })
        return jsonify({"voices": grouped, "total": len(voices)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/preview_audio", methods=["POST"])
def api_preview_audio():
    data   = request.json or {}
    script = data.get("script", "")[:500]
    voice  = data.get("voice", "") or ""
    engine = data.get("engine", "edge-tts")
    voice_preset = data.get("voice_preset", "")
    if not script:
        return jsonify({"error": "No script"}), 400

    # Resolve voice preset (rate/pitch + default voice) so the PREVIEW matches
    # exactly what the final generation will produce. Without this, picking a
    # preset like "documentary_narrator" in the UI and clicking preview failed
    # (empty voice → Edge-TTS 500). Now preview honors presets just like /generate.
    voice_rate, voice_pitch = "+0%", "+0Hz"
    if voice_preset and voice_preset in VOICE_PRESETS:
        p = VOICE_PRESETS[voice_preset]
        if not voice:
            voice = p.get("edge_voice", voice)
        voice_rate  = p.get("rate", "+0%")
        voice_pitch = p.get("pitch", "+0Hz")
    # Graceful fallback: never call Edge-TTS with an empty/blank voice (→ 500).
    if not str(voice).strip():
        voice = load_settings().get("default_voice", "en-US-GuyNeural") or "en-US-GuyNeural"

    pid        = uuid.uuid4().hex[:8]
    audio_path = os.path.join(OUTPUT_DIR, f"preview_{pid}.mp3")
    try:
        if engine == "elevenlabs":
            s = load_settings()
            elevenlabs_generate(script, data.get("voice_id", ""), s.get("elevenlabs_key", ""), audio_path)
        elif voice_rate != "+0%" or voice_pitch != "+0Hz":
            edge_tts_generate_advanced(script, voice, audio_path, rate=voice_rate, pitch=voice_pitch)
        else:
            edge_tts_generate(script, voice, audio_path)
        return jsonify({"audio_url": f"/outputs/preview_{pid}.mp3"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# ROUTES — GENERATE
# ============================================================================
@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Start single avatar generation job."""
    _client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    if not _check_rate_limit(_client_ip):
        return jsonify({"error": f"Rate limit: máximo {_RATE_LIMIT_MAX} jobs por {_RATE_LIMIT_WIN}s por IP."}), 429

    # API key auth (optional — ativado quando AUTH_REQUIRED=True)
    _api_key_str = (request.headers.get("X-API-Key") or
                    request.form.get("api_key") or
                    request.args.get("api_key") or "")
    _key_record = apikey_validate(_api_key_str) if _api_key_str else None
    if AUTH_REQUIRED and not _key_record:
        return jsonify({"error": "API key inválida ou ausente. Inclua X-API-Key no header."}), 401

    script          = request.form.get("script", "")
    # Validate script early — unless user uploaded their own audio (audio_upload path)
    _has_audio = bool(request.files.get("audio"))
    _script_valid = bool(script and script.strip() and any(c.isalnum() for c in script))
    if not _has_audio and not _script_valid:
        return jsonify({
            "error": "Roteiro vazio: digite o texto que o avatar deve falar (mínimo uma palavra)."
        }), 400
    if script and len(script) > MAX_SCRIPT_CHARS:
        return jsonify({
            "error": f"Roteiro muito longo ({len(script)} chars). Máximo: {MAX_SCRIPT_CHARS} caracteres."
        }), 400
    voice           = request.form.get("voice", "en-US-GuyNeural")
    # Legacy: "engine" param was TTS engine. New: prefer "tts_engine" + "video_engine"
    engine          = request.form.get("tts_engine") or request.form.get("engine", "edge-tts")
    # Video engine (lip-sync/animation). Default auto = SadTalker+MuseTalk pipeline.
    # Options: "auto" | "echomimic_v2" (HeyGen-class half-body with gestures, requires local install)
    video_engine    = request.form.get("video_engine", "auto")
    voice_id        = request.form.get("voice_id", "")
    preprocess      = request.form.get("preprocess", "crop")
    still_mode      = request.form.get("still_mode") == "true"
    enhancer        = request.form.get("enhancer", "gfpgan")
    bg_name         = request.form.get("background", "")
    avatar_position = request.form.get("avatar_position", "bottom_right")
    avatar_size     = request.form.get("avatar_size", "medium")
    voice_preset    = request.form.get("voice_preset", "")
    remove_bg       = request.form.get("remove_bg") == "true"
    captions        = request.form.get("captions") == "true"
    caption_lang    = request.form.get("caption_lang", "") or None
    caption_model   = request.form.get("caption_model", "base")
    caption_color   = request.form.get("caption_color", "white")
    caption_pos     = request.form.get("caption_position", "bottom")
    normalize_audio   = request.form.get("normalize_audio") == "true"
    output_format     = request.form.get("output_format", "landscape")
    watermark_text    = request.form.get("watermark_text", "")[:200]
    watermark_pos     = request.form.get("watermark_pos", "bottom_right")
    music_url         = request.form.get("music_url", "")
    enable_fade       = request.form.get("enable_fade") == "true"
    export_format     = request.form.get("export_format", "")
    enhance_img       = request.form.get("enhance_image") == "true"
    enhance_face      = request.form.get("enhance_face", "true").lower() != "false"
    lip_sync_engine   = request.form.get("lip_sync_engine", "wav2lip")
    chroma_key        = request.form.get("chroma_key", "")
    caption_translate = request.form.get("caption_translate", "")
    template_vars_raw = request.form.get("template_vars", "")
    gesture_video_name = request.form.get("gesture_video", "")
    plan              = load_settings().get("plan", DEFAULT_PLAN)

    # Validate script length
    if len(script) > MAX_SCRIPT_CHARS:
        return jsonify({"error": f"Script muito longo (máximo {MAX_SCRIPT_CHARS} caracteres)"}), 400

    # Safe numeric parsing — never crash on invalid client input
    try:
        expression_scale = max(0.1, min(3.0, float(request.form.get("expression_scale", "1.0"))))
    except (ValueError, TypeError):
        expression_scale = 1.0
    try:
        size = int(request.form.get("size", "256"))
        if size not in (256, 512):
            size = 256
    except (ValueError, TypeError):
        size = 256
    try:
        avatar_opacity = max(0.1, min(1.0, float(request.form.get("avatar_opacity", "1.0"))))
    except (ValueError, TypeError):
        avatar_opacity = 1.0
    try:
        caption_font_sz = max(10, min(72, int(request.form.get("caption_font_size", "22"))))
    except (ValueError, TypeError):
        caption_font_sz = 22
    try:
        music_volume = max(0.0, min(1.0, float(request.form.get("music_volume", "0.15"))))
    except (ValueError, TypeError):
        music_volume = 0.15
    try:
        fade_in  = max(0.0, min(10.0, float(request.form.get("fade_in", "0.5"))))
        fade_out = max(0.0, min(10.0, float(request.form.get("fade_out", "0.5"))))
    except (ValueError, TypeError):
        fade_in = fade_out = 0.5
    try:
        chroma_tolerance = max(0, min(200, int(request.form.get("chroma_tolerance", "40"))))
    except (ValueError, TypeError):
        chroma_tolerance = 40

    # Parse template variables JSON
    template_vars = {}
    if template_vars_raw:
        try:
            template_vars = json.loads(template_vars_raw)
        except Exception:
            pass

    # Apply voice preset if selected
    voice_rate  = "+0%"
    voice_pitch = "+0Hz"
    if voice_preset and voice_preset in VOICE_PRESETS:
        p = VOICE_PRESETS[voice_preset]
        if not voice:
            voice = p.get("edge_voice", voice)
        voice_rate  = p.get("rate", "+0%")
        voice_pitch = p.get("pitch", "+0Hz")

    # Image
    if "image" not in request.files:
        return jsonify({"error": "Imagem não enviada. Selecione um avatar (foto ou vídeo) antes de gerar."}), 400
    img_file = request.files["image"]
    if not img_file.filename:
        return jsonify({"error": "Arquivo de imagem inválido (nome vazio). Tente fazer upload novamente."}), 400
    _img_ext_raw = os.path.splitext(img_file.filename)[1].lower()
    _video_exts  = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
    _image_exts  = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    _is_vid_upload = _img_ext_raw in _video_exts
    if _img_ext_raw not in (_image_exts | _video_exts):
        _img_ext_raw = ".jpg"  # safe default for unknown extension
    img_id   = uuid.uuid4().hex[:8]
    img_ext  = _img_ext_raw
    img_path = os.path.join(UPLOAD_DIR, f"avatar_{img_id}{img_ext}")
    img_file.save(img_path)

    # Auto-resize only for images (not videos)
    # Use binary read/write to handle non-ASCII Windows paths (cv2.imwrite fails on ç).
    if not _is_vid_upload:
        try:
            import cv2 as _cv2r
            import numpy as _np_r
            _raw = open(img_path, 'rb').read()
            _rim = _cv2r.imdecode(_np_r.frombuffer(_raw, dtype=_np_r.uint8), _cv2r.IMREAD_COLOR)
            if _rim is not None:
                _rh, _rw = _rim.shape[:2]
                if max(_rh, _rw) > 1280:
                    _rscale = 1280 / max(_rh, _rw)
                    _rim = _cv2r.resize(_rim, (int(_rw * _rscale), int(_rh * _rscale)), interpolation=_cv2r.INTER_LANCZOS4)
                    _ok, _enc = _cv2r.imencode('.jpg', _rim, [_cv2r.IMWRITE_JPEG_QUALITY, 95])
                    if _ok:
                        with open(img_path, 'wb') as _fout:
                            _fout.write(_enc.tobytes())
                        print(f"  [ImageResize] {_rw}×{_rh} → {int(_rw*_rscale)}×{int(_rh*_rscale)}")
        except Exception as _re:
            print(f"  [ImageResize] skipped: {_re}")

    # Audio upload (optional)
    audio_upload = None
    if "audio" in request.files and request.files["audio"].filename:
        aud_file = request.files["audio"]
        aud_ext  = os.path.splitext(aud_file.filename)[1] or ".mp3"
        aud_path = os.path.join(UPLOAD_DIR, f"audio_{img_id}{aud_ext}")
        aud_file.save(aud_path)
        audio_upload = aud_path

    if not script and not audio_upload:
        return jsonify({"error": "Need script or audio"}), 400

    # Fast face detection pre-check (images only — skip for video input)
    if not _is_vid_upload and preprocess != "resize":
        face_ok, face_msg = validate_face_in_image(img_path)
        if not face_ok:
            return jsonify({"error": face_msg}), 400

    bg_path = os.path.join(BG_DIR, bg_name) if bg_name else ""

    # Gesture video: resolve name → full path, validate it stays inside GESTURE_VIDEOS_DIR
    gesture_video_path = ""
    if gesture_video_name:
        _gv_safe = re.sub(r'[^\w\-_\.]', '_', os.path.basename(gesture_video_name))
        _gv_full = os.path.normpath(os.path.join(GESTURE_VIDEOS_DIR, _gv_safe))
        if _gv_full.startswith(os.path.normpath(GESTURE_VIDEOS_DIR)) and os.path.exists(_gv_full):
            gesture_video_path = _gv_full

    job_id = uuid.uuid4().hex[:12]
    # Voice cloning reference (F5-TTS local) — path provided by /api/voices/f5_clone
    voice_ref_audio = request.form.get("voice_ref_audio", "")
    voice_ref_text  = request.form.get("voice_ref_text", "")
    # EchoMimic V2 pose template name (when video_engine=echomimic_v2)
    echo_pose       = request.form.get("echo_pose", "01")
    config = {
        "script": script, "voice": voice, "tts_engine": engine,
        "engine": video_engine,
        "voice_ref_audio": voice_ref_audio, "voice_ref_text": voice_ref_text,
        "echomimic_settings": {"pose": echo_pose},
        "voice_id": voice_id, "image_path": img_path,
        "audio_upload": audio_upload, "preprocess": preprocess,
        "still_mode": still_mode, "expression_scale": expression_scale,
        "enhancer": enhancer, "background": bg_path, "size": size,
        "avatar_position": avatar_position, "avatar_size": avatar_size,
        "avatar_opacity": avatar_opacity, "plan": plan,
        "voice_rate": voice_rate, "voice_pitch": voice_pitch,
        "voice_preset": voice_preset,
        "remove_bg": remove_bg,
        "captions": captions, "caption_lang": caption_lang,
        "caption_model": caption_model, "caption_font_size": caption_font_sz,
        "caption_color": caption_color, "caption_position": caption_pos,
        "normalize_audio": normalize_audio,
        "output_format":   output_format,
        "watermark_text":  watermark_text, "watermark_pos": watermark_pos,
        "music_url":       music_url,      "music_volume":  music_volume,
        "enable_fade":     enable_fade,    "fade_in":       fade_in,    "fade_out": fade_out,
        "export_format":   export_format,
        "enhance_image":   enhance_img,
        "enhance_face":    enhance_face,
        "lip_sync_engine": lip_sync_engine,
        "chroma_key":      chroma_key,
        "chroma_tolerance": chroma_tolerance,
        "caption_translate": caption_translate,
        "template_vars":   template_vars,
        "gesture_video":   gesture_video_path,
        "_api_key":        _api_key_str if _key_record else "",
        "_key_plan":       _key_record["plan"] if _key_record else "",
    }
    # If using API key, override plan with key's plan
    if _key_record:
        config["plan"] = _key_record["plan"]
    # Throttle: se muitos jobs ativos, coloca na fila ao invés de iniciar direto
    _queue_msg = "Queued"
    with workers_lock:
        _cur_workers = active_workers
    if _cur_workers >= MAX_WORKERS:
        _queue_msg = f"Na fila (aguardando vaga — {_cur_workers}/{MAX_WORKERS} jobs ativos)..."
        print(f"  [Queue] {_cur_workers}/{MAX_WORKERS} workers ativos — job {job_id[:8]} enfileirado")

    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "status": "queued", "progress": 0,
            "created": datetime.now().isoformat(),
            "script_preview": (script[:100] + "...") if len(script) > 100 else script,
            "message": _queue_msg, "error": "", "_config": config,
        }
    db_save_job(job_id, jobs[job_id])  # persist immediately so job survives restarts
    t = Thread(target=_safe_pipeline_runner, args=(job_id, config), daemon=True)
    t.start()
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['_thread'] = t
    return jsonify({"job_id": job_id, "queued_workers": _cur_workers})

@app.route("/api/job/<job_id>")
def api_job_status(job_id):
    if job_id in jobs:
        j = dict(jobs[job_id])
        j.pop("_config", None)
        j.pop("_thread", None)   # Thread objects are not JSON serializable
        j.pop("_cancel", None)   # internal flag, not needed by client
        return jsonify(j)
    # Fallback: look in DB (survives server restarts)
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT id,status,progress,output_path,output_filename,error,message,"
            "created_at,duration,thumbnail,audio_duration FROM jobs_db WHERE id=?",
            (job_id,)
        ).fetchone()
        if row:
            return jsonify({
                "id":              row[0], "status":          row[1],
                "progress":        row[2], "output_path":     row[3],
                "output_filename": row[4], "error":           row[5],
                "message":         row[6] or ("Done!" if row[1]=="done" else row[5] or row[1]),
                "created":         row[7], "duration":        row[8],
                "thumbnail":       row[9], "audio_duration":  row[10],
            })
    except Exception as _dbe:
        print(f"  [DB] job lookup error: {_dbe}")
    finally:
        if conn:
            conn.close()
    return jsonify({"error": "Job not found — server may have restarted", "status": "error", "progress": 0}), 404

@app.route("/api/job/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id):
    """Request cancellation of a queued or running job."""
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            return jsonify({"error": "Job not found"}), 404
        if j.get("status") in ("done", "error", "cancelled"):
            return jsonify({"error": f"Job already in terminal state: {j['status']}"}), 400
        jobs[job_id]["_cancel"] = True
        jobs[job_id]["message"] = "Cancelamento solicitado..."
    return jsonify({"ok": True, "job_id": job_id})

# ============================================================================
# ROUTES — BATCH
# ============================================================================
@app.route("/api/batch", methods=["POST"])
def api_batch():
    """Queue multiple jobs for sequential processing."""
    data = request.json or {}
    job_configs = data.get("jobs", [])
    if not job_configs:
        return jsonify({"error": "No jobs provided"}), 400
    if len(job_configs) > 10:
        return jsonify({"error": "Max 10 jobs per batch"}), 400

    plan     = load_settings().get("plan", DEFAULT_PLAN)
    job_ids  = []
    first_id = None

    with batch_lock:
        for i, cfg in enumerate(job_configs):
            job_id = uuid.uuid4().hex[:12]
            cfg["plan"] = plan
            jobs[job_id] = {
                "id": job_id, "status": "queued", "progress": 0,
                "created": datetime.now().isoformat(),
                "message": f"Batch job {i+1}/{len(job_configs)} — waiting",
                "error": "", "_config": cfg,
                "batch_index": i, "batch_total": len(job_configs),
            }
            batch_queue.append(job_id)
            job_ids.append(job_id)
            if first_id is None:
                first_id = job_id

    # Start first job
    if first_id:
        cfg0 = jobs[first_id]["_config"]
        t = Thread(target=_safe_pipeline_runner, args=(first_id, cfg0), daemon=True)
        t.start()
        with jobs_lock:
            if first_id in jobs:
                jobs[first_id]['_thread'] = t

    return jsonify({"batch_id": uuid.uuid4().hex[:8], "job_ids": job_ids, "total": len(job_ids)})

@app.route("/api/batch/status", methods=["POST"])
def api_batch_status():
    """Get status of multiple jobs at once."""
    data    = request.json or {}
    job_ids = data.get("job_ids", [])
    result  = []
    for jid in job_ids:
        if jid in jobs:
            j = dict(jobs[jid])
            j.pop("_config", None)
            result.append(j)
    return jsonify({"jobs": result})

# ============================================================================
# ROUTES — HISTORY
# ============================================================================
@app.route("/api/history")
def api_history():
    history = load_history()
    total = len(history)
    total_size = sum(r.get("size_mb", 0) for r in history)
    # Optional pagination — honored only when the client passes limit/offset.
    # Without params, returns the full list (preserves existing frontend behavior).
    # With thousands of jobs across 1000+ users this lets clients page efficiently.
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        offset = 0
    limit_raw = request.args.get("limit")
    videos = history
    if limit_raw is not None:
        try:
            limit = int(limit_raw)
            videos = history[offset:offset + limit] if limit >= 0 else history[offset:]
        except (ValueError, TypeError):
            videos = history[offset:]
    elif offset:
        videos = history[offset:]
    return jsonify({
        "videos": videos,
        "total": total,
        "total_size_mb": round(total_size, 2),
    })

@app.route("/api/history/<video_id>", methods=["DELETE"])
def api_history_delete(video_id):
    history = load_history()
    new_history = []
    deleted = False
    for r in history:
        if r["id"] == video_id:
            deleted = True
            # Delete files
            for suffix in ("_final.mp4", "_audio.mp3", "_thumb.jpg", "_avatar.mp4"):
                _safe_rm(os.path.join(OUTPUT_DIR, f"{video_id}{suffix}"))
        else:
            new_history.append(r)
    save_history(new_history)
    return jsonify({"deleted": deleted})

@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():
    history = load_history()
    for r in history:
        vid_id = r["id"]
        for suffix in ("_final.mp4", "_audio.mp3", "_thumb.jpg"):
            _safe_rm(os.path.join(OUTPUT_DIR, f"{vid_id}{suffix}"))
    save_history([])
    return jsonify({"cleared": len(history)})

@app.route("/api/history/<video_id>/thumbnail")
def api_history_thumbnail(video_id):
    thumb = os.path.join(OUTPUT_DIR, f"{video_id}_thumb.jpg")
    if not os.path.exists(thumb):
        video = os.path.join(OUTPUT_DIR, f"{video_id}_final.mp4")
        if os.path.exists(video):
            generate_thumbnail(video, thumb)
    if os.path.exists(thumb):
        return send_from_directory(OUTPUT_DIR, f"{video_id}_thumb.jpg")
    return jsonify({"error": "No thumbnail"}), 404

# ============================================================================
# ROUTES — AI
# ============================================================================
@app.route("/api/ai/generate_script", methods=["POST"])
def api_ai_generate_script():
    data   = request.json or {}
    topic  = data.get("topic", "").strip()
    lang   = data.get("language", "English")
    style  = data.get("style", "professional")
    length = data.get("length", "medium")  # short / medium / long

    length_map = {"short": "30-45 seconds", "medium": "60-90 seconds", "long": "2-3 minutes"}
    length_str = length_map.get(length, "60-90 seconds")

    if not topic:
        return jsonify({"error": "Provide a topic"}), 400

    prompt = (
        f"Write a {style} talking avatar script in {lang} about: {topic}\n\n"
        f"Requirements:\n"
        f"- Duration: {length_str} when spoken at normal pace\n"
        f"- Natural, conversational tone for video narration\n"
        f"- No stage directions, just the spoken words\n"
        f"- Engaging opening and clear call-to-action at the end\n"
        f"- Output ONLY the script text, nothing else"
    )
    result = ask_groq(prompt, max_tokens=1024)
    return jsonify({"script": result})

@app.route("/api/ai/enhance_script", methods=["POST"])
def api_ai_enhance_script():
    data   = request.json or {}
    script = data.get("script", "").strip()
    goal   = data.get("goal", "make it more engaging and natural")
    if not script:
        return jsonify({"error": "Provide a script"}), 400
    prompt = (
        f"Improve this talking avatar script. Goal: {goal}\n\n"
        f"Original script:\n{script}\n\n"
        f"Requirements:\n"
        f"- Keep the same language\n"
        f"- Keep similar length (within 20%)\n"
        f"- Make it more natural for speech\n"
        f"- Output ONLY the improved script, nothing else"
    )
    result = ask_groq(prompt, max_tokens=1024)
    return jsonify({"script": result})

@app.route("/api/ai/suggest_voice", methods=["POST"])
def api_ai_suggest_voice():
    data    = request.json or {}
    script  = data.get("script", "").strip()[:500]
    content = data.get("content_type", "general")
    if not script:
        return jsonify({"error": "Provide a script"}), 400
    prompt = (
        f"Given this talking avatar script for a {content} video, suggest the best Edge-TTS voice.\n"
        f"Script sample: {script}\n\n"
        f"Choose from these popular options and explain why:\n"
        f"- en-US-GuyNeural (professional male)\n"
        f"- en-US-JennyNeural (friendly female)\n"
        f"- en-US-AriaNeural (expressive female)\n"
        f"- en-US-DavisNeural (deep male)\n"
        f"- pt-BR-AntonioNeural (Brazilian male)\n"
        f"- pt-BR-FranciscaNeural (Brazilian female)\n\n"
        f"Respond in JSON: {{\"voice\": \"voice-name\", \"reason\": \"short explanation\"}}"
    )
    result = ask_groq(prompt, max_tokens=256, model="llama-3.3-70b-versatile")
    try:
        parsed = json.loads(result)
    except Exception:
        parsed = {"voice": "en-US-GuyNeural", "reason": result}
    return jsonify(parsed)

@app.route("/api/ai/generate_image", methods=["POST"])
def api_ai_generate_image():
    """Generate an avatar image via Pollinations.ai (free, no key needed)."""
    data   = request.json or {}
    prompt = data.get("prompt", "").strip()
    style  = data.get("style", "realistic")
    width  = int(data.get("width", 512))
    height = int(data.get("height", 512))
    if not prompt:
        return jsonify({"error": "Provide a prompt"}), 400
    try:
        img_path = generate_avatar_image_pollinations(prompt, style, width, height)
        filename = os.path.basename(img_path)
        return jsonify({"image_url": f"/outputs/{filename}", "path": img_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# ROUTES — VOICES (ElevenLabs — list + clone)
# ============================================================================
@app.route("/api/voices/elevenlabs")
def api_elevenlabs_voices():
    """List all ElevenLabs voices for the configured API key."""
    s = load_settings()
    api_key = s.get("elevenlabs_key", "")
    if not api_key:
        return jsonify({"error": "ElevenLabs API key not configured"}), 400
    voices = elevenlabs_list_voices(api_key)
    return jsonify({"voices": voices, "total": len(voices)})

@app.route("/api/voice_presets")
def api_voice_presets():
    """List all voice presets grouped by category."""
    category = request.args.get("category", "")
    presets = []
    for key, p in VOICE_PRESETS.items():
        if category and p.get("category", "").lower() != category.lower():
            continue
        presets.append({"id": key, **p})
    categories = sorted(set(p.get("category", "Other") for p in VOICE_PRESETS.values()))
    return jsonify({"presets": presets, "categories": categories, "total": len(presets)})

@app.route("/api/voice_presets/<preset_id>")
def api_voice_preset_detail(preset_id):
    p = VOICE_PRESETS.get(preset_id)
    if not p:
        return jsonify({"error": "Preset not found"}), 404
    return jsonify({"id": preset_id, **p})

@app.route("/api/webhooks", methods=["GET"])
def api_webhooks_list():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT id, job_id, url, global_hook, created_at FROM webhooks").fetchall()
        return jsonify({"webhooks": [{"id": r[0], "job_id": r[1], "url": r[2],
                                      "global": bool(r[3]), "created_at": r[4]} for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route("/api/webhooks", methods=["POST"])
def api_webhooks_register():
    data = request.json or {}
    url     = data.get("url", "").strip()
    job_id  = data.get("job_id", "")
    is_glob = data.get("global", True)
    if not url or not url.startswith("http"):
        return jsonify({"error": "Valid URL required"}), 400
    db_register_webhook(url, job_id, is_glob)
    return jsonify({"status": "registered", "url": url, "global": is_glob})

@app.route("/api/webhooks/<int:wid>", methods=["DELETE"])
def api_webhooks_delete(wid):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM webhooks WHERE id=?", (wid,))
        conn.commit()
        return jsonify({"deleted": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route("/api/cloud/test", methods=["POST"])
def api_cloud_test():
    """Test cloud GPU connectivity (Replicate or HuggingFace)."""
    import requests as _r
    s        = load_settings()
    executor = s.get("executor", "local")

    if executor == "huggingface":
        from gradio_client import Client
        SPACES = ["vinthony/SadTalker", "fffiloni/SadTalker", "KwaiVGI/LivePortrait"]
        for space in SPACES:
            try:
                client = Client(space)
                info   = client.view_api(return_format="dict")
                eps    = list(info.get("named_endpoints", {}).keys()) + list(info.get("unnamed_endpoints", {}).keys())
                return jsonify({"ok": True, "space": space,
                                "gpu": "A100 ZeroGPU (free)", "endpoints": eps[:5]})
            except Exception as space_err:
                print(f"  [HF Test] {space}: {space_err}")
                continue
        return jsonify({"ok": False, "error": "All HuggingFace spaces unavailable right now. Try again later or use Local GPU."})

    # Replicate test
    key = s.get("replicate_key", "")
    if not key:
        return jsonify({"ok": False, "error": "Replicate key not configured"})
    try:
        r = _r.get("https://api.replicate.com/v1/models/cjwbw/sadtalker",
                   headers={"Authorization": f"Token {key}"}, timeout=10)
        if r.status_code == 200:
            m = r.json()
            latest = m.get("latest_version", {}).get("id", "")[:12] or "latest"
            return jsonify({"ok": True, "model": "cjwbw/sadtalker",
                            "version": latest, "gpu": "A40/A100 (cloud)"})
        return jsonify({"ok": False, "error": f"API error {r.status_code}: {r.text[:200]}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/ai/detect_voice", methods=["POST"])
def api_detect_voice():
    """Auto-detect script language and suggest best voice."""
    data   = request.json or {}
    script = data.get("script", "").strip()
    if not script:
        return jsonify({"error": "No script"}), 400
    voice = auto_detect_voice(script)
    return jsonify({"voice": voice, "auto_detected": True})

@app.route("/api/vram")
def api_vram():
    """Current VRAM status."""
    free = get_vram_free_gb()
    sufficient = vram_is_sufficient(2.0)
    return jsonify({"vram_free_gb": free, "sufficient": sufficient,
                    "recommendation": "ready" if sufficient else "wait_or_use_cloud"})

@app.route("/api/voices/f5_clone", methods=["POST"])
def api_voices_f5_clone():
    """
    Upload a 5-30s reference audio for F5-TTS local voice cloning (SOTA).
    No API key needed - runs on the local GPU. Returns reference paths to use
    in subsequent /api/generate calls with tts_engine=f5_tts.
    """
    if "audio" not in request.files:
        return jsonify({"error": "audio file required"}), 400
    name     = request.form.get("name", "").strip() or f"voice_{uuid.uuid4().hex[:6]}"
    ref_text = request.form.get("ref_text", "").strip()
    f        = request.files["audio"]

    ref_id   = uuid.uuid4().hex[:8]
    ext      = os.path.splitext(f.filename)[1].lower() or ".wav"
    if ext not in (".wav", ".mp3", ".m4a", ".ogg", ".flac"):
        ext = ".wav"
    ref_path = os.path.join(UPLOAD_DIR, f"f5ref_{ref_id}{ext}")
    f.save(ref_path)

    dur = _get_duration_safe(ref_path)
    if dur < 3:
        _safe_rm(ref_path)
        return jsonify({"error": f"Reference audio too short ({dur:.1f}s). Use 5-30s."}), 400
    if dur > 60:
        return jsonify({"error": f"Reference audio too long ({dur:.1f}s). Trim to 5-30s clean speech."}), 400

    meta = {"id": ref_id, "name": name, "ref_path": ref_path,
            "ref_text": ref_text, "duration": round(dur, 1),
            "created": datetime.now().isoformat()}
    meta_path = os.path.join(UPLOAD_DIR, f"f5ref_{ref_id}.json")
    with open(meta_path, "w", encoding="utf-8") as mf:
        json.dump(meta, mf, ensure_ascii=False)

    return jsonify({
        "ok": True, "voice_ref_id": ref_id, "name": name,
        "voice_ref_audio": ref_path, "duration": meta["duration"],
        "message": f"Voz '{name}' pronta. Use tts_engine=f5_tts e voice_ref_audio em /api/generate."
    })


@app.route("/api/voices/clone", methods=["POST"])
def api_voices_clone():
    """
    Clone a voice using ElevenLabs Instant Voice Cloning.
    Accepts multipart/form-data: name, description, and one or more audio files.
    """
    s = load_settings()
    api_key = s.get("elevenlabs_key", "")
    if not api_key:
        return jsonify({"error": "ElevenLabs API key not configured in Settings"}), 400

    name        = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    if not name:
        return jsonify({"error": "Voice name required"}), 400

    audio_files = request.files.getlist("audio")
    if not audio_files:
        return jsonify({"error": "At least one audio sample required"}), 400

    # Save uploaded samples to disk
    saved = []
    clone_id = uuid.uuid4().hex[:8]
    for i, f in enumerate(audio_files[:5]):
        ext  = os.path.splitext(f.filename)[1] or ".mp3"
        path = os.path.join(UPLOAD_DIR, f"clone_{clone_id}_{i}{ext}")
        f.save(path)
        saved.append(path)

    try:
        voice_id = elevenlabs_clone_voice(name, description, saved, api_key)
        return jsonify({"voice_id": voice_id, "name": name,
                        "message": f"Voice '{name}' cloned successfully!"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        for p in saved:
            _safe_rm(p)

# ============================================================================
# ROUTES — IMAGE TOOLS (enhance, chroma key)
# ============================================================================
@app.route("/api/tools/enhance_image", methods=["POST"])
def api_enhance_image():
    """Enhance uploaded image and optionally save to avatar library."""
    if "image" not in request.files:
        return jsonify({"error": "No image"}), 400
    f         = request.files["image"]
    sharpen   = request.form.get("sharpen",  "true") == "true"
    denoise   = request.form.get("denoise",  "false") == "true"
    brightness= float(request.form.get("brightness", "0"))
    contrast  = float(request.form.get("contrast",   "0"))

    img_id   = uuid.uuid4().hex[:8]
    in_path  = os.path.join(UPLOAD_DIR, f"enh_in_{img_id}.jpg")
    out_path = os.path.join(UPLOAD_DIR, f"enh_out_{img_id}.jpg")
    f.save(in_path)
    enhance_image(in_path, out_path, sharpen=sharpen, denoise=denoise,
                  brightness=brightness, contrast=contrast)
    _safe_rm(in_path)
    if not os.path.exists(out_path):
        return jsonify({"error": "Enhancement failed"}), 500
    return jsonify({"url": f"/uploads/enh_out_{img_id}.jpg", "path": out_path})


@app.route("/api/tools/chroma_key", methods=["POST"])
def api_chroma_key():
    """Apply chroma key to image, returns PNG with transparent background."""
    if "image" not in request.files:
        return jsonify({"error": "No image"}), 400
    f         = request.files["image"]
    color     = request.form.get("color", "green")
    tolerance = int(request.form.get("tolerance", "40"))

    img_id   = uuid.uuid4().hex[:8]
    in_path  = os.path.join(UPLOAD_DIR, f"chroma_in_{img_id}.jpg")
    out_path = os.path.join(UPLOAD_DIR, f"chroma_out_{img_id}.png")
    f.save(in_path)
    result = apply_chroma_key(in_path, out_path, color=color, tolerance=tolerance)
    _safe_rm(in_path)
    fname = os.path.basename(result)
    return jsonify({"url": f"/uploads/{fname}", "path": result})


@app.route("/api/tools/music_library")
def api_music_library():
    """List available background music tracks."""
    music_dir = os.path.join(BASE_DIR, "static", "music")
    os.makedirs(music_dir, exist_ok=True)
    tracks = []
    for fname in sorted(os.listdir(music_dir)):
        if fname.lower().endswith((".mp3", ".wav", ".ogg", ".m4a")):
            fpath = os.path.join(music_dir, fname)
            size  = os.path.getsize(fpath)
            name  = os.path.splitext(fname)[0].replace("_", " ").replace("-", " ").title()
            tracks.append({
                "name": name,
                "file": fname,
                "url":  f"/static/music/{fname}",
                "size_kb": round(size / 1024, 1)
            })
    return jsonify({"tracks": tracks})


@app.route("/api/tools/translate_srt", methods=["POST"])
def api_translate_srt():
    """Translate SRT content to target language."""
    data        = request.json or {}
    srt_content = data.get("srt", "")
    target_lang = data.get("target", "")
    if not srt_content or not target_lang:
        return jsonify({"error": "srt and target required"}), 400
    translated = translate_srt(srt_content, target_lang)
    return jsonify({"translated": translated, "target": target_lang})


# ============================================================================
# ROUTES — EXTERNAL API (integration with StudioPilot)
# ============================================================================
@app.route("/api/external/generate", methods=["POST"])
def api_external_generate():
    """
    External API for integration with StudioPilot or other services.
    Accepts JSON with base64 image or image_url.
    Returns job_id for polling via /api/job/<id>
    """
    data = request.json or {}
    script   = data.get("script", "")
    voice    = data.get("voice", "en-US-GuyNeural")
    plan     = data.get("plan", load_settings().get("plan", DEFAULT_PLAN))
    img_b64  = data.get("image_base64", "")
    img_url  = data.get("image_url", "")

    if not script:
        return jsonify({"error": "script required"}), 400
    if not img_b64 and not img_url:
        return jsonify({"error": "image_base64 or image_url required"}), 400

    img_id   = uuid.uuid4().hex[:8]
    img_path = os.path.join(UPLOAD_DIR, f"ext_{img_id}.jpg")

    if img_b64:
        import base64
        try:
            img_data = base64.b64decode(img_b64)
            with open(img_path, "wb") as f:
                f.write(img_data)
        except Exception as e:
            return jsonify({"error": f"Invalid base64 image: {e}"}), 400
    elif img_url:
        import urllib.request
        try:
            _MAX_IMG_BYTES = 20 * 1024 * 1024  # 20MB cap
            req = urllib.request.Request(img_url, headers={"User-Agent": "AvatarPilot/3.0"})
            with urllib.request.urlopen(req, timeout=10) as _resp:
                _cl = int(_resp.headers.get("Content-Length", 0) or 0)
                if _cl > _MAX_IMG_BYTES:
                    return jsonify({"error": f"Image too large: {_cl//1024//1024}MB (max 20MB)"}), 400
                _data = _resp.read(_MAX_IMG_BYTES + 1)
            if len(_data) > _MAX_IMG_BYTES:
                return jsonify({"error": "Image too large (max 20MB)"}), 400
            with open(img_path, "wb") as f:
                f.write(_data)
        except Exception as e:
            return jsonify({"error": f"Could not download image: {e}"}), 400

    job_id = uuid.uuid4().hex[:12]
    config = {
        "script": script, "voice": voice,
        "tts_engine": data.get("engine", "edge-tts"),
        "image_path": img_path, "preprocess": data.get("preprocess", "crop"),
        "still_mode": data.get("still_mode", False),
        "expression_scale": float(data.get("expression_scale", 1.0)),
        "enhancer": data.get("enhancer", "gfpgan"),
        "size": int(data.get("size", 256)),
        "plan": plan, "source": "external",
    }
    jobs[job_id] = {
        "id": job_id, "status": "queued", "progress": 0,
        "created": datetime.now().isoformat(),
        "message": "External job queued", "error": "", "_config": config,
    }
    t = Thread(target=_safe_pipeline_runner, args=(job_id, config), daemon=True)
    t.start()
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['_thread'] = t
    base = request.host_url.rstrip("/")
    return jsonify({
        "job_id": job_id,
        "status_url":   f"{base}/api/job/{job_id}",
        "download_url": f"{base}/outputs/{job_id}_final.mp4",
    })

# ============================================================================
# ROUTES — DASHBOARD
# ============================================================================
@app.route("/api/dashboard")
def api_dashboard():
    """System monitoring dashboard data."""
    stats = load_stats()

    # GPU info
    gpu_info = {"available": False, "name": "N/A", "vram_total": 0, "vram_free": 0}
    try:
        import torch
        if torch.cuda.is_available():
            gpu_info["available"]   = True
            gpu_info["name"]        = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            gpu_info["vram_total"]  = round(props.total_memory / 1024**3, 1)
            mem   = torch.cuda.mem_get_info(0)
            gpu_info["vram_free"]   = round(mem[0] / 1024**3, 1)
            gpu_info["vram_used"]   = round((mem[1] - mem[0]) / 1024**3, 1)
    except Exception:
        pass

    # Disk usage
    disk_used = 0
    try:
        for fname in os.listdir(OUTPUT_DIR):
            fp = os.path.join(OUTPUT_DIR, fname)
            try:
                if os.path.isfile(fp):
                    disk_used += os.path.getsize(fp)
            except (OSError, FileNotFoundError):
                pass
    except Exception:
        pass

    # Active jobs
    active = sum(1 for j in jobs.values() if j.get("status") in ("queued", "generating_audio", "generating_video", "compositing"))

    # SadTalker
    st_detail = check_sadtalker_detailed()

    # Edge-TTS
    edge_ok = False
    try:
        import edge_tts
        edge_ok = True
    except ImportError:
        pass

    # Uptime
    start_date = stats.get("start_date", datetime.now().isoformat())

    w2l_ready = check_wav2lip()

    return jsonify({
        "total_generated":  stats.get("total_generated", 0),
        "total_seconds":    stats.get("total_seconds", 0),
        "total_hours":      round(stats.get("total_seconds", 0) / 3600, 2),
        "disk_used_mb":     round(disk_used / 1024**2, 1),
        "disk_free_mb":     round(_free_disk_mb(), 0),
        "active_jobs":      active,
        "gpu":              gpu_info,
        "musetalk":         {"ready": check_musetalk(), "engine": "primary" if check_musetalk() else "unavailable"},
        "wav2lip":          {"ready": w2l_ready, "engine": "fallback" if check_musetalk() else ("primary" if w2l_ready else "unavailable")},
        "sadtalker":        {**st_detail, "ready": check_sadtalker(), "engine": "primary" if check_sadtalker() else "unavailable"},
        "echomimic_v2":     {"ready": check_echomimic_v2(), "engine": "premium" if check_echomimic_v2() else "unavailable",
                             "feature": "half-body com gestos (HeyGen-class)"},
        "edge_tts":         edge_ok,
        "plan":             load_settings().get("plan", DEFAULT_PLAN),
        "plan_limits":      {k: (v // 60 if v else None) for k, v in PLAN_LIMITS.items()},
        "start_date":       start_date,
    })

# ============================================================================
# ROUTES — TEMPLATES
# ============================================================================
@app.route("/api/templates")
def api_get_templates():
    return jsonify({"templates": load_templates()})

@app.route("/api/templates/save", methods=["POST"])
def api_save_template():
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Template name required"}), 400
    templates = load_templates()
    template = {
        "id":      uuid.uuid4().hex[:8],
        "name":    name,
        "created": datetime.now().isoformat(),
        "settings": {
            "voice":            data.get("voice", "en-US-GuyNeural"),
            "engine":           data.get("engine", "edge-tts"),
            "preprocess":       data.get("preprocess", "crop"),
            "still_mode":       data.get("still_mode", False),
            "expression_scale": data.get("expression_scale", 1.0),
            "enhancer":         data.get("enhancer", "gfpgan"),
            "size":             data.get("size", 256),
            "background":       data.get("background", ""),
            "avatar_position":  data.get("avatar_position", "bottom_right"),
            "avatar_size":      data.get("avatar_size", "medium"),
            "avatar_opacity":   data.get("avatar_opacity", 1.0),
            "script_template":  data.get("script_template", ""),
        }
    }
    templates.insert(0, template)
    save_templates(templates)
    return jsonify({"id": template["id"], "name": name})

@app.route("/api/templates/<tmpl_id>", methods=["DELETE"])
def api_delete_template(tmpl_id):
    templates = [t for t in load_templates() if t["id"] != tmpl_id]
    save_templates(templates)
    return jsonify({"deleted": True})

# ============================================================================
# ROUTES — BACKGROUNDS
# ============================================================================
@app.route("/api/backgrounds")
def api_backgrounds():
    bgs = []
    for f in os.listdir(BG_DIR):
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            bgs.append({"name": f, "url": f"/backgrounds/{f}"})
    return jsonify({"backgrounds": bgs})

@app.route("/api/gesture_videos")
def api_gesture_videos():
    videos = []
    for f in sorted(os.listdir(GESTURE_VIDEOS_DIR)):
        if f.lower().endswith((".mp4", ".webm", ".mov")):
            fpath = os.path.join(GESTURE_VIDEOS_DIR, f)
            dur_g = _get_duration_safe(fpath)
            # Thumbnail: /static/gesture_videos/<name>.jpg if exists
            thumb_name = os.path.splitext(f)[0] + ".jpg"
            thumb_url = f"/static/gesture_videos/{thumb_name}" if os.path.exists(
                os.path.join(GESTURE_VIDEOS_DIR, thumb_name)) else None
            label = os.path.splitext(f)[0].replace("_", " ").title()
            videos.append({
                "name":  f,
                "label": label,
                "url":   f"/static/gesture_videos/{f}",
                "thumb": thumb_url,
                "duration": round(dur_g, 1),
            })
    return jsonify({"gesture_videos": videos, "face_swap_ready": check_face_swap_ready()})


@app.route("/api/admin/gesture_pack/download", methods=["POST"])
def api_admin_gesture_pack_download():
    """
    Admin-only: download a gesture-pack from Pexels (CC0 commercial-free).
    Body: {"pexels_key": "...", "count": 20}
    Async — returns immediately. Status polled via /api/gesture_videos.
    """
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    key = data.get("pexels_key", "").strip()
    if not key or len(key) < 10:
        return jsonify({"error": "pexels_key required (get free at pexels.com/api)"}), 400
    count = int(data.get("count", 20))

    script = os.path.join(BASE_DIR, "scripts", "download_gesture_pack.py")
    if not os.path.exists(script):
        return jsonify({"error": "download_gesture_pack.py missing"}), 500

    # Run in background — uses main venv (has requests)
    venv_py = sys.executable
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    log_path = os.path.join(BASE_DIR, "logs", "gesture_pack_download.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def _runner():
        try:
            with open(log_path, "w", encoding="utf-8") as lf:
                subprocess.run(
                    [venv_py, script, "--key", key, "--count", str(count)],
                    stdout=lf, stderr=subprocess.STDOUT, timeout=3600, env=env,
                )
            print(f"  [GesturePack] Download complete — see {log_path}", flush=True)
        except Exception as _e:
            print(f"  [GesturePack] Download failed: {_e}", flush=True)

    Thread(target=_runner, daemon=True).start()
    return jsonify({
        "ok": True,
        "message": f"Download started (background). Check /api/gesture_videos to see progress.",
        "log_path": log_path,
    })

@app.route("/api/gesture_videos/upload", methods=["POST"])
def api_upload_gesture_video():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    _base = os.path.basename(f.filename)
    safe_name = re.sub(r'[^\w\-_\.]', '_', _base)
    if not safe_name:
        safe_name = f"gesture_{uuid.uuid4().hex[:8]}.mp4"
    _ext = os.path.splitext(safe_name)[1].lower()
    if _ext not in (".mp4", ".webm", ".mov"):
        return jsonify({"error": "Unsupported file type. Use MP4, WEBM or MOV."}), 400
    _dest = os.path.normpath(os.path.join(GESTURE_VIDEOS_DIR, safe_name))
    if not _dest.startswith(os.path.normpath(GESTURE_VIDEOS_DIR)):
        return jsonify({"error": "Invalid filename"}), 400
    f.save(_dest)
    # Generate thumbnail
    _thumb_dest = _dest.replace(_ext, ".jpg")
    try:
        _ff = _ffmpeg_path()
        subprocess.run([_ff, "-y", "-i", _dest, "-ss", "1", "-frames:v", "1",
                        "-q:v", "3", _thumb_dest],
                       capture_output=True, timeout=30)
    except Exception:
        pass
    return jsonify({"status": "ok", "name": safe_name})

@app.route("/api/gesture_videos/<name>", methods=["DELETE"])
def api_delete_gesture_video(name):
    safe_name = re.sub(r'[^\w\-_\.]', '_', os.path.basename(name))
    _target = os.path.normpath(os.path.join(GESTURE_VIDEOS_DIR, safe_name))
    if not _target.startswith(os.path.normpath(GESTURE_VIDEOS_DIR)):
        return jsonify({"error": "Invalid filename"}), 400
    if os.path.exists(_target):
        os.remove(_target)
    _thumb = _target.rsplit(".", 1)[0] + ".jpg"
    if os.path.exists(_thumb):
        os.remove(_thumb)
    return jsonify({"status": "ok"})

@app.route("/api/backgrounds/upload", methods=["POST"])
def api_upload_bg():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    # Prevent path traversal: use only basename, strip special chars
    _base = os.path.basename(f.filename)
    safe_name = re.sub(r'[^\w\-_\.]', '_', _base)
    if not safe_name:
        safe_name = f"bg_{uuid.uuid4().hex[:8]}.jpg"
    # Validate extension
    _ext = os.path.splitext(safe_name)[1].lower()
    if _ext not in (".jpg", ".jpeg", ".png", ".webp"):
        return jsonify({"error": "Unsupported file type. Use JPG, PNG or WEBP."}), 400
    # Ensure final path stays inside BG_DIR
    _dest = os.path.normpath(os.path.join(BG_DIR, safe_name))
    if not _dest.startswith(os.path.normpath(BG_DIR)):
        return jsonify({"error": "Invalid filename"}), 400
    f.save(_dest)
    return jsonify({"status": "ok", "name": safe_name})


# ============================================================================
# ROUTES — AVATAR CREATOR
# ============================================================================
@app.route("/api/avatar/create", methods=["POST"])
def api_avatar_create():
    """Generate a photorealistic avatar person from description."""
    data        = request.json or {}
    description = data.get("description", "professional person")
    clothing    = data.get("clothing", "business casual outfit")
    style       = data.get("style", "photorealistic")
    gender      = data.get("gender", "")
    age         = data.get("age", "")
    bg_desc     = data.get("background", "studio white background")
    name        = data.get("name", f"Avatar {datetime.now().strftime('%H:%M')}")

    try:
        img_path = generate_avatar_person(
            description=description, clothing=clothing, style=style,
            gender=gender, age=age, background_desc=bg_desc
        )
        # Save to library
        lib = _load_avatar_library()
        entry = {
            "id":          uuid.uuid4().hex[:8],
            "name":        name,
            "path":        img_path,
            "url":         f"/uploads/{os.path.basename(img_path)}",
            "description": description,
            "clothing":    clothing,
            "style":       style,
            "gender":      gender,
            "age":         age,
            "created":     datetime.now().isoformat(),
        }
        lib.insert(0, entry)
        lib = lib[:50]  # keep max 50 avatars
        _save_avatar_library(lib)
        return jsonify({"status": "ok", "avatar": entry})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/avatar/library")
def api_avatar_library():
    """List saved avatars."""
    lib = _load_avatar_library()
    # Filter out avatars with missing files
    lib = [a for a in lib if os.path.exists(a.get("path", ""))]
    return jsonify({"avatars": lib})


@app.route("/api/avatar/stock_library")
def api_avatar_stock_library():
    """List built-in stock avatars from static/stock_avatars/."""
    stock_dir = os.path.join(BASE_DIR, "static", "stock_avatars")
    os.makedirs(stock_dir, exist_ok=True)
    avatars = []
    for fname in sorted(os.listdir(stock_dir)):
        if fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            name = os.path.splitext(fname)[0].replace("_", " ")
            avatars.append({
                "id":   f"stock_{os.path.splitext(fname)[0]}",
                "name": name,
                "url":  f"/static/stock_avatars/{fname}",
                "path": os.path.join(stock_dir, fname),
                "source": "stock",
            })
    return jsonify({"avatars": avatars})


@app.route("/api/avatar/delete/<avatar_id>", methods=["DELETE"])
def api_avatar_delete(avatar_id):
    """Delete avatar from library and disk."""
    lib = _load_avatar_library()
    target = next((a for a in lib if a.get("id") == avatar_id), None)
    if not target:
        return jsonify({"error": "Avatar not found"}), 404
    # Remove file if it exists
    path = target.get("path", "")
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
    lib = [a for a in lib if a.get("id") != avatar_id]
    _save_avatar_library(lib)
    return jsonify({"ok": True, "deleted": avatar_id})


@app.route("/api/avatar/upload", methods=["POST"])
def api_avatar_upload():
    """Upload a real photo and save it to the avatar library."""
    if "image" not in request.files:
        return jsonify({"error": "No image file"}), 400
    img_file = request.files["image"]
    name     = request.form.get("name", "").strip() or f"Foto {datetime.now().strftime('%d/%m %H:%M')}"

    img_id  = uuid.uuid4().hex[:8]
    img_ext = os.path.splitext(img_file.filename)[1].lower() or ".jpg"
    img_path = os.path.join(UPLOAD_DIR, f"avatar_upload_{img_id}{img_ext}")
    img_file.save(img_path)

    # Auto-resize if too large
    try:
        import cv2 as _cv2u
        import numpy as _npu
        _raw = open(img_path, 'rb').read()
        _im  = _cv2u.imdecode(_npu.frombuffer(_raw, _npu.uint8), _cv2u.IMREAD_COLOR)
        if _im is not None:
            _h, _w = _im.shape[:2]
            if max(_h, _w) > 1280:
                _sc = 1280 / max(_h, _w)
                _im = _cv2u.resize(_im, (int(_w * _sc), int(_h * _sc)))
                _ok, _enc = _cv2u.imencode('.jpg', _im, [_cv2u.IMWRITE_JPEG_QUALITY, 95])
                if _ok:
                    with open(img_path, 'wb') as _f: _f.write(_enc.tobytes())
    except Exception as _e:
        print(f"  [AvatarUpload] resize skipped: {_e}")

    entry = {
        "id":          img_id,
        "name":        name,
        "path":        img_path,
        "url":         f"/uploads/avatar_upload_{img_id}{img_ext}",
        "description": "Uploaded photo",
        "clothing":    "",
        "style":       "photo",
        "gender":      "",
        "age":         "",
        "created":     datetime.now().isoformat(),
    }
    lib = _load_avatar_library()
    lib.insert(0, entry)
    lib = lib[:50]
    _save_avatar_library(lib)
    return jsonify({"status": "ok", "avatar": entry})


@app.route("/api/cleanup", methods=["POST"])
def api_cleanup():
    """Delete output files older than N days to free disk space."""
    data        = request.json or {}
    older_than  = int(data.get("older_than_days", 7))
    cutoff      = time.time() - older_than * 86400
    deleted     = 0
    freed_bytes = 0
    patterns    = ("_final.mp4", "_avatar.mp4", "_audio.mp3", "_audio.wav",
                   "_captioned.mp4", "_thumb.jpg", ".srt")
    for fname in os.listdir(OUTPUT_DIR):
        fpath = os.path.join(OUTPUT_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        if any(fname.endswith(p) for p in patterns):
            if os.path.getmtime(fpath) < cutoff:
                freed_bytes += os.path.getsize(fpath)
                try:
                    os.remove(fpath)
                    deleted += 1
                except Exception:
                    pass
    return jsonify({
        "ok": True,
        "deleted": deleted,
        "freed_mb": round(freed_bytes / 1024**2, 1)
    })


@app.route("/api/avatar/change_clothing", methods=["POST"])
def api_avatar_change_clothing():
    """Change avatar clothing using AI img2img."""
    data           = request.json or {}
    avatar_id      = data.get("avatar_id", "")
    new_clothing   = data.get("clothing", "")
    person_desc    = data.get("description", "")
    style          = data.get("style", "photorealistic")

    if not new_clothing:
        return jsonify({"error": "Clothing description required"}), 400

    # Find avatar in library
    lib    = _load_avatar_library()
    avatar = next((a for a in lib if a.get("id") == avatar_id), None)

    if avatar:
        img_path    = avatar["path"]
        person_desc = person_desc or avatar.get("description", "")
        if not person_desc:
            person_desc = avatar.get("name", "professional person")
    elif "image_path" in data:
        img_path = data["image_path"]
    else:
        return jsonify({"error": "Avatar not found"}), 404

    if not os.path.exists(img_path):
        return jsonify({"error": "Avatar image file not found"}), 404

    try:
        new_path = change_avatar_clothing(img_path, new_clothing, person_desc, style)
        # Save new variant to library
        if avatar:
            new_entry = {**avatar,
                "id":      uuid.uuid4().hex[:8],
                "name":    f"{avatar['name']} — {new_clothing[:30]}",
                "path":    new_path,
                "url":     f"/uploads/{os.path.basename(new_path)}",
                "clothing": new_clothing,
                "created":  datetime.now().isoformat(),
                "parent_id": avatar_id,
            }
            lib.insert(0, new_entry)
            _save_avatar_library(lib[:50])
            return jsonify({"status": "ok", "avatar": new_entry})
        return jsonify({"status": "ok", "url": f"/uploads/{os.path.basename(new_path)}", "path": new_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/avatar/remove_bg", methods=["POST"])
def api_avatar_remove_bg():
    """Remove background from uploaded image."""
    body_json = request.get_json(silent=True) or {}
    if "image" not in request.files and "image_path" not in body_json:
        return jsonify({"error": "No image provided"}), 400

    try:
        if "image" in request.files:
            f        = request.files["image"]
            img_id   = uuid.uuid4().hex[:8]
            ext      = os.path.splitext(f.filename)[1] or ".jpg"
            img_path = os.path.join(UPLOAD_DIR, f"avatar_raw_{img_id}{ext}")
            f.save(img_path)
        else:
            img_path = body_json["image_path"]

        out_path = os.path.join(UPLOAD_DIR, f"avatar_nobg_{uuid.uuid4().hex[:8]}.png")
        remove_background(img_path, out_path)
        return jsonify({"status": "ok", "url": f"/uploads/{os.path.basename(out_path)}",
                        "path": out_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/avatar/transcribe", methods=["POST"])
def api_transcribe():
    """Transcribe audio file to SRT captions."""
    body_json = request.get_json(silent=True) or {}
    if "audio" not in request.files and "audio_path" not in body_json:
        return jsonify({"error": "No audio provided"}), 400

    try:
        if "audio" in request.files:
            f        = request.files["audio"]
            aud_id   = uuid.uuid4().hex[:8]
            ext      = os.path.splitext(f.filename)[1] or ".mp3"
            aud_path = os.path.join(UPLOAD_DIR, f"transcribe_{aud_id}{ext}")
            f.save(aud_path)
        else:
            aud_path = body_json["audio_path"]

        lang  = (request.form.get("language") or body_json.get("language")) or None
        model = (request.form.get("model") or body_json.get("model", "base"))
        srt   = transcribe_to_srt(aud_path, language=lang, model_size=model)
        return jsonify({"status": "ok", "srt": srt,
                        "lines": len([l for l in srt.split('\n') if l.strip() and not l.strip().isdigit() and '-->' not in l])})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# ROUTES — PUBLIC PRICING PAGE
# ============================================================================
@app.route("/pricing")
def page_pricing():
    return render_template("pricing.html")


@app.route("/api/pricing/plans")
def api_pricing_plans():
    """Public endpoint — returns plan info + whether Stripe is configured."""
    cfg = _get_stripe_keys()
    stripe_ok = bool(cfg["secret_key"] and (cfg["price_starter"] or cfg["price_pro"]))
    return jsonify({
        "stripe_configured": stripe_ok,
        "plans": {
            "free":    {"name": "Free",    "limit_min": 5,    "price_brl": 0},
            "starter": {"name": "Starter", "limit_min": 30,   "price_brl": 97},
            "pro":     {"name": "Pro",     "limit_min": 60,   "price_brl": 197},
        }
    })


@app.route("/api/pricing/checkout", methods=["POST"])
def api_pricing_checkout():
    """Public endpoint — creates a Stripe checkout session and returns the URL."""
    # Rate limit: 5 checkouts per IP per minute to prevent abuse
    ip = request.remote_addr or "unknown"
    if not _check_rate_limit(ip):
        return jsonify({"error": "Muitas tentativas. Aguarde um momento."}), 429

    data  = request.json or {}
    plan  = data.get("plan", "")
    email = (data.get("email", "") or "").strip()
    name  = (data.get("name", "") or "").strip()[:100]

    if plan not in ("starter", "pro"):
        return jsonify({"error": "Plano inválido. Use 'starter' ou 'pro'."}), 400
    if not email or "@" not in email or len(email) > 200:
        return jsonify({"error": "E-mail inválido."}), 400

    try:
        import stripe as _stripe
    except ImportError:
        return jsonify({"error": "Pagamentos online não disponíveis no momento."}), 503

    cfg = _get_stripe_keys()
    if not cfg["secret_key"]:
        return jsonify({"error": "Pagamentos online não configurados. Entre em contato."}), 503

    price_id = cfg["price_starter"] if plan == "starter" else cfg["price_pro"]
    if not price_id:
        return jsonify({"error": f"Plano {plan} ainda não disponível para compra online."}), 503

    _stripe.api_key = cfg["secret_key"]
    try:
        session = _stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="payment",
            success_url=cfg["success_url"],
            cancel_url=cfg["cancel_url"] if cfg["cancel_url"] else request.referrer or cfg["success_url"],
            customer_email=email or None,
            metadata={"plan": plan, "customer_name": name},
        )
        return jsonify({"url": session.url, "session_id": session.id})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ============================================================================
# ROUTES — SETTINGS
# ============================================================================
@app.route("/api/settings")
def api_get_settings():
    s = dict(load_settings())
    if s.get("elevenlabs_key"):
        s["elevenlabs_key_set"] = True
        s["elevenlabs_key"]     = "***" + s["elevenlabs_key"][-4:]
    if s.get("replicate_key"):
        s["replicate_key_set"] = True
        s["replicate_key"]     = "r8_***" + s["replicate_key"][-4:]
    groq_key = _get_groq_key()
    s["groq_key_set"] = bool(groq_key)
    s["groq_key"]     = ("***" + groq_key[-4:]) if groq_key else ""
    return jsonify(s)

@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.json or {}
    s    = load_settings()
    editable = ("elevenlabs_key", "tts_engine", "default_voice", "plan",
                "replicate_key", "executor", "watermark_text", "watermark_pos",
                "watermark_opacity", "watermark_size", "watermark_color")
    for field in editable:
        if field in data and data[field] is not None:
            s[field] = data[field]
    save_settings(s)

    # Save Groq key separately in .api_keys.json
    if "groq_key" in data and data["groq_key"] and data["groq_key"] != "***":
        import base64
        keys_path = os.path.normpath(os.path.join(BASE_DIR, "..", ".api_keys.json"))
        try:
            existing = {}
            if os.path.exists(keys_path):
                with open(keys_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            existing["groq"] = base64.b64encode(data["groq_key"].strip().encode()).decode()
            with open(keys_path, "w", encoding="utf-8") as f:
                json.dump(existing, f)
        except Exception as _ke:
            print(f"  [Settings] Failed to save Groq key: {_ke}")

    return jsonify({"status": "ok", "plan": s.get("plan", DEFAULT_PLAN)})

# ============================================================================
# STRIPE INTEGRATION
# ============================================================================
_STRIPE_CFG_FILE = os.path.join(BASE_DIR, ".stripe_cfg.json")

def _load_stripe_cfg() -> dict:
    if os.path.exists(_STRIPE_CFG_FILE):
        try:
            with open(_STRIPE_CFG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_stripe_cfg(data: dict):
    existing = _load_stripe_cfg()
    existing.update(data)
    with open(_STRIPE_CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

def _get_stripe_keys() -> dict:
    c = _load_stripe_cfg()
    return {
        "secret_key":       c.get("stripe_secret_key", ""),
        "webhook_secret":   c.get("stripe_webhook_secret", ""),
        "price_starter":    c.get("stripe_price_starter", ""),
        "price_pro":        c.get("stripe_price_pro", ""),
        "price_free":       c.get("stripe_price_free", ""),
        "success_url":      c.get("stripe_success_url", "http://localhost:5052/?payment=success"),
        "cancel_url":       c.get("stripe_cancel_url",  "http://localhost:5052/?payment=cancel"),
    }

def _send_key_email(to_email: str, customer_name: str, api_key: str, plan: str) -> bool:
    c = _load_stripe_cfg()
    smtp_host = c.get("smtp_host", "")
    smtp_port = int(c.get("smtp_port", 587))
    smtp_user = c.get("smtp_user", "")
    smtp_pass = c.get("smtp_pass", "")
    smtp_from = c.get("smtp_from", smtp_user)
    if not smtp_host or not smtp_user:
        print(f"  [Stripe] SMTP não configurado — chave para {to_email}: {api_key}", flush=True)
        return False
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    plan_limits = {"free": "5 minutos/mês", "starter": "30 minutos/mês", "pro": "1 hora/mês", "unlimited": "Ilimitado"}
    limit_txt = plan_limits.get(plan, plan.title())
    html_body = f"""
<html><body style="font-family:Arial,sans-serif;background:#0f0f18;color:#cdd6f4;padding:32px">
<div style="max-width:520px;margin:auto;background:#1e1e2e;border-radius:12px;padding:32px">
  <h2 style="color:#cba6f7;margin-top:0">🎉 Bem-vindo ao AvatarPilot Pro!</h2>
  <p>Olá <strong>{customer_name}</strong>, seu pagamento foi confirmado.</p>
  <p>Aqui está sua API Key — <strong>guarde-a agora:</strong></p>
  <div style="background:#181825;border-radius:8px;padding:16px;font-family:monospace;font-size:15px;color:#a6e3a1;word-break:break-all;margin:16px 0">{api_key}</div>
  <table style="width:100%;border-collapse:collapse;margin:16px 0">
    <tr><td style="color:#888;padding:4px 0">Plano</td><td style="color:#cba6f7;font-weight:bold">{plan.title()}</td></tr>
    <tr><td style="color:#888;padding:4px 0">Limite</td><td style="color:#cdd6f4">{limit_txt}</td></tr>
  </table>
  <p style="font-size:13px;color:#888">Para usar: adicione o header <code style="color:#89dceb">X-API-Key: {api_key}</code> nas suas requests à API.</p>
  <hr style="border-color:#313244;margin:24px 0">
  <p style="font-size:12px;color:#585b70">AvatarPilot Pro — Em caso de dúvidas responda este e-mail.</p>
</div></body></html>
"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Sua API Key AvatarPilot Pro — Plano {plan.title()}"
    msg["From"]    = smtp_from
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html"))
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(smtp_user, smtp_pass)
            srv.sendmail(smtp_from, to_email, msg.as_string())
        print(f"  [Stripe] Email enviado para {to_email}", flush=True)
        return True
    except Exception as e:
        print(f"  [Stripe] Falha ao enviar email: {e}", flush=True)
        return False

@app.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    try:
        import stripe as _stripe
    except ImportError:
        return jsonify({"error": "stripe não instalado"}), 503
    cfg = _get_stripe_keys()
    if not cfg["secret_key"]:
        return jsonify({"error": "Stripe não configurado"}), 503
    _stripe.api_key = cfg["secret_key"]
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        if cfg["webhook_secret"]:
            event = _stripe.Webhook.construct_event(payload, sig, cfg["webhook_secret"])
        else:
            # webhook_secret not configured: only allow in dev (localhost) to prevent spoofing
            remote = request.remote_addr or ""
            if remote not in ("127.0.0.1", "::1"):
                print(f"  [Stripe] Rejected unsigned webhook from {remote} — configure webhook_secret", flush=True)
                return jsonify({"error": "webhook_secret not configured — unsigned webhooks rejected"}), 403
            event = json.loads(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    if event.get("type") == "checkout.session.completed":
        session  = event["data"]["object"]
        email    = (session.get("customer_email") or
                    session.get("customer_details", {}).get("email", ""))
        meta     = session.get("metadata") or {}
        plan     = meta.get("plan", "starter")
        name     = meta.get("customer_name") or (email.split("@")[0] if email else "Cliente")
        amount   = session.get("amount_total", 0)
        currency = (session.get("currency") or "brl").upper()
        api_key  = apikey_create(name, plan)
        email_ok = _send_key_email(email, name, api_key, plan) if email else False
        print(f"  [Stripe] Pagamento {amount/100:.2f} {currency} — {name} ({plan})"
              f" → key criada, email={'OK' if email_ok else 'SKIP'}", flush=True)
        # ── Licença desktop: se o cliente informou o hardware_id no checkout,
        #    assina a licença e envia por email. Senão, envia instruções de ativação.
        if _LICENSE_AVAILABLE:
            try:
                _hwid = (meta.get("hardware_id") or "").strip()
                _days = {"starter": 365, "pro": 365, "unlimited": 0}.get(plan, 365)
                if _hwid:
                    _license = _lic.sign_license(_hwid, plan=plan, days=_days, customer=name)
                    _send_license_email(email, name, _license, plan) if email else None
                    print(f"  [License] assinada p/ hwid={_hwid[:12]}... plan={plan}", flush=True)
                else:
                    print(f"  [License] sem hardware_id no checkout — cliente deve ativar manualmente", flush=True)
            except Exception as _le:
                print(f"  [License] falha ao assinar: {_le}", flush=True)
    return jsonify({"received": True})


# ============================================================================
# ROUTES — LICENÇA DESKTOP (hardware-bound)
# ============================================================================
@app.route("/api/license/hardware_id")
def api_license_hwid():
    """ID de hardware desta máquina — o cliente informa ao comprar/ativar."""
    if not _LICENSE_AVAILABLE:
        return jsonify({"error": "Licenciamento indisponível"}), 503
    return jsonify({"hardware_id": _lic.get_hardware_id()})

@app.route("/api/license/status")
def api_license_status():
    """Estado da licença ativa nesta máquina (plano, expiração, etc.)."""
    if not _LICENSE_AVAILABLE:
        return jsonify({"active": False, "plan": "unlimited",
                        "reason": "Licenciamento desabilitado (dev)"})
    st = _lic.load_active_license()
    # não expor dados sensíveis além do necessário
    return jsonify({
        "active":      st.get("active", False),
        "plan":        st.get("plan", "trial"),
        "expires":     st.get("expires", "never"),
        "customer":    st.get("customer", ""),
        "hardware_id": st.get("hardware_id", ""),
        "reason":      st.get("reason", ""),
        "limits":      st.get("limits", {}),
    })

@app.route("/api/license/activate", methods=["POST"])
def api_license_activate():
    """Cliente cola a string de licença; valida p/ ESTA máquina e salva."""
    if not _LICENSE_AVAILABLE:
        return jsonify({"error": "Licenciamento indisponível"}), 503
    data = request.json or {}
    lic_str = (data.get("license") or "").strip()
    if not lic_str:
        return jsonify({"error": "Informe a licença (campo 'license')"}), 400
    ok, res = _lic.activate(lic_str)
    if not ok:
        return jsonify({"error": str(res)}), 400
    global _LICENSE_STATE
    _LICENSE_STATE = _lic.load_active_license()
    return jsonify({"ok": True, "plan": res.get("plan"), "expires": res.get("expires", "never"),
                    "message": "Licença ativada com sucesso!"})

@app.route("/api/license/deactivate", methods=["POST"])
def api_license_deactivate():
    if not _LICENSE_AVAILABLE:
        return jsonify({"error": "Licenciamento indisponível"}), 503
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    ok = _lic.deactivate()
    global _LICENSE_STATE
    _LICENSE_STATE = _lic.load_active_license()
    return jsonify({"ok": ok})

@app.route("/api/admin/license/generate", methods=["POST"])
def api_admin_license_generate():
    """VENDOR (admin): assina uma licença p/ um hardware_id. Usado p/ emissão manual."""
    if not _LICENSE_AVAILABLE:
        return jsonify({"error": "Licenciamento indisponível"}), 503
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    hwid = (data.get("hardware_id") or "").strip()
    plan = data.get("plan", "pro")
    try:
        days = int(data.get("days", 365))
    except (ValueError, TypeError):
        days = 365
    customer = (data.get("customer") or "").strip()
    if not hwid or len(hwid) < 8:
        return jsonify({"error": "hardware_id inválido"}), 400
    if plan not in _lic.PLAN_LIMITS:
        return jsonify({"error": f"plano inválido — use: {', '.join(_lic.PLAN_LIMITS)}"}), 400
    try:
        lic_str = _lic.sign_license(hwid, plan=plan, days=days, customer=customer)
        return jsonify({"license": lic_str, "plan": plan, "days": days,
                        "hardware_id": hwid, "customer": customer})
    except Exception as e:
        return jsonify({"error": f"Falha ao assinar: {e}"}), 500


def _send_license_email(email, name, license_str, plan):
    """Envia a licença por email (reusa infra de email do _send_key_email)."""
    try:
        return _send_key_email(email, name, license_str, plan, subject_kind="license")
    except TypeError:
        # _send_key_email pode não aceitar subject_kind — fallback
        try:
            return _send_key_email(email, name, license_str, plan)
        except Exception:
            return False
    except Exception:
        return False


@app.route("/api/admin/stripe/status")
def stripe_admin_status():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    cfg = _get_stripe_keys()
    sk  = cfg["secret_key"]
    return jsonify({
        "configured":   bool(sk),
        "mode":         "test" if sk.startswith("sk_test_") else ("live" if sk.startswith("sk_live_") else "none"),
        "webhook_set":  bool(cfg["webhook_secret"]),
        "prices": {
            "free":    bool(cfg["price_free"]),
            "starter": bool(cfg["price_starter"]),
            "pro":     bool(cfg["price_pro"]),
        },
        "smtp_set": bool(_load_stripe_cfg().get("smtp_host")),
    })

@app.route("/api/admin/stripe/config", methods=["GET"])
def stripe_admin_get_config():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    c = _load_stripe_cfg()
    def _mask(v): return ("***" + v[-4:]) if len(v) > 8 else ("***" if v else "")
    return jsonify({
        "stripe_secret_key":     _mask(c.get("stripe_secret_key", "")),
        "stripe_webhook_secret": _mask(c.get("stripe_webhook_secret", "")),
        "stripe_price_starter":  c.get("stripe_price_starter", ""),
        "stripe_price_pro":      c.get("stripe_price_pro", ""),
        "stripe_price_free":     c.get("stripe_price_free", ""),
        "stripe_success_url":    c.get("stripe_success_url", ""),
        "stripe_cancel_url":     c.get("stripe_cancel_url", ""),
        "smtp_host":  c.get("smtp_host", ""),
        "smtp_port":  c.get("smtp_port", 587),
        "smtp_user":  c.get("smtp_user", ""),
        "smtp_pass":  _mask(c.get("smtp_pass", "")),
        "smtp_from":  c.get("smtp_from", ""),
    })

@app.route("/api/admin/stripe/config", methods=["POST"])
def stripe_admin_save_config():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    allowed = ("stripe_secret_key", "stripe_webhook_secret", "stripe_price_starter",
               "stripe_price_pro", "stripe_price_free", "stripe_success_url", "stripe_cancel_url",
               "smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from")
    to_save = {k: v for k, v in data.items() if k in allowed and v not in (None, "", "***")}
    _save_stripe_cfg(to_save)
    return jsonify({"ok": True})

@app.route("/api/admin/stripe/create_checkout", methods=["POST"])
def stripe_create_checkout():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        import stripe as _stripe
    except ImportError:
        return jsonify({"error": "stripe não instalado. Execute: pip install stripe"}), 503
    cfg = _get_stripe_keys()
    if not cfg["secret_key"]:
        return jsonify({"error": "Configure stripe_secret_key nas Configurações"}), 400
    _stripe.api_key = cfg["secret_key"]
    data    = request.json or {}
    plan    = data.get("plan", "starter")
    prices  = {"starter": cfg["price_starter"], "pro": cfg["price_pro"], "free": cfg["price_free"]}
    price_id = prices.get(plan, "")
    if not price_id:
        return jsonify({"error": f"Configure stripe_price_{plan} nas Configurações → Stripe"}), 400
    try:
        session = _stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="payment",
            success_url=cfg["success_url"],
            cancel_url=cfg["cancel_url"],
            customer_email=data.get("customer_email") or None,
            metadata={"plan": plan, "customer_name": data.get("customer_name", "")},
        )
        return jsonify({"url": session.url, "session_id": session.id, "plan": plan})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/admin/stripe/create_link", methods=["POST"])
def stripe_create_link():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        import stripe as _stripe
    except ImportError:
        return jsonify({"error": "stripe não instalado"}), 503
    cfg = _get_stripe_keys()
    if not cfg["secret_key"]:
        return jsonify({"error": "Configure stripe_secret_key nas Configurações"}), 400
    _stripe.api_key = cfg["secret_key"]
    data     = request.json or {}
    plan     = data.get("plan", "starter")
    prices   = {"starter": cfg["price_starter"], "pro": cfg["price_pro"], "free": cfg["price_free"]}
    price_id = prices.get(plan, "")
    if not price_id:
        return jsonify({"error": f"Configure stripe_price_{plan} nas Configurações → Stripe"}), 400
    try:
        link = _stripe.PaymentLink.create(
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={"plan": plan},
            after_completion={"type": "redirect", "redirect": {"url": cfg["success_url"]}},
        )
        return jsonify({"url": link.url, "id": link.id, "plan": plan})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/admin/stripe/test_email", methods=["POST"])
def stripe_test_email():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data  = request.json or {}
    email = data.get("email", "")
    if not email:
        return jsonify({"error": "Email obrigatório"}), 400
    ok = _send_key_email(email, "Teste AvatarPilot", "avp_TEST_KEY_EXEMPLO_12345", "pro")
    return jsonify({"ok": ok, "message": "Email enviado!" if ok else "Falha — verifique configurações SMTP"})

# ============================================================================
# ROUTES — ADMIN ANALYTICS
# ============================================================================
@app.route("/api/admin/analytics")
def admin_analytics():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        daily = conn.execute("""
            SELECT substr(created_at,1,10) as day,
                   COUNT(*) as jobs,
                   COALESCE(SUM(duration),0) as secs
            FROM jobs_db
            WHERE status='done' AND created_at >= date('now','-30 days')
            GROUP BY day ORDER BY day ASC
        """).fetchall()

        top_clients = conn.execute("""
            SELECT name, plan, jobs_generated,
                   COALESCE(seconds_generated,0) as secs, active
            FROM api_keys
            ORDER BY jobs_generated DESC LIMIT 10
        """).fetchall()

        by_plan = conn.execute("""
            SELECT plan,
                   COUNT(*) as keys,
                   COALESCE(SUM(jobs_generated),0) as jobs,
                   COALESCE(SUM(seconds_generated),0) as secs
            FROM api_keys GROUP BY plan
        """).fetchall()

        totals = conn.execute("""
            SELECT COUNT(*) as total_jobs,
                   COALESCE(SUM(duration),0) as total_secs
            FROM jobs_db WHERE status='done'
        """).fetchone()

        recent = conn.execute("""
            SELECT id, status, output_filename, created_at,
                   COALESCE(duration,0) as duration
            FROM jobs_db ORDER BY created_at DESC LIMIT 10
        """).fetchall()

    except Exception as e:
        if conn: conn.close()
        return jsonify({"error": str(e)}), 500
    conn.close()

    return jsonify({
        "daily":       [{"day": r["day"], "jobs": r["jobs"],
                         "hours": round(r["secs"] / 3600, 2)} for r in daily],
        "top_clients": [{"name": r["name"], "plan": r["plan"],
                         "jobs": r["jobs_generated"],
                         "hours": round(r["secs"] / 3600, 2),
                         "active": bool(r["active"])} for r in top_clients],
        "by_plan":     [{"plan": r["plan"], "keys": r["keys"],
                         "jobs": r["jobs"],
                         "hours": round(r["secs"] / 3600, 2)} for r in by_plan],
        "total_jobs":  totals["total_jobs"],
        "total_hours": round(totals["total_secs"] / 3600, 2),
        "recent":      [{"id": r["id"][:8], "status": r["status"],
                         "filename": r["output_filename"],
                         "created_at": (r["created_at"] or "")[:16],
                         "duration": round(r["duration"], 1)} for r in recent],
    })


# ============================================================================
# ROUTES — ADMIN (API Key Management)
# ============================================================================

@app.route("/api/admin/keys", methods=["GET"])
def admin_list_keys():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized — X-Admin-Token inválido"}), 401
    keys = apikey_list()
    # Mask key — show only last 8 chars
    for k in keys:
        k["key_masked"] = "avp_****" + k["key"][-8:]
    return jsonify({"keys": keys, "total": len(keys)})

@app.route("/api/admin/keys", methods=["POST"])
def admin_create_key():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    name = data.get("name", "").strip()
    plan = data.get("plan", "starter")
    if not name:
        return jsonify({"error": "name é obrigatório"}), 400
    if plan not in ("free", "starter", "pro", "unlimited"):
        return jsonify({"error": "plan inválido — use: free, starter, pro, unlimited"}), 400
    key = apikey_create(name, plan)
    return jsonify({"key": key, "name": name, "plan": plan, "message": "Guarde esta chave! Ela não será exibida novamente."})

@app.route("/api/admin/keys/<key_id>/revoke", methods=["POST"])
def admin_revoke_key(key_id):
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    apikey_revoke(key_id)
    return jsonify({"ok": True, "message": "Chave revogada"})

@app.route("/api/admin/keys/<key_id>", methods=["DELETE"])
def admin_delete_key(key_id):
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    apikey_delete(key_id)
    return jsonify({"ok": True, "message": "Chave deletada"})

@app.route("/api/admin/keys/<key_id>/activate", methods=["POST"])
def admin_activate_key(key_id):
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    _api_key_db(write_fn=lambda c: c.execute(
        "UPDATE api_keys SET active=1 WHERE key=?", (key_id,)
    ))
    return jsonify({"ok": True, "message": "Chave reativada"})

@app.route("/api/admin/auth_mode", methods=["POST"])
def admin_set_auth_mode():
    """Enable or disable mandatory API key authentication."""
    global AUTH_REQUIRED
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    AUTH_REQUIRED = bool(data.get("required", False))
    return jsonify({"auth_required": AUTH_REQUIRED})

@app.route("/api/admin/status")
def admin_status():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    keys = apikey_list()
    active_keys  = sum(1 for k in keys if k["active"])
    total_jobs   = sum(k["jobs_generated"] for k in keys)
    total_secs   = sum(k["seconds_generated"] for k in keys)
    return jsonify({
        "auth_required":   AUTH_REQUIRED,
        "total_keys":      len(keys),
        "active_keys":     active_keys,
        "total_jobs_via_key": total_jobs,
        "total_hours_via_key": round(total_secs / 3600, 2),
    })

# ============================================================================
# ROUTES — MODELS / INSTALL
# ============================================================================
@app.route("/api/check_models")
def api_check_models():
    return jsonify({
        "sadtalker":  check_sadtalker(),
        "detail":     check_sadtalker_detailed(),
        "models_dir": MODELS_DIR,
    })

@app.route("/api/install_sadtalker", methods=["POST"])
def api_install_sadtalker():
    def do_install():
        st_path = os.path.join(MODELS_DIR, "SadTalker")
        if not os.path.exists(st_path):
            subprocess.run(["git", "clone", "--depth", "1",
                           "https://github.com/OpenTalker/SadTalker.git", st_path], check=True)
        req_file = os.path.join(st_path, "requirements.txt")
        if os.path.exists(req_file):
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file, "--quiet"], check=True)
        print("  [Install] SadTalker installation complete.")
    Thread(target=do_install, daemon=True).start()
    return jsonify({"status": "installing", "message": "SadTalker installation started..."})

@app.route("/api/validate_sadtalker", methods=["POST"])
def api_validate_sadtalker():
    """Quick end-to-end SadTalker test with a tiny 1s audio + sample image."""
    if not check_sadtalker():
        return jsonify({"ok": False, "error": "SadTalker not installed"})
    try:
        # Generate 1s test audio
        test_audio = os.path.join(OUTPUT_DIR, "st_test_audio.wav")
        test_img   = os.path.join(MODELS_DIR, "SadTalker", "examples", "source_image", "full_body_1.png")
        test_out   = os.path.join(OUTPUT_DIR, "st_test_out.mp4")

        edge_tts_generate("Hello, SadTalker test.", "en-US-GuyNeural", test_audio)
        run_sadtalker(test_img, test_audio, test_out, settings={"size": 256, "preprocess": "crop", "enhancer": None})

        dur = get_media_duration(test_out)
        _safe_rm(test_audio)
        _safe_rm(test_out)
        return jsonify({"ok": True, "output_duration": dur, "cuda": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ============================================================================
# ROUTES — STATIC FILES
# ============================================================================
@app.route("/outputs/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)

@app.route("/backgrounds/<path:filename>")
def serve_bg(filename):
    return send_from_directory(BG_DIR, filename)

@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# ============================================================================
# VIDEO TRANSLATION (HeyGen-style: translate existing video to another language)
# ============================================================================
def translate_video_pipeline(job_id: str, config: dict):
    """Translate a video: transcribe → translate → TTS → new lip sync."""
    try:
        jobs[job_id]["status"]   = "generating_audio"
        jobs[job_id]["progress"] = 5
        jobs[job_id]["message"]  = "Extracting audio from video..."

        input_video = config["input_video"]
        target_lang = config["target_lang"]
        target_voice = config.get("target_voice", "")
        job_dir      = OUTPUT_DIR

        _ff = _ffmpeg_path()
        # Step 1: Extract audio from original video
        raw_audio = os.path.join(job_dir, f"{job_id}_src_audio.wav")
        cmd_ext = [_ff, "-y", "-i", input_video, "-vn", "-acodec", "pcm_s16le",
                   "-ar", "16000", "-ac", "1", raw_audio]
        subprocess.run(cmd_ext, capture_output=True, timeout=120)

        # Step 2: Extract best frame as avatar image (try middle then start)
        jobs[job_id]["progress"] = 15
        jobs[job_id]["message"]  = "Extracting avatar frame..."
        avatar_frame = os.path.join(job_dir, f"{job_id}_src_frame.jpg")
        # Get video duration first (find ffprobe alongside ffmpeg)
        _ffprobe = shutil.which("ffprobe") or "ffprobe"
        dur_probe = subprocess.run(
            [_ffprobe, "-v","error","-show_entries","format=duration","-of","csv=p=0",input_video],
            capture_output=True, timeout=15
        )
        try:
            _vid_dur = float(dur_probe.stdout.decode().strip())
            _seek    = max(0, _vid_dur * 0.3)  # 30% in
        except Exception:
            _seek = 1.0
        cmd_frame = [_ff, "-y", "-ss", str(_seek), "-i", input_video,
                     "-frames:v", "1", "-update", "1",
                     "-vf", "scale=iw*2:ih*2", avatar_frame]
        subprocess.run(cmd_frame, capture_output=True, timeout=30)
        if not os.path.exists(avatar_frame) or os.path.getsize(avatar_frame) < 1000:
            # fallback to first frame
            cmd_frame0 = [_ff, "-y", "-i", input_video, "-frames:v", "1",
                          "-update", "1", avatar_frame]
            subprocess.run(cmd_frame0, capture_output=True, timeout=30)

        # Step 3: Transcribe with faster-whisper
        jobs[job_id]["progress"] = 25
        jobs[job_id]["message"]  = "Transcribing with Whisper AI..."
        try:
            from faster_whisper import WhisperModel
            wm = WhisperModel("base", device="cuda", compute_type="float16")
            segments, _ = wm.transcribe(raw_audio)
            original_text = " ".join(seg.text.strip() for seg in segments)
            del wm
        except Exception as e:
            original_text = ""
            print(f"  [TranslVideo] Whisper error: {e}")

        if not original_text:
            raise Exception("Could not transcribe audio from video")

        jobs[job_id]["message"] = f"Original: {original_text[:80]}..."

        # Step 4: Translate text
        jobs[job_id]["progress"] = 40
        jobs[job_id]["message"]  = f"Translating to {target_lang}..."
        try:
            translated_text = translate_srt(original_text, target_lang)
            # translate_srt works on SRT format; handle plain text fallback
            if not translated_text or translated_text == original_text:
                from deep_translator import GoogleTranslator
                translated_text = GoogleTranslator(source="auto", target=target_lang).translate(original_text)
        except Exception as e:
            translated_text = original_text
            print(f"  [TranslVideo] Translation error: {e}")

        jobs[job_id]["message"] = f"Translated: {translated_text[:80]}..."

        # Step 5: Generate TTS in target language
        jobs[job_id]["progress"] = 55
        jobs[job_id]["message"]  = "Generating new voice audio..."
        new_audio = os.path.join(job_dir, f"{job_id}_new_audio.mp3")
        if not target_voice:
            # Auto pick voice for target language
            lang_voices = {
                "pt": "pt-BR-AntonioNeural", "en": "en-US-GuyNeural",
                "es": "es-ES-AlvaroNeural",  "fr": "fr-FR-HenriNeural",
                "de": "de-DE-ConradNeural",  "it": "it-IT-DiegoNeural",
                "ja": "ja-JP-KeitaNeural",   "zh-CN": "zh-CN-YunxiNeural",
                "ko": "ko-KR-InJoonNeural",  "ar": "ar-SA-HamedNeural",
            }
            target_voice = lang_voices.get(target_lang, "en-US-GuyNeural")

        edge_tts_generate(translated_text, target_voice, new_audio)

        # Step 6: Lip sync with new audio — MuseTalk (primary) ou Wav2Lip (fallback)
        jobs[job_id]["status"]   = "generating_video"
        jobs[job_id]["progress"] = 65
        jobs[job_id]["message"]  = "Lip sync with new language..."
        output_video = os.path.join(job_dir, f"{job_id}_translated.mp4")
        if check_musetalk():
            try:
                run_musetalk_chunked(avatar_frame, new_audio, output_video, settings={}, chunk_duration=300, job_id=job_id)
            except Exception as _tl_mst_err:
                print(f"  [MuseTalk] Translate falhou ({_tl_mst_err}) — fallback Wav2Lip")
                run_wav2lip_chunked(avatar_frame, new_audio, output_video, settings={}, chunk_duration=300, job_id=job_id)
        else:
            run_wav2lip_chunked(avatar_frame, new_audio, output_video, settings={}, chunk_duration=300, job_id=job_id)

        if not os.path.exists(output_video) or os.path.getsize(output_video) < 1000:
            raise Exception("Lip sync failed for translated video")

        jobs[job_id]["status"]   = "compositing"
        jobs[job_id]["progress"] = 90
        jobs[job_id]["message"]  = "Finalizing translated video..."

        # Step 7: Generate thumbnail
        final_name  = f"{job_id}_final.mp4"
        final_path  = os.path.join(job_dir, final_name)
        shutil.copy2(output_video, final_path)
        thumb_path  = os.path.join(job_dir, f"{job_id}_thumb.jpg")
        cmd_thumb   = [_ff, "-y", "-i", final_path, "-frames:v", "1",
                       "-update", "1", "-vf", "scale=320:-1", thumb_path]
        subprocess.run(cmd_thumb, capture_output=True, timeout=30)

        final_dur = _get_duration_safe(final_path)
        jobs[job_id].update({
            "status": "done", "progress": 100, "message": "Translation complete!",
            "output_filename": final_name, "output_path": final_path,
            "thumbnail": f"/outputs/{job_id}_thumb.jpg",
            "original_text": original_text[:200],
            "translated_text": translated_text[:200],
            "duration": round(final_dur, 1),
        })
        history_record = {
            "id": job_id, "filename": final_name,
            "created": jobs[job_id].get("created", datetime.now().isoformat()),
            "duration": round(final_dur, 1),
            "size_mb": round(os.path.getsize(final_path) / 1024 / 1024, 2),
            "script_preview": f"[Translation → {target_lang}] {translated_text[:80]}",
            "voice": target_voice, "thumbnail": f"/outputs/{job_id}_thumb.jpg",
            "status": "done", "plan": "unlimited",
        }
        add_to_history(history_record)
        db_save_job(job_id, jobs[job_id])
        notify_webhooks(job_id, jobs[job_id])

    except Exception as e:
        jobs[job_id].update({"status": "error", "error": str(e), "message": "Translation failed"})
        print(f"  [TranslVideo] PIPELINE ERROR: {e}")
    finally:
        # Release GPU memory and clean intermediates (same as run_pipeline)
        release_vram()
        for _tmp_suffix in ("_src_audio.wav", "_src_frame.jpg", "_new_audio.mp3", "_translated.mp4"):
            _safe_rm(os.path.join(OUTPUT_DIR, f"{job_id}{_tmp_suffix}"))


@app.route("/api/video/translate", methods=["POST"])
def api_video_translate():
    """Translate an existing video to another language with new lip sync."""
    if "video" not in request.files:
        return jsonify({"error": "No video file uploaded"}), 400
    vid_file    = request.files["video"]
    target_lang = request.form.get("target_lang", "pt")
    target_voice = request.form.get("target_voice", "")

    job_id  = uuid.uuid4().hex[:12]
    vid_ext = os.path.splitext(vid_file.filename)[1].lower() or ".mp4"
    vid_path = os.path.join(UPLOAD_DIR, f"transl_src_{job_id}{vid_ext}")
    vid_file.save(vid_path)

    jobs[job_id] = {
        "id": job_id, "status": "queued", "progress": 0,
        "created": datetime.now().isoformat(),
        "script_preview": f"[Video Translation → {target_lang}]",
        "message": "Queued", "error": "",
    }
    config = {"input_video": vid_path, "target_lang": target_lang, "target_voice": target_voice}
    Thread(target=translate_video_pipeline, args=(job_id, config), daemon=True).start()
    return jsonify({"job_id": job_id})


# ============================================================================
# URL TO VIDEO (HeyGen-style: paste URL → AI script → video)
# ============================================================================
@app.route("/api/ai/url_to_script", methods=["POST"])
def api_url_to_script():
    """Fetch a URL, extract content, and generate a video script with AI."""
    import urllib.request as _ureq
    data = request.json or {}
    url  = data.get("url", "").strip()
    lang = data.get("language", "English")
    style = data.get("style", "professional")
    length = data.get("length", "short")

    if not url or not url.startswith("http"):
        return jsonify({"error": "Valid URL required (http/https)"}), 400

    try:
        req = _ureq.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AvatarPilot/2.0)",
            "Accept": "text/html,application/xhtml+xml,*/*",
        })
        with _ureq.urlopen(req, timeout=10) as resp:
            raw_html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return jsonify({"error": f"Could not fetch URL: {e}"}), 400

    # Extract readable text (strip tags)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>",  " ", text,     flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text[:4000]  # Limit context

    if len(text) < 50:
        return jsonify({"error": "Could not extract meaningful text from URL"}), 400

    length_map = {"short": "30-45 seconds", "medium": "60-90 seconds", "long": "2-3 minutes"}
    duration_hint = length_map.get(length, "30-45 seconds")

    prompt = f"""You are a professional video script writer.
Based on the following web content, write a compelling {style} video script in {lang} that is approximately {duration_hint} long.
The script should be suitable for a talking avatar video — natural speech, no stage directions, no hashtags.
Only output the script itself, nothing else.

Web content:
{text}

Script:"""

    script = ask_groq(prompt, max_tokens=1024)
    if script.startswith("[AI Error"):
        return jsonify({"error": script}), 500

    return jsonify({"script": script.strip(), "source_url": url, "extracted_chars": len(text)})


# ============================================================================
# VIDEO EDITOR / MERGER (stitch clips from history)
# ============================================================================
@app.route("/api/editor/merge", methods=["POST"])
def api_editor_merge():
    """Merge multiple output videos into one using FFmpeg concat."""
    data     = request.json or {}
    filenames = data.get("filenames", [])
    add_transitions = data.get("transitions", False)

    if len(filenames) < 2:
        return jsonify({"error": "At least 2 videos required"}), 400
    if len(filenames) > 20:
        return jsonify({"error": "Max 20 videos per merge"}), 400

    # Build concat list
    concat_list_path = os.path.join(OUTPUT_DIR, f"concat_{uuid.uuid4().hex[:8]}.txt")
    valid_files = []
    for fn in filenames:
        safe_fn = os.path.basename(fn)  # prevent path traversal
        fp = os.path.join(OUTPUT_DIR, safe_fn)
        if os.path.exists(fp):
            valid_files.append(fp)

    if len(valid_files) < 2:
        return jsonify({"error": "Could not find the specified video files"}), 400

    with open(concat_list_path, "w", encoding="utf-8") as f:
        for fp in valid_files:
            f.write(f"file '{fp}'\n")

    out_id   = uuid.uuid4().hex[:12]
    out_name = f"{out_id}_merged.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    _ff = _ffmpeg_path()
    # Copy files to ASCII tmpdir to avoid non-ASCII path issues (Windows ç)
    import tempfile as _tf
    _tmpdir = _tf.mkdtemp(prefix="avatarpilot_merge_")
    tmp_files = []
    for i, fp in enumerate(valid_files):
        ext  = os.path.splitext(fp)[1] or ".mp4"
        tmp  = os.path.join(_tmpdir, f"clip_{i:03d}{ext}")
        shutil.copy2(fp, tmp)
        tmp_files.append(tmp)
    # Rewrite concat list with ASCII paths
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for tp in tmp_files:
            f.write(f"file '{tp}'\n")
    cmd = [_ff, "-y", "-f", "concat", "-safe", "0",
           "-i", concat_list_path,
           "-c", "copy", out_path]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    _safe_rm(concat_list_path)
    for tp in tmp_files:
        _safe_rm(tp)
    try:
        os.rmdir(_tmpdir)
    except Exception:
        pass

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        return jsonify({"error": "Merge failed: " + result.stderr.decode("utf-8", errors="replace")[-200:]}), 500

    # Thumbnail
    thumb_name = f"{out_id}_thumb.jpg"
    thumb_path = os.path.join(OUTPUT_DIR, thumb_name)
    subprocess.run([_ff, "-y", "-i", out_path, "-frames:v", "1",
                    "-update", "1", "-vf", "scale=320:-1", thumb_path],
                   capture_output=True, timeout=30)

    size_mb = round(os.path.getsize(out_path) / 1024 / 1024, 2)
    return jsonify({
        "ok": True, "filename": out_name,
        "url": f"/outputs/{out_name}",
        "thumbnail": f"/outputs/{thumb_name}",
        "clips_merged": len(valid_files),
        "size_mb": size_mb,
    })


@app.route("/api/editor/trim", methods=["POST"])
def api_editor_trim():
    """Trim a video to start/end times."""
    data     = request.json or {}
    filename = os.path.basename(data.get("filename", ""))
    start    = float(data.get("start", 0))
    end_time = data.get("end", None)

    in_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(in_path):
        return jsonify({"error": "File not found"}), 404

    out_id   = uuid.uuid4().hex[:12]
    out_name = f"{out_id}_trimmed.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    _ff = _ffmpeg_path()
    cmd = [_ff, "-y", "-i", in_path, "-ss", str(start)]
    if end_time is not None:
        cmd += ["-to", str(float(end_time))]
    cmd += ["-c", "copy", out_path]

    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 500:
        return jsonify({"error": "Trim failed"}), 500

    return jsonify({"ok": True, "filename": out_name, "url": f"/outputs/{out_name}"})


# ============================================================================
# KARAOKE CAPTIONS (word-level highlight — HeyGen Pro style)
# ============================================================================
def generate_karaoke_ass(audio_path: str, output_ass: str, model_size: str = "base",
                          font_size: int = 28, primary_color: str = "white",
                          highlight_color: str = "yellow") -> bool:
    """Generate ASS subtitle file with word-level timing for karaoke effect."""
    try:
        from faster_whisper import WhisperModel
        wm = WhisperModel(model_size, device="cuda", compute_type="float16")
        segments, _ = wm.transcribe(audio_path, word_timestamps=True)

        # Color mapping
        color_map = {
            "white": "&H00FFFFFF", "yellow": "&H0000FFFF", "cyan": "&H00FFFF00",
            "green": "&H0000FF00", "orange": "&H000080FF", "red": "&H000000FF",
        }
        primary_ass = color_map.get(primary_color, "&H00FFFFFF")
        highlight_ass = color_map.get(highlight_color, "&H0000FFFF")

        ass_header = f"""[Script Info]
ScriptType: v4.00+
Collisions: Normal
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},{primary_ass},&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,1,2,10,10,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        def fmt_time(t):
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = int(t % 60)
            cs = int((t % 1) * 100)
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

        lines = [ass_header]
        for seg in segments:
            words = list(seg.words) if hasattr(seg, 'words') and seg.words else []
            if not words:
                start = fmt_time(seg.start)
                end   = fmt_time(seg.end)
                lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{seg.text.strip()}\n")
                continue

            # Build karaoke line: each word highlighted in sequence
            seg_start = fmt_time(words[0].start)
            seg_end   = fmt_time(words[-1].end)

            # Build text with karaoke tags
            parts = []
            for w in words:
                dur_cs = max(1, int((w.end - w.start) * 100))
                parts.append(f"{{\\k{dur_cs}}}{w.word.strip()}")
            karaoke_text = " ".join(parts)

            # Override style with highlight color
            line = (f"Dialogue: 0,{seg_start},{seg_end},Default,,0,0,0,,"
                    f"{{\\1c{highlight_ass}}}{{\\kf0}}{karaoke_text}\n")
            lines.append(line)

        del wm
        with open(output_ass, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return True
    except Exception as e:
        print(f"  [Karaoke] ASS generation error: {e}")
        return False


@app.route("/api/tools/karaoke_captions", methods=["POST"])
def api_karaoke_captions():
    """Generate karaoke ASS subtitles for a given audio/video file."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f         = request.files["file"]
    font_size = int(request.form.get("font_size", "28"))
    primary   = request.form.get("primary_color", "white")
    highlight = request.form.get("highlight_color", "yellow")
    model_sz  = request.form.get("model", "base")

    file_id  = uuid.uuid4().hex[:8]
    in_path  = os.path.join(UPLOAD_DIR, f"karaoke_in_{file_id}{os.path.splitext(f.filename)[1] or '.mp4'}")
    ass_path = os.path.join(OUTPUT_DIR, f"karaoke_{file_id}.ass")
    f.save(in_path)

    ok = generate_karaoke_ass(in_path, ass_path, model_sz, font_size, primary, highlight)
    _safe_rm(in_path)

    if not ok or not os.path.exists(ass_path):
        return jsonify({"error": "Karaoke generation failed"}), 500

    with open(ass_path, "r", encoding="utf-8") as af:
        ass_content = af.read()

    return jsonify({"ok": True, "ass_content": ass_content,
                    "ass_url": f"/outputs/karaoke_{file_id}.ass"})


# ── Enhance existing video with GFPGAN ──────────────────────────────────────
@app.route("/api/tools/enhance_video", methods=["POST"])
def api_enhance_video():
    """Apply GFPGAN face restoration to an existing output video."""
    data     = request.json or {}
    filename = os.path.basename(data.get("filename", ""))
    if not filename:
        return jsonify({"error": "filename required"}), 400

    in_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(in_path):
        return jsonify({"error": "File not found"}), 404

    out_id   = uuid.uuid4().hex[:12]
    out_name = f"{out_id}_enhanced.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    def _run():
        try:
            apply_gfpgan_to_video(in_path, out_path)
        except Exception as e:
            print(f"[enhance_video] Error: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=600)

    if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
        return jsonify({
            "ok": True,
            "filename": out_name,
            "url": f"/outputs/{out_name}",
            "size_mb": round(os.path.getsize(out_path) / 1024 / 1024, 2),
        })
    return jsonify({"error": "Enhancement failed"}), 500


# ── Fast HD upscale for existing videos — async with polling ─────────────────
_hd_jobs: dict = {}  # job_id → {"status": "running"|"done"|"error", "filename": ..., "url": ...}

@app.route("/api/tools/hd_upscale", methods=["POST"])
def api_hd_upscale():
    """Upscale an existing output video to 1280×720 HD. Returns immediately with job_id."""
    data     = request.json or {}
    filename = os.path.basename(data.get("filename", ""))
    if not filename:
        return jsonify({"error": "filename required"}), 400
    in_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(in_path):
        return jsonify({"error": "File not found"}), 404

    job_id   = uuid.uuid4().hex[:12]
    out_name = f"{job_id}_hd1280.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)
    _hd_jobs[job_id] = {"status": "running", "filename": out_name}

    def _run():
        import tempfile as _hdu_tmp
        ff      = _ffmpeg_path()
        ffprobe = _ffprobe_path()
        try:
            tmp      = _hdu_tmp.mkdtemp(prefix="avp_hdu_")
            safe_in  = os.path.join(tmp, "in.mp4")
            safe_out = os.path.join(tmp, "out.mp4")
            shutil.copy2(in_path, safe_in)

            _dr = subprocess.run([ffprobe, "-v","error","-show_entries","format=duration",
                                  "-of","default=noprint_wrappers=1:nokey=1", safe_in],
                                 capture_output=True, timeout=15)
            _video_dur = float(_dr.stdout.decode().strip() or "60")
            print(f"  [HD_upscale] {filename} — {_video_dur:.0f}s")

            vf = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,unsharp=5:5:0.5:5:5:0"
            _preset = "fast" if _video_dur > 60 else "medium"
            _timeout = max(1800, int(_video_dur * 15))
            subprocess.run(
                [ff, "-y", "-i", safe_in, "-vf", vf,
                 "-c:v", "libx264", "-crf", "16", "-preset", _preset,
                 "-b:v", "2500k", "-maxrate", "4000k", "-bufsize", "8000k",
                 "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", safe_out],
                capture_output=True, timeout=_timeout
            )
            if os.path.exists(safe_out) and os.path.getsize(safe_out) > 10000:
                shutil.copy2(safe_out, out_path)
                _hd_jobs[job_id].update({
                    "status": "done",
                    "url": f"/outputs/{out_name}",
                    "size_mb": round(os.path.getsize(out_path) / 1024 / 1024, 2),
                })
                print(f"  [HD_upscale] Done → {out_name}")
            else:
                _hd_jobs[job_id]["status"] = "error"
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception as e:
            print(f"  [HD_upscale] Error: {e}")
            _hd_jobs[job_id].update({"status": "error", "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id, "status": "running",
                    "message": "Upscale iniciado em background"})


@app.route("/api/tools/hd_upscale/<job_id>", methods=["GET"])
def api_hd_upscale_status(job_id):
    """Poll HD upscale status."""
    job = _hd_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


# ── Burn karaoke captions into video ────────────────────────────────────────
@app.route("/api/tools/burn_karaoke", methods=["POST"])
def api_burn_karaoke():
    """Burn a karaoke ASS subtitle into a video file."""
    data       = request.json or {}
    vid_name   = os.path.basename(data.get("video_filename", ""))
    ass_url    = data.get("ass_url", "")
    ass_name   = os.path.basename(ass_url.replace("/outputs/", ""))

    vid_path = os.path.join(OUTPUT_DIR, vid_name)
    ass_path = os.path.join(OUTPUT_DIR, ass_name)

    if not os.path.exists(vid_path):
        return jsonify({"error": "Video not found"}), 404
    if not os.path.exists(ass_path):
        return jsonify({"error": "ASS file not found"}), 404

    out_id   = uuid.uuid4().hex[:12]
    out_name = f"{out_id}_karaoke.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    # ASS path must be ASCII — copy to temp if needed
    import tempfile
    tmp_ass = os.path.join(tempfile.gettempdir(), f"karaoke_{out_id}.ass")
    shutil.copy2(ass_path, tmp_ass)

    # Escape backslashes for FFmpeg subtitles filter
    ass_ffmpeg = tmp_ass.replace("\\", "/").replace(":", "\\:")
    _ff = _ffmpeg_path()
    cmd = [_ff, "-y", "-i", vid_path,
           "-vf", f"ass='{ass_ffmpeg}'",
           "-c:a", "copy", out_path]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    _safe_rm(tmp_ass)

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        err = result.stderr.decode("utf-8", errors="replace")[-300:]
        return jsonify({"error": "Burn failed: " + err}), 500

    return jsonify({"ok": True, "filename": out_name, "url": f"/outputs/{out_name}"})


# ============================================================================
# FACE SWAP (using DeepFace/InsightFace if available, else Pillow blend)
# ============================================================================
@app.route("/api/tools/face_swap", methods=["POST"])
def api_face_swap():
    """Swap face from source image into target image."""
    if "source" not in request.files or "target" not in request.files:
        return jsonify({"error": "source and target images required"}), 400

    src_file = request.files["source"]
    tgt_file = request.files["target"]
    fid      = uuid.uuid4().hex[:8]

    src_path = os.path.join(UPLOAD_DIR, f"fswap_src_{fid}.jpg")
    tgt_path = os.path.join(UPLOAD_DIR, f"fswap_tgt_{fid}.jpg")
    out_path = os.path.join(UPLOAD_DIR, f"fswap_out_{fid}.jpg")
    src_file.save(src_path)
    tgt_file.save(tgt_path)

    try:
        # Try roop/insightface first
        try:
            import insightface
            from insightface.app import FaceAnalysis
            import cv2 as _cv2
            import numpy as _np

            app_face = FaceAnalysis(name="buffalo_l", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
            app_face.prepare(ctx_id=0, det_size=(640, 640))

            src_img = _cv2.imdecode(_np.frombuffer(open(src_path, "rb").read(), dtype=_np.uint8), _cv2.IMREAD_COLOR)
            tgt_img = _cv2.imdecode(_np.frombuffer(open(tgt_path, "rb").read(), dtype=_np.uint8), _cv2.IMREAD_COLOR)

            src_faces = app_face.get(src_img)
            tgt_faces = app_face.get(tgt_img)

            if not src_faces or not tgt_faces:
                raise ValueError("No faces detected")

            # Try direct path first — insightface auto-download is finicky on Windows
            _isw_candidates = [
                os.path.join(MODELS_DIR, "inswapper_128.onnx"),
                os.path.join(MODELS_DIR, "SadTalker", "inswapper_128.onnx"),
            ]
            _isw_path = next((p for p in _isw_candidates if os.path.exists(p)), None)
            if _isw_path:
                swapper = insightface.model_zoo.get_model(_isw_path)
            else:
                swapper = insightface.model_zoo.get_model("inswapper_128.onnx", root=MODELS_DIR)
            result  = swapper.get(tgt_img, tgt_faces[0], src_faces[0], paste_back=True)
            _, buf  = _cv2.imencode(".jpg", result, [_cv2.IMWRITE_JPEG_QUALITY, 92])
            with open(out_path, "wb") as f_out:
                f_out.write(buf.tobytes())
            method = "insightface"

        except ImportError:
            # Fallback: face blend using OpenCV Haar cascade + Seamless Clone
            import cv2 as _cv2
            import numpy as _np
            import tempfile, shutil as _shutil

            src_img_cv = _cv2.imdecode(_np.frombuffer(open(src_path, "rb").read(), dtype=_np.uint8), _cv2.IMREAD_COLOR)
            tgt_img_cv = _cv2.imdecode(_np.frombuffer(open(tgt_path, "rb").read(), dtype=_np.uint8), _cv2.IMREAD_COLOR)

            # Find Haar cascade XML — copy to ASCII tempdir if needed
            import cv2
            cv2_data = os.path.join(os.path.dirname(cv2.__file__), "data")
            casc_src  = os.path.join(cv2_data, "haarcascade_frontalface_default.xml")
            if not os.path.exists(casc_src):
                raise ValueError("OpenCV Haar cascade not found")
            tmp_casc = os.path.join(tempfile.gettempdir(), "haarcascade_frontalface_default.xml")
            if not os.path.exists(tmp_casc):
                _shutil.copy2(casc_src, tmp_casc)
            cascade = _cv2.CascadeClassifier(tmp_casc)

            src_gray  = _cv2.cvtColor(src_img_cv, _cv2.COLOR_BGR2GRAY)
            tgt_gray  = _cv2.cvtColor(tgt_img_cv, _cv2.COLOR_BGR2GRAY)
            src_faces = cascade.detectMultiScale(src_gray, 1.1, 4, minSize=(60, 60))
            tgt_faces = cascade.detectMultiScale(tgt_gray, 1.1, 4, minSize=(60, 60))

            if len(src_faces) == 0 or len(tgt_faces) == 0:
                raise ValueError("No faces detected in one or both images")

            sx, sy, sw, sh = src_faces[0]
            tx, ty, tw, th = tgt_faces[0]

            # Crop source face, resize to target face region, seamless clone
            src_face_crop    = src_img_cv[sy:sy+sh, sx:sx+sw]
            src_face_resized = _cv2.resize(src_face_crop, (tw, th))
            center = (tx + tw // 2, ty + th // 2)
            mask   = 255 * _np.ones(src_face_resized.shape, src_face_resized.dtype)
            result = _cv2.seamlessClone(src_face_resized, tgt_img_cv, mask, center, _cv2.NORMAL_CLONE)

            _, buf = _cv2.imencode(".jpg", result, [_cv2.IMWRITE_JPEG_QUALITY, 92])
            with open(out_path, "wb") as f_out:
                f_out.write(buf.tobytes())
            method = "opencv_seamless"

    except Exception as e:
        _safe_rm(src_path); _safe_rm(tgt_path)
        return jsonify({"error": f"Face swap failed: {e}"}), 500

    _safe_rm(src_path); _safe_rm(tgt_path)
    fname = os.path.basename(out_path)
    return jsonify({"ok": True, "url": f"/uploads/{fname}", "path": out_path, "method": method})


# ============================================================================
# ANTI-ERROR SYSTEM
# ============================================================================

# ── Lightweight health check (for uptime monitors / load balancers) ──────────
@app.route("/api/healthz")
def api_healthz():
    """Ultra-fast health check — responds in <5ms. Use for uptime monitoring."""
    return jsonify({
        "status": "ok",
        "ts":     datetime.now().isoformat(),
        "jobs":   len(jobs),
        "queue":  len(batch_queue),
    })


# ── Request-ID middleware (traces each request in logs) ──────────────────────
import uuid as _uuid_mod
@app.before_request
def _attach_request_id():
    request.environ["X-Request-ID"] = _uuid_mod.uuid4().hex[:8]


# ── Watchdog thread: detects stuck jobs and auto-recovers them ───────────────
# 4h: long enough for 1h video on RTX 4060 (SadTalker base ~3min + MuseTalk chunked ~2h
# + GFPGAN chunked ~30min + HD encode ~20min ≈ 3h total). Tune up for slower hardware.
# Configurável via env p/ vídeos longos/hardware lento. O stall-timeout de 60min
# (sem progresso) já pega jobs realmente travados, então subir este teto absoluto
# é seguro — só permite que vídeos longos legítimos terminem. Ex: AVP_STUCK_TIMEOUT_MIN=720
# (12h) p/ rodar vídeos de horas com enhance_face=false.
_STUCK_JOB_TIMEOUT_MIN = int(os.environ.get("AVP_STUCK_TIMEOUT_MIN", "240"))

def _auto_cleanup_old_outputs():
    """
    Background disk-space guardian. Hundreds of users will fill the disk
    in days. Auto-prune output files older than configured retention.
    - Triggers if disk free < 5GB OR every 6h regardless.
    - Keeps recent jobs (last 7 days by default).
    - Removes orphan intermediates (avatar/audio/captioned/thumb) older than 24h.
    """
    try:
        free_mb = _free_disk_mb()
        retention_days  = 7    # final outputs kept this long
        intermediates_h = 24   # intermediates always pruned after 1 day
        now = time.time()
        deleted = 0
        freed = 0
        # Patterns by retention class
        long_patterns  = ("_final.mp4",)
        short_patterns = ("_avatar.mp4", "_audio.mp3", "_audio.wav",
                          "_audio_norm.mp3", "_captioned.mp4", "_sync.mp4",
                          "_sway.mp4", "_gfpgan.mp4", "_musetalk.mp4",
                          "_fmt.mp4", "_wm.mp4", "_music.mp4", "_fade.mp4",
                          "_fswap.mp4", "preview_")
        # Force more aggressive cleanup if disk pressure
        if free_mb < 5000:
            retention_days = 2
            intermediates_h = 6
            print(f"  [AutoCleanup] DISK PRESSURE ({free_mb:.0f}MB free) — aggressive prune", flush=True)
        long_cutoff  = now - retention_days * 86400
        short_cutoff = now - intermediates_h * 3600
        try:
            for fname in os.listdir(OUTPUT_DIR):
                fpath = os.path.join(OUTPUT_DIR, fname)
                if not os.path.isfile(fpath): continue
                try:
                    mtime = os.path.getmtime(fpath)
                except Exception: continue
                # Intermediate files
                if any(p in fname for p in short_patterns):
                    if mtime < short_cutoff:
                        try:
                            sz = os.path.getsize(fpath)
                            os.remove(fpath)
                            freed += sz; deleted += 1
                        except Exception: pass
                # Final outputs
                elif any(fname.endswith(p) for p in long_patterns) or fname.endswith("_thumb.jpg") or fname.endswith(".srt"):
                    if mtime < long_cutoff:
                        try:
                            sz = os.path.getsize(fpath)
                            os.remove(fpath)
                            freed += sz; deleted += 1
                        except Exception: pass
        except Exception as _le:
            print(f"  [AutoCleanup] OUTPUT_DIR scan failed: {_le}", flush=True)
        # Also prune temp dirs from crashed jobs
        _cleanup_orphan_tmps()
        if deleted > 0:
            print(f"  [AutoCleanup] Removed {deleted} files, freed {freed/1024/1024:.1f}MB", flush=True)
    except Exception as _ce:
        print(f"  [AutoCleanup] failed: {_ce}", flush=True)



def _release_dead_worker_slot(jid):
    """Release semaphore and worker count for a dead pipeline thread."""
    global active_workers
    try:
        with workers_lock:
            active_workers = max(0, active_workers - 1)
        try: _pipeline_semaphore.release()
        except Exception: pass
        print(f"  [Watchdog] Released worker slot for dead job {jid[:8]}", flush=True)
    except Exception as e:
        print(f"  [Watchdog] Error releasing slot: {e}", flush=True)

def _watchdog_loop():
    """Runs every 30s. Detects dead worker threads AND timeout. Auto-cleanup every 6h."""
    import time as _wt
    _last_cleanup = _wt.time()
    # Track last-seen progress per job to detect stalls
    _last_progress = {}  # jid -> (progress, timestamp)
    _STALL_TIMEOUT_SEC = 3600  # 60 min with zero progress = dead (GPU ops can take 30min+)
    while True:
        try:
            _wt.sleep(30)  # Check every 30s (was 60s)
            _now = datetime.now()
            dead_jobs = []
            with jobs_lock:
                for jid, jdata in list(jobs.items()):
                    status = jdata.get("status", "")
                    if status in ("tts", "generating_video", "compositing",
                                  "generating_audio", "processing"):
                        # --- CHECK 1: Thread alive? ---
                        _thread = jdata.get("_thread")
                        if _thread is not None and not _thread.is_alive():
                            dead_jobs.append((jid, "dead_thread"))
                            continue
                        # --- CHECK 2: Progress stalled? ---
                        prog = jdata.get("progress", 0)
                        now_ts = _wt.time()
                        prev = _last_progress.get(jid)
                        if prev is None or prev[0] != prog:
                            _last_progress[jid] = (prog, now_ts)
                        else:
                            # Same progress for too long
                            stall_sec = now_ts - prev[1]
                            if stall_sec > _STALL_TIMEOUT_SEC:
                                dead_jobs.append((jid, f"stalled_{int(stall_sec)}s"))
                                continue
                        # --- CHECK 3: Absolute timeout ---
                        created_str = jdata.get("created", "")
                        if created_str:
                            try:
                                age_min = (_now - datetime.fromisoformat(created_str)).total_seconds() / 60
                                if age_min > _STUCK_JOB_TIMEOUT_MIN:
                                    dead_jobs.append((jid, f"timeout_{int(age_min)}min"))
                            except Exception:
                                pass
            # Mark dead jobs as error
            for jid, reason in dead_jobs:
                with jobs_lock:
                    if jobs.get(jid, {}).get("status") not in ("done", "error", "cancelled"):
                        if reason == "dead_thread":
                            err_msg = "Worker thread morreu inesperadamente (possivel crash de GPU/VRAM). Tente novamente com um script mais curto."
                        elif reason.startswith("stalled"):
                            err_msg = f"Job travado sem progresso por mais de {_STALL_TIMEOUT_SEC//60} minutos. Processo pode ter crashado."
                        else:
                            err_msg = f"Timeout: job travado por >{_STUCK_JOB_TIMEOUT_MIN}min"
                        jobs[jid]["status"]   = "error"
                        jobs[jid]["error"]    = err_msg
                        jobs[jid]["message"]  = err_msg
                        jobs[jid]["progress"] = 0
                        db_save_job(jid, jobs[jid])
                        print(f"  [Watchdog] Job {jid[:8]} -> ERROR ({reason}): {err_msg}", flush=True)
                # Release worker slot if thread is dead
                if reason == "dead_thread":
                    _release_dead_worker_slot(jid)
                # Clean from progress tracker
                _last_progress.pop(jid, None)
            # Clean finished jobs from progress tracker
            done_jids = [j for j in _last_progress if j not in jobs or jobs.get(j, {}).get("status") in ("done", "error", "cancelled")]
            for j in done_jids:
                _last_progress.pop(j, None)
            # Memory leak detection
            with jobs_lock:
                n_jobs = len(jobs)
            if n_jobs > 500:
                print(f"  [Watchdog] ALERTA: {n_jobs} jobs em memoria - possivel leak", flush=True)
                with jobs_lock:
                    cutoff_ts = (_now - timedelta(hours=2)).isoformat()
                    to_remove = [jid for jid, jd in jobs.items()
                                 if jd.get("status") in ("done", "error", "cancelled")
                                 and jd.get("created", "") < cutoff_ts]
                    for jid in to_remove:
                        del jobs[jid]
                    if to_remove:
                        print(f"  [Watchdog] Pruned {len(to_remove)} old jobs from memory", flush=True)
            # Auto-cleanup of disk every 6h OR when disk pressure
            free_mb = _free_disk_mb()
            if _wt.time() - _last_cleanup > 6 * 3600 or free_mb < 5000:
                _auto_cleanup_old_outputs()
                _last_cleanup = _wt.time()
        except Exception as _we:
            print(f"  [Watchdog] Erro interno: {_we}", flush=True)

def _start_watchdog():
    t = threading.Thread(target=_watchdog_loop, name="WatchdogThread", daemon=True)
    t.start()
    print("  Watchdog: OK (stuck-job detector ativo)", flush=True)


# ── Startup diagnostics: validate critical dependencies at boot ──────────────
def _run_startup_diagnostics():
    """Called once at startup. Logs warnings for missing/broken dependencies."""
    _ok, _warn = [], []

    # FFmpeg
    try:
        r = subprocess.run([_ffmpeg_path(), "-version"], capture_output=True, timeout=5)
        if r.returncode == 0:
            _ok.append("FFmpeg")
        else:
            _warn.append("FFmpeg retornou erro")
    except Exception as e:
        _warn.append(f"FFmpeg não encontrado: {e}")

    # SQLite write test
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT COUNT(*) FROM jobs_db")
        conn.close()
        _ok.append("SQLite")
    except Exception as e:
        _warn.append(f"SQLite: {e}")

    # Disk space
    free_mb = _free_disk_mb()
    if free_mb < DISK_WARN_MB:
        _warn.append(f"Disco baixo: {free_mb:.0f}MB livres")
    else:
        _ok.append(f"Disco ({free_mb:.0f}MB livres)")

    # Output dir writable
    try:
        _test = os.path.join(OUTPUT_DIR, "_write_test")
        open(_test, "w").close()
        os.remove(_test)
        _ok.append("Output dir")
    except Exception as e:
        _warn.append(f"Output dir não gravável: {e}")

    # VRAM
    try:
        import torch
        if torch.cuda.is_available():
            free_gb = torch.cuda.mem_get_info(0)[0] / 1024**3
            if free_gb < 1.0:
                _warn.append(f"VRAM crítica: {free_gb:.1f}GB livres")
            else:
                _ok.append(f"VRAM ({free_gb:.1f}GB livres)")
    except Exception:
        pass

    if _warn:
        print(f"  [Diagnostics] ⚠️  Avisos: {', '.join(_warn)}", flush=True)
    print(f"  [Diagnostics] ✅ OK: {', '.join(_ok)}", flush=True)


# ── Input sanitization helpers ───────────────────────────────────────────────
def _safe_filename(name: str, max_len: int = 100) -> str:
    """Strip path components and dangerous characters from user-supplied filenames."""
    name = os.path.basename(name)
    name = re.sub(r'[^\w.\-]', '_', name)
    return name[:max_len]

def _safe_url(url: str) -> bool:
    """Return True if URL is safe to fetch (blocks SSRF targets)."""
    if not url:
        return False
    blocked = ("169.254.", "127.", "10.", "192.168.", "172.16.", "172.17.",
               "172.18.", "172.19.", "172.2", "172.3", "0.0.0.0", "localhost",
               "metadata.google", "169.254.169.254")
    import urllib.parse as _up
    try:
        parsed = _up.urlparse(url)
        host = parsed.hostname or ""
        return (parsed.scheme in ("http", "https") and
                not any(host.startswith(b) or host == b.rstrip(".") for b in blocked))
    except Exception:
        return False


# ============================================================================
# GLOBAL ERROR HANDLERS
# ============================================================================
@app.errorhandler(413)
def error_413(e):
    return jsonify({"error": "Arquivo muito grande. Limite: 500MB."}), 413

@app.errorhandler(404)
def error_404(e):
    # Rotas de API retornam JSON; outras rotas retornam HTML normal
    if request.path.startswith("/api/"):
        return jsonify({"error": "Endpoint não encontrado"}), 404
    return "404 Not Found", 404

@app.errorhandler(500)
def error_500(e):
    rid = request.environ.get("X-Request-ID", "?")
    print(f"[Flask 500] req={rid} {e}", flush=True)
    return jsonify({"error": "Erro interno do servidor. Verifique os logs."}), 500


@app.route("/api/debug/jobs")
def api_debug_jobs():
    """Diagnostic: show in-memory jobs dict and semaphore state."""
    with jobs_lock:
        all_jobs = {jid: {"status": j.get("status"), "message": j.get("message","")[:60], "progress": j.get("progress",0)} for jid, j in jobs.items()}
    with workers_lock:
        aw = active_workers
    # Try to check semaphore availability
    sem_free = _pipeline_semaphore.acquire(blocking=False)
    if sem_free:
        _pipeline_semaphore.release()  # give it back
    return jsonify({
        "jobs_in_memory": all_jobs,
        "jobs_count": len(all_jobs),
        "active_workers": aw,
        "max_workers": MAX_WORKERS,
        "semaphore_available": sem_free,
    })

@app.errorhandler(Exception)
def error_unhandled(e):
    import traceback
    from werkzeug.exceptions import HTTPException
    # Preserve real HTTP errors (405 Method Not Allowed, 400 Bad Request from
    # malformed JSON, 404, etc.) — do NOT mask them as 500. A catch-all that
    # turns every HTTPException into 500 hides client errors and breaks REST
    # semantics for the 1000+ users hitting the API.
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({
                "error": e.description or e.name,
                "code":  e.code,
            }), (e.code or 500)
        return e  # let Flask render its default page for non-API routes
    rid = request.environ.get("X-Request-ID", "?")
    print(f"[Flask Unhandled] req={rid}\n{traceback.format_exc()}", flush=True)
    if request.path.startswith("/api/"):
        return jsonify({"error": f"Erro inesperado: {str(e)}"}), 500
    return "Erro interno", 500

# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    # Init SQLite and clean up any interrupted jobs
    init_db()
    db_load_incomplete_jobs()
    _cleanup_orphan_tmps()

    s = load_settings()
    executor = s.get("executor", "local")

    print("=" * 60)
    print("  AvatarPilot Pro v3 — AI Talking Avatar Generator")
    print(f"  URL:    http://localhost:5052")
    print(f"  Plan:   {s.get('plan', DEFAULT_PLAN)}")
    exec_label = {"replicate": "(Replicate A100 — paid)", "huggingface": "(HuggingFace ZeroGPU — free)", "local": "(local GPU)"}.get(executor, "(local GPU)")
    print(f"  Executor: {executor.upper()} {exec_label}")
    print(f"  GPU:    ", end="")
    try:
        import torch
        if torch.cuda.is_available():
            free_gb = round(torch.cuda.mem_get_info(0)[0] / 1024**3, 1)
            print(f"{torch.cuda.get_device_name(0)} ({free_gb}GB free VRAM)")
        else:
            print("CPU only")
    except Exception:
        print("PyTorch not found")
    _mst_rdy = check_musetalk()
    _w2l_rdy = check_wav2lip()
    print(f"  MuseTalk:  {'OK (primary lip sync)' if _mst_rdy else 'NOT READY (modelos faltando)'}")
    print(f"  Wav2Lip:   {'OK (fallback)' if _w2l_rdy else 'NOT INSTALLED'}")
    print(f"  SadTalker: {'OK (body animation)' if check_sadtalker() else 'NOT INSTALLED'}")
    print(f"  Workers:   {MAX_WORKERS} concurrent jobs")
    print("=" * 60)
    _init_admin_token()
    # ── Licença desktop: validar no boot e exibir estado ─────────────────────
    if _LICENSE_AVAILABLE:
        try:
            _lic.ensure_vendor_keys()  # garante par de chaves do vendor (license authority)
            _LICENSE_STATE = _lic.load_active_license()
            _exp = _LICENSE_STATE.get("expires", "never")
            _exp_lbl = "vitalícia" if _exp == "never" else _exp[:10]
            if _LICENSE_STATE.get("active"):
                print(f"  Licença:   ATIVA — plano '{_LICENSE_STATE['plan']}' (expira: {_exp_lbl})")
            else:
                print(f"  Licença:   TRIAL — {_LICENSE_STATE.get('reason','')}")
                print(f"             Hardware ID: {_LICENSE_STATE.get('hardware_id','')}")
        except Exception as _lbe:
            print(f"  [License] erro no boot: {_lbe}", flush=True)
    _run_startup_diagnostics()
    # DEFINITIVE: Reset active_workers to 0 on every startup
    # All worker threads from previous session are dead after restart.
    _reset_active_workers()  # properly resets the global
    print(f"  Workers: {MAX_WORKERS} max concurrent | active_workers reset to 0")
    _start_watchdog()

    # ── Production server: Waitress (handles 1000+ concurrent pollers) ──────
    # Flask's built-in dev server (app.run) is single-process WSGI and is NOT
    # designed for production load — under hundreds of users polling job status
    # it degrades and can drop connections. Waitress is a battle-tested pure-
    # Python WSGI server that handles high concurrency safely on Windows.
    # Job processing runs in background threads and is unaffected by the HTTP
    # layer choice. Set AVP_DEV=1 to force the Flask dev server (debugging).
    _force_dev = os.environ.get("AVP_DEV", "").strip().lower() in ("1", "true", "yes")
    _served = False
    if not _force_dev:
        try:
            from waitress import serve
            print(f"  Server:  Waitress (production WSGI) — threads=32, conn_limit=1000")
            print("=" * 60, flush=True)
            # listen on BOTH IPv4 and IPv6. On Windows `localhost` resolves to
            # ::1 (IPv6) first; if we bound IPv4-only, every NEW connection via
            # localhost paid a ~2s IPv6→IPv4 fallback penalty (measured). Dual-
            # stack makes every fresh connection ~13ms instead of ~2000ms.
            try:
                serve(
                    app, listen="0.0.0.0:5052 [::]:5052",
                    threads=32,             # generous for polling-heavy workload
                    connection_limit=1000,  # support many concurrent users
                    channel_timeout=300,    # allow slow downloads of large MP4s
                    max_request_body_size=2 * 1024 * 1024 * 1024,  # 2GB uploads
                    ident="AvatarPilotPro",
                )
            except OSError:
                # Some hosts can't dual-bind (e.g. [::] already covers IPv4);
                # fall back to IPv4-only which still serves correctly.
                serve(
                    app, host="0.0.0.0", port=5052,
                    threads=32, connection_limit=1000, channel_timeout=300,
                    max_request_body_size=2 * 1024 * 1024 * 1024,
                    ident="AvatarPilotPro",
                )
            _served = True
        except ImportError:
            print("  [WARN] Waitress não instalado — usando Flask dev server.")
            print("         Para produção rode: pip install waitress")
            print("=" * 60, flush=True)
        except Exception as _e:
            print(f"  [WARN] Waitress falhou ({_e}) — fallback p/ Flask dev server.", flush=True)
    if not _served:
        app.run(host="0.0.0.0", port=5052, debug=False, threaded=True)
