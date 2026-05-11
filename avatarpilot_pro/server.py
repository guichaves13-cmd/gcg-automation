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
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, Response
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
batch_lock  = threading.Lock()
jobs_lock   = threading.Lock()

# ── SQLite path ───────────────────────────────────────────────────────────────
DB_PATH = os.path.join(DATA_DIR, "avatarpilot.db")

# ── Webhook registry {job_id: [urls]} and global webhooks ────────────────────
webhook_registry = {}   # {job_id: [url, ...]}
global_webhooks  = []   # URLs notified on every completed job

# ── Cloud GPU max workers ─────────────────────────────────────────────────────
MAX_WORKERS   = 3       # concurrent SadTalker jobs (cloud can handle more)
active_workers = 0
workers_lock   = threading.Lock()

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
        "edge_voice": "en-US-DavisNeural", "rate": "+15%", "pitch": "+5Hz",
        "description": "Energetic, persuasive sales voice",
        "best_for": ["sales", "marketing", "ads", "promos"],
        "category": "English",
    },
    "corporate_trainer": {
        "edge_voice": "en-US-TonyNeural", "rate": "-5%", "pitch": "+0Hz",
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
        "edge_voice": "en-US-DavisNeural", "rate": "+20%", "pitch": "+15Hz",
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
    conn.commit()
    conn.close()

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
            "SELECT id, config, created_at FROM jobs_db WHERE status NOT IN ('done','error','queued')"
        ).fetchall()
        for row in rows:
            conn.execute("UPDATE jobs_db SET status='error', error='Server restarted' WHERE id=?", (row[0],))
        conn.commit()
        conn.close()
        if rows:
            print(f"  [DB] Marked {len(rows)} interrupted jobs as failed on startup")
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
async def _edge_tts_generate(text, voice, output_path):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

def edge_tts_generate(text, voice, output_path):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_edge_tts_generate(text, voice, output_path))
    finally:
        loop.close()

async def _edge_tts_advanced(text, voice, output_path, rate="0%", pitch="0%"):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(output_path)

def edge_tts_generate_advanced(text, voice, output_path, rate="0%", pitch="0%"):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_edge_tts_advanced(text, voice, output_path, rate, pitch))
    finally:
        loop.close()

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
    return _ffmpeg_path().replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")

def get_media_duration(path: str) -> float:
    """Return duration in seconds via ffprobe.
    Uses a temp copy if the path contains non-ASCII chars (Windows cv2/ffprobe compat)."""
    import tempfile, shutil as _shutil
    work_path = path
    tmp_copy  = None
    try:
        path.encode('ascii')
    except UnicodeEncodeError:
        # Non-ASCII path — copy to temp with safe name
        ext = os.path.splitext(path)[1]
        tmp_copy  = os.path.join(tempfile.gettempdir(), f"apro_dur_{uuid.uuid4().hex[:8]}{ext}")
        _shutil.copy2(path, tmp_copy)
        work_path = tmp_copy
    try:
        r = subprocess.run(
            [_ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", work_path],
            capture_output=True, text=True, timeout=15
        )
        return float(r.stdout.strip())
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

def run_sadtalker(image_path, audio_path, output_path, settings=None):
    """Run SadTalker inference with the venv311 Python."""
    import tempfile
    st_path = os.path.join(MODELS_DIR, "SadTalker")
    if not check_sadtalker():
        raise Exception("SadTalker not installed. Check Settings.")

    settings   = settings or {}
    enhancer   = settings.get("enhancer", "gfpgan")
    still_mode = settings.get("still", False)
    preprocess = settings.get("preprocess", "crop")
    size       = int(settings.get("size", 256))
    exp_scale  = float(settings.get("expression_scale", 1.0))

    # SadTalker's cv2.imread() breaks on non-ASCII Windows paths (e.g. ç).
    # Copy all inputs to a clean ASCII temp directory before running.
    tmp_root = tempfile.mkdtemp(prefix="avatarpilot_")
    safe_img   = os.path.join(tmp_root, "src" + os.path.splitext(image_path)[1])
    safe_audio = os.path.join(tmp_root, "aud" + os.path.splitext(audio_path)[1])
    shutil.copy2(image_path, safe_img)
    shutil.copy2(audio_path, safe_audio)
    image_path = safe_img
    audio_path = safe_audio

    result_dir = os.path.join(tmp_root, "result")
    os.makedirs(result_dir, exist_ok=True)

    ckpt_dir = os.path.join(st_path, "checkpoints")

    cmd = [
        sys.executable,
        os.path.join(st_path, "inference.py"),
        "--driven_audio", audio_path,
        "--source_image", image_path,
        "--result_dir", result_dir,
        "--checkpoint_dir", ckpt_dir,
        "--size", str(size),
        "--expression_scale", str(exp_scale),
        "--preprocess", preprocess,
    ]
    if enhancer in ("gfpgan", "RestoreFormer"):
        cmd += ["--enhancer", enhancer]
    if still_mode:
        cmd.append("--still")

    print(f"  [SadTalker] size={size}, preprocess={preprocess}, enhancer={enhancer}")
    # Timeout scales with size and mode: 256px crop=1800s, 512px full=7200s
    timeout_s = 7200 if (size >= 512 or preprocess in ("full", "extfull")) else 1800
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=st_path, timeout=timeout_s,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}
    )

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[:800]
        # VRAM out-of-memory: clear cache and retry once on CPU
        if "CUDA out of memory" in err or "OutOfMemoryError" in err:
            print("  [SadTalker] VRAM OOM — clearing cache and retrying...")
            try:
                import torch; torch.cuda.empty_cache()
            except Exception:
                pass
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=st_path, timeout=1800,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"}
            )
            if proc.returncode != 0:
                shutil.rmtree(tmp_root, ignore_errors=True)
                raise Exception(f"SadTalker failed after OOM retry: {proc.stderr[:500]}")
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
    s = load_settings()
    if s.get("executor", "local") == "replicate" and s.get("replicate_key", ""):
        print("  [SadTalker] Using Cloud GPU (Replicate A100)")
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = "Using Cloud GPU (Replicate A100)..."
        return run_sadtalker_cloud_replicate(image_path, audio_path, output_path, settings, job_id)

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

    # Short audio: direct processing
    if total_dur <= chunk_duration + 30:
        return run_sadtalker(image_path, audio_path, output_path, settings)

    n_chunks  = math.ceil(total_dur / chunk_duration)
    chunk_dir = os.path.join(OUTPUT_DIR, f"chunks_{uuid.uuid4().hex[:8]}")
    os.makedirs(chunk_dir, exist_ok=True)
    print(f"  [SadTalker] Chunked mode: {total_dur:.0f}s audio → {n_chunks} chunks of {chunk_duration}s")

    chunk_videos = []
    try:
        for i in range(n_chunks):
            start = i * chunk_duration
            dur   = min(chunk_duration, total_dur - start)
            if dur < 2.0:
                break

            # Update job progress
            if job_id and job_id in jobs:
                pct = 35 + int(45 * i / n_chunks)
                jobs[job_id]["progress"] = pct
                jobs[job_id]["message"]  = f"Lip sync chunk {i+1}/{n_chunks} ({start:.0f}s-{start+dur:.0f}s)..."

            # Extract audio chunk (WAV 16kHz mono for SadTalker)
            chunk_audio = os.path.join(chunk_dir, f"chunk_{i:03d}.wav")
            r = subprocess.run([
                ffmpeg, "-y", "-i", audio_path,
                "-ss", str(round(start, 3)), "-t", str(round(dur, 3)),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                chunk_audio
            ], capture_output=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(chunk_audio):
                raise Exception(f"Audio chunk {i} extraction failed")

            # Run SadTalker on this chunk
            chunk_video = os.path.join(chunk_dir, f"chunk_{i:03d}.mp4")
            run_sadtalker(image_path, chunk_audio, chunk_video, settings)
            chunk_videos.append(chunk_video)

            # Free VRAM between chunks
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            print(f"  [SadTalker] Chunk {i+1}/{n_chunks} done — VRAM cleared")

        if not chunk_videos:
            raise Exception("No chunks were processed successfully")

        # Concatenate all chunks
        if job_id and job_id in jobs:
            jobs[job_id]["message"] = f"Concatenating {len(chunk_videos)} video chunks..."

        concat_txt = os.path.join(chunk_dir, "concat.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            for v in chunk_videos:
                f.write(f"file '{v.replace(os.sep, '/')}'\n")

        r = subprocess.run([
            ffmpeg, "-y", "-f", "concat", "-safe", "0",
            "-i", concat_txt,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            output_path
        ], capture_output=True, timeout=600)

        if r.returncode != 0:
            raise Exception(f"Concat failed: {r.stderr[:300]}")

        print(f"  [SadTalker] Concat done: {len(chunk_videos)} chunks → {output_path}")
        return output_path

    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)


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


# ============================================================================
# WEBHOOK NOTIFICATIONS
# ============================================================================
def notify_webhooks(job_id: str, job_data: dict):
    """Fire all registered webhooks for this job (runs in background thread)."""
    urls = db_get_webhooks(job_id)
    if not urls:
        return
    payload = {
        "event":       "job_complete",
        "job_id":      job_id,
        "status":      job_data.get("status"),
        "duration":    job_data.get("duration"),
        "output_url":  f"http://localhost:5052/outputs/{job_data.get('output_filename', '')}",
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
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", output_path
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        print(f"  [Composite] FFmpeg failed: {proc.stderr[:300]}, using avatar directly")
        shutil.copy2(avatar_video, output_path)
    return output_path

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
def run_pipeline(job_id: str, config: dict):
    """Run full avatar generation pipeline with plan limits."""
    try:
        plan = config.get("plan", DEFAULT_PLAN)

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
        else:
            voice = config.get("voice", "")
            # Auto-detect language if voice is not specified
            if not voice or voice == "auto":
                voice = auto_detect_voice(config.get("script", ""))
                print(f"  [AutoVoice] Detected voice: {voice}")
            # Apply voice preset rate/pitch if set
            rate  = config.get("voice_rate", "+0%")
            pitch = config.get("voice_pitch", "+0Hz")
            edge_tts_generate_advanced(
                config["script"], voice, audio_path, rate=rate, pitch=pitch
            )
            jobs[job_id]["progress"] = 25

        # ── Plan limit check ───────────────────────────────────────────────
        ok, dur, limit = check_plan_limit(audio_path, plan)
        jobs[job_id]["audio_duration"] = round(dur, 1)
        if not ok:
            limit_min = int(limit // 60)
            dur_min   = round(dur / 60, 1)
            raise Exception(
                f"Audio duration {dur_min} min exceeds your plan limit of {limit_min} min. "
                f"Upgrade your plan or shorten the script."
            )

        # ── Step 2: Lip Sync (SadTalker) ──────────────────────────────────
        jobs[job_id]["status"]   = "generating_video"
        jobs[job_id]["progress"] = 35
        jobs[job_id]["message"]  = f"Running SadTalker lip sync ({dur:.0f}s audio)..."

        avatar_video = os.path.join(OUTPUT_DIR, f"{job_id}_avatar.mp4")
        st_settings = {
            "preprocess":       config.get("preprocess", "crop"),
            "still":            config.get("still_mode", False),
            "expression_scale": config.get("expression_scale", 1.0),
            "enhancer":         config.get("enhancer", "gfpgan"),
            "size":             config.get("size", 256),
        }
        # Auto-chunk for long audio (>5min) to avoid VRAM overflow + timeout
        run_sadtalker_chunked(
            config["image_path"], audio_path, avatar_video,
            settings=st_settings, chunk_duration=270, job_id=job_id
        )
        jobs[job_id]["progress"] = 80
        jobs[job_id]["message"]  = "Compositing..."

        # ── Step 3: Background Compositing ────────────────────────────────
        jobs[job_id]["status"] = "compositing"
        final_output = os.path.join(OUTPUT_DIR, f"{job_id}_final.mp4")
        bg_path      = config.get("background", "")

        if bg_path and os.path.exists(bg_path):
            composite_with_background(
                avatar_video, bg_path, final_output,
                position=config.get("avatar_position", "bottom_right"),
                avatar_size=config.get("avatar_size", "medium"),
                opacity=float(config.get("avatar_opacity", 1.0)),
            )
        else:
            shutil.copy2(avatar_video, final_output)

        # ── Step 4: Thumbnail ──────────────────────────────────────────────
        thumb_path = os.path.join(OUTPUT_DIR, f"{job_id}_thumb.jpg")
        generate_thumbnail(final_output, thumb_path)

        jobs[job_id]["progress"]        = 100
        jobs[job_id]["status"]          = "done"
        jobs[job_id]["output_path"]     = final_output
        jobs[job_id]["output_filename"] = os.path.basename(final_output)
        jobs[job_id]["thumbnail"]       = f"/outputs/{job_id}_thumb.jpg" if os.path.exists(thumb_path) else ""
        jobs[job_id]["duration"]        = round(get_media_duration(final_output), 1)
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

        # Persist to SQLite + fire webhooks
        db_save_job(job_id, jobs[job_id])
        notify_webhooks(job_id, jobs[job_id])

    except Exception as e:
        jobs[job_id]["status"]  = "error"
        jobs[job_id]["error"]   = str(e)
        jobs[job_id]["message"] = f"Error: {e}"
        print(f"  [Pipeline Error] job={job_id}: {e}")
        db_save_job(job_id, jobs[job_id])
        notify_webhooks(job_id, jobs[job_id])

    finally:
        # Release GPU memory
        release_vram()
        # Clean up tmp avatar (keep final only)
        _safe_rm(os.path.join(OUTPUT_DIR, f"{job_id}_avatar.mp4"))
        # Advance batch queue
        _advance_batch_queue()

def _safe_rm(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

def _advance_batch_queue():
    """Start next batch job if any is waiting."""
    with batch_lock:
        waiting = [j for j in batch_queue if jobs.get(j, {}).get("status") == "queued"]
        if waiting:
            next_id = waiting[0]
            cfg = jobs[next_id].get("_config", {})
            t = Thread(target=run_pipeline, args=(next_id, cfg), daemon=True)
            t.start()

# ============================================================================
# ROUTES — CORE
# ============================================================================
@app.route("/")
def index():
    return render_template("index.html")

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
    voice  = data.get("voice", "en-US-GuyNeural")
    engine = data.get("engine", "edge-tts")
    if not script:
        return jsonify({"error": "No script"}), 400
    pid        = uuid.uuid4().hex[:8]
    audio_path = os.path.join(OUTPUT_DIR, f"preview_{pid}.mp3")
    try:
        if engine == "elevenlabs":
            s = load_settings()
            elevenlabs_generate(script, data.get("voice_id", ""), s.get("elevenlabs_key", ""), audio_path)
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
    script          = request.form.get("script", "")
    voice           = request.form.get("voice", "en-US-GuyNeural")
    engine          = request.form.get("engine", "edge-tts")
    voice_id        = request.form.get("voice_id", "")
    preprocess      = request.form.get("preprocess", "crop")
    still_mode      = request.form.get("still_mode") == "true"
    expression_scale= float(request.form.get("expression_scale", "1.0"))
    enhancer        = request.form.get("enhancer", "gfpgan")
    bg_name         = request.form.get("background", "")
    size            = int(request.form.get("size", "256"))
    avatar_position = request.form.get("avatar_position", "bottom_right")
    avatar_size     = request.form.get("avatar_size", "medium")
    avatar_opacity  = float(request.form.get("avatar_opacity", "1.0"))
    voice_preset    = request.form.get("voice_preset", "")
    plan            = load_settings().get("plan", DEFAULT_PLAN)

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
        return jsonify({"error": "No image uploaded"}), 400
    img_file = request.files["image"]
    img_id   = uuid.uuid4().hex[:8]
    img_ext  = os.path.splitext(img_file.filename)[1] or ".png"
    img_path = os.path.join(UPLOAD_DIR, f"avatar_{img_id}{img_ext}")
    img_file.save(img_path)

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

    bg_path = os.path.join(BG_DIR, bg_name) if bg_name else ""

    job_id = uuid.uuid4().hex[:12]
    config = {
        "script": script, "voice": voice, "tts_engine": engine,
        "voice_id": voice_id, "image_path": img_path,
        "audio_upload": audio_upload, "preprocess": preprocess,
        "still_mode": still_mode, "expression_scale": expression_scale,
        "enhancer": enhancer, "background": bg_path, "size": size,
        "avatar_position": avatar_position, "avatar_size": avatar_size,
        "avatar_opacity": avatar_opacity, "plan": plan,
        "voice_rate": voice_rate, "voice_pitch": voice_pitch,
        "voice_preset": voice_preset,
    }
    jobs[job_id] = {
        "id": job_id, "status": "queued", "progress": 0,
        "created": datetime.now().isoformat(),
        "script_preview": (script[:100] + "...") if len(script) > 100 else script,
        "message": "Queued", "error": "", "_config": config,
    }
    t = Thread(target=run_pipeline, args=(job_id, config), daemon=True)
    t.start()
    return jsonify({"job_id": job_id})

@app.route("/api/job/<job_id>")
def api_job_status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    j = dict(jobs[job_id])
    j.pop("_config", None)
    return jsonify(j)

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
        t = Thread(target=run_pipeline, args=(first_id, cfg0), daemon=True)
        t.start()

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
    total_size = sum(r.get("size_mb", 0) for r in history)
    return jsonify({
        "videos": history,
        "total": len(history),
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
    result = ask_groq(prompt, max_tokens=256, model="llama-4-scout-17b-16e-instruct")
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
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT id, job_id, url, global_hook, created_at FROM webhooks").fetchall()
        conn.close()
        return jsonify({"webhooks": [{"id": r[0], "job_id": r[1], "url": r[2],
                                      "global": bool(r[3]), "created_at": r[4]} for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM webhooks WHERE id=?", (wid,))
        conn.commit()
        conn.close()
        return jsonify({"deleted": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/cloud/test", methods=["POST"])
def api_cloud_test():
    """Test Replicate API key and SadTalker model availability."""
    import requests as _r
    s   = load_settings()
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
            urllib.request.urlretrieve(img_url, img_path)
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
    t = Thread(target=run_pipeline, args=(job_id, config), daemon=True)
    t.start()
    return jsonify({
        "job_id": job_id,
        "status_url": f"http://localhost:5052/api/job/{job_id}",
        "download_url": f"http://localhost:5052/outputs/{job_id}_final.mp4",
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
    for fname in os.listdir(OUTPUT_DIR):
        fp = os.path.join(OUTPUT_DIR, fname)
        if os.path.isfile(fp):
            disk_used += os.path.getsize(fp)

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

    return jsonify({
        "total_generated":  stats.get("total_generated", 0),
        "total_seconds":    stats.get("total_seconds", 0),
        "total_hours":      round(stats.get("total_seconds", 0) / 3600, 2),
        "disk_used_mb":     round(disk_used / 1024**2, 1),
        "active_jobs":      active,
        "gpu":              gpu_info,
        "sadtalker":        {**st_detail, "ready": check_sadtalker()},
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

@app.route("/api/backgrounds/upload", methods=["POST"])
def api_upload_bg():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    safe_name = re.sub(r'[^\w\-_\. ]', '_', f.filename)
    f.save(os.path.join(BG_DIR, safe_name))
    return jsonify({"status": "ok", "name": safe_name})

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
    return jsonify(s)

@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.json or {}
    s    = load_settings()
    for field in ("elevenlabs_key", "tts_engine", "default_voice", "plan", "replicate_key", "executor"):
        if field in data and data[field] is not None:
            s[field] = data[field]
    save_settings(s)
    return jsonify({"status": "ok", "plan": s.get("plan", DEFAULT_PLAN)})

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
# MAIN
# ============================================================================
if __name__ == "__main__":
    # Init SQLite and clean up any interrupted jobs
    init_db()
    db_load_incomplete_jobs()

    s = load_settings()
    executor = s.get("executor", "local")

    print("=" * 60)
    print("  AvatarPilot Pro v3 — AI Talking Avatar Generator")
    print(f"  URL:    http://localhost:5052")
    print(f"  Plan:   {s.get('plan', DEFAULT_PLAN)}")
    print(f"  Executor: {executor.upper()} {'(cloud A100 active)' if executor=='replicate' else '(local GPU)'}")
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
    print(f"  SadTalker: {'OK' if check_sadtalker() else 'NOT INSTALLED'}")
    print(f"  Workers:   {MAX_WORKERS} concurrent jobs")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5052, debug=False, threaded=True)
