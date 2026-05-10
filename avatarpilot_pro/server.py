# -*- coding: utf-8 -*-
"""
AvatarPilot Pro — AI Talking Avatar Generator
Local HeyGen alternative with realistic lip sync + voice
Port: 5052
"""
import os, sys, json, time, uuid, asyncio, subprocess, re, shutil
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from threading import Thread

# =============================================
# CONFIG
# =============================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
BG_DIR = os.path.join(BASE_DIR, "backgrounds")
MODELS_DIR = os.path.join(BASE_DIR, "models")

for d in [UPLOAD_DIR, OUTPUT_DIR, BG_DIR, MODELS_DIR]:
    os.makedirs(d, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB max upload

# =============================================
# SETTINGS STORAGE
# =============================================
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {"elevenlabs_key": "", "tts_engine": "edge-tts", "default_voice": "en-US-GuyNeural"}

def save_settings(s):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)

# =============================================
# JOBS TRACKING
# =============================================
jobs = {}  # {job_id: {status, progress, output_path, error, ...}}

# =============================================
# TTS ENGINES
# =============================================
async def _edge_tts_generate(text, voice, output_path):
    """Generate speech with Edge-TTS (free, Microsoft)."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

def edge_tts_generate(text, voice, output_path):
    """Sync wrapper for edge-tts."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_edge_tts_generate(text, voice, output_path))
    finally:
        loop.close()

def elevenlabs_generate(text, voice_id, api_key, output_path):
    """Generate speech with ElevenLabs API."""
    try:
        from elevenlabs import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        audio = client.text_to_speech.convert(
            text=text,
            voice_id=voice_id,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        )
        with open(output_path, "wb") as f:
            for chunk in audio:
                f.write(chunk)
    except Exception as e:
        raise Exception(f"ElevenLabs error: {e}")

async def _get_edge_voices():
    """List available Edge-TTS voices."""
    import edge_tts
    voices = await edge_tts.list_voices()
    return voices

def get_edge_voices():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_get_edge_voices())
    finally:
        loop.close()

# =============================================
# SADTALKER / LIP SYNC ENGINE  
# =============================================
def check_sadtalker():
    """Check if SadTalker is installed."""
    st_path = os.path.join(MODELS_DIR, "SadTalker")
    return os.path.exists(st_path) and os.path.exists(os.path.join(st_path, "inference.py"))

def run_sadtalker(image_path, audio_path, output_path, settings=None):
    """Run SadTalker inference."""
    st_path = os.path.join(MODELS_DIR, "SadTalker")
    
    if not check_sadtalker():
        raise Exception("SadTalker not installed. Click 'Install SadTalker' in Settings.")
    
    settings = settings or {}
    enhancer = settings.get("enhancer", "gfpgan")  # gfpgan or RestoreFormer
    still_mode = settings.get("still", False)  # Less head movement
    preprocess = settings.get("preprocess", "crop")  # crop, resize, full, extcrop, extfull
    size = settings.get("size", 256)
    exp_scale = settings.get("expression_scale", 1.0)
    
    result_dir = os.path.join(OUTPUT_DIR, f"st_{uuid.uuid4().hex[:8]}")
    os.makedirs(result_dir, exist_ok=True)
    
    cmd = [
        sys.executable, os.path.join(st_path, "inference.py"),
        "--driven_audio", audio_path,
        "--source_image", image_path,
        "--result_dir", result_dir,
        "--size", str(size),
        "--expression_scale", str(exp_scale),
        "--preprocess", preprocess,
        "--enhancer", enhancer,
    ]
    
    if still_mode:
        cmd.append("--still")
    
    print(f"  [SadTalker] Running: {' '.join(cmd)}")
    
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=st_path, timeout=600)
    
    if proc.returncode != 0:
        raise Exception(f"SadTalker failed: {proc.stderr[:500]}")
    
    # Find output video
    for root, dirs, files in os.walk(result_dir):
        for f in files:
            if f.endswith(".mp4"):
                result_video = os.path.join(root, f)
                shutil.copy2(result_video, output_path)
                return output_path
    
    raise Exception("SadTalker produced no output video")

# =============================================
# BACKGROUND COMPOSITING
# =============================================
def composite_with_background(avatar_video, bg_image, output_path):
    """Composite avatar video onto a background image using FFmpeg."""
    if not bg_image or not os.path.exists(bg_image):
        shutil.copy2(avatar_video, output_path)
        return output_path
    
    # Use FFmpeg to overlay avatar on background with chroma key or overlay
    cmd = [
        "ffmpeg", "-y",
        "-i", bg_image,
        "-i", avatar_video,
        "-filter_complex",
        "[1:v]scale=480:-1[avatar];[0:v]scale=1920:1080[bg];[bg][avatar]overlay=W-w-50:H-h-50",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path
    ]
    
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        # Fallback: just use avatar video
        shutil.copy2(avatar_video, output_path)
    
    return output_path

# =============================================
# FULL PIPELINE
# =============================================
def run_pipeline(job_id, config):
    """Run the full avatar generation pipeline."""
    try:
        jobs[job_id]["status"] = "generating_audio"
        jobs[job_id]["progress"] = 10
        
        # Step 1: Generate Audio (TTS)
        audio_path = os.path.join(OUTPUT_DIR, f"{job_id}_audio.mp3")
        
        if config.get("audio_upload"):
            # User uploaded their own audio
            audio_path = config["audio_upload"]
            jobs[job_id]["progress"] = 30
        elif config.get("tts_engine") == "elevenlabs":
            settings = load_settings()
            elevenlabs_generate(
                config["script"],
                config.get("voice_id", "21m00Tcm4TlvDq8ikWAM"),
                settings.get("elevenlabs_key", ""),
                audio_path
            )
            jobs[job_id]["progress"] = 30
        else:
            # Edge-TTS (free)
            edge_tts_generate(
                config["script"],
                config.get("voice", "en-US-GuyNeural"),
                audio_path
            )
            jobs[job_id]["progress"] = 30
        
        # Step 2: Lip Sync (SadTalker)
        jobs[job_id]["status"] = "generating_video"
        jobs[job_id]["progress"] = 40
        
        avatar_video = os.path.join(OUTPUT_DIR, f"{job_id}_avatar.mp4")
        
        run_sadtalker(
            config["image_path"],
            audio_path,
            avatar_video,
            settings={
                "preprocess": config.get("preprocess", "full"),
                "still": config.get("still_mode", False),
                "expression_scale": config.get("expression_scale", 1.0),
                "enhancer": config.get("enhancer", "gfpgan"),
                "size": config.get("size", 256),
            }
        )
        jobs[job_id]["progress"] = 80
        
        # Step 3: Composite with background (optional)
        jobs[job_id]["status"] = "compositing"
        final_output = os.path.join(OUTPUT_DIR, f"{job_id}_final.mp4")
        
        bg_path = config.get("background")
        if bg_path and os.path.exists(bg_path):
            composite_with_background(avatar_video, bg_path, final_output)
        else:
            shutil.copy2(avatar_video, final_output)
        
        jobs[job_id]["progress"] = 100
        jobs[job_id]["status"] = "done"
        jobs[job_id]["output_path"] = final_output
        jobs[job_id]["output_filename"] = os.path.basename(final_output)
        
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        print(f"  [Pipeline Error] {e}")

# =============================================
# ROUTES
# =============================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/voices", methods=["GET"])
def api_voices():
    """List available TTS voices."""
    try:
        voices = get_edge_voices()
        # Group by language
        grouped = {}
        for v in voices:
            lang = v.get("Locale", "unknown")[:5]
            if lang not in grouped:
                grouped[lang] = []
            grouped[lang].append({
                "name": v.get("ShortName", ""),
                "display": v.get("FriendlyName", ""),
                "gender": v.get("Gender", ""),
                "locale": v.get("Locale", ""),
            })
        return jsonify({"voices": grouped, "total": len(voices)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/preview_audio", methods=["POST"])
def api_preview_audio():
    """Generate audio preview."""
    data = request.json
    script = data.get("script", "")[:500]  # Limit preview
    voice = data.get("voice", "en-US-GuyNeural")
    engine = data.get("engine", "edge-tts")
    
    if not script:
        return jsonify({"error": "No script"}), 400
    
    preview_id = uuid.uuid4().hex[:8]
    audio_path = os.path.join(OUTPUT_DIR, f"preview_{preview_id}.mp3")
    
    try:
        if engine == "elevenlabs":
            settings = load_settings()
            elevenlabs_generate(script, data.get("voice_id", ""), settings.get("elevenlabs_key", ""), audio_path)
        else:
            edge_tts_generate(script, voice, audio_path)
        
        return jsonify({"audio_url": f"/outputs/preview_{preview_id}.mp3"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Start avatar video generation."""
    # Handle multipart form data
    script = request.form.get("script", "")
    voice = request.form.get("voice", "en-US-GuyNeural")
    engine = request.form.get("engine", "edge-tts")
    voice_id = request.form.get("voice_id", "")
    preprocess = request.form.get("preprocess", "full")
    still_mode = request.form.get("still_mode") == "true"
    expression_scale = float(request.form.get("expression_scale", "1.0"))
    enhancer = request.form.get("enhancer", "gfpgan")
    bg_name = request.form.get("background", "")
    
    # Handle image upload
    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400
    
    img_file = request.files["image"]
    img_ext = os.path.splitext(img_file.filename)[1] or ".png"
    img_id = uuid.uuid4().hex[:8]
    img_path = os.path.join(UPLOAD_DIR, f"avatar_{img_id}{img_ext}")
    img_file.save(img_path)
    
    # Handle audio upload (optional)
    audio_upload = None
    if "audio" in request.files and request.files["audio"].filename:
        aud_file = request.files["audio"]
        aud_ext = os.path.splitext(aud_file.filename)[1] or ".mp3"
        aud_path = os.path.join(UPLOAD_DIR, f"audio_{img_id}{aud_ext}")
        aud_file.save(aud_path)
        audio_upload = aud_path
    
    # Background
    bg_path = ""
    if bg_name:
        bg_path = os.path.join(BG_DIR, bg_name)
    
    if not script and not audio_upload:
        return jsonify({"error": "Need script or audio"}), 400
    
    # Create job
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "progress": 0,
        "created": datetime.now().isoformat(),
        "script": script[:100] + "..." if len(script) > 100 else script,
    }
    
    config = {
        "script": script,
        "voice": voice,
        "tts_engine": engine,
        "voice_id": voice_id,
        "image_path": img_path,
        "audio_upload": audio_upload,
        "preprocess": preprocess,
        "still_mode": still_mode,
        "expression_scale": expression_scale,
        "enhancer": enhancer,
        "background": bg_path,
    }
    
    # Run pipeline in background thread
    t = Thread(target=run_pipeline, args=(job_id, config), daemon=True)
    t.start()
    
    return jsonify({"job_id": job_id})

@app.route("/api/job/<job_id>", methods=["GET"])
def api_job_status(job_id):
    """Check job status."""
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(jobs[job_id])

@app.route("/api/backgrounds", methods=["GET"])
def api_backgrounds():
    """List available backgrounds."""
    bgs = []
    for f in os.listdir(BG_DIR):
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            bgs.append({"name": f, "url": f"/backgrounds/{f}"})
    return jsonify({"backgrounds": bgs})

@app.route("/api/backgrounds/upload", methods=["POST"])
def api_upload_bg():
    """Upload a new background."""
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    f.save(os.path.join(BG_DIR, f.filename))
    return jsonify({"status": "ok", "name": f.filename})

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    s = load_settings()
    # Mask API key
    if s.get("elevenlabs_key"):
        s["elevenlabs_key_set"] = True
        s["elevenlabs_key"] = "***" + s["elevenlabs_key"][-4:]
    return jsonify(s)

@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.json
    s = load_settings()
    if data.get("elevenlabs_key"):
        s["elevenlabs_key"] = data["elevenlabs_key"]
    if data.get("tts_engine"):
        s["tts_engine"] = data["tts_engine"]
    if data.get("default_voice"):
        s["default_voice"] = data["default_voice"]
    save_settings(s)
    return jsonify({"status": "ok"})

@app.route("/api/install_sadtalker", methods=["POST"])
def api_install_sadtalker():
    """Install SadTalker model."""
    def do_install():
        st_path = os.path.join(MODELS_DIR, "SadTalker")
        if not os.path.exists(st_path):
            subprocess.run([
                "git", "clone", "--depth", "1",
                "https://github.com/OpenTalker/SadTalker.git",
                st_path
            ], check=True)
        
        # Install requirements
        req_file = os.path.join(st_path, "requirements.txt")
        if os.path.exists(req_file):
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file, "--quiet"], check=True)
        
        # Download checkpoints
        ckpt_script = os.path.join(st_path, "scripts", "download_models.sh")
        # For Windows, we'll need to handle this differently
        print("  [Install] SadTalker cloned. Download model weights manually if needed.")
    
    t = Thread(target=do_install, daemon=True)
    t.start()
    return jsonify({"status": "installing", "message": "SadTalker installation started..."})

@app.route("/api/check_models", methods=["GET"])
def api_check_models():
    """Check installed models."""
    return jsonify({
        "sadtalker": check_sadtalker(),
        "models_dir": MODELS_DIR,
    })

@app.route("/outputs/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)

@app.route("/backgrounds/<path:filename>")
def serve_bg(filename):
    return send_from_directory(BG_DIR, filename)

@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# =============================================
# MAIN
# =============================================
if __name__ == "__main__":
    import io, sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    print("=" * 60)
    print("  AvatarPilot Pro - AI Talking Avatar Generator")
    print(f"  Base: {BASE_DIR}")
    print(f"  URL: http://localhost:5052")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5052, debug=False)
