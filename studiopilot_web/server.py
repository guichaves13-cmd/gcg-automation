"""StudioPilot Pro — Web Edition (Flask Server)
Port: 5051 (independent from TitlePilot on 5050)
"""
import os, sys, json, threading, shutil, time, hashlib, secrets, tempfile

# Fix Windows encoding crash (charmap codec can't encode unicode)
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
from datetime import datetime, timedelta
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

# ══ SESSION ANALYTICS TRACKER ══
import datetime as _dt
_ti_session_start = _dt.datetime.now()
_ti_call_counts = {}  # {tool_name: call_count}
app.secret_key = secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")

# ─── Global error handler (catches ALL route exceptions) ────────────────
import traceback
from core.auto_recovery import _suggest_fix

# Initialize Self-Healing System
try:
    from core.self_healing import (
        run_startup_check, get_error_summary, clear_error_counts,
        DependencyChecker, validate_video_file, safe_config, PipelineGuard
    )
    _startup_health = run_startup_check()
    print(f"  [SelfHealing] Startup: {'ALL OK' if _startup_health.get('all_ok') else 'WARNINGS'}")
except ImportError:
    print("  [SelfHealing] Module not found, running without self-healing")
    _startup_health = {"all_ok": True}
    def get_error_summary(): return {"total_errors": 0}
    def clear_error_counts(): pass
    def safe_config(c, k, d=None, **kw): return c.get(k, d)
    class DependencyChecker:
        @staticmethod
        def check_all(): return {"all_ok": True}

@app.errorhandler(Exception)
def handle_global_error(error):
    """Catches any unhandled exception in any route — returns JSON with fix suggestion."""
    tb = traceback.format_exc()
    err_str = str(error)
    print(f"[GLOBAL ERROR] {err_str}\n{tb[:500]}")
    
    # Log to self-healing system
    try:
        from core.self_healing import _log_error
        _log_error(type(error).__name__, "server_route", error)
    except Exception:
        pass
    
    return jsonify({
        "error": err_str,
        "suggestion": _suggest_fix(err_str),
        "traceback": tb[-1000:] if len(tb) > 1000 else tb,
    }), 500

@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "Route not found"}), 404

@app.errorhandler(405)
def handle_405(e):
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(415)
def handle_415(e):
    return jsonify({"error": "Unsupported media type. Send Content-Type: application/json"}), 415

@app.errorhandler(400)
def handle_400(e):
    return jsonify({"error": str(e) or "Bad request"}), 400

# ─── Health & Diagnostics API ────────────────────────────────────────────

@app.route("/api/system/health")
def api_system_health():
    """Full system health check — used by monitoring and tests."""
    try:
        deps = DependencyChecker.check_all()
        return jsonify({
            "status": "healthy" if deps.get("all_ok") else "degraded",
            "dependencies": deps,
            "uptime_seconds": int(time.time() - _server_start_time),
            "pipeline_running": pipeline_status.get("running", False),
        })
    except Exception as e:
        return jsonify({"status": "healthy", "note": str(e)})

@app.route("/api/system/errors")
def api_system_errors():
    """Error summary for dashboard."""
    return jsonify(get_error_summary())

@app.route("/api/system/errors/clear", methods=["POST"])
def api_system_errors_clear():
    clear_error_counts()
    return jsonify({"ok": True})

_server_start_time = time.time()

# ─── Run boot checks ────────────────────────────────────────────────────
from core.auto_recovery import run_boot_checks
run_boot_checks()

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
    for k in ["google_ai","youtube","pexels","pixabay","unsplash","nvidia"]:
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
    """Generate video script using GLM-5.1 Agent (NVIDIA) with AI Engine fallback."""
    from core.glm_agent import analyze_script
    from core.ai_engine import ask_ai
    from studiopilot_web.script_engine import build_prompt
    data = request.json or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "Topic is required"}), 400
    lang = data.get("language","English")
    duration = data.get("duration","5 min")
    tone = data.get("tone","Documentary serious")
    engine = data.get("engine", "glm-5.1")
    try:
        if engine == "glm-5.1":
            result_data, err = analyze_script(topic, lang, duration)
            if result_data:
                script = result_data.get("content", "")
                reasoning = result_data.get("reasoning", "")
                word_count = len(script.split()) if script else 0
                return jsonify({"script": script, "word_count": word_count, "reasoning": reasoning[:500], "agent": "GLM-5.1"})
        # Fallback to AI Engine
        prompt = build_prompt(topic, duration, tone, lang)
        result = ask_ai(prompt)
        if result.startswith("["):
            return jsonify({"error": result}), 503
        word_count = len(result.split()) if result else 0
        return jsonify({"script": result, "word_count": word_count, "agent": "AI Engine fallback"})
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

@app.route("/api/outputs")
def api_outputs():
    """Alias for /api/gallery — returns list of output files."""
    return api_gallery()

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

# NOTE: /api/system/health is defined above (near line 81) with self-healing integration.
# The old version was here but caused a Flask duplicate endpoint crash.
# Removed to prevent AssertionError on startup.

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
    resp = dict(pipeline_status)
    resp["status"] = "running" if pipeline_status.get("running") else "idle"
    return jsonify(resp)

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

    # ── Pre-flight check ────────────────────────────────────────────────
    from core.auto_recovery import run_preflight, check_avatar_file
    av_check = check_avatar_file(avatar_path)
    if not av_check["ok"]:
        active_pipelines -= 1
        return jsonify({
            "error": f"Avatar invalido: {av_check['error']}",
            "fix": "Envie um arquivo de video MP4 valido (nao corrompido) em Uploads.",
        }), 400

    preflight = run_preflight()
    critical_issues = [i for i in preflight["issues"] if i["severity"] == "critical"]
    if critical_issues:
        active_pipelines -= 1
        return jsonify({
            "error": f"Pre-flight falhou: {critical_issues[0]['message']}",
            "issues": [i["message"] for i in critical_issues],
            "fixes": preflight["fixes_applied"],
            "hint": "Use o Diagnostico IA (menu lateral) para resolver.",
        }), 400
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

    _start_time = time.time()
    def run():
        global pipeline_status, active_pipelines
        from core.auto_recovery import run_with_recovery, _log_recovery, _suggest_fix
        try:
            def _pipe_runner(cfg, on_progress=None):
                # Propagate recovery adjustments into 'data' sub-dict
                run_data = dict(cfg["data"])
                if cfg.get("auto_broll_count"):
                    run_data["broll_count"] = cfg["auto_broll_count"]
                if cfg.get("reduce_quality"):
                    run_data["reduce_quality"] = True
                _run_pipeline_task_impl(
                    run_data, cfg["avatar_path"], cfg["output_path"],
                    cfg["output_name"], cfg["start_time"], on_progress,
                )
            result = run_with_recovery(_pipe_runner, {
                "data": data,
                "avatar_path": avatar_path,
                "output_path": output_path,
                "output_name": output_name,
                "start_time": _start_time,
            }, on_progress=_progress_cb)
            if not result.get("success"):
                pipeline_status["error"] = result.get("error", "Pipeline failed")
                pipeline_status["message"] = f"Falhou: {result.get('hint', result.get('error', 'Erro desconhecido'))}"
                _log_recovery("pipeline", output_name, "failed", result.get("error", "unknown"))
        except Exception as e:
            pipeline_status["error"] = str(e)
            pipeline_status["message"] = f"Erro: {_suggest_fix(str(e))}"
        finally:
            pipeline_status["running"] = False
            active_pipelines = 0
            _pipeline_thread = None
    t = threading.Thread(target=run, daemon=False)
    _pipeline_thread = t
    t.start()
    # Start watchdog monitoring progress
    wd = threading.Thread(
        target=_watchdog,
        args=(t, pipeline_status, 15),
        daemon=True,
    )
    wd.start()
    return jsonify({"started": True, "output": output_path})


def _watchdog(thread, progress_ref, timeout_min=15):
    """Background watchdog: if progress doesn't change for N minutes, flag as stalled."""
    last_pct = progress_ref.get("progress", 0)
    last_time = time.time()
    while thread.is_alive():
        time.sleep(15)
        current_pct = progress_ref.get("progress", 0)
        if current_pct == last_pct:
            if time.time() - last_time > timeout_min * 60:
                print(f"[WATCHDOG] Pipeline stuck at {current_pct}% for {timeout_min}min!")
                progress_ref["error"] = (
                    f"Pipeline travado em {current_pct}% por mais de {timeout_min}min. "
                    "Use o botao Cancelar e tente novamente."
                )
                progress_ref["message"] = f"Travado em {current_pct}% — cancele e tente de novo"
                try:
                    import ctypes
                    tid = thread.ident
                    if tid:
                        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, ctypes.py_object(SystemExit))
                except:
                    pass
                return
        else:
            last_pct = current_pct
            last_time = time.time()

def _run_pipeline_task(data, avatar_path, output_path, output_name, start_time=None, on_progress=None):
    """Wrapper: unpacks kwargs for run_with_recovery compatibility."""
    return _run_pipeline_task_impl(data, avatar_path, output_path, output_name, start_time, on_progress)

def _run_pipeline_task_impl(data, avatar_path, output_path, output_name, start_time=None, on_progress=None):
    """
    Standalone pipeline runner — used by API, scheduler, queue worker, and agent mode.
    This is the single source of truth for executing a pipeline.
    Hardened with safe_config to prevent ANY config-related crash.
    """
    global pipeline_status
    from core.api_keys import load_api_key
    
    # Safe broll_count parsing (handles string, None, invalid values)
    try:
        broll_count = int(data.get("broll_count", 30))
        if broll_count < 1:
            broll_count = 10
        elif broll_count > 200:
            broll_count = 200
    except (ValueError, TypeError):
        broll_count = 30
    
    config = {
        "avatar_video": avatar_path,
        "output_file": output_path,
        "pipeline": data.get("pipeline", "avatar_auto"),
        "resolution": data.get("resolution", "1080p"),
        "fps": 30,
        "music_enabled": data.get("music", False),
        "subtitles_enabled": data.get("subtitles", True),
        "force_new_subtitles": True,
        "google_api_key": load_api_key("google_ai") or load_api_key("gemini") or "",
        "pexels_api_key": load_api_key("pexels") or "",
        "pixabay_api_key": load_api_key("pixabay") or "",
        "unsplash_api_key": load_api_key("unsplash") or "",
        "youtube_api_key": load_api_key("youtube") or "",
        # v10: B-roll Intelligence fallbacks (when Gemini is rate-limited)
        "groq_api_key": load_api_key("groq") or "",
        "nvidia_api_key": load_api_key("nvidia") or "",
        "youtube_channel_ids": data.get("youtube_channel_ids", load_api_key("youtube_channels") or ""),
        "auto_broll_count": broll_count,
        "stickers_enabled": data.get("stickers", True),
        "youtube_priority": data.get("youtube_priority", False),
        "reduce_quality": data.get("reduce_quality", False),
        # v10: opt-in (default True) for Intelligent B-roll with Vision pre-verification
        "use_intelligent_broll": data.get("use_intelligent_broll", True),
        "avatar": {"min_broll_duration": 4, "max_broll_duration": 9},
    }
    
    # Execute pipeline with protection
    try:
        if on_progress:
            from core.pipeline_avatar_auto import run_auto
            run_auto(config, on_progress=on_progress)
        else:
            def _silent_progress(c, t, m): pass
            from core.pipeline_avatar_auto import run_auto
            run_auto(config, on_progress=_silent_progress)
    except Exception as pipe_err:
        # Log error and re-raise for recovery system
        try:
            from core.self_healing import _log_error
            _log_error(type(pipe_err).__name__, "pipeline_execution", pipe_err)
        except Exception:
            pass
        raise
    
    pipeline_status["progress"] = 100
    pipeline_status["message"] = f"Done! {output_name}"
    
    # Stats tracking (protected)
    try:
        s = _load_stats()
        today = datetime.now().strftime("%Y-%m-%d")
        if s.get("last") != today: s["today"] = 0; s["last"] = today
        s["total"] = s.get("total", 0) + 1
        s["today"] = s.get("today", 0) + 1
        s.setdefault("history", []).insert(0, {"f": output_name, "d": datetime.now().strftime("%H:%M")})
        s["history"] = s["history"][:30]
        _save_stats(s)
    except Exception as stats_err:
        print(f"[stats] Error saving stats: {stats_err}")
    
    # Load quality report if available
    try:
        report_path = output_path.replace(".mp4", "_quality_report.json")
        if os.path.exists(report_path):
            with open(report_path) as f:
                report = json.load(f)
            pipeline_status["ai_stats"]["quality_score"] = report.get("quality_score")
    except Exception:
        pass
    
    try:
        from core.analytics import track_production
        elapsed = round(time.time() - (start_time or time.time()))
        out_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        track_production(data, elapsed, "completed", out_size)
    except Exception as e:
        print(f"[analytics] track_production error: {e}")


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

@app.route("/api/pipeline/rerender", methods=["POST"])
def api_pipeline_rerender():
    """
    Re-renderiza um video aplicando picker_decisions.json.

    Body JSON:
      {
        "timeline_path":   "/abs/path/output_beat_timeline.json",
        "decisions_path":  "/abs/path/picker_decisions.json",
        "avatar_path":     "/abs/path/avatar.mp4",
        "output_name":     "output_v2.mp4",          # opcional
        "subtitles_srt":   "/abs/path/subs.srt",     # opcional
        "decisions":       {"3":"approved","5":"rejected",...},  # inline (sem arquivo)
        "replacements":    {"7":"/path/file.mp4"}    # inline replacements
      }
    """
    global active_pipelines, pipeline_status, _pipeline_thread

    data = request.json or {}

    timeline_path = data.get("timeline_path", "").strip()
    decisions_path = data.get("decisions_path", "").strip()
    avatar_path = data.get("avatar_path", "").strip()
    inline_decisions = data.get("decisions")      # dict ou None
    inline_replacements = data.get("replacements", {})
    subtitles_srt = data.get("subtitles_srt", "").strip()
    # Lower thirds opcoes (do picker UI)
    lower_thirds_enabled = bool(data.get("lower_thirds_enabled", False))
    lower_thirds_style = str(data.get("lower_thirds_style", "modern")).strip()
    if lower_thirds_style not in ("modern", "minimal", "bold"):
        lower_thirds_style = "modern"

    # Validacoes (antes de checar pipeline ativo para retornar 400 em inputs invalidos)
    if not avatar_path:
        return jsonify({"error": "avatar_path e obrigatorio"}), 400

    if not os.path.isfile(avatar_path):
        return jsonify({"error": f"avatar_path invalido: {avatar_path}"}), 400

    if not timeline_path:
        return jsonify({"error": "timeline_path e obrigatorio"}), 400

    if not os.path.isfile(timeline_path):
        return jsonify({"error": f"timeline_path invalido: {timeline_path}"}), 400

    if not inline_decisions and (not decisions_path or not os.path.isfile(decisions_path)):
        return jsonify({"error": "Forneca decisions_path ou decisions inline"}), 400

    # Reset dead pipeline
    if _pipeline_thread is not None and not _pipeline_thread.is_alive():
        active_pipelines = 0
        pipeline_status["running"] = False

    if active_pipelines >= 1:
        return jsonify({"error": "Pipeline ja em execucao. Cancele antes de re-renderizar."}), 409

    # Aceita decisions inline (dict no body) OU decisions_path (arquivo JSON)
    if inline_decisions and isinstance(inline_decisions, dict):
        # Salva temporariamente para o auditor
        import tempfile as _tmp
        tf = _tmp.NamedTemporaryFile(mode="w", suffix=".json",
                                     delete=False, encoding="utf-8")
        json.dump({"decisions": inline_decisions, "replacements": inline_replacements}, tf)
        tf.close()
        decisions_path = tf.name

    # Output
    output_name = data.get("output_name") or \
        f"rerender_{os.path.splitext(os.path.basename(timeline_path))[0]}_{int(time.time())}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_name)

    from core.api_keys import load_api_key
    width, height = (3840, 2160) if data.get("resolution") == "4k" else (1920, 1080)

    # Configurar pipeline_status
    active_pipelines += 1
    pipeline_status.update({
        "running": True, "progress": 0,
        "message": "Auditor: carregando decisoes...",
        "error": "", "phase": "Auditor", "phase_idx": 0,
        "logs": [], "start_time": time.time(), "elapsed": 0,
        "ai_stats": {"clips_downloaded": 0, "clips_validated": 0, "clips_rejected": 0,
                     "clips_fixed": 0, "segments_total": 0, "segments_done": 0,
                     "bad_segments": 0, "quality_score": None, "theme": "", "language": ""},
    })

    def _progress_cb(current, total, msg):
        pct = int((current / max(total, 1)) * 100)
        pipeline_status["progress"] = pct
        pipeline_status["message"] = msg
        pipeline_status["elapsed"] = round(time.time() - pipeline_status["start_time"], 1)
        _push_log(msg, "info")

    def run():
        global pipeline_status, active_pipelines
        try:
            from core.broll_auditor import run_auditor
            result = run_auditor(
                timeline_path=timeline_path,
                decisions_path=decisions_path,
                avatar_path=avatar_path,
                output_path=output_path,
                pexels_key=load_api_key("pexels") or "",
                pixabay_key=load_api_key("pixabay") or "",
                unsplash_key=load_api_key("unsplash") or "",
                width=width, height=height, fps=30,
                subtitles_srt=subtitles_srt,
                on_progress=_progress_cb,
                lower_thirds_enabled=lower_thirds_enabled,
                lower_thirds_style=lower_thirds_style,
            )
            if result.get("ok"):
                pipeline_status["message"] = f"Re-render concluido: {output_name}"
                pipeline_status["progress"] = 100
                _push_log(f"Re-render OK: {output_name}", "phase")
                _push_log(f"Stats: {result.get('stats', {})}", "info")
            else:
                pipeline_status["error"] = result.get("error", "Falhou")
                pipeline_status["message"] = f"Re-render falhou: {result.get('error', '')}"
        except Exception as e:
            pipeline_status["error"] = str(e)
            pipeline_status["message"] = f"Erro no re-render: {e}"
        finally:
            pipeline_status["running"] = False
            active_pipelines = 0
            _pipeline_thread = None
            # Limpa arquivo temporario de decisions se criado inline
            if inline_decisions and os.path.isfile(decisions_path):
                try:
                    os.remove(decisions_path)
                except Exception:
                    pass

    t = threading.Thread(target=run, daemon=False)
    _pipeline_thread = t
    t.start()
    return jsonify({"started": True, "output": output_path, "output_name": output_name})


@app.route("/api/pipeline/auditor/analyze", methods=["POST"])
def api_auditor_analyze():
    """
    Pre-visualiza o impacto das decisoes SEM re-renderizar.

    Body: {"timeline_path": "...", "decisions_path": "..."} ou inline decisions.
    """
    data = request.json or {}
    timeline_path = data.get("timeline_path", "").strip()
    decisions_path = data.get("decisions_path", "").strip()
    inline_decisions = data.get("decisions")
    inline_replacements = data.get("replacements", {})

    if not timeline_path or not os.path.isfile(timeline_path):
        return jsonify({"error": f"timeline_path invalido: {timeline_path}"}), 400

    if inline_decisions and isinstance(inline_decisions, dict):
        decisions = {str(k): v for k, v in inline_decisions.items()}
        replacements = {str(k): v for k, v in inline_replacements.items()}
    elif decisions_path and os.path.isfile(decisions_path):
        from core.broll_auditor import load_decisions
        decisions, replacements = load_decisions(decisions_path)
    else:
        return jsonify({"error": "Forneca decisions_path ou decisions inline"}), 400

    try:
        from core.broll_auditor import load_timeline, analyze_decisions
        timeline = load_timeline(timeline_path)
        analysis = analyze_decisions(timeline, decisions, replacements)
        return jsonify({
            "ok": True,
            "total_broll": analysis["total_broll"],
            "approved": len(analysis["approved"]),
            "rejected": len(analysis["rejected"]),
            "replace_manual": len(analysis["replace_manual"]),
            "replace_auto": len(analysis["replace_auto"]),
            "unchanged": len(analysis["unchanged"]),
            "invalid_files": len(analysis["invalid_files"]),
            "beat_ids": {
                "approved": analysis["approved"],
                "rejected": analysis["rejected"],
                "replace_manual": list(analysis["replace_manual"].keys()),
                "replace_auto": analysis["replace_auto"],
                "unchanged": analysis["unchanged"],
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pipeline/auditor/load_timeline", methods=["POST"])
def api_auditor_load_timeline():
    """Retorna o beat_timeline.json de um output existente."""
    data = request.json or {}
    path = data.get("timeline_path", "").strip()
    if not path or not os.path.isfile(path):
        # Tenta inferir pelo output_name
        output_name = data.get("output_name", "").strip()
        if output_name:
            inferred = os.path.join(OUTPUT_DIR, output_name.replace(".mp4", "_beat_timeline.json"))
            if os.path.isfile(inferred):
                path = inferred
    if not path or not os.path.isfile(path):
        return jsonify({"error": "timeline_path nao encontrado"}), 404
    try:
        from core.beat_timeline import load_beat_timeline
        tl = load_beat_timeline(path)
        return jsonify({"ok": True, "timeline": tl, "path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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

@app.route("/api/system/recovery-log")
def api_recovery_log():
    from core.auto_recovery import get_recovery_log
    return jsonify({"log": get_recovery_log(limit=100)})

@app.route("/api/pipeline/diagnose", methods=["GET"])
def api_pipeline_diagnose():
    """Full system diagnostic — uses auto_recovery engine."""
    from core.auto_recovery import run_preflight, get_recovery_log, _suggest_fix, check_api_key
    preflight = run_preflight()

    # Test Gemini connectivity
    gemini_ok = False
    try:
        from core.api_keys import load_api_key
        gk = load_api_key("google_ai")
        if gk:
            from google import genai
            c = genai.Client(api_key=gk)
            rc = []
            def _test():
                r = c.models.generate_content(model="gemini-2.0-flash", contents="say ok")
                rc.append(r)
            t = threading.Thread(target=_test, daemon=True)
            t.start()
            t.join(timeout=15)
            gemini_ok = bool(rc and rc[0].text)
    except:
        pass

    # Test GLM
    glm_ok = False
    try:
        from core.api_keys import load_api_key
        nk = load_api_key("nvidia")
        if nk:
            from openai import OpenAI
            o = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=nk, timeout=10)
            o.chat.completions.create(model="z-ai/glm-5.1", messages=[{"role":"user","content":"say ok"}], max_tokens=10, timeout=10)
            glm_ok = True
    except:
        pass

    avatar_files = [f for f in os.listdir(UPLOAD_DIR) if f.endswith(('.mp4', '.mov', '.avi', '.mkv'))] if os.path.isdir(UPLOAD_DIR) else []

    status = "healthy" if len(preflight["issues"]) == 0 else ("issues" if len([i for i in preflight["issues"] if i["severity"]=="critical"]) <= 2 else "critical")

    return jsonify({
        "status": status,
        "python_version": sys.version.split()[0],
        "project_dir": PROJECT_DIR,
        "issues": [i["message"] for i in preflight["issues"]],
        "fixes": preflight["fixes_applied"],
        "warnings": preflight["warnings"],
        "checks": {
            "ffmpeg": preflight["tools"].get("ffmpeg", {}).get("ok", False),
            "ffprobe": preflight["tools"].get("ffprobe", {}).get("ok", False),
            "whisper": preflight["whisper"]["ok"],
            "google_ai": check_api_key("google_ai")["ok"],
            "gemini_api": gemini_ok,
            "pexels": check_api_key("pexels")["ok"],
            "pixabay": check_api_key("pixabay")["ok"],
            "nvidia": check_api_key("nvidia")["ok"],
            "glm_api": glm_ok,
            "disk_free_gb": preflight["disk"].get("free_gb", "unknown"),
            "avatar_uploads": len(avatar_files),
            "output_dir": r"C:\Users\Guilherme\Downloads\videos ferramenta",
        },
    })

# ─── Timeline state (Agent 3) ──────────────────────────────────────
_timeline_state = {"clips": []}
TIMELINE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "timeline_state.json")

def _save_timeline():
    try:
        with open(TIMELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(_timeline_state, f, ensure_ascii=False)
    except Exception as e:
        print(f"[Timeline] Save error: {e}")

def _load_timeline():
    global _timeline_state
    try:
        if os.path.exists(TIMELINE_FILE):
            with open(TIMELINE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("clips"), list):
                _timeline_state = data
            else:
                _timeline_state = {"clips": []}
    except (json.JSONDecodeError, IOError, Exception):
        _timeline_state = {"clips": []}

_load_timeline()

@app.route("/api/broll/preview", methods=["POST"])
def api_broll_preview():
    """Fetch B-roll previews (thumbnails) for a given topic — Agent 3."""
    data = request.json
    if not data:
        return jsonify({"error": "Invalid JSON body", "clips": [], "total": 0}), 400
    topic = data.get("topic", "").strip()
    try:
        count = max(1, min(int(data.get("count", 12)), 50))
    except (TypeError, ValueError):
        count = 12
    shot_types = data.get("shot_types", [])
    if not isinstance(shot_types, list):
        shot_types = []

    if not topic:
        return jsonify({"error": "Topic is required", "clips": [], "total": 0}), 400

    from core.api_keys import load_api_key
    clips = []
    seen_urls = set()

    def add_clips(new_clips, source):
        if not new_clips:
            return
        for c in new_clips:
            if not isinstance(c, dict):
                continue
            url = c.get("url") or c.get("preview", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                c["source"] = source
                clips.append(c)

    # 1. Try YouTube first
    try:
        yt_key = load_api_key("youtube")
        if yt_key:
            from core.youtube_api import search_trending
            week_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
            vids = search_trending(topic, yt_key, max_results=count, published_after=week_ago)
            if vids:
                yt_clips = []
                for i, v in enumerate(vids):
                    if not isinstance(v, dict):
                        continue
                    st = shot_types[i % len(shot_types)] if shot_types else "wide"
                    yt_clips.append({
                        "thumbnail": v.get("thumbnail", ""),
                        "url": v.get("url", ""),
                        "label": str(v.get("title", topic))[:60],
                        "duration": v.get("duration", 10),
                        "resolution": "HD",
                        "shot_type": st
                    })
                add_clips(yt_clips, "youtube")
    except Exception as e:
        print(f"[Broll Preview] YouTube error: {e}")

    # 2. Try Pexels
    if len(clips) < count:
        try:
            pexels_key = load_api_key("pexels")
            if pexels_key:
                from core.pexels_stock import search_videos
                vids = search_videos(pexels_key, topic, count=count - len(clips))
                if vids:
                    pex_clips = []
                    for v in vids:
                        if not isinstance(v, dict):
                            continue
                        pex_clips.append({
                            "thumbnail": v.get("preview", v.get("url", "")),
                            "url": v.get("url", ""),
                            "label": str(v.get("title", topic))[:60],
                            "duration": v.get("duration", 8),
                            "resolution": str(v.get("quality", "HD")),
                        })
                    add_clips(pex_clips, "pexels")
        except Exception as e:
            print(f"[Broll Preview] Pexels error: {e}")

    # 3. Try Pixabay
    if len(clips) < count:
        try:
            pix_key = load_api_key("pixabay")
            if pix_key:
                from core.pixabay_stock import search_videos
                vids = search_videos(pix_key, topic, count=count - len(clips))
                if vids:
                    pab_clips = []
                    for v in vids:
                        if not isinstance(v, dict):
                            continue
                        pab_clips.append({
                            "thumbnail": v.get("preview", v.get("url", "")),
                            "url": v.get("url", ""),
                            "label": str(v.get("title", topic))[:60],
                            "duration": v.get("duration", 8),
                            "resolution": str(v.get("quality", "HD")),
                        })
                    add_clips(pab_clips, "pixabay")
        except Exception as e:
            print(f"[Broll Preview] Pixabay error: {e}")

    # 4. Try Mixkit (free, no key)
    if len(clips) < count:
        try:
            from core.mixkit_stock import search_videos
            vids = search_videos(topic, count=count - len(clips))
            if vids:
                mix_clips = []
                for v in vids:
                    if not isinstance(v, dict):
                        continue
                    mix_clips.append({
                        "thumbnail": v.get("preview", v.get("url", "")),
                        "url": v.get("url", ""),
                        "label": str(v.get("title", topic))[:60],
                        "duration": v.get("duration", 8),
                        "resolution": str(v.get("quality", "4K")),
                    })
                add_clips(mix_clips, "mixkit")
        except Exception as e:
            print(f"[Broll Preview] Mixkit error: {e}")

    # Trim to requested count
    clips = clips[:count]

    # Assign shot types if selected
    if shot_types and clips:
        for i, c in enumerate(clips):
            c["shot_type"] = shot_types[i % len(shot_types)]

    return jsonify({
        "clips": clips,
        "total": len(clips),
        "topic": topic
    })


@app.route("/api/timeline/status")
def api_timeline_status():
    """Return current timeline state."""
    global _timeline_state
    if not isinstance(_timeline_state, dict) or not isinstance(_timeline_state.get("clips"), list):
        _timeline_state = {"clips": []}
    return jsonify(_timeline_state)


@app.route("/api/timeline/save", methods=["POST"])
def api_timeline_save():
    """Save timeline clip order."""
    global _timeline_state
    data = request.json
    if not data:
        return jsonify({"ok": False, "error": "Invalid JSON body"}), 400
    raw_clips = data.get("clips", [])
    if not isinstance(raw_clips, list):
        return jsonify({"ok": False, "error": "clips must be a list"}), 400
    # Validate and sanitize each clip
    clips = []
    max_clips = 200
    for c in raw_clips[:max_clips]:
        if not isinstance(c, dict):
            continue
        safe = {}
        for key in ("thumbnail", "url", "label", "source", "shot_type", "resolution"):
            val = c.get(key)
            if val is not None:
                safe[key] = str(val)[:500]
        dur = c.get("duration")
        if dur is not None:
            try:
                safe["duration"] = float(dur)
            except (TypeError, ValueError):
                safe["duration"] = 0
        clips.append(safe)
    _timeline_state["clips"] = clips
    _save_timeline()
    return jsonify({"ok": True, "count": len(clips)})


@app.route("/api/research", methods=["POST"])
def api_research():
    """Search stock footage from multiple sources."""
    from core.api_keys import load_api_key
    data = request.json or {}
    query = (data.get("query") or data.get("topic") or "").strip()
    if not query:
        return jsonify({"error": "Query is required"}), 400
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
        month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        vids = search_trending(query, key, max_results=20, published_after=month_ago, region=region)
        return jsonify({"results": vids, "total": len(vids)})
    except Exception as e:
        err = str(e)
        if "API key" in err or "keyInvalid" in err or "400 Client" in err or "403" in err:
            return jsonify({"error": "YouTube API key inválida."}), 400
        return jsonify({"error": err}), 500

@app.route("/api/veo3/generate", methods=["POST"])
def api_veo3_generate():
    """Parse VEO3 prompts and generate clips."""
    data = request.json
    prompts_text = data.get("prompts","")
    prompts = [p.strip() for p in prompts_text.split("\n") if p.strip()]
    return jsonify({"prompts": prompts, "count": len(prompts), "status": "ready"})


# ═══════════════════════════════════════════════════════════
#  VEO FLOW CONNECTION — Cookie-based direct API
# ═══════════════════════════════════════════════════════════
import json as _json

_VEO_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "core", "veo_accounts.json")

def _load_veo_config():
    try:
        with open(_VEO_CONFIG_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {"veo1": {}, "veo2": {}}

def _save_veo_config(cfg):
    os.makedirs(os.path.dirname(_VEO_CONFIG_PATH), exist_ok=True)
    with open(_VEO_CONFIG_PATH, "w", encoding="utf-8") as f:
        _json.dump(cfg, f, indent=2)

@app.route("/api/veo/connect", methods=["POST"])
def api_veo_connect():
    """Save Flow cookies and project ID for a VEO account."""
    data = request.json
    account = f"veo{data.get('account', 1)}"
    cookies = data.get("cookies", "")
    project_id = data.get("project_id", "").strip()
    
    if not cookies or not project_id:
        return jsonify({"error": "Cookies and Project ID are required"}), 400
    
    # Parse cookies (accept JSON array or string)
    try:
        if cookies.strip().startswith("["):
            cookie_list = _json.loads(cookies)
        else:
            cookie_list = [{"name": c.split("=")[0].strip(), "value": c.split("=",1)[1].strip()} 
                          for c in cookies.split(";") if "=" in c]
    except Exception as e:
        return jsonify({"error": f"Invalid cookie format: {e}"}), 400
    
    cfg = _load_veo_config()
    cfg[account] = {
        "cookies": cookie_list,
        "project_id": project_id,
        "connected": True,
        "connected_at": __import__("datetime").datetime.now().isoformat()
    }
    _save_veo_config(cfg)
    
    return jsonify({
        "status": "connected",
        "account": account,
        "cookie_count": len(cookie_list),
        "project_id": project_id
    })

@app.route("/api/veo/send", methods=["POST"])
def api_veo_send():
    """Send prompts to Google Flow via stored cookies."""
    data = request.json
    account = f"veo{data.get('account', 1)}"
    prompts = data.get("prompts", [])
    
    if not prompts:
        return jsonify({"error": "No prompts to send"}), 400
    
    cfg = _load_veo_config()
    acct = cfg.get(account, {})
    
    if not acct.get("connected"):
        return jsonify({"error": f"{account.upper()} not connected. Add cookies first."}), 400
    
    project_id = acct.get("project_id", "")
    cookies = acct.get("cookies", [])
    
    # Build cookie string for requests
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies if 'name' in c and 'value' in c)
    
    results = []
    import requests as _req
    
    for i, prompt in enumerate(prompts[:50]):  # Max 50 prompts per batch
        try:
            # Google Flow API endpoint for video generation
            flow_url = f"https://labs.google.com/fx/api/flow/project/{project_id}/generate"
            headers = {
                "Cookie": cookie_str,
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Origin": "https://labs.google.com",
                "Referer": f"https://labs.google.com/fx/pt/tools/flow/project/{project_id}"
            }
            payload = {
                "prompt": prompt,
                "type": "video",
                "settings": {"aspect_ratio": "16:9", "duration": "8s"}
            }
            
            r = _req.post(flow_url, json=payload, headers=headers, timeout=30)
            results.append({
                "prompt": prompt[:60] + "..." if len(prompt) > 60 else prompt,
                "status": "sent" if r.status_code < 400 else "failed",
                "code": r.status_code,
                "index": i + 1
            })
        except Exception as e:
            results.append({
                "prompt": prompt[:60] + "...",
                "status": "error",
                "error": str(e),
                "index": i + 1
            })
    
    sent = sum(1 for r in results if r["status"] == "sent")
    return jsonify({
        "account": account,
        "total": len(results),
        "sent": sent,
        "failed": len(results) - sent,
        "results": results
    })

@app.route("/api/veo/status")
def api_veo_status():
    """Get connection status for both VEO accounts."""
    cfg = _load_veo_config()
    return jsonify({
        "veo1": {
            "connected": cfg.get("veo1", {}).get("connected", False),
            "project_id": cfg.get("veo1", {}).get("project_id", ""),
            "connected_at": cfg.get("veo1", {}).get("connected_at", "")
        },
        "veo2": {
            "connected": cfg.get("veo2", {}).get("connected", False),
            "project_id": cfg.get("veo2", {}).get("project_id", ""),
            "connected_at": cfg.get("veo2", {}).get("connected_at", "")
        }
    })


@app.route("/api/veo/launch", methods=["POST"])
def api_veo_launch():
    """Launch Flow Automator as independent background process."""
    import subprocess as _sp
    data = request.json
    prompts = data.get("prompts", [])
    account = data.get("account", 1)
    model = data.get("model", "veo31lite")
    if model not in ("veo31lite", "veo31", "nanobanana", "geminiomni"):
        model = "veo31lite"
    multiplier = data.get("multiplier", 1)
    
    if not prompts:
        return jsonify({"error": "No prompts provided"}), 400
    
    # Save prompts to file
    prompts_file = os.path.join(os.path.dirname(__file__), f"_prompts_veo{account}.txt")
    try:
        with open(prompts_file, "w", encoding="utf-8") as f:
            f.write("\n".join(prompts))
    except Exception as e:
        return jsonify({"error": f"Failed to write prompts file: {e}"}), 500
    
    # Launch automator as independent process (survives server restart)
    automator_path = os.path.join(os.path.dirname(__file__), "flow_automator.py")
    cmd = ["python", automator_path, "--file", prompts_file, "--account", str(account), "--model", model, "--mult", str(multiplier)]
    
    try:
        proc = _sp.Popen(
            cmd,
            cwd=os.path.dirname(__file__),
            creationflags=_sp.CREATE_NEW_PROCESS_GROUP | _sp.DETACHED_PROCESS,
            stdout=open(os.path.join(os.path.dirname(__file__), f"flow_log_veo{account}.txt"), "w"),
            stderr=_sp.STDOUT,
        )
    except Exception as e:
        return jsonify({"error": f"Failed to launch automator: {e}"}), 500
    
    return jsonify({
        "status": "launched",
        "pid": proc.pid,
        "prompts_count": len(prompts),
        "account": account,
        "model": model
    })


@app.route("/api/veo/automation_status")
def api_veo_automation_status():
    """Get current automation status."""
    status_file = os.path.join(os.path.dirname(__file__), "flow_status.json")
    try:
        with open(status_file, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except:
        return jsonify({"running": False, "message": "No automation running"})


@app.route("/api/veo/control", methods=["POST"])
def api_veo_control():
    """Pause/Cancel/Resume the automation."""
    data = request.json
    action = data.get("action", "run")
    control_file = os.path.join(os.path.dirname(__file__), "flow_control.json")
    
    if action in ("pause", "cancel", "resume", "run"):
        if action == "resume":
            action = "run"
        with open(control_file, "w") as f:
            json.dump({"action": action}, f)
        return jsonify({"ok": True, "action": action})
    return jsonify({"error": "Invalid action"}), 400


@app.route("/api/veo3/auto_topic", methods=["POST"])
def api_veo3_auto_topic():
    """Generate VEO3 prompts from a topic/theme — no avatar required."""
    data = request.json
    topic = data.get("topic", "").strip()
    count = min(50, max(5, int(data.get("count", 30))))
    
    if not topic:
        return jsonify({"error": "Topic is required"}), 400
    
    prompt = f"""You are a cinematic video prompt engineer for VEO3 AI video generation.

TOPIC: {topic}
GENERATE: {count} prompts

Each prompt must be a SINGLE detailed shot description for an 8-second cinematic clip.
Format: ONE prompt per line, no numbering, no bullets.

Rules:
- Each prompt = 1 complete visual scene (camera angle + subject + action + lighting + mood)
- Use cinematic language: "wide shot", "close-up", "aerial view", "tracking shot", "dolly zoom"
- Include lighting: "golden hour", "dramatic rim lighting", "bioluminescent glow", "volumetric fog"
- Include mood: "mysterious", "epic", "serene", "terrifying", "awe-inspiring"
- Add style: "4K ultra-realistic", "documentary style", "photorealistic", "cinematic color grading"
- Vary camera angles throughout the sequence
- Create a natural flow from establishing shots to details to dramatic close-ups
- NO dialogue, NO text overlays, NO music descriptions — ONLY visuals
- Each prompt should be 15-30 words

Generate {count} prompts, one per line."""

    try:
        from core.ai_engine import ask_ai
        result = ask_ai(prompt, use_cache=False)
        if result.startswith("["):
            return jsonify({"error": "AI temporarily unavailable. Try again in a moment."}), 503
    except Exception as e:
        err = str(e)
        if "unavailable" in err.lower() or "exhausted" in err.lower() or "rate" in err.lower():
            return jsonify({"error": "AI temporarily unavailable. Try again in a moment."}), 503
        return jsonify({"error": err}), 500
    
    prompts = [l.strip().lstrip("0123456789.-) ") for l in result.strip().split("\n") if l.strip() and len(l.strip()) > 10]
    
    return jsonify({
        "prompts": prompts,
        "count": len(prompts),
        "topic": topic
    })

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


# ═══════════════════════════════════════════════════════════
#  THUMBNAIL — AI Thumbnail Generator
# ═══════════════════════════════════════════════════════════

@app.route("/api/thumbnail/generate", methods=["POST"])
def api_thumbnail_generate():
    from core.thumbnail_generator import generate_thumbnail, generate_thumbnail_ai
    data = request.json
    video_path = data.get("video_path", "")
    topic = data.get("topic", data.get("video_path", ""))
    
    if video_path and os.path.exists(video_path):
        result = generate_thumbnail(video_path, topic)
        if result:
            return jsonify({"ok": True, "main": result.get("main"), "variants": result.get("variants", []), "analysis": result.get("analysis", {})})
        return jsonify({"error": "Falha ao gerar thumbnail"}), 500
    
    concepts = generate_thumbnail_ai(topic or "video")
    return jsonify({"ok": True, "concepts": concepts})

@app.route("/api/thumbnail/concepts", methods=["POST"])
def api_thumbnail_concepts():
    from core.thumbnail_generator import generate_thumbnail_ai
    data = request.json or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "Topic is required"}), 400
    count = min(5, int(data.get("count", 3)))
    concepts = generate_thumbnail_ai(topic, count)
    return jsonify({"concepts": concepts})

# ═══════════════════════════════════════════════════════════
#  SCHEDULER — Production Scheduler
# ═══════════════════════════════════════════════════════════

from core import scheduler as sched_mod
sched_mod.start()

@app.route("/api/schedule", methods=["GET"])
def api_schedule_get():
    return jsonify({"schedules": sched_mod.get_schedules()})

@app.route("/api/schedule", methods=["POST"])
def api_schedule_add():
    data = request.json
    item = sched_mod.add_schedule(
        title=data.get("title", "Untitled"),
        cron_expr=data.get("cron", "daily 08:00"),
        config=data.get("config", {}),
        active=data.get("active", True),
    )
    item_id = item.get("id") if isinstance(item, dict) else None
    return jsonify({"ok": True, "item": item, "id": item_id})

@app.route("/api/schedule/<int:sid>", methods=["DELETE"])
def api_schedule_delete(sid):
    sched_mod.remove_schedule(sid)
    return jsonify({"ok": True})

@app.route("/api/schedule/<int:sid>", methods=["PATCH"])
def api_schedule_update(sid):
    sched_mod.update_schedule(sid, request.json)
    return jsonify({"ok": True})

@app.route("/api/schedule/logs")
def api_schedule_logs():
    return jsonify({"logs": sched_mod.get_logs()})

@app.route("/api/schedule/status")
def api_schedule_status():
    return jsonify(sched_mod.status())

# ═══════════════════════════════════════════════════════════
#  ANALYTICS — Production Analytics Dashboard
# ═══════════════════════════════════════════════════════════

@app.route("/api/analytics/overview")
def api_analytics_overview():
    from core.analytics import get_overview
    return jsonify(get_overview())

@app.route("/api/analytics/detailed")
def api_analytics_detailed():
    from core.analytics import get_detailed_stats
    days = int(request.args.get("days", 30))
    return jsonify(get_detailed_stats(days))

@app.route("/api/analytics/clear", methods=["POST"])
def api_analytics_clear():
    from core.analytics import clear_analytics
    clear_analytics()
    return jsonify({"ok": True})

@app.route("/api/analytics/track", methods=["POST"])
def api_analytics_track():
    from core.analytics import track_event
    data = request.json
    track_event(data.get("category", "general"), data.get("action", ""), data.get("label", ""))
    return jsonify({"ok": True})

# ═══════════════════════════════════════════════════════════
#  TEMPLATES — Production Templates
# ═══════════════════════════════════════════════════════════

@app.route("/api/templates", methods=["GET"])
def api_templates_list():
    from core.templates_manager import list_templates, get_template_categories
    category = request.args.get("category")
    return jsonify({"templates": list_templates(category), "categories": get_template_categories()})

@app.route("/api/templates", methods=["POST"])
def api_templates_save():
    from core.templates_manager import save_template
    data = request.json
    t = save_template(
        name=data.get("name", "Template"),
        config=data.get("config", {}),
        category=data.get("category", "general"),
        description=data.get("description", ""),
    )
    return jsonify({"ok": True, "template": t})

@app.route("/api/templates/<int:tid>", methods=["GET"])
def api_templates_get(tid):
    from core.templates_manager import get_template
    t = get_template(tid)
    if not t:
        return jsonify({"error": "Not found"}), 404
    return jsonify(t)

@app.route("/api/templates/<int:tid>", methods=["DELETE"])
def api_templates_delete(tid):
    from core.templates_manager import delete_template
    delete_template(tid)
    return jsonify({"ok": True})

@app.route("/api/templates/<int:tid>", methods=["PATCH"])
def api_templates_update(tid):
    from core.templates_manager import update_template
    update_template(tid, request.json)
    return jsonify({"ok": True})

@app.route("/api/templates/apply", methods=["POST"])
def api_templates_apply():
    from core.templates_manager import apply_template
    data = request.json
    config = apply_template(data.get("tid"), data.get("overrides"))
    if not config:
        return jsonify({"error": "Template not found"}), 404
    return jsonify({"config": config})

@app.route("/api/templates/categories")
def api_templates_categories():
    from core.templates_manager import get_template_categories
    return jsonify(get_template_categories())

# ═══════════════════════════════════════════════════════════
#  CHANNELS — YouTube Channel Management (My Channels)
# ═══════════════════════════════════════════════════════════

_CHANNELS_FILE = os.path.join(PROJECT_DIR, "my_channels.json")

def _load_channels():
    try:
        with open(_CHANNELS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def _save_channels(channels):
    with open(_CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, indent=2)

@app.route("/api/channels", methods=["GET"])
def api_channels_get():
    channels = _load_channels()
    return jsonify({"channels": channels})

@app.route("/api/channels/add", methods=["POST"])
def api_channels_add():
    from core.api_keys import load_api_key, save_api_key
    data = request.json
    channel_input = data.get("channel", "").strip()
    if not channel_input:
        return jsonify({"error": "Channel ID or URL required"}), 400

    import re
    channel_id = channel_input
    url_match = re.search(r'(?:youtube\.com/(?:@|channel/|c/))([\w-]+)', channel_input)
    if url_match:
        channel_id = url_match.group(1)
    if not channel_id.startswith("UC") and not channel_id.startswith("@"):
        if channel_input.startswith("UC"):
            channel_id = channel_input

    channels = _load_channels()
    for c in channels:
        if c.get("id") == channel_id or c.get("input") == channel_input:
            return jsonify({"error": "Channel already exists"}), 409

    channel_entry = {
        "id": channel_id,
        "input": channel_input,
        "added_at": datetime.now().isoformat(),
    }

    yt_key = load_api_key("youtube")
    if yt_key:
        try:
            from core.youtube_api import get_channel_info
            info = get_channel_info(channel_id, yt_key)
            if info:
                channel_entry["title"] = info.get("title", "")
                channel_entry["subscribers"] = info.get("subscribers", 0)
                channel_entry["video_count"] = info.get("video_count", 0)
                channel_entry["thumbnail"] = info.get("thumbnail", "")
        except Exception:
            pass

    channels.append(channel_entry)
    _save_channels(channels)

    existing_ids = load_api_key("youtube_channels")
    ids_list = [c.strip() for c in existing_ids.split(",") if c.strip()] if existing_ids else []
    if channel_id not in ids_list:
        ids_list.append(channel_id)
        save_api_key("youtube_channels", ",".join(ids_list))

    return jsonify({"ok": True, "channel": channel_entry})

@app.route("/api/channels/remove", methods=["POST"])
def api_channels_remove():
    from core.api_keys import load_api_key, save_api_key
    data = request.json
    channel_id = data.get("id", "")
    channels = _load_channels()
    channels = [c for c in channels if c.get("id") != channel_id]
    _save_channels(channels)

    existing_ids = load_api_key("youtube_channels")
    ids_list = [c.strip() for c in existing_ids.split(",") if c.strip()] if existing_ids else []
    if channel_id in ids_list:
        ids_list.remove(channel_id)
        save_api_key("youtube_channels", ",".join(ids_list))

    return jsonify({"ok": True})

@app.route("/api/channels/scan", methods=["POST"])
def api_channels_scan():
    from core.api_keys import load_api_key
    data = request.json
    channel_id = (data.get("id") or data.get("channel_id") or "").strip()
    if not channel_id:
        return jsonify({"error": "Channel ID is required"}), 400
    yt_key = load_api_key("youtube")
    if not yt_key:
        return jsonify({"error": "YouTube API key not configured"}), 400

    try:
        from core.youtube_api import get_channel_videos
        from datetime import timedelta
        month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        videos = get_channel_videos(channel_id, yt_key, max_videos=15)
        return jsonify({"videos": videos, "total": len(videos)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════
#  QUEUE MANAGER — Batch Video Processing
# ═══════════════════════════════════════════════════════════

from core.queue_manager import add_job, list_jobs, get_job, cancel_job as qcancel
from core.queue_manager import remove_job as qremove, queue_stats, retry_failed, clear_completed, next_pending, mark_done

# Background queue worker — auto-consumes queued jobs
_queue_worker_running = False
_queue_worker_thread = None

def _start_queue_worker():
    global _queue_worker_running, _queue_worker_thread
    if _queue_worker_running:
        return
    _queue_worker_running = True
    def _loop():
        while _queue_worker_running:
            try:
                job = next_pending()
                if job:
                    cfg = job.get("config", {})
                    av_path = cfg.get("avatar_path", "")
                    out_name = f"queue_{job['id'][:8]}_{int(time.time())}.mp4"
                    out_dir = r"C:\Users\Guilherme\Downloads\videos ferramenta"
                    os.makedirs(out_dir, exist_ok=True)
                    out_path = os.path.join(out_dir, out_name)
                    try:
                        _run_pipeline_task(cfg, av_path, out_path, out_name)
                        mark_done(job["id"], output_path=out_path)
                    except Exception as e:
                        mark_done(job["id"], error=str(e))
            except Exception:
                pass
            time.sleep(5)
    _queue_worker_thread = threading.Thread(target=_loop, daemon=True)
    _queue_worker_thread.start()

_start_queue_worker()

@app.route("/api/queue/add", methods=["POST"])
def api_queue_add():
    data = request.json or {}
    config = data.get("config", {})
    priority = int(data.get("priority", 0))
    job = add_job(config, priority)
    return jsonify({"ok": True, "job": job})

@app.route("/api/queue/list")
def api_queue_list():
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))
    jobs = list_jobs(status, limit)
    return jsonify({"jobs": jobs, "total": len(jobs)})

@app.route("/api/queue/<job_id>")
def api_queue_get(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)

@app.route("/api/queue/cancel/<job_id>", methods=["POST"])
def api_queue_cancel(job_id):
    ok = qcancel(job_id)
    if not ok:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"ok": ok})

@app.route("/api/queue/remove/<job_id>", methods=["POST"])
def api_queue_remove(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    qremove(job_id)
    return jsonify({"ok": True})

@app.route("/api/queue/retry-failed", methods=["POST"])
def api_queue_retry():
    retry_failed()
    return jsonify({"ok": True})

@app.route("/api/queue/stats")
def api_queue_stats():
    return jsonify(queue_stats())

@app.route("/api/queue/clear-completed", methods=["POST"])
def api_queue_clear():
    clear_completed()
    return jsonify({"ok": True})

# ═══════════════════════════════════════════════════════════
#  YOUTUBE UPLOAD — Direct Video Publishing
# ═══════════════════════════════════════════════════════════

@app.route("/api/yt-upload/status")
def api_yt_upload_status():
    from core.youtube_upload import get_auth_status
    return jsonify(get_auth_status())

@app.route("/api/yt-upload/save-client-secret", methods=["POST"])
def api_yt_upload_save_secret():
    from core.youtube_upload import save_client_secret
    data = request.json or {}
    raw = data.get("client_secret", "")
    if not raw:
        return jsonify({"error": "No client_secret JSON provided"}), 400
    try:
        save_client_secret(raw)
        return jsonify({"ok": True, "message": "Client secret saved. Re-authentication required."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/yt-upload/upload", methods=["POST"])
def api_yt_upload_do():
    from core.youtube_upload import upload_video, is_configured
    if not is_configured():
        return jsonify({"error": "YouTube OAuth not configured. Add client_secret.json first."}), 400
    data = request.json or {}
    video_path = data.get("video_path", "")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": f"Video not found: {video_path}"}), 400
    try:
        def _cb(pct, msg):
            pass
        video_id, url = upload_video(
            video_path,
            title=data.get("title", "My Video"),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            category_id=data.get("category_id", "22"),
            privacy_status=data.get("privacy_status", "public"),
            playlist_id=data.get("playlist_id"),
            thumbnail_path=data.get("thumbnail_path"),
            on_progress=_cb,
        )
        if video_id:
            return jsonify({"ok": True, "video_id": video_id, "url": url})
        return jsonify({"error": url}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════
#  BRAND KIT — Brand Identity Management
# ═══════════════════════════════════════════════════════════

@app.route("/api/brand/kit", methods=["GET"])
def api_brand_kit_get():
    from core.brand_kit import get_kit
    return jsonify(get_kit())

@app.route("/api/brand/kit", methods=["POST"])
def api_brand_kit_update():
    from core.brand_kit import update_kit
    data = request.json or {}
    kit = update_kit(data)
    return jsonify({"ok": True, "kit": kit})

@app.route("/api/brand/reset", methods=["POST"])
def api_brand_reset():
    from core.brand_kit import reset_kit
    kit = reset_kit()
    return jsonify({"ok": True, "kit": kit})

@app.route("/api/brand/upload-logo", methods=["POST"])
def api_brand_upload_logo():
    from core.brand_kit import upload_logo
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    tmp = tempfile.NamedTemporaryFile(suffix=os.path.splitext(f.filename)[1], delete=False)
    f.save(tmp.name)
    try:
        path = upload_logo(tmp.name)
        return jsonify({"ok": True, "path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try: os.unlink(tmp.name)
        except: pass

@app.route("/api/brand/upload-watermark", methods=["POST"])
def api_brand_upload_watermark():
    from core.brand_kit import upload_watermark
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    tmp = tempfile.NamedTemporaryFile(suffix=os.path.splitext(f.filename)[1], delete=False)
    f.save(tmp.name)
    try:
        path = upload_watermark(tmp.name)
        return jsonify({"ok": True, "path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try: os.unlink(tmp.name)
        except: pass

@app.route("/api/brand/assets")
def api_brand_assets():
    from core.brand_kit import get_assets_list
    return jsonify({"assets": get_assets_list()})

@app.route("/api/brand/delete-asset", methods=["POST"])
def api_brand_delete_asset():
    from core.brand_kit import delete_asset
    data = request.json or {}
    name = data.get("name", "")
    if not name:
        return jsonify({"error": "No filename"}), 400
    ok = delete_asset(name)
    return jsonify({"ok": ok})

# ═══════════════════════════════════════════════════════════
#  CAPTION STYLER — Animated Caption Styles
# ═══════════════════════════════════════════════════════════

@app.route("/api/captions/styles")
def api_captions_styles():
    from core.caption_styler import list_styles
    return jsonify({"styles": list_styles()})

@app.route("/api/captions/styles/<name>")
def api_captions_style_get(name):
    from core.caption_styler import get_style
    style = get_style(name)
    if not style:
        return jsonify({"error": "Style not found"}), 404
    return jsonify(style)

@app.route("/api/captions/styles", methods=["POST"])
def api_captions_style_save():
    from core.caption_styler import save_style
    data = request.json or {}
    name = data.get("name", "")
    if not name:
        return jsonify({"error": "Style name required"}), 400
    style = save_style(name, data.get("style", {}))
    return jsonify({"ok": True, "style": style})

@app.route("/api/captions/styles/<name>", methods=["DELETE"])
def api_captions_style_delete(name):
    from core.caption_styler import delete_style
    ok = delete_style(name)
    return jsonify({"ok": ok})

@app.route("/api/captions/reset", methods=["POST"])
def api_captions_reset():
    from core.caption_styler import reset_styles
    styles = reset_styles()
    return jsonify({"ok": True, "styles": styles})

@app.route("/api/captions/render", methods=["POST"])
def api_captions_render():
    """Render captions onto a video with selected style."""
    from core.caption_styler import render_captions, get_style
    data = request.json or {}
    video_path = data.get("video_path", "")
    segments = data.get("segments", [])
    style_name = data.get("style", "typewriter")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video not found"}), 400
    if not segments:
        return jsonify({"error": "No subtitle segments"}), 400

    output_dir = os.path.dirname(video_path)
    output_name = f"captioned_{Path(video_path).stem}_{int(time.time())}.mp4"
    output_path = os.path.join(output_dir, output_name)
    try:
        result = render_captions(video_path, segments, output_path, style_name)
        return jsonify({"ok": True, "output": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════
#  BLOG TO VIDEO — URL Content to Video Pipeline
# ═══════════════════════════════════════════════════════════

@app.route("/api/blog/extract", methods=["POST"])
def api_blog_extract():
    from core.blog_to_video import extract_url_content, validate_url
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not validate_url(url):
        return jsonify({"error": "Invalid URL"}), 400
    try:
        content = extract_url_content(url)
        if "error" in content:
            return jsonify({"error": content["error"]}), 500
        return jsonify(content)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/blog/summarize", methods=["POST"])
def api_blog_summarize():
    from core.blog_to_video import summarize_content
    data = request.json or {}
    text = data.get("text", "")
    language = data.get("language", "portuguese")
    if not text or len(text.strip()) < 50:
        return jsonify({"error": "Text too short (min 50 chars)"}), 400
    try:
        summary = summarize_content(text, language=language)
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/blog/to-video", methods=["POST"])
def api_blog_to_video():
    from core.blog_to_video import url_to_pipeline_config, validate_url
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not validate_url(url):
        return jsonify({"error": "Invalid URL"}), 400
    try:
        config = url_to_pipeline_config(
            url,
            language=data.get("language", "portuguese"),
            duration=data.get("duration", "5 min"),
            tone=data.get("tone", "Documentary"),
        )
        if "error" in config:
            return jsonify({"error": config["error"]}), 500
        return jsonify(config)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/blog/validate-url", methods=["POST"])
def api_blog_validate():
    from core.blog_to_video import validate_url
    data = request.json or {}
    url = data.get("url", "")
    return jsonify({"valid": validate_url(url)})

# ═══════════════════════════════════════════════════════════
#  AGENT MODE — One-Click Autonomous Video Creation
# ═══════════════════════════════════════════════════════════

@app.route("/api/agent/start", methods=["POST"])
def api_agent_start():
    from core.agent_mode import run_agent
    data = request.json or {}
    topic = data.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "Topic is required"}), 400
    config = data.get("config", {})
    avatar_path = data.get("avatar_path", "")
    if avatar_path and not os.path.exists(avatar_path):
        return jsonify({"error": f"Avatar not found: {avatar_path}"}), 400
    session = run_agent(topic, config, avatar_path if avatar_path else None)
    return jsonify({"ok": True, "session": session.to_dict()})

@app.route("/api/agent/status/<session_id>")
def api_agent_status(session_id):
    from core.agent_mode import get_session
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session)

@app.route("/api/agent/cancel/<session_id>", methods=["POST"])
def api_agent_cancel(session_id):
    from core.agent_mode import cancel_session
    ok = cancel_session(session_id)
    return jsonify({"ok": ok})

@app.route("/api/agent/list")
def api_agent_list():
    from core.agent_mode import list_sessions
    limit = int(request.args.get("limit", 20))
    return jsonify({"sessions": list_sessions(limit)})

@app.route("/api/agent/active")
def api_agent_active():
    from core.agent_mode import active_sessions_list
    return jsonify({"sessions": active_sessions_list()})


# ═══════════════════════════════════════════════════════════
#  AUDIO TO VIDEO — Waveform Visualization
# ═══════════════════════════════════════════════════════════

@app.route("/api/audio-to-video/render", methods=["POST"])
def api_audio_to_video():
    from core.audio_to_video import render_waveform_video, get_audio_duration
    data = request.json or {}
    audio_path = data.get("audio_path", "")
    if not audio_path or not os.path.exists(audio_path):
        return jsonify({"error": "Audio file not found"}), 400
    output_dir = os.path.join(PROJECT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"waveform_{Path(audio_path).stem}_{int(time.time())}.mp4"
    out_path = os.path.join(output_dir, out_name)
    try:
        result = render_waveform_video(
            audio_path, out_path,
            title=data.get("title", ""),
            author=data.get("author", ""),
            background_image=data.get("background_image"),
            bar_color=data.get("bar_color", "#1a73e8"),
            bg_color=data.get("bg_color", "#000000"),
        )
        return jsonify({"ok": True, "output": result, "name": out_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/audio-to-video/info", methods=["POST"])
def api_audio_info():
    from core.audio_to_video import get_audio_duration
    data = request.json or {}
    path = data.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 400
    try:
        dur = get_audio_duration(path)
        return jsonify({"duration": dur, "duration_str": f"{int(dur//60)}:{int(dur%60):02d}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════
#  VIDEO CLIPPER — OpusClip-style AI Clip Extraction
# ═══════════════════════════════════════════════════════════

@app.route("/api/clipper/detect", methods=["POST"])
def api_clipper_detect():
    from core.video_clipper import detect_clips
    data = request.json or {}
    video_path = data.get("video_path", "")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video not found"}), 400
    try:
        clips = detect_clips(
            video_path,
            min_clip_dur=int(data.get("min_duration", 15)),
            max_clip_dur=int(data.get("max_duration", 120)),
            max_clips=int(data.get("max_clips", 5)),
            min_score=int(data.get("min_score", 50)),
        )
        return jsonify({"clips": clips, "total": len(clips)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/clipper/extract", methods=["POST"])
def api_clipper_extract():
    from core.video_clipper import extract_clip
    data = request.json or {}
    video_path = data.get("video_path", "")
    start = float(data.get("start", 0))
    end = float(data.get("end", 0))
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video not found"}), 400
    if end <= start:
        return jsonify({"error": "End must be > start"}), 400
    output_dir = os.path.join(PROJECT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"clip_{Path(video_path).stem}_{int(start)}_{int(end)}_{int(time.time())}.mp4"
    out_path = os.path.join(output_dir, out_name)
    try:
        result = extract_clip(
            video_path, start, end, out_path,
            add_captions=data.get("add_captions", True),
            format=data.get("format", "vertical"),
            caption_style=data.get("caption_style", "highlight"),
        )
        return jsonify({"ok": True, "output": result, "name": out_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/clipper/auto", methods=["POST"])
def api_clipper_auto():
    from core.video_clipper import detect_and_extract_clips
    data = request.json or {}
    video_path = data.get("video_path", "")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video not found"}), 400
    output_dir = os.path.join(PROJECT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)
    try:
        results = detect_and_extract_clips(
            video_path, output_dir,
            max_clips=int(data.get("max_clips", 5)),
            formats=data.get("formats", ["vertical", "landscape"]),
        )
        return jsonify({"results": results, "total": len(results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════
#  SOCIAL PUBLISHER — TikTok, Instagram, Facebook
# ═══════════════════════════════════════════════════════════

@app.route("/api/social/connections")
def api_social_connections():
    from core.social_publisher import get_connections
    return jsonify(get_connections())

@app.route("/api/social/connect", methods=["POST"])
def api_social_connect():
    from core.social_publisher import connect_tiktok, connect_instagram, connect_facebook
    data = request.json or {}
    platform = data.get("platform", "")
    if platform == "tiktok":
        result = connect_tiktok(data.get("session_id", ""), data.get("cookies", {}))
    elif platform == "instagram":
        result = connect_instagram(data.get("user_id", ""), data.get("access_token", ""), data.get("page_id"))
    elif platform == "facebook":
        result = connect_facebook(data.get("page_id", ""), data.get("access_token", ""), data.get("user_id", ""))
    else:
        return jsonify({"error": f"Unknown platform: {platform}"}), 400
    return jsonify({"ok": True, "connection": result})

@app.route("/api/social/disconnect", methods=["POST"])
def api_social_disconnect():
    from core.social_publisher import disconnect
    data = request.json or {}
    ok = disconnect(data.get("platform", ""))
    return jsonify({"ok": ok})

@app.route("/api/social/publish", methods=["POST"])
def api_social_publish():
    from core.social_publisher import publish_to_all
    data = request.json or {}
    video_path = data.get("video_path", "")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video not found"}), 400
    try:
        results = publish_to_all(
            video_path,
            title=data.get("title", "My Video"),
            description=data.get("description", ""),
            platforms=data.get("platforms", ["youtube"]),
        )
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/social/publishable-videos")
def api_social_videos():
    from core.social_publisher import get_publishable_videos
    return jsonify({"videos": get_publishable_videos()})

# ═══════════════════════════════════════════════════════════
#  AI IMAGE GENERATION — Text-to-Image
# ═══════════════════════════════════════════════════════════

@app.route("/api/image-gen/generate", methods=["POST"])
def api_image_gen():
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400
    api = data.get("api", "pollinations")
    output_dir = os.path.join(PROJECT_DIR, "output", "ai_images")
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"ai_img_{int(time.time())}.png"
    out_path = os.path.join(output_dir, out_name)
    try:
        if api == "gemini":
            from core.ai_image_gen import generate_image_gemini
            path, err = generate_image_gemini(prompt, output_path=out_path)
        else:
            from core.ai_image_gen import generate_image_pollinations
            path, err = generate_image_pollinations(
                prompt, output_path=out_path,
                width=int(data.get("width", 1024)),
                height=int(data.get("height", 1024)),
            )
        if err:
            return jsonify({"error": err}), 500
        return jsonify({"ok": True, "path": path, "name": out_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/image-gen/scenes", methods=["POST"])
def api_image_gen_scenes():
    from core.ai_image_gen import generate_scene_images
    data = request.json or {}
    segments = data.get("segments", [])
    if not segments:
        return jsonify({"error": "No segments provided"}), 400
    output_dir = os.path.join(PROJECT_DIR, "output", "ai_scenes")
    os.makedirs(output_dir, exist_ok=True)
    try:
        results = generate_scene_images(
            segments, output_dir,
            style=data.get("style", "cinematic"),
            api=data.get("api", "pollinations"),
        )
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/image-gen/thumbnail", methods=["POST"])
def api_image_gen_thumbnail():
    from core.ai_image_gen import generate_thumbnail_image
    data = request.json or {}
    topic = data.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "Topic is required"}), 400
    output_dir = os.path.join(PROJECT_DIR, "output", "ai_thumbnails")
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"thumb_{int(time.time())}.png"
    out_path = os.path.join(output_dir, out_name)
    try:
        path, err = generate_thumbnail_image(topic, out_path, data.get("style", "youtube_thumbnail"))
        if err:
            return jsonify({"error": err}), 500
        return jsonify({"ok": True, "path": path, "name": out_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════
#  TRANSLATION & MULTI-LANGUAGE VOICEOVER
# ═══════════════════════════════════════════════════════════

@app.route("/api/translate/languages")
def api_translate_languages():
    from core.translation_pipeline import list_supported_languages
    return jsonify({"languages": list_supported_languages()})

@app.route("/api/translate/voices", methods=["POST"])
def api_translate_voices():
    from core.translation_pipeline import list_voices_for_language
    data = request.json or {}
    lang = data.get("language", "english")
    return jsonify({"voices": list_voices_for_language(lang)})

@app.route("/api/translate/text", methods=["POST"])
def api_translate_text():
    from core.translation_pipeline import translate_text
    data = request.json or {}
    text = data.get("text", "")
    target = data.get("target_language", "english")
    source = data.get("source_language")
    if not text:
        return jsonify({"error": "Text is required"}), 400
    try:
        result = translate_text(text, target, source)
        return jsonify({"translated": result, "language": target})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/translate/script", methods=["POST"])
def api_translate_script():
    from core.translation_pipeline import translate_script
    data = request.json or {}
    segments = data.get("segments", [])
    target = data.get("target_language", "english")
    if not segments:
        return jsonify({"error": "No segments"}), 400
    try:
        result = translate_script(segments, target, data.get("source_language"))
        return jsonify({"segments": result, "language": target})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/translate/dub", methods=["POST"])
def api_translate_dub():
    from core.translation_pipeline import dub_video
    data = request.json or {}
    video_path = data.get("video_path", "")
    segments = data.get("segments", [])
    target = data.get("target_language", "english")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video not found"}), 400
    if not segments:
        return jsonify({"error": "No subtitle segments"}), 400
    output_dir = os.path.join(PROJECT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"dubbed_{target}_{Path(video_path).stem}_{int(time.time())}.mp4"
    out_path = os.path.join(output_dir, out_name)
    try:
        result = dub_video(video_path, segments, target, out_path)
        return jsonify({"ok": True, "output": result, "name": out_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/translate/voiceover", methods=["POST"])
def api_translate_voiceover():
    from core.translation_pipeline import generate_voiceover, generate_segment_voiceovers
    data = request.json or {}
    text = data.get("text", "")
    language = data.get("language", "portuguese")
    segments = data.get("segments")
    if segments:
        output_dir = os.path.join(PROJECT_DIR, "output", f"voiceover_{int(time.time())}")
        results = generate_segment_voiceovers(segments, language, output_dir)
        return jsonify({"results": results, "dir": output_dir})
    if not text:
        return jsonify({"error": "Text or segments required"}), 400
    output_dir = os.path.join(PROJECT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"voice_{language}_{int(time.time())}.mp3"
    out_path = os.path.join(output_dir, out_name)
    try:
        result = generate_voiceover(text, language, out_path)
        return jsonify({"ok": True, "output": result, "name": out_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════
#  PPT TO VIDEO — PowerPoint to Video
# ═══════════════════════════════════════════════════════════

@app.route("/api/ppt-to-video/convert", methods=["POST"])
def api_ppt_to_video():
    from core.ppt_to_video import ppt_to_video, extract_slides
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".pptx"):
        return jsonify({"error": "Please upload a .pptx file"}), 400
    tmp = tempfile.NamedTemporaryFile(suffix=".pptx", delete=False)
    f.save(tmp.name)
    tmp.close()
    output_dir = os.path.join(PROJECT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"pptx_{Path(f.filename).stem}_{int(time.time())}.mp4"
    out_path = os.path.join(output_dir, out_name)
    try:
        result = ppt_to_video(
            tmp.name, out_path,
            language=request.form.get("language", "portuguese"),
            slide_duration=int(request.form.get("slide_duration", 8)),
        )
        return jsonify({"ok": True, "output": result, "name": out_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try: os.unlink(tmp.name)
        except: pass

@app.route("/api/ppt-to-video/extract", methods=["POST"])
def api_ppt_extract():
    from core.ppt_to_video import extract_slides
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    tmp = tempfile.NamedTemporaryFile(suffix=".pptx", delete=False)
    f.save(tmp.name)
    tmp.close()
    output_dir = os.path.join(PROJECT_DIR, "output", f"pptx_slides_{int(time.time())}")
    try:
        slides = extract_slides(tmp.name, output_dir)
        if isinstance(slides, tuple) and slides[0] is None:
            return jsonify({"error": slides[1]}), 500
        return jsonify({"slides": slides, "total": len(slides)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try: os.unlink(tmp.name)
        except: pass

# ═══════════════════════════════════════════════════════════
#  AUTO MODE — Daily Autonomous Content
# ═══════════════════════════════════════════════════════════

_auto_mode_running = False
_auto_mode_thread = None

@app.route("/api/auto-mode/start", methods=["POST"])
def api_auto_mode_start():
    global _auto_mode_running, _auto_mode_thread
    if _auto_mode_running:
        return jsonify({"error": "Auto mode already running"}), 409
    data = request.json or {}
    topic = data.get("topic", "").strip()
    interval_hours = int(data.get("interval_hours", 24))
    if not topic:
        return jsonify({"error": "Topic is required"}), 400
    config = data.get("config", {})

    def _loop():
        global _auto_mode_running
        while _auto_mode_running:
            try:
                from core.agent_mode import run_agent
                session = run_agent(topic, config)
                session.log(f"Auto mode: generating video for '{topic}'", "info")
                timeout = interval_hours * 3600
                waited = 0
                while waited < timeout and _auto_mode_running:
                    if session.status in ("done", "failed", "cancelled"):
                        break
                    time.sleep(10)
                    waited += 10
            except Exception:
                pass
            for _ in range(interval_hours * 12):
                if not _auto_mode_running:
                    break
                time.sleep(300)

    _auto_mode_running = True
    _auto_mode_thread = threading.Thread(target=_loop, daemon=True)
    _auto_mode_thread.start()
    return jsonify({"ok": True, "topic": topic, "interval_hours": interval_hours})

@app.route("/api/auto-mode/stop", methods=["POST"])
def api_auto_mode_stop():
    global _auto_mode_running
    _auto_mode_running = False
    return jsonify({"ok": True})

@app.route("/api/auto-mode/status")
def api_auto_mode_status():
    global _auto_mode_running
    return jsonify({"running": _auto_mode_running, "topic": getattr(_auto_mode_thread, "name", "")})

# ═══════════════════════════════════════════════════════════
#  SCREEN RECORDER — Record desktop and convert to video
# ═══════════════════════════════════════════════════════════

@app.route("/api/screen-recorder/save", methods=["POST"])
def api_screen_recorder_save():
    """Save uploaded screen recording and return path."""
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No filename"}), 400
    safe_name = f"screenrec_{int(time.time())}_{f.filename.replace(' ', '_')}"
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    f.save(save_path)
    return jsonify({"path": save_path, "name": safe_name, "size_mb": round(os.path.getsize(save_path)/1024/1024, 1)})

@app.route("/api/screen-recorder/convert", methods=["POST"])
def api_screen_recorder_convert():
    """Convert screen recording to pipeline-ready format."""
    data = request.json or {}
    video_path = data.get("video_path", "")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Recording not found"}), 400
    output_dir = os.path.join(PROJECT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"converted_{Path(video_path).stem}_{int(time.time())}.mp4"
    out_path = os.path.join(output_dir, out_name)
    try:
        import subprocess
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            out_path,
        ], capture_output=True, text=True, timeout=300, check=True)
        return jsonify({"ok": True, "output": out_path, "name": out_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  TITLE INTELLIGENCE ENGINE — Viral Analysis + AI Tools (ported from TitlePilot Pro)
# ═══════════════════════════════════════════════════════════════════════════
import re as _re
import math as _math
from collections import Counter as _Counter

# ── AI concurrency limiter (prevents Gemini rate-limit under load) ────────
_AI_SEMAPHORE = threading.Semaphore(4)  # max 4 concurrent AI calls

# ── AI helper (wraps core.ai_engine with image support) ──────────────────
def _ask_gemini_gcg(prompt, user_api_key=None, max_retries=None, image_b64=None):
    """Production-grade AI engine: 3-model cascade, rate-limit protection, exponential backoff.
    
    Fallback chain:
      1. core.ai_engine (primary — uses best available model)
      2. gemini-2.5-flash (backup 1)
      3. gemini-2.0-flash (backup 2)
      4. gemini-1.5-flash (backup 3 — most stable/available)
    """
    import time as _t
    import traceback as _tb

    # ── Proactive rate limiting ──
    if not hasattr(_ask_gemini_gcg, '_last_call'):
        _ask_gemini_gcg._last_call = 0
        _ask_gemini_gcg._call_count = 0
        _ask_gemini_gcg._error_count = 0
    elapsed = _t.time() - _ask_gemini_gcg._last_call
    min_interval = 2.0 if _ask_gemini_gcg._error_count > 3 else 1.5
    if elapsed < min_interval:
        _t.sleep(min_interval - elapsed)
    _ask_gemini_gcg._call_count += 1

    def _is_rate_limit(err):
        s = str(err).lower()
        return any(k in s for k in ['429', 'rate', 'quota', 'resource_exhausted', 'too many', 'overloaded'])

    def _direct_gemini(model_name, api_key, prompt_text):
        """Direct google-genai call as fallback."""
        from google import genai
        c = genai.Client(api_key=api_key)
        r = c.models.generate_content(model=model_name, contents=prompt_text)
        return r.text if r and r.text else None

    with _AI_SEMAPHORE:
        retries = max_retries or 5
        last_error = None

        # ── Stage 1: Primary AI engine with retries ──
        for attempt in range(retries):
            try:
                from core.ai_engine import ask_ai as _ask_ai
                from core.api_keys import load_api_key
                key = user_api_key if (user_api_key and len(str(user_api_key)) > 10) else load_api_key("google_ai")
                result = _ask_ai(prompt, api_key=key or None, max_retries=2, image_b64=image_b64)
                result_len = len(str(result).strip()) if result else 0
                if result_len > 200:
                    _ask_gemini_gcg._last_call = _t.time()
                    _ask_gemini_gcg._error_count = max(0, _ask_gemini_gcg._error_count - 1)
                    return result
                elif result_len > 0:
                    print(f"  [AI] Short response ({result_len}ch), retrying...")
                    last_error = RuntimeError(f"Response too short: {result_len} chars")
                    _t.sleep(3)
            except Exception as e:
                last_error = e
                if _is_rate_limit(e):
                    _ask_gemini_gcg._error_count += 1
                    wait = min(30, 3 * (2 ** attempt))
                    print(f"  [AI] Rate limit (attempt {attempt+1}/{retries}), waiting {wait}s")
                else:
                    wait = 2 * (attempt + 1)
                _t.sleep(wait)

        # ── Stage 2: Cascade through backup models ──
        from core.api_keys import load_api_key
        key = user_api_key if (user_api_key and len(str(user_api_key)) > 10) else load_api_key("google_ai")
        if not key:
            raise RuntimeError("No AI API key configured")

        backup_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
        for i, model in enumerate(backup_models):
            try:
                _t.sleep(3 + i * 2)  # Increasing pause: 3s, 5s, 7s
                print(f"  [AI] Trying backup model: {model} ({i+1}/{len(backup_models)})")
                result = _direct_gemini(model, key, prompt)
                result_len = len(str(result).strip()) if result else 0
                if result_len > 200:
                    _ask_gemini_gcg._last_call = _t.time()
                    _ask_gemini_gcg._error_count = max(0, _ask_gemini_gcg._error_count - 1)
                    print(f"  [AI] Backup {model} succeeded! ({result_len}ch)")
                    return result
                elif result_len > 0:
                    print(f"  [AI] Backup {model} short response ({result_len}ch), trying next...")
            except Exception as e:
                print(f"  [AI] Backup {model} failed: {str(e)[:80]}")
                last_error = e
                if _is_rate_limit(e):
                    _t.sleep(10)  # Extra wait on rate limit
                continue

        # ── Stage 3: All models failed ──
        _ask_gemini_gcg._error_count += 1
        err_msg = str(last_error)[:200] if last_error else "Unknown"
        raise RuntimeError(f"AI temporarily unavailable after trying all models. Error: {err_msg}")

# ── Viral structures & emotional words database ───────────────────────────
_VIRAL_STRUCTURES = {
    "curiosity_gap":       {"pattern": r"\b(?:why|how|what happens|the real reason|the truth about|nobody|no one|nobody knows|secret|hidden|they don't want|they don't tell|what they|you won't believe)\b", "name": "Curiosity Gap",         "ctr_boost": 1.40},
    "superlative":         {"pattern": r"\b(?:most|largest|biggest|deadliest|worst|best|extreme|impossible|insane|craziest|rarest|greatest|highest|lowest|richest|poorest)\b", "name": "Superlative",        "ctr_boost": 1.35},
    "specific_number":     {"pattern": r"\b\d+\b",                                                                                                                            "name": "Specific Number",     "ctr_boost": 1.30},
    "money_number":        {"pattern": r"\$[\d,.]+",                                                                                                                          "name": "Money Hook",          "ctr_boost": 1.32},
    "permanence":          {"pattern": r"\b(?:never|forever|always|still|remains|eternal|no longer|ended|disappeared|vanished|erased|gone forever)\b",                        "name": "Permanence Claim",    "ctr_boost": 1.25},
    "authority_emotion":   {"pattern": r"\b(?:scientist|government|nasa|expert|doctor|military|fbi|cia|professor|study|research)\b",                                           "name": "Authority Signal",    "ctr_boost": 1.35},
    "contrast":            {"pattern": r"\b(?:but|however|yet|despite|instead|actually|turns out|thought|wrong|until|suddenly)\b",                                            "name": "Contrast Hook",       "ctr_boost": 1.28},
    "forbidden":           {"pattern": r"\b(?:forbidden|banned|illegal|restricted|classified|censored|deleted|suppressed|covered up)\b",                                      "name": "Forbidden Content",   "ctr_boost": 1.42},
    "absolute_negative":   {"pattern": r"\b(?:never|stop|don't|avoid|ruin|destroy|fail|fake|lie|scam|wrong|disaster|mistake)\b",                                             "name": "Absolute Negative",   "ctr_boost": 1.35},
    "timeline_urgency":    {"pattern": r"\b(?:just|finally|last|too late|now|before|after|suddenly|overnight|in \d+ days|in \d+ hours|in \d+ minutes)\b",                    "name": "Timeline Urgency",    "ctr_boost": 1.28},
    "antagonist":          {"pattern": r"\b(?:vs|versus|against|enemy|villain|monster|predator|killer|scam|fraud|trap|threat|danger)\b",                                      "name": "Antagonist/Conflict", "ctr_boost": 1.33},
    "discovery":           {"pattern": r"\b(?:discovered|solved|proved|breakthrough|unlocked|found|revealed|exposed|uncovered|finally|confirmed)\b",                          "name": "Discovery/Revelation","ctr_boost": 1.27},
    "nobody_lives_in":     {"pattern": r"\b(?:nobody lives|no one lives|nobody can|no one can|nobody talks|nobody knows|nobody told)\b",                                      "name": "Absolute Exclusion",  "ctr_boost": 1.45},
    "dark_truth":          {"pattern": r"\b(?:dark truth|dark secret|dark side|dark history|disturbing truth|shocking truth|ugly truth|real truth|terrifying truth)\b",       "name": "Dark Truth",          "ctr_boost": 1.42},
    "personal_story":      {"pattern": r"\b(?:i tried|i spent|i tested|i found|i discovered|i lost|i survived|i lived|we tried|they tried)\b",                              "name": "Personal Story",      "ctr_boost": 1.30},
    "place_mystery":       {"pattern": r"\b(?:country|town|city|place|island|mountain|ocean|desert|village|region|state|nation)\b",                                           "name": "Place Hook",          "ctr_boost": 1.20},
}

_EMOTIONAL_WORDS = {
    # Score 10 — maximum impact
    "terrifying": 10, "deadly": 10, "forbidden": 10, "brutal": 10, "horrifying": 10,
    "terrified": 10, "fatal": 10, "banned": 10, "illegal": 9, "doomed": 9,
    # Score 8-9 — high impact
    "chilling": 9, "shocking": 8, "insane": 8, "dangerous": 8, "secret": 8,
    "impossible": 8, "cursed": 8, "savage": 8, "catastrophic": 8, "nightmare": 8,
    "destroyed": 8, "unstoppable": 8, "speechless": 8, "exposed": 8, "trapped": 8,
    # Score 7 — medium-high
    "unbelievable": 7, "hidden": 7, "extreme": 7, "mysterious": 7, "haunted": 7,
    "abandoned": 7, "genius": 7, "bizarre": 7, "creepy": 7, "terrifying": 7,
    "disturbing": 7, "sinister": 7, "shocking": 7, "vanished": 7, "erased": 7,
    # Score 6 — medium
    "incredible": 6, "ancient": 6, "legendary": 6, "massive": 6, "epic": 6,
    "lost": 6, "unsolved": 6, "dark": 6, "nobody": 6, "no one": 6,
    "truth": 5, "revealed": 6, "exposed": 6, "classified": 6, "conspiracy": 6,
    # Score 5 — baseline
    "weird": 5, "strange": 5, "wild": 5, "real": 4, "actual": 4, "true": 4,
    "mountain": 4, "ocean": 4, "discovered": 5, "found": 4, "secret": 5,
}

def _analyze_title_viral(title):
    result = {
        "title": title, "length": len(title), "words": len(title.split()),
        "score": 0, "structures": [], "emotional_words": [], "power_words": [],
        "issues": [], "suggestions": [],
    }
    t_lower = title.lower()
    tlen = len(title)

    # ── Length scoring (separate bucket, max 15 pts) ──────────────────────
    if tlen < 35:
        result["issues"].append("Too short — aim for 60-100 characters for maximum CTR")
        result["score"] -= 8
    elif tlen < 55:
        result["issues"].append("Short — 60-100 chars performs 40% better")
        result["score"] -= 2
    elif 75 <= tlen <= 95:
        result["score"] += 15
    elif 60 <= tlen <= 100:
        result["score"] += 10
    elif 55 <= tlen <= 59:
        result["score"] += 4
    elif tlen > 110:
        result["issues"].append("Too long — YouTube truncates after ~100 chars on mobile")
        result["score"] -= 4

    # ── ALL CAPS power words (max 18 pts) ─────────────────────────────────
    caps = _re.findall(r'\b[A-Z]{2,}\b', title)
    caps = [c for c in caps if c not in {"IN","OF","THE","AND","TO","A","IS","IT","OR","BY","ON","AT","AS"}]
    if caps:
        result["power_words"] = caps
        result["score"] += min(len(caps) * 7, 18)
    else:
        result["suggestions"].append("Add 1-2 CAPS words for emphasis (SHOCKING, NEVER, DARK, etc.)")

    # ── Emotional words (max 25 pts) ──────────────────────────────────────
    emo_score = 0
    for word, val in _EMOTIONAL_WORDS.items():
        if word in t_lower:
            if word not in result["emotional_words"]:
                result["emotional_words"].append(word)
                emo_score += val
    result["score"] += min(emo_score, 25)
    if not result["emotional_words"]:
        result["suggestions"].append("Add emotional trigger words (secret, forbidden, deadly, dark, etc.)")

    # ── Viral structures (max 42 pts total) ───────────────────────────────
    struct_score = 0
    for sid, sdata in _VIRAL_STRUCTURES.items():
        if _re.search(sdata["pattern"], t_lower):
            result["structures"].append({
                "id": sid, "name": sdata["name"],
                "ctr_boost": sdata["ctr_boost"], "desc": ""
            })
            struct_score += int((sdata["ctr_boost"] - 1) * 100)   # e.g. 1.40 → 40 pts raw
    # Cap and normalize: max 42 pts
    result["score"] += min(struct_score, 42)
    if not result["structures"]:
        result["suggestions"].append("Use a viral structure: Curiosity Gap, Superlative, or Nobody Lives In")

    # ── Numbers & specifics (+8) ──────────────────────────────────────────
    if _re.search(r'\$?[\d,.]+', title):
        result["score"] += 8
    else:
        result["suggestions"].append("Add specific numbers ($2.5B, 15 Places, 40 Days, etc.)")

    # ── Question mark engagement (+5) ────────────────────────────────────
    if "?" in title:
        result["score"] += 5

    # ── Parentheses/brackets context boost (+3) ──────────────────────────
    if _re.search(r'[\(\[\{]', title):
        result["score"] += 3

    # ── Clamp & grade ────────────────────────────────────────────────────
    result["score"] = max(0, min(result["score"], 100))
    if result["score"] >= 80:   result["grade"] = "S"
    elif result["score"] >= 65: result["grade"] = "A"
    elif result["score"] >= 50: result["grade"] = "B"
    elif result["score"] >= 35: result["grade"] = "C"
    elif result["score"] >= 20: result["grade"] = "D"
    else:                        result["grade"] = "F"
    return result

# ── TITLE INTELLIGENCE ROUTES ─────────────────────────────────────────────

@app.route("/api/ti/analyze", methods=["POST"])
def ti_analyze():
    data = request.json or {}
    title = data.get("title", "")
    if not title:
        return jsonify({"error": "No title provided"}), 400
    return jsonify(_analyze_title_viral(title))

@app.route("/api/ti/deep_analysis", methods=["POST"])
def ti_deep_analysis():
    data = request.json or {}
    title = data.get("title", "")
    basic = _analyze_title_viral(title)
    prompt = f"""You are the world's #1 YouTube title optimization expert.
TITLE: "{title}"
Provide a COMPREHENSIVE analysis covering: First Impression, Mental Image Test, Click Trigger, Emotional Hook (score 1-10), Specificity Score (1-10), Competition Check, Thumbnail Compatibility, 3 Improved Versions (60-100 chars), Main Weakness, and Viral Verdict.
Be brutally honest and specific. No generic advice."""
    basic["ai_deep_analysis"] = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify(basic)

@app.route("/api/ti/generate", methods=["POST"])
def ti_generate():
    data = request.json or {}
    topic = (data.get("topic") or "").strip()[:500]
    if not topic:
        return jsonify({"error": "Topic required"}), 400
    language = data.get("language", "English")[:50]
    niche = data.get("niche", "")[:300]

    # Inject niche intelligence if available
    niche_intel = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE, VIRAL_STRUCTURES, PSYCHOLOGICAL_TRIGGERS
        for niche_key, intel in NICHE_INTELLIGENCE.items():
            if niche_key.lower() in niche.lower() or niche.lower() in niche_key.lower():
                niche_intel = f"""
NICHE INTELLIGENCE (data-driven for '{niche_key}'):
- RPM range: ${intel['rpm_usd'][0]}-${intel['rpm_usd'][1]}/1000 views
- Best upload days: {', '.join(intel['best_days'])}
- Best posting time: {intel['best_time']}
- Top hooks that WORK in this niche: {', '.join(intel['top_hooks'])}
- Target demographic: {intel['target_demo']}
- Thumbnail style proven: {intel['thumbnail_style']}"""
                break
        viral_ctx = """
PROVEN VIRAL STRUCTURES (use these as templates):
- Curiosity Gap: 'The [Adj] [Subject] Nobody Talks About'
- Fear/FOMO: 'STOP [Common Action] Immediately (Here's Why)'
- Data-Driven: 'After [N] Years of Research, Scientists Discovered [X]'
- Story Hook: 'I Spent [Time] [Extreme Thing] — Here's What Happened'
- Superlative: 'Inside The World's [Most Adj] [Place/Thing]'
- Listicle: '[N] [Subject] Facts That [Shocked/Changed] Everything'"""
    except ImportError:
        niche_intel = ""
        viral_ctx = ""

    prompt = f"""You are the world's #1 YouTube Title Engineer. You have decoded the viral DNA of every 100M+ view video.

TOPIC: {topic}
LANGUAGE: {language}
{f'NICHE: {niche}' if niche else ''}
{niche_intel}
{viral_ctx}

YOUR MISSION: Generate exactly 15 EXTREMELY viral YouTube titles.

MANDATORY RULES:
1. Every title MUST be 70-100 characters (count exactly)
2. Every title MUST have 1-2 ALL CAPS power words (NEVER, HIDDEN, EXPOSED, REAL, ACTUAL, DANGEROUS, etc.)
3. Every title MUST trigger ONE of these emotions: Curiosity, Fear, Awe, Controversy, Hope
4. Mix title structures: use at least 3 different structures from the list above
5. Include specific numbers or dates where it makes the title more credible
6. FORBIDDEN: generic words like 'Amazing', 'Awesome', 'Great', 'Nice'

For EACH title, output EXACTLY this format (one per line):
[Title] | Thumbnail Concept: [Specific visual element + mood]

No numbering. No headers. No explanations. Just 15 lines.
Verify each title character count before output."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))

    # Detect AI fallback/error text (not actual titles)
    _fallback_markers = ("temporarily unavailable", "rate limit", "quota", "error", "sorry", "cannot", "unable to", "try again", "api key")
    result_lower = result.lower()
    is_fallback = any(m in result_lower for m in _fallback_markers) and len(result.strip().split("\n")) < 5

    raw_titles = []
    if not is_fallback:
        for line in result.strip().split("\n"):
            line = line.strip()
            if not line: continue
            line = _re.sub(r'^\d+[\.\)\-\s]+', '', line).strip().strip('"\'')
            # Skip lines that look like headers or error messages
            if line and len(line) > 20 and not any(m in line.lower() for m in ("temporarily", "rate limit", "quota exceeded", "error occurred")):
                raw_titles.append(line)

    # Filter valid titles (≥50 chars before pipe) — lenient to avoid losing all titles
    valid_long = [t for t in raw_titles if len(t.split('| Thumbnail Concept:')[0].strip()) >= 50]

    if len(valid_long) < 5 and not is_fallback:
        # Not enough titles — retry with ultra-explicit prompt
        retry_prompt = f"""Generate exactly 15 viral YouTube titles about: {topic}
Language: {language}
{f'Niche: {niche}' if niche else ''}

STRICT RULES — violating ANY rule = FAILURE:
- Each title MUST be between 70 and 100 characters (count the characters!)
- Each title MUST contain 1-2 words in ALL CAPS
- Mix hooks: curiosity gap, fear, numbers, superlatives

Output format (one per line, no numbering):
[Title exactly 70-100 chars] | Thumbnail Concept: [Brief visual idea]

Verify every title is 70+ characters before writing it."""
        result2 = _ask_gemini_gcg(retry_prompt, data.get("ai_api_key"))
        raw_titles = []
        for line in result2.strip().split("\n"):
            line = line.strip()
            if not line: continue
            line = _re.sub(r'^\d+[\.\)\-\s]+', '', line).strip().strip('"\'')
            if line and len(line) > 30 and not any(m in line.lower() for m in ("temporarily", "rate limit", "quota exceeded")):
                raw_titles.append(line)
        valid_long = [t for t in raw_titles if len(t.split('| Thumbnail Concept:')[0].strip()) >= 50]
        if not valid_long:
            valid_long = raw_titles

    # Use whatever valid titles we have (fall back to 30-char min if needed)
    raw_titles = valid_long if valid_long else raw_titles

    titles = []
    min_title_len = 30  # Always accept titles — the prompt encourages 70+ but don't drop short ones
    for line in raw_titles:
        parts = line.split("| Thumbnail Concept:")
        actual_title = parts[0].strip()
        if len(actual_title) < min_title_len:
            continue
        thumb_concept = parts[1].strip() if len(parts) > 1 else ""
        analysis = _analyze_title_viral(actual_title)
        titles.append({
            "title": actual_title, "length": len(actual_title),
            "score": analysis["score"], "grade": analysis["grade"],
            "structures": [s["name"] for s in analysis["structures"]],
            "thumbnail_concept": thumb_concept
        })
    titles.sort(key=lambda x: x["score"], reverse=True)
    if not titles:
        return jsonify({"error": "AI unavailable, try again in a moment"}), 503
    return jsonify({"titles": titles, "topic": topic})


@app.route("/api/ti/batch", methods=["POST"])
def ti_batch():
    data = request.json or {}
    titles = data.get("titles", [])
    results = [_analyze_title_viral(t) for t in titles if isinstance(t, str) and t.strip()]
    if not results:
        return jsonify({"error": "No titles"}), 400
    scores = [r["score"] for r in results]
    struct_count = _Counter()
    word_freq = _Counter()
    skip = {"the","a","an","is","in","on","of","and","to","that","this","for","with","are","was"}
    for r in results:
        for s in r["structures"]: struct_count[s["name"]] += 1
        for w in r["title"].lower().split():
            w = w.strip(".,!?;:'\"()-[]|")
            if w and len(w) > 2 and w not in skip: word_freq[w] += 1
    return jsonify({
        "count": len(results), "avg_score": round(sum(scores)/len(scores), 1),
        "best": max(results, key=lambda r: r["score"]),
        "worst": min(results, key=lambda r: r["score"]),
        "structures": dict(struct_count.most_common(10)),
        "top_words": dict(word_freq.most_common(25)),
        "results": sorted(results, key=lambda r: r["score"], reverse=True),
    })

@app.route("/api/ti/ab_battle", methods=["POST"])
def ti_ab_battle():
    data = request.json or {}
    title_a = data.get("title_a", "")[:300]
    title_b = data.get("title_b", "")[:300]
    language = data.get("language", "English")[:50]
    if not title_a or not title_b:
        return jsonify({"error": "Both titles required"}), 400
    prompt = f"""You are the ultimate 2026 YouTube Algorithm Simulator.
Two titles are fighting for impressions. Evaluate head-to-head based on CTR psychological triggers (Curiosity Gap, Negative Framing, FOMO, Authority, Specific Data).
Title A: "{title_a}"
Title B: "{title_b}"
Language: {language}
Output MUST be valid JSON (no markdown):
{{"winner":"A or B","ctr_delta":"+X% CTR difference","reasoning":"Why winner commands more clicks","breakdown_a":"Strengths/weaknesses of A","breakdown_b":"Strengths/weaknesses of B"}}"""
    try:
        result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
        clean = result.replace("```json","").replace("```","").strip()
        m = _re.search(r'\{.*\}', clean, _re.DOTALL)
        if m:
            raw = m.group()
            raw = _re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
            try:
                return jsonify({"battle": json.loads(raw)})
            except Exception:
                pass
        # Fallback: extract structured info from free text
        winner = "A" if "title a" in clean.lower() or '"winner":"a"' in clean.lower() else "B"
        return jsonify({"battle": {
            "winner": winner,
            "ctr_delta": "AI analysis unavailable — response was unstructured",
            "reasoning": clean[:500] if clean else "AI temporarily unavailable",
            "breakdown_a": "See reasoning above",
            "breakdown_b": "See reasoning above"
        }})
    except Exception as e:
        return jsonify({"error": f"AI Battle Failed: {e}"}), 500

@app.route("/api/ti/hook_analyze", methods=["POST"])
def ti_hook_analyze():
    data = request.json or {}
    hook = data.get("hook", "")[:2500]
    title = data.get("title", "")[:300]
    language = data.get("language", "English")[:50]
    if not hook or len(hook) < 20:
        return jsonify({"error": "Script too short to analyze"}), 400
    prompt = f"""You are an elite YouTube Retention Architect who has studied thousands of videos with >70% AVD.
Your goal: tear apart the FIRST 30 SECONDS (the hook) and rebuild it for maximum psychological grip.
VIDEO TITLE: "{title}"
TARGET LANGUAGE: {language}
SCRIPT HOOK: "{hook}"
Provide EXACTLY this structure (Markdown):
### 📊 Predicted 30s Retention: [XX]%
### 🧠 Psychological Grip Analysis
- **Curiosity Loop:** [Is core question established immediately?]
- **Pacing & Rhythm:** [Too slow? Too fast?]
- **The "So What" Factor:** [Why should viewer care right now?]
### ⚠️ Retention Killers (Drop-off Points)
- [Exact sentence where viewers click away and why]
### ✍️ The "Viral" Re-Write (Script + B-Roll)
[Rewrite punch, visceral, fast-paced. AUDIO | VISUAL format]
### 💡 Secret Sauce
[1 advanced psychological tip to hold attention from second 30-60]"""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"analysis": result})

@app.route("/api/ti/seo_optimize", methods=["POST"])
def ti_seo_optimize():
    """SEO Optimizer v2: with real YouTube autocomplete data."""
    import requests as _req
    data = request.json or {}
    title = (data.get("title") or "").strip()[:300]
    if not title:
        return jsonify({"error": "Title required"}), 400
    language = data.get("language", "pt")[:5]
    # Fetch real YouTube autocomplete suggestions
    suggestions = []
    try:
        # Get autocomplete for the main topic words
        import re as _re5
        main_words = " ".join(w for w in title.split() if len(w) > 3)[:60]
        r = _req.get("https://suggestqueries.google.com/complete/search", params={
            "client":"youtube","ds":"yt","q":main_words,"hl":language}, timeout=8,
            headers={"User-Agent":"Mozilla/5.0"})
        m = _re5.search(r'\((.+)\)$', r.text)
        if m:
            parsed = json.loads(m.group(1))
            suggestions = [s[0] for s in parsed[1]][:10] if len(parsed) > 1 else []
    except: pass
    sug_text = "\n".join(f"- {s}" for s in suggestions) if suggestions else "No autocomplete data available"
    prompt = f"""You are a YouTube SEO expert with access to REAL search data.
TITLE TO OPTIMIZE: "{title}"
LANGUAGE: {language}

REAL YOUTUBE SEARCH SUGGESTIONS (what people actually search):
{sug_text}

Provide:
### 1. SEO SCORE (0-100) with breakdown
### 2. KEYWORD ANALYSIS
- Primary keyword detected
- Secondary keywords
- Missing high-value keywords (from the autocomplete data above)
### 3. OPTIMIZED TITLE VERSIONS
- 5 SEO-optimized versions (70-100 chars) incorporating real search terms
- Each with estimated search volume: HIGH/MEDIUM/LOW
### 4. TAGS SUGGESTION (15 tags for YouTube)
### 5. DESCRIPTION TEMPLATE (first 2 lines with keywords)
### 6. SEARCH INTENT MATCH
- What search intent does this title satisfy?
- How to better match viewer intent"""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"seo": result, "title": title, "autocomplete": suggestions})


@app.route("/api/ti/channel_strategy", methods=["POST"])
def ti_channel_strategy():
    """Channel Strategy v2: Data-driven with real YouTube viral intelligence."""
    import requests as _req
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:300]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    language = data.get("language", "English")[:50]
    channel_name = data.get("channel_name", "")[:100]
    current_titles = data.get("current_titles", "")[:1000]
    # STEP 1: Fetch REAL viral data from YouTube
    viral_data = []
    try:
        from core.api_keys import load_api_key
        yt_key = data.get("yt_api_key") or load_api_key("youtube")
        if yt_key:
            from datetime import timedelta
            pub_after = (datetime.utcnow() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
            sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
                "part":"snippet","q":niche,"type":"video","order":"viewCount",
                "maxResults":30,"publishedAfter":pub_after,"key":yt_key}, timeout=12)
            video_ids = [i["id"]["videoId"] for i in sr.json().get("items",[]) if "videoId" in i.get("id",{})]
            if video_ids:
                stats_r = _req.get("https://www.googleapis.com/youtube/v3/videos", params={
                    "part":"snippet,statistics","id":",".join(video_ids[:25]),"key":yt_key}, timeout=12)
                for item in stats_r.json().get("items",[]):
                    snip=item.get("snippet",{}); stat=item.get("statistics",{})
                    views=int(stat.get("viewCount",0))
                    pub=snip.get("publishedAt","")
                    hours_ago=1
                    try:
                        from datetime import datetime as _dt
                        pub_dt=_dt.strptime(pub[:19],"%Y-%m-%dT%H:%M:%S")
                        hours_ago=max(1,(_dt.utcnow()-pub_dt).total_seconds()/3600)
                    except: pass
                    viral_data.append({"title":snip.get("title",""),"channel":snip.get("channelTitle",""),
                        "views":views,"vph":round(views/hours_ago,1),"subs":"","published":pub[:10]})
            viral_data.sort(key=lambda x: x["vph"], reverse=True)
    except Exception as e:
        print(f"  [Strategy] YouTube data fetch failed: {e}")
    viral_context = ""
    if viral_data:
        viral_context = "\n\nREAL YOUTUBE DATA (last 14 days, sorted by VPH - Views Per Hour):\n"
        for v in viral_data[:20]:
            viral_context += f"- [{v['vph']:.0f} VPH] {v['title']} | by {v['channel']} | {v['views']:,} views\n"
    prompt = f"""You are the world's #1 YouTube Channel Strategist. You build channels from 0 to 1M subscribers.
NICHE: {niche}
{f"CHANNEL: {channel_name}" if channel_name else ""}
LANGUAGE: {language}
{f"CURRENT TITLES: {current_titles}" if current_titles else ""}{viral_context}

Based on the REAL YouTube viral data above, create a COMPLETE channel strategy:

### 1. NICHE POSITIONING
- Where exactly to position in the market
- What makes this channel DIFFERENT from the 20 viral channels above
- The "Blue Ocean" angle no one is exploiting

### 2. CONTENT PILLARS (4 pillars)
For each pillar: name, description, % of content, example titles (3), why it works

### 3. VIRAL TITLE FORMULAS
- Extract the TOP 5 title structures from the viral data above
- For each structure: the pattern, why it gets clicks, 3 adapted examples for YOUR channel
- Include the VPH data as proof

### 4. UNEXPLORED SUB-THEMES
- 5 sub-themes within this niche that have HIGH demand but LOW supply
- Evidence from the viral data (what's missing?)

### 5. POSTING STRATEGY
- Optimal frequency, best days/times, thumbnail style guide

### 6. GROWTH ROADMAP (0 to 100K)
- Month 1-3: Foundation (what to do)
- Month 3-6: Growth phase
- Month 6-12: Scale phase

### 7. TOP 15 VIDEO IDEAS (ready to produce)
For each: title (70-100 chars), thumbnail concept, estimated views, why NOW is the time

Be DATA-DRIVEN. Reference the viral videos above. Actionable, specific, no fluff."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"strategy": result, "niche": niche, "viral_data": viral_data[:20],
                    "data_driven": bool(viral_data), "channel": channel_name})

@app.route("/api/ti/strategy_remix", methods=["POST"])
def ti_strategy_remix():
    """Strategy Remix v2: SWAP tema/subtema with real YouTube viral data."""
    import requests as _req
    data = request.json or {}
    topic = (data.get("topic") or "").strip()[:300]
    if not topic:
        return jsonify({"error": "Topic required"}), 400
    language = data.get("language", "English")[:50]
    path_a = data.get("path_a", "Curiosity & Mystery")[:100]
    path_b = data.get("path_b", "Fear & Consequence")[:100]
    # Fetch real viral titles from YouTube for context
    viral_titles = []
    try:
        from core.api_keys import load_api_key
        yt_key = data.get("yt_api_key") or load_api_key("youtube")
        if yt_key:
            from datetime import timedelta
            published_after = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
                "part":"snippet","q":topic,"type":"video","order":"viewCount",
                "maxResults":20,"publishedAfter":published_after,"key":yt_key}, timeout=12)
            for item in sr.json().get("items",[]):
                viral_titles.append(item.get("snippet",{}).get("title",""))
    except: pass
    viral_ctx = ""
    if viral_titles:
        viral_ctx = "\n\nREAL VIRAL TITLES (last 7 days):\n" + "\n".join(f"- {t}" for t in viral_titles[:15])
    prompt = f"""You are the world's #1 YouTube Strategy Remix Architect. You SWAP themes and sub-themes to create NEW viral angles.
TOPIC: {topic}
LANGUAGE: {language}{viral_ctx}

## YOUR MISSION: Create TWO distinct remix strategies by SWAPPING structural elements.

### STRATEGY A: TEMA SWAP (Keep the sub-theme, CHANGE the main theme)
- Identify the TEMA (main theme) and SUBTEMA (specific angle) of the topic
- Keep SUBTEMA, transplant into COMPLETELY DIFFERENT TEMA
- Example: "Ancient Ruins + Conspiracy" -> keep "Conspiracy" change tema to "Modern Hospitals"

### STRATEGY B: SUBTEMA SWAP (Keep the main theme, CHANGE the sub-angle)
- Keep TEMA, find UNEXPLORED sub-angle NO ONE is covering
- Find the BLUE OCEAN perspective

For EACH STRATEGY provide:
1. **ORIGINAL DNA**: tema + subtema identified
2. **THE SWAP**: What was swapped and WHY
3. **COMPETITION CHECK**: Why LOW competition
4. **5 VIRAL TITLES** (70-100 chars, using structures from viral data above)
5. **THUMBNAIL CONCEPT**: 1-sentence visual description
6. **PATH A ({path_a}) vs PATH B ({path_b})**: Apply each psychological path

### BONUS: HYBRID STRATEGY (3 titles merging both swaps)

Be data-driven. Use viral title structures from above."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"remix": result, "topic": topic, "path_a": path_a, "path_b": path_b,
                    "viral_titles_used": viral_titles[:15], "data_driven": bool(viral_titles)})

@app.route("/api/ti/trend_scanner", methods=["POST"])
def ti_trend_scanner():
    data = request.json or {}
    category = data.get("category", "all")[:200]
    language = data.get("language", "English")[:50]
    prompt = f"""You are a YouTube trend analyst with real-time market awareness.
CATEGORY: {category if category != 'all' else 'All categories'}
LANGUAGE: {language}
Analyze current YouTube trends and provide:
1. TOP 10 TRENDING THEMES RIGHT NOW (name, why trending, demand 1-10, competition 1-10, best sub-angle, viral title example ≤100 chars)
2. EMERGING MICRO-NICHES (5 untapped niches with opportunity score, target audience, 2 title examples each)
3. DYING NICHES TO AVOID (3 niches losing traction and why)
4. CROSS-NICHE OPPORTUNITIES (3 combinations of trending themes)
5. VIRAL MECHANICS THAT WORK NOW (top 3 title structures, top 5 emotional triggers, optimal title length)
Be specific with real examples. Actionable insights only."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"trends": result, "category": category, "date": datetime.now().isoformat()})

@app.route("/api/ti/subniche", methods=["POST"])
def ti_subniche():
    data = request.json or {}
    theme = data.get("theme", "")[:300]
    language = data.get("language", "English")[:50]

    # Load niche intelligence for market data
    niche_context = ""
    try:
        from core.niche_database import NICHE_DATABASE, PSYCHOLOGICAL_TRIGGERS
        total = sum(len(v) for v in NICHE_DATABASE.values())
        categories = list(NICHE_DATABASE.keys())
        niche_context = f"""
MARKET INTELLIGENCE (analyze based on these proven categories):
Total tracked niches: {total} across {len(categories)} categories: {', '.join(categories)}

PSYCHOLOGICAL TRIGGERS ranked by CTR impact:
- Curiosity Gap: +35-60% CTR
- Fear/Loss Aversion: +25-50% CTR
- Social Proof/Authority: +15-30% CTR
- Novelty/Shock: +20-40% CTR
- Controversy: +30-55% CTR

HIGH RPM NICHES (prioritize these):
- Finance & Wealth: $8-25 RPM
- AI & Tech: $5-15 RPM
- Health & Medical: $4-12 RPM
- Psychology: $4-11 RPM
- History: $3.5-8 RPM"""
    except ImportError:
        niche_context = ""

    prompt = f"""You are a master YouTube Blue Ocean Strategist with 10 years of data-driven niche analysis.
{f'MAIN THEME: {theme}' if theme else 'Analyze ALL trending YouTube themes and find the best opportunities.'}
TARGET LANGUAGE: {language}
{niche_context}

Using Blue Ocean Strategy + Supply/Demand analysis, find 8 UNTAPPED subniches with HIGH opportunity.

Return ONLY a valid JSON array (no markdown, no code blocks, raw JSON only).
Each object MUST have ALL of these fields exactly:
{{
  "name": "Specific subniche name",
  "demand": 8,
  "supply": 3,
  "opportunity": 5,
  "blue_ocean_score": 78,
  "rpm_estimate": "$4-8/1000 views",
  "target_audience": "Specific demographic description",
  "audience_pain": "The specific problem they want solved",
  "content_angle": "Unique angle that hasn't been done to death",
  "why_now": "Why this is trending RIGHT NOW in 2025",
  "example_titles": [
    "Title 1 (70-100 chars with CAPS power word)",
    "Title 2 (70-100 chars with emotional trigger)",
    "Title 3 (70-100 chars with specific number)"
  ],
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4"],
  "estimated_views_per_video": "50K-500K",
  "monetization_methods": "AdSense + affiliate + sponsorships",
  "first_video_idea": "Complete idea for the FIRST video to make in this subniche that maximizes first impression",
  "micro_niche_count": 4
}}

Sort by blue_ocean_score descending (100=max opportunity, 0=completely saturated)."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    try:
        m = _re.search(r'\[.*\]', result, _re.DOTALL)
        niches = json.loads(m.group() if m else result)
        niches.sort(key=lambda x: x.get("blue_ocean_score", x.get("opportunity", 0)), reverse=True)
        return jsonify({"niches": niches, "theme": theme})
    except:
        return jsonify({"niches": [], "raw": result, "theme": theme})

@app.route("/api/ti/micronicho", methods=["POST"])
def ti_micronicho():
    """Discover 6 micro-niches within a sub-niche — 3rd level of the niche strategy."""
    data = request.json or {}
    subniche = (data.get("subniche") or data.get("theme", "")).strip()[:300]
    parent_niche = data.get("parent_niche", "").strip()[:200]
    language = data.get("language", "English")[:50]
    if not subniche:
        return jsonify({"error": "Sub-niche name required"}), 400

    prompt = f"""You are an elite YouTube Micro-Niche Intelligence Analyst.

PARENT NICHE: {parent_niche or subniche}
SUB-NICHE TO DRILL INTO: {subniche}
TARGET LANGUAGE: {language}

Go one level DEEPER. Find 6 ULTRA-SPECIFIC micro-niches within "{subniche}".

A micro-niche is laser-focused with:
- Very low competition (few established channels <10k subs)
- Highly passionate niche audience
- Clear content roadmap (100+ video ideas possible)
- Specific monetization angle

Return ONLY a valid JSON array (no markdown, no code blocks).
Each micro-niche object MUST have ALL these fields:
{{
  "name": "Ultra-specific micro-niche name",
  "parent_subniche": "{subniche}",
  "demand": 7,
  "supply": 2,
  "blue_ocean_score": 85,
  "competition_level": "Very Low",
  "target_avatar": "Exact person: age, job, problem, desire",
  "unique_angle": "What makes THIS micro-niche different from the sub-niche",
  "content_gap": "What content doesn't exist yet that the audience desperately wants",
  "first_3_videos": [
    "Video 1 title — introduction/hook that grabs immediately",
    "Video 2 title — deep dive that establishes authority",
    "Video 3 title — controversy/secret with viral potential"
  ],
  "viral_title_formula": "The specific formula that works for this micro-niche",
  "estimated_rpm": "$X-Y",
  "monthly_search_volume": "low/medium/high",
  "time_to_monetize": "X-Y months",
  "keywords": ["keyword1", "keyword2", "keyword3"]
}}

Sort by blue_ocean_score descending. Focus on ULTRA-SPECIFIC topics, not broad ones."""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    try:
        m = _re.search(r'\[.*\]', result, _re.DOTALL)
        micronichos = json.loads(m.group() if m else result)
        micronichos.sort(key=lambda x: x.get("blue_ocean_score", 0), reverse=True)
        return jsonify({"micronichos": micronichos, "subniche": subniche, "parent_niche": parent_niche})
    except:
        return jsonify({"micronichos": [], "raw": result, "subniche": subniche})


@app.route("/api/ti/niche_strategy_complete", methods=["POST"])
def ti_niche_strategy_complete():
    """Generate complete 3-level niche strategy: Niche > Sub-nichos > Micro-nichos in one call."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:300]
    language = data.get("language", "English")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400

    prompt = f"""You are a master YouTube Niche Architect. Generate a complete 3-level niche strategy map.

MAIN NICHE: {niche}
TARGET LANGUAGE: {language}

Create a COMPLETE strategy tree:
- 1 Main Niche analysis
- 5 Sub-niches (level 2), each with blue_ocean_score and first_video_idea
- 3 Micro-niches per sub-niche = 15 total (level 3)

Return ONLY valid JSON (no markdown, no code blocks):
{{
  "main_niche": {{
    "name": "{niche}",
    "market_size": "description of market size",
    "avg_rpm": "$X-Y",
    "competition": "Low/Medium/High",
    "opportunity_summary": "2-3 sentence market analysis with specific numbers",
    "best_content_types": ["format1", "format2", "format3"]
  }},
  "subniches": [
    {{
      "name": "Sub-niche name",
      "blue_ocean_score": 82,
      "demand": 8,
      "supply": 3,
      "rpm_estimate": "$X-Y",
      "content_angle": "unique angle",
      "first_video_idea": "specific first video to make",
      "example_titles": ["Title 1", "Title 2", "Title 3"],
      "micronichos": [
        {{
          "name": "Micro-niche name",
          "blue_ocean_score": 91,
          "target_avatar": "exact person: age, job, desire",
          "unique_angle": "why this is different",
          "first_3_videos": ["Video 1", "Video 2", "Video 3"],
          "estimated_rpm": "$X-Y",
          "competition_level": "Very Low"
        }},
        {{...}},
        {{...}}
      ]
    }},
    {{...}},
    {{...}},
    {{...}},
    {{...}}
  ]
}}

Make everything SPECIFIC and ACTIONABLE — no generic answers. Sort sub-niches by blue_ocean_score desc."""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    try:
        m = _re.search(r'\{.*\}', result, _re.DOTALL)
        strategy = json.loads(m.group() if m else result)
        return jsonify({"strategy": strategy, "niche": niche, "language": language})
    except:
        return jsonify({"strategy": None, "raw": result, "niche": niche})


@app.route("/api/ti/subniche_validate", methods=["POST"])
def ti_subniche_validate():
    """Validate subniche with real YouTube Data API metrics."""
    import requests as _req
    data = request.json or {}
    subniche_name = data.get("name", "")
    keywords = data.get("keywords", [])
    if not subniche_name:
        return jsonify({"error": "No subniche name"}), 400
    from core.api_keys import load_api_key
    yt_key = load_api_key("youtube")
    if not yt_key:
        return jsonify({"error": "YouTube API key not configured. Go to Settings."}), 400
    search_q = subniche_name + " " + " ".join(keywords[:3])
    published_after = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
            "part":"snippet","q":search_q,"type":"video","order":"viewCount",
            "maxResults":15,"publishedAfter":published_after,"key":yt_key}, timeout=10)
        search_data = sr.json()
        if "error" in search_data:
            return jsonify({"error": search_data["error"].get("message","API error")}), 400
        video_ids = [i["id"]["videoId"] for i in search_data.get("items",[]) if "videoId" in i.get("id",{})]
        if not video_ids:
            return jsonify({"channels":[],"videos":[],"subniche":subniche_name})
        stats_r = _req.get("https://www.googleapis.com/youtube/v3/videos", params={
            "part":"snippet,statistics,contentDetails","id":",".join(video_ids),"key":yt_key}, timeout=10)
        stats_data = stats_r.json()
        videos = []
        channel_map = {}
        for item in stats_data.get("items",[]):
            snip = item.get("snippet",{})
            stats = item.get("statistics",{})
            cid = snip.get("channelId","")
            views = int(stats.get("viewCount",0))
            pub = snip.get("publishedAt","")
            hours_ago = 1
            try:
                pub_dt = datetime.strptime(pub[:19],"%Y-%m-%dT%H:%M:%S")
                hours_ago = max(1,(datetime.utcnow()-pub_dt).total_seconds()/3600)
            except: pass
            vph = round(views/hours_ago,1)
            videos.append({"id":item["id"],"title":snip.get("title",""),"channel_name":snip.get("channelTitle",""),
                           "channel_id":cid,"views":views,"vph":vph,"published":pub[:10],
                           "thumbnail":snip.get("thumbnails",{}).get("medium",{}).get("url","")})
            if cid not in channel_map:
                channel_map[cid] = {"id":cid,"name":snip.get("channelTitle",""),"videos_found":0,"total_views":0,"avg_vph":0}
            channel_map[cid]["videos_found"] += 1
            channel_map[cid]["total_views"] += views
        if channel_map:
            ch_r = _req.get("https://www.googleapis.com/youtube/v3/channels", params={
                "part":"statistics,snippet","id":",".join(list(channel_map.keys())[:10]),"key":yt_key}, timeout=10)
            for ch in ch_r.json().get("items",[]):
                cid = ch["id"]
                if cid in channel_map:
                    cs = ch.get("statistics",{})
                    channel_map[cid].update({"subscribers":int(cs.get("subscriberCount",0)),
                        "total_channel_views":int(cs.get("viewCount",0)),"video_count":int(cs.get("videoCount",0)),
                        "thumbnail":ch.get("snippet",{}).get("thumbnails",{}).get("default",{}).get("url","")})
        videos.sort(key=lambda v: v["vph"],reverse=True)
        channels = sorted(channel_map.values(),key=lambda c:c.get("avg_vph",0),reverse=True)
        avg_vph = round(sum(v["vph"] for v in videos)/max(1,len(videos)),1)
        return jsonify({"subniche":subniche_name,"videos":videos,"channels":channels,
                        "total_videos":len(videos),"total_channels":len(channels),"avg_vph":avg_vph})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ti/niche_list", methods=["GET"])
def ti_niche_list():
    try:
        import core.niche_database as ndb
        return jsonify({"niches": ndb.NICHE_DATABASE})
    except:
        default = {
            "Documentary": ["History & Ancient Civilizations","Ocean & Deep Sea","Space & Universe","Megaprojects & Construction"],
            "Education": ["Health & Psychology","Science Mysteries","True Crime","Finance & Wealth"],
            "Lifestyle": ["Abandoned Places","Extreme Survival","Underground Architecture","Lost Civilizations"],
        }
        return jsonify({"niches": default})

@app.route("/api/ti/thumb_prompt", methods=["POST"])
def ti_thumb_prompt():
    data = request.json or {}
    title = data.get("title", "")[:300]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are an elite YouTube Thumbnail Director in 2026.
Design the perfect high-CTR thumbnail for: "{title}"
Generate 3 distinct Midjourney/Ideogram v2 prompts following these rules:
1. Low visual clutter (max 3 elements)
2. High contrast, cinematic lighting, dramatic mood
3. Leave a "Curiosity Gap" (show mystery/danger, not the answer)
Output MUST be valid JSON (no markdown):
{{"prompts":[{{"style":"e.g., Hyper-realistic Documentary","visuals":"Describe exactly what is shown","text_overlay":"Optional 1-3 words","midjourney_prompt":"Exact English prompt --ar 16:9 --v 6.0"}},{{}},{{"..."}}]}}"""
    try:
        result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
        clean = result.replace("```json","").replace("```","").strip()
        m = _re.search(r'\{.*\}', clean, _re.DOTALL)
        if m:
            raw = m.group()
            raw = _re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
            try:
                return jsonify({"thumbs": json.loads(raw)})
            except Exception:
                pass
        # Fallback: synthesize a basic prompt from the title
        return jsonify({"thumbs": {"prompts": [
            {"style": "Cinematic Documentary",
             "visuals": f"Dramatic wide shot illustrating the concept of: {title}",
             "text_overlay": title[:20] + "...",
             "midjourney_prompt": f"cinematic dramatic thumbnail, {title}, dark atmosphere, high contrast, 8k --ar 16:9 --v 6.0"},
            {"style": "Mystery & Curiosity",
             "visuals": f"Close-up mysterious element related to: {title}",
             "text_overlay": "SHOCKING",
             "midjourney_prompt": f"mysterious dramatic photo, curiosity gap, dark background, {title[:50]}, photorealistic --ar 16:9 --v 6.0"},
            {"style": "Human Reaction",
             "visuals": "Person with shocked/amazed expression looking at camera",
             "text_overlay": "REVEALED",
             "midjourney_prompt": f"person shocked expression, thumbnail style, dramatic lighting, {title[:40]} --ar 16:9 --v 6.0"},
        ]}})
    except Exception as e:
        return jsonify({"error": f"Thumb Gen Failed: {e}"}), 500

@app.route("/api/ti/vision_audit", methods=["POST"])
def ti_vision_audit():
    """Gemini Pro Vision — brutally analyzes a thumbnail image for CTR flaws."""
    data = request.json or {}
    image_b64 = data.get("image", "")
    title = (data.get("title") or "").strip()[:200]
    if not image_b64:
        return jsonify({"error": "No image provided"}), 400
    if not title:
        return jsonify({"error": "Title required for context"}), 400
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]
    prompt = f"""You are the world's most brutal YouTube Thumbnail Designer and CTR Expert.
I am providing a YouTube thumbnail image.
Title Context: "{title}"
Analyze rigorously:
### 🖼️ Instant Visual Impression (0.5s Rule)
### 🎯 Composition & Focal Point
### 🎨 Color Psychology & Contrast
### 🔤 Typography & Readability (if applicable)
### 💡 The "Curiosity Gap" Verdict
### 🛠️ 3 Brutal Suggestions to Improve CTR
1. [Suggestion 1]
2. [Suggestion 2]
3. [Suggestion 3]
Be highly critical. If it looks amateur, say exactly why."""
    try:
        result = _ask_gemini_gcg(prompt, data.get("ai_api_key"), image_b64=image_b64)
        return jsonify({"audit": result})
    except Exception as e:
        return jsonify({"error": f"Vision Audit failed: {e}"}), 500

# ── YouTube Intelligence (Newborn Virals + Sync Channels) ─────────────────

@app.route("/api/ti/youtube/newborn_virals", methods=["POST"])
def ti_newborn_virals():
    """Scans for extremely viral videos from NEW or SMALL channels."""
    import requests as _req
    data = request.json or {}
    query = data.get("query", "")
    from core.api_keys import load_api_key
    key = data.get("yt_api_key") or load_api_key("youtube")
    if not key:
        return jsonify({"error": "YouTube API key not set. Go to Settings."}), 400
    published_after = (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
            "part":"snippet","q":query,"type":"video","order":"viewCount",
            "maxResults":30,"publishedAfter":published_after,"key":key}, timeout=15)
        video_ids = [i["id"]["videoId"] for i in sr.json().get("items",[]) if "videoId" in i.get("id",{})]
        if not video_ids:
            return jsonify({"virals":[],"query":query})
        stats_r = _req.get("https://www.googleapis.com/youtube/v3/videos", params={
            "part":"snippet,statistics","id":",".join(video_ids),"key":key}, timeout=15)
        videos_data = []
        channel_ids = []
        for item in stats_r.json().get("items",[]):
            snip = item.get("snippet",{}); stat = item.get("statistics",{})
            cid = snip.get("channelId","")
            pub = snip.get("publishedAt","")
            views = int(stat.get("viewCount",0))
            hours_ago = 1
            try:
                pub_dt = datetime.strptime(pub[:19],"%Y-%m-%dT%H:%M:%S")
                hours_ago = max(1,(datetime.utcnow()-pub_dt).total_seconds()/3600)
            except: pass
            videos_data.append({"video_id":item["id"],"title":snip.get("title",""),
                "channel_id":cid,"channel_name":snip.get("channelTitle",""),
                "views":views,"vph":round(views/hours_ago,1),"published":pub[:10],
                "thumbnail":snip.get("thumbnails",{}).get("medium",{}).get("url","")})
            if cid and cid not in channel_ids: channel_ids.append(cid)
        channels_info = {}
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i:i+50]
            ch_r = _req.get("https://www.googleapis.com/youtube/v3/channels", params={
                "part":"snippet,statistics","id":",".join(batch),"key":key}, timeout=15)
            for ch in ch_r.json().get("items",[]):
                stat = ch.get("statistics",{}); snip = ch.get("snippet",{})
                channels_info[ch["id"]] = {
                    "subscribers":int(stat.get("subscriberCount",0)),
                    "video_count":int(stat.get("videoCount",0)),
                    "published_at":snip.get("publishedAt","")[:10],
                    "thumbnail":snip.get("thumbnails",{}).get("default",{}).get("url","")}
        newborns = []
        for v in videos_data:
            c = channels_info.get(v["channel_id"])
            if not c: continue
            if c["video_count"] <= 35 and v["vph"] >= 50:
                v["channel_stats"] = c
                newborns.append(v)
        newborns.sort(key=lambda x: x["vph"],reverse=True)
        return jsonify({"virals":newborns,"query":query})
    except Exception as e:
        err = str(e)
        if "API key" in err or "keyInvalid" in err or "400 Client" in err or "403" in err:
            return jsonify({"error": "YouTube API key inválida."}), 400
        return jsonify({"error": err}), 500

@app.route("/api/ti/youtube/sync_channels", methods=["POST"])
def ti_sync_channels():
    """Auto-Sync: checks tracked channels for explosive new videos (High VPH)."""
    import requests as _req
    data = request.json or {}
    channel_ids = data.get("channels", [])
    from core.api_keys import load_api_key
    key = data.get("yt_api_key") or load_api_key("youtube")
    if not key:
        return jsonify({"error": "YouTube API key not set"}), 400
    if not channel_ids:
        return jsonify({"sync_results": []})
    try:
        results = []
        for cid in channel_ids[:20]:
            sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
                "part":"snippet","channelId":cid,"type":"video","order":"date","maxResults":2,"key":key}, timeout=10)
            video_ids = [i["id"]["videoId"] for i in sr.json().get("items",[]) if "videoId" in i.get("id",{})]
            if not video_ids: continue
            stats_r = _req.get("https://www.googleapis.com/youtube/v3/videos", params={
                "part":"snippet,statistics","id":",".join(video_ids),"key":key}, timeout=10)
            for item in stats_r.json().get("items",[]):
                snip = item.get("snippet",{}); stat = item.get("statistics",{})
                pub = snip.get("publishedAt",""); views = int(stat.get("viewCount",0))
                hours_ago = 1
                try:
                    pub_dt = datetime.strptime(pub[:19],"%Y-%m-%dT%H:%M:%S")
                    hours_ago = max(1,(datetime.utcnow()-pub_dt).total_seconds()/3600)
                except: pass
                vph = round(views/hours_ago,1)
                if hours_ago <= 48 and vph > 10:
                    results.append({"channel_id":cid,"channel_name":snip.get("channelTitle",""),
                        "video_id":item["id"],"title":snip.get("title",""),
                        "vph":vph,"views":views,"hours_ago":int(hours_ago)})
        results.sort(key=lambda x: x["vph"],reverse=True)
        return jsonify({"sync_results": results})
    except Exception as e:
        err = str(e)
        if "API key" in err or "keyInvalid" in err or "400 Client" in err or "403" in err:
            return jsonify({"error": "YouTube API key inválida."}), 400
        return jsonify({"error": err}), 500

@app.route("/api/ti/youtube/save_key", methods=["POST"])
def ti_save_yt_key():
    data = request.json or {}
    key = data.get("key", "").strip()
    if not key:
        return jsonify({"error": "No key provided"}), 400
    from core.api_keys import save_api_key
    save_api_key("youtube", key)
    return jsonify({"status": "ok"})

@app.route("/api/ti/youtube/channel", methods=["POST"])
def ti_yt_channel():
    """Analyze a YouTube channel with real YouTube Data API."""
    from core.youtube_api import get_channel_info, get_channel_videos
    from collections import Counter
    data = request.json or {}
    channel_input = data.get("channel", "")
    from core.api_keys import load_api_key
    key = data.get("yt_api_key") or load_api_key("youtube")
    if not key:
        return jsonify({"error": "YouTube API key não configurado. Salve a chave na aba YouTube Scanner."}), 400
    try:
        info = get_channel_info(channel_input, key)
    except Exception as e:
        err = str(e)
        if "API key" in err or "keyInvalid" in err or "400" in err or "403" in err:
            return jsonify({"error": "YouTube API key inválida. Verifique a chave na aba YouTube Scanner."}), 400
        return jsonify({"error": err}), 500
    if not info:
        return jsonify({"error": f"Canal não encontrado: {channel_input}"}), 404
    videos = get_channel_videos(info["id"], key, max_videos=int(data.get("max_videos", 30)))
    vph_list = [v["vph"] for v in videos if v["vph"] > 0]
    views_list = [v["views"] for v in videos]
    eng_list = [v["engagement"] for v in videos if v["engagement"] > 0]
    avg_vph = round(sum(vph_list) / max(len(vph_list), 1), 1)
    avg_views = round(sum(views_list) / max(len(views_list), 1))
    for v in videos:
        v["vph_multiplier"] = round(v["vph"] / max(avg_vph, 0.1), 1)
        v["outlier_score"] = round(v["views"] / max(avg_views, 1), 1)
    word_freq = Counter()
    skip = {"the","a","an","is","in","on","of","and","to","that","this","for","with","are","was","you","your","it","o","a","e","os","as","de","da","do","um","uma"}
    for v in videos:
        for w in v["title"].lower().split():
            w = w.strip(".,!?;:'\"()-[]|#")
            if w and len(w) > 2 and w not in skip:
                word_freq[w] += 1
    return jsonify({
        "channel": info,
        "videos": sorted(videos, key=lambda v: v["vph"], reverse=True),
        "metrics": {
            "avg_vph": avg_vph,
            "max_vph": max(vph_list) if vph_list else 0,
            "avg_views": avg_views,
            "avg_engagement": round(sum(eng_list) / max(len(eng_list), 1), 2),
            "total_analyzed": len(videos),
        },
        "top_words": dict(word_freq.most_common(25)),
    })

@app.route("/api/ti/youtube/niche", methods=["POST"])
def ti_yt_niche():
    """Deep niche analysis with real YouTube data."""
    from core.youtube_api import analyze_niche
    data = request.json or {}
    query = data.get("query", "")
    region = data.get("region", "US")
    from core.api_keys import load_api_key
    key = data.get("yt_api_key") or load_api_key("youtube")
    if not key:
        return jsonify({"error": "YouTube API key não configurado"}), 400
    if not query:
        return jsonify({"error": "Nenhuma query fornecida"}), 400
    try:
        result = analyze_niche(query, key, region=region)
    except Exception as e:
        err = str(e)
        if "API key" in err or "keyInvalid" in err or "400" in err or "403" in err:
            return jsonify({"error": "YouTube API key inválida."}), 400
        return jsonify({"error": err}), 500
    return jsonify(result)

@app.route("/api/ti/youtube/trending", methods=["POST"])
def ti_yt_trending():
    """Get trending/popular videos from YouTube."""
    from core.youtube_api import get_most_popular, search_trending
    data = request.json or {}
    query = data.get("query", "")
    region = data.get("region", "US")
    category = data.get("category_id", "")
    from core.api_keys import load_api_key
    key = data.get("yt_api_key") or load_api_key("youtube")
    if not key:
        return jsonify({"error": "YouTube API key não configurado"}), 400
    try:
        if query:
            week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            videos = search_trending(query, key, max_results=25,
                                     published_after=week_ago, region=region,
                                     category_id=category or None)
        else:
            videos = get_most_popular(key, region=region,
                                      category_id=category or None, max_results=25)
    except Exception as e:
        err = str(e)
        if "API key" in err or "keyInvalid" in err or "400" in err or "403" in err:
            return jsonify({"error": "YouTube API key inválida."}), 400
        return jsonify({"error": err}), 500
    return jsonify({"videos": videos, "query": query, "region": region})

@app.route("/api/ti/youtube/compare", methods=["POST"])
def ti_yt_compare():
    """Compare multiple YouTube channels side by side."""
    from core.youtube_api import compare_channels
    data = request.json or {}
    channels = data.get("channels", [])
    from core.api_keys import load_api_key
    key = data.get("yt_api_key") or load_api_key("youtube")
    if not key:
        return jsonify({"error": "YouTube API key não configurado"}), 400
    if not channels:
        return jsonify({"error": "Nenhum canal fornecido"}), 400
    try:
        results = compare_channels(channels, key)
    except Exception as e:
        err = str(e)
        if "API key" in err or "keyInvalid" in err or "400" in err or "403" in err:
            return jsonify({"error": "YouTube API key inválida."}), 400
        return jsonify({"error": err}), 500
    return jsonify({"comparisons": results})

@app.route("/api/ti/channels/update", methods=["POST"])
def ti_update_channel():
    """Update channel data — merges new subniches, keywords, structures, themes."""
    data = request.json or {}
    channel_id = str(data.get("id",""))
    updates = data.get("updates", {})
    channels = _ti_load_channels()
    for c in channels:
        if str(c.get("id","")) == channel_id:
            for key in ["subniches", "keywords", "titles"]:
                if key in updates and updates[key]:
                    existing = c.get(key, [])
                    c[key] = existing + [i for i in updates[key] if i not in existing]
            for key in ["niche", "micro_niche", "name", "url", "language"]:
                if key in updates and updates[key]:
                    c[key] = updates[key]
            if "reference_structures" in updates:
                existing = c.get("reference_structures", [])
                c["reference_structures"] = existing + [s for s in updates["reference_structures"] if s not in existing]
            if "trending_themes" in updates:
                existing = c.get("trending_themes", [])
                c["trending_themes"] = existing + [t for t in updates["trending_themes"] if t not in existing]
            c["last_updated"] = datetime.now().isoformat()
            break
    _ti_save_channels(channels)
    return jsonify({"status": "ok"})

@app.route("/api/ti/strategy_from_viral", methods=["POST"])
def ti_strategy_from_viral():
    data = request.json or {}
    video_title = data.get("title", "")
    niche = data.get("niche", "")
    language = data.get("language", "English")
    prompt = f"""You are a master YouTube Viral Architect. The user discovered a "Newborn Viral" video (exploded on a small new channel).
VIRAL TITLE: "{video_title}"
NICHE CONTEXT: "{niche}"
TARGET LANGUAGE: {language}
Extract the exact structural and psychological DNA of this title and CLONE IT into 3 different sub-niches.
Provide (Markdown):
### 🧬 The Viral DNA Extracted
- **Psychological Trigger:** Why exactly did people click this?
- **The Structural Syntax:** (e.g., "[Negative Word] + [Subject] + [Consequence]")
- **Thumbnail Hypothesis:** What visual likely accompanied this to make it viral?
### 🔄 The Cloning Process (3 New Subniches)
**1. [Subniche Name 1]** - Why this works here: [1 sentence]
- ⚡ Title 1/2/3: [Cloned 70-100 chars titles]
[same for 2 and 3]
Be brutal, aggressive, highly strategic. Use ALL CAPS power words."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"strategy": result, "original_title": video_title})

# ── My Channels (Title Intelligence) ──────────────────────────────────────
_TI_CHANNELS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ti_channels.json")

def _ti_load_channels():
    if os.path.exists(_TI_CHANNELS_FILE):
        with open(_TI_CHANNELS_FILE,"r",encoding="utf-8") as f: return json.load(f)
    return []

def _ti_save_channels(channels):
    with open(_TI_CHANNELS_FILE,"w",encoding="utf-8") as f: json.dump(channels,f,indent=2,ensure_ascii=False)

@app.route("/api/ti/channels", methods=["GET"])
def ti_get_channels():
    return jsonify({"channels": _ti_load_channels()})

@app.route("/api/ti/channels/add", methods=["POST"])
def ti_add_channel():
    data = request.json or {}
    channels = _ti_load_channels()
    # Use client-provided id if available, otherwise generate one
    ch_id = data.get("id") or f"ti_ch_{int(datetime.now().timestamp()*1000)}"
    ch = {"id": ch_id, "name": data.get("name",""), "url": data.get("url",""),
          "niche": data.get("niche",""), "micro_niche": data.get("micro_niche",""),
          "subniches": data.get("subniches",[]), "keywords": data.get("keywords",[]),
          "language": data.get("language","English"), "titles": data.get("titles",[]),
          "reference_structures": data.get("reference_structures",[]),
          "trending_themes": data.get("trending_themes",[]), "metrics": [],
          "created": datetime.now().isoformat()}
    channels.append(ch)
    _ti_save_channels(channels)
    return jsonify({"status": "ok", "id": ch_id, "channel": ch})

@app.route("/api/ti/channels/delete", methods=["POST"])
def ti_delete_channel():
    data = request.json or {}
    del_id = str(data.get("id",""))
    channels = [c for c in _ti_load_channels() if str(c.get("id","")) != del_id]
    _ti_save_channels(channels)
    return jsonify({"status":"ok"})

@app.route("/api/ti/channels/analyze", methods=["POST"])
def ti_analyze_channel():
    data = request.json or {}
    channel = data.get("channel", {})
    ref_text = "\n".join(f"- {s}" for s in data.get("reference_structures",[])[:15]) or "None"
    trend_text = "\n".join(f"- {t}" for t in data.get("trending_themes",[])[:10]) or "Use current YouTube trends"
    titles_text = "\n".join(f"- {t}" for t in channel.get("titles",[])[:20]) or "None"
    prompt = f"""You are the ultimate YouTube Trend Hacker and Blue Ocean Strategist.
NAME: {channel.get('name','')}
CURRENT NICHE: {channel.get('niche','')}
LANGUAGE: {channel.get('language','English')}
REFERENCE TITLE STRUCTURES:\n{ref_text}
TRENDING THEMES:\n{trend_text}
YOUR TITLES:\n{titles_text}
STEP 1: Reverse-engineer trending structures — identify the 3 most lethal, high-CTR structures.
STEP 2: Blue Ocean Discovery — find 5 NEW unsaturated subthemes (High Demand/Low Supply logic).
STEP 3: For each of the 5 subniches:
### 🌊 Subniche #[X]: [Name]
- Market Viability, Audience Hunger, Cloned Strategy
- 3 Viral Titles (70-100 chars, exact syntax from trending structures)
STEP 4: Which subniche has highest Opportunity Score? 3-step execution plan.
Be aggressive, data-driven, Markdown formatted."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"analysis": result, "channel": channel})

@app.route("/api/ti/channels/update_metrics", methods=["POST"])
def ti_update_metrics():
    data = request.json or {}
    cid = str(data.get("id",""))
    metrics = data.get("metrics", {})
    channels = _ti_load_channels()
    for c in channels:
        if str(c.get("id","")) == cid:
            if "metrics" not in c: c["metrics"] = []
            c["metrics"].append({**metrics,"date":datetime.now().isoformat()})
            c["metrics"] = c["metrics"][-50:]
            break
    _ti_save_channels(channels)
    return jsonify({"status":"ok"})

# ── Saved Trend Channels (browser-based, but also persisted server-side) ──
_SAVED_TREND_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_trend_channels.json")

@app.route("/api/ti/saved_channels", methods=["GET"])
def ti_get_saved_channels():
    try:
        with open(_SAVED_TREND_PATH,"r",encoding="utf-8") as f: return jsonify(json.load(f))
    except:
        return jsonify({"channels":[]})

@app.route("/api/ti/saved_channels", methods=["POST"])
def ti_save_channel():
    data = request.json or {}
    # Accept channel data from either root or nested "channel" key
    ch = data.get("channel", data)
    try:
        try:
            with open(_SAVED_TREND_PATH,"r",encoding="utf-8") as f: saved = json.load(f)
        except:
            saved = {"channels":[]}
        existing_ids = {c.get("id") for c in saved["channels"]}
        if ch.get("id") not in existing_ids:
            saved["channels"].append({
                "id": ch.get("id",""), "name": ch.get("name",""),
                "subscribers": ch.get("subscribers",0), "avg_vph": ch.get("avg_vph",0),
                "subniche": ch.get("subniche",""), "saved_at": datetime.now().isoformat(),
                "thumbnail": ch.get("thumbnail","")})
            with open(_SAVED_TREND_PATH,"w",encoding="utf-8") as f: json.dump(saved,f,indent=2)
        return jsonify({"status":"ok","total":len(saved["channels"])})
    except Exception as e:
        return jsonify({"error":str(e)}),500

# ── AI Engine Status ──────────────────────────────────────────────────────
@app.route("/api/ti/ai/status")
def ti_ai_status():
    try:
        from core.ai_engine import get_model_status
        return jsonify(get_model_status())
    except Exception as e:
        return jsonify({"health":"unknown","error":str(e),"providers":[]})

@app.route("/api/ti/ai/reset", methods=["POST"])
def ti_ai_reset():
    try:
        from core.ai_engine import clear_cooldowns
        clear_cooldowns()
        try:
            from core.ai_engine import clear_cache
            clear_cache()
        except: pass
        return jsonify({"status":"ok","message":"All cooldowns and cache cleared"})
    except Exception as e:
        return jsonify({"error":str(e)}),500


# ================================================================
# STRATEGY MEGA UPGRADE: 5 New Endpoints (NexLev-level features)
# ================================================================


# ---- Channel Resolution Helper ----
def _resolve_channel_id(channel_input, yt_key):
    """Resolve @handle, channel name, or channel ID to a valid channel ID."""
    import requests as _req
    channel_input = channel_input.strip()
    # Already a channel ID
    if channel_input.startswith("UC") and len(channel_input) >= 20:
        return channel_input
    # Try forHandle first (YouTube Data API v3)
    if channel_input.startswith("@"):
        handle = channel_input[1:]
        try:
            r = _req.get("https://www.googleapis.com/youtube/v3/channels", params={
                "part": "id", "forHandle": handle, "key": yt_key}, timeout=10)
            items = r.json().get("items", [])
            if items:
                return items[0]["id"]
        except: pass
    # Fallback: search
    query = channel_input.lstrip("@")
    try:
        r = _req.get("https://www.googleapis.com/youtube/v3/search", params={
            "part": "snippet", "q": query, "type": "channel", "maxResults": 5, "key": yt_key}, timeout=10)
        for item in r.json().get("items", []):
            cid = item.get("id", {}).get("channelId", "")
            if cid:
                return cid
    except: pass
    return None

@app.route("/api/ti/niche_overview", methods=["POST"])
def ti_niche_overview():
    """Niche Overview: What's ACTUALLY viral in this niche RIGHT NOW with real YouTube data."""
    import requests as _req
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:300]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    from core.api_keys import load_api_key
    yt_key = data.get("yt_api_key") or load_api_key("youtube")
    if not yt_key:
        return jsonify({"error": "YouTube API key required. Go to Settings."}), 400
    try:
        from datetime import timedelta
        pub_after = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Fetch viral videos
        sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
            "part":"snippet","q":niche,"type":"video","order":"viewCount",
            "maxResults":50,"publishedAfter":pub_after,"key":yt_key}, timeout=15)
        video_ids = [i["id"]["videoId"] for i in sr.json().get("items",[]) if "videoId" in i.get("id",{})]
        videos = []
        channel_ids = set()
        if video_ids:
            for batch_start in range(0, len(video_ids), 50):
                batch = video_ids[batch_start:batch_start+50]
                stats_r = _req.get("https://www.googleapis.com/youtube/v3/videos", params={
                    "part":"snippet,statistics,contentDetails","id":",".join(batch),"key":yt_key}, timeout=15)
                for item in stats_r.json().get("items",[]):
                    snip=item.get("snippet",{}); stat=item.get("statistics",{})
                    views=int(stat.get("viewCount",0)); likes=int(stat.get("likeCount",0))
                    cid=snip.get("channelId","")
                    pub=snip.get("publishedAt","")
                    hours_ago=1
                    try:
                        pub_dt=datetime.strptime(pub[:19],"%Y-%m-%dT%H:%M:%S")
                        hours_ago=max(1,(datetime.utcnow()-pub_dt).total_seconds()/3600)
                    except: pass
                    vph = round(views/hours_ago,1)
                    videos.append({"id":item["id"],"title":snip.get("title",""),
                        "channel_id":cid,"channel_name":snip.get("channelTitle",""),
                        "views":views,"likes":likes,"vph":vph,"published":pub[:10],
                        "thumbnail":snip.get("thumbnails",{}).get("medium",{}).get("url",""),
                        "engagement":round(likes/max(1,views)*100,2)})
                    channel_ids.add(cid)
        videos.sort(key=lambda x: x["vph"], reverse=True)
        # Fetch channel info for top channels
        channels = {}
        ch_list = list(channel_ids)[:25]
        if ch_list:
            ch_r = _req.get("https://www.googleapis.com/youtube/v3/channels", params={
                "part":"snippet,statistics","id":",".join(ch_list),"key":yt_key}, timeout=12)
            for ch in ch_r.json().get("items",[]):
                stat=ch.get("statistics",{}); snip=ch.get("snippet",{})
                channels[ch["id"]] = {"name":snip.get("title",""),
                    "subscribers":int(stat.get("subscriberCount",0)),
                    "videos":int(stat.get("videoCount",0)),
                    "thumbnail":snip.get("thumbnails",{}).get("default",{}).get("url","")}
        # Enrich videos with channel data
        for v in videos:
            if v["channel_id"] in channels:
                v["channel_stats"] = channels[v["channel_id"]]
        # Calculate niche metrics
        if videos:
            avg_vph = round(sum(v["vph"] for v in videos)/len(videos),1)
            max_vph = max(v["vph"] for v in videos)
            avg_views = round(sum(v["views"] for v in videos)/len(videos))
            avg_engagement = round(sum(v["engagement"] for v in videos)/len(videos),2)
            # Word frequency analysis
            import re as _re2
            words = {}
            for v in videos:
                for w in _re2.findall(r'[A-Za-zÀ-ɏ]{4,}', v["title"].lower()):
                    if w not in ("this","that","with","from","what","your","they","have","will","about","their","when","were","been","more","than"):
                        words[w] = words.get(w,0) + 1
            top_words = dict(sorted(words.items(), key=lambda x: x[1], reverse=True)[:20])
            # Title structure patterns
            structures = {"question":0,"number":0,"negative":0,"superlative":0,"how_to":0}
            for v in videos:
                t = v["title"]
                if "?" in t: structures["question"] += 1
                if _re2.search(r'\d+', t): structures["number"] += 1
                if any(w in t.lower() for w in ["never","worst","dangerous","scary","terrifying","dark","secret"]): structures["negative"] += 1
                if any(w in t.lower() for w in ["most","best","biggest","greatest","ultimate","insane"]): structures["superlative"] += 1
                if t.lower().startswith("how"): structures["how_to"] += 1
        else:
            avg_vph=0;max_vph=0;avg_views=0;avg_engagement=0;top_words={};structures={}
        return jsonify({
            "niche": niche,
            "videos": videos[:30],
            "channels": channels,
            "metrics": {"avg_vph":avg_vph,"max_vph":max_vph,"avg_views":avg_views,
                       "avg_engagement":avg_engagement,"total_analyzed":len(videos)},
            "top_words": top_words,
            "structures": structures,
            "period": "7 days"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ti/similar_channels", methods=["POST"])
def ti_similar_channels():
    """Find channels similar to a given channel (NexLev-style)."""
    import requests as _req
    data = request.json or {}
    channel_input = (data.get("channel") or "").strip()[:200]
    if not channel_input:
        return jsonify({"error": "Channel name, handle, or ID required"}), 400
    from core.api_keys import load_api_key
    yt_key = data.get("yt_api_key") or load_api_key("youtube")
    if not yt_key:
        return jsonify({"error": "YouTube API key required"}), 400
    try:
        # Resolve channel
        channel_id = _resolve_channel_id(channel_input, yt_key)
        if not channel_id:
            return jsonify({"error": f"Channel '{channel_input}' not found. Try using a Channel ID (starts with UC)."}), 404
        # Get source channel info
        src_r = _req.get("https://www.googleapis.com/youtube/v3/channels", params={
            "part":"snippet,statistics,topicDetails","id":channel_id,"key":yt_key}, timeout=10)
        src_items = src_r.json().get("items",[])
        if not src_items:
            return jsonify({"error": "Channel not found"}), 404
        src = src_items[0]
        src_info = {"id":channel_id,"name":src.get("snippet",{}).get("title",""),
            "subscribers":int(src.get("statistics",{}).get("subscriberCount",0)),
            "videos":int(src.get("statistics",{}).get("videoCount",0)),
            "thumbnail":src.get("snippet",{}).get("thumbnails",{}).get("medium",{}).get("url",""),
            "description":src.get("snippet",{}).get("description","")[:200]}
        # Search for similar channels using keywords from channel name/description
        keywords = src_info["name"]
        topics = src.get("topicDetails",{}).get("topicCategories",[])
        if topics:
            keywords += " " + " ".join(t.split("/")[-1].replace("_"," ") for t in topics[:3])
        sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
            "part":"snippet","q":keywords,"type":"channel","maxResults":20,"key":yt_key}, timeout=12)
        similar_ids = []
        for item in sr.json().get("items", []):
            cid = item.get("id", {}).get("channelId", "") or item.get("snippet", {}).get("channelId", "")
            if cid and cid != channel_id:
                similar_ids.append(cid)
        similar_ids = [cid for cid in similar_ids if cid][:15]
        similar = []
        if similar_ids:
            ch_r2 = _req.get("https://www.googleapis.com/youtube/v3/channels", params={
                "part":"snippet,statistics","id":",".join(similar_ids),"key":yt_key}, timeout=12)
            for ch in ch_r2.json().get("items",[]):
                stat=ch.get("statistics",{}); snip=ch.get("snippet",{})
                similar.append({"id":ch["id"],"name":snip.get("title",""),
                    "subscribers":int(stat.get("subscriberCount",0)),
                    "videos":int(stat.get("videoCount",0)),
                    "thumbnail":snip.get("thumbnails",{}).get("default",{}).get("url",""),
                    "description":snip.get("description","")[:150]})
            similar.sort(key=lambda x: x["subscribers"], reverse=True)
        return jsonify({"source": src_info, "similar": similar})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ti/competitor_spy", methods=["POST"])
def ti_competitor_spy():
    """Spy on a competitor's best-performing titles and extract patterns."""
    import requests as _req
    data = request.json or {}
    channel_input = (data.get("channel") or "").strip()[:200]
    if not channel_input:
        return jsonify({"error": "Channel required"}), 400
    from core.api_keys import load_api_key
    yt_key = data.get("yt_api_key") or load_api_key("youtube")
    if not yt_key:
        return jsonify({"error": "YouTube API key required"}), 400
    try:
        # Resolve channel ID
        channel_id = _resolve_channel_id(channel_input, yt_key)
        if not channel_id:
            return jsonify({"error": f"Channel '{channel_input}' not found. Try using a Channel ID (starts with UC)."}), 404
        # Get channel info
        ch_info_r = _req.get("https://www.googleapis.com/youtube/v3/channels", params={
            "part":"snippet,statistics","id":channel_id,"key":yt_key}, timeout=10)
        ch_items = ch_info_r.json().get("items",[])
        channel_info = {}
        if ch_items:
            snip=ch_items[0].get("snippet",{}); stat=ch_items[0].get("statistics",{})
            channel_info = {"name":snip.get("title",""),"subscribers":int(stat.get("subscriberCount",0)),
                "videos":int(stat.get("videoCount",0)),"thumbnail":snip.get("thumbnails",{}).get("medium",{}).get("url","")}
        # Get latest 50 videos
        sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
            "part":"snippet","channelId":channel_id,"type":"video","order":"date",
            "maxResults":50,"key":yt_key}, timeout=12)
        video_ids = [i["id"]["videoId"] for i in sr.json().get("items",[]) if "videoId" in i.get("id",{})]
        videos = []
        if video_ids:
            for batch_start in range(0, len(video_ids), 50):
                batch = video_ids[batch_start:batch_start+50]
                stats_r = _req.get("https://www.googleapis.com/youtube/v3/videos", params={
                    "part":"snippet,statistics","id":",".join(batch),"key":yt_key}, timeout=12)
                for item in stats_r.json().get("items",[]):
                    snip=item.get("snippet",{}); stat=item.get("statistics",{})
                    views=int(stat.get("viewCount",0)); likes=int(stat.get("likeCount",0))
                    pub=snip.get("publishedAt","")
                    hours_ago=1
                    try:
                        pub_dt=datetime.strptime(pub[:19],"%Y-%m-%dT%H:%M:%S")
                        hours_ago=max(1,(datetime.utcnow()-pub_dt).total_seconds()/3600)
                    except: pass
                    videos.append({"id":item["id"],"title":snip.get("title",""),
                        "views":views,"likes":likes,"vph":round(views/hours_ago,1),
                        "published":pub[:10],"engagement":round(likes/max(1,views)*100,2),
                        "thumbnail":snip.get("thumbnails",{}).get("medium",{}).get("url","")})
        # Calculate averages and find outliers
        if videos:
            avg_views = sum(v["views"] for v in videos)/len(videos)
            avg_vph = sum(v["vph"] for v in videos)/len(videos)
            for v in videos:
                v["outlier_score"] = round(v["views"]/max(1,avg_views),2)
                v["vph_multiplier"] = round(v["vph"]/max(1,avg_vph),2)
            videos.sort(key=lambda x: x["vph"], reverse=True)
            outliers = [v for v in videos if v["outlier_score"] >= 2.0]
            # AI analysis of winning patterns
            top_titles = "\n".join(f"- [{v['vph']:.0f} VPH, {v['outlier_score']}x] {v['title']}" for v in videos[:15])
            outlier_titles = "\n".join(f"- [{v['vph']:.0f} VPH, {v['outlier_score']}x OUTLIER] {v['title']}" for v in outliers[:10])
            prompt = f"""Analyze this YouTube channel's title patterns. Find what makes their BEST videos go viral.
CHANNEL: {channel_info.get('name','')} ({channel_info.get('subscribers',0):,} subs)
TOP 15 VIDEOS (by VPH):
{top_titles}

OUTLIER VIDEOS (2x+ above average):
{outlier_titles if outlier_titles else 'No clear outliers found'}

Provide:
### Winning Title Patterns
- What structural patterns do the outlier titles share?
- What emotional triggers are used?
- What word patterns appear repeatedly?

### Their Secret Formula
- The exact title template they use for viral hits
- Why it works psychologically

### How to BEAT This Channel
- 5 title ideas that use their formula but with a UNIQUE angle
- What they're NOT covering that you could exploit"""
            ai_analysis = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
        else:
            outliers=[]; ai_analysis="No videos found"
        return jsonify({"channel": channel_info, "videos": videos[:50], "outliers": outliers[:10],
            "total_analyzed": len(videos), "ai_analysis": ai_analysis,
            "metrics": {"avg_views":round(avg_views) if videos else 0,
                       "avg_vph":round(avg_vph,1) if videos else 0}})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ti/script_to_titles", methods=["POST"])
def ti_script_to_titles():
    """Generate viral titles FROM a script/roteiro."""
    data = request.json or {}
    script = (data.get("script") or "").strip()[:3000]
    if not script:
        return jsonify({"error": "Script/roteiro required"}), 400
    language = data.get("language", "Português")[:50]
    niche = data.get("niche", "")[:200]
    prompt = f"""You are the world's #1 YouTube Title Specialist. Generate viral titles from this script.
SCRIPT CONTENT:
{script[:2000]}

{f"NICHE: {niche}" if niche else ""}
TARGET LANGUAGE: {language}

RULES:
- Each title MUST be 70-100 characters
- Use 1-2 ALL CAPS power words per title
- Include at least 1 viral structure (curiosity gap, superlative, specific number, etc.)
- Titles must capture the CORE hook of the script
- Generate titles that would make someone STOP scrolling

Generate EXACTLY 15 titles. Return ONLY a JSON array of objects:
[{{"title":"...", "structure":"...", "hook":"...", "estimated_ctr":"high/medium/low"}}]
No markdown. No explanation. ONLY the JSON array."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    try:
        import re as _re3
        m = _re3.search(r'\[.*\]', result, _re3.DOTALL)
        titles = json.loads(m.group() if m else result)
        return jsonify({"titles": titles, "script_preview": script[:200]})
    except:
        return jsonify({"titles": [], "raw": result, "script_preview": script[:200]})

@app.route("/api/ti/one_click_strategy", methods=["POST"])
def ti_one_click_strategy():
    """ONE-CLICK STRATEGY: Capture viral structures from YouTube + create adapted strategy."""
    import requests as _req
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:300]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    language = data.get("language", "Português")[:50]
    from core.api_keys import load_api_key
    yt_key = data.get("yt_api_key") or load_api_key("youtube")
    if not yt_key:
        return jsonify({"error": "YouTube API key required"}), 400
    try:
        from datetime import timedelta
        # Fetch viral videos from LAST 7 DAYS
        pub_after = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
            "part":"snippet","q":niche,"type":"video","order":"viewCount",
            "maxResults":30,"publishedAfter":pub_after,"key":yt_key}, timeout=12)
        video_ids = [i["id"]["videoId"] for i in sr.json().get("items",[]) if "videoId" in i.get("id",{})]
        viral_titles = []
        if video_ids:
            stats_r = _req.get("https://www.googleapis.com/youtube/v3/videos", params={
                "part":"snippet,statistics","id":",".join(video_ids[:25]),"key":yt_key}, timeout=12)
            for item in stats_r.json().get("items",[]):
                snip=item.get("snippet",{}); stat=item.get("statistics",{})
                views=int(stat.get("viewCount",0))
                pub=snip.get("publishedAt","")
                hours_ago=1
                try:
                    pub_dt=datetime.strptime(pub[:19],"%Y-%m-%dT%H:%M:%S")
                    hours_ago=max(1,(datetime.utcnow()-pub_dt).total_seconds()/3600)
                except: pass
                viral_titles.append({"title":snip.get("title",""),"views":views,
                    "vph":round(views/hours_ago,1),"channel":snip.get("channelTitle",""),
                    "published":pub[:10]})
            viral_titles.sort(key=lambda x: x["vph"], reverse=True)
        titles_text = "\n".join(f"- [{v['vph']:.0f} VPH] {v['title']} (by {v['channel']})" for v in viral_titles[:20])
        prompt = f"""You are the world's #1 YouTube Strategy Architect with access to REAL viral data.
NICHE: {niche}
LANGUAGE: {language}

REAL VIRAL TITLES FROM YOUTUBE (last 7 days):
{titles_text}

## YOUR MISSION: Create a ONE-CLICK viral content strategy.

### STEP 1: EXTRACT viral title structures
From the titles above, identify the TOP 5 structural patterns that are WORKING RIGHT NOW.
For each: the pattern syntax, example from data, why it works, VPH proof.

### STEP 2: FIND THE GAPS
What SUBTEMAS within "{niche}" are NOT being covered by these viral videos?
List 5 unexplored angles with HIGH demand potential.

### STEP 3: ADAPT & CREATE
For each of the 5 gaps, use the TOP viral structures from Step 1 to create:
- 3 titles (70-100 chars, using the PROVEN structures)
- 1 thumbnail concept
- Why this combo of proven structure + unexplored angle = GUARANTEED virality

### STEP 4: CONTENT CALENDAR (next 7 days)
Day-by-day plan with specific title + angle for each video.

### STEP 5: QUICK WINS
3 videos you can make TODAY that would likely go viral based on the data.

Be extremely data-driven. Reference specific VPH numbers as proof."""
        result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
        return jsonify({"strategy": result, "niche": niche, "viral_data": viral_titles[:20],
                        "total_viral_analyzed": len(viral_titles)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ti/viral_trends", methods=["POST"])
def ti_viral_trends():
    """Capture viral title structures from videos with 100K+ views in the last 7 days.
    Extracts the DNA (structure, hooks, patterns) from trending titles in a niche."""
    import requests as _req
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:300]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    language = data.get("language", "English")[:50]
    min_views = max(10000, min(10000000, int(data.get("min_views", 100000))))
    days = max(1, min(30, int(data.get("days", 7))))

    from core.api_keys import load_api_key
    yt_key = data.get("yt_api_key") or load_api_key("youtube")

    viral_titles = []
    viral_videos = []

    # --- Strategy 1: YouTube Data API (if key available) ---
    if yt_key and not yt_key.startswith("AIzaSyTEST"):
        try:
            from datetime import timedelta
            pub_after = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            # Search top videos by viewCount
            sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
                "part":"snippet","q":niche,"type":"video","order":"viewCount",
                "maxResults":50,"publishedAfter":pub_after,"key":yt_key}, timeout=15)
            video_ids = [i["id"]["videoId"] for i in sr.json().get("items",[]) if "videoId" in i.get("id",{})]
            if video_ids:
                stats_r = _req.get("https://www.googleapis.com/youtube/v3/videos", params={
                    "part":"snippet,statistics","id":",".join(video_ids[:50]),"key":yt_key}, timeout=15)
                for item in stats_r.json().get("items",[]):
                    snip=item.get("snippet",{}); stat=item.get("statistics",{})
                    views=int(stat.get("viewCount",0))
                    if views >= min_views:
                        pub=snip.get("publishedAt","")
                        hours_ago=1
                        try:
                            pub_dt=datetime.strptime(pub[:19],"%Y-%m-%dT%H:%M:%S")
                            hours_ago=max(1,(datetime.utcnow()-pub_dt).total_seconds()/3600)
                        except: pass
                        title = snip.get("title","")
                        viral_titles.append(title)
                        viral_videos.append({
                            "title": title,
                            "channel": snip.get("channelTitle",""),
                            "views": views,
                            "vph": round(views/hours_ago, 1),
                            "likes": int(stat.get("likeCount",0)),
                            "published": pub[:10],
                        })
        except Exception as e:
            pass  # Fall through to Strategy 2

    # --- Strategy 2: Autocomplete + AI analysis (always works, no API key needed) ---
    autocomplete_data = []
    try:
        keywords = niche.split()[:3]
        for prefix in [niche, keywords[0] if keywords else niche]:
            r = _req.get("https://suggestqueries.google.com/complete/search",
                params={"client": "youtube", "ds": "yt", "q": prefix, "hl": "en"},
                timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            m = _re.search(r'\((.+)\)$', r.text)
            if m:
                parsed = json.loads(m.group(1))
                if len(parsed) > 1:
                    autocomplete_data.extend([s[0] for s in parsed[1]][:8])
    except:
        pass

    # --- Extract Title DNA Structures ---
    structures_found = {
        "curiosity_gap": [], "fear_fomo": [], "superlative": [],
        "story_hook": [], "data_driven": [], "listicle": [],
        "controversy": [], "transformation": [], "comparison": [],
        "how_to": [], "other": []
    }
    for title in viral_titles:
        t_lower = title.lower()
        classified = False
        if any(w in t_lower for w in ["nobody", "secret", "hidden", "truth", "they don't", "finally"]):
            structures_found["curiosity_gap"].append(title); classified = True
        if any(w in t_lower for w in ["warning", "danger", "stop", "never", "scary", "terrifying"]):
            structures_found["fear_fomo"].append(title); classified = True
        if any(w in t_lower for w in ["most", "best", "biggest", "world's", "greatest", "insane"]):
            structures_found["superlative"].append(title); classified = True
        if any(w in t_lower for w in ["i spent", "i tried", "i went", "here's what happened", "the result"]):
            structures_found["story_hook"].append(title); classified = True
        if _re.search(r'\b\d{2,}\b', title) and any(w in t_lower for w in ["study", "research", "analyzed", "tested", "data"]):
            structures_found["data_driven"].append(title); classified = True
        if _re.search(r'^\d+\s', title) or _re.search(r'\btop\s+\d+\b', t_lower):
            structures_found["listicle"].append(title); classified = True
        if any(w in t_lower for w in ["wrong", "scam", "lie", "exposed", "debunked", "myth"]):
            structures_found["controversy"].append(title); classified = True
        if any(w in t_lower for w in ["transform", "changed", "before and after", "journey", "from", "to"]):
            structures_found["transformation"].append(title); classified = True
        if any(w in t_lower for w in [" vs ", "versus", "compared", "better", "which"]):
            structures_found["comparison"].append(title); classified = True
        if t_lower.startswith("how") or "how to" in t_lower:
            structures_found["how_to"].append(title); classified = True
        if not classified:
            structures_found["other"].append(title)

    # Remove empty categories
    structures_found = {k: v for k, v in structures_found.items() if v}

    # --- AI Analysis: Extract deeper patterns ---
    ai_analysis = ""
    if viral_titles:
        titles_text = "\n".join(f"- {t}" for t in viral_titles[:30])
        niche_intel = ""
        try:
            from core.niche_database import NICHE_INTELLIGENCE, VIRAL_STRUCTURES
            for nk, intel in NICHE_INTELLIGENCE.items():
                if nk.lower() in niche.lower() or niche.lower() in nk.lower():
                    niche_intel = f"\nNiche Intel: RPM ${intel['rpm_usd'][0]}-${intel['rpm_usd'][1]}, top hooks: {', '.join(intel['top_hooks'])}"
                    break
        except: pass

        analysis_prompt = f"""You are a viral title DNA analyst. Analyze these {len(viral_titles)} titles that got 100K+ views in the last {days} days in the '{niche}' niche.
{niche_intel}

VIRAL TITLES:
{titles_text}

Extract and output:

### DOMINANT TITLE STRUCTURES (ranked by frequency)
For each structure found, show:
1. **[Structure Name]** (X of {len(viral_titles)} titles use this = Y%)
   - Template: "[Generalized structure with placeholders]"
   - Best example: "[exact title from list]"
   - Why it works: [1 sentence]

### POWER WORD CLUSTERS
- Most used CAPS words: [list]
- Most used emotional triggers: [list]
- Most used numbers/specifics: [list]

### TITLE DNA FORMULA
The winning formula for '{niche}' titles right now is:
[Hook Type] + [Subject Structure] + [Emotional Trigger] + [Specific Element]
Example template: "[filled in template]"

### 3 UNTAPPED STRUCTURES
Structures that are NOT being used but would likely work in this niche:
1. [Template] — why it would work
2. [Template]
3. [Template]"""

        ai_analysis = _ask_gemini_gcg(analysis_prompt, data.get("ai_api_key"))

    return jsonify({
        "niche": niche,
        "viral_titles": viral_titles,
        "viral_videos": sorted(viral_videos, key=lambda x: x.get("views", 0), reverse=True)[:30],
        "structures": structures_found,
        "structure_counts": {k: len(v) for k, v in structures_found.items()},
        "autocomplete_trending": list(set(autocomplete_data))[:15],
        "ai_analysis": ai_analysis,
        "total_viral_found": len(viral_titles),
        "min_views_filter": min_views,
        "period_days": days,
        "data_source": "youtube_api" if viral_titles else "autocomplete_only",
    })


@app.route("/api/ti/adapt_viral", methods=["POST"])
def ti_adapt_viral():
    """Adapt viral title structures from trending videos to a specific channel's DNA.
    Takes viral structures + channel identity and generates adapted titles."""
    data = request.json or {}
    channel_name = (data.get("channel_name") or "").strip()[:200]
    channel_niche = (data.get("channel_niche") or "").strip()[:300]
    channel_themes = data.get("channel_themes", [])
    if isinstance(channel_themes, str):
        channel_themes = [t.strip() for t in channel_themes.split(",") if t.strip()]
    channel_themes = channel_themes[:15]
    channel_titles = data.get("channel_titles", [])
    if isinstance(channel_titles, str):
        channel_titles = [t.strip() for t in channel_titles.split("\n") if t.strip()]
    channel_titles = channel_titles[:20]
    viral_structures = data.get("viral_structures", [])
    if isinstance(viral_structures, str):
        viral_structures = [s.strip() for s in viral_structures.split("\n") if s.strip()]
    viral_structures = viral_structures[:30]
    language = data.get("language", "English")[:50]

    if not channel_niche:
        return jsonify({"error": "channel_niche required"}), 400

    # Auto-fetch viral structures if not provided
    if not viral_structures:
        import requests as _req
        from core.api_keys import load_api_key
        yt_key = data.get("yt_api_key") or load_api_key("youtube")
        if yt_key and not yt_key.startswith("AIzaSyTEST"):
            try:
                from datetime import timedelta
                pub_after = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
                sr = _req.get("https://www.googleapis.com/youtube/v3/search", params={
                    "part":"snippet","q":channel_niche,"type":"video","order":"viewCount",
                    "maxResults":30,"publishedAfter":pub_after,"key":yt_key}, timeout=15)
                v_ids = [i["id"]["videoId"] for i in sr.json().get("items",[]) if "videoId" in i.get("id",{})]
                if v_ids:
                    stats_r = _req.get("https://www.googleapis.com/youtube/v3/videos", params={
                        "part":"snippet,statistics","id":",".join(v_ids[:30]),"key":yt_key}, timeout=15)
                    for item in stats_r.json().get("items",[]):
                        views = int(item.get("statistics",{}).get("viewCount",0))
                        if views >= 50000:
                            viral_structures.append(item["snippet"]["title"])
            except:
                pass

    # Load niche intelligence
    niche_intel = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE, VIRAL_STRUCTURES as VS, PSYCHOLOGICAL_TRIGGERS
        for nk, intel in NICHE_INTELLIGENCE.items():
            if nk.lower() in channel_niche.lower() or channel_niche.lower() in nk.lower():
                niche_intel = f"""
NICHE DATA for '{nk}':
- RPM: ${intel['rpm_usd'][0]}-${intel['rpm_usd'][1]}/1000 views
- Best hooks: {', '.join(intel['top_hooks'])}
- Target demo: {intel['target_demo']}
- Thumbnail style: {intel['thumbnail_style']}"""
                break
    except:
        pass

    # Build the mega-prompt
    viral_ctx = "\n".join(f"- {s}" for s in viral_structures[:25]) if viral_structures else "No viral data available — use your knowledge of current YouTube trends"
    themes_ctx = "\n".join(f"- {t}" for t in channel_themes) if channel_themes else "Not specified — infer from niche"
    existing_ctx = "\n".join(f"- {t}" for t in channel_titles[:15]) if channel_titles else "No existing titles provided"

    prompt = f"""You are a YouTube Title Adaptation Specialist. Your job is to take PROVEN viral title structures and adapt them to a specific channel's DNA while keeping the viral power intact.

CHANNEL DNA:
- Name: {channel_name or 'Not specified'}
- Niche: {channel_niche}
- Language: {language}
- Themes: 
{themes_ctx}
- Existing titles (to match style/voice):
{existing_ctx}
{niche_intel}

VIRAL STRUCTURES TO ADAPT (these got 100K+ views in the last 7 days):
{viral_ctx}

YOUR MISSION:
1. Analyze each viral title structure — what makes it click-worthy?
2. Adapt EACH structure to this channel's DNA, themes, and voice
3. Generate titles that feel native to this channel, not generic copies

OUTPUT FORMAT:

### ADAPTED VIRAL TITLES (15 titles minimum)

For each title:
**Original Viral:** "[exact viral title]"
**Adapted:** "[new title using same structure but channel's DNA]" (70-100 chars)
**Hook Type:** [curiosity_gap/fear_fomo/superlative/story_hook/etc.]
**Why This Works:** [1 sentence linking the viral DNA to channel context]

---

After all 15+ adapted titles:

### CHANNEL-NATIVE ORIGINALS (5 bonus titles)
Titles that use the BEST viral structures but feel 100% original to this channel:
1. "[title]" — combines [structure A] + [channel theme]
2. "[title]"
3. "[title]"
4. "[title]"
5. "[title]"

### DNA MATCH SCORE
How well does this channel's niche match the current viral wave: X/10
Recommendation: [should channel pivot, double down, or find adjacent niches?]

RULES:
- Every title MUST be 70-100 characters
- Every title MUST have 1-2 ALL CAPS power words
- Titles must feel like NATURAL content for this channel, not copycats
- Preserve the psychological trigger from the original viral title
- Adapt subjects, locations, names, numbers to the channel's specific niche"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "adapted_titles": result,
        "channel_name": channel_name,
        "channel_niche": channel_niche,
        "viral_structures_used": len(viral_structures),
        "channel_themes": channel_themes,
        "language": language,
    })


@app.route("/api/ti/viral_score", methods=["POST"])
def ti_viral_score():
    """Viral Score Calculator — DNA breakdown of a title's viral potential (0-100)."""
    import requests as _req
    data = request.json or {}
    title = (data.get("title") or "").strip()[:300]
    if not title:
        return jsonify({"error": "Title required"}), 400
    language = data.get("language", "English")[:50]
    niche = data.get("niche", "")[:200]

    # Get real autocomplete for keyword density
    suggestions = []
    try:
        import re as _re6
        main_words = " ".join(w for w in title.split() if len(w) > 3)[:60]
        r = _req.get("https://suggestqueries.google.com/complete/search",
            params={"client":"youtube","ds":"yt","q":main_words,"hl":"en"},
            timeout=6, headers={"User-Agent":"Mozilla/5.0"})
        m = _re6.search(r'\((.+)\)$', r.text)
        if m:
            parsed = json.loads(m.group(1))
            suggestions = [s[0] for s in parsed[1]][:8] if len(parsed) > 1 else []
    except: pass

    sug_ctx = "\n".join(f"- {s}" for s in suggestions) if suggestions else "N/A"
    prompt = f"""You are the world's top YouTube viral title analyst. Calculate the VIRAL SCORE (0-100) for this title with full scientific DNA breakdown.

TITLE: "{title}"
NICHE: {niche or 'General'}
LANGUAGE: {language}
YOUTUBE AUTOCOMPLETE (real demand signals):
{sug_ctx}

Provide your analysis in this EXACT format (use real numbers, be precise):

## VIRAL SCORE: [NUMBER]/100

### DNA BREAKDOWN
| Factor | Score | Weight | Analysis |
|--------|-------|--------|---------|
| 🧠 Curiosity Gap | X/20 | 20% | [why] |
| ⚡ Emotional Trigger | X/20 | 20% | [emotion triggered] |
| 🔑 Keyword Power | X/15 | 15% | [primary keyword strength] |
| 📏 Length & Clarity | X/15 | 15% | [char count analysis] |
| 🎯 Specificity | X/15 | 15% | [numbers/names/dates] |
| 🔥 Urgency/FOMO | X/15 | 15% | [urgency signals] |

### PSYCHOLOGICAL TRIGGERS DETECTED
- Primary: [main trigger]
- Secondary: [secondary trigger]
- Missing: [triggers not used]

### COMPARISON
- Better than: X% of titles in this niche
- Predicted CTR range: X%-Y%
- Best platform for this title: YouTube/Shorts/Both

### 3 POWER UPGRADES
1. [Upgraded version with score estimate]
2. [Upgraded version with score estimate]  
3. [Upgraded version with score estimate]

### VERDICT
[2-3 sentences: what makes this title work or fail]"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"score": result, "title": title, "autocomplete": suggestions})


@app.route("/api/ti/title_predictor", methods=["POST"])
def ti_title_predictor():
    """Title Predictor — Predicts which title from a list will perform best."""
    import requests as _req
    data = request.json or {}
    titles = data.get("titles", [])
    if not titles or not isinstance(titles, list):
        return jsonify({"error": "titles array required"}), 400
    titles = [str(t)[:200] for t in titles[:10]]
    niche = data.get("niche", "")[:200]
    language = data.get("language", "English")[:50]

    titles_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    prompt = f"""You are a YouTube algorithm expert who predicts title performance with data-driven precision.

NICHE: {niche or 'General'}
LANGUAGE: {language}

RANK THESE TITLES by predicted performance:
{titles_list}

For each title provide:
### RANKING (best to worst)

**#1 WINNER: "[title]"**
- Viral Score: X/100
- Predicted CTR: X%-Y%
- Why it wins: [2 sentences]
- Weakness: [1 sentence]

**#2: "[title]"**
- Viral Score: X/100
- Predicted CTR: X%-Y%
- Why: [1 sentence]

[Continue for all titles]

### HYBRID CHAMPION
Combine the best elements of #1 and #2:
"[New title incorporating strengths of both]"
Estimated Score: X/100

### KEY INSIGHT
[What pattern makes the winner better than the rest]"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"prediction": result, "titles_compared": len(titles), "niche": niche})


@app.route("/api/ti/content_calendar", methods=["POST"])
def ti_content_calendar():
    """Content Calendar — Generate a 30-day viral content calendar for a channel."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:300]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    language = data.get("language", "English")[:50]
    channel_size = data.get("channel_size", "small")  # small/medium/large
    freq = data.get("frequency", 3)  # videos per week
    try:
        freq = int(freq)
    except:
        freq = 3
    freq = max(1, min(7, freq))

    prompt = f"""You are a YouTube growth strategist creating a data-driven 30-day content calendar.

NICHE: {niche}
LANGUAGE: {language}
CHANNEL SIZE: {channel_size} ({{'small': '<10K subs', 'medium': '10K-100K subs', 'large': '>100K subs'}}.get(channel_size, channel_size))
FREQUENCY: {freq} videos/week

Create a 30-day content calendar with THESE EXACT structures:

## 📅 30-DAY VIRAL CONTENT CALENDAR
### MONTH THEME: [Overall monthly narrative arc]

**WEEK 1: [Theme]** (establish authority)
| Day | Title | Format | Hook Type | Expected Performance |
|-----|-------|--------|-----------|---------------------|
| Mon | [title] | [Short/Long/Both] | [Curiosity/Fear/FOMO] | [Low/Med/High/Viral] |
[4 rows for freq videos]

**WEEK 2: [Theme]** (build momentum)
[Same table format]

**WEEK 3: [Theme]** (viral push)
[Same table format]

**WEEK 4: [Theme]** (convert & retain)
[Same table format]

### 🎯 VIRAL BETS (Top 3 videos most likely to explode)
1. Week X, Day Y: "[title]" — Why: [reason]
2. [same]
3. [same]

### 📊 GROWTH STRATEGY NOTES
- Upload timing: [best days/times for this niche]
- Thumbnail pattern: [consistent visual style recommended]
- CTA strategy: [what call-to-action to use each week]"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"calendar": result, "niche": niche, "frequency": freq, "duration_days": 30})


@app.route("/api/ti/youtube_autocomplete", methods=["POST"])
def ti_youtube_autocomplete():
    """YouTube Search Autocomplete — real search suggestions for SEO."""
    import requests as _req
    data = request.json or {}
    query = (data.get("query") or "").strip()[:200]
    if not query:
        return jsonify({"error": "Query required"}), 400
    language = data.get("language", "pt")[:5]
    try:
        r = _req.get("https://suggestqueries.google.com/complete/search", params={
            "client":"youtube","ds":"yt","q":query,"hl":language}, timeout=8,
            headers={"User-Agent":"Mozilla/5.0"})
        # Parse JSONP response
        import re as _re4
        text = r.text
        m = _re4.search(r'\((.+)\)$', text)
        if m:
            parsed = json.loads(m.group(1))
            suggestions = [s[0] for s in parsed[1]] if len(parsed) > 1 else []
        else:
            suggestions = []
        return jsonify({"suggestions": suggestions, "query": query})
    except Exception as e:
        return jsonify({"suggestions": [], "error": str(e)})


@app.route("/api/ti/algorithm_explainer", methods=["POST"])
def ti_algorithm_explainer():
    """Algorithm Explainer — How YouTube's algorithm works for YOUR specific niche."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:300]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    language = data.get("language", "English")[:50]
    channel_size = data.get("channel_size", "small")[:20]
    content_type = data.get("content_type", "long-form")[:30]

    # Load niche intelligence
    niche_intel = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        for nk, intel in NICHE_INTELLIGENCE.items():
            if nk.lower() in niche.lower() or niche.lower() in nk.lower():
                niche_intel = f"""\nNICHE DATA for '{nk}':
- RPM: ${intel['rpm_usd'][0]}-${intel['rpm_usd'][1]}/1000 views
- Best upload days: {', '.join(intel['best_days'])}
- Best posting time: {intel['best_time']}
- Top hooks: {', '.join(intel['top_hooks'])}
- Target demo: {intel['target_demo']}
- Thumbnail style: {intel['thumbnail_style']}"""
                break
    except: pass

    prompt = f"""You are a YouTube Algorithm Expert. Explain how the algorithm works for this niche concisely but with all key data.

NICHE: {niche}
LANGUAGE: {language}
CHANNEL SIZE: {channel_size}
CONTENT TYPE: {content_type}
{niche_intel}

## 🧠 ALGORITHM GUIDE: "{niche}"

### 1. DISCOVERY (3-4 bullet points)
How YouTube finds and recommends videos in this niche. What triggers Browse/Search/Suggested placement.

### 2. KEY METRICS (table format)
| Metric | Target for {niche} | Why it matters |
|--------|-------------------|----------------|
| CTR | X%-Y% | [reason] |
| AVD | X min | [reason] |
| Engagement | X% | [reason] |

### 3. CONTENT FORMULA
- Best length: X-Y min | Upload freq: X/week | Best time: Day HH:MM
- Title formula: [the pattern that works in this niche]
- Thumbnail: [colors, faces, text pattern that converts]

### 4. TOP 3 ALGORITHM HACKS FOR {niche}
1. [Hack + why it works]
2. [Hack + why it works]
3. [Hack + why it works]

### 5. MONETIZATION
- RPM: $X-$Y/1000 views | Best sponsors: [type] | Diversify at: [milestone]

### 6. 30-DAY ACTION PLAN
Week 1-2: [actions] | Week 3-4: [actions]

Be specific with numbers. No filler."""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"explanation": result, "niche": niche, "channel_size": channel_size, "content_type": content_type})


@app.route("/api/ti/upload_timing", methods=["POST"])
def ti_upload_timing():
    """Upload Timing Optimizer — Best days and times to upload for maximum reach."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:300]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    language = data.get("language", "English")[:50]
    region = data.get("region", "US")[:10]
    channel_size = data.get("channel_size", "small")[:20]

    # Load niche timing data
    timing_data = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        for nk, intel in NICHE_INTELLIGENCE.items():
            if nk.lower() in niche.lower() or niche.lower() in nk.lower():
                timing_data = f"""\nDATA-DRIVEN TIMING for '{nk}':
- Best days: {', '.join(intel['best_days'])}
- Best time: {intel['best_time']}
- Target demo: {intel['target_demo']}"""
                break
    except: pass

    prompt = f"""You are a YouTube Upload Timing Specialist who has analyzed millions of videos to find the perfect upload windows.

NICHE: {niche}
LANGUAGE: {language}
REGION: {region}
CHANNEL SIZE: {channel_size}
{timing_data}

Provide a PRECISE upload timing strategy:

## ⏰ OPTIMAL UPLOAD TIMING FOR "{niche}"

### BEST UPLOAD WINDOWS (ranked #1 to #5)
| Rank | Day | Time (local) | Why This Works | Expected Reach Boost |
|------|-----|-------------|----------------|---------------------|
| #1 | [Day] | [HH:MM] | [Reason] | +X% vs random |
| #2 | [Day] | [HH:MM] | [Reason] | +X% |
| #3 | [Day] | [HH:MM] | [Reason] | +X% |
| #4 | [Day] | [HH:MM] | [Reason] | +X% |
| #5 | [Day] | [HH:MM] | [Reason] | +X% |

### WORST TIMES TO UPLOAD (avoid these!)
| Day | Time | Why It's Bad | Reach Penalty |
|-----|------|-------------|---------------|
| [Day] | [HH:MM] | [Reason] | -X% |

### WEEKLY SCHEDULE TEMPLATE
For {channel_size} channels uploading 3x/week:
- **Monday** [HH:MM]: [Content type]
- **Wednesday** [HH:MM]: [Content type]
- **Saturday** [HH:MM]: [Content type]

### AUDIENCE BEHAVIOR PATTERN
- Peak browsing hours for '{niche}' audience: [times]
- Mobile vs Desktop split: X% / Y%
- Weekend vs Weekday performance: [comparison]

### SEASONAL TIMING
- Best months for '{niche}': [months]
- Holiday opportunities: [specific dates/events]
- Algorithm boost periods: [when YouTube promotes more]

### PRO TIP
[One counter-intuitive timing hack specific to this niche]

All times should be in {region} timezone. Be specific with numbers."""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"timing": result, "niche": niche, "region": region, "channel_size": channel_size})


@app.route("/api/ti/channel_health", methods=["POST"])
def ti_channel_health():
    """Channel Health Score — Comprehensive health check with actionable recommendations."""
    data = request.json or {}
    channel_name = (data.get("channel_name") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:300]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    subscribers = data.get("subscribers", 0)
    try: subscribers = int(subscribers)
    except: subscribers = 0
    monthly_views = data.get("monthly_views", 0)
    try: monthly_views = int(monthly_views)
    except: monthly_views = 0
    videos_count = data.get("videos_count", 0)
    try: videos_count = int(videos_count)
    except: videos_count = 0
    upload_frequency = data.get("upload_frequency", "2-3/week")[:30]
    avg_views = data.get("avg_views", 0)
    try: avg_views = int(avg_views)
    except: avg_views = 0
    recent_titles = data.get("recent_titles", [])
    if isinstance(recent_titles, str):
        recent_titles = [t.strip() for t in recent_titles.split("\n") if t.strip()]
    recent_titles = recent_titles[:10]
    language = data.get("language", "English")[:50]

    titles_ctx = "\n".join(f"- {t}" for t in recent_titles) if recent_titles else "Not provided"

    # Niche benchmarks
    niche_bench = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        for nk, intel in NICHE_INTELLIGENCE.items():
            if nk.lower() in niche.lower() or niche.lower() in nk.lower():
                niche_bench = f"""\nNICHE BENCHMARKS for '{nk}':
- RPM: ${intel['rpm_usd'][0]}-${intel['rpm_usd'][1]}/1000 views
- Best days: {', '.join(intel['best_days'])}
- Target demo: {intel['target_demo']}"""
                break
    except: pass

    prompt = f"""You are a YouTube Channel Health Diagnostician. Perform a COMPREHENSIVE health check on this channel.

CHANNEL: {channel_name or 'Not specified'}
NICHE: {niche}
LANGUAGE: {language}
SUBSCRIBERS: {subscribers:,} 
MONTHLY VIEWS: {monthly_views:,}
TOTAL VIDEOS: {videos_count}
UPLOAD FREQUENCY: {upload_frequency}
AVG VIEWS/VIDEO: {avg_views:,}
RECENT TITLES:
{titles_ctx}
{niche_bench}

## 🏥 CHANNEL HEALTH REPORT

### OVERALL HEALTH SCORE: [X]/100

### VITAL SIGNS (score each 0-100)
| Metric | Score | Status | Benchmark | Your Value |
|--------|-------|--------|-----------|------------|
| 📊 Growth Rate | X/100 | 🟢/🟡/🔴 | [niche avg] | [calculated] |
| 👁️ View/Sub Ratio | X/100 | 🟢/🟡/🔴 | [niche avg] | [calculated] |
| 📈 Upload Consistency | X/100 | 🟢/🟡/🔴 | [ideal] | {upload_frequency} |
| 🎯 Title Quality | X/100 | 🟢/🟡/🔴 | [S/A grade] | [from titles] |
| 💰 Monetization Health | X/100 | 🟢/🟡/🔴 | [RPM range] | [estimated] |
| 🚀 Viral Potential | X/100 | 🟢/🟡/🔴 | [avg for size] | [assessed] |

### DIAGNOSIS
**Strengths (what's working):**
1. [specific strength with data]
2. [specific strength]
3. [specific strength]

**Critical Issues (fix immediately):**
1. [issue + exact fix]
2. [issue + exact fix]
3. [issue + exact fix]

**Growth Blockers (medium priority):**
1. [blocker + solution]
2. [blocker + solution]

### PRESCRIPTION (Next 30 Days)
| Week | Action | Expected Impact |
|------|--------|----------------|
| 1 | [specific action] | [metric improvement] |
| 2 | [specific action] | [metric improvement] |
| 3 | [specific action] | [metric improvement] |
| 4 | [specific action] | [metric improvement] |

### REVENUE DIAGNOSIS
- Current estimated monthly revenue: $X-$Y
- Revenue potential at optimal performance: $X-$Y
- Revenue gap: $X (Y% below potential)
- Top revenue optimization: [specific action]

### COMPETITIVE POSITION
- Channel is in the [top/middle/bottom] X% of '{niche}' channels
- Closest competitor benchmark: [channel size] with [views]
- To reach next tier: [specific milestone + timeline]

Be brutally honest. Use the data provided to calculate real metrics. No fluff."""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "health_report": result,
        "channel_name": channel_name,
        "niche": niche,
        "subscribers": subscribers,
        "monthly_views": monthly_views,
        "videos_count": videos_count,
    })


@app.route("/api/ti/tag_generator", methods=["POST"])
def ti_tag_generator():
    """Tag Generator & Optimizer — Generate SEO-optimized YouTube tags."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:200]
    if not title and not niche:
        return jsonify({"error": "Title or niche required"}), 400
    language = data.get("language", "English")[:50]
    import requests as _req2
    suggestions = []
    try:
        import re as _re7
        q = (title or niche)[:80]
        r = _req2.get("https://suggestqueries.google.com/complete/search",
            params={"client":"youtube","ds":"yt","q":q,"hl":"en"},
            timeout=6, headers={"User-Agent":"Mozilla/5.0"})
        m = _re7.search(r'\((.+)\)$', r.text)
        if m:
            parsed = json.loads(m.group(1))
            suggestions = [s[0] for s in parsed[1]][:10] if len(parsed) > 1 else []
    except: pass
    sug_ctx = "\n".join(f"- {s}" for s in suggestions) if suggestions else "N/A"
    prompt = f"""You are a YouTube SEO expert. Generate a complete, optimized tag set for this video.

TITLE: {title or 'N/A'}
NICHE: {niche or 'General'}
LANGUAGE: {language}
YOUTUBE AUTOCOMPLETE (real demand signals):
{sug_ctx}

Generate the PERFECT tag strategy:

## 🏷️ OPTIMIZED TAG SET

### PRIMARY TAGS (exact match — put first, highest priority)
[List 5 exact-match tags from title keywords, enclosed in backticks]

### SECONDARY TAGS (broad match — niche variations)
[List 8-10 broader niche tags]

### LONG-TAIL TAGS (question-based — capture search intent)
[List 5-7 long-tail question tags people actually search]

### TRENDING TAGS (from autocomplete signals)
[List 5 tags from the autocomplete data above]

### COMPETITOR TAGS (what top channels use)
[List 5 tags that top channels in this niche use]

## COMPLETE TAG LIST (copy-paste ready, comma separated)
[All tags in one comma-separated list, 400-450 chars total — YouTube's tag limit]

## SEO ANALYSIS
- Total tags: X
- Character count: X/450
- Search volume estimate: [Low/Medium/High] for primary tags
- Competition level: [Low/Medium/High]
- Predicted search rank opportunity: [Top 10/Top 30/Difficult]

## PRO TIP
[One advanced tagging strategy specific to this niche]"""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"tags": result, "autocomplete_used": suggestions, "title": title, "niche": niche})


@app.route("/api/ti/description_generator", methods=["POST"])
def ti_description_generator():
    """Description Generator — Full SEO-optimized YouTube description."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    if not title:
        return jsonify({"error": "Title required"}), 400
    niche = (data.get("niche") or "").strip()[:200]
    language = data.get("language", "English")[:50]
    channel_name = (data.get("channel_name") or "").strip()[:100]
    cta_type = (data.get("cta_type") or "subscribe")[:50]
    social_links = data.get("social_links", "")[:500]
    prompt = f"""You are a YouTube description copywriter who specializes in SEO + CTR optimization.

VIDEO TITLE: {title}
NICHE: {niche or 'General'}
LANGUAGE: {language}
CHANNEL: {channel_name or 'My Channel'}
CTA TYPE: {cta_type}
SOCIAL LINKS: {social_links or 'Not provided'}

Generate a COMPLETE, optimized YouTube description (5000 char limit):

## FULL DESCRIPTION (copy-paste ready):

[HOOK - first 2 lines visible before 'Show More' — CRITICAL for CTR]
[These lines MUST contain the main keyword and create urgency/curiosity]

[BODY - 150-200 words expanding on the video topic]
[Include 3-4 natural keyword mentions]
[Bullet points for scanability]

[CHAPTERS/TIMESTAMPS - if applicable]
00:00 - Introduction
[Add logical chapter breaks]

[SOCIAL PROOF + CTA]
✓ Subscribe for [specific value prop]: [channel link]
✓ [Platform]: [link]

[KEYWORDS SECTION]
[15-20 comma-separated keyword tags for SEO]

[HASHTAGS - 3-5 relevant]
#[hashtag1] #[hashtag2] #[hashtag3]

---
## DESCRIPTION ANALYSIS
- Character count: X/5000
- Primary keyword: [keyword]
- Secondary keywords: [list]
- Hook strength: [score]/10 — [why]
- SEO score: [X]/100
- Predicted CTR impact: [Low/Medium/High]"""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"description": result, "title": title, "niche": niche, "language": language})


@app.route("/api/ti/revenue_calculator", methods=["POST"])
def ti_revenue_calculator():
    """Revenue/RPM Calculator — Estimate YouTube revenue for a channel."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:200]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    monthly_views = data.get("monthly_views", 0)
    try: monthly_views = int(monthly_views)
    except: monthly_views = 0
    subscribers = data.get("subscribers", 0)
    try: subscribers = int(subscribers)
    except: subscribers = 0
    region = data.get("region", "US")[:10]
    language = data.get("language", "English")[:50]
    monetized = data.get("monetized", True)

    # Pull real RPM from niche database
    rpm_low, rpm_high, rpm_avg = 1.0, 3.0, 2.0
    niche_data_ctx = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        for nk, intel in NICHE_INTELLIGENCE.items():
            if nk.lower() in niche.lower() or niche.lower() in nk.lower():
                rpm_low = intel['rpm_usd'][0]
                rpm_high = intel['rpm_usd'][1]
                rpm_avg = (rpm_low + rpm_high) / 2
                niche_data_ctx = f"""\nNICHE DATABASE DATA for '{nk}':
- RPM: ${rpm_low}-${rpm_high}/1000 views
- Target demo: {intel['target_demo']}
- Thumbnail style: {intel['thumbnail_style']}"""
                break
    except: pass

    # Calculate estimates
    monthly_low = round((monthly_views / 1000) * rpm_low, 2)
    monthly_high = round((monthly_views / 1000) * rpm_high, 2)
    monthly_avg = round((monthly_views / 1000) * rpm_avg, 2)
    yearly_low = round(monthly_low * 12, 2)
    yearly_high = round(monthly_high * 12, 2)

    prompt = f"""You are a YouTube Revenue Expert. Provide a comprehensive revenue analysis and growth roadmap.

NICHE: {niche}
MONTHLY VIEWS: {monthly_views:,}
SUBSCRIBERS: {subscribers:,}
REGION: {region}
LANGUAGE: {language}
MONETIZED: {monetized}
{niche_data_ctx}

CALCULATED ESTIMATES:
- Monthly AdSense: ${monthly_low:,.2f} - ${monthly_high:,.2f} (avg: ${monthly_avg:,.2f})
- Annual AdSense: ${yearly_low:,.2f} - ${yearly_high:,.2f}
- RPM Range: ${rpm_low} - ${rpm_high}/1000 views

Provide the FULL revenue analysis:

## 💰 REVENUE ANALYSIS: {niche}

### ADSENSE REVENUE (current)
| Period | Low | Average | High |
|--------|-----|---------|------|
| Monthly | ${monthly_low:,.2f} | ${monthly_avg:,.2f} | ${monthly_high:,.2f} |
| Annual | ${yearly_low:,.2f} | ${round(monthly_avg*12):,.2f} | ${yearly_high:,.2f} |

### RPM ANALYSIS
- Your niche RPM: ${rpm_low} - ${rpm_high}/1000 views
- [Explain why this RPM is high/low vs YouTube average of ~$1.50]
- [What types of ads appear in this niche]
- [How to maximize RPM in this specific niche]

### REVENUE DIVERSIFICATION ROADMAP
| Revenue Stream | Potential | When to Start | How to Implement |
|---------------|-----------|---------------|------------------|
| AdSense | ${monthly_avg:,.2f}/mo | Now | [tip] |
| Sponsorships | $X-$Y/video | [milestone] | [how] |
| Affiliate Marketing | $X-$Y/mo | [milestone] | [what products] |
| Channel Memberships | $X-$Y/mo | [milestone] | [pricing] |
| Digital Products | $X-$Y/mo | [milestone] | [what type] |
| Merchandise | $X-$Y/mo | [milestone] | [strategy] |

### GROWTH PROJECTIONS
| Subscribers | Monthly Views | Monthly Revenue |
|-------------|---------------|-----------------|
| Current: {subscribers:,} | {monthly_views:,} | ${monthly_avg:,.2f} |
| 10K | [estimate] | [estimate] |
| 50K | [estimate] | [estimate] |
| 100K | [estimate] | [estimate] |
| 500K | [estimate] | [estimate] |

### TOP REVENUE OPTIMIZATION TIPS (for {niche})
1. [Specific tip to increase RPM]
2. [Specific tip to increase views]
3. [Best sponsorship type for this niche]
4. [Best affiliate programs for this niche]

### COMPETITIVE REVENUE BENCHMARK
- Average channel at your size earns: $X-$Y/month
- Top 10% in this niche earns: $X-$Y/month
- Your revenue potential score: X/100"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "revenue_analysis": result,
        "estimates": {"monthly_low": monthly_low, "monthly_high": monthly_high, "monthly_avg": monthly_avg, "yearly_low": yearly_low, "yearly_high": yearly_high, "rpm_low": rpm_low, "rpm_high": rpm_high},
        "niche": niche,
        "monthly_views": monthly_views,
        "region": region
    })


@app.route("/api/ti/content_gap", methods=["POST"])
def ti_content_gap():
    """Content Gap Analyzer — Find topics competitors cover that you don't."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:300]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    my_titles = data.get("my_titles", [])[:30]
    if isinstance(my_titles, str):
        my_titles = [t.strip() for t in my_titles.split("\n") if t.strip()]
    competitor_channels = data.get("competitor_channels", [])[:5]
    language = data.get("language", "English")[:50]

    import requests as _req3
    import re as _re8
    auto_topics = []
    try:
        for q in [niche, f"{niche} tips", f"{niche} tutorial", f"{niche} secrets"][:3]:
            r = _req3.get("https://suggestqueries.google.com/complete/search",
                params={"client":"youtube","ds":"yt","q":q[:60],"hl":"en"},
                timeout=5, headers={"User-Agent":"Mozilla/5.0"})
            m = _re8.search(r'\((.+)\)$', r.text)
            if m:
                parsed = json.loads(m.group(1))
                auto_topics += [s[0] for s in parsed[1][:5]] if len(parsed) > 1 else []
    except: pass
    auto_topics = list(dict.fromkeys(auto_topics))[:20]

    my_ctx = "\n".join(f"- {t}" for t in my_titles) if my_titles else "Not provided"
    comp_ctx = ", ".join(competitor_channels) if competitor_channels else "Not specified"
    auto_ctx = "\n".join(f"- {t}" for t in auto_topics) if auto_topics else "N/A"

    prompt = f"""You are a YouTube Content Strategy Expert. Identify content gaps and untapped opportunities.

NICHE: {niche}
LANGUAGE: {language}
MY EXISTING TITLES:
{my_ctx}
COMPETITOR CHANNELS: {comp_ctx}
YOUTUBE SEARCH DEMAND (autocomplete data):
{auto_ctx}

Analyze and identify CONTENT GAPS:

## 🔍 CONTENT GAP ANALYSIS: {niche}

### TOPICS YOU'RE MISSING (High Opportunity)
| # | Topic/Title Idea | Search Demand | Competition | Opportunity Score |
|---|-----------------|--------------|-------------|-------------------|
| 1 | [Topic] | High/Med/Low | High/Med/Low | X/10 |
[List 10-15 gap topics]

### UNTAPPED FORMATS
- [Format competitors aren't doing but audience wants]
- [Format that works in similar niches but not yours]
- [Trending format to test]

### BLUE OCEAN OPPORTUNITIES
[3 completely untapped sub-niches within {niche} that have demand but ZERO competition]

### CONTENT CALENDAR GAPS
- Missing seasonal content: [what + when]
- Missing evergreen pillars: [topics that never go out of style]
- Missing series ideas: [multi-part series gaps]

### COMPETITOR ANALYSIS
Based on niche knowledge, top channels in {niche} focus on:
[List their main topic clusters]

You could differentiate by:
[3 unique angles that competitors haven't fully exploited]

### QUICK WINS (topics to make NOW)
1. [Topic — why it's urgent] → Suggested title: "[title]"
2. [Topic — why it's urgent] → Suggested title: "[title]"
3. [Topic — why it's urgent] → Suggested title: "[title]"

### LONG-TERM CONTENT PILLARS MISSING
[3-5 cornerstone topic categories that every serious channel in this niche should have]"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"gap_analysis": result, "niche": niche, "auto_demand": auto_topics, "my_titles_count": len(my_titles)})


@app.route("/api/ti/strategy_advisor", methods=["POST"])
def ti_strategy_advisor():
    """AI Strategy Advisor — Personalized YouTube growth advisor."""
    data = request.json or {}
    question = (data.get("question") or "").strip()[:1000]
    niche = (data.get("niche") or "").strip()[:200]
    if not question:
        return jsonify({"error": "Question required"}), 400
    channel_context = (data.get("channel_context") or "").strip()[:500]
    language = data.get("language", "English")[:50]
    history = data.get("history", [])
    if not isinstance(history, list): history = []
    history = history[-6:]
    niche_ctx = ""
    if niche:
        try:
            from core.niche_database import NICHE_INTELLIGENCE
            for nk, intel in NICHE_INTELLIGENCE.items():
                if nk.lower() in niche.lower() or niche.lower() in nk.lower():
                    niche_ctx = f"""\nNICHE DATA for '{nk}':\n- RPM: ${intel['rpm_usd'][0]}-${intel['rpm_usd'][1]}/1000 views\n- Best days: {', '.join(intel['best_days'])}\n- Top hooks: {', '.join(intel['top_hooks'][:3])}"""
                    break
        except: pass
    history_ctx = ""
    if history:
        history_ctx = "\n\nCONVERSATION HISTORY:\n"
        for h in history:
            role = "Creator" if h.get("role") == "user" else "Advisor"
            history_ctx += f"{role}: {h.get('content','')[:300]}\n"
    prompt = f"""You are the world's best YouTube Growth Strategist and Algorithm Expert. You have helped thousands of channels grow from 0 to millions of subscribers. Think like Mr. Beast's strategy team + top SEO experts + a data scientist who understands the YouTube algorithm deeply.\n\nNICHE: {niche or 'Not specified'}\nCHANNEL CONTEXT: {channel_context or 'Not specified'}\nLANGUAGE: {language}\n{niche_ctx}\n{history_ctx}\n\nCREATOR'S QUESTION: {question}\n\nRespond as a brilliant, direct advisor. Be specific with data. Give actionable advice. Format clearly with headers if needed. Always end with ONE most important action the creator should take right now."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"answer": result, "niche": niche, "question": question})


@app.route("/api/ti/competitor_tracker", methods=["POST"])
def ti_competitor_tracker():
    """Competitor Tracker — Monitor and analyze multiple competitor channels."""
    import requests as _req4
    data = request.json or {}
    channels = data.get("channels", [])
    if isinstance(channels, str):
        channels = [c.strip() for c in channels.split(",") if c.strip()]
    if not channels:
        return jsonify({"error": "channels array required"}), 400
    channels = channels[:10]
    try:
        from core.api_keys import load_api_key as _lak4
        yt_key = data.get("yt_api_key") or _lak4("youtube") or ""
    except:
        yt_key = data.get("yt_api_key") or ""

    language = data.get("language", "English")[:50]
    niche = (data.get("niche") or "").strip()[:200]
    tracker_data = []
    for ch in channels:
        ch_info = {"input": ch, "id": None, "title": ch, "subscribers": 0, "views": 0, "videos": 0, "recent_titles": [], "error": None}
        if yt_key:
            try:
                ch_id = _resolve_channel_id(ch, yt_key)
                if ch_id:
                    r = _req4.get("https://www.googleapis.com/youtube/v3/channels",
                        params={"part":"snippet,statistics","id":ch_id,"key":yt_key}, timeout=8)
                    items = r.json().get("items", [])
                    if items:
                        item = items[0]
                        stats = item.get("statistics", {})
                        ch_info["id"] = ch_id
                        ch_info["title"] = item["snippet"]["title"]
                        ch_info["subscribers"] = int(stats.get("subscriberCount",0))
                        ch_info["views"] = int(stats.get("viewCount",0))
                        ch_info["videos"] = int(stats.get("videoCount",0))
                    r2 = _req4.get("https://www.googleapis.com/youtube/v3/search",
                        params={"part":"snippet","channelId":ch_id,"order":"date","maxResults":5,"key":yt_key}, timeout=8)
                    for item in r2.json().get("items", []):
                        if item.get("id",{}).get("kind") == "youtube#video":
                            ch_info["recent_titles"].append(item["snippet"]["title"])
            except Exception as e:
                ch_info["error"] = str(e)[:100]
        tracker_data.append(ch_info)
    comp_summary = "\n".join(
        f"- {c['title']}: {int(c['subscribers'] or 0):,} subs, {c['videos']} videos. Recent: {c['recent_titles'][:2]}"
        for c in tracker_data
    )
    ai_analysis = ""
    try:
        prompt = f"""You are a YouTube competitive intelligence expert. Analyze these competitor channels:

NICHE: {niche or 'General'}
COMPETITOR DATA:
{comp_summary}

## \U0001f575\ufe0f COMPETITOR INTELLIGENCE REPORT

### COMPETITIVE LANDSCAPE
[Who dominates, who is growing fastest, overall market dynamics]

### CHANNEL-BY-CHANNEL BREAKDOWN
For each channel listed above:
- **[Channel]**: strength, weakness, content pattern, opportunity for you

### TOP 3 GAPS YOU CAN EXPLOIT
1. [Gap + how to fill it]
2. [Gap + how to fill it]
3. [Gap + how to fill it]

### DIFFERENTIATION STRATEGY
[Specific strategy to stand out from all these competitors]

### ALERT: Channel to Watch Most Closely
[Which competitor + why]"""
        ai_analysis = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    except Exception as _e:
        ai_analysis = f"AI analysis unavailable: {str(_e)[:100]}"
    return jsonify({"competitors": tracker_data, "ai_analysis": ai_analysis, "total_tracked": len(tracker_data), "niche": niche, "has_yt_api": bool(yt_key)})


@app.route("/api/ti/growth_predictor", methods=["POST"])
def ti_growth_predictor():
    """Growth Predictor — Predict channel growth trajectory."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:200]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    subscribers = data.get("subscribers", 0)
    try: subscribers = int(subscribers)
    except: subscribers = 0
    monthly_views = data.get("monthly_views", 0)
    try: monthly_views = int(monthly_views)
    except: monthly_views = 0
    monthly_growth_rate = data.get("monthly_growth_rate", 5.0)
    try: monthly_growth_rate = float(monthly_growth_rate)
    except: monthly_growth_rate = 5.0
    upload_freq = data.get("upload_frequency", "2-3/week")[:30]
    language = data.get("language", "English")[:50]
    channel_age_months = data.get("channel_age_months", 6)
    try: channel_age_months = int(channel_age_months)
    except: channel_age_months = 6
    rate = monthly_growth_rate / 100.0
    projections = []
    for month in [1, 3, 6, 12, 18, 24]:
        proj_subs = round(subscribers * ((1 + rate) ** month))
        proj_views = round(monthly_views * ((1 + rate * 1.2) ** month))
        projections.append({"month": month, "subscribers": proj_subs, "monthly_views": proj_views})
    niche_ctx = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        for nk, intel in NICHE_INTELLIGENCE.items():
            if nk.lower() in niche.lower() or niche.lower() in nk.lower():
                niche_ctx = f"\nNICHE DATA:\n- RPM: ${intel['rpm_usd'][0]}-${intel['rpm_usd'][1]}/1000 views\n- Target demo: {intel['target_demo']}"
                break
    except: pass
    proj_summary = "\n".join(f"- Month {p['month']}: {p['subscribers']:,} subs | {p['monthly_views']:,} views/month" for p in projections)
    prompt = f"""YouTube growth analyst. Analyze this channel's growth trajectory.\n\nNICHE: {niche}\nCURRENT SUBSCRIBERS: {subscribers:,}\nMONTHLY VIEWS: {monthly_views:,}\nMONTHLY GROWTH RATE: {monthly_growth_rate}%\nUPLOAD FREQUENCY: {upload_freq}\nCHANNEL AGE: {channel_age_months} months\n{niche_ctx}\n\nCALCULATED PROJECTIONS:\n{proj_summary}\n\n## \U0001f680 GROWTH PREDICTION REPORT\n\n### CURRENT TRAJECTORY\n- {monthly_growth_rate}% per month is [Slow/Average/Fast/Exceptional] for {niche}\n- Time to 1K, 10K, 100K subscribers\n- Current growth phase\n\n### 24-MONTH MILESTONE TABLE\n| Milestone | Subscribers | Monthly Views | Est. Revenue |\n|-----------|-------------|---------------|--------------|\n[Use the calculated projections above]\n\n### ACCELERATION SCENARIOS\n- Conservative (current rate): [outcome]\n- Optimized strategy (+2x): [what changes]\n- One viral video: [trajectory change]\n\n### TOP 3 GROWTH ACCELERATORS\n1. [Action + expected impact]\n2. [Action + expected impact]\n3. [Action + expected impact]\n\n### VERDICT\n[Honest 2-sentence assessment of this channel's trajectory]"""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"prediction": result, "projections": projections, "inputs": {"niche": niche, "subscribers": subscribers, "monthly_views": monthly_views, "growth_rate": monthly_growth_rate}, "niche": niche})


@app.route("/api/ti/hook_generator", methods=["POST"])
def ti_hook_generator():
    """Hook Generator — Generate 10+ viral hooks for any video."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:300]
    niche = (data.get("niche") or "").strip()[:200]
    if not title and not niche:
        return jsonify({"error": "Title or niche required"}), 400
    language = data.get("language", "English")[:50]
    hook_style = data.get("hook_style", "mixed")[:30]  # curiosity/shock/story/question/mixed
    video_type = data.get("video_type", "long-form")[:20]

    # Get autocomplete for demand signals
    import requests as _rh
    import re as _reh
    demand = []
    try:
        q = (title or niche)[:60]
        r = _rh.get("https://suggestqueries.google.com/complete/search",
            params={"client":"youtube","ds":"yt","q":q,"hl":"en"}, timeout=5,
            headers={"User-Agent":"Mozilla/5.0"})
        m = _reh.search(r'\((.+)\)$', r.text)
        if m:
            parsed = json.loads(m.group(1))
            demand = [s[0] for s in parsed[1][:6]] if len(parsed) > 1 else []
    except: pass

    demand_ctx = "\n".join(f"- {d}" for d in demand) if demand else "N/A"
    prompt = f"""You are a viral content hook specialist. Generate a complete hook arsenal for this video.

TITLE: {title or 'N/A'}
NICHE: {niche or 'General'}
LANGUAGE: {language}
HOOK STYLE: {hook_style}
VIDEO TYPE: {video_type}
YOUTUBE DEMAND SIGNALS:
{demand_ctx}

## \U0001f3af HOOK ARSENAL (10+ Hooks)

### TIER 1: PATTERN INTERRUPTS (first 3 seconds)
1. **[SHOCK]** "[Hook that shocks and stops scrolling]"
2. **[QUESTION]** "[Question that forces viewer to keep watching for the answer]"
3. **[BOLD CLAIM]** "[Controversial statement that demands attention]"

### TIER 2: CURIOSITY GAPS
4. **[SECRET]** "[Tease something hidden/unknown]"
5. **[CONTRAST]** "[Juxtapose two opposing ideas]"
6. **[FUTURE PAIN]** "[What happens if they DON\'T watch this]"

### TIER 3: STORY HOOKS (for long-form)
7. **[STORY]** "[In-media-res opening sentence]"
8. **[PERSONAL]** "[First-person vulnerable opening]"

### TIER 4: AUTHORITY HOOKS
9. **[DATA]** "[Specific statistic that surprises]"
10. **[EXPERT]** "[What experts/studies say]"

### BONUS: SHORTS HOOKS (\u226415 words)
11. "[Ultra-short Shorts hook]"
12. "[Another Shorts hook]"

### HOOK PSYCHOLOGY BREAKDOWN
| Hook # | Trigger | CTR Impact | Best For |
|--------|---------|------------|----------|
| 1-3 | Pattern interrupt | Highest | Cold audience |
| 4-6 | Curiosity | Very High | Search traffic |
| 7-8 | Story | High | Retention | 
| 9-10 | Authority | Medium-High | SEO/trust |

### BEST HOOK RECOMMENDATION
**TOP PICK:** "[Single best hook for this specific title/niche]"
**Why:** [2-sentence psychological reason]
**Expected AVD improvement:** +X% vs no hook"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"hooks": result, "title": title, "niche": niche, "demand_signals": demand, "hook_style": hook_style})


@app.route("/api/ti/viral_blueprint", methods=["POST"])
def ti_viral_blueprint():
    """Viral Blueprint — Complete video structure blueprint optimized for retention."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:300]
    if not title:
        return jsonify({"error": "Title required"}), 400
    niche = (data.get("niche") or "").strip()[:200]
    language = data.get("language", "English")[:50]
    duration = data.get("duration", "10-15 min")[:20]
    channel_style = data.get("channel_style", "educational")[:30]

    prompt = f"""You are a YouTube retention expert who creates video structures that keep viewers watching to the end.

VIDEO TITLE: {title}
NICHE: {niche or 'General'}
LANGUAGE: {language}
TARGET DURATION: {duration}
CHANNEL STYLE: {channel_style}

## \U0001f4cb VIRAL BLUEPRINT: \"{title}\"

### THE HOOK (0:00 - 0:30)
**Opening Hook:** [Exact script for first line]
**Pattern Interrupt:** [What visual/audio element grabs attention]
**Promise Statement:** [What you're promising the viewer]
**Why They Should Care:** [Stakes/relevance in 1 sentence]

### RETENTION ARCHITECTURE
| Timestamp | Section | Retention Technique | Script Direction |
|-----------|---------|--------------------|------------------|
| 0:00-0:30 | Hook | Pattern interrupt | [direction] |
| 0:30-2:00 | Setup | Stakes + promise | [direction] |
| 2:00-X:00 | Core Loop 1 | Reveal + re-hook | [direction] |
| X:00-Y:00 | Core Loop 2 | Surprise + escalate | [direction] |
| Y:00-Z:00 | Core Loop 3 | Peak revelation | [direction] |
| Z:00-End | CTA | Satisfying resolution | [direction] |

### RE-HOOKS (Retention Anchors)
Place these at minutes [X], [Y], [Z]:
1. "[Re-hook line to prevent drop-off at 30%]"
2. "[Re-hook at 50% mark]"
3. "[Re-hook at 70% mark]"

### THE CORE CONTENT STRUCTURE
**Main Topics to Cover (in this order for max retention):**
1. [Topic 1 - Start with this, builds curiosity]
2. [Topic 2 - Delivers on first promise]
3. [Topic 3 - Biggest revelation, save for 60% mark]
4. [Topic 4 - Satisfying conclusion]

### PATTERN INTERRUPTS (every 90-120 seconds)
- At minute [X]: [Interrupt type: zoom, graphic, stat, story]
- At minute [Y]: [Interrupt type]
- At minute [Z]: [Interrupt type]

### EMOTIONAL JOURNEY MAP
[Beginning] Curiosity \u2192 [30%] Surprise \u2192 [60%] Tension \u2192 [80%] Relief \u2192 [End] Satisfaction

### THE PERFECT OUTRO (CTA)
**Watch-time CTA:** [What to say to send to another video]
**Subscribe CTA:** [Exact subscribe line]
**Comment CTA:** [Question to spark engagement]

### THUMBNAIL BRIEF
- Background: [Color + style]
- Main element: [Person/object]
- Text overlay: [3-5 words max]
- Emotion: [What face/expression to show]

### PREDICTED PERFORMANCE
- Estimated AVD: X% if structure followed
- Viral probability: [Low/Medium/High/Very High]
- Best audience segment: [Type of viewer]"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"blueprint": result, "title": title, "niche": niche, "duration": duration})


@app.route("/api/ti/thumbnail_strategist", methods=["POST"])
def ti_thumbnail_strategist():
    """Thumbnail Strategist — AI-powered thumbnail strategy with psychology."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:300]
    niche = (data.get("niche") or "").strip()[:200]
    if not title and not niche:
        return jsonify({"error": "Title or niche required"}), 400
    language = data.get("language", "English")[:50]
    channel_style = data.get("channel_style", "educational")[:30]
    competitor_style = data.get("competitor_style", "")[:200]

    # Load niche thumbnail data
    thumb_intel = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        for nk, intel in NICHE_INTELLIGENCE.items():
            if nk.lower() in (niche or title).lower() or (niche or title).lower() in nk.lower():
                thumb_intel = f"\nNICHE THUMBNAIL PATTERN for '{nk}': {intel['thumbnail_style']}"
                break
    except: pass

    prompt = f"""You are a YouTube thumbnail psychologist and CTR optimization expert. Create a complete thumbnail strategy.

TITLE: {title or 'N/A'}
NICHE: {niche or 'General'}
LANGUAGE: {language}
CHANNEL STYLE: {channel_style}
{thumb_intel}
{f'COMPETITOR STYLE: {competitor_style}' if competitor_style else ''}

## \U0001f5bc\ufe0f THUMBNAIL STRATEGY REPORT

### THUMBNAIL BRIEF (give to your designer/AI)
**Concept:** [One sentence describing the main visual idea]
**Composition:** [How elements are arranged]
**Background:** [Color hex #XXXXXX + why this color works psychologically]
**Foreground:** [Main subject: person/object/graphic]
**Text Overlay:** [EXACT text, max 4 words in CAPS] — Font: [Bold/Impact]
**Expression/Emotion:** [Exact emotion if person is shown: shock/curiosity/joy/anger]

### COLOR PSYCHOLOGY
| Color | Hex | Psychological Trigger | CTR Impact |
|-------|-----|----------------------|------------|
| Primary | #XXXXXX | [trigger] | +X% |
| Accent | #XXXXXX | [trigger] | [impact] |
| Text | #XXXXXX | [readability] | [impact] |

### 3 THUMBNAIL VARIATIONS (test these)
**Variation A: [High CTR - Face-based]**
- Visual: [description]
- Text: [text]
- Why: [psychology]

**Variation B: [Curiosity-based]**
- Visual: [description]
- Text: [text]
- Why: [psychology]

**Variation C: [Pattern Interrupt]**
- Visual: [description]
- Text: [text]
- Why: [psychology]

### THUMBNAIL MISTAKES TO AVOID
1. [Common mistake in {niche} thumbnails]
2. [Another common mistake]
3. [Third mistake]

### CTR PREDICTION
- Expected CTR range: X%-Y%
- Key CTR driver: [main element]
- Mobile optimization: [how it looks on mobile screen]

### MIDJOURNEY / DALL-E PROMPT
`[Ready-to-use AI image generation prompt for this thumbnail]`"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"strategy": result, "title": title, "niche": niche, "channel_style": channel_style})


@app.route("/api/ti/shorts_optimizer", methods=["POST"])
def ti_shorts_optimizer():
    """Shorts Optimizer — Specific strategy for YouTube Shorts growth."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:200]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    topic = (data.get("topic") or "").strip()[:300]
    language = data.get("language", "English")[:50]
    goal = data.get("goal", "growth")[:30]  # growth/monetization/brand

    # Get trending shorts topics
    import requests as _rs
    import re as _res
    trending = []
    try:
        for q in [niche, f"{niche} shorts"][:2]:
            r = _rs.get("https://suggestqueries.google.com/complete/search",
                params={"client":"youtube","ds":"yt","q":q[:60],"hl":"en"}, timeout=5,
                headers={"User-Agent":"Mozilla/5.0"})
            m = _res.search(r'\((.+)\)$', r.text)
            if m:
                parsed = json.loads(m.group(1))
                trending += [s[0] for s in parsed[1][:5]] if len(parsed) > 1 else []
    except: pass
    trending = list(dict.fromkeys(trending))[:12]

    trending_ctx = "\n".join(f"- {t}" for t in trending) if trending else "N/A"
    prompt = f"""You are a YouTube Shorts specialist who has studied the Shorts algorithm deeply.

NICHE: {niche}
TOPIC: {topic or 'General ' + niche + ' content'}
LANGUAGE: {language}
GOAL: {goal}
TRENDING SEARCHES:
{trending_ctx}

## \u26a1 SHORTS STRATEGY FOR {niche.upper()}

### SHORTS ALGORITHM SIGNALS (what matters most)
1. **Swipe-away rate** — target: keep below X% in first 1s
2. **Full-watch rate** — must achieve X%+ for viral push
3. **Re-watches** — how to make them rewatch
4. **Shares** — what makes shorts shareable in {niche}

### TOP 10 SHORTS IDEAS (ready to film)
| # | Title/Hook (\u226415 words) | Format | Viral Potential |
|---|--------------------------|--------|----------------|
| 1 | [hook] | [talking head/screen/animation] | [High/Med] |
[Continue for all 10]

### PERFECT SHORTS STRUCTURE (59 seconds)
- **0-2s:** [Hook - what to say/show immediately]
- **3-15s:** [Core value delivery]
- **16-45s:** [Main content]
- **46-55s:** [Key reveal/payoff]
- **56-59s:** [CTA: subscribe + next Short to watch]

### SHORTS FORMAT THAT WORKS IN {niche}
- Best format: [Talking head / Screen record / Animation / POV]
- Captions: [On/Off] — [why]
- Music: [Yes/No] — [type]
- Face required: [Yes/No] — [why]

### UPLOAD STRATEGY
- Frequency: X Shorts/day for viral momentum
- Best posting time: [Day HH:MM]
- Hashtags: [3-5 Shorts-specific hashtags]

### SHORTS SERIES IDEAS
[3 recurring series formats that build subscriber habit]

### SHORTS TO LONG-FORM FUNNEL
How to convert Shorts viewers to long-form subscribers:
[Specific strategy with CTA examples]

### MONETIZATION (Shorts RPM Reality)
- Current Shorts RPM: $0.03-$0.08 per 1000 views
- Break-even point: [X views/month]
- Better monetization strategy: [alternative beyond ads]"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"strategy": result, "niche": niche, "topic": topic, "trending_topics": trending, "goal": goal})


@app.route("/api/ti/script_writer", methods=["POST"])
def ti_script_writer():
    """Script Writer — Generate a complete video script from scratch."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:300]
    if not title:
        return jsonify({"error": "Title required"}), 400
    niche = (data.get("niche") or "").strip()[:200]
    language = data.get("language", "English")[:50]
    duration = data.get("duration", "10 min")[:20]
    style = data.get("style", "educational")[:30]
    audience = data.get("audience", "")[:200]
    include_broll = data.get("include_broll", True)

    prompt = f"""You are a professional YouTube scriptwriter who has written for channels with millions of subscribers. Write a complete, ready-to-record video script.

VIDEO TITLE: {title}
NICHE: {niche or 'General'}
LANGUAGE: {language}
TARGET DURATION: {duration}
STYLE: {style}
AUDIENCE: {audience or 'General YouTube viewers'}

## \U0001f4dd COMPLETE VIDEO SCRIPT: \"{title}\"

---
### [INTRO — 0:00-0:30] HOOK SEQUENCE
*[VISUAL: Describe what appears on screen]*

[SPEAKER - EXACT WORDS TO SAY]:
\"[Open with your strongest hook — a shocking statement, question, or bold claim]\"

*[B-ROLL SUGGESTION: {"Include specific B-roll directions" if include_broll else "No B-roll included"}]*

\"[The promise — what will they learn/gain by watching this video]\"
\"[Pattern interrupt / tease the most shocking part]\"

---
### [SECTION 1 — 2:00-4:00] [TITLE OF SECTION]
*[VISUAL: Screen graphic / talking head / B-roll]*

[SPEAKER]:
\"[Transition from hook to first main point]\"
\"[Key insight / information / story]\"
\"[Supporting evidence, example, or anecdote]\"

*[RE-HOOK at 2:30]: \"[Line that prevents people from leaving]\"

---
### [SECTION 2 — 4:00-7:00] [TITLE OF SECTION]
*[VISUAL]*

[SPEAKER]:
\"[Build on section 1]\"
\"[The core revelation / main value]\"
\"[Specific example or case study]\"

*[PATTERN INTERRUPT — zoom, graphic, sound effect at 5:30]*

---
### [SECTION 3 — 7:00-9:00] [TITLE OF SECTION]
*[VISUAL]*

[SPEAKER]:
\"[Build tension / escalate to peak revelation]\"
\"[The biggest insight — save this for 65-75% of the video]\"
\"[Resolution / how to use this information]\"

---
### [OUTRO — 9:00-{duration}] CALL TO ACTION SEQUENCE

[SPEAKER]:
\"[Summary: what they just learned in 2 sentences]\"

*[Pause for effect]*

\"[Subscribe CTA — specific compelling reason to subscribe]\"
\"[Comment CTA — specific question that generates engagement]\"
\"[Next video CTA — tease the next video they should watch]\"

---
## PRODUCTION NOTES
- **Total Word Count:** ~X words (for {duration} at 130 wpm)
- **Tone:** [Conversational/authoritative/curious]
- **Key Moments to Emphasize:** [timestamps]
- **Music:** [Background music mood suggestions]
- **Thumbnail Moment:** [Best visual moment to screenshot for thumbnail]

## SEO METADATA
- **Primary Keyword:** [main search term]
- **Secondary Keywords:** [2-3 related terms]
- **Suggested Tags:** [8-10 tags]

Write the COMPLETE script — every single word the presenter should say, not just an outline."""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"script": result, "title": title, "niche": niche, "duration": duration, "style": style})


@app.route("/api/ti/keyword_research", methods=["POST"])
def ti_keyword_research():
    """Keyword Research — YouTube keyword explorer with difficulty/volume/competition scoring."""
    import requests as _rk
    import re as _rek
    data = request.json or {}
    keyword = (data.get("keyword") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:200]
    if not keyword and not niche:
        return jsonify({"error": "Keyword or niche required"}), 400
    language = data.get("language", "English")[:50]
    lang_code = data.get("lang_code", "en")[:5]
    region = data.get("region", "US")[:5]

    # Fetch real autocomplete data for multiple seed variations
    seeds = [keyword or niche]
    if keyword:
        seeds += [f"how to {keyword}", f"{keyword} tips", f"best {keyword}", f"{keyword} for beginners"]

    all_suggestions = []
    try:
        for seed in seeds[:4]:
            r = _rk.get("https://suggestqueries.google.com/complete/search",
                params={"client":"youtube","ds":"yt","q":seed[:60],"hl":lang_code},
                timeout=6, headers={"User-Agent":"Mozilla/5.0"})
            m = _rek.search(r'\((.+)\)$', r.text)
            if m:
                parsed = json.loads(m.group(1))
                if len(parsed) > 1:
                    all_suggestions += [s[0] for s in parsed[1][:8]]
    except: pass

    all_suggestions = list(dict.fromkeys(all_suggestions))[:40]
    suggestions_ctx = "\n".join(f"- {s}" for s in all_suggestions) if all_suggestions else "N/A"

    # Load niche RPM/competition data
    niche_data = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        for nk, intel in NICHE_INTELLIGENCE.items():
            if nk.lower() in (niche or keyword).lower() or (niche or keyword).lower() in nk.lower():
                niche_data = f"\nNICHE INTEL: RPM ${intel['rpm_usd'][0]}-${intel['rpm_usd'][1]}, Target: {intel['target_demo']}"
                break
    except: pass

    prompt = f"""You are a YouTube SEO specialist who reverse-engineers the search algorithm. Perform a comprehensive keyword research analysis.

PRIMARY KEYWORD: {keyword or niche}
NICHE: {niche or 'General'}
LANGUAGE: {language}
REGION: {region}
{niche_data}

REAL YOUTUBE AUTOCOMPLETE DATA (actual search demand):
{suggestions_ctx}

## \U0001f511 KEYWORD RESEARCH REPORT: \"{keyword or niche}\"

### PRIMARY KEYWORD ANALYSIS
| Metric | Score | Details |
|--------|-------|--------|
| Search Volume | [High/Med/Low] | Est. X-Y searches/month |
| Competition | [Score]/10 | [Why easy/hard to rank] |
| CPM/RPM Value | $X-$Y | [Advertiser demand] |
| Trend Direction | \u2191\u2193\u2192 | [Growing/Declining/Stable] |
| Best Content Type | [format] | [Why this format wins] |

### KEYWORD OPPORTUNITY MATRIX
| Keyword | Search Intent | Difficulty (1-10) | Est. Volume | CPM | Opportunity |
|---------|-------------|-------------------|-------------|-----|-------------|
[List 10-15 keywords from the autocomplete data above, with scores]

### GOLDEN KEYWORDS (Hidden Gems)
These are high-value, low-competition keywords in this niche:
1. \"[keyword]\" — Difficulty: X/10 | Volume: [High/Med/Low] | Why: [reason]
2. \"[keyword]\" — Difficulty: X/10 | Volume: [High/Med/Low] | Why: [reason]
3. \"[keyword]\" — Difficulty: X/10 | Volume: [High/Med/Low] | Why: [reason]
4. \"[keyword]\" — Difficulty: X/10 | Volume: [High/Med/Low] | Why: [reason]
5. \"[keyword]\" — Difficulty: X/10 | Volume: [High/Med/Low] | Why: [reason]

### LONG-TAIL GOLDMINE (easiest to rank)
[List 8 long-tail keywords with low competition]

### SEARCH INTENT BREAKDOWN
- **Informational** (\"how to\", \"what is\"): X% of searches — [content type to create]
- **Educational** (\"tutorial\", \"learn\"): X% — [format]
- **Inspirational** (\"best\", \"top\"): X% — [format]
- **Transactional** (\"buy\", \"review\"): X% — [monetization opportunity]

### TITLE FORMULAS THAT RANK
5 proven title structures for \"[primary keyword]\" that rank on YouTube search:
1. [Formula with example]
2. [Formula with example]
3. [Formula with example]
4. [Formula with example]
5. [Formula with example]

### COMPETITION ANALYSIS
- Top channels ranking for this keyword: [types of channels]
- Average views of top 10 results: [range]
- What makes the top videos win: [2-3 patterns]
- Your entry strategy: [specific advice for new vs established channels]

### CONTENT STRATEGY USING THESE KEYWORDS
Create this sequence of videos to dominate this keyword cluster:
1. Start with: [easiest keyword] — [why start here]
2. Then: [next keyword]
3. Then: [next]
4. Build to: [hardest/most valuable keyword]"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"research": result, "keyword": keyword or niche, "suggestions_found": len(all_suggestions), "raw_suggestions": all_suggestions[:20], "niche": niche, "region": region})


@app.route("/api/ti/audience_builder", methods=["POST"])
def ti_audience_builder():
    """Audience Builder — Build a detailed audience persona for your channel."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:200]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    channel_type = (data.get("channel_type") or "").strip()[:100]
    language = data.get("language", "English")[:50]
    existing_audience = (data.get("existing_audience") or "").strip()[:500]

    # Load niche data for persona accuracy
    niche_intel = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        for nk, intel in NICHE_INTELLIGENCE.items():
            if nk.lower() in niche.lower() or niche.lower() in nk.lower():
                niche_intel = f"\nNICHE DATA: RPM ${intel['rpm_usd'][0]}-${intel['rpm_usd'][1]} | Demo: {intel['target_demo']} | Best time: {intel['best_time']}"
                break
    except: pass

    prompt = f"""You are a YouTube audience research expert and behavioral psychologist. Build a complete, data-driven audience persona.

NICHE: {niche}
CHANNEL TYPE: {channel_type or 'General YouTube channel'}
LANGUAGE: {language}
{niche_intel}
{f'EXISTING AUDIENCE NOTES: {existing_audience}' if existing_audience else ''}

## \U0001f465 AUDIENCE PERSONA REPORT: \"{niche}\"

### PRIMARY PERSONA: \"[Give them a name, e.g., 'Alex the Ambitious']\"
*[One sentence character summary]*

### DEMOGRAPHICS
| Factor | Details |
|--------|--------|
| Age Range | [Primary: X-Y years, Secondary: Y-Z years] |
| Gender Split | [X% Male / Y% Female / Z% Other] |
| Location | [Top 3 countries] |
| Income Level | [$X-$Y/year] |
| Education | [Most common level] |
| Occupation | [Most common jobs/fields] |
| Device | [X% Mobile / Y% Desktop / Z% TV] |
| Watch Time | [When they watch: morning/evening/weekend] |

### PSYCHOGRAPHICS (Why they watch)
**Core Motivations:**
- Primary: [The #1 reason they watch this type of content]
- Secondary: [Second reason]
- Tertiary: [Third reason]

**Core Fears & Pain Points:**
1. [Their biggest fear related to this niche]
2. [Their biggest frustration]
3. [What keeps them up at night]
4. [Their biggest challenge]

**Dreams & Desires:**
1. [What they desperately want]
2. [Their ideal outcome]
3. [Status they want to achieve]

**Values:**
- They believe: [core belief]
- They distrust: [what they reject]
- They aspire to: [identity they want]

### CONTENT CONSUMPTION BEHAVIOR
**Watch Triggers (what makes them click):**
1. [Trigger 1]
2. [Trigger 2]
3. [Trigger 3]

**Skip Triggers (what makes them leave):**
1. [What causes them to swipe away]
2. [Content format they hate]
3. [Topics that bore them]

**Ideal Video Length:** X-Y minutes (because [reason])
**Preferred Style:** [Talking head / Animated / Documentary / Tutorial]
**Caption Needs:** [Yes/No/Optional] — [why]

### WHERE TO FIND THEM (Distribution Strategy)
| Platform | Usage | How to reach |
|----------|-------|-------------|
| YouTube Search | X% | Keywords: [top 3] |
| Browse/Suggested | X% | [What triggers recommendation] |
| YouTube Shorts | X% | [Short format topics that work] |
| Reddit | X% | Subreddits: [top 3] |
| Instagram | X% | [Content type] |
| TikTok | X% | [Hook style] |

### MESSAGING THAT CONVERTS
**Words/phrases that RESONATE with this audience:**
[10 specific words/phrases they love to hear]

**Words/phrases to AVOID:**
[5 words that repel this audience]

**Emotional Triggers that Drive Action:**
1. [Primary trigger: e.g., FOMO, social proof, authority]
2. [Secondary trigger]
3. [Tertiary trigger]

### TITLE FORMULA FOR THIS PERSONA
\"[Example title perfectly crafted for this specific audience]\"
**Why it works:** [psychological reason]

### THUMBNAIL PSYCHOLOGY FOR THIS PERSONA
- Face expression they respond to: [specific emotion]
- Color palette that converts: [colors + hex]
- Text that resonates: [style/words]

### MONETIZATION MATCH
- RPM Potential: $X-$Y/1000 views
- Best sponsorship categories: [3 types]
- Membership likelihood: [Low/Med/High] — [what they'd pay for]
- Merch potential: [products that match their identity]

### SECONDARY PERSONA (Smaller but valuable segment)
\"[Second persona name]\"
[Brief profile: 3-4 sentences]
[How to create content that serves BOTH personas simultaneously]"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"persona": result, "niche": niche, "channel_type": channel_type, "language": language})


@app.route("/api/ti/comment_analyzer", methods=["POST"])
def ti_comment_analyzer():
    """Comment Analyzer — Analyze YouTube comments for sentiment, insights, and content ideas."""
    import requests as _rc
    data = request.json or {}
    comments_text = (data.get("comments") or "").strip()[:8000]
    video_url = (data.get("video_url") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:200]
    language = data.get("language", "English")[:50]
    yt_key = data.get("yt_api_key") or ""
    try:
        from core.api_keys import load_api_key as _lak_c
        yt_key = yt_key or _lak_c("youtube") or ""
    except: pass

    # Try to fetch real comments if video URL + YouTube API key provided
    fetched_comments = []
    video_title = ""
    if yt_key and video_url:
        try:
            import re as _rec
            # Extract video ID
            vid_match = _rec.search(r'(?:v=|youtu\.be/|/shorts/)([\w-]{11})', video_url)
            if vid_match:
                vid_id = vid_match.group(1)
                r = _rc.get("https://www.googleapis.com/youtube/v3/commentThreads",
                    params={"part":"snippet","videoId":vid_id,"maxResults":100,"order":"relevance","key":yt_key},
                    timeout=10)
                items = r.json().get("items", [])
                for item in items:
                    c = item.get("snippet",{}).get("topLevelComment",{}).get("snippet",{})
                    text = c.get("textDisplay","").strip()[:300]
                    likes = c.get("likeCount", 0)
                    if text:
                        fetched_comments.append({"text": text, "likes": likes})
                # Get video title
                vr = _rc.get("https://www.googleapis.com/youtube/v3/videos",
                    params={"part":"snippet","id":vid_id,"key":yt_key}, timeout=8)
                vitems = vr.json().get("items", [])
                if vitems:
                    video_title = vitems[0]["snippet"]["title"]
        except: pass

    # Build analysis corpus
    if fetched_comments:
        corpus = "\n".join(f"[{c['likes']} likes] {c['text']}" for c in fetched_comments[:80])
        source = f"REAL YouTube comments fetched ({len(fetched_comments)} comments)"
    elif comments_text:
        corpus = comments_text[:5000]
        source = "Comments provided manually"
        fetched_comments = [{"text": l, "likes": 0} for l in comments_text.split("\n") if l.strip()]
    else:
        return jsonify({"error": "Provide comments text or a video URL with YouTube API key"}), 400

    prompt = f"""You are a YouTube audience intelligence analyst. Perform a deep analysis of these YouTube comments to extract actionable insights.

VIDEO: {video_title or video_url or 'N/A'}
NICHE: {niche or 'General'}
LANGUAGE: {language}
SOURCE: {source}
COMMENTS TO ANALYZE:
{corpus[:4000]}

## \U0001f4ac COMMENT INTELLIGENCE REPORT

### SENTIMENT OVERVIEW
| Sentiment | % | Key Phrases |
|-----------|---|-------------|
| \U0001f7e2 Positive | X% | [phrases] |
| \U0001f534 Negative | X% | [phrases] |
| \U0001f7e1 Neutral | X% | [phrases] |
| \U0001f4a1 Questions | X% | [phrases] |
| \U0001f525 Enthusiastic | X% | [phrases] |

**Overall Sentiment Score:** X/10
**Community Health:** [Toxic / Neutral / Supportive / Highly Engaged]

### TOP PRAISED ELEMENTS
What the audience loved:
1. [Most praised aspect]
2. [Second most praised]
3. [Third most praised]

### TOP COMPLAINTS / CRITICISMS
What the audience disliked or asked to improve:
1. [Biggest complaint]
2. [Second complaint]
3. [Third complaint]

### AUDIENCE QUESTIONS (Content Ideas)
The most asked questions in comments (each = a future video idea):
1. \"[Question]\" — Video idea: [title suggestion]
2. \"[Question]\" — Video idea: [title suggestion]
3. \"[Question]\" — Video idea: [title suggestion]
4. \"[Question]\" — Video idea: [title suggestion]
5. \"[Question]\" — Video idea: [title suggestion]

### EMOTIONAL TRIGGERS DETECTED
Emotions expressed in comments:
1. [Emotion]: [% of comments] — [what triggered it]
2. [Emotion]: [%] — [trigger]
3. [Emotion]: [%] — [trigger]

### AUDIENCE VOCABULARY
Words/phrases YOUR audience uses (use these in future titles/scripts):
[20 specific words/phrases from the comments]

### MOST ENGAGING COMMENT PATTERNS
Types of comments that got the most likes:
1. [Pattern]: \"[Example comment]\" — [why it resonated]
2. [Pattern]: \"[Example]\"
3. [Pattern]: \"[Example]\"

### CONTENT OPPORTUNITIES (from comment data)
Immediate video ideas based on what the audience is asking for:
| Video Title Idea | Priority | Reason |
|-----------------|----------|---------|
| [title] | HIGH | [demand signal] |
| [title] | HIGH | [demand signal] |
| [title] | MED | [demand signal] |
| [title] | MED | [demand signal] |

### CREATOR RECOMMENDATIONS
Based on this comment analysis:
1. **Do more of:** [specific content direction]
2. **Stop doing:** [what's not working]
3. **Respond to:** [specific comments to engage with]
4. **Pin this comment:** [type of comment to pin for engagement]
5. **Next video:** [most requested topic]"""

    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "analysis": result,
        "comments_analyzed": len(fetched_comments),
        "source": source,
        "video_title": video_title,
        "has_real_data": bool(fetched_comments and yt_key)
    })


@app.route("/api/ti/auto_pilot", methods=["POST"])
def ti_auto_pilot():
    """AI Auto-Pilot — One-click channel analysis + weekly content plan."""
    data = request.json or {}
    channel = (data.get("channel") or "").strip()[:300]
    niche = (data.get("niche") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    channel_size = data.get("channel_size", "small")[:20]
    goals = (data.get("goals") or "").strip()[:300]
    if not channel and not niche:
        return jsonify({"error": "Channel name or niche required"}), 400
    niche_context = ""
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        for nk, intel in NICHE_INTELLIGENCE.items():
            if nk.lower() in (niche or channel).lower() or (niche or channel).lower() in nk.lower():
                niche_context = f"\nNICHE INTEL: RPM ${intel['rpm_usd'][0]}-${intel['rpm_usd'][1]} | Demo: {intel['target_demo']} | Best time: {intel['best_time']}"
                break
    except: pass
    prompt = f"""You are a senior YouTube strategist who has grown 500+ channels to monetization. Act as an AI Auto-Pilot that analyzes a YouTube channel and generates a complete weekly action plan.

CHANNEL: {channel or 'Not specified'}
NICHE: {niche or 'Auto-detect from channel name'}
LANGUAGE: {language}
CHANNEL SIZE: {channel_size} (small=<10k, medium=10k-100k, large=100k+)
GOALS: {goals or 'Grow subscribers, increase views, get monetized'}
{niche_context}

## \U0001f916 AI AUTO-PILOT REPORT \u2014 {channel or niche}

### \U0001f4ca CHANNEL DIAGNOSIS
**Overall Score: [X]/100** \u2014 [Grade: S/A/B/C/D]

| Dimension | Score | Status |
|-----------|-------|--------|
| Content Strategy | X/10 | [\u2705/\u26a0\ufe0f/\u274c] [reason] |
| SEO & Discovery | X/10 | [\u2705/\u26a0\ufe0f/\u274c] [reason] |
| Upload Consistency | X/10 | [\u2705/\u26a0\ufe0f/\u274c] [reason] |
| Audience Connection | X/10 | [\u2705/\u26a0\ufe0f/\u274c] [reason] |
| Monetization Readiness | X/10 | [\u2705/\u26a0\ufe0f/\u274c] [reason] |
| Viral Potential | X/10 | [\u2705/\u26a0\ufe0f/\u274c] [reason] |
| Niche Authority | X/10 | [\u2705/\u26a0\ufe0f/\u274c] [reason] |
| Thumbnail & CTR | X/10 | [\u2705/\u26a0\ufe0f/\u274c] [reason] |
| Hook Quality | X/10 | [\u2705/\u26a0\ufe0f/\u274c] [reason] |
| Algorithm Alignment | X/10 | [\u2705/\u26a0\ufe0f/\u274c] [reason] |

**\U0001f4aa 3 Biggest Strengths:**
1. [Strength with specific advice]
2. [Strength with specific advice]
3. [Strength with specific advice]

**\u26a0\ufe0f 3 Critical Gaps (fix these first):**
1. [Gap] \u2192 Quick fix: [action]
2. [Gap] \u2192 Quick fix: [action]
3. [Gap] \u2192 Quick fix: [action]

---

### \U0001f4c5 7-DAY CONTENT PLAN

| Day | Upload? | Video Topic | Full Title | Best Time |
|-----|---------|-------------|------------|----------|
| Monday | [YES/REST] | [topic] | [full optimized title] | [HH:MM] |
| Tuesday | [YES/REST] | [topic] | [full optimized title] | [HH:MM] |
| Wednesday | [YES/REST] | [topic] | [full optimized title] | [HH:MM] |
| Thursday | [YES/REST] | [topic] | [full optimized title] | [HH:MM] |
| Friday | [YES/REST] | [topic] | [full optimized title] | [HH:MM] |
| Saturday | [YES/REST] | [topic] | [full optimized title] | [HH:MM] |
| Sunday | [YES/REST] | [topic] | [full optimized title] | [HH:MM] |

**Upload Frequency Recommendation:** [X videos/week \u2014 why]

---

### \U0001f525 5 VIRAL TITLES \u2014 READY TO RECORD NOW

1. **[Title 1]**
   - Structure: [pattern used] | Viral element: [hook type] | Est. CTR: [X-Y%]
   - Thumbnail: [concept in 10 words]

2. **[Title 2]**
   - Structure: [pattern] | Viral element: [element] | Est. CTR: [X-Y%]
   - Thumbnail: [concept]

3. **[Title 3]**
   - Structure: [pattern] | Viral element: [element] | Est. CTR: [X-Y%]

4. **[Title 4]**
   - Structure: [pattern] | Viral element: [element] | Est. CTR: [X-Y%]

5. **[Title 5]**
   - Structure: [pattern] | Viral element: [element] | Est. CTR: [X-Y%]

---

### \u26a1 3 QUICK WINS (Do This Week)

1. **[Action]** \u2014 Time: [X min] | Impact: [expected result]
2. **[Action]** \u2014 Time: [X min] | Impact: [expected result]
3. **[Action]** \u2014 Time: [X min] | Impact: [expected result]

---

### \U0001f4c8 30-DAY GROWTH PROJECTION

- Week 1: [expected change]
- Week 2: [expected change]
- Week 3: [expected change]
- Week 4: [expected change]
- Month 1 total: [subscriber/view projection]
- Path to monetization: [timeline]

---

### \U0001f9ec CHANNEL DNA PROFILE
- **Content Pillars:** [pillar 1], [pillar 2], [pillar 3]
- **Voice Style:** [authoritative/casual/educational/entertainment]
- **Hook Style:** [type that works for this niche]
- **Never make videos about:** [what to avoid]"""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"plan": result, "channel": channel, "niche": niche, "language": language, "channel_size": channel_size})


@app.route("/api/ti/analytics_dashboard", methods=["GET"])
def ti_analytics_dashboard():
    """Analytics Dashboard — Real-time TI usage stats + niche trend alerts."""
    global _ti_session_start, _ti_call_counts
    if _ti_session_start is None:
        _ti_session_start = _dt.datetime.now()
    uptime_secs = int((_dt.datetime.now() - _ti_session_start).total_seconds())
    h, rem = divmod(uptime_secs, 3600)
    m, s = divmod(rem, 60)
    uptime_str = f"{h:02d}:{m:02d}:{s:02d}"
    ti_routes = [str(r.rule) for r in app.url_map.iter_rules() if r.rule.startswith('/api/ti/')]
    trend_alerts = []
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        for niche, data in list(NICHE_INTELLIGENCE.items())[:8]:
            rpm = data.get('rpm_usd', [5, 20])
            trend_alerts.append({
                'niche': niche,
                'rpm_min': rpm[0],
                'rpm_max': rpm[1],
                'demand': data.get('demand', 'High'),
                'target_demo': data.get('target_demo', ''),
                'best_time': data.get('best_time', ''),
            })
    except Exception:
        trend_alerts = [
            {'niche': 'Finance', 'rpm_min': 15, 'rpm_max': 45, 'demand': 'High', 'target_demo': '25-45', 'best_time': '18:00-21:00'},
            {'niche': 'Psychology', 'rpm_min': 8, 'rpm_max': 25, 'demand': 'Very High', 'target_demo': '18-35', 'best_time': '19:00-22:00'},
        ]
    total_calls = sum(_ti_call_counts.values())
    top_tools = sorted(_ti_call_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    return jsonify({
        'uptime': uptime_str,
        'uptime_seconds': uptime_secs,
        'ti_routes': len(ti_routes),
        'total_calls': total_calls,
        'call_counts': _ti_call_counts,
        'top_tools': [{'tool': t, 'count': c} for t, c in top_tools],
        'trend_alerts': trend_alerts,
        'phases_complete': 7,
        'sidebar_groups': 11,
        'session_start': _ti_session_start.isoformat(),
    })


@app.route("/api/ti/track_call", methods=["POST"])
def ti_track_call():
    """Track a TI tool call for analytics."""
    global _ti_call_counts
    data = request.json or {}
    tool = (data.get('tool') or '').strip()[:50]
    if tool:
        _ti_call_counts[tool] = _ti_call_counts.get(tool, 0) + 1
    return jsonify({'ok': True, 'count': _ti_call_counts.get(tool, 0)})


@app.route("/api/ti/channel_deep_dive", methods=["POST"])
def ti_channel_deep_dive():
    """Phase 7.4 — Deep channel analysis: real YouTube data + AI insights."""
    data = request.json or {}
    channel_input = (data.get("channel") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not channel_input:
        return jsonify({"error": "Channel URL or ID required"}), 400
    from core.api_keys import load_api_key as _lak
    api_key = (data.get("yt_api_key") or "").strip() or _lak("youtube") or ""
    if not api_key:
        return jsonify({"error": "YouTube API key required — configure em Configurações → YouTube API Key"}), 400
    channel_info = {}
    videos = []
    try:
        import sys as _sys
        _pdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _pdir not in _sys.path:
            _sys.path.insert(0, _pdir)
        from core.youtube_api import get_channel_info, get_channel_videos
        channel_info = get_channel_info(channel_input, api_key)
        if channel_info and channel_info.get("id"):
            videos = get_channel_videos(channel_info["id"], api_key, max_videos=20)
    except Exception as e:
        return jsonify({"error": f"YouTube API error: {str(e)}"}), 500
    if not channel_info or channel_info.get("error"):
        return jsonify({"error": "Channel not found. Check the channel URL/ID and API key."}), 404
    ch_name = channel_info.get("name", channel_input)
    subs = channel_info.get("subscribers", 0)
    total_views = channel_info.get("views", 0)
    total_videos_count = channel_info.get("video_count", 0)
    video_summary = ""
    vph_list = []
    for i, v in enumerate(videos[:15], 1):
        vph = v.get("vph", 0) or 0
        vph_list.append(vph)
        views = v.get("views", 0) or 0
        title = (v.get("title") or "")[:80]
        ago = v.get("published_ago", "") or ""
        video_summary += f"{i}. '{title}' — {views:,} views | VPH: {vph:.0f} | {ago}\n"
    avg_vph = sum(vph_list) / len(vph_list) if vph_list else 0
    prompt = f"""You are a professional YouTube strategist with 10+ years experience growing channels. Analyze this channel's REAL YouTube data and provide specific, data-backed insights.

CHANNEL: {ch_name}
SUBSCRIBERS: {subs:,}
TOTAL VIEWS: {total_views:,}
TOTAL VIDEOS: {total_videos_count:,}
AVERAGE VPH (recent): {avg_vph:.1f}
LANGUAGE OUTPUT: {language}

LAST {len(videos[:15])} VIDEOS (real YouTube API data):
{video_summary or 'No video data available'}

Generate a DEEP DIVE report in {language}:

### 🎯 CHANNEL DIAGNOSIS
2-line summary of channel health, positioning, and growth stage.

### 📊 PERFORMANCE METRICS
- Avg VPH: {avg_vph:.1f} views/hour → [above/below/at] niche average
- Best performing video: [from data above]
- Weakest performing video: [from data above]
- Views/Subscriber ratio: {(total_views / max(subs, 1)):.1f}x → [interpretation]
- Upload velocity: [estimate from data]

### 🔥 3 KEY INSIGHTS (data-driven)
1. [Insight with specific data point from above]
2. [Insight with specific data point]
3. [Insight with specific data point]

### 📌 TITLE PATTERNS THAT WORK (based on top videos)
From the data, these patterns drive the most views:
1. [Pattern — e.g., "How to X in Y" format]
2. [Pattern]
3. [Pattern]

### ⚡ 3 IMMEDIATE ACTIONS (do this week)
1. [Specific action — e.g., "Remake video #X with new title: '...'"]
2. [Specific action]
3. [Specific action]

### 🚀 30-DAY GROWTH PLAN
Week 1: [specific tasks]
Week 2: [specific tasks]
Week 3: [specific tasks]
Week 4: [specific tasks]
Expected result: [realistic projection based on current VPH]"""
    ai_insights = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "channel": channel_info,
        "videos": videos[:15],
        "ai_insights": ai_insights,
        "avg_vph": avg_vph,
        "video_count_fetched": len(videos),
    })


@app.route("/api/ti/video_performance", methods=["POST"])
def ti_video_performance():
    """Phase 7.4 — Analyze a specific video's performance vs channel avg."""
    data = request.json or {}
    video_url = (data.get("video_url") or "").strip()[:300]
    channel_avg_vph = float(data.get("channel_avg_vph") or 0)
    language = data.get("language", "Portuguese")[:50]
    if not video_url:
        return jsonify({"error": "Video URL required"}), 400
    # Extract video ID
    import re as _re
    vid_match = _re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', video_url)
    video_id = vid_match.group(1) if vid_match else video_url.strip()
    from core.api_keys import load_api_key as _lak
    api_key = (data.get("yt_api_key") or "").strip() or _lak("youtube") or ""
    if not api_key:
        return jsonify({"error": "YouTube API key required"}), 400
    try:
        import requests as _req, sys as _sys
        url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={video_id}&key={api_key}"
        r = _req.get(url, timeout=10)
        items = r.json().get("items", [])
        if not items:
            return jsonify({"error": "Video not found"}), 404
        item = items[0]
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        title = snippet.get("title", "")
        channel_name = snippet.get("channelTitle", "")
        published = snippet.get("publishedAt", "")
        views = int(stats.get("viewCount", 0))
        likes = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))
        # Calculate VPH
        from datetime import datetime, timezone
        pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        hours_live = max((datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600, 1)
        vph = views / hours_live
        performance_ratio = (vph / channel_avg_vph) if channel_avg_vph > 0 else None
        status = "🔥 VIRAL" if performance_ratio and performance_ratio > 3 else "✅ BOM" if performance_ratio and performance_ratio > 1 else "⚠️ ABAIXO" if performance_ratio else "📊 SEM REF"
        prompt = f"""Analyze this YouTube video performance in {language}:

TITLE: {title}
CHANNEL: {channel_name}
VIEWS: {views:,}
VPH: {vph:.1f} views/hour
HOURS LIVE: {hours_live:.0f}h
LIKES: {likes:,}
COMMENTS: {comments:,}
LIKE RATIO: {likes/max(views,1)*100:.2f}%
CHANNEL AVG VPH: {channel_avg_vph:.1f}
PERFORMANCE STATUS: {status}

Provide in {language}:

### 📊 PERFORMANCE ANALYSIS
[2 paragraphs on how this video is performing and why]

### 💡 WHY IS IT PERFORMING THIS WAY
[Root cause analysis — title? thumbnail? topic? timing?]

### 🚀 HOW TO REPLICATE SUCCESS (or fix underperformance)
1. [Action]
2. [Action]
3. [Action]

### 📝 IMPROVED TITLE SUGGESTION
Original: {title}
Improved: [better title that could increase CTR by 20%+]"""
        ai_analysis = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
        return jsonify({
            "video_id": video_id,
            "title": title,
            "channel": channel_name,
            "views": views,
            "likes": likes,
            "comments": comments,
            "vph": round(vph, 1),
            "hours_live": round(hours_live, 1),
            "like_ratio": round(likes / max(views, 1) * 100, 2),
            "performance_status": status,
            "performance_ratio": round(performance_ratio, 2) if performance_ratio else None,
            "ai_analysis": ai_analysis,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ti/trending_intelligence", methods=["POST"])
def ti_trending_intelligence():
    """Phase 7.4 — Trending videos + AI pattern analysis."""
    data = request.json or {}
    query = (data.get("query") or "").strip()[:200]
    region = (data.get("region") or "BR")[:2]
    language = data.get("language", "Portuguese")[:50]
    from core.api_keys import load_api_key as _lak
    api_key = (data.get("yt_api_key") or "").strip() or _lak("youtube") or ""
    if not api_key:
        return jsonify({"error": "YouTube API key required"}), 400
    try:
        import sys as _sys
        _pdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _pdir not in _sys.path:
            _sys.path.insert(0, _pdir)
        from core.youtube_api import search_trending
        videos = search_trending(query, api_key, max_results=15, order="viewCount", region=region) if query else []
        if not videos:
            from core.youtube_api import get_most_popular
            videos = get_most_popular(api_key, region=region, max_results=15)
    except Exception as e:
        return jsonify({"error": f"API error: {str(e)}"}), 500
    if not videos:
        return jsonify({"error": "No trending data found"}), 404
    video_list = ""
    for i, v in enumerate(videos[:12], 1):
        title = (v.get("title") or "")[:80]
        vph = v.get("vph", 0) or 0
        views = v.get("views", 0) or 0
        channel = (v.get("channel") or "")[:30]
        video_list += f"{i}. '{title}' | {channel} | {views:,} views | VPH:{vph:.0f}\n"
    prompt = f"""You are a YouTube trend analyst. Analyze these REAL trending videos and extract actionable patterns.

QUERY: {query or f'Most Popular in {region}'}
REGION: {region}
LANGUAGE: {language}

REAL TRENDING DATA (from YouTube API):
{video_list}

Generate a TREND INTELLIGENCE report in {language}:

### 🔥 TREND PATTERNS (what makes these videos viral)
1. Title pattern: [identify common title structures]
2. Topic pattern: [what topics/angles dominate]
3. Format pattern: [long, short, listicle, tutorial, story, etc.]
4. Emotional hook: [fear, curiosity, FOMO, inspiration, etc.]

### 📊 KEY METRICS ANALYSIS
- Average VPH of trending videos: [calculate]
- VPH threshold to be "trending": [estimate]
- Best performing channel strategy: [observation]

### 💡 3 VIDEO IDEAS YOU CAN MAKE NOW (based on this data)
1. [Idea with exact title suggestion]
2. [Idea with exact title suggestion]
3. [Idea with exact title suggestion]

### ⚡ HOW TO RIDE THIS TREND (next 48h window)
[Specific action plan to capitalize on what's trending NOW]"""
    ai_analysis = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "videos": videos[:12],
        "query": query,
        "region": region,
        "ai_analysis": ai_analysis,
        "total": len(videos),
    })


# ══ PHASE 7.5: MULTI-LANGUAGE + PERSONALIZATION ══════════════════════════════
_USER_PREFS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_prefs.json")

def _load_user_prefs():
    try:
        if os.path.isfile(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_user_prefs(prefs):
    try:
        with open(_USER_PREFS_FILE, 'w', encoding='utf-8') as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


@app.route("/api/ti/user_preferences", methods=["GET", "POST"])
def ti_user_preferences():
    """Phase 7.5 — Save/load user preferences for personalization."""
    if request.method == "GET":
        prefs = _load_user_prefs()
        return jsonify(prefs)
    # POST — merge new prefs into saved
    data = request.json or {}
    prefs = _load_user_prefs()
    allowed_keys = [
        'language', 'niche', 'channel_name', 'channel_id', 'channel_size',
        'tone', 'goals', 'region', 'default_engine', 'theme',
        'sidebar_collapsed', 'favorite_tools', 'auto_fill', 'show_tips',
    ]
    for k in allowed_keys:
        if k in data:
            val = data[k]
            if isinstance(val, str):
                import re as _re_xss
                val = _re_xss.sub(r'<[^>]+>', '', val).strip()[:200]
                prefs[k] = val
            elif isinstance(val, (bool, int, float)):
                prefs[k] = val
            elif isinstance(val, list):
                prefs[k] = [str(v)[:50] for v in val[:20]]
    prefs['updated_at'] = datetime.now().isoformat()
    ok = _save_user_prefs(prefs)
    return jsonify({'ok': ok, 'prefs': prefs})


@app.route("/api/ti/set_language", methods=["POST"])
def ti_set_language():
    """Phase 7.5 — Set UI language (saved to prefs)."""
    data = request.json or {}
    lang = (data.get("language") or "").strip()[:20]
    supported = {
        'pt': 'Portuguese', 'en': 'English', 'es': 'Spanish',
        'Portuguese': 'Portuguese', 'English': 'English', 'Spanish': 'Spanish',
    }
    lang_full = supported.get(lang, 'Portuguese')
    lang_code = {'Portuguese': 'pt', 'English': 'en', 'Spanish': 'es'}.get(lang_full, 'pt')
    prefs = _load_user_prefs()
    prefs['language'] = lang_full
    prefs['lang_code'] = lang_code
    _save_user_prefs(prefs)
    # Return UI translations for the selected language
    translations = _get_ui_translations(lang_code)
    return jsonify({
        'ok': True,
        'language': lang_full,
        'lang_code': lang_code,
        'translations': translations,
    })


def _get_ui_translations(lang_code='pt'):
    """Return UI label translations for TI interface."""
    t = {
        'pt': {
            'analyze': 'Analisar', 'generate': 'Gerar', 'search': 'Buscar',
            'results': 'Resultados', 'loading': 'Carregando...', 'error': 'Erro',
            'channel': 'Canal', 'niche': 'Nicho', 'language': 'Idioma',
            'title': 'Título', 'views': 'Visualizações', 'subscribers': 'Inscritos',
            'trending': 'Em Alta', 'viral': 'Viral', 'performance': 'Desempenho',
            'insights': 'Insights', 'strategy': 'Estratégia', 'analytics': 'Analytics',
            'save': 'Salvar', 'reset': 'Resetar', 'export': 'Exportar',
            'favorites': 'Favoritos', 'history': 'Histórico', 'settings': 'Configurações',
            'tools': 'Ferramentas', 'dashboard': 'Painel', 'report': 'Relatório',
            'copy': 'Copiar', 'download': 'Baixar', 'share': 'Compartilhar',
            'dark_psychology': 'Psicologia Sombria', 'finance': 'Finanças',
            'welcome': 'Bem-vindo ao Title Intelligence Pro',
            'auto_pilot_desc': 'Diagnóstico completo + plano de 7 dias',
            'deep_dive_desc': 'Análise profunda com dados reais do YouTube',
            'trend_desc': 'Tendências reais + padrões virais',
        },
        'en': {
            'analyze': 'Analyze', 'generate': 'Generate', 'search': 'Search',
            'results': 'Results', 'loading': 'Loading...', 'error': 'Error',
            'channel': 'Channel', 'niche': 'Niche', 'language': 'Language',
            'title': 'Title', 'views': 'Views', 'subscribers': 'Subscribers',
            'trending': 'Trending', 'viral': 'Viral', 'performance': 'Performance',
            'insights': 'Insights', 'strategy': 'Strategy', 'analytics': 'Analytics',
            'save': 'Save', 'reset': 'Reset', 'export': 'Export',
            'favorites': 'Favorites', 'history': 'History', 'settings': 'Settings',
            'tools': 'Tools', 'dashboard': 'Dashboard', 'report': 'Report',
            'copy': 'Copy', 'download': 'Download', 'share': 'Share',
            'dark_psychology': 'Dark Psychology', 'finance': 'Finance',
            'welcome': 'Welcome to Title Intelligence Pro',
            'auto_pilot_desc': 'Full diagnosis + 7-day plan',
            'deep_dive_desc': 'Deep analysis with real YouTube data',
            'trend_desc': 'Real trends + viral patterns',
        },
        'es': {
            'analyze': 'Analizar', 'generate': 'Generar', 'search': 'Buscar',
            'results': 'Resultados', 'loading': 'Cargando...', 'error': 'Error',
            'channel': 'Canal', 'niche': 'Nicho', 'language': 'Idioma',
            'title': 'Título', 'views': 'Vistas', 'subscribers': 'Suscriptores',
            'trending': 'Tendencia', 'viral': 'Viral', 'performance': 'Rendimiento',
            'insights': 'Insights', 'strategy': 'Estrategia', 'analytics': 'Analíticas',
            'save': 'Guardar', 'reset': 'Restablecer', 'export': 'Exportar',
            'favorites': 'Favoritos', 'history': 'Historial', 'settings': 'Configuración',
            'tools': 'Herramientas', 'dashboard': 'Panel', 'report': 'Informe',
            'copy': 'Copiar', 'download': 'Descargar', 'share': 'Compartir',
            'dark_psychology': 'Psicología Oscura', 'finance': 'Finanzas',
            'welcome': 'Bienvenido a Title Intelligence Pro',
            'auto_pilot_desc': 'Diagnóstico completo + plan de 7 días',
            'deep_dive_desc': 'Análisis profundo con datos reales de YouTube',
            'trend_desc': 'Tendencias reales + patrones virales',
        },
    }
    return t.get(lang_code, t['pt'])


@app.route("/api/ti/smart_defaults", methods=["GET"])
def ti_smart_defaults():
    """Phase 7.5 — Get smart defaults for auto-filling TI forms."""
    prefs = _load_user_prefs()
    lang = prefs.get('language', 'Portuguese')
    lang_code = prefs.get('lang_code', 'pt')
    niche = prefs.get('niche', '')
    channel = prefs.get('channel_name', '')
    channel_id = prefs.get('channel_id', '')
    region = prefs.get('region', 'BR')
    size = prefs.get('channel_size', 'small')
    tone = prefs.get('tone', '')
    goals = prefs.get('goals', '')
    favorites = prefs.get('favorite_tools', [])
    translations = _get_ui_translations(lang_code)
    # Also provide niche suggestions based on niche_database
    niche_suggestions = []
    try:
        from core.niche_database import NICHE_INTELLIGENCE
        niche_suggestions = list(NICHE_INTELLIGENCE.keys())[:20]
    except Exception:
        niche_suggestions = ['Psychology', 'Finance', 'Technology', 'Gaming', 'Education',
                             'Health', 'Cooking', 'Travel', 'Music', 'Sports']
    return jsonify({
        'language': lang,
        'lang_code': lang_code,
        'niche': niche,
        'channel_name': channel,
        'channel_id': channel_id,
        'channel_size': size,
        'region': region,
        'tone': tone,
        'goals': goals,
        'favorite_tools': favorites,
        'translations': translations,
        'niche_suggestions': niche_suggestions,
        'auto_fill': prefs.get('auto_fill', True),
        'show_tips': prefs.get('show_tips', True),
    })


@app.route("/api/ti/add_favorite", methods=["POST"])
def ti_add_favorite():
    """Phase 7.5 — Add/remove a tool from favorites."""
    data = request.json or {}
    tool = (data.get('tool') or '').strip()[:50]
    action = data.get('action', 'add')  # 'add' or 'remove'
    if not tool:
        return jsonify({'error': 'Tool name required'}), 400
    prefs = _load_user_prefs()
    favs = prefs.get('favorite_tools', [])
    if action == 'add' and tool not in favs:
        favs.append(tool)
    elif action == 'remove' and tool in favs:
        favs.remove(tool)
    prefs['favorite_tools'] = favs[:20]
    _save_user_prefs(prefs)
    return jsonify({'ok': True, 'favorites': prefs['favorite_tools']})


# ══ PHASE 7.6: EXPORT & REPORTS ══════════════════════════════════════════════
_REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
os.makedirs(_REPORTS_DIR, exist_ok=True)


@app.route("/api/ti/save_report", methods=["POST"])
def ti_save_report():
    """Phase 7.6 — Save an analysis result as a report."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    tool = (data.get("tool") or "").strip()[:50]
    content = (data.get("content") or "").strip()[:50000]
    metadata = data.get("metadata") or {}
    if not content:
        return jsonify({"error": "Report content required"}), 400
    report_id = f"rpt_{int(time.time())}_{hashlib.md5(content[:100].encode()).hexdigest()[:6]}"
    report = {
        "id": report_id,
        "title": title or f"Report — {tool or 'TI'}",
        "tool": tool,
        "content": content,
        "metadata": {k: str(v)[:200] for k, v in list(metadata.items())[:20]} if isinstance(metadata, dict) else {},
        "created_at": datetime.now().isoformat(),
        "format": data.get("format", "text"),
    }
    report_path = os.path.join(_REPORTS_DIR, f"{report_id}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "id": report_id, "report": report})


@app.route("/api/ti/list_reports", methods=["GET"])
def ti_list_reports():
    """Phase 7.6 — List all saved reports."""
    reports = []
    try:
        for fn in sorted(os.listdir(_REPORTS_DIR), reverse=True):
            if fn.endswith(".json"):
                fp = os.path.join(_REPORTS_DIR, fn)
                with open(fp, "r", encoding="utf-8") as f:
                    r = json.load(f)
                reports.append({
                    "id": r.get("id", fn.replace(".json", "")),
                    "title": r.get("title", ""),
                    "tool": r.get("tool", ""),
                    "created_at": r.get("created_at", ""),
                    "format": r.get("format", "text"),
                    "size": len(r.get("content", "")),
                })
    except Exception:
        pass
    return jsonify({"reports": reports, "total": len(reports)})


@app.route("/api/ti/get_report/<report_id>", methods=["GET"])
def ti_get_report(report_id):
    """Phase 7.6 — Get a saved report by ID."""
    import re as _re
    safe_id = _re.sub(r'[^a-zA-Z0-9_-]', '', report_id)[:80]
    fp = os.path.join(_REPORTS_DIR, f"{safe_id}.json")
    if not os.path.isfile(fp):
        return jsonify({"error": "Report not found"}), 404
    with open(fp, "r", encoding="utf-8") as f:
        report = json.load(f)
    return jsonify(report)


@app.route("/api/ti/delete_report", methods=["POST"])
def ti_delete_report():
    """Phase 7.6 — Delete a saved report."""
    data = request.json or {}
    import re as _re
    report_id = _re.sub(r'[^a-zA-Z0-9_-]', '', (data.get("id") or ""))[:80]
    if not report_id:
        return jsonify({"error": "Report ID required"}), 400
    fp = os.path.join(_REPORTS_DIR, f"{report_id}.json")
    if os.path.isfile(fp):
        os.remove(fp)
        return jsonify({"ok": True})
    return jsonify({"error": "Report not found"}), 404


@app.route("/api/ti/generate_combined_report", methods=["POST"])
def ti_generate_combined_report():
    """Phase 7.6 — Combine multiple saved reports into an AI-powered summary."""
    data = request.json or {}
    report_ids = data.get("report_ids") or []
    language = data.get("language", "Portuguese")[:50]
    if not report_ids or not isinstance(report_ids, list):
        return jsonify({"error": "Provide report_ids array"}), 400
    contents = []
    titles = []
    for rid in report_ids[:10]:
        import re as _re
        safe_id = _re.sub(r'[^a-zA-Z0-9_-]', '', str(rid))[:80]
        fp = os.path.join(_REPORTS_DIR, f"{safe_id}.json")
        if os.path.isfile(fp):
            with open(fp, "r", encoding="utf-8") as f:
                r = json.load(f)
            contents.append(f"### {r.get('title','Report')}\n{r.get('content','')[:5000]}")
            titles.append(r.get("title", ""))
    if not contents:
        return jsonify({"error": "No valid reports found"}), 404
    combined_text = "\n\n---\n\n".join(contents)
    prompt = f"""You are a senior content strategist creating an executive summary report.
Combine these {len(contents)} analysis reports into ONE cohesive executive report in {language}.

INDIVIDUAL REPORTS:
{combined_text[:15000]}

Generate an EXECUTIVE COMBINED REPORT in {language}:

### 📋 EXECUTIVE SUMMARY
[2-3 paragraph overview combining all insights]

### 🎯 KEY FINDINGS (top 5 across all reports)
1. [Most important finding]
2. [Second most important]
3. [Third]
4. [Fourth]
5. [Fifth]

### ⚡ UNIFIED ACTION PLAN (next 7 days)
Day 1-2: [actions based on combined insights]
Day 3-4: [actions]
Day 5-7: [actions]

### 📊 METRICS TO TRACK
- [Metric 1]
- [Metric 2]
- [Metric 3]

### 🏆 FINAL RECOMMENDATION
[One clear, actionable recommendation based on all data]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    combined_id = f"combined_{int(time.time())}"
    combined_report = {
        "id": combined_id,
        "title": f"Combined Report — {', '.join(titles[:3])}",
        "tool": "combined",
        "content": ai_result,
        "metadata": {"source_reports": report_ids, "source_count": len(contents)},
        "created_at": datetime.now().isoformat(),
        "format": "text",
    }
    fp = os.path.join(_REPORTS_DIR, f"{combined_id}.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(combined_report, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "id": combined_id, "report": combined_report})


@app.route("/api/ti/export_report/<report_id>", methods=["GET"])
def ti_export_report(report_id):
    """Phase 7.6 — Export a report as downloadable HTML file."""
    import re as _re
    safe_id = _re.sub(r'[^a-zA-Z0-9_-]', '', report_id)[:80]
    fp = os.path.join(_REPORTS_DIR, f"{safe_id}.json")
    if not os.path.isfile(fp):
        return jsonify({"error": "Report not found"}), 404
    with open(fp, "r", encoding="utf-8") as f:
        report = json.load(f)
    title = report.get("title", "Report")
    content = report.get("content", "").replace("\n", "<br>")
    created = report.get("created_at", "")
    tool = report.get("tool", "")
    html_out = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — StudioPilot Pro</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0a1a;color:#e0e0f0;padding:40px;max-width:900px;margin:0 auto;line-height:1.7}}
h1{{font-size:24px;background:linear-gradient(135deg,#06b6d4,#8b5cf6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px}}
.meta{{color:#6b6b8d;font-size:12px;margin-bottom:24px;border-bottom:1px solid #1e1e2d;padding-bottom:12px}}
.content{{font-size:14px;white-space:pre-wrap}}
h3{{color:#06b6d4;margin:20px 0 8px}}
.footer{{margin-top:40px;padding-top:16px;border-top:1px solid #1e1e2d;color:#6b6b8d;font-size:11px;text-align:center}}
@media print{{body{{background:#fff;color:#333}}h1{{color:#06b6d4;-webkit-text-fill-color:#06b6d4}}h3{{color:#06b6d4}}.meta,.footer{{color:#999}}}}
</style></head>
<body>
<h1>{title}</h1>
<div class="meta">Tool: {tool} | Generated: {created} | StudioPilot Pro — Title Intelligence</div>
<div class="content">{content}</div>
<div class="footer">Generated by StudioPilot Pro — Title Intelligence &copy; 2026</div>
</body></html>"""
    return Response(html_out, mimetype='text/html', headers={
        'Content-Disposition': f'attachment; filename="{safe_id}.html"'
    })


# ══ PHASE 7.7: AI CHAT ASSISTANT ═════════════════════════════════════════════
_CHAT_SESSIONS = {}  # session_id -> [{"role":"user/ai","msg":"...","ts":"..."}]
_CHAT_MAX_HISTORY = 20


@app.route("/api/ti/chat_send", methods=["POST"])
def ti_chat_send():
    """Phase 7.7 — Send a message to the AI chat assistant."""
    data = request.json or {}
    message = (data.get("message") or "").strip()[:2000]
    session_id = (data.get("session_id") or "default").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    context = data.get("context") or {}
    if not message:
        return jsonify({"error": "Message required"}), 400
    # Get/create session
    if session_id not in _CHAT_SESSIONS:
        _CHAT_SESSIONS[session_id] = []
    history = _CHAT_SESSIONS[session_id]
    # Add user message
    history.append({
        "role": "user",
        "msg": message,
        "ts": datetime.now().isoformat(),
    })
    # Build conversation context
    conv_history = ""
    for h in history[-10:]:
        role = "USER" if h["role"] == "user" else "ASSISTANT"
        conv_history += f"{role}: {h['msg']}\n\n"
    # Load user prefs for context
    prefs = _load_user_prefs()
    niche = context.get("niche") or prefs.get("niche", "")
    channel = context.get("channel") or prefs.get("channel_name", "")
    channel_size = context.get("channel_size") or prefs.get("channel_size", "")
    prompt = f"""You are StudioPilot AI — an expert YouTube strategist and content advisor. 
You are having a real-time conversation with a YouTube creator. Be direct, actionable, and data-driven.

CREATOR PROFILE:
- Channel: {channel or 'Not specified'}
- Niche: {niche or 'Not specified'}
- Size: {channel_size or 'Not specified'}
- Language: {language}

CONVERSATION HISTORY:
{conv_history}

INSTRUCTIONS:
- Respond ONLY as the ASSISTANT in {language}
- Be concise but thorough (max 300 words)
- Always provide specific, actionable advice
- Reference data/numbers when possible
- If asked about titles, provide 3-5 specific title suggestions
- If asked about strategy, give step-by-step plans
- If asked about trends, reference real YouTube patterns
- Format with emojis and bullet points for readability
- Never break character — you ARE the YouTube strategist

ASSISTANT:"""
    ai_response = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    if not ai_response:
        ai_response = "Desculpe, não consegui gerar uma resposta. Tente novamente."
    # Add AI response to history
    history.append({
        "role": "ai",
        "msg": ai_response,
        "ts": datetime.now().isoformat(),
    })
    # Trim history
    if len(history) > _CHAT_MAX_HISTORY * 2:
        _CHAT_SESSIONS[session_id] = history[-_CHAT_MAX_HISTORY * 2:]
    return jsonify({
        "response": ai_response,
        "session_id": session_id,
        "message_count": len(history),
    })


@app.route("/api/ti/chat_history", methods=["GET"])
def ti_chat_history():
    """Phase 7.7 — Get chat history for a session."""
    session_id = request.args.get("session_id", "default").strip()[:50]
    history = _CHAT_SESSIONS.get(session_id, [])
    return jsonify({
        "session_id": session_id,
        "messages": history,
        "count": len(history),
    })


@app.route("/api/ti/chat_clear", methods=["POST"])
def ti_chat_clear():
    """Phase 7.7 — Clear chat history."""
    data = request.json or {}
    session_id = (data.get("session_id") or "default").strip()[:50]
    _CHAT_SESSIONS.pop(session_id, None)
    return jsonify({"ok": True, "session_id": session_id})


# ══ PHASE 7.8: BATCH OPERATIONS & TEMPLATE LIBRARY ═══════════════════════════

_TITLE_TEMPLATES = {
    "curiosity": {
        "name": "Curiosity Gap", "emoji": "🧠",
        "patterns": [
            "{Número} {Tópico} Que {Autoridade} Não Querem Que Você Saiba",
            "O Que Acontece Quando Você {Ação}... (ninguém fala sobre isso)",
            "Por Que {Tópico} Está {Mudança} e Ninguém Percebeu",
            "{Tópico}: A Verdade Que {Grupo} Esconde de Você",
            "Eu Descobri {Segredo} Sobre {Tópico} (e fiquei chocado)",
        ],
    },
    "how_to": {
        "name": "How-To / Tutorial", "emoji": "📋",
        "patterns": [
            "Como {Resultado} em {Tempo} (passo a passo)",
            "{Número} Passos Para {Resultado} Sem {Obstáculo}",
            "Como Eu {Resultado} em Apenas {Tempo} (método completo)",
            "O Guia Definitivo Para {Resultado} em {Ano}",
            "{Resultado}: O Método de {Tempo} Que Funciona Para Qualquer {Pessoa}",
        ],
    },
    "listicle": {
        "name": "Listicle / Numbers", "emoji": "🔢",
        "patterns": [
            "{Número} {Tópico} Que Vão Mudar Sua {Área} Para Sempre",
            "Top {Número} {Tópico} de {Ano} (ranking definitivo)",
            "{Número} {Erros} Que {Percentual}% Das Pessoas Cometem em {Área}",
            "{Número} {Tópico} Que Eu Gostaria de Saber Antes de {Ação}",
            "Os {Número} Melhores {Tópico} Para {Resultado} em {Ano}",
        ],
    },
    "shock": {
        "name": "Shock / Polêmica", "emoji": "⚡",
        "patterns": [
            "{Tópico} Está ACABANDO — e a culpa é {Causa}",
            "PARE de {Ação} AGORA (antes que seja tarde demais)",
            "A Mentira Sobre {Tópico} Que {Grupo} Conta Todo Dia",
            "{Autoridade} ADMITIU: {Revelação} Sobre {Tópico}",
            "O Fim de {Tópico}? O Que Está Acontecendo é {Adjetivo}",
        ],
    },
    "story": {
        "name": "Story / Personal", "emoji": "📖",
        "patterns": [
            "Eu {Ação} Por {Tempo} e Isso Aconteceu...",
            "De {Estado Inicial} Para {Estado Final}: Minha Jornada Com {Tópico}",
            "O Dia Em Que {Evento} Mudou Minha Vida Para Sempre",
            "Como {Tópico} Me Fez {Resultado} (história real)",
            "{Pessoa Famosa} Me Ensinou {Lição} Sobre {Tópico}",
        ],
    },
    "comparison": {
        "name": "Comparison / VS", "emoji": "⚔️",
        "patterns": [
            "{Opção A} vs {Opção B}: Qual é Melhor em {Ano}?",
            "{Opção A} ou {Opção B}? A Resposta Vai Te Surpreender",
            "Testei {Opção A} e {Opção B} Por {Tempo} — O Resultado",
            "{Número} Diferenças Entre {Opção A} e {Opção B} Que Ninguém Fala",
            "Por Que Troquei {Opção A} Por {Opção B} (e nunca mais voltei)",
        ],
    },
}


@app.route("/api/ti/batch_analyze", methods=["POST"])
def ti_batch_analyze():
    """Phase 7.8 — Analyze multiple titles at once with AI scoring."""
    data = request.json or {}
    titles = data.get("titles") or []
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not titles or not isinstance(titles, list):
        return jsonify({"error": "Provide 'titles' array"}), 400
    titles = [str(t).strip()[:200] for t in titles[:20] if str(t).strip()]
    if not titles:
        return jsonify({"error": "No valid titles provided"}), 400
    titles_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    prompt = f"""You are a YouTube title analyst. Score each title for viral potential.

NICHE: {niche or 'General'}
LANGUAGE: {language}

TITLES TO ANALYZE:
{titles_text}

For EACH title, provide in {language}:
TITLE #[number]: "[original title]"
- VIRAL SCORE: [0-100]/100
- CTR POTENTIAL: [Alto/Médio/Baixo]
- EMOTIONAL HOOK: [which emotion it triggers]
- STRENGTH: [what works well — 1 line]
- WEAKNESS: [what could improve — 1 line]
- IMPROVED VERSION: [rewritten title with higher viral potential]

After all titles, provide:
### 🏆 RANKING (best to worst)
[Numbered list with scores]

### 💡 PATTERN INSIGHTS
[What patterns the best titles share]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "titles_analyzed": len(titles),
        "titles": titles,
        "analysis": ai_result,
        "niche": niche,
    })


@app.route("/api/ti/title_templates", methods=["GET"])
def ti_title_templates():
    """Phase 7.8 — Get curated title templates by category."""
    category = request.args.get("category", "").strip()[:30]
    if category and category in _TITLE_TEMPLATES:
        return jsonify({"template": _TITLE_TEMPLATES[category], "category": category})
    return jsonify({
        "templates": {k: {"name": v["name"], "emoji": v["emoji"], "count": len(v["patterns"])} for k, v in _TITLE_TEMPLATES.items()},
        "total_patterns": sum(len(v["patterns"]) for v in _TITLE_TEMPLATES.values()),
        "categories": list(_TITLE_TEMPLATES.keys()),
    })


@app.route("/api/ti/bulk_generate", methods=["POST"])
def ti_bulk_generate():
    """Phase 7.8 — Generate titles in bulk from template + niche."""
    data = request.json or {}
    template_category = (data.get("category") or "").strip()[:30]
    niche = (data.get("niche") or "").strip()[:100]
    count = min(int(data.get("count") or 5), 15)
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    # Get template patterns
    patterns = []
    if template_category and template_category in _TITLE_TEMPLATES:
        patterns = _TITLE_TEMPLATES[template_category]["patterns"]
    else:
        for v in _TITLE_TEMPLATES.values():
            patterns.extend(v["patterns"][:2])
    patterns_text = "\n".join(f"- {p}" for p in patterns[:10])
    prompt = f"""You are a viral YouTube title generator. Generate {count} titles using these patterns.

NICHE: {niche}
LANGUAGE: {language}
TEMPLATE PATTERNS:
{patterns_text}

Generate EXACTLY {count} unique, ready-to-use titles in {language} for the niche "{niche}".
Fill in the placeholders with niche-specific content. Make them viral, emotional, click-worthy.

Format each as:
[number]. [TITLE] | Score: [0-100] | Hook: [emotion]

After the titles:
### 🎯 BEST 3 FOR IMMEDIATE USE
[Which 3 to publish first and why]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "titles_generated": count,
        "niche": niche,
        "category": template_category or "mixed",
        "result": ai_result,
    })


# ══ PHASE 7.9: A/B TITLE TESTING & HEADLINE OPTIMIZER ════════════════════════

@app.route("/api/ti/ab_test", methods=["POST"])
def ti_ab_test():
    """Phase 7.9 — A/B compare 2+ titles with detailed scoring."""
    data = request.json or {}
    titles = data.get("titles") or []
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    audience = (data.get("audience") or "").strip()[:100]
    if not isinstance(titles, list) or len(titles) < 2:
        return jsonify({"error": "Provide at least 2 titles"}), 400
    titles = [str(t).strip()[:200] for t in titles[:10] if str(t).strip()]
    if len(titles) < 2:
        return jsonify({"error": "At least 2 valid titles required"}), 400
    titles_text = "\n".join(f"  {chr(65+i)}. \"{t}\"" for i, t in enumerate(titles))
    prompt = f"""You are a YouTube A/B testing expert. Compare these titles and determine the winner.

NICHE: {niche or 'General'}
TARGET AUDIENCE: {audience or 'General YouTube viewers'}
LANGUAGE: {language}

TITLES TO COMPARE:
{titles_text}

Provide a DETAILED A/B TEST REPORT in {language}:

### 🆚 HEAD-TO-HEAD COMPARISON
For each title, score these dimensions (0-100):
| Title | CTR | Curiosity | Emotion | SEO | Clarity | TOTAL |
[Fill table]

### 🏆 WINNER: [Letter]. "[Title]"
**Win margin**: [X points]
**Why it wins**: [2-3 clear reasons]

### 📊 DIMENSION BREAKDOWN
For each title provide:
**[Letter]. "[Title]"**
- ✅ Strengths: [what works]
- ❌ Weaknesses: [what doesn't]  
- 🎯 CTR Prediction: [High/Medium/Low]
- 💡 Improvement: [specific rewrite suggestion]

### 🔬 PSYCHOLOGICAL ANALYSIS
- Which cognitive biases each title triggers
- Emotional hooks used
- Click-trigger patterns

### ⚡ FINAL VERDICT
[Clear recommendation with confidence level: High/Medium/Low]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "titles_compared": len(titles),
        "titles": titles,
        "report": ai_result,
        "niche": niche,
    })


@app.route("/api/ti/headline_optimize", methods=["POST"])
def ti_headline_optimize():
    """Phase 7.9 — Iteratively optimize a headline with AI."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    style = (data.get("style") or "").strip()[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a viral headline optimization expert. Take this title and create 5 progressively better versions.

ORIGINAL TITLE: "{title}"
NICHE: {niche or 'General'}
STYLE PREFERENCE: {style or 'Maximize CTR and virality'}
LANGUAGE: {language}

Provide in {language}:

### 📝 ORIGINAL ANALYSIS
**Title**: "{title}"
- Viral Score: [0-100]/100
- CTR Potential: [percentage estimate]
- Main Issue: [biggest weakness]

### 🔄 OPTIMIZATION ITERATIONS

**v1 — Quick Fix** (minor tweak)
"[improved title]"
Score: [0-100] | Change: [what changed and why]

**v2 — Emotional Boost** (add emotional triggers)
"[improved title]"
Score: [0-100] | Change: [what changed and why]

**v3 — Curiosity Gap** (add mystery/curiosity)
"[improved title]"
Score: [0-100] | Change: [what changed and why]

**v4 — Power Words** (add power words + urgency)
"[improved title]"
Score: [0-100] | Change: [what changed and why]

**v5 — MAXIMUM VIRAL** (all techniques combined)
"[improved title]"
Score: [0-100] | Change: [what changed and why]

### 🏆 RECOMMENDED: v[number]
[Why this version is the best balance of virality and authenticity]

### 📐 OPTIMIZATION FORMULA USED
[List the specific techniques applied]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "original": title,
        "optimized": ai_result,
        "niche": niche,
    })


@app.route("/api/ti/title_variations", methods=["POST"])
def ti_title_variations():
    """Phase 7.9 — Generate style variations of a single title."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    count = min(int(data.get("count") or 8), 15)
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""Generate {count} creative variations of this YouTube title. Each variation should use a DIFFERENT psychological approach.

ORIGINAL: "{title}"
NICHE: {niche or 'General'}
LANGUAGE: {language}

Generate {count} variations in {language}, each with a DIFFERENT style:
1. 🧠 Curiosity Gap — [variation]
2. 😱 Shock/Fear — [variation]
3. 📋 How-To — [variation]
4. 🔢 Listicle — [variation]
5. 📖 Story/Personal — [variation]
6. ⚔️ VS/Comparison — [variation]
7. ⏰ Urgency — [variation]
8. 🎯 Direct Benefit — [variation]

For each: Score [0-100] | Best for: [audience type]

### 🏆 TOP 3 FOR IMMEDIATE USE
[Which 3 to test first and why]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "original": title,
        "variations": ai_result,
        "count": count,
        "niche": niche,
    })


# ══ PHASE 7.10: CONTENT CALENDAR & SCHEDULING ════════════════════════════════
_CALENDARS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calendars")
os.makedirs(_CALENDARS_DIR, exist_ok=True)


@app.route("/api/ti/generate_calendar", methods=["POST"])
def ti_generate_calendar():
    """Phase 7.10 — Generate an AI-powered content calendar."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    channel = (data.get("channel") or "").strip()[:100]
    days = min(int(data.get("days") or 7), 30)
    frequency = (data.get("frequency") or "3x/week").strip()[:30]
    language = data.get("language", "Portuguese")[:50]
    goals = (data.get("goals") or "").strip()[:200]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube content strategist. Create a detailed {days}-day content calendar.

CHANNEL: {channel or 'Not specified'}
NICHE: {niche}
POSTING FREQUENCY: {frequency}
GOALS: {goals or 'Grow channel and engagement'}
LANGUAGE: {language}

Create a DETAILED {days}-DAY CONTENT CALENDAR in {language}:

### 📅 CONTENT CALENDAR — {days} DAYS
For each publishing day, provide:

**📅 Day [N] — [Weekday]**
- 🎬 Title: "[viral-optimized title]"
- 📝 Topic: [brief description]
- 🏷️ Format: [Long-form/Shorts/Live/Series]
- ⏰ Best Upload Time: [HH:MM]
- 🎯 Target: [what metric to optimize]
- 🔑 Keywords: [3-5 SEO keywords]
- 🎨 Thumbnail Concept: [brief visual description]

### 📊 CALENDAR OVERVIEW
- Total videos: [count]
- Long-form: [count] | Shorts: [count]
- Content mix: [percentages by type]

### 🎯 STRATEGIC NOTES
- Why this sequence works
- How videos connect to each other
- Expected growth impact

### ⚡ QUICK START
- Which video to film FIRST and why
- Minimum equipment needed"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    # Auto-save
    cal_id = f"cal_{int(time.time())}"
    cal = {
        "id": cal_id,
        "niche": niche,
        "channel": channel,
        "days": days,
        "frequency": frequency,
        "goals": goals,
        "calendar": ai_result,
        "created_at": datetime.now().isoformat(),
    }
    fp = os.path.join(_CALENDARS_DIR, f"{cal_id}.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "id": cal_id, "calendar": cal})


@app.route("/api/ti/list_calendars", methods=["GET"])
def ti_list_calendars():
    """Phase 7.10 — List saved content calendars."""
    cals = []
    try:
        for fn in sorted(os.listdir(_CALENDARS_DIR), reverse=True):
            if fn.endswith(".json"):
                fp = os.path.join(_CALENDARS_DIR, fn)
                with open(fp, "r", encoding="utf-8") as f:
                    c = json.load(f)
                cals.append({
                    "id": c.get("id", fn.replace(".json", "")),
                    "niche": c.get("niche", ""),
                    "days": c.get("days", 0),
                    "frequency": c.get("frequency", ""),
                    "created_at": c.get("created_at", ""),
                })
    except Exception:
        pass
    return jsonify({"calendars": cals, "total": len(cals)})


@app.route("/api/ti/get_calendar/<cal_id>", methods=["GET"])
def ti_get_calendar(cal_id):
    """Phase 7.10 — Get a saved calendar by ID."""
    import re as _re
    safe_id = _re.sub(r'[^a-zA-Z0-9_-]', '', cal_id)[:80]
    fp = os.path.join(_CALENDARS_DIR, f"{safe_id}.json")
    if not os.path.isfile(fp):
        return jsonify({"error": "Calendar not found"}), 404
    with open(fp, "r", encoding="utf-8") as f:
        cal = json.load(f)
    return jsonify(cal)


@app.route("/api/ti/delete_calendar", methods=["POST"])
def ti_delete_calendar():
    """Phase 7.10 — Delete a saved calendar."""
    data = request.json or {}
    import re as _re
    cal_id = _re.sub(r'[^a-zA-Z0-9_-]', '', (data.get("id") or ""))[:80]
    if not cal_id:
        return jsonify({"error": "Calendar ID required"}), 400
    fp = os.path.join(_CALENDARS_DIR, f"{cal_id}.json")
    if os.path.isfile(fp):
        os.remove(fp)
        return jsonify({"ok": True})
    return jsonify({"error": "Calendar not found"}), 404


# ══ PHASE 7.11: COMPETITOR INTELLIGENCE ══════════════════════════════════════

@app.route("/api/ti/competitor_analyze", methods=["POST"])
def ti_competitor_analyze():
    """Phase 7.11 — Deep AI analysis of a competitor channel."""
    data = request.json or {}
    competitor = (data.get("competitor") or "").strip()[:100]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not competitor:
        return jsonify({"error": "Competitor channel name required"}), 400
    prompt = f"""You are a YouTube competitive intelligence expert. Analyze this competitor channel in detail.

COMPETITOR CHANNEL: {competitor}
NICHE: {niche or 'General'}
LANGUAGE: {language}

Provide a COMPREHENSIVE COMPETITOR ANALYSIS in {language}:

### 🕵️ COMPETITOR PROFILE
- Channel: {competitor}
- Estimated subscribers range
- Estimated monthly views
- Content frequency
- Average video length
- Main content format

### 📊 CONTENT STRATEGY ANALYSIS
- Top 5 performing video types
- Title patterns that work for them
- Thumbnail style analysis
- Upload schedule patterns
- SEO strategy assessment

### 💪 STRENGTHS
[5 key strengths with specific examples]

### 🎯 WEAKNESSES & VULNERABILITIES
[5 exploitable weaknesses]

### 📈 GROWTH TRAJECTORY
- Growth speed assessment
- Viral content ratio
- Audience engagement level
- Community building effectiveness

### 🔑 KEY TAKEAWAYS
- What to COPY from this competitor
- What to do DIFFERENTLY
- Specific content gaps they leave open

### ⚔️ HOW TO BEAT THEM
[5 specific, actionable strategies to outperform this channel]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "competitor": competitor,
        "niche": niche,
        "analysis": ai_result,
    })


@app.route("/api/ti/competitor_compare", methods=["POST"])
def ti_competitor_compare():
    """Phase 7.11 — Compare your channel vs a competitor."""
    data = request.json or {}
    your_channel = (data.get("your_channel") or "").strip()[:100]
    competitor = (data.get("competitor") or "").strip()[:100]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not your_channel or not competitor:
        return jsonify({"error": "Both channels required"}), 400
    prompt = f"""You are a YouTube competitive analysis expert. Compare these two channels head-to-head.

YOUR CHANNEL: {your_channel}
COMPETITOR: {competitor}
NICHE: {niche or 'General'}
LANGUAGE: {language}

Provide a DETAILED HEAD-TO-HEAD COMPARISON in {language}:

### 🆚 {your_channel} vs {competitor}

| Dimension | {your_channel} | {competitor} | Winner |
|-----------|----------|------------|--------|
| Content Quality | [score] | [score] | [who] |
| Title Strategy | [score] | [score] | [who] |
| Upload Frequency | [score] | [score] | [who] |
| SEO Optimization | [score] | [score] | [who] |
| Engagement Rate | [score] | [score] | [who] |
| Thumbnail Quality | [score] | [score] | [who] |
| Niche Authority | [score] | [score] | [who] |
| Growth Potential | [score] | [score] | [who] |

### 🏆 OVERALL WINNER: [channel name]
**Your advantage areas**: [list]
**Their advantage areas**: [list]

### 📋 ACTION PLAN
[10 specific actions to close the gap or extend your lead]

### 🎯 90-DAY BATTLE PLAN
[Month-by-month strategy to outperform the competitor]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "your_channel": your_channel,
        "competitor": competitor,
        "comparison": ai_result,
    })


@app.route("/api/ti/content_gap_finder", methods=["POST"])
def ti_content_gap_finder():
    """Phase 7.11 — Find untapped content opportunities vs competitors."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    competitors = data.get("competitors") or []
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    competitors = [str(c).strip()[:100] for c in competitors[:5] if str(c).strip()]
    comp_text = ", ".join(competitors) if competitors else "general competitors in the niche"
    prompt = f"""You are a content gap analysis expert. Find untapped opportunities in this niche.

NICHE: {niche}
COMPETITORS ANALYZED: {comp_text}
LANGUAGE: {language}

Provide a DETAILED CONTENT GAP ANALYSIS in {language}:

### 🔍 CONTENT GAP ANALYSIS — {niche}

### 📊 MARKET OVERVIEW
- Current content saturation level
- Top content types in this niche
- Audience demand vs supply assessment

### 🎯 TOP 10 CONTENT GAPS
For each gap:
**[Number]. [Gap Topic]**
- 🔥 Demand Level: [High/Medium/Low]
- 📉 Competition Level: [High/Medium/Low]  
- 💰 Monetization Potential: [High/Medium/Low]
- 🎬 Suggested Title: "[viral title for this gap]"
- 📝 Why it works: [brief explanation]

### ⚡ QUICK WIN OPPORTUNITIES
[5 topics you could create videos about THIS WEEK with low competition]

### 🚀 BLUE OCEAN CONTENT
[3 completely unique content angles nobody is doing yet]

### 📈 TREND-BASED GAPS
[5 emerging trends with no content yet]

### 🏆 PRIORITY MATRIX
| Topic | Demand | Competition | Effort | Priority |
[Fill with top 10 sorted by priority]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "niche": niche,
        "competitors": competitors,
        "gaps": ai_result,
    })


# ══ PHASE 7.12: MONETIZATION & REVENUE STRATEGY ═════════════════════════════

@app.route("/api/ti/revenue_projection", methods=["POST"])
def ti_revenue_projection():
    """Phase 7.12 — Calculate revenue projections based on views/CPM."""
    data = request.json or {}
    try:
        monthly_views = max(0, int(data.get("monthly_views") or 0))
        cpm = max(0.1, min(float(data.get("cpm") or 3.0), 100.0))
        subscribers = max(0, int(data.get("subscribers") or 0))
        videos_per_month = max(1, min(int(data.get("videos_per_month") or 4), 60))
        growth_rate = max(0, min(float(data.get("growth_rate") or 10), 200))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid numeric values"}), 400
    if monthly_views <= 0:
        return jsonify({"error": "Monthly views required"}), 400
    # Calculate projections
    monthly_revenue = (monthly_views / 1000) * cpm
    yearly_revenue = monthly_revenue * 12
    views_per_video = monthly_views / videos_per_month if videos_per_month else 0
    rpm = cpm * 0.55  # YouTube takes ~45%
    projections = []
    curr_views = monthly_views
    curr_subs = subscribers
    for month in range(1, 13):
        curr_views = int(curr_views * (1 + growth_rate / 100))
        curr_subs = int(curr_subs * (1 + growth_rate / 100 * 0.7))
        rev = (curr_views / 1000) * cpm
        projections.append({
            "month": month,
            "views": curr_views,
            "subscribers": curr_subs,
            "revenue": round(rev, 2),
            "cumulative": round(sum(p["revenue"] for p in projections) + rev, 2),
        })
    return jsonify({
        "current": {
            "monthly_views": monthly_views,
            "cpm": cpm,
            "rpm": round(rpm, 2),
            "monthly_revenue": round(monthly_revenue, 2),
            "yearly_revenue": round(yearly_revenue, 2),
            "views_per_video": round(views_per_video),
            "videos_per_month": videos_per_month,
        },
        "projections": projections,
        "total_12_months": round(projections[-1]["cumulative"], 2) if projections else 0,
    })


@app.route("/api/ti/monetization_strategy", methods=["POST"])
def ti_monetization_strategy():
    """Phase 7.12 — AI-powered full monetization strategy."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    channel = (data.get("channel") or "").strip()[:100]
    subscribers = (data.get("subscribers") or "").strip()[:20]
    monthly_views = (data.get("monthly_views") or "").strip()[:20]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube monetization expert. Create a comprehensive monetization strategy.

CHANNEL: {channel or 'Not specified'}
NICHE: {niche}
SUBSCRIBERS: {subscribers or 'Not specified'}
MONTHLY VIEWS: {monthly_views or 'Not specified'}
LANGUAGE: {language}

Provide a DETAILED MONETIZATION STRATEGY in {language}:

### 💰 REVENUE STREAMS ANALYSIS

**1. AdSense Revenue**
- Expected CPM range for {niche}: $[min]-$[max]
- Revenue optimization tips
- Best ad placement strategies

**2. Sponsorships & Brand Deals**
- Ideal sponsor types for {niche}
- Pricing guide based on audience size
- How to pitch to brands
- Expected revenue per deal

**3. Affiliate Marketing**
- Top 5 affiliate programs for {niche}
- Commission rates
- Product recommendation strategies

**4. Digital Products**
- Course ideas for {niche}
- E-book/guide opportunities
- Membership/community potential
- Expected pricing and revenue

**5. Merchandise**
- Product ideas that fit {niche}
- Platforms to use
- Expected margins

### 📊 REVENUE PROJECTION
| Revenue Stream | Month 1 | Month 6 | Month 12 |
[Fill with realistic projections]

### 🎯 90-DAY MONETIZATION PLAN
[Week-by-week action plan to implement all streams]

### ⚡ QUICK WINS
[5 things to implement THIS WEEK to start earning]

### 🏆 LONG-TERM STRATEGY
[12-month vision for sustainable income]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "niche": niche,
        "channel": channel,
        "strategy": ai_result,
    })


@app.route("/api/ti/sponsorship_pricing", methods=["POST"])
def ti_sponsorship_pricing():
    """Phase 7.12 — AI-powered sponsorship pricing calculator."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = (data.get("subscribers") or "").strip()[:20]
    avg_views = (data.get("avg_views") or "").strip()[:20]
    engagement = (data.get("engagement") or "").strip()[:20]
    language = data.get("language", "Portuguese")[:50]
    if not subscribers and not avg_views:
        return jsonify({"error": "Subscribers or average views required"}), 400
    prompt = f"""You are a YouTube influencer marketing pricing expert. Calculate sponsorship rates.

NICHE: {niche or 'General'}
SUBSCRIBERS: {subscribers or 'Not specified'}
AVERAGE VIEWS: {avg_views or 'Not specified'}
ENGAGEMENT RATE: {engagement or 'Not specified'}
LANGUAGE: {language}

Provide DETAILED SPONSORSHIP PRICING in {language}:

### 💰 SPONSORSHIP PRICING GUIDE

**📋 Integration Types & Pricing**
| Type | Duration | Suggested Price | Market Range |
|------|----------|----------------|-------------|
| Mention (15-30s) | Quick mention | $[price] | $[range] |
| Dedicated Segment (60-90s) | Mid-video integration | $[price] | $[range] |
| Full Dedicated Video | Entire video about product | $[price] | $[range] |
| YouTube Shorts | Short-form | $[price] | $[range] |
| Package (3 videos) | Multi-video deal | $[price] | $[range] |

### 📊 PRICING FORMULA
[How the prices were calculated]
- CPV (Cost per View): $[value]
- CPE (Cost per Engagement): $[value]

### 🎯 NEGOTIATION TIPS
[5 tips for negotiating with brands]

### 📧 MEDIA KIT ESSENTIALS
[What to include in your media kit]

### ⚠️ PRICING DO'S AND DON'TS
[Common mistakes to avoid]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "niche": niche,
        "subscribers": subscribers,
        "pricing": ai_result,
    })


# ══ PHASE 7.13: SCRIPT WRITER & VIDEO PLANNER ═══════════════════════════════

@app.route("/api/ti/script_full", methods=["POST"])
def ti_script_full():
    """Phase 7.13 — Generate a complete video script with AI."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    duration = (data.get("duration") or "10").strip()[:10]
    tone = (data.get("tone") or "engaging").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Video title required"}), 400
    prompt = f"""You are a professional YouTube scriptwriter. Write a complete video script.

TITLE: "{title}"
NICHE: {niche or 'General'}
TARGET DURATION: {duration} minutes
TONE: {tone}
LANGUAGE: {language}

Write a COMPLETE, PRODUCTION-READY SCRIPT in {language}:

### 🎬 SCRIPT: "{title}"
**Duration**: ~{duration} min | **Tone**: {tone}

---

### 🎣 HOOK (0:00 - 0:30)
[Write the exact opening words — must grab attention in 5 seconds]

### 📢 INTRO (0:30 - 1:00)
[Channel branding, what viewer will learn, why they should stay]

### 📝 BODY

**Segment 1: [Topic] (1:00 - 3:00)**
[Full script with dialogue, b-roll suggestions, and transitions]

**Segment 2: [Topic] (3:00 - 5:00)**
[Continue with detailed script]

**Segment 3: [Topic] (5:00 - 7:00)**
[Continue with detailed script]

**Segment 4: [Topic] (7:00 - 9:00)**
[Continue with detailed script]

### 🎯 CTA & OUTRO (last minute)
[Call-to-action: subscribe, comment, like, next video teaser]

---

### 📋 PRODUCTION NOTES
- B-roll suggestions for each segment
- Music/sound effects recommendations
- On-screen text/graphics suggestions
- Thumbnail concept based on this script

### 📊 SCRIPT STATS
- Estimated word count: [count]
- Reading pace: [words per minute]
- Hook strength: [score/10]
- Retention prediction: [percentage]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "title": title,
        "duration": duration,
        "tone": tone,
        "script": ai_result,
    })


@app.route("/api/ti/hook_pro", methods=["POST"])
def ti_hook_pro():
    """Phase 7.13 — Generate 10 powerful opening hooks."""
    data = request.json or {}
    topic = (data.get("topic") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not topic:
        return jsonify({"error": "Topic required"}), 400
    prompt = f"""You are a YouTube hook specialist. Create 10 irresistible opening hooks.

TOPIC: "{topic}"
NICHE: {niche or 'General'}
LANGUAGE: {language}

Generate 10 POWERFUL OPENING HOOKS in {language}:

Each hook should be the EXACT words the creator says in the first 5-15 seconds.

### 🎣 10 HOOKS FOR: "{topic}"

**1. 🧠 Curiosity Hook**
"[exact words]"
Why it works: [explanation] | Retention boost: [%]

**2. 😱 Shock Hook**
"[exact words]"
Why it works: [explanation] | Retention boost: [%]

**3. 📊 Statistic Hook**
"[exact words]"
Why it works: [explanation] | Retention boost: [%]

**4. ❓ Question Hook**
"[exact words]"
Why it works: [explanation] | Retention boost: [%]

**5. 📖 Story Hook**
"[exact words]"
Why it works: [explanation] | Retention boost: [%]

**6. ⚠️ Warning Hook**
"[exact words]"
Why it works: [explanation] | Retention boost: [%]

**7. 🎯 Promise Hook**
"[exact words]"
Why it works: [explanation] | Retention boost: [%]

**8. 🔄 Contrarian Hook**
"[exact words]"
Why it works: [explanation] | Retention boost: [%]

**9. ⏰ Urgency Hook**
"[exact words]"
Why it works: [explanation] | Retention boost: [%]

**10. 🏆 Authority Hook**
"[exact words]"
Why it works: [explanation] | Retention boost: [%]

### 🏆 TOP 3 RECOMMENDATIONS
[Which 3 hooks to use and why]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "topic": topic,
        "hooks": ai_result,
    })


@app.route("/api/ti/video_plan", methods=["POST"])
def ti_video_plan():
    """Phase 7.13 — Complete video structure blueprint."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    format_type = (data.get("format") or "long-form").strip()[:30]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Video title required"}), 400
    prompt = f"""You are a YouTube video strategist. Create a complete video blueprint.

TITLE: "{title}"
NICHE: {niche or 'General'}
FORMAT: {format_type}
LANGUAGE: {language}

Create a COMPLETE VIDEO BLUEPRINT in {language}:

### 📋 VIDEO BLUEPRINT: "{title}"

### 🎯 VIDEO STRATEGY
- Target audience: [who]
- Main value proposition: [what viewer gets]
- Competitive angle: [how it's different]
- Viral potential: [score/10]

### 🗂️ STRUCTURE & TIMING
| Section | Time | Duration | Content | Retention Goal |
|---------|------|----------|---------|----------------|
[Fill with detailed breakdown]

### 🎣 HOOK OPTIONS (pick one)
1. "[hook option 1]"
2. "[hook option 2]"
3. "[hook option 3]"

### 📝 CONTENT OUTLINE
[Detailed bullet-point outline of every topic covered]

### 🎨 VISUAL PLAN
- Thumbnail concept: [description]
- B-roll list: [specific shots needed]
- Graphics/text overlays: [what to show on screen]
- Transitions: [recommended style]

### 🏷️ SEO PLAN
- Tags: [15 relevant tags]
- Description template: [first 2 lines]
- Hashtags: [5 hashtags]

### 📊 PERFORMANCE PREDICTIONS
- Expected CTR: [percentage]
- Retention curve: [description]
- Comment trigger: [what will drive comments]
- Share trigger: [what will drive shares]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "title": title,
        "format": format_type,
        "blueprint": ai_result,
    })


# ══ PHASE 7.14: SEO AUDIT & OPTIMIZATION ════════════════════════════════════

@app.route("/api/ti/seo_audit", methods=["POST"])
def ti_seo_audit():
    """Phase 7.14 — Complete SEO audit for a YouTube video."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    description = (data.get("description") or "").strip()[:2000]
    tags = (data.get("tags") or "").strip()[:500]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube SEO expert. Perform a comprehensive SEO audit.

TITLE: "{title}"
DESCRIPTION: "{description or 'Not provided'}"
TAGS: "{tags or 'Not provided'}"
NICHE: {niche or 'General'}
LANGUAGE: {language}

Provide a DETAILED SEO AUDIT in {language}:

### 🔎 SEO AUDIT REPORT

### 📊 OVERALL SEO SCORE: [0-100]/100

### 📋 TITLE ANALYSIS
- Length: [count]/100 chars — [Good/Too short/Too long]
- Keyword placement: [score/10]
- Click-through potential: [score/10]
- Searchability: [score/10]
- ✅ What works: [list]
- ❌ Issues: [list]
- 🔧 Optimized version: "[improved title]"

### 📝 DESCRIPTION ANALYSIS
- Length: [count]/5000 chars
- Keyword density: [score/10]
- Link structure: [score/10]
- First 2 lines (search preview): [score/10]
- ✅ What works: [list]
- ❌ Issues: [list]
- 🔧 Optimized first 2 lines: "[improved]"

### 🏷️ TAG ANALYSIS
- Total tags: [count]
- Relevance: [score/10]
- Mix (broad+specific): [score/10]
- Missing high-value tags: [list]
- 🔧 Optimized tag set: [15 tags]

### 🎯 KEYWORD STRATEGY
- Primary keyword: [keyword]
- Secondary keywords: [3-5 keywords]
- Long-tail opportunities: [3-5 phrases]
- Search volume estimate: [High/Medium/Low]

### ⚡ TOP 5 QUICK FIXES
[Prioritized list of immediate improvements]

### 🏆 COMPETITOR SEO COMPARISON
[How this compares to top-ranking videos for similar keywords]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "title": title,
        "audit": ai_result,
        "niche": niche,
    })


@app.route("/api/ti/smart_tags", methods=["POST"])
def ti_smart_tags():
    """Phase 7.14 — Generate AI-optimized tags."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    count = min(int(data.get("count") or 30), 50)
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""Generate {count} perfectly optimized YouTube tags for this video.

TITLE: "{title}"
NICHE: {niche or 'General'}
LANGUAGE: {language}

Rules:
- Mix broad and specific tags
- Include the exact title as first tag
- Include variations and synonyms
- Include trending related terms
- Include long-tail keywords
- Max 500 chars total (YouTube limit)

Return ONLY the tags, one per line, sorted by priority (most important first).
Format each as: [number]. [tag] — [search volume: High/Med/Low]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "title": title,
        "tags": ai_result,
        "count": count,
    })


@app.route("/api/ti/desc_writer", methods=["POST"])
def ti_desc_writer():
    """Phase 7.14 — Generate SEO-optimized video description."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    channel = (data.get("channel") or "").strip()[:100]
    links = (data.get("links") or "").strip()[:500]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""Write a perfectly SEO-optimized YouTube description for this video.

TITLE: "{title}"
CHANNEL: {channel or 'Not specified'}
NICHE: {niche or 'General'}
CUSTOM LINKS: {links or 'None'}
LANGUAGE: {language}

Write a COMPLETE DESCRIPTION in {language} (aim for 1500-3000 chars):

FIRST 2 LINES (appear in search — CRITICAL):
[Hook + primary keyword + value proposition]

FULL DESCRIPTION:
[Detailed description with natural keyword placement, timestamps placeholder, social links section, about section, hashtags]

Include:
- 📌 Timestamps section (placeholder)
- 🔗 Links section
- 📱 Social media section
- ℹ️ About the channel
- #Hashtags (5-8 relevant)

IMPORTANT: The first 2 lines must be compelling AND contain the primary keyword."""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "title": title,
        "description": ai_result,
    })


# ══ PHASE 7.15: COMMUNITY & ENGAGEMENT ══════════════════════════════════════

@app.route("/api/ti/comment_strategy", methods=["POST"])
def ti_comment_strategy():
    """Phase 7.15 — AI comment engagement strategy."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    video_topic = (data.get("video_topic") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not video_topic and not niche:
        return jsonify({"error": "Video topic or niche required"}), 400
    prompt = f"""You are a YouTube community engagement expert. Create a comment strategy.

VIDEO TOPIC: {video_topic or 'General content'}
NICHE: {niche or 'General'}
LANGUAGE: {language}

Provide a DETAILED COMMENT STRATEGY in {language}:

### 💬 COMMENT ENGAGEMENT STRATEGY

### 🎯 PINNED COMMENT
[Write the EXACT pinned comment to post — must drive engagement]

### ❓ ENGAGEMENT QUESTIONS (5)
[5 questions to ask viewers that drive comments]

### 💡 REPLY TEMPLATES (10)
For different comment types:
1. **Positive comment**: "[reply template]"
2. **Question**: "[reply template]"
3. **Criticism**: "[reply template]"
4. **Request**: "[reply template]"
5. **Personal story**: "[reply template]"
6. **Confusion**: "[reply template]"
7. **Complement**: "[reply template]"
8. **Disagreement**: "[reply template]"
9. **Funny**: "[reply template]"
10. **First commenter**: "[reply template]"

### 📊 ENGAGEMENT METRICS TO TRACK
[Key metrics and how to improve them]

### 🔥 CONTROVERSY TRIGGERS
[3 safe controversial statements to drive debate]

### ⏰ OPTIMAL REPLY TIMING
[When to reply for maximum algorithm boost]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "video_topic": video_topic,
        "niche": niche,
        "strategy": ai_result,
    })


@app.route("/api/ti/community_post", methods=["POST"])
def ti_community_post():
    """Phase 7.15 — Generate community tab posts."""
    data = request.json or {}
    post_type = (data.get("post_type") or "poll").strip()[:30]
    niche = (data.get("niche") or "").strip()[:100]
    topic = (data.get("topic") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not niche and not topic:
        return jsonify({"error": "Niche or topic required"}), 400
    prompt = f"""You are a YouTube Community Tab expert. Generate an engaging community post.

POST TYPE: {post_type}
NICHE: {niche or 'General'}
TOPIC: {topic or 'General'}
LANGUAGE: {language}

Generate 5 COMMUNITY POSTS in {language}:

For each post:
### Post [number] — [{post_type}]
**Text**: [exact post text]
**Poll options** (if poll): [4 options]
**Image suggestion**: [what image to use]
**Best time to post**: [day and time]
**Expected engagement**: [likes/comments estimate]
**Why it works**: [explanation]

Include variety:
1. Poll post
2. Question post  
3. Behind-the-scenes
4. Teaser/countdown
5. Interactive challenge"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "post_type": post_type,
        "posts": ai_result,
    })


@app.route("/api/ti/engagement_boost", methods=["POST"])
def ti_engagement_boost():
    """Phase 7.15 — AI engagement and retention optimizer."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    current_metrics = (data.get("metrics") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not title and not niche:
        return jsonify({"error": "Title or niche required"}), 400
    prompt = f"""You are a YouTube retention and engagement optimization expert.

VIDEO: {title or 'General'}
NICHE: {niche or 'General'}
CURRENT METRICS: {current_metrics or 'Not provided'}
LANGUAGE: {language}

Provide a DETAILED ENGAGEMENT BOOST PLAN in {language}:

### 🚀 ENGAGEMENT BOOST PLAN

### 📈 RETENTION OPTIMIZATION
- Hook improvements (first 30 seconds)
- Pattern interrupts (every 2-3 min)
- Re-engagement triggers
- End screen strategy

### 💬 COMMENT GENERATION TACTICS
[10 techniques to increase comments by 300%]

### 👍 LIKE RATIO OPTIMIZATION
[5 strategies to improve like ratio]

### 🔔 SUBSCRIBER CONVERSION
[5 techniques to convert viewers to subscribers]

### 📤 SHARE TRIGGERS
[What makes people share this type of content]

### 🔄 ALGORITHM SIGNALS
[How to send positive signals to YouTube algorithm]

### 📊 ENGAGEMENT SCORECARD
| Metric | Current | Target | Strategy |
|--------|---------|--------|----------|
[Fill with actionable metrics]

### ⚡ 7-DAY ACTION PLAN
[Day-by-day plan to boost engagement]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "title": title,
        "boost_plan": ai_result,
    })


# ══ PHASE 7.16: THUMBNAIL STRATEGY & VISUAL A/B ═════════════════════════════

@app.route("/api/ti/thumb_strategy", methods=["POST"])
def ti_thumb_strategy():
    """Phase 7.16 — AI thumbnail strategy with psychology and CTR optimization."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    style = (data.get("style") or "dramatic").strip()[:30]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube thumbnail design expert and click psychology specialist.

TITLE: "{title}"
NICHE: {niche or 'General'}
STYLE: {style}
LANGUAGE: {language}

Create a COMPLETE THUMBNAIL STRATEGY in {language}:

### 🎨 THUMBNAIL STRATEGY

### 🧠 CLICK PSYCHOLOGY
- What emotion to trigger: [specific emotion]
- Color psychology: [which colors and why]
- Face expression needed: [specific expression]
- Eye direction: [where eyes should look]
- Curiosity gap visual: [how to create visual mystery]

### 📐 LAYOUT (3 concepts)
For each concept:
**Concept [1/2/3]: [Name]**
- Background: [description + hex colors]
- Main subject: [what/who + position]
- Text overlay: [exact text, max 4 words] 
- Font: [style + size + color + outline]
- Additional elements: [arrows, circles, emojis]
- Estimated CTR: [percentage]

### 🎯 TEXT OVERLAY RULES
- Maximum 4 words
- Font size: [recommendation]
- Contrast ratio: [min 4.5:1]
- Position: [where on thumbnail]
- Shadow/outline: [specifications]

### ❌ COMMON MISTAKES TO AVOID
[5 thumbnail mistakes for this niche]

### 📊 A/B TEST RECOMMENDATIONS
[How to test these thumbnails effectively]

### 🔥 VIRAL THUMBNAIL FORMULAS
[3 proven formulas for this niche with examples]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "title": title,
        "strategy": ai_result,
        "style": style,
    })


@app.route("/api/ti/thumb_ab_test", methods=["POST"])
def ti_thumb_ab_test():
    """Phase 7.16 — Compare two thumbnail concepts."""
    data = request.json or {}
    concept_a = (data.get("concept_a") or "").strip()[:300]
    concept_b = (data.get("concept_b") or "").strip()[:300]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not concept_a or not concept_b:
        return jsonify({"error": "Both concepts required"}), 400
    prompt = f"""You are a YouTube thumbnail A/B testing expert.

CONCEPT A: "{concept_a}"
CONCEPT B: "{concept_b}"
NICHE: {niche or 'General'}
LANGUAGE: {language}

Provide a DETAILED A/B COMPARISON in {language}:

### 🔬 THUMBNAIL A/B TEST ANALYSIS

### 📊 SCORING MATRIX
| Criterion | Concept A | Concept B | Winner |
|-----------|-----------|-----------|--------|
| Click Appeal | /10 | /10 | |
| Color Impact | /10 | /10 | |
| Text Readability | /10 | /10 | |
| Mobile Visibility | /10 | /10 | |
| Emotional Trigger | /10 | /10 | |
| Curiosity Gap | /10 | /10 | |
| Niche Fit | /10 | /10 | |
| **TOTAL** | **/70** | **/70** | |

### 🏆 WINNER: [A or B]
**Confidence**: [High/Medium/Low]
**Expected CTR Difference**: [X% higher]

### 🧠 PSYCHOLOGY BREAKDOWN
[Why the winner works better psychologically]

### 🔧 OPTIMIZATION SUGGESTIONS
For A: [3 improvements]
For B: [3 improvements]

### 💡 HYBRID CONCEPT
[Combine the best of both into a superior concept]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "concept_a": concept_a,
        "concept_b": concept_b,
        "comparison": ai_result,
    })


@app.route("/api/ti/thumb_text", methods=["POST"])
def ti_thumb_text():
    """Phase 7.16 — Optimize thumbnail text overlay."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    current_text = (data.get("current_text") or "").strip()[:100]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube thumbnail text optimization expert.

VIDEO TITLE: "{title}"
CURRENT TEXT ON THUMBNAIL: "{current_text or 'None'}"
NICHE: {niche or 'General'}
LANGUAGE: {language}

Generate 10 OPTIMIZED TEXT OVERLAYS in {language}:

### ✏️ THUMBNAIL TEXT OPTIONS

For each option:
**[number]. "[TEXT]"** (max 4 words)
- Why it works: [explanation]
- Emotion triggered: [emotion]
- Font suggestion: [font name + weight]
- Color: [hex code + reasoning]
- Position: [where on thumbnail]
- Estimated CTR boost: [+X%]

### RULES FOLLOWED:
- Max 4 words per option
- High contrast on any background
- Readable at small sizes (mobile)
- Creates curiosity gap with title
- Triggers emotional response

### 🏆 TOP 3 RECOMMENDATIONS
[Ranked best to worst with reasoning]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "title": title,
        "text_options": ai_result,
    })


# ══ PHASE 7.17: ANALYTICS PRO & GROWTH TRACKING ═════════════════════════════

@app.route("/api/ti/growth_simulator", methods=["POST"])
def ti_growth_simulator():
    """Phase 7.17 — Channel growth simulator with math projections."""
    data = request.json or {}
    subscribers = int(data.get("subscribers") or 0)
    monthly_views = int(data.get("monthly_views") or 0)
    videos_per_month = int(data.get("videos_per_month") or 4)
    growth_rate = float(data.get("growth_rate") or 10)
    months = min(int(data.get("months") or 12), 36)
    if subscribers <= 0:
        return jsonify({"error": "Subscribers must be > 0"}), 400
    import math
    projections = []
    current_subs = subscribers
    current_views = monthly_views or subscribers * 3
    milestones = []
    milestone_targets = [1000, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000]
    for m in range(1, months + 1):
        monthly_growth = growth_rate / 100
        # Diminishing returns formula — larger channels grow slower
        adjusted_rate = monthly_growth * (1 - math.log10(max(current_subs, 10)) / 8)
        adjusted_rate = max(adjusted_rate, 0.003)  # min 0.3% growth
        new_subs = int(current_subs * (1 + adjusted_rate))
        if new_subs <= current_subs:
            new_subs = current_subs + max(1, int(current_subs * 0.01))  # guarantee min growth
        new_views = int(current_views * (1 + adjusted_rate * 0.8))
        if new_views <= current_views:
            new_views = current_views + max(10, int(current_views * 0.01))
        subs_gained = new_subs - current_subs
        # Check milestones
        for target in milestone_targets:
            if current_subs < target <= new_subs:
                milestones.append({"month": m, "milestone": target, "label": f"{target:,} subscribers"})
        projections.append({
            "month": m,
            "subscribers": new_subs,
            "monthly_views": new_views,
            "subs_gained": subs_gained,
            "growth_rate_actual": round(adjusted_rate * 100, 2),
            "estimated_revenue": round(new_views * 0.003, 2),
            "videos_total": videos_per_month * m,
        })
        current_subs = new_subs
        current_views = new_views
    return jsonify({
        "current": {"subscribers": subscribers, "monthly_views": monthly_views},
        "projections": projections,
        "milestones": milestones,
        "final": projections[-1] if projections else {},
        "total_growth": round((current_subs - subscribers) / subscribers * 100, 1),
    })


@app.route("/api/ti/viral_probability", methods=["POST"])
def ti_viral_probability():
    """Phase 7.17 — AI viral probability calculator."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = data.get("subscribers", "unknown")
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube viral content analyst with deep data expertise.

TITLE: "{title}"
NICHE: {niche or 'General'}
CHANNEL SUBSCRIBERS: {subscribers}
LANGUAGE: {language}

Calculate VIRAL PROBABILITY in {language}:

### 🎯 VIRAL PROBABILITY REPORT

### 📊 VIRAL SCORE: [0-100]%

### 🔬 FACTOR ANALYSIS
| Factor | Score | Weight | Impact |
|--------|-------|--------|--------|
| Title Hook Power | /10 | 20% | |
| Emotional Trigger | /10 | 15% | |
| Curiosity Gap | /10 | 15% | |
| Search Demand | /10 | 15% | |
| Niche Trending | /10 | 10% | |
| Shareability | /10 | 10% | |
| Click-Through Est. | /10 | 10% | |
| Controversy Level | /10 | 5% | |

### 📈 PROJECTED PERFORMANCE
- First 24h views: [estimate]
- First week views: [estimate]  
- First month views: [estimate]
- Viral ceiling: [max views possible]
- Organic reach: [percentage]

### ⚡ VIRAL TRIGGERS PRESENT
[List which viral triggers the title activates]

### 🔧 OPTIMIZATION TO INCREASE PROBABILITY
[5 specific changes to increase viral probability by X%]

### ⚠️ RISK FACTORS
[What could prevent this from going viral]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "title": title,
        "analysis": ai_result,
    })


@app.route("/api/ti/performance_predictor", methods=["POST"])
def ti_performance_predictor():
    """Phase 7.17 — AI video performance prediction."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = data.get("subscribers", "10000")
    avg_views = data.get("avg_views", "5000")
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube analytics expert who predicts video performance.

TITLE: "{title}"
NICHE: {niche or 'General'}
CHANNEL SUBSCRIBERS: {subscribers}
AVERAGE VIEWS PER VIDEO: {avg_views}
LANGUAGE: {language}

Predict DETAILED PERFORMANCE in {language}:

### 📊 PERFORMANCE PREDICTION

### 🎯 PREDICTED METRICS
| Metric | Predicted | Confidence |
|--------|-----------|------------|
| Views (24h) | | High/Med/Low |
| Views (7 days) | | |
| Views (30 days) | | |
| CTR | % | |
| Avg. View Duration | min | |
| Retention Rate | % | |
| Likes | | |
| Comments | | |
| Shares | | |
| New Subscribers | | |

### 📈 PERFORMANCE VS CHANNEL AVERAGE
- Expected: [above/below/at] average
- By how much: [+/-X%]
- Reasoning: [why]

### ⏰ BEST UPLOAD TIME
[Specific day and time for maximum reach]

### 🔄 ALGORITHM PREDICTION
- Browse features probability: [%]
- Suggested videos probability: [%]  
- Search ranking potential: [High/Med/Low]
- Shorts feed potential: [if applicable]

### 💰 REVENUE ESTIMATE
- Ad revenue (30 days): $[estimate]
- RPM estimate: $[estimate]

### 🎯 ACTIONABLE RECOMMENDATIONS
[5 things to do before/after upload to maximize performance]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "title": title,
        "prediction": ai_result,
    })


# ══ PHASE 7.18: AUDIENCE PERSONA & PSYCH PROFILE ════════════════════════════

@app.route("/api/ti/audience_persona", methods=["POST"])
def ti_audience_persona():
    """Phase 7.18 — AI audience persona builder."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    channel = (data.get("channel") or "").strip()[:100]
    content_type = (data.get("content_type") or "educational").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube audience research expert and consumer psychology specialist.

NICHE: {niche}
CHANNEL: {channel or 'Generic'}
CONTENT TYPE: {content_type}
LANGUAGE: {language}

Create 3 DETAILED AUDIENCE PERSONAS in {language}:

### 👥 AUDIENCE PERSONAS

For each persona:

### Persona [1/2/3]: "[Name]"
**📊 Demographics**
- Age: [range]
- Gender: [distribution %]
- Location: [countries/regions]
- Income: [range]
- Education: [level]
- Occupation: [common jobs]

**🧠 Psychographics**
- Values: [top 3]
- Interests: [5 interests]
- Pain points: [3 frustrations]
- Aspirations: [what they want to achieve]
- Content consumption habits: [when, where, how much]
- Social media behavior: [platforms and usage]

**📱 YouTube Behavior**
- Watch time: [hours/week]
- Preferred video length: [minutes]
- When they watch: [time of day]
- Device: [mobile vs desktop %]
- Subscription habits: [how many channels]
- Engagement style: [like, comment, share patterns]

**🎯 Content Triggers**
- What makes them click: [3 triggers]
- What makes them subscribe: [3 triggers]
- What makes them share: [3 triggers]
- What makes them leave: [3 turn-offs]

### 📊 AUDIENCE OVERLAP ANALYSIS
[How these 3 personas overlap and differ]

### 🎯 CONTENT STRATEGY PER PERSONA
[What content type works best for each]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "niche": niche,
        "personas": ai_result,
    })


@app.route("/api/ti/psycho_profile", methods=["POST"])
def ti_psycho_profile():
    """Phase 7.18 — Psychological profile of target audience."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    title = (data.get("title") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not niche and not title:
        return jsonify({"error": "Niche or title required"}), 400
    prompt = f"""You are a consumer psychology expert specializing in YouTube audiences.

NICHE: {niche or 'General'}
VIDEO TITLE: {title or 'General content'}
LANGUAGE: {language}

Create a DEEP PSYCHOLOGICAL PROFILE of the target audience in {language}:

### 🧠 PSYCHOLOGICAL PROFILE

### 🎭 EMOTIONAL LANDSCAPE
- Primary emotions: [3 emotions when searching this content]
- Emotional state before clicking: [description]
- Desired emotional state after watching: [description]
- Emotional triggers: [what activates engagement]

### 🔬 COGNITIVE PATTERNS
- Decision-making style: [analytical/impulsive/social proof]
- Information processing: [visual/auditory/reading]
- Attention span: [how long before drop-off]
- Cognitive biases exploitable: [5 biases with examples]

### 💡 MOTIVATION FRAMEWORK
| Need Level | Specific Need | Content Approach |
|------------|--------------|------------------|
| Survival | | |
| Safety | | |
| Belonging | | |
| Esteem | | |
| Self-actualization | | |

### 🎯 PERSUASION MAP
- Cialdini principles that work: [which 6 principles apply]
- Trust triggers: [what builds trust]
- Authority signals: [what they respect]
- Social proof needs: [what convinces them]

### ⚡ CONTENT CONSUMPTION PSYCHOLOGY
- Why they binge: [psychological reason]
- Why they subscribe: [core motivation]
- Why they share: [social currency reason]
- Why they comment: [participation trigger]

### 🔧 ACTIONABLE INSIGHTS
[10 specific content decisions based on this profile]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "niche": niche,
        "profile": ai_result,
    })


@app.route("/api/ti/content_matrix", methods=["POST"])
def ti_content_matrix():
    """Phase 7.18 — Content strategy matrix based on audience."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    goals = (data.get("goals") or "growth").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube content strategy expert.

NICHE: {niche}
GOALS: {goals}
LANGUAGE: {language}

Create a CONTENT STRATEGY MATRIX in {language}:

### 📊 CONTENT MATRIX

### 🎯 CONTENT PILLARS (4)
| Pillar | Purpose | Frequency | Example Titles |
|--------|---------|-----------|----------------|
| Hero | Viral reach | 1/month | [3 titles] |
| Hub | Regular audience | 2/week | [3 titles] |
| Help | Search/SEO | 1/week | [3 titles] |
| Shorts | Discovery | Daily | [3 titles] |

### 📅 WEEKLY CONTENT PLAN
| Day | Content Type | Pillar | Title Idea |
|-----|-------------|--------|------------|
| Mon | | | |
| Tue | | | |
| Wed | | | |
| Thu | | | |
| Fri | | | |
| Sat | | | |
| Sun | | | |

### 🔄 CONTENT FUNNEL
[How each content type feeds into the next]
Awareness → Interest → Desire → Action → Loyalty

### 📈 GROWTH LEVERS
[5 specific content strategies that compound growth]

### ⚡ QUICK WINS
[3 content ideas that could grow the channel fastest]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({
        "niche": niche,
        "matrix": ai_result,
    })



# ══ PHASE 7.19: SHORTS OPTIMIZER & REPURPOSE ════════════════════════════════

@app.route("/api/ti/shorts_strategy", methods=["POST"])
def ti_shorts_strategy():
    """Phase 7.19 — AI Shorts optimization strategy."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not title and not niche:
        return jsonify({"error": "Title or niche required"}), 400
    prompt = f"""You are a YouTube Shorts expert with deep knowledge of short-form viral content.

LONG-FORM TITLE: "{title or 'General content'}"
NICHE: {niche or 'General'}
LANGUAGE: {language}

Create a SHORTS OPTIMIZATION STRATEGY in {language}:

### ⚡ SHORTS STRATEGY

### 🎬 5 SHORTS IDEAS FROM THIS TOPIC
For each Short:
**Short [1-5]: "[Title]"**
- Hook (first 2 seconds): [exact words]
- Duration: [15/30/45/60 seconds]
- Visual format: [talking head/text overlay/B-roll/screen record]
- CTA: [what to say at the end]
- Viral potential: [High/Medium/Low]

### 📐 OPTIMAL SHORTS FORMAT
- Aspect ratio: 9:16
- Resolution: 1080x1920
- Text size: [recommendation]
- Caption style: [recommendation]
- Music: [yes/no + type]

### ⏰ TIMING & HOOKS
- First 1 second: [what must happen]
- 3-second hook: [exact script]
- Pattern interrupt at: [timestamp]
- CTA placement: [when]
- Loop trigger: [how to make viewers rewatch]

### 📊 SHORTS ALGORITHM TIPS
[5 algorithm-specific tips for Shorts feed]

### 🔄 LONG-FORM TO SHORTS PIPELINE
[How to extract 5-10 Shorts from one long video]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "strategy": ai_result})


@app.route("/api/ti/shorts_hooks", methods=["POST"])
def ti_shorts_hooks():
    """Phase 7.19 — Generate viral hooks for Shorts."""
    data = request.json or {}
    topic = (data.get("topic") or "").strip()[:200].replace("<", "&lt;").replace(">", "&gt;")
    niche = (data.get("niche") or "").strip()[:100].replace("<", "&lt;").replace(">", "&gt;")
    language = data.get("language", "Portuguese")[:50].replace("<", "&lt;").replace(">", "&gt;")
    if not topic:
        return jsonify({"error": "Topic required"}), 400
    prompt = f"""You are a short-form content hook specialist.

TOPIC: "{topic}"
NICHE: {niche or 'General'}
LANGUAGE: {language}

Generate 15 VIRAL SHORTS HOOKS in {language}:

### ⚡ VIRAL SHORTS HOOKS

For each hook:
**[number]. "[EXACT HOOK TEXT]"**
- Type: [question/shock/controversial/story/challenge]
- Duration: [how long to deliver: 1-3 seconds]
- Why it works: [psychology behind it]
- Best visual: [what to show while saying it]
- Estimated scroll-stop rate: [%]

### HOOK CATEGORIES:
1-3: Shock/Controversy hooks
4-6: Question hooks
7-9: Story hooks
10-12: Challenge/Dare hooks
13-15: Curiosity gap hooks

### 🏆 TOP 3 HOOKS (ranked by viral potential)"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"topic": topic, "hooks": ai_result})


@app.route("/api/ti/repurpose_strategy", methods=["POST"])
def ti_repurpose_strategy():
    """Phase 7.19 — AI cross-platform repurposing plan."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    platforms = (data.get("platforms") or "TikTok, Instagram, Twitter").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a cross-platform content repurposing expert.

ORIGINAL VIDEO: "{title}"
NICHE: {niche or 'General'}
TARGET PLATFORMS: {platforms}
LANGUAGE: {language}

Create a REPURPOSING STRATEGY in {language}:

### 🔄 REPURPOSE STRATEGY

### 📱 PLATFORM ADAPTATIONS
For each platform:
**[Platform]**
- Format: [dimensions + duration]
- Adapted title: [platform-specific title]
- Caption: [exact caption text]
- Hashtags: [10 hashtags]
- Best posting time: [day + time]
- Hook adaptation: [how to change the hook]
- CTA: [platform-specific CTA]

### 📅 POSTING SCHEDULE
| Day | Platform | Content Type | Time |
|-----|----------|-------------|------|
[7-day repurposing schedule]

### 💡 CONTENT MULTIPLICATION
- Blog post title: [title]
- Twitter thread: [5 tweet hooks]
- Instagram carousel: [5 slide topics]
- TikTok series: [3 episode ideas]
- Pinterest pins: [3 pin ideas]
- Newsletter subject: [subject line]

### 📊 EXPECTED REACH MULTIPLIER
[How much total reach increases with repurposing]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "plan": ai_result})



# ══ PHASE 7.20: PLAYLIST STRATEGY & SERIES PLANNER ══════════════════════════

@app.route("/api/ti/playlist_strategy", methods=["POST"])
def ti_playlist_strategy():
    """Phase 7.20 — AI playlist optimization strategy."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    videos_count = int(data.get("videos_count") or 20)
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube playlist optimization expert who maximizes watch time and session duration.

NICHE: {niche}
TOTAL VIDEOS AVAILABLE: {videos_count}
LANGUAGE: {language}

Create a PLAYLIST STRATEGY in {language}:

### 📋 PLAYLIST STRATEGY

### 🎯 PLAYLIST ARCHITECTURE (5 playlists)
For each playlist:
**Playlist [1-5]: "[Title]"**
- Theme: [specific sub-topic]
- Videos: [8-15 video titles in order]
- Opening video: [which video starts and why]
- Closer video: [which ends and why]
- SEO keywords: [5 keywords]
- Estimated session time: [minutes]

### 📊 PLAYLIST SEO
- Title format: [best format for search]
- Description template: [with keywords]
- Tags: [10 playlist-level tags]

### 🔄 WATCH TIME OPTIMIZATION
- Video ordering logic: [why this sequence]
- Cliffhanger between videos: [how to connect]
- Auto-play optimization: [thumbnail consistency]
- Playlist end screens: [strategy]

### 📈 GROWTH THROUGH PLAYLISTS
[5 ways playlists increase channel authority]

### ⚡ QUICK WINS
[3 playlist changes that immediately boost watch time]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "strategy": ai_result})


@app.route("/api/ti/series_planner", methods=["POST"])
def ti_series_planner():
    """Phase 7.20 — AI video series blueprint."""
    data = request.json or {}
    topic = (data.get("topic") or "").strip()[:200]
    episodes = int(data.get("episodes") or 10)
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not topic:
        return jsonify({"error": "Topic required"}), 400
    prompt = f"""You are a YouTube series planning expert who creates binge-worthy content.

SERIES TOPIC: "{topic}"
NUMBER OF EPISODES: {min(episodes, 30)}
NICHE: {niche or 'General'}
LANGUAGE: {language}

Create a SERIES BLUEPRINT in {language}:

### 🎬 SERIES BLUEPRINT: "{topic}"

### 📋 EPISODE GUIDE
For each episode:
**Episode [N]: "[Title]"**
- Hook: [opening hook for this episode]
- Core content: [what this episode covers]
- Cliffhanger: [what makes viewers watch next]
- Duration: [estimated minutes]
- Thumbnail concept: [brief visual description]

### 🔗 SERIES CONTINUITY
- Series intro template: [standard opening]
- Episode transitions: [how to connect episodes]
- Recurring elements: [what stays consistent]
- Character/topic arc: [how the story evolves]

### 📊 RELEASE STRATEGY
- Upload frequency: [recommendation]
- Best launch day: [day of week]
- Premiere vs regular: [when to use each]
- Community engagement: [between episodes]

### 🎯 SERIES SEO
- Series title format: [naming convention]
- Playlist strategy: [how to organize]
- Cross-promotion: [how episodes promote each other]

### 📈 SUCCESS METRICS
[How to know if the series is working]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"topic": topic, "blueprint": ai_result, "episodes": min(episodes, 30)})


@app.route("/api/ti/content_pillars", methods=["POST"])
def ti_content_pillars():
    """Phase 7.20 — AI content pillar generator with 30-day plan."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    goals = (data.get("goals") or "growth").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube content strategy architect.

NICHE: {niche}
GOALS: {goals}
LANGUAGE: {language}

Create CONTENT PILLARS with 30-DAY PLAN in {language}:

### 🏛️ CONTENT PILLARS

### 📊 4 PILLARS
For each pillar:
**Pillar [1-4]: "[Name]"**
- Purpose: [why this pillar exists]
- Audience: [who watches this]
- Format: [video format]
- Frequency: [how often]
- 5 video ideas: [titles]
- KPI: [key metric to track]

### 📅 30-DAY CONTENT CALENDAR
| Day | Pillar | Video Title | Type | Est. Views |
|-----|--------|-------------|------|------------|
[30 days of content]

### 🔄 CONTENT ROTATION
[How to rotate between pillars for maximum growth]

### 📈 SCALING STRATEGY
[How to add new pillars as channel grows]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "pillars": ai_result})



# ══ PHASE 7.21: BRAND KIT & CHANNEL IDENTITY ════════════════════════════════

@app.route("/api/ti/brand_identity", methods=["POST"])
def ti_brand_identity():
    """Phase 7.21 — AI channel branding strategy."""
    data = request.json or {}
    channel = (data.get("channel") or "").strip()[:100]
    niche = (data.get("niche") or "").strip()[:100]
    style = (data.get("style") or "professional").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube branding expert and visual identity specialist.

CHANNEL: {channel or 'New Channel'}
NICHE: {niche}
STYLE: {style}
LANGUAGE: {language}

Create a COMPLETE BRAND KIT in {language}:

### 🎨 BRAND IDENTITY KIT

### 🎯 BRAND POSITIONING
- Brand promise: [one sentence]
- Unique value proposition: [what sets you apart]
- Brand voice: [tone description]
- Brand personality: [3 traits]
- Target audience: [specific description]

### 🎨 VISUAL IDENTITY
- Primary color: [hex + meaning]
- Secondary color: [hex + meaning]
- Accent color: [hex + meaning]
- Font for titles: [Google Font name]
- Font for body: [Google Font name]
- Logo concept: [description]

### 📐 CHANNEL ART SPECS
- Banner: [1280x720 layout description]
- Profile picture: [concept]
- Thumbnail template: [consistent elements]
- Watermark: [recommendation]
- End screen layout: [description]

### ✍️ BRAND VOICE GUIDE
- Intro catchphrase: [exact words]
- Outro catchphrase: [exact words]
- Comment response style: [examples]
- Community post tone: [description]

### 📝 CHANNEL SECTIONS
[5 recommended sections with descriptions]

### 🔗 CROSS-PLATFORM CONSISTENCY
[How to maintain brand across platforms]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "brand": ai_result, "style": style})


@app.route("/api/ti/channel_about", methods=["POST"])
def ti_channel_about():
    """Phase 7.21 — AI channel description writer."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    channel = (data.get("channel") or "").strip()[:100]
    keywords = (data.get("keywords") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube channel optimization expert.

CHANNEL: {channel or 'My Channel'}
NICHE: {niche}
KEYWORDS: {keywords or 'Not specified'}
LANGUAGE: {language}

Write 5 CHANNEL DESCRIPTIONS (About section) in {language}:

### 📝 CHANNEL DESCRIPTIONS

For each version:
**Version [1-5]: "[Style]"**
[Full channel description, 150-300 words]
- SEO keywords naturally included
- Clear value proposition
- Upload schedule mentioned
- CTA to subscribe
- Social links placeholder

### 🎯 SEO OPTIMIZATION
- Primary keyword: [keyword]
- Secondary keywords: [5 keywords]
- Search phrases: [3 phrases people search]

### 📊 BEST PRACTICES
[5 tips for channel About section]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "descriptions": ai_result})


@app.route("/api/ti/collab_finder", methods=["POST"])
def ti_collab_finder():
    """Phase 7.21 — AI collaboration opportunity analyzer."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = data.get("subscribers", "10000")
    goals = (data.get("goals") or "growth").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube collaboration strategy expert.

NICHE: {niche}
SUBSCRIBERS: {subscribers}
GOALS: {goals}
LANGUAGE: {language}

Create a COLLABORATION STRATEGY in {language}:

### 🤝 COLLABORATION STRATEGY

### 🎯 IDEAL COLLAB PARTNERS (5 profiles)
For each:
**Partner [1-5]: "[Type]"**
- Channel size: [subscriber range]
- Content overlap: [%]
- Audience match: [description]
- Collab format: [video type]
- Pitch template: [exact message to send]
- Expected growth: [subscriber gain]

### 📧 OUTREACH TEMPLATES
[3 different DM/email templates]

### 🎬 COLLAB VIDEO IDEAS (10)
[10 video concepts that work for collaborations]

### 📊 COLLAB SUCCESS METRICS
[How to measure if a collaboration was successful]

### ⚠️ RED FLAGS
[5 signs a collaboration won't work]

### 📈 GROWTH PROJECTION
[Expected growth from strategic collaborations over 6 months]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "strategy": ai_result})



# ══ PHASE 7.22: MONETIZATION PRO & REVENUE OPTIMIZER ════════════════════════

@app.route("/api/ti/monetization_pro", methods=["POST"])
def ti_monetization_pro():
    """Phase 7.22 — AI multi-stream revenue blueprint."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = data.get("subscribers", "10000")
    monthly_views = data.get("monthly_views", "50000")
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube monetization expert and digital business strategist.

NICHE: {niche}
SUBSCRIBERS: {subscribers}
MONTHLY VIEWS: {monthly_views}
LANGUAGE: {language}

Create a COMPLETE MONETIZATION BLUEPRINT in {language}:

### 💰 MONETIZATION BLUEPRINT

### 📊 REVENUE STREAMS (ranked by potential)
For each stream:
**[1-8] [Revenue Stream]**
- Current potential: $[monthly estimate]
- Setup difficulty: [Easy/Medium/Hard]
- Time to first revenue: [weeks/months]
- Scalability: [1-10]
- Action steps: [3 concrete steps]

Streams to analyze:
1. AdSense optimization
2. Sponsorships/Brand deals
3. Affiliate marketing
4. Digital products (courses, ebooks)
5. Memberships/Patreon
6. Merchandise
7. Consulting/Coaching
8. Super Chat/Thanks

### 💵 REVENUE PROJECTION
| Month | AdSense | Sponsors | Products | Total |
|-------|---------|----------|----------|-------|
[12-month projection]

### 🎯 QUICK MONETIZATION WINS
[5 things to do THIS WEEK to start earning]

### 📈 SCALING ROADMAP
[How to go from $0 to $10K/month]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "strategy": ai_result})


@app.route("/api/ti/sponsor_pitch_pro", methods=["POST"])
def ti_sponsor_pitch_pro():
    """Phase 7.22 — AI sponsor pitch generator."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    channel = (data.get("channel") or "").strip()[:100]
    subscribers = data.get("subscribers", "10000")
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube sponsorship negotiation expert.

CHANNEL: {channel or 'My Channel'}
NICHE: {niche}
SUBSCRIBERS: {subscribers}
LANGUAGE: {language}

Create SPONSOR PITCH MATERIALS in {language}:

### 📧 SPONSOR PITCH KIT

### 💼 MEDIA KIT CONTENT
- Channel summary: [2-3 sentences]
- Audience demographics: [age, gender, location]
- Engagement rate: [estimated]
- Average views: [per video]
- Content style: [description]

### 📧 3 PITCH EMAIL TEMPLATES
**Template 1: Cold Outreach**
[Full email text]

**Template 2: After Engagement**
[Full email text]

**Template 3: Follow-up**
[Full email text]

### 💰 PRICING GUIDE
| Integration Type | Price Range | Deliverables |
|-----------------|-------------|--------------|
| Dedicated video | $X-$Y | [details] |
| 60s integration | $X-$Y | [details] |
| Shorts mention | $X-$Y | [details] |
| Series sponsor | $X-$Y | [details] |

### 🎯 IDEAL BRANDS TO PITCH (10)
[10 specific brand categories with examples]

### ⚠️ NEGOTIATION TIPS
[5 tips for maximizing sponsor revenue]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "pitch": ai_result})


@app.route("/api/ti/product_ideas_pro", methods=["POST"])
def ti_product_ideas_pro():
    """Phase 7.22 — AI digital product ideas."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    audience = (data.get("audience") or "general").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a digital product creation expert for content creators.

NICHE: {niche}
TARGET AUDIENCE: {audience}
LANGUAGE: {language}

Generate DIGITAL PRODUCT IDEAS in {language}:

### 🛍️ DIGITAL PRODUCT IDEAS

### 🎓 COURSES (3 ideas)
For each:
**Course [1-3]: "[Title]"**
- Modules: [5-8 module titles]
- Price point: $[price]
- Target student: [who buys this]
- Unique angle: [why this stands out]
- Launch strategy: [3 steps]
- Revenue potential: $[monthly]

### 📱 DIGITAL DOWNLOADS (5 ideas)
For each:
**Product [1-5]: "[Name]"**
- Type: [template/ebook/checklist/toolkit/preset]
- Price: $[price]
- Description: [one line]
- Revenue potential: $[monthly]

### 🏆 PREMIUM COMMUNITY
- Platform: [recommendation]
- Price: $[monthly]
- Exclusive content: [what members get]
- Growth strategy: [how to reach 100 members]

### 📊 PRODUCT LAUNCH TIMELINE
| Week | Action | Goal |
|------|--------|------|
[8-week launch plan]

### 💡 VALIDATION STRATEGY
[How to validate before building]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "products": ai_result})


# ══ PHASE 7.23: SEO MASTERY & YOUTUBE ALGORITHM ═════════════════════════════

@app.route("/api/ti/seo_strategy_pro", methods=["POST"])
def ti_seo_strategy_pro():
    """Phase 7.23 — Complete YouTube SEO strategy."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    title = (data.get("title") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not niche and not title:
        return jsonify({"error": "Niche or title required"}), 400
    prompt = f"""You are a YouTube SEO grandmaster with deep knowledge of search algorithms.

NICHE: {niche or 'General'}
VIDEO TITLE: {title or 'Not specified'}
LANGUAGE: {language}

Create a COMPLETE SEO STRATEGY in {language}:

### 🔍 SEO MASTERY STRATEGY

### 🎯 KEYWORD RESEARCH
- Primary keyword: [keyword + monthly searches]
- Secondary keywords: [10 keywords with search volume]
- Long-tail keywords: [10 long-tail phrases]
- Trending keywords: [5 rising keywords]
- Competitor keywords: [5 keywords competitors rank for]

### 📝 TITLE OPTIMIZATION
- SEO-optimized title: [title with keyword]
- 3 title variations: [alternatives]
- Title score: [1-100]
- Character count: [optimal length]

### 📋 DESCRIPTION TEMPLATE
[Full 5000-char optimized description with timestamps, keywords, links]

### 🏷️ TAG STRATEGY
- Primary tags: [10 high-volume]
- Secondary tags: [10 medium-volume]
- Long-tail tags: [10 specific]
- Competitor tags: [5 stolen tags]

### 🖼️ THUMBNAIL SEO
[5 thumbnail elements that boost CTR]

### 📊 RANKING FACTORS BREAKDOWN
| Factor | Weight | Your Score | Action |
|--------|--------|------------|--------|
[10 ranking factors]

### ⚡ QUICK SEO WINS
[5 changes to make RIGHT NOW]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "seo": ai_result})


@app.route("/api/ti/video_seo_checklist", methods=["POST"])
def ti_video_seo_checklist():
    """Phase 7.23 — Pre-publish SEO checklist."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube pre-publish optimization specialist.

VIDEO TITLE: "{title}"
NICHE: {niche or 'General'}
LANGUAGE: {language}

Create a PRE-PUBLISH SEO CHECKLIST in {language}:

### ✅ VIDEO SEO CHECKLIST

### BEFORE UPLOAD
- [ ] Title optimized (60 chars max, keyword first)
- [ ] Description written (5000 chars, timestamps, links)
- [ ] Tags added (30 tags, mix of broad and specific)
- [ ] Thumbnail created (1280x720, high contrast, face)
- [ ] Category selected
- [ ] End screen prepared
- [ ] Cards planned

For each item provide:
**[Item]**
- Status: [To-do]
- Best practice: [specific recommendation for THIS video]
- Common mistake: [what to avoid]
- Example: [concrete example]

### AFTER UPLOAD (first 48 hours)
[10 actions to take]

### AFTER 1 WEEK
[5 optimization actions]

### 📊 SEO SCORE CARD
| Element | Max Score | Recommendation |
|---------|-----------|---------------|
[Score each SEO element for this specific title]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "checklist": ai_result})


@app.route("/api/ti/algorithm_decoder", methods=["POST"])
def ti_algorithm_decoder():
    """Phase 7.23 — YouTube algorithm deep analysis."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    problem = (data.get("problem") or "low views").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube algorithm reverse-engineering expert.

NICHE: {niche}
CURRENT PROBLEM: {problem}
LANGUAGE: {language}

Provide ALGORITHM ANALYSIS in {language}:

### 🧠 ALGORITHM DECODER

### 📊 HOW THE ALGORITHM WORKS FOR "{niche}"
- Discovery signals: [what triggers recommendations]
- Ranking signals: [what determines position]
- Suppression signals: [what kills reach]
- Viral triggers: [what causes explosive growth]

### 🔍 DIAGNOSING "{problem}"
- Root cause analysis: [3 possible causes]
- Data to check: [specific metrics]
- Benchmarks: [what numbers are normal]
- Fix priority: [ordered action list]

### ⚡ ALGORITHM HACKS (10)
For each:
**[Number]. [Hack Name]**
- What: [description]
- Why: [algorithm logic]
- How: [implementation steps]
- Impact: [expected result]

### 📈 30-DAY ALGORITHM RECOVERY PLAN
| Week | Focus | Actions | Expected Result |
|------|-------|---------|----------------|
[4-week plan]

### ⚠️ ALGORITHM MYTHS DEBUNKED
[5 common myths about the YouTube algorithm]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "analysis": ai_result})



# ══ PHASE 7.24: SCRIPT PRO & CONTENT WRITER ═════════════════════════════════

@app.route("/api/ti/script_pro_gen", methods=["POST"])
def ti_script_pro_gen():
    """Phase 7.24 — AI full video script with timestamps."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    duration = int(data.get("duration") or 10)
    tone = (data.get("tone") or "engaging").strip()[:50]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are an elite YouTube scriptwriter who creates viral, engaging video scripts.

VIDEO TITLE: "{title}"
DURATION: {min(duration, 60)} minutes
TONE: {tone}
NICHE: {niche or 'General'}
LANGUAGE: {language}

Write a COMPLETE VIDEO SCRIPT in {language}:

### 🎬 VIDEO SCRIPT: "{title}"
Duration: ~{min(duration, 60)} min | Tone: {tone}

### [0:00 - 0:30] HOOK
[Write the exact opening hook — first 30 seconds that grab attention]

### [0:30 - 2:00] INTRO
[Introduction that establishes credibility and previews content]

### [2:00 - {min(duration,60)-2}:00] MAIN CONTENT
For each section:
**[Timestamp] Section Title**
[Full script text with transitions]
[B-roll suggestion: visual description]
[Graphics suggestion: text overlays]

### [{min(duration,60)-2}:00 - {min(duration,60)}:00] OUTRO
[Strong closing with CTA]

### 📝 SCRIPT NOTES
- Key phrases to emphasize: [list]
- Emotional beats: [where to shift tone]
- B-roll needed: [list of visuals]
- Music suggestions: [mood per section]
- Total word count: [estimated]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "script": ai_result, "duration": min(duration, 60)})


@app.route("/api/ti/intro_writer", methods=["POST"])
def ti_intro_writer():
    """Phase 7.24 — AI video intro generator."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    style = (data.get("style") or "storytelling").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube intro specialist who creates irresistible video openings.

VIDEO TITLE: "{title}"
STYLE: {style}
LANGUAGE: {language}

Create 5 VIDEO INTROS in {language}:

### 🎤 VIDEO INTROS

For each intro:
**Intro [1-5]: "[Style]"**
[Full intro text, 30-60 seconds when spoken]
- Style: [what makes this intro unique]
- Hook type: [question/shock/story/statistic/challenge]
- Retention prediction: [% of viewers who stay]
- Best for: [audience type]
- Transition to content: [how to bridge to main video]

Styles to use:
1. Storytelling (personal story hook)
2. Shock value (surprising fact)
3. Question-based (provocative question)
4. Statistics (data-driven opening)
5. Challenge (dare or bold claim)

### 📊 INTRO BEST PRACTICES
[5 rules for YouTube intros that retain viewers]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "intros": ai_result, "style": style})


@app.route("/api/ti/cta_generator", methods=["POST"])
def ti_cta_generator():
    """Phase 7.24 — AI call-to-action optimizer."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    goal = (data.get("goal") or "subscribe").strip()[:50]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube conversion expert who maximizes subscriber and engagement rates.

VIDEO TITLE: "{title}"
PRIMARY GOAL: {goal}
NICHE: {niche or 'General'}
LANGUAGE: {language}

Create CTA STRATEGIES in {language}:

### 📢 CTA STRATEGY

### 🎯 MID-VIDEO CTAs (5)
For each:
**CTA [1-5]:**
- Exact script: "[what to say]"
- Placement: [timestamp/moment]
- Visual: [what to show on screen]
- Psychology: [why this works]

### 🔚 END SCREEN CTAs (3)
For each:
**End CTA [1-3]:**
- Exact script: "[full closing CTA]"
- Duration: [seconds]
- Next video suggestion: [what to recommend]

### 💬 DESCRIPTION CTAs (5)
[5 text CTAs for the description]

### 📌 PINNED COMMENT CTA
[The perfect pinned comment]

### 📊 CTA ANALYTICS
| CTA Type | Expected Conv Rate | Best Placement |
|----------|-------------------|----------------|
[Performance comparison]

### ⚡ CTA PSYCHOLOGY
[5 psychological principles for effective CTAs]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "ctas": ai_result, "goal": goal})



# ══ PHASE 7.25: ANALYTICS INTELLIGENCE & PERFORMANCE TRACKER ════════════════

@app.route("/api/ti/performance_audit", methods=["POST"])
def ti_performance_audit():
    """Phase 7.25 — AI complete channel performance audit."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = data.get("subscribers", "10000")
    avg_views = data.get("avg_views", "2000")
    upload_freq = (data.get("upload_freq") or "weekly").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube analytics expert who audits channel performance.

NICHE: {niche}
SUBSCRIBERS: {subscribers}
AVERAGE VIEWS/VIDEO: {avg_views}
UPLOAD FREQUENCY: {upload_freq}
LANGUAGE: {language}

Create a PERFORMANCE AUDIT in {language}:

### 📊 CHANNEL PERFORMANCE AUDIT

### 🎯 HEALTH SCORE
- Overall score: [1-100]
- Growth rate: [grade A-F]
- Engagement rate: [grade A-F]
- Content quality: [grade A-F]
- SEO optimization: [grade A-F]
- Monetization readiness: [grade A-F]

### 📈 KEY METRICS ANALYSIS
| Metric | Your Value | Benchmark | Status | Action |
|--------|-----------|-----------|--------|--------|
| Views/Sub ratio | {avg_views}/{subscribers} | [benchmark] | [🟢/🟡/🔴] | [action] |
[10 more metrics]

### 🔍 STRENGTHS (5)
[What the channel is doing well]

### ⚠️ WEAKNESSES (5)
[What needs improvement with specific fixes]

### 🚀 90-DAY ACTION PLAN
| Month | Focus | KPI Target | Actions |
|-------|-------|-----------|---------|
[3-month plan]

### 💡 QUICK WINS (5)
[5 changes to make this week for immediate impact]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "audit": ai_result})


@app.route("/api/ti/viral_autopsy", methods=["POST"])
def ti_viral_autopsy():
    """Phase 7.25 — AI video success/failure analysis."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    views = data.get("views", "50000")
    outcome = (data.get("outcome") or "viral").strip()[:30]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube performance analyst who reverse-engineers viral and failed videos.

VIDEO TITLE: "{title}"
VIEWS: {views}
OUTCOME: {outcome} (viral/moderate/flop)
NICHE: {niche or 'General'}
LANGUAGE: {language}

Create a VIDEO AUTOPSY in {language}:

### 🔬 VIDEO AUTOPSY: "{title}"

### 📊 PERFORMANCE DIAGNOSIS
- Outcome: {outcome}
- Views vs expected: [{views} vs benchmark]
- CTR estimate: [%]
- Retention estimate: [%]
- Engagement rate: [%]

### 🧬 SUCCESS/FAILURE DNA
**What worked:**
[5 factors with evidence]

**What didn't work:**
[5 factors with evidence]

### 🔄 TITLE ANALYSIS
- Emotional triggers: [present/missing]
- Curiosity gap: [score 1-10]
- Keyword optimization: [score 1-10]
- Click-bait level: [healthy/excessive/missing]
- Improved title: [suggested title]

### 🖼️ THUMBNAIL ANALYSIS
[5 recommendations based on the title]

### 📈 REPLICATION STRATEGY
[How to replicate success / avoid failure in next video]

### 🎯 NEXT VIDEO RECOMMENDATIONS (3)
[3 follow-up videos based on this analysis]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "autopsy": ai_result, "outcome": outcome})


@app.route("/api/ti/benchmark_report", methods=["POST"])
def ti_benchmark_report():
    """Phase 7.25 — AI competitive benchmarking report."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = data.get("subscribers", "10000")
    region = (data.get("region") or "Brazil").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube competitive intelligence analyst.

NICHE: {niche}
YOUR SUBSCRIBERS: {subscribers}
REGION: {region}
LANGUAGE: {language}

Create a BENCHMARK REPORT in {language}:

### 📊 COMPETITIVE BENCHMARK REPORT

### 🏆 NICHE BENCHMARKS
| Metric | Your Channel | Top 10% | Average | Bottom 25% |
|--------|-------------|---------|---------|------------|
| Subscribers | {subscribers} | [value] | [value] | [value] |
[10 more metrics]

### 📈 CHANNEL TIER
- Current tier: [Beginner/Growing/Established/Authority]
- Next tier requirements: [what to achieve]
- Time to next tier: [months]

### 🎯 TOP PERFORMERS IN {niche} (5)
For each:
**[Channel Type]**
- Subscriber range: [range]
- Upload frequency: [frequency]
- Content strategy: [what they do]
- Growth secret: [their advantage]
- What to learn: [actionable takeaway]

### 📊 CONTENT PERFORMANCE BENCHMARKS
| Content Type | Avg Views | Avg CTR | Best Day | Best Duration |
|-------------|-----------|---------|----------|--------------|
[5 content types]

### 🚀 OVERTAKE STRATEGY
[Step-by-step plan to outperform competitors]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "benchmark": ai_result})



# ══ PHASE 7.26: CONTENT REPURPOSE & MULTI-PLATFORM ══════════════════════════

@app.route("/api/ti/repurpose_pro", methods=["POST"])
def ti_repurpose_pro():
    """Phase 7.26 — AI multi-platform content repurposing strategy."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    platforms = data.get("platforms", "instagram,tiktok,twitter,linkedin")
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a multi-platform content strategist who maximizes reach from a single video.

VIDEO TITLE: "{title}"
TARGET PLATFORMS: {platforms}
LANGUAGE: {language}

Create a REPURPOSE STRATEGY in {language}:

### ♻️ MULTI-PLATFORM REPURPOSE PLAN

### 📱 INSTAGRAM (3 content pieces)
**1. Reels (60s)**
- Hook: [first 3 seconds]
- Content: [what to include]
- Caption: [full caption with hashtags]
- Best time: [posting time]

**2. Carousel (10 slides)**
- Slide 1: [cover]
[Slides 2-9: key points]
- Slide 10: [CTA]
- Caption: [full caption]

**3. Stories (5 frames)**
[5 story frames with polls/questions]

### 🎵 TIKTOK (2 content pieces)
[2 TikTok adaptations with hooks and trends]

### 🐦 TWITTER/X (5 tweets)
[5-tweet thread + standalone viral tweet]

### 💼 LINKEDIN (1 post)
[Professional adaptation]

### 📌 PINTEREST (2 pins)
[2 pin ideas with descriptions]

### 📊 REPURPOSE CALENDAR
| Day | Platform | Content | Format |
|-----|----------|---------|--------|
[7-day posting schedule]

### ⚡ ADAPTATION TIPS
[5 rules for platform-specific optimization]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "plan": ai_result})


@app.route("/api/ti/social_captions", methods=["POST"])
def ti_social_captions():
    """Phase 7.26 — AI social media caption generator."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    platform = (data.get("platform") or "instagram").strip()[:30]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a social media copywriting expert who creates viral captions.

VIDEO TITLE: "{title}"
PLATFORM: {platform}
LANGUAGE: {language}

Create 10 CAPTIONS for {platform} in {language}:

### ✍️ {platform.upper()} CAPTIONS

For each caption:
**Caption [1-10]:**
"[Full caption text optimized for {platform}]"
- Style: [informative/emotional/controversial/funny/inspirational]
- Hashtags: [10 relevant hashtags]
- Emoji usage: [strategic placement]
- CTA: [call to action]
- Expected engagement: [low/medium/high/viral]

### 📊 CAPTION BEST PRACTICES FOR {platform.upper()}
- Ideal length: [characters]
- Hashtag limit: [number]
- Best posting times: [times]
- Hook formula: [pattern]
- Engagement triggers: [5 triggers]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "captions": ai_result, "platform": platform})


@app.route("/api/ti/blog_converter", methods=["POST"])
def ti_blog_converter():
    """Phase 7.26 — AI video-to-blog post converter."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    key_points = (data.get("key_points") or "").strip()[:500]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a content writer who converts YouTube videos into SEO-optimized blog posts.

VIDEO TITLE: "{title}"
KEY POINTS: {key_points or 'Extract from title context'}
LANGUAGE: {language}

Convert to a BLOG POST in {language}:

### 📝 BLOG POST

**Title:** [SEO-optimized blog title]
**Meta Description:** [155 chars max]
**URL Slug:** [seo-friendly-slug]

---

## Introduction
[3-4 paragraphs, hook + context + preview]

## [Section 1 Title]
[3-4 paragraphs with subheadings]

## [Section 2 Title]
[3-4 paragraphs with subheadings]

## [Section 3 Title]
[3-4 paragraphs with subheadings]

## [Section 4 Title]
[3-4 paragraphs with subheadings]

## Conclusion
[Summary + CTA to watch the video]

---

### 📊 SEO OPTIMIZATION
- Primary keyword: [keyword]
- Secondary keywords: [5 keywords]
- Internal links: [3 suggested topics]
- External links: [3 authority sources]
- Word count: [target]
- Reading time: [minutes]

### 🔗 CROSS-PROMOTION
[How to link blog and video for maximum SEO benefit]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "blog": ai_result})


# ══ PHASE 7.27: COMMUNITY & ENGAGEMENT PRO ══════════════════════════════════

@app.route("/api/ti/community_strategy", methods=["POST"])
def ti_community_strategy():
    """Phase 7.27 — AI community building strategy."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = data.get("subscribers", "10000")
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube community building expert.

NICHE: {niche}
SUBSCRIBERS: {subscribers}
LANGUAGE: {language}

Create a COMMUNITY STRATEGY in {language}:

### 🤝 COMMUNITY BUILDING STRATEGY

### 📊 COMMUNITY HEALTH ASSESSMENT
| Metric | Current Estimate | Target | Action |
|--------|-----------------|--------|--------|
[8 community metrics]

### 💬 COMMENT ENGAGEMENT (5 strategies)
For each:
**[Strategy Name]**
- What: [description]
- When: [timing]
- Template: "[exact response template]"
- Impact: [expected result]

### 📌 COMMUNITY POSTS (10)
For each:
**Post [1-10]:**
- Type: [poll/question/image/text/behind-the-scenes]
- Content: "[full post text]"
- Best time: [posting time]
- Expected engagement: [rate]

### 🏆 LOYALTY PROGRAM
[How to create superfans with members, badges, shoutouts]

### 📈 30-DAY ENGAGEMENT PLAN
| Week | Focus | Actions | KPI |
|------|-------|---------|-----|
[4-week plan]

### ⚡ QUICK ENGAGEMENT WINS (5)
[5 things to do TODAY]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "strategy": ai_result})


@app.route("/api/ti/comment_templates", methods=["POST"])
def ti_comment_templates():
    """Phase 7.27 — AI comment response templates."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    tone = (data.get("tone") or "friendly").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube comment engagement specialist.

NICHE: {niche}
TONE: {tone}
LANGUAGE: {language}

Create COMMENT RESPONSE TEMPLATES in {language}:

### 💬 COMMENT TEMPLATES

### 😊 POSITIVE COMMENTS (5 templates)
**Scenario:** [type of positive comment]
**Response:** "[exact response]"
**Why:** [engagement psychology]

### 🤔 QUESTIONS (5 templates)
**Scenario:** [type of question]
**Response:** "[exact response that drives engagement]"

### 😠 NEGATIVE/HATE (5 templates)
**Scenario:** [type of negative comment]
**Response:** "[professional response]"
**Strategy:** [de-escalation technique]

### 🎯 ENGAGEMENT BOOSTERS (5 templates)
**Trigger:** [what prompts this]
**Response:** "[response that encourages more comments]"

### 📌 PINNED COMMENTS (5)
[5 pinned comment templates that boost engagement]

### 📊 COMMENT METRICS
| Response Time | Impact on Algorithm | Best Practice |
|--------------|-------------------|--------------|
[5 data-driven insights]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "templates": ai_result, "tone": tone})


@app.route("/api/ti/poll_generator", methods=["POST"])
def ti_poll_generator():
    """Phase 7.27 — AI YouTube poll and community post generator."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    topic = (data.get("topic") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube community post and poll specialist.

NICHE: {niche}
TOPIC: {topic or 'General'}
LANGUAGE: {language}

Create POLLS & COMMUNITY POSTS in {language}:

### 📊 YOUTUBE POLLS (10)
For each:
**Poll [1-10]:**
- Question: "[poll question]"
- Option A: "[option]"
- Option B: "[option]"
- Option C: "[option]" (optional)
- Option D: "[option]" (optional)
- Strategy: [why this poll drives engagement]
- Best timing: [when to post]

### 📝 COMMUNITY POSTS (5)
For each:
**Post [1-5]:**
- Type: [text/image/gif/poll]
- Content: "[full post text]"
- CTA: [what action to drive]
- Expected engagement: [rate]

### 📈 POSTING SCHEDULE
| Day | Time | Post Type | Topic |
|-----|------|-----------|-------|
[7-day schedule]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "polls": ai_result})



# ══ PHASE 7.28: GROWTH HACKING & VIRAL LAB ══════════════════════════════════

@app.route("/api/ti/growth_hacks", methods=["POST"])
def ti_growth_hacks():
    """Phase 7.28 — AI growth hacking strategies."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = data.get("subscribers", "10000")
    goal = (data.get("goal") or "100000 subscribers").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube growth hacking genius who has grown channels from 0 to 1M.

NICHE: {niche}
CURRENT SUBSCRIBERS: {subscribers}
GROWTH GOAL: {goal}
LANGUAGE: {language}

Create GROWTH HACKING STRATEGIES in {language}:

### 🚀 GROWTH HACKING PLAYBOOK

### 📊 CURRENT STATE ANALYSIS
| Metric | Current | Target | Gap | Strategy |
|--------|---------|--------|-----|----------|
[8 growth metrics]

### ⚡ TOP 10 GROWTH HACKS
For each:
**[Number]. [Hack Name]**
- Difficulty: [🟢 Easy / 🟡 Medium / 🔴 Hard]
- Time to results: [days/weeks]
- Expected impact: [subscriber gain]
- Step-by-step: [detailed implementation]
- Example: [real-world case]

### 🔥 VIRAL TRIGGERS (5)
[5 psychological triggers that make content go viral]

### 📈 WEEK-BY-WEEK GROWTH PLAN (12 weeks)
| Week | Focus | Action | Target Subs |
|------|-------|--------|------------|
[12-week plan to reach goal]

### 💡 UNCONVENTIONAL TACTICS (5)
[5 tactics most creators don't know about]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "hacks": ai_result})


@app.route("/api/ti/viral_formula", methods=["POST"])
def ti_viral_formula():
    """Phase 7.28 — AI viral content formula."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    content_type = (data.get("content_type") or "educational").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a viral content scientist who studies what makes videos explode.

NICHE: {niche}
CONTENT TYPE: {content_type}
LANGUAGE: {language}

Create a VIRAL FORMULA in {language}:

### 🧪 VIRAL FORMULA

### 🔬 THE VIRAL EQUATION
[Visual formula: Element A + Element B + Element C = VIRAL]

### 🎯 VIRAL ELEMENTS (10)
For each:
**[Element Name]**
- What: [description]
- Why it works: [psychology]
- How to implement: [steps]
- Example: [viral video that used this]
- Score multiplier: [1.5x - 5x]

### 📊 VIRAL SCORE CALCULATOR
| Element | Weight | Present? | Score |
|---------|--------|----------|-------|
[10 elements with scoring]
Total: [score/100]

### 🎬 10 VIRAL VIDEO IDEAS FOR {niche}
For each:
**[Number]. "[Title]"**
- Viral score: [1-100]
- Hook: [first 5 seconds]
- Why it would go viral: [reason]

### ⚡ VIRAL TIMING
[Best posting times, trends to ride, seasonal opportunities]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "formula": ai_result})


@app.route("/api/ti/collab_matchmaker", methods=["POST"])
def ti_collab_matchmaker():
    """Phase 7.28 — AI collaboration matching strategy."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = data.get("subscribers", "10000")
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube collaboration strategist.

NICHE: {niche}
SUBSCRIBERS: {subscribers}
LANGUAGE: {language}

Create a COLLAB STRATEGY in {language}:

### 🤝 COLLABORATION PLAYBOOK

### 🎯 IDEAL COLLAB PROFILES (5)
For each:
**Profile [1-5]:**
- Channel type: [description]
- Subscriber range: [ideal range for your size]
- Content style: [compatible styles]
- Approach strategy: [how to reach out]
- Collab format: [interview/challenge/reaction/etc]

### 📧 OUTREACH TEMPLATES (3)
**Template [1-3]:**
- Subject: "[email subject]"
- Body: "[full email/DM text]"
- Follow-up: "[follow-up message after 7 days]"

### 🎬 COLLAB VIDEO IDEAS (10)
For each:
**[Number]. "[Title]"**
- Format: [type]
- Both channels benefit: [how]
- Expected views: [range]

### 📊 COLLAB ROI CALCULATOR
| Collab Type | Effort | Expected Subs | Views | Worth It? |
|------------|--------|--------------|-------|-----------|
[5 collab types compared]

### ⚠️ COLLAB RED FLAGS (5)
[5 warning signs of bad collaborations]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "collab": ai_result})



# ══ PHASE 7.29: THUMBNAIL PRO & VISUAL STRATEGY ═════════════════════════════

@app.route("/api/ti/thumbnail_strategy", methods=["POST"])
def ti_thumbnail_strategy():
    """Phase 7.29 — AI thumbnail blueprint."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    niche = (data.get("niche") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube thumbnail design expert with deep CTR optimization knowledge.

VIDEO TITLE: "{title}"
NICHE: {niche or 'General'}
LANGUAGE: {language}

Create a THUMBNAIL BLUEPRINT in {language}:

### 🖼️ THUMBNAIL BLUEPRINT

### 🎨 PRIMARY DESIGN (recommended)
- Layout: [composition description]
- Background: [color/gradient/image]
- Text overlay: "[2-4 words max]" in [font/style/color]
- Face expression: [emotion to convey]
- Focal point: [where eye goes first]
- Color palette: [3-4 hex colors]
- Contrast level: [high/medium]

### 🔄 VARIANT DESIGNS (3)
For each:
**Variant [1-3]:**
[Full design description with different approach]

### 📊 CTR OPTIMIZATION
| Element | Impact on CTR | Your Design | Score |
|---------|--------------|-------------|-------|
| Text readability | High | [score] | [1-10] |
| Emotional trigger | High | [score] | [1-10] |
[8 more elements]
**Total CTR Score: [/100]**

### ⚠️ THUMBNAIL MISTAKES TO AVOID (5)
[5 common mistakes with fixes]

### 📱 MOBILE OPTIMIZATION
[How to ensure thumbnail looks good at 120px]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "blueprint": ai_result})


@app.route("/api/ti/ab_test_thumbs", methods=["POST"])
def ti_ab_test_thumbs():
    """Phase 7.29 — AI A/B thumbnail test variants."""
    data = request.json or {}
    title = (data.get("title") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are a YouTube A/B testing specialist for thumbnails.

VIDEO TITLE: "{title}"
LANGUAGE: {language}

Create 5 A/B TEST VARIANTS in {language}:

### 🧪 A/B THUMBNAIL TEST

For each variant:
**Variant [A-E]:**
- Design concept: [description]
- Text: "[overlay text]"
- Colors: [palette]
- Emotion: [facial expression/vibe]
- Target audience: [who this appeals to]
- Expected CTR: [%]
- Why test this: [hypothesis]

### 📊 TESTING FRAMEWORK
| Variant | Hypothesis | Success Metric | Min Duration |
|---------|-----------|---------------|-------------|
[5 variants]

### 📈 HOW TO RUN THE TEST
1. [Step-by-step A/B testing guide]
2. [When to declare winner]
3. [Statistical significance rules]

### 🏆 WINNING PATTERNS
[5 thumbnail patterns that consistently win A/B tests]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"title": title, "variants": ai_result})


@app.route("/api/ti/visual_branding", methods=["POST"])
def ti_visual_branding():
    """Phase 7.29 — AI visual identity guide for YouTube."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    style = (data.get("style") or "modern").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a visual branding expert for YouTube channels.

NICHE: {niche}
STYLE: {style}
LANGUAGE: {language}

Create a VISUAL BRANDING GUIDE in {language}:

### 🎨 VISUAL BRANDING GUIDE

### 🎨 COLOR SYSTEM
- Primary: [hex + name]
- Secondary: [hex + name]
- Accent: [hex + name]
- Background: [hex + name]
- Text: [hex + name]
- Gradient: [from -> to]

### 📝 TYPOGRAPHY
- Title font: [font name + weight]
- Body font: [font name + weight]
- Accent font: [font name + weight]
- Size hierarchy: [sizes]

### 🖼️ THUMBNAIL TEMPLATE
[Standard layout that creates visual consistency]

### 📐 CHANNEL ART SPECS
- Banner: [design description]
- Profile pic: [design concept]
- Watermark: [design concept]
- End screen: [layout]

### 📊 CONSISTENCY RULES (10)
[10 rules to maintain visual consistency across all content]

### 🎯 BRAND RECOGNITION
[How to make your channel instantly recognizable]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "branding": ai_result})



# ══ PHASE 7.30: SPONSORSHIP & PR PRO ════════════════════════════════════════

@app.route("/api/ti/sponsor_pitch", methods=["POST"])
def ti_sponsor_pitch():
    """Phase 7.30 — AI Sponsor Pitch Deck generator."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    brand = (data.get("brand") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not niche or not brand:
        return jsonify({"error": "Niche and Brand required"}), 400
    prompt = f"""You are a top-tier YouTube talent manager who secures 6-figure sponsorships.

NICHE: {niche}
TARGET BRAND: {brand}
LANGUAGE: {language}

Create a SPONSOR PITCH STRATEGY in {language}:

### 🎯 SPONSOR PITCH: {brand}

### 💡 THE ANGLE
[Why {brand} NEEDS to sponsor this specific channel right now]

### 🤝 ALIGNMENT
- Brand Values: [what the brand cares about]
- Audience Overlap: [why your audience buys their products]
- Unique Selling Proposition: [what you offer that others don't]

### 🎬 INTEGRATION IDEAS (3)
For each:
**Idea [1-3]:**
- Concept: [how to integrate the product]
- Hook: [first 5 seconds of the integration]
- CTA: [how to get viewers to click the link/use code]

### 💰 PRICING STRATEGY
| Deliverable | Value Prop | Suggested Tier |
|-------------|------------|----------------|
| Dedicated Video | [Why it works] | Premium |
| 60s Integration | [Why it works] | Standard |
| Shorts/Reels | [Why it works] | Add-on |

### 📧 PITCH EMAIL
**Subject:** "[High open-rate subject line]"
**Body:**
"[Professional, concise email pitching the integration]"

### 🛡️ OBJECTION HANDLING (3)
[How to respond if the brand says: too expensive, no budget, wrong fit]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"brand": brand, "pitch": ai_result})


@app.route("/api/ti/media_kit", methods=["POST"])
def ti_media_kit():
    """Phase 7.30 — AI Media Kit generator."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = data.get("subscribers", "10000")
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a PR expert designing YouTube media kits.

NICHE: {niche}
SUBSCRIBERS: {subscribers}
LANGUAGE: {language}

Create the text content for a PROFESSIONAL MEDIA KIT in {language}:

### 📊 PROFESSIONAL MEDIA KIT COPY

### 👤 ABOUT THE CREATOR
[Professional bio template to fill in (3-4 sentences)]

### 🎯 THE AUDIENCE (Demographics to highlight)
- Age range focus: [ideal range]
- Gender split: [typical for {niche}]
- Top Geographies: [typical for {niche}]
- Audience Persona: [describe the typical viewer]

### 📈 KEY METRICS TO SHOWCASE
| Metric | Why it matters to sponsors |
|--------|----------------------------|
| Avg Views/Video | Predictable reach |
| Click-Through Rate | High intent audience |
| Watch Time | Deep engagement |
| Conversion Rate | ROI potential |

### ⭐ PAST WORK / CASE STUDIES (Format)
[How to present 2 past successful sponsorships]

### 💼 SERVICES OFFERED
- [Service 1 + Description]
- [Service 2 + Description]
- [Service 3 + Description]
- [Service 4 + Description]

### ✉️ CONTACT INFO SECTION
[Professional sign-off and CTA for brands]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "media_kit": ai_result})


@app.route("/api/ti/brand_outreach", methods=["POST"])
def ti_brand_outreach():
    """Phase 7.30 — AI Brand Outreach Strategy."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    tier = (data.get("tier") or "mid-tier").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a B2B sales expert specializing in influencer marketing.

NICHE: {niche}
BRAND TIER TO TARGET: {tier}
LANGUAGE: {language}

Create a BRAND OUTREACH STRATEGY in {language}:

### 🤝 BRAND OUTREACH STRATEGY

### 🎯 TARGET LIST BUILDING
[How to find the right {tier} brands in {niche}]
- Where to look: [platforms/tools]
- Who to contact: [job titles to search for on LinkedIn]

### 📧 COLD OUTREACH SEQUENCE
**Email 1: The Hook**
[Template]

**Email 2: The Value Add (Day 4)**
[Template]

**Email 3: The Breakup (Day 10)**
[Template]

### 💬 LINKEDIN DM STRATEGY
[How to network with influencer marketing managers on LinkedIn]
**DM Template:** "[professional DM]"

### 📊 TRACKING PIPELINE
| Stage | Actions Required | Conversion Goal |
|-------|------------------|-----------------|
| Lead Gen | [Actions] | [%] |
| Outreach | [Actions] | [%] |
| Discovery | [Actions] | [%] |
| Proposal | [Actions] | [%] |

### 🔥 NEGOTIATION TACTICS (5)
[5 ways to increase the deal size or secure long-term contracts]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "outreach": ai_result})


# ══ PHASE 7.31: COURSE & PRODUCT LAUNCH PRO ═════════════════════════════════

@app.route("/api/ti/course_blueprint", methods=["POST"])
def ti_course_blueprint():
    """Phase 7.31 — AI Course & Info-product Blueprint."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    topic = (data.get("topic") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not niche or not topic:
        return jsonify({"error": "Niche and Topic required"}), 400
    prompt = f"""You are an expert digital product creator and instructional designer.

NICHE: {niche}
COURSE TOPIC: {topic}
LANGUAGE: {language}

Create a COURSE BLUEPRINT in {language}:

### 📚 COURSE BLUEPRINT: {topic}

### 🎯 THE TRANSFORMATION
- From: [Current pain state of the student]
- To: [Desired dream state of the student]
- The Bridge: [How your course gets them there]

### 📝 COURSE CURRICULUM (5 Modules)
For each module:
**Module [1-5]: [Module Title]**
- Lesson 1: [Topic] - [Key takeaway]
- Lesson 2: [Topic] - [Key takeaway]
- Lesson 3: [Topic] - [Key takeaway]
- Action Item: [Homework/Task for the student]

### 🎁 BONUSES TO INCREASE VALUE (3)
[3 high-value bonuses that overcome objections]

### 💰 PRICING STRATEGY
| Tier | Price | What's Included | Target Audience |
|------|-------|-----------------|-----------------|
| Basic | [$] | [Features] | [Who it's for] |
| Pro | [$] | [Features] | [Who it's for] |
| VIP | [$] | [Features + Coaching] | [Who it's for] |

### 🛠️ DELIVERY METHOD
[Best platforms to host this (e.g. Kajabi, Hotmart, Skool) and format (video, text, cohort)]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"topic": topic, "blueprint": ai_result})


@app.route("/api/ti/launch_funnel", methods=["POST"])
def ti_launch_funnel():
    """Phase 7.31 — AI Launch Funnel Strategy."""
    data = request.json or {}
    product = (data.get("product") or "").strip()[:100]
    duration = (data.get("duration") or "14 days").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not product:
        return jsonify({"error": "Product name required"}), 400
    prompt = f"""You are an elite digital marketing funnel strategist.

PRODUCT: {product}
LAUNCH DURATION: {duration}
LANGUAGE: {language}

Create a LAUNCH FUNNEL STRATEGY in {language}:

### 🚀 LAUNCH FUNNEL: {product}

### 📅 PRE-LAUNCH PHASE (Build Hype)
- Content Strategy: [3 types of videos to post before launch]
- Lead Magnet: [Idea to collect emails]
- Urgency Trigger: [Why they should join the waitlist]

### 📧 OPEN CART EMAIL SEQUENCE
**Email 1: The Announcement (Day 1)**
- Subject: "[Subject]"
- Angle: [The big reveal and transformation]

**Email 2: Logical Argument (Day 2)**
- Subject: "[Subject]"
- Angle: [Features, benefits, ROI]

**Email 3: Social Proof (Day 3)**
- Subject: "[Subject]"
- Angle: [Testimonials, case studies]

**Email 4: Objection Crusher (Day X)**
- Subject: "[Subject]"
- Angle: [FAQ, risk reversal/guarantee]

**Email 5: Final Warning (Last Day)**
- Subject: "[Subject]"
- Angle: [Scarcity, FOMO, last chance]

### 🎬 YOUTUBE PROMO INTEGRATIONS (3)
[How to naturally pitch the product in your regular YouTube videos during launch]

### 🛡️ SCARCITY TACTICS
[Ethical ways to create urgency (limited seats, price increase, expiring bonuses)]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"product": product, "funnel": ai_result})


@app.route("/api/ti/sales_copy", methods=["POST"])
def ti_sales_copy():
    """Phase 7.31 — AI Sales Page Copywriter."""
    data = request.json or {}
    product = (data.get("product") or "").strip()[:100]
    target_audience = (data.get("target_audience") or "").strip()[:100]
    language = data.get("language", "Portuguese")[:50]
    if not product:
        return jsonify({"error": "Product name required"}), 400
    prompt = f"""You are a world-class direct response copywriter.

PRODUCT: {product}
TARGET AUDIENCE: {target_audience or 'General'}
LANGUAGE: {language}

Create HIGH-CONVERTING SALES PAGE COPY in {language}:

### ✍️ SALES PAGE COPY: {product}

### 🪝 THE HEADLINE
[Main headline that grabs attention and states the big promise]
**Sub-headline:** [Elaborates on the promise and handles the biggest objection]

### 😫 THE PAIN (Agitation)
"Are you tired of [pain 1], [pain 2], and [pain 3]?"
[Paragraph explaining you understand their struggle]

### 🌟 THE SOLUTION (Introduction)
"Introducing {product}..."
[Paragraph on how this changes everything]

### ✨ BENEFITS (Not just features)
- Feature 1: [What it is] ➔ **Benefit:** [What it does for them]
- Feature 2: [What it is] ➔ **Benefit:** [What it does for them]
- Feature 3: [What it is] ➔ **Benefit:** [What it does for them]

### 🚫 WHO THIS IS NOT FOR
[Polarizing section to qualify buyers]

### 🛡️ THE GUARANTEE
[Risk-reversal guarantee copy (e.g., 30-day money back)]

### 🔥 CALL TO ACTION (CTA)
[Strong button text]
[Urgency/Scarcity text below button]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"product": product, "copy": ai_result})


# ══ PHASE 7.32: MASTER AI DIRECTOR (THE FINALE) ═════════════════════════════

@app.route("/api/ti/channel_audit", methods=["POST"])
def ti_channel_audit():
    """Phase 7.32 — Full 360 AI Channel Audit."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    channel_url = (data.get("channel_url") or "").strip()[:200]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube Executive Producer and Channel Auditor.

NICHE: {niche}
CHANNEL URL/HANDLE: {channel_url or 'N/A'}
LANGUAGE: {language}

Create a MASTER 360 CHANNEL AUDIT in {language}:

### 📋 360 MASTER CHANNEL AUDIT

### 🎯 BRAND IDENTITY SCORE
- Niche clarity: [1-10] + [Why]
- Value proposition: [What they are offering]
- Target audience: [Who they are reaching vs who they SHOULD reach]

### 🖼️ PACKAGING (Thumbnails & Titles)
| Element | Current State | How to Improve |
|---------|---------------|----------------|
| Thumbnails | [Analysis] | [Action] |
| Titles | [Analysis] | [Action] |
| Consistency | [Analysis] | [Action] |

### 🎬 CONTENT STRATEGY
- **Keep doing:** [What works in this niche]
- **Stop doing:** [Common mistakes in this niche]
- **Start doing:** [Untapped opportunities]

### ⚡ RETENTION & PACING
[3 specific editing and pacing techniques to increase AVD (Average View Duration) for {niche}]

### 💰 MONETIZATION GAPS
[Where they are leaving money on the table]

### 🚀 90-DAY ACTION PLAN
1. Days 1-30: [Focus]
2. Days 31-60: [Focus]
3. Days 61-90: [Focus]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "audit": ai_result})


@app.route("/api/ti/master_calendar", methods=["POST"])
def ti_master_content_calendar():
    """Phase 7.32 — AI 30-Day Master Calendar."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    frequency = (data.get("frequency") or "2 videos per week").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a YouTube Content Strategist.

NICHE: {niche}
POSTING FREQUENCY: {frequency}
LANGUAGE: {language}

Create a 30-DAY MASTER CONTENT CALENDAR in {language}:

### 📅 30-DAY MASTER CONTENT CALENDAR

### 🎯 STRATEGIC PILLARS
1. Discoverability (Search/SEO) - [Goal]
2. Community (Connection) - [Goal]
3. Authority (Deep dives) - [Goal]
4. Virality (Broad appeal) - [Goal]

### 📆 THE CALENDAR
Provide the schedule based on {frequency}:

| Week | Pillar | Title/Concept | Format (Long/Short) | Goal |
|------|--------|---------------|---------------------|------|
[Generate the full month schedule balancing the 4 pillars]

### 💡 BATCHING STRATEGY
[How to script, film, and edit this entire month in the least amount of days]

### 🔄 REPURPOSING WORKFLOW
[How to turn these specific videos into Shorts, Tweets, and LinkedIn posts]

### ⚠️ AVOID BURNOUT
[3 tips to maintain this schedule without burning out]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "calendar": ai_result})


@app.route("/api/ti/monetization_roadmap", methods=["POST"])
def ti_monetization_roadmap():
    """Phase 7.32 — AI Monetization Roadmap."""
    data = request.json or {}
    niche = (data.get("niche") or "").strip()[:100]
    subscribers = (data.get("subscribers") or "10000").strip()[:50]
    language = data.get("language", "Portuguese")[:50]
    if not niche:
        return jsonify({"error": "Niche required"}), 400
    prompt = f"""You are a Creator Economy Business Advisor.

NICHE: {niche}
CURRENT SUBSCRIBERS: {subscribers}
LANGUAGE: {language}

Create a MULTI-STREAM MONETIZATION ROADMAP in {language}:

### 💸 MASTER MONETIZATION ROADMAP

### 📊 CURRENT POTENTIAL ANALYSIS
With {subscribers} subs in {niche}, here is your estimated earning potential:
- AdSense: [Estimate based on RPM]
- Sponsorships: [Estimate per integration]
- Affiliates: [Estimate]

### 💰 REVENUE STREAMS TO ACTIVATE
| Stream | Effort | Income Potential | How to Start TODAY |
|--------|--------|------------------|--------------------|
| Affiliates | Low | Medium | [Action] |
| Brand Deals | Medium | High | [Action] |
| Digital Product | High | Very High | [Action] |
| Community/Members | High | Medium/Recurring | [Action] |

### 🚀 YOUR FIRST $10K PLAN
[Step-by-step math and strategy to hit $10,000/month in {niche}]
- [Product/Service] x [Price] x [Number of buyers] = $10k

### 💼 B2B VS B2C BALANCE
[How to balance selling to consumers vs selling to businesses/brands]

### 🛑 BIGGEST MONEY MISTAKES
[3 ways creators in {niche} lose money without realizing it]"""
    ai_result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"niche": niche, "roadmap": ai_result})


# ══ PHASE 8: AUTO-EDITOR ENGINE ═══════════════════════════════════════════

@app.route("/api/ae/render", methods=["POST"])
def ae_render_video():
    """Phase 8 — Auto-Editor: Text -> TTS -> Veo -> FFmpeg -> Final Video."""
    data = request.json or {}
    script_text = (data.get("script") or "").strip()
    voice = data.get("voice", "pt-BR-AntonioNeural")
    aspect_ratio = data.get("aspect_ratio", "16:9")
    broll_prompt = (data.get("broll_prompt") or "cinematic pan, beautiful lighting").strip()
    
    if not script_text:
        return jsonify({"error": "Script required"}), 400

    try:
        from core.voice_engine import VoiceEngine
        from core.veo_director import VeoDirector
        from core.ffmpeg_renderer import FFmpegRenderer
        import tempfile
        
        # Temp dir for this job
        job_dir = tempfile.mkdtemp(prefix="sp_auto_")
        
        # 1. Voice Engine
        ve = VoiceEngine()
        audio_path = os.path.join(job_dir, "voice.mp3")
        ve.generate_audio(script_text, voice=voice, output_path=audio_path)
        
        # 2. Veo Director (Optional or Mocked for testing if cookie is missing)
        try:
            vd = VeoDirector()
            print("[AE] Requesting Veo clip...")
            # For this MVP, we just trigger it. In a real scenario we might wait and download.
            vd.trigger_generation(broll_prompt, aspect_ratio=aspect_ratio, duration="8s")
            # To avoid actual 5-min hangs during UI testing, we'll use a placeholder video if it doesn't download.
            video_clips = []
        except Exception as e:
            print(f"[AE] Veo Director skipped: {e}")
            video_clips = []
            
        # If no clips from Veo, we'll create a blank clip using ffmpeg as fallback for the UI to show something
        if not video_clips:
            blank_mp4 = os.path.join(job_dir, "blank.mp4")
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=10", "-c:v", "libx264", blank_mp4], capture_output=True)
            video_clips.append(blank_mp4)
            
        # 3. Master Renderer
        ff = FFmpegRenderer()
        final_mp4 = os.path.join(job_dir, "final_video.mp4")
        ff.render_final_video(audio_path, video_clips, final_mp4)
        
        # Copy to static so UI can load it
        static_out = os.path.join(app.static_folder, "final_video.mp4")
        import shutil
        shutil.copy2(final_mp4, static_out)
        
        return jsonify({
            "status": "success",
            "video_url": "/static/final_video.mp4",
            "audio_track": audio_path,
            "clips_used": len(video_clips)
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ══ PHASE 9: MASTER ORCHESTRATOR ══════════════════════════════════════════

@app.route("/api/pipeline/start", methods=["POST"])
def pipeline_start():
    """Phase 9 — The Final Automator (Title -> Script -> Voice -> Veo -> Final)."""
    data = request.json or {}
    topic = data.get("topic", "").strip()
    niche = data.get("niche", "General").strip()
    language = data.get("language", "Portuguese")
    voice = data.get("voice", "pt-BR-AntonioNeural")
    aspect = data.get("aspect_ratio", "16:9")
    
    if not topic:
        return jsonify({"error": "Topic is required"}), 400
        
    try:
        from core.orchestrator import PipelineOrchestrator
        orch = PipelineOrchestrator(_ask_gemini_gcg)
        
        # Run the full pipeline
        result = orch.run_full_pipeline(
            topic=topic,
            niche=niche,
            language=language,
            voice=voice,
            aspect_ratio=aspect
        )
        
        # Copy output to static dir for browser playback
        final_mp4 = result["video_path"]
        static_out = os.path.join(app.static_folder, "pipeline_video.mp4")
        import shutil
        shutil.copy2(final_mp4, static_out)
        
        return jsonify({
            "status": "success",
            "video_url": "/static/pipeline_video.mp4",
            "script": result["script"],
            "job_dir": result["job_dir"]
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ══ PHASE 11: TITLE STRATEGY PRO ENDPOINTS ══════════════════════════

@app.route("/api/ti/abtest", methods=["POST"])
def api_ti_abtest():
    data = request.json or {}
    # Aceita tanto array 'titles' quanto 'title_a'/'title_b'
    titles = data.get("titles", [])
    if not titles:
        title_a = data.get("title_a", "").strip()
        title_b = data.get("title_b", "").strip()
        if title_a and title_b:
            titles = [title_a, title_b]
    if len(titles) < 2:
        return jsonify({"error": "Forneça pelo menos 2 títulos para a batalha."}), 400
    niche = data.get("niche", "YouTube")
    titles_text = "\n".join([f"Opção {i+1}: {t}" for i, t in enumerate(titles)])
    prompt = f"""Você é o Juiz Definitivo de Algoritmo do YouTube especialista em psicologia de CTR.
Nicho do Canal: {niche}

Títulos em Batalha A/B:
{titles_text}

Sua missão:
1. 🏆 Declare o VENCEDOR com justificativa baseada em psicologia (curiosidade, urgência, dor, ganho).
2. 📊 Score de CTR Estimado para cada título (0-100).
3. ❌ Por que o perdedor falhou (palavras fracas, falta de gancho emocional, muito genérico).
4. 💡 Versão melhorada do vencedor para maximizar ainda mais o CTR.
5. 🎯 Estrutura viral usada no vencedor (curiosidade gap, número, urgência, etc).

Formate com emojis, parágrafos curtos e direto ao ponto."""
    try:
        ans = _ask_gemini_gcg(prompt, "You are a master YouTube CTR strategist.")
        return jsonify({"result": ans})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ti/ctr_score", methods=["POST"])
def api_ti_ctr_score():
    data = request.json or {}
    title = data.get("title", "")
    if not title:
        return jsonify({"error": "Forneça o título."}), 400
        
    prompt = f"""Atue como o Maior Especialista de Retenção e CTR do YouTube Mundial.
Faça a Auditoria Extrema de CTR para este título: "{title}"

Sua análise deve conter EXATAMENTE este formato:
1. Score CTR Estimado: [0 a 100]
2. Fator Curiosidade: [Análise de 1 linha]
3. Clareza & Urgência: [Análise de 1 linha]
4. Veredito: [Ruim / Mediano / Viral / Lendário]
5. Como Melhorar: Forneça 3 versões reescritas deste título que aumentariam o score para 99/100."""
    try:
        ans = _ask_gemini_gcg(prompt, "You are a master YouTube CTR Auditor.")
        return jsonify({"result": ans})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ti/hooks", methods=["POST"])
def api_ti_hooks():
    data = request.json or {}
    niche = data.get("niche", "Geral")
    prompt = f"""Gere um 'Dicionário de Hooks (Ganchos) Virais' para o nicho de {niche}.
Crie 10 fórmulas no estilo Fill-in-the-blank (Preencha as lacunas) que possuem CTR comprovadamente insano.
Exemplo: "O Segredo Sombrio que a [Industria] Tenta Esconder Sobre [Assunto]".
Após listar as 10 fórmulas, dê 1 exemplo prático preenchido para CADA UMA aplicado ao nicho de {niche}.
Formate com espaçamentos claros e marcadores de lista."""
    try:
        ans = _ask_gemini_gcg(prompt, "You are a viral title formulas engineer.")
        return jsonify({"result": ans})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══ PHASE 10: AUTOMATION FARM ══════════════════════════════════════════

farm = None

def init_farm():
    global farm
    try:
        from core.farm_scheduler import FarmScheduler
        farm = FarmScheduler(_ask_gemini_gcg)
        farm.start()
    except Exception as e:
        print(f"[Farm] Init failed: {e}")

@app.route("/api/farm/status", methods=["GET"])
def api_farm_status():
    if not farm: return jsonify({"error": "Farm not initialized"}), 500
    return jsonify(farm.get_status())

@app.route("/api/farm/worker", methods=["POST"])
def api_farm_add_worker():
    if not farm: return jsonify({"error": "Farm not initialized"}), 500
    data = request.json or {}
    name = data.get("name")
    niche = data.get("niche")
    cron_hour = data.get("cron_hour", "18")
    cron_minute = data.get("cron_minute", "0")
    
    if not name or not niche:
        return jsonify({"error": "Name and Niche required"}), 400
        
    worker = farm.add_worker(name, niche, cron_hour, cron_minute)
    return jsonify(worker)


if __name__ == "__main__":
    init_farm()
    print("\n  StudioPilot Pro - Web Edition")
    print("  AI Engine v4: 6 providers, auto-failover + smart cache")
    print("  NexLev Tools: viral_score | title_predictor | content_calendar")
    print("  Phase 2: algorithm_explainer | upload_timing | channel_health")
    print("  Phase 3: tag_generator | description_generator | revenue_calculator | content_gap")
    print("  Phase 4: strategy_advisor | competitor_tracker | growth_predictor")
    print("  Phase 5: hook_generator | viral_blueprint | thumbnail_strategist | shorts_optimizer")
    print("  Phase 6: script_writer | keyword_research | audience_builder | comment_analyzer")
    print("  Phase 7: auto_pilot")
    print("  http://localhost:5051\n")
    app.run(host="0.0.0.0", port=5051, debug=False, threaded=True)

