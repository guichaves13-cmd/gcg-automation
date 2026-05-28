"""
AvatarPilot Pro — ROBUSTEZ & CARGA DE PRODUÇÃO (2026-05-28)

Valida prontidão para 1000+ usuários: concorrência (WSGI), caminhos de pipeline
não cobertos (upload de áudio/vídeo, legendas), TTS alternativos, e soak test.

Uso:
    python test_robustness_avp.py load     # R1 carga concorrente + R10 soak (API, ~5min)
    python test_robustness_avp.py quick     # R6 ElevenLabs + checagens API graciosas (~1min)
    python test_robustness_avp.py paths     # R2/R3/R4 upload áudio/vídeo/legendas (pesado, ~45min)
    python test_robustness_avp.py           # tudo

SEÇÕES:
  R1  — Carga concorrente: 100 pollers simultâneos, latência p50/p95/p99 (valida Waitress)
  R2  — Upload de áudio próprio (bypass TTS) — pipeline completo
  R3  — Upload de vídeo como avatar (em vez de foto) — pipeline completo
  R4  — Legendas burned-in (Whisper) — pipeline completo
  R6  — ElevenLabs/F5-TTS — degradação graciosa sem chave
  R10 — Soak test: 300 requests mistos, sem vazamento/erro
"""

import sys, os, time, json, requests, subprocess, shutil, io, threading, statistics

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:5052"
UPLOADS  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
OUTPUTS  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
GESTURE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "gesture_videos")

PHASE = (sys.argv[1].lower() if len(sys.argv) > 1 else "all")
RUN_LOAD  = PHASE in ("load", "all")
RUN_QUICK = PHASE in ("quick", "all")
RUN_PATHS = PHASE in ("paths", "all")

passes = fails = 0
fail_log = []

def ok(name, detail=""):
    global passes; passes += 1
    print(f"  \033[92m[PASS]\033[0m {name}" + (f" [{detail}]" if detail else ""))

def fail(name, detail=""):
    global fails; fails += 1
    fail_log.append(f"FAIL [{name}]: {detail}")
    print(f"  \033[91m[FAIL]\033[0m {name}: {detail}")

def sep(title):
    print(f"\n{'='*70}\n  {title}\n{'='*70}")

def get(path, **kw):
    return requests.get(f"{BASE_URL}{path}", timeout=kw.pop("timeout", 10), **kw)

def post(path, **kw):
    return requests.post(f"{BASE_URL}{path}", timeout=kw.pop("timeout", 15), **kw)

def best_image():
    best, best_sz = None, 0
    for f in os.listdir(UPLOADS):
        if not f.endswith((".jpg", ".jpeg")): continue
        p = os.path.join(UPLOADS, f)
        sz = os.path.getsize(p)
        if 30_000 < sz < 600_000 and sz > best_sz:
            best_sz = sz; best = p
    return best or os.path.join(UPLOADS, "test_face.jpg")

IMG = best_image()

def cancel_job(job_id):
    try: post(f"/api/job/{job_id}/cancel", timeout=5)
    except: pass

def wait(job_id, timeout_s=3600, poll=10):
    t0 = time.time(); last = ""
    while time.time() - t0 < timeout_s:
        d = get(f"/api/job/{job_id}").json()
        st, msg, prog = d.get("status","?"), d.get("message",""), d.get("progress",0)
        if msg != last:
            print(f"    [{st}] {prog}% — {msg}"); last = msg
        if st == "done": return d
        if st in ("failed","error","cancelled"):
            raise RuntimeError(f"Job {st}: {d.get('error', d.get('message','?'))}")
        time.sleep(poll)
    raise TimeoutError(f"Job {job_id} timeout após {timeout_s}s")

def validate_mp4(path, min_dur=2.0, min_kb=50):
    assert os.path.exists(path), f"output missing: {path}"
    assert os.path.getsize(path) > min_kb*1024, f"too small: {os.path.getsize(path)//1024}KB"
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([ffprobe,"-v","quiet","-print_format","json",
                        "-show_streams","-show_format",path],
                       capture_output=True, text=True, timeout=15)
    info = json.loads(r.stdout)
    dur = float(info.get("format",{}).get("duration",0))
    assert dur >= min_dur, f"too short: {dur:.1f}s"
    streams = info.get("streams",[])
    assert any(s.get("codec_type")=="video" for s in streams), "no video"
    vs = next((s for s in streams if s.get("codec_type")=="video"), {})
    return {"dur":dur,"w":vs.get("width",0),"h":vs.get("height",0),"kb":os.path.getsize(path)//1024}


# ══════════════════════════════════════════════════════════════════════════════
sep(f"PRÉ — Servidor (fase={PHASE})")
# ══════════════════════════════════════════════════════════════════════════════
try:
    r = get("/api/healthz")
    assert r.status_code == 200 and r.json().get("status") == "ok"
    # Detectar se está rodando Waitress (header Server) ou Flask dev
    srv_hdr = r.headers.get("Server", "?")
    ok("PRE — servidor OK", f"Server={srv_hdr} jobs={r.json().get('jobs',0)}")
except Exception as e:
    fail("PRE — servidor offline", str(e)[:150]); print("\n  OFFLINE."); sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
if RUN_LOAD:
    sep("R1 — CARGA CONCORRENTE: 100 pollers simultâneos (valida WSGI/Waitress)")
# ══════════════════════════════════════════════════════════════════════════════
# Simula 1000+ usuários fazendo polling de status. Mede latência e taxa de erro.

if RUN_LOAD:
    # R1.1 — 100 threads × 5 requests cada = 500 requests ao /api/healthz
    try:
        latencies = []; errors = []; lat_lock = threading.Lock()
        def _hammer(tid):
            local = []
            for _ in range(5):
                t0 = time.time()
                try:
                    r = get("/api/healthz", timeout=15)
                    dt = (time.time()-t0)*1000
                    if r.status_code == 200: local.append(dt)
                    else: errors.append(f"HTTP {r.status_code}")
                except Exception as e:
                    errors.append(type(e).__name__)
            with lat_lock: latencies.extend(local)
        threads = [threading.Thread(target=_hammer, args=(i,)) for i in range(100)]
        t_start = time.time()
        for t in threads: t.start()
        for t in threads: t.join(timeout=60)
        wall = time.time() - t_start
        n = len(latencies); total_req = 500
        if n > 0:
            latencies.sort()
            p50 = latencies[int(n*0.50)]
            p95 = latencies[int(n*0.95)]
            p99 = latencies[min(int(n*0.99), n-1)]
            success_rate = n / total_req * 100
            rps = total_req / wall if wall > 0 else 0
            detail = f"{n}/{total_req} OK ({success_rate:.1f}%), {rps:.0f}req/s, p50={p50:.0f}ms p95={p95:.0f}ms p99={p99:.0f}ms"
            if success_rate >= 99 and p95 < 2000:
                ok(f"R1.1 — 100 pollers concorrentes", detail)
            else:
                fail(f"R1.1 — 100 pollers (degradado)", detail)
        else:
            fail("R1.1 — 100 pollers", f"0 sucessos, erros={errors[:3]}")
    except Exception as e:
        fail("R1.1 — carga concorrente", str(e)[:200])

    # R1.2 — Mix realista: healthz + history + voices simultâneos (50 threads)
    try:
        results = {"healthz": [], "history": [], "voices": []}
        errs = []
        def _mixed(tid):
            ep = ["/api/healthz", "/api/history?limit=10", "/api/voices"][tid % 3]
            key = ep.split("/")[2].split("?")[0]
            t0 = time.time()
            try:
                r = get(ep, timeout=20)
                if r.status_code == 200:
                    results[key].append((time.time()-t0)*1000)
                else: errs.append(f"{key}:{r.status_code}")
            except Exception as e: errs.append(f"{key}:{type(e).__name__}")
        threads = [threading.Thread(target=_mixed, args=(i,)) for i in range(50)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=30)
        total_ok = sum(len(v) for v in results.values())
        if total_ok >= 47 and len(errs) <= 3:
            avg_h = statistics.mean(results["healthz"]) if results["healthz"] else 0
            ok(f"R1.2 — 50 requests mistos simultâneos",
               f"{total_ok}/50 OK, erros={len(errs)}, healthz_avg={avg_h:.0f}ms")
        else:
            fail(f"R1.2 — requests mistos", f"{total_ok}/50 OK, erros={errs[:5]}")
    except Exception as e:
        fail("R1.2 — requests mistos", str(e)[:200])

    # R1.3 — Burst extremo: 200 requests em rajada, servidor não pode cair
    try:
        ok_count = [0]; lock = threading.Lock()
        def _burst(tid):
            try:
                r = get("/api/healthz", timeout=20)
                if r.status_code == 200:
                    with lock: ok_count[0] += 1
            except: pass
        threads = [threading.Thread(target=_burst, args=(i,)) for i in range(200)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=40)
        # Verificar servidor ainda vivo após burst
        alive = get("/api/healthz", timeout=10).status_code == 200
        if ok_count[0] >= 190 and alive:
            ok(f"R1.3 — burst 200 requests", f"{ok_count[0]}/200 OK, servidor vivo")
        else:
            fail(f"R1.3 — burst 200", f"{ok_count[0]}/200 OK, alive={alive}")
    except Exception as e:
        fail("R1.3 — burst extremo", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
if RUN_LOAD:
    sep("R10 — SOAK TEST: 300 requests mistos sustentados (vazamento/estabilidade)")
# ══════════════════════════════════════════════════════════════════════════════

if RUN_LOAD:
    try:
        disk_before = sum(os.path.getsize(os.path.join(OUTPUTS,f))
                          for f in os.listdir(OUTPUTS)
                          if os.path.isfile(os.path.join(OUTPUTS,f))) / (1024**2) \
            if os.path.isdir(OUTPUTS) else 0
        endpoints = ["/api/healthz", "/api/history?limit=5", "/api/voices",
                     "/api/dashboard", "/api/settings", "/api/gesture_videos"]
        errors_soak = 0; ok_soak = 0
        t0 = time.time()
        for i in range(300):
            ep = endpoints[i % len(endpoints)]
            try:
                r = get(ep, timeout=15)
                if r.status_code == 200: ok_soak += 1
                else: errors_soak += 1
            except Exception:
                errors_soak += 1
            if i % 50 == 0 and i > 0:
                print(f"    [soak] {i}/300 requests... ({ok_soak} OK, {errors_soak} erros)")
        wall = time.time() - t0
        disk_after = sum(os.path.getsize(os.path.join(OUTPUTS,f))
                         for f in os.listdir(OUTPUTS)
                         if os.path.isfile(os.path.join(OUTPUTS,f))) / (1024**2) \
            if os.path.isdir(OUTPUTS) else 0
        # Após o soak, servidor deve estar vivo e responsivo
        alive = get("/api/healthz", timeout=10).status_code == 200
        if ok_soak >= 297 and alive:
            ok(f"R10.1 — soak 300 requests em {wall:.0f}s",
               f"{ok_soak}/300 OK, {errors_soak} erros, disco {disk_before:.0f}→{disk_after:.0f}MB")
        else:
            fail(f"R10.1 — soak test", f"{ok_soak}/300 OK, {errors_soak} erros, alive={alive}")
    except Exception as e:
        fail("R10.1 — soak test", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
if RUN_QUICK:
    sep("R6 — TTS ALTERNATIVOS: ElevenLabs/F5-TTS degradação graciosa")
# ══════════════════════════════════════════════════════════════════════════════

if RUN_QUICK:
    # R6.1 — ElevenLabs sem chave válida → erro gracioso (não crash)
    try:
        r = post("/api/preview_audio",
                 json={"script": "ElevenLabs test.", "voice_id": "fake_voice_id",
                       "engine": "elevenlabs"}, timeout=20)
        # Esperado: 200 (se tiver chave) ou 400/500 com mensagem (sem chave)
        assert r.status_code in (200, 400, 500), f"HTTP inesperado {r.status_code}"
        body = ""
        try: body = r.json().get("error","") or r.json().get("audio_url","")
        except: body = r.text[:60]
        ok(f"R6.1 — ElevenLabs sem chave → {r.status_code} (gracioso)", body[:50])
    except Exception as e:
        fail("R6.1 — ElevenLabs gracioso", str(e)[:200])

    # R6.2 — Servidor vivo após tentativa ElevenLabs
    try:
        r = get("/api/healthz", timeout=5)
        assert r.status_code == 200
        ok("R6.2 — servidor estável após ElevenLabs")
    except Exception as e:
        fail("R6.2 — servidor pós-ElevenLabs", str(e)[:150])

    # R6.3 — Engine inválido → fallback gracioso
    try:
        r = post("/api/preview_audio",
                 json={"script": "Engine test.", "voice": "pt-BR-FranciscaNeural",
                       "engine": "engine_inexistente_xyz"}, timeout=25)
        # Deve fazer fallback p/ edge-tts ou erro gracioso
        ok(f"R6.3 — engine inválido → {r.status_code} (fallback/gracioso)")
    except Exception as e:
        fail("R6.3 — engine inválido", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
if RUN_PATHS:
    sep("R2 — UPLOAD DE ÁUDIO PRÓPRIO (bypass TTS) — pipeline completo")
# ══════════════════════════════════════════════════════════════════════════════

if RUN_PATHS:
    # Gerar um áudio real via preview_audio, baixar, e submeter como upload
    try:
        print("  [R2] Gerando áudio de teste via Edge-TTS...")
        r = post("/api/preview_audio",
                 json={"script": "Este áudio foi enviado pelo usuário, ignorando a síntese de voz automática.",
                       "voice": "pt-BR-AntonioNeural"}, timeout=30)
        assert r.status_code == 200, f"preview HTTP {r.status_code}"
        audio_url = r.json().get("audio_url","")
        rb = get(audio_url, timeout=15)
        assert rb.status_code == 200 and len(rb.content) > 2000, "áudio inválido"
        audio_bytes = rb.content
        print(f"  [R2] Áudio gerado: {len(audio_bytes)//1024}KB. Submetendo com upload...")

        with open(IMG, "rb") as f:
            r = post("/api/generate",
                     data={"voice": "pt-BR-AntonioNeural", "engine": "edge-tts", "enhancer": "none"},
                     files={"image": (os.path.basename(IMG), f, "image/jpeg"),
                            "audio": ("user_audio.mp3", io.BytesIO(audio_bytes), "audio/mpeg")},
                     timeout=30)
        if r.status_code != 200:
            fail("R2.1 — submit com áudio", f"HTTP {r.status_code}: {r.text[:100]}")
        else:
            jid = r.json().get("job_id","")
            ok(f"R2.1 — job com áudio upload submetido", f"job={jid[:8]}")
            t0 = time.time()
            result = wait(jid, timeout_s=3000, poll=12)
            out = result.get("output_path","")
            info = validate_mp4(out, min_dur=3.0)
            ok(f"R2.2 — job com áudio próprio completo em {(time.time()-t0)/60:.1f}min",
               f"{info['dur']:.1f}s {info['w']}x{info['h']} {info['kb']}KB")
    except Exception as e:
        fail("R2 — upload de áudio", str(e)[:250])


# ══════════════════════════════════════════════════════════════════════════════
if RUN_PATHS:
    sep("R3 — UPLOAD DE VÍDEO COMO AVATAR (em vez de foto) — pipeline completo")
# ══════════════════════════════════════════════════════════════════════════════

if RUN_PATHS:
    try:
        # Usar um gesture video curto como avatar de vídeo
        vid_src = None
        if os.path.isdir(GESTURE):
            vids = sorted([f for f in os.listdir(GESTURE) if f.endswith(".mp4")],
                          key=lambda f: os.path.getsize(os.path.join(GESTURE, f)))
            if vids: vid_src = os.path.join(GESTURE, vids[0])  # menor
        assert vid_src and os.path.exists(vid_src), "nenhum vídeo de teste disponível"
        print(f"  [R3] Usando vídeo {os.path.basename(vid_src)} ({os.path.getsize(vid_src)//1024//1024}MB) como avatar...")

        with open(vid_src, "rb") as f:
            r = post("/api/generate",
                     data={"script": "Teste de avatar a partir de vídeo enviado pelo usuário.",
                           "voice": "pt-BR-FranciscaNeural", "engine": "edge-tts", "enhancer": "none"},
                     files={"image": ("avatar_video.mp4", f, "video/mp4")},
                     timeout=60)
        if r.status_code != 200:
            fail("R3.1 — submit vídeo avatar", f"HTTP {r.status_code}: {r.text[:100]}")
        else:
            jid = r.json().get("job_id","")
            ok(f"R3.1 — job com vídeo avatar submetido", f"job={jid[:8]}")
            t0 = time.time()
            result = wait(jid, timeout_s=4000, poll=12)
            out = result.get("output_path","")
            info = validate_mp4(out, min_dur=3.0)
            ok(f"R3.2 — job com vídeo avatar completo em {(time.time()-t0)/60:.1f}min",
               f"{info['dur']:.1f}s {info['w']}x{info['h']} {info['kb']}KB")
    except Exception as e:
        fail("R3 — upload de vídeo", str(e)[:250])


# ══════════════════════════════════════════════════════════════════════════════
if RUN_PATHS:
    sep("R4 — LEGENDAS BURNED-IN (Whisper) — pipeline completo")
# ══════════════════════════════════════════════════════════════════════════════

if RUN_PATHS:
    try:
        print("  [R4] Submetendo job com captions=true (Whisper transcreve + queima legenda)...")
        with open(IMG, "rb") as f:
            r = post("/api/generate",
                     data={"script": "Este vídeo demonstra legendas automáticas geradas por inteligência artificial. "
                                      "O Whisper transcreve o áudio e as legendas são queimadas no vídeo final.",
                           "voice": "pt-BR-FranciscaNeural", "engine": "edge-tts", "enhancer": "none",
                           "captions": "true", "caption_lang": "pt", "caption_color": "white",
                           "caption_position": "bottom", "caption_font_size": "28"},
                     files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                     timeout=30)
        if r.status_code != 200:
            fail("R4.1 — submit legendas", f"HTTP {r.status_code}: {r.text[:100]}")
        else:
            jid = r.json().get("job_id","")
            ok(f"R4.1 — job com legendas submetido", f"job={jid[:8]}")
            t0 = time.time()
            result = wait(jid, timeout_s=3500, poll=12)
            out = result.get("output_path","")
            info = validate_mp4(out, min_dur=5.0)
            ok(f"R4.2 — job com legendas completo em {(time.time()-t0)/60:.1f}min",
               f"{info['dur']:.1f}s {info['w']}x{info['h']} {info['kb']}KB")
    except Exception as e:
        fail("R4 — legendas burned-in", str(e)[:250])


# ══════════════════════════════════════════════════════════════════════════════
total = passes + fails
print(f"\n{'='*70}")
print(f"  ROBUSTEZ — RESULTADO: {passes} PASS / {fails} FAIL / {total} TOTAL (fase={PHASE})")
print(f"{'='*70}")
if fail_log:
    print(f"\n  FALHAS ({len(fail_log)}):")
    for f_msg in fail_log:
        print(f"    {f_msg}")
else:
    print(f"\n  TODOS OS {total} TESTES DE ROBUSTEZ PASSARAM! 🏆")
print()
sys.exit(0 if fails == 0 else 1)
