"""StudioPilot Pro â€" Web Edition (Flask Server)
Port: 5051 (independent from TitlePilot on 5050)
"""
import os, sys, json, threading, shutil, time, hashlib, secrets, tempfile

# Fix Windows encoding crash (charmap codec can't encode unicode)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
os.environ['PYTHONIOENCODING'] = 'utf-8'
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory, send_file, session, redirect, Response, stream_with_context

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

# Ensure ffmpeg/ffprobe are on PATH
ffmpeg_dir = os.path.join(PROJECT_DIR, "ffmpeg")
if os.path.isdir(ffmpeg_dir) and ffmpeg_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
    print(f"  [PATH] Added ffmpeg: {ffmpeg_dir}")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")

def _load_users():
    try:
        with open(USERS_FILE) as f: return json.load(f)
    except: return {"admin": hashlib.sha256("admin123".encode()).hexdigest()}

def _save_users(users):
    with open(USERS_FILE,"w") as f: json.dump(users,f,indent=2)
STATS_FILE = os.path.join(PROJECT_DIR, "stats.json")
PLANS_FILE = os.path.join(PROJECT_DIR, "plans.json")
UPLOAD_DIR = os.path.join(PROJECT_DIR, "uploads")
OUTPUT_DIR = r"C:\Users\Guilherme\Downloads\videos ferramenta"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Pipeline status tracking — expanded for real-time monitoring
pipeline_status = {
    "running": False,
    "progress": 0,
    "message": "",
    "error": "",
    "phase": "",
    "phase_idx": 0,       # 0-8 phase steps
    "logs": [],           # circular buffer, last 60 entries {t, msg, level}
    "ai_stats": {
        "clips_downloaded": 0,
        "clips_validated": 0,
        "clips_rejected": 0,
        "clips_fixed": 0,
        "segments_total": 0,
        "segments_done": 0,
        "bad_segments": 0,
        "quality_score": None,
        "theme": "",
        "language": "",
    },
    "start_time": None,
    "elapsed": 0,
}
active_pipelines = 0
_pipeline_thread = None  # track the actual thread for is_alive() checks

def _push_log(msg, level="info"):
    """Add entry to rotating log buffer (max 60)."""
    elapsed = 0
    if pipeline_status["start_time"]:
        elapsed = round(time.time() - pipeline_status["start_time"], 1)
    pipeline_status["logs"].append({"t": elapsed, "msg": msg[:200], "level": level})
    if len(pipeline_status["logs"]) > 60:
        pipeline_status["logs"] = pipeline_status["logs"][-60:]

_PHASE_MAP = [
    (5,  20, 1, "IA Whisper + Video Intelligence"),
    (20, 53, 2, "IA Validadora — Download B-roll"),
    (53, 55, 3, "IA Gerente Geral — Auditoria"),
    (55, 60, 4, "Timeline Builder"),
    (60, 83, 5, "Processamento de Segmentos"),
    (83, 85, 6, "IA Corretor — Inspecao Final"),
    (85, 97, 7, "Concatenacao + Legendas + SFX"),
    (97, 100, 8, "IA Auditor Final"),
]

def _detect_phase(pct, msg):
    """Derive phase name from progress % and message."""
    for lo, hi, idx, name in _PHASE_MAP:
        if lo <= pct < hi:
            return idx, name
    if pct >= 100:
        return 9, "Finalizado"
    return 0, "Inicializando"

def _parse_stats_from_msg(msg):
    """Extract numeric stats from on_progress messages."""
    import re
    stats = pipeline_status["ai_stats"]
    # Segment progress: "Segment 12/30"
    m = re.search(r"Segment\s+(\d+)/(\d+)", msg, re.IGNORECASE)
    if m:
        stats["segments_done"] = int(m.group(1))
        stats["segments_total"] = int(m.group(2))
    # clips_downloaded= / clips_validated= (structured stat lines)
    m = re.search(r"clips_downloaded=(\d+)", msg)
    if m:
        stats["clips_downloaded"] = int(m.group(1))
    m = re.search(r"clips_validated=(\d+)", msg)
    if m:
        stats["clips_validated"] = int(m.group(1))
    m = re.search(r"clips_fixed=(\d+)", msg)
    if m:
        stats["clips_fixed"] = int(m.group(1))
    # Gerente Geral bad clips: "Re-baixando 5 clips"
    m = re.search(r"Re-baixando\s+(\d+)\s+clips", msg)
    if m:
        stats["clips_rejected"] = int(m.group(1))
    # IA Corretor: "X segmentos reprovados"
    m = re.search(r"(\d+)\s+segmentos?\s+reprovados", msg)
    if m:
        stats["bad_segments"] = int(m.group(1))
    # Quality score: "score=75%" or "score=0.75"
    m = re.search(r"score=(\d[\d.]*%?)", msg)
    if m:
        raw = m.group(1).replace("%", "")
        try:
            v = float(raw)
            stats["quality_score"] = v / 100 if v > 1 else v
        except ValueError:
            pass
    # Theme/language
    m = re.search(r"Theme:(\S+)", msg)
    if m:
        stats["theme"] = m.group(1)
    m = re.search(r"Language:(\w+)", msg)
    if m:
        stats["language"] = m.group(1)

def _load_stats():
    try:
        with open(STATS_FILE) as f: return json.load(f)
    except: return {"total":0,"today":0,"last":"","history":[]}

def _save_stats(stats):
    with open(STATS_FILE,"w") as f: json.dump(stats,f,indent=2)

def _load_plans():
    try:
        with open(PLANS_FILE) as f: return json.load(f)
    except: return []

def _save_plans(plans):
    with open(PLANS_FILE,"w") as f: json.dump(plans,f,indent=2)

@app.route("/")
def index():
    r = app.make_response(render_template("index.html"))
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, public, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r

@app.after_request
def add_header(r):
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, public, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r

@app.route("/api/stats")
def api_stats():
    s = _load_stats()
    return jsonify(s)

@app.route("/api/plans", methods=["GET"])
def api_plans_get():
    return jsonify(_load_plans())

@app.route("/api/plans", methods=["POST"])
def api_plans_post():
    data = request.json
    plans = _load_plans()
    plans.append({
        "id": int(time.time()*1000),
        "title": data.get("title",""),
        "notes": data.get("notes",""),
        "status": data.get("status","idea"),
        "tags": data.get("tags",[]),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    _save_plans(plans)
    return jsonify({"ok":True})

@app.route("/api/plans/move", methods=["POST"])
def api_plans_move():
    data = request.json
    plans = _load_plans()
    for p in plans:
        if p.get("id") == data.get("id"):
            p["status"] = data.get("status","idea")
            break
    _save_plans(plans)
    return jsonify({"ok":True})

@app.route("/api/plans/delete", methods=["POST"])
def api_plans_delete():
    data = request.json
    plans = [p for p in _load_plans() if p.get("id") != data.get("id")]
    _save_plans(plans)
    return jsonify({"ok":True})

@app.route("/api/keys", methods=["GET"])
def api_keys_get():
    from core.api_keys import load_api_key
    keys = {}
    for k in ["google_ai","youtube","pexels","pixabay","unsplash"]:
        v = load_api_key(k)
        keys[k] = bool(v)
    # youtube_channels é texto visível — retorna o valor real para mostrar no campo
    keys["youtube_channels"] = load_api_key("youtube_channels") or ""
    return jsonify(keys)

@app.route("/api/keys/save", methods=["POST"])
def api_keys_save():
    from core.api_keys import save_api_key
    data = request.json
    count = 0
    for k,v in data.items():
        if v and v.strip():
            save_api_key(k, v.strip())
            count += 1
    return jsonify({"saved": count})

@app.route("/api/voices")
def api_voices():
    from studiopilot_web.voices import VOICES, LANG_LABELS, get_total
    return jsonify({"voices": VOICES, "labels": LANG_LABELS, "total": get_total()})

@app.route("/api/narrate", methods=["POST"])
def api_narrate():
    from core.api_keys import load_api_key
    from studiopilot_web.script_engine import build_prompt
    data = request.json
    key = data.get("api_key") or load_api_key("google_ai")
    if not key: return jsonify({"error":"No Google AI key"}), 400
    topic = data.get("topic","")
    lang = data.get("language","English")
    duration = data.get("duration","5 min")
    tone = data.get("tone","Documentary serious")
    engine = data.get("engine","gemini-2.0-flash")
    try:
        from google import genai
        client = genai.Client(api_key=key)
        prompt = build_prompt(topic, duration, tone, lang)
        r = client.models.generate_content(model=engine, contents=prompt)
        word_count = len(r.text.split()) if r.text else 0
        return jsonify({"script": r.text, "word_count": word_count, "hook_used": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.json
    users = _load_users()
    user = data.get("username","")
    pwd = hashlib.sha256(data.get("password","").encode()).hexdigest()
    if users.get(user) == pwd:
        session["user"] = user
        return jsonify({"ok": True, "user": user})
    return jsonify({"error": "Invalid credentials"}), 401

@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.json
    users = _load_users()
    user = data.get("username","")
    if user in users: return jsonify({"error": "User already exists"}), 409
    users[user] = hashlib.sha256(data.get("password","").encode()).hexdigest()
    _save_users(users)
    session["user"] = user
    return jsonify({"ok": True, "user": user})

@app.route("/api/auth/check")
def api_auth_check():
    return jsonify({"logged_in": "user" in session, "user": session.get("user","")})

@app.route("/api/gallery")
def api_gallery():
    """List all output files available for download."""
    files = []
    try:
        entries = list(Path(OUTPUT_DIR).iterdir())
    except Exception as e:
        return jsonify({"files": [], "dir": OUTPUT_DIR, "error": str(e)})
    for f in entries:
        try:
            if not f.is_file():
                continue
            if f.suffix.lower() not in ('.mp4', '.mkv', '.avi', '.mov', '.srt'):
                continue
            st = f.stat()
            files.append({
                "name": f.name,
                "size": st.st_size,
                "size_mb": round(st.st_size / 1024 / 1024, 1),
                "modified": datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"),
                "mtime": st.st_mtime,
                "ext": f.suffix.lower(),
            })
        except Exception:
            continue
    files.sort(key=lambda x: x["mtime"], reverse=True)
    for f in files:
        f.pop("mtime", None)
    return jsonify({"files": files, "dir": OUTPUT_DIR, "total": len(files)})

@app.route("/api/gallery/delete", methods=["POST"])
def api_gallery_delete():
    """Delete a file from the output directory."""
    data = request.json or {}
    name = Path(data.get("name", "")).name  # strip any path traversal
    if not name:
        return jsonify({"error": "No filename"}), 400
    target = Path(OUTPUT_DIR) / name
    if not target.exists():
        return jsonify({"error": "File not found"}), 404
    try:
        target.unlink()
        # Also delete matching .srt if deleting .mp4, and vice-versa
        stem = target.stem
        for ext in ('.srt', '.mp4', '_quality_report.json'):
            companion = Path(OUTPUT_DIR) / (stem + ext)
            if companion.exists() and companion != target:
                try:
                    companion.unlink()
                except Exception:
                    pass
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/download/<path:filename>")
def api_download(filename):
    """Download a file from the output directory."""
    safe_name = Path(filename).name  # Prevent directory traversal
    return send_from_directory(OUTPUT_DIR, safe_name, as_attachment=True)

@app.route("/output/<path:filename>")
def serve_output(filename):
    """Serve output files for preview."""
    safe_name = Path(filename).name
    return send_from_directory(OUTPUT_DIR, safe_name)

@app.route("/api/system/health")
def api_system_health():
    """AI System Diagnostic."""
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ffprobe_ok = shutil.which("ffprobe") is not None

    try:
        from core.api_keys import load_api_key
        keys_ok = bool(load_api_key("google_ai") or load_api_key("pexels"))
        configured_keys = [k for k in ["google_ai","youtube","pexels","pixabay","unsplash"] if load_api_key(k)]
    except Exception:
        keys_ok = False
        configured_keys = []

    whisper_ok = False
    try:
        import whisper  # noqa
        whisper_ok = True
    except ImportError:
        pass

    healthy = ffmpeg_ok and ffprobe_ok
    status = "healthy" if (healthy and keys_ok) else ("warning" if healthy else "error")
    if not whisper_ok:
        status = "warning"

    msgs = []
    if healthy and keys_ok:
        msgs.append(f"Sistema operacional. {len(configured_keys)} APIs configuradas.")
    elif not healthy:
        msgs.append("FFmpeg nao encontrado!")
    if not whisper_ok:
        msgs.append("Whisper nao instalado (transcricao desativada). pip install openai-whisper")

    return jsonify({
        "status": status,
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
        "api_keys": keys_ok,
        "configured_keys": configured_keys,
        "whisper": whisper_ok,
        "storage": os.path.exists(OUTPUT_DIR),
        "message": " | ".join(msgs) if msgs else "OK"
    })

@app.route("/api/voice_preview", methods=["POST"])
def api_voice_preview():
    data = request.json
    voice = data.get("voice", "")
    if not voice:
        return jsonify({"error": "No voice provided"}), 400
    
    import edge_tts, tempfile, asyncio
    preview_text = "Esta e uma demonstracao da minha voz. Espero que goste!"
    if "en" in voice.lower():
        preview_text = "This is a demonstration of my voice. I hope you like it!"
    
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        
        async def _gen():
            c = edge_tts.Communicate(preview_text, voice)
            await c.save(tmp.name)
        asyncio.run(_gen())
        
        return send_file(tmp.name, mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Upload avatar video file."""
    if 'file' not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({"error": "No filename"}), 400
    # Clean filename
    safe_name = f.filename.replace(' ', '_')
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    f.save(save_path)
    size_mb = os.path.getsize(save_path) / 1024 / 1024
    return jsonify({"path": save_path, "name": safe_name, "size_mb": round(size_mb, 1)})

@app.route("/api/uploads")
def api_uploads_list():
    """List uploaded files."""
    files = []
    if os.path.exists(UPLOAD_DIR):
        for f in sorted(os.listdir(UPLOAD_DIR), key=lambda x: os.path.getmtime(os.path.join(UPLOAD_DIR, x)), reverse=True):
            fp = os.path.join(UPLOAD_DIR, f)
            if os.path.isfile(fp):
                files.append({"name": f, "path": fp, "size_mb": round(os.path.getsize(fp)/1024/1024, 1)})
    return jsonify(files)

@app.route("/api/pipeline/status")
def api_pipeline_status():
    # Auto-sync: if thread is dead, mark not running
    global active_pipelines, _pipeline_thread
    if _pipeline_thread is not None and not _pipeline_thread.is_alive():
        active_pipelines = 0
        pipeline_status["running"] = False
        _pipeline_thread = None
    return jsonify(pipeline_status)

@app.route("/api/pipeline/stream")
def api_pipeline_stream():
    """SSE stream — pushes pipeline_status updates in real-time."""
    @stream_with_context
    def event_gen():
        last_log_len = 0
        last_progress = -1
        while True:
            changed = (
                pipeline_status["progress"] != last_progress
                or len(pipeline_status["logs"]) != last_log_len
            )
            if changed:
                last_progress = pipeline_status["progress"]
                last_log_len = len(pipeline_status["logs"])
                data = json.dumps(pipeline_status, ensure_ascii=False)
                yield f"data: {data}\n\n"
            if not pipeline_status["running"]:
                # Final event after run ends
                data = json.dumps(pipeline_status, ensure_ascii=False)
                yield f"data: {data}\n\n"
                yield "data: {\"__done__\": true}\n\n"
                break
            time.sleep(0.4)
    return Response(event_gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/pipeline/start", methods=["POST"])
def api_pipeline_start():
    global active_pipelines, pipeline_status, _pipeline_thread

    # Ground truth: if the thread is dead (for ANY reason), reset everything
    if _pipeline_thread is None or not _pipeline_thread.is_alive():
        active_pipelines = 0
        pipeline_status["running"] = False

    if active_pipelines >= 1:
        return jsonify({"error": "Ja existe um video sendo processado. Aguarde terminar ou cancele antes de iniciar outro."}), 409

    active_pipelines += 1
    data = request.json
    avatar_path = data.get("avatar_path", "")
    if not avatar_path or not os.path.exists(avatar_path):
        active_pipelines -= 1
        return jsonify({"error": f"Arquivo nao encontrado: {avatar_path}"}), 400
    output_name = data.get("output_name", f"video_{int(time.time())}.mp4")
    output_path = os.path.join(OUTPUT_DIR, output_name)
    # CRITICAL FIX: update GLOBAL dict in-place (not create local!)
    pipeline_status["running"] = True
    pipeline_status["progress"] = 0
    pipeline_status["message"] = "Inicializando..."
    pipeline_status["error"] = ""
    pipeline_status["phase"] = "Inicializando"
    pipeline_status["phase_idx"] = 0
    pipeline_status["logs"] = []
    pipeline_status["start_time"] = time.time()
    pipeline_status["elapsed"] = 0
    pipeline_status["ai_stats"] = {
        "clips_downloaded": 0, "clips_validated": 0, "clips_rejected": 0,
        "clips_fixed": 0, "segments_total": 0, "segments_done": 0,
        "bad_segments": 0, "quality_score": None, "theme": "", "language": "",
    }
    _push_log("Pipeline iniciado", "info")

    def _progress_cb(current, total, msg):
        pct = int((current / max(total, 1)) * 100)
        pipeline_status["progress"] = pct
        pipeline_status["message"] = msg
        if pipeline_status["start_time"]:
            pipeline_status["elapsed"] = round(time.time() - pipeline_status["start_time"], 1)
        idx, phase_name = _detect_phase(pct, msg)
        if pipeline_status["phase_idx"] != idx:
            pipeline_status["phase_idx"] = idx
            pipeline_status["phase"] = phase_name
            _push_log(f"[FASE {idx}] {phase_name}", "phase")
        _parse_stats_from_msg(msg)
        _push_log(msg, "info")

    def run():
        global pipeline_status, active_pipelines
        try:
            from core.api_keys import load_api_key
            config = {
                "avatar_video": avatar_path,
                "output_file": output_path,
                "pipeline": data.get("pipeline", "avatar_auto"),
                "resolution": data.get("resolution", "1080p"),
                "fps": 30,
                "music_enabled": data.get("music", False),
                "subtitles_enabled": data.get("subtitles", True),
                "force_new_subtitles": True,
                "google_api_key": load_api_key("google_ai"),
                "pexels_api_key": load_api_key("pexels"),
                "pixabay_api_key": load_api_key("pixabay"),
                "unsplash_api_key": load_api_key("unsplash"),
                "youtube_api_key": load_api_key("youtube"),
                "youtube_channel_ids": data.get("youtube_channel_ids", load_api_key("youtube_channels") or ""),
                "auto_broll_count": int(data.get("broll_count", 30)),
                "stickers_enabled": data.get("stickers", True),
                "avatar": {"min_broll_duration": 5, "max_broll_duration": 8},
            }
            from core.pipeline_avatar_auto import run_auto
            run_auto(config, on_progress=_progress_cb)
            pipeline_status["progress"] = 100
            pipeline_status["message"] = f"Done! {output_name}"
            s = _load_stats()
            today = datetime.now().strftime("%Y-%m-%d")
            if s.get("last") != today: s["today"] = 0; s["last"] = today
            s["total"] = s.get("total", 0) + 1
            s["today"] = s.get("today", 0) + 1
            s.setdefault("history", []).insert(0, {"f": output_name, "d": datetime.now().strftime("%H:%M")})
            s["history"] = s["history"][:30]
            _save_stats(s)
        except Exception as e:
            pipeline_status["error"] = str(e)
            pipeline_status["message"] = f"Error: {e}"
            print(f"Pipeline error: {e}")
        finally:
            pipeline_status["running"] = False
            global active_pipelines
            active_pipelines = 0  # always reset — one pipeline at a time
    t = threading.Thread(target=run, daemon=False)
    _pipeline_thread = t
    t.start()
    return jsonify({"started": True, "output": output_path})

@app.route("/api/pipeline/cancel", methods=["POST"])
def api_pipeline_cancel():
    global pipeline_status, active_pipelines, _pipeline_thread
    active_pipelines = 0
    _pipeline_thread = None
    pipeline_status["running"] = False
    pipeline_status["progress"] = 0
    pipeline_status["message"] = "Cancelado pelo usuario."
    pipeline_status["error"] = "Processo interrompido."
    return jsonify({"success": True})

@app.route("/api/pipeline/reset", methods=["POST"])
def api_pipeline_reset():
    """Emergency reset — clears all pipeline state no matter what."""
    global pipeline_status, active_pipelines, _pipeline_thread
    active_pipelines = 0
    _pipeline_thread = None
    pipeline_status["running"] = False
    pipeline_status["progress"] = 0
    pipeline_status["message"] = ""
    pipeline_status["error"] = ""
    pipeline_status["phase"] = ""
    pipeline_status["phase_idx"] = 0
    pipeline_status["logs"] = []
    pipeline_status["start_time"] = None
    pipeline_status["elapsed"] = 0
    return jsonify({"success": True, "message": "Estado resetado com sucesso."})

@app.route("/api/clean", methods=["POST"])
def api_clean():
    try:
        tmp = tempfile.gettempdir()
        cleaned = 0
        for d in os.listdir(tmp):
            if d.startswith("studiopilot_auto") or d.startswith("veo3_prompts_"):
                path = os.path.join(tmp, d)
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                    cleaned += 1
        return jsonify({"success": True, "cleaned_dirs": cleaned})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/research", methods=["POST"])
def api_research():
    """Search stock footage from multiple sources."""
    from core.api_keys import load_api_key
    data = request.json
    query = data.get("query","")
    sources = data.get("sources",[])
    results = []
    
    if "youtube" in sources:
        try:
            key = load_api_key("youtube")
            if key:
                from core.youtube_api import search_trending
                from datetime import timedelta
                week_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
                vids = search_trending(query, key, max_results=12, published_after=week_ago)
                for v in vids:
                    results.append({
                        "source": "youtube",
                        "type": "video",
                        "title": v.get("title", ""),
                        "url": v.get("url", ""),
                        "preview": v.get("thumbnail", ""),
                        "vph": v.get("vph", 0),
                        "views": v.get("views", 0)
                    })
        except Exception as e:
            print(f"YouTube search error: {e}")
            
    if "pexels" in sources:
        try:
            key = load_api_key("pexels")
            if key:
                from core.pexels_stock import search_videos, search_photos
                vids = search_videos(key, query, count=6)
                for v in vids:
                    results.append({"source":"pexels","type":"video","title":query,"url":v.get("url",""),"preview":v.get("preview","")})
                pics = search_photos(key, query, count=6)
                for p in pics:
                    results.append({"source":"pexels","type":"photo","title":query,"url":p.get("url",""),"preview":p.get("preview","")})
        except Exception as e:
            print(f"Pexels error: {e}")
    
    if "pixabay" in sources:
        try:
            key = load_api_key("pixabay")
            if key:
                from core.pixabay_stock import search_videos, search_photos
                vids = search_videos(key, query, count=6)
                for v in vids:
                    results.append({"source":"pixabay","type":"video","title":query,"url":v.get("url",""),"preview":v.get("preview","")})
                pics = search_photos(key, query, count=6)
                for p in pics:
                    results.append({"source":"pixabay","type":"photo","title":query,"url":p.get("url",""),"preview":p.get("preview","")})
        except Exception as e:
            print(f"Pixabay error: {e}")
    
    return jsonify({"results": results, "total": len(results)})

@app.route("/api/radar", methods=["POST"])
def api_radar():
    """Scan YouTube for trending niches and high potential channels."""
    from core.api_keys import load_api_key
    data = request.json
    query = data.get("query", "")
    region = data.get("region", "US")
    
    key = load_api_key("youtube")
    if not key:
        return jsonify({"error": "YouTube API key nao configurada. Va em Configuracoes e adicione sua chave."}), 400
        
    try:
        from core.youtube_api import search_trending
        from datetime import timedelta
        # Search last 30 days for fresh trends
        month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        vids = search_trending(query, key, max_results=20, published_after=month_ago, region=region)
        return jsonify({"results": vids, "total": len(vids)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/veo3/generate", methods=["POST"])
def api_veo3_generate():
    """Parse VEO3 prompts and generate clips."""
    data = request.json
    prompts_text = data.get("prompts","")
    prompts = [p.strip() for p in prompts_text.split("\n") if p.strip()]
    return jsonify({"prompts": prompts, "count": len(prompts), "status": "ready"})


@app.route("/api/veo3/auto_prompts", methods=["POST"])
def api_veo3_auto_prompts():
    """Auto-generate VEO3 prompts from an uploaded avatar video using AI analysis."""
    from core.api_keys import load_api_key
    data = request.json
    avatar_path = data.get("avatar_path", "")
    if not avatar_path or not os.path.exists(avatar_path):
        return jsonify({"error": f"File not found: {avatar_path}"}), 400

    key = load_api_key("google_ai")
    if not key:
        return jsonify({"error": "Google AI key not configured"}), 400

    try:
        import tempfile, shutil as _sh
        tmp = tempfile.mkdtemp(prefix="veo3_prompts_")
        try:
            from core.video_intelligence import VideoIntelligence
            intel = VideoIntelligence(google_api_key=key)
            analysis = intel.analyze_video(avatar_path, tmp)
            from core.veo3_generator import generate_prompts_from_analysis
            prompts = generate_prompts_from_analysis(analysis)
            return jsonify({
                "prompts": prompts,
                "count": len(prompts),
                "theme": analysis.get("theme", ""),
                "language": analysis.get("language", "en"),
            })
        finally:
            _sh.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/gallery/open_folder", methods=["POST"])
def api_gallery_open_folder():
    """Open the output folder in Windows Explorer."""
    try:
        import subprocess as _sp
        _sp.Popen(["explorer", OUTPUT_DIR])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/storage")
def api_storage():
    """Get storage usage info."""
    total = 0
    files = []
    if os.path.exists(OUTPUT_DIR):
        for f in os.listdir(OUTPUT_DIR):
            fp = os.path.join(OUTPUT_DIR, f)
            if os.path.isfile(fp) and f.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.srt')):
                size = os.path.getsize(fp)
                total += size
                files.append({"name":f,"size":size,"date":datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")})
    return jsonify({"total_bytes":total,"files":sorted(files,key=lambda x:x["date"],reverse=True)})

@app.route("/api/download_clip", methods=["POST"])
def api_download_clip():
    """Baixa um video do YouTube via yt-dlp e salva em uploads."""
    data = request.json
    url = data.get("url")
    title = data.get("title", f"youtube_clip_{int(time.time())}")
    if not url: return jsonify({"error": "No URL"}), 400
    try:
        import yt_dlp
        safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
        safe_title = safe_title.replace(' ', '_')[:30]
        output_file = os.path.join(UPLOAD_DIR, f"{safe_title}_{int(time.time())}.mp4")
        ydl_opts = {
            'format': 'best',
            'outtmpl': output_file,
            'noplaylist': True,
            'max_filesize': 500 * 1024 * 1024, # max 500mb
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        size_mb = os.path.getsize(output_file) / 1024 / 1024
        return jsonify({"success": True, "path": output_file, "name": os.path.basename(output_file), "size_mb": round(size_mb, 1)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    print("\n  StudioPilot Pro - Web Edition")
    print("  http://localhost:5051\n")
    app.run(host="0.0.0.0", port=5051, debug=False)

