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
    data = request.json
    topic = data.get("topic","")
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
            raise Exception(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
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
    data = request.json
    topic = data.get("topic", "")
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
    return jsonify({"ok": True, "item": item})

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
    channel_id = data.get("id", "")
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


if __name__ == "__main__":
    print("\n  StudioPilot Pro - Web Edition")
    print("  AI Engine v3: 5 providers, auto-failover")
    print("  http://localhost:5051\n")
    app.run(host="0.0.0.0", port=5051, debug=False, threaded=True)

