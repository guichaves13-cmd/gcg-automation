# -*- coding: utf-8 -*-
"""
AvatarPilot Pro — AI Talking Avatar Generator v2
Port: 5052
Features: SadTalker lip sync, Edge-TTS/ElevenLabs, background compositing,
          history, batch processing, AI script generation, templates, dashboard,
          plan-based duration limits (30min / 1h).
"""
import os, sys, json, time, uuid, asyncio, subprocess, re, shutil, hashlib, threading
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

def elevenlabs_generate(text, voice_id, api_key, output_path):
    try:
        from elevenlabs import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        audio = client.text_to_speech.convert(
            text=text, voice_id=voice_id,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        )
        with open(output_path, "wb") as f:
            for chunk in audio:
                f.write(chunk)
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
    """Return duration in seconds via ffprobe."""
    try:
        r = subprocess.run(
            [_ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0

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
    st_path = os.path.join(MODELS_DIR, "SadTalker")
    if not check_sadtalker():
        raise Exception("SadTalker not installed. Check Settings.")

    settings   = settings or {}
    enhancer   = settings.get("enhancer", "gfpgan")
    still_mode = settings.get("still", False)
    preprocess = settings.get("preprocess", "crop")
    size       = int(settings.get("size", 256))
    exp_scale  = float(settings.get("expression_scale", 1.0))

    result_dir = os.path.join(OUTPUT_DIR, f"st_{uuid.uuid4().hex[:8]}")
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
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=st_path, timeout=1800,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}
    )

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[:800]
        raise Exception(f"SadTalker failed (code {proc.returncode}): {err}")

    # Find output video (deepest .mp4 inside result_dir)
    found = []
    for root, _, files in os.walk(result_dir):
        for f in files:
            if f.endswith(".mp4"):
                found.append(os.path.join(root, f))
    if not found:
        raise Exception("SadTalker produced no output video. Check GPU/CUDA.")

    shutil.copy2(max(found, key=os.path.getmtime), output_path)
    return output_path

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
            elevenlabs_generate(
                config["script"],
                config.get("voice_id", "21m00Tcm4TlvDq8ikWAM"),
                settings.get("elevenlabs_key", ""),
                audio_path
            )
            jobs[job_id]["progress"] = 25
        else:
            edge_tts_generate(
                config["script"],
                config.get("voice", "en-US-GuyNeural"),
                audio_path
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
        run_sadtalker(
            config["image_path"],
            audio_path,
            avatar_video,
            settings={
                "preprocess":        config.get("preprocess", "crop"),
                "still":             config.get("still_mode", False),
                "expression_scale":  config.get("expression_scale", 1.0),
                "enhancer":          config.get("enhancer", "gfpgan"),
                "size":              config.get("size", 256),
            }
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

    except Exception as e:
        jobs[job_id]["status"]  = "error"
        jobs[job_id]["error"]   = str(e)
        jobs[job_id]["message"] = f"Error: {e}"
        print(f"  [Pipeline Error] job={job_id}: {e}")

    finally:
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
    plan            = load_settings().get("plan", DEFAULT_PLAN)

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
    return jsonify(s)

@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.json or {}
    s    = load_settings()
    for field in ("elevenlabs_key", "tts_engine", "default_voice", "plan"):
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
    print("=" * 60)
    print("  AvatarPilot Pro v2 — AI Talking Avatar Generator")
    print(f"  URL:  http://localhost:5052")
    print(f"  Plan: {load_settings().get('plan', DEFAULT_PLAN)}")
    print(f"  GPU:  ", end="")
    try:
        import torch
        if torch.cuda.is_available():
            print(torch.cuda.get_device_name(0))
        else:
            print("CPU only")
    except Exception:
        print("PyTorch not found")
    print(f"  SadTalker: {'OK' if check_sadtalker() else 'NOT INSTALLED'}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5052, debug=False, threaded=True)
