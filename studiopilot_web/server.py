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
        "google_api_key": load_api_key("google_ai") or "",
        "pexels_api_key": load_api_key("pexels") or "",
        "pixabay_api_key": load_api_key("pixabay") or "",
        "unsplash_api_key": load_api_key("unsplash") or "",
        "youtube_api_key": load_api_key("youtube") or "",
        "youtube_channel_ids": data.get("youtube_channel_ids", load_api_key("youtube_channels") or ""),
        "auto_broll_count": broll_count,
        "stickers_enabled": data.get("stickers", True),
        "youtube_priority": data.get("youtube_priority", False),
        "reduce_quality": data.get("reduce_quality", False),
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

# ── AI helper (wraps core.ai_engine with image support) ──────────────────
def _ask_gemini_gcg(prompt, user_api_key=None, max_retries=None, image_b64=None):
    """Unified Gemini call with multi-model failover and optional Vision."""
    try:
        from core.ai_engine import ask_ai as _ask_ai, get_model_status, clear_cooldowns
        from core.api_keys import load_api_key
        key = user_api_key if (user_api_key and len(str(user_api_key)) > 10) else load_api_key("google_ai")
        return _ask_ai(prompt, api_key=key or None, max_retries=max_retries, image_b64=image_b64)
    except Exception as e:
        # Fallback: direct google-genai
        try:
            from core.api_keys import load_api_key
            from google import genai
            k = load_api_key("google_ai")
            c = genai.Client(api_key=k)
            r = c.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            return r.text
        except Exception as e2:
            raise RuntimeError(f"AI unavailable: {e} | {e2}")

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
    prompt = f"""You are the #1 YouTube title engineer. You've studied every 100M+ view viral video.
TOPIC: {topic}
LANGUAGE: {language}
{"NICHE CONTEXT: " + niche if niche else ""}
Generate exactly 15 EXTREMELY viral YouTube titles (70-95 characters each).
MANDATORY: 1-2 ALL CAPS words per title. Emotional triggers. Specific numbers.
EVERY title MUST be 70-100 characters. Titles under 65 chars = FAILURE.
Return each line as: [Title] | Thumbnail Concept: [Brief visual idea]
No explanations. Verify each title is 70+ characters."""
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
            # Skip lines that look like headers or error messages (no pipe separator and very long single line)
            if line and len(line) > 20 and not any(m in line.lower() for m in ("temporarily", "rate limit", "quota exceeded", "error occurred")):
                raw_titles.append(line)

    if len(raw_titles) < 5:
        # Retry with a simpler, more explicit prompt to get numbered list
        retry_prompt = f"""Generate exactly 15 viral YouTube titles about: {topic}
Language: {language}

Rules: 70-100 characters each, ALL CAPS 1-2 words, emotional triggers, numbers where natural.
Format strictly as:
1. [Title] | Thumbnail Concept: [Brief idea]
2. [Title] | Thumbnail Concept: [Brief idea]
...continue to 15"""
        result2 = _ask_gemini_gcg(retry_prompt, data.get("ai_api_key"))
        raw_titles = []
        for line in result2.strip().split("\n"):
            line = line.strip()
            if not line: continue
            line = _re.sub(r'^\d+[\.\)\-\s]+', '', line).strip().strip('"\'')
            if line and len(line) > 20 and not any(m in line.lower() for m in ("temporarily", "rate limit", "quota exceeded")):
                raw_titles.append(line)

    titles = []
    for line in raw_titles:
        if len(line) < 30: continue
        parts = line.split("| Thumbnail Concept:")
        actual_title = parts[0].strip()
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
    data = request.json or {}
    title = data.get("title", "")[:300]
    context = data.get("context", "")[:1500]
    language = data.get("language", "English")[:50]
    if not title:
        return jsonify({"error": "Title required"}), 400
    prompt = f"""You are an elite YouTube SEO & Algorithm Expert. Generate ultimate YouTube metadata to trigger algorithmic recommendations.
VIDEO TITLE: "{title}"
VIDEO CONTEXT: "{context}"
TARGET LANGUAGE: {language}
Provide Markdown-formatted:
### 📝 Optimized Description (Above The Fold — 3 Lines)
### 📌 Pinned Comment Strategy
### 🏷️ 15 Keyword Tags (comma-separated, primary keyword first)
### 📅 Best Upload Time & Day (based on niche)
### 📣 Thumbnail A/B Test Suggestion (color psychology)
### 🔗 End Screen CTA Script (15 seconds)"""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"seo": result})

@app.route("/api/ti/channel_strategy", methods=["POST"])
def ti_channel_strategy():
    data = request.json or {}
    channel_type = (data.get("channel_type") or "").strip()[:200]
    if not channel_type:
        return jsonify({"error": "Channel type required"}), 400
    current_titles = data.get("titles", [])
    target_audience = data.get("target_audience", "")[:300]
    language = data.get("language", "English")[:50]
    titles_text = "\n".join(f"- {t}" for t in current_titles[:20]) if current_titles else "No titles provided"
    prompt = f"""You are a master YouTube Channel Architect who has scaled channels from 0 to 1M+ subscribers.
CHANNEL TYPE: {channel_type}
TARGET AUDIENCE: {target_audience}
LANGUAGE: {language}
CURRENT TITLES:\n{titles_text}
Provide a MASTERCLASS strategy (Markdown, extreme specificity):
### 🎯 1. THE BLUE OCEAN POSITIONING (Core Identity, Competitor Gap, Unfair Advantage)
### 🏛️ 2. THE 4 CONTENT PILLARS (each with psychological hook + 2 example titles + thumbnail synergy)
### 💡 3. THE VIRAL PACKAGING FORMULA (best structures, power words, pacing)
### 🧠 4. AUDIENCE PSYCHOGRAPHICS (demographics, burning pain point, retention anchors)
### 📈 5. THE 6-MONTH EXPLOSION ROADMAP (Phase 1/2/3)
### 🏆 6. TOP 10 IMMEDIATE VIDEO IDEAS (70-100 char titles)
Be brutal, highly specific, zero generic advice."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"strategy": result, "channel_type": channel_type})

@app.route("/api/ti/strategy_remix", methods=["POST"])
def ti_strategy_remix():
    data = request.json or {}
    topic = (data.get("topic") or "").strip()[:300]
    if not topic:
        return jsonify({"error": "Topic required"}), 400
    language = data.get("language", "English")[:50]
    path_a = data.get("path_a", "Curiosity & Mystery")[:100]
    path_b = data.get("path_b", "Fear & Consequence")[:100]
    prompt = f"""You are the world's #1 YouTube A/B Testing Strategist and Behavioral Psychologist.
TOPIC: {topic}
LANGUAGE: {language}
Create a "Strategy Remix" comparing two psychological paths:
PATH A: {path_a}
PATH B: {path_b}
For EACH PATH provide (Markdown):
### 🧠 1. THE PSYCHOLOGICAL ANGLE (click trigger + why it works)
### 🖼️ 2. THUMBNAIL SYNERGY RULES (visual subject, lighting/color, text max 3 words)
### ✍️ 3. 5 VIRAL TITLES (70-100 chars, 1-2 ALL CAPS words)
### 🏆 4. THE WINNING SCENARIO (use Path A if... / use Path B if...)"""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    return jsonify({"remix": result, "topic": topic, "path_a": path_a, "path_b": path_b})

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
    prompt = f"""You are a master YouTube Niche Strategist using the Blue Ocean Strategy.
{"MAIN THEME: " + theme if theme else "Analyze ALL trending YouTube themes."}
TARGET LANGUAGE: {language}
Find 8 most PROFITABLE and EXPLOSIVE subniches with MASSIVE demand, ZERO supply, HIGH RPM, CROSS-NICHE appeal.
For each provide: name, demand(1-10), supply(1-10), opportunity(demand-supply), target_audience, audience_pain, content_angle, example_titles(3×70-100chars), keywords(3), estimated_views_per_video.
Return ONLY valid JSON array. No markdown. No explanation."""
    result = _ask_gemini_gcg(prompt, data.get("ai_api_key"))
    try:
        m = _re.search(r'\[.*\]', result, _re.DOTALL)
        niches = json.loads(m.group() if m else result)
        niches.sort(key=lambda x: x.get("opportunity", 0), reverse=True)
        return jsonify({"niches": niches, "theme": theme})
    except:
        return jsonify({"niches": [], "raw": result, "theme": theme})

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


if __name__ == "__main__":
    print("\n  StudioPilot Pro - Web Edition")
    print("  AI Engine v3: 5 providers, auto-failover")
    print("  http://localhost:5051\n")
    app.run(host="0.0.0.0", port=5051, debug=False, threaded=True)

