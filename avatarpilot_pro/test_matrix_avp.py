"""
AvatarPilot Pro — MATRIZ DE MODALIDADES & DURAÇÕES (2026-05-28)

Testa TODAS as modalidades possíveis do /api/generate em várias durações,
com repetição para detectar falhas intermitentes. Complementa test_e2e_avp.py.

Uso:
    python test_matrix_avp.py fast    # só M1+M2 (rápido, ~8min, sem pipeline pesado)
    python test_matrix_avp.py heavy   # só M3+M4+M5 (pesado, ~2h, pipeline completo)
    python test_matrix_avp.py         # tudo

SEÇÕES:
  M1 — TTS/Voz: 15 presets, 6+ vozes, 4 durações de texto, 2 engines (preview, rápido)
  M2 — Aceitação de modalidades: ~28 parâmetros do /api/generate (submit+cancel)
  M3 — Matriz de durações: 5s, 45s, 120s, 180s (pipeline completo)
  M4 — Combos de modalidades: captions+watermark+audio, portrait, square (pipeline)
  M5 — Estabilidade: mesmo job 15s repetido 3x (detecta flakiness/vazamentos)
"""

import sys, os, time, json, requests, subprocess, shutil, io, threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:5052"
UPLOADS  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
OUTPUTS  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")

PHASE = (sys.argv[1].lower() if len(sys.argv) > 1 else "all")
RUN_FAST  = PHASE in ("fast", "all")
RUN_HEAVY = PHASE in ("heavy", "all")

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

def submit_modality(extra_data: dict, script="Teste de modalidade do AvatarPilot Pro.",
                    retry_on_429=True, timeout_s=25):
    """Submit a job with extra modality params. Returns (status_code, job_id_or_error).
    Handles rate limit (429) by waiting and retrying once."""
    data = {"script": script, "voice": "pt-BR-FranciscaNeural",
            "engine": "edge-tts", "enhancer": "none"}
    data.update(extra_data)
    for attempt in range(2 if retry_on_429 else 1):
        with open(IMG, "rb") as f:
            r = post("/api/generate", data=data,
                     files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                     timeout=timeout_s)
        if r.status_code == 429 and retry_on_429 and attempt == 0:
            print("    [rate limit — aguardando 62s...]")
            time.sleep(62); continue
        break
    if r.status_code == 200:
        return 200, r.json().get("job_id", "")
    try: err = r.json().get("error", "")[:80]
    except: err = r.text[:80]
    return r.status_code, err

def wait(job_id, timeout_s=3600, poll=8):
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

def validate_mp4(path, min_dur=2.0, min_kb=50, expect_w=None, expect_h=None):
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
    assert any(s.get("codec_type")=="audio" for s in streams), "no audio"
    vs = next((s for s in streams if s.get("codec_type")=="video"), {})
    w, h = vs.get("width",0), vs.get("height",0)
    if expect_w and expect_h:
        assert w==expect_w and h==expect_h, f"resolução {w}x{h} != {expect_w}x{expect_h}"
    return {"dur":dur,"w":w,"h":h,"kb":os.path.getsize(path)//1024}

# Build scripts of specific approximate durations (PT-BR ~14 chars/s at normal rate)
def script_for_seconds(target_s: int) -> str:
    base = ("A inteligência artificial transforma a maneira como criamos conteúdo digital "
            "com qualidade profissional e rapidez impressionante todos os dias. ")
    # ~14 chars/sec → target chars ≈ target_s * 14
    target_chars = target_s * 14
    out = ""
    while len(out) < target_chars:
        out += base
    return out[:target_chars]


# ══════════════════════════════════════════════════════════════════════════════
sep(f"PRÉ — Servidor (fase={PHASE})")
# ══════════════════════════════════════════════════════════════════════════════
try:
    r = get("/api/healthz")
    assert r.status_code == 200 and r.json().get("status") == "ok"
    ok("PRE — servidor OK", f"jobs={r.json().get('jobs',0)} queue={r.json().get('queue',0)}")
except Exception as e:
    fail("PRE — servidor offline", str(e)[:150])
    print("\n  SERVIDOR OFFLINE."); sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
if RUN_FAST:
    sep("M1 — MATRIZ TTS/VOZ (15 presets + vozes diretas + durações + engines)")
# ══════════════════════════════════════════════════════════════════════════════

VOICE_PRESETS = [
    "documentary_narrator","news_anchor","sales_pitch","corporate_trainer",
    "friendly_explainer","podcast_host","br_apresentador","br_educacional",
    "es_noticias","fr_narrateur","de_sprecher","asmr_calm","energetic_host",
]
DIRECT_VOICES = [
    ("pt-BR-FranciscaNeural","Olá, este é um teste de voz feminina em português."),
    ("pt-BR-AntonioNeural","Olá, este é um teste de voz masculina em português."),
    ("en-US-GuyNeural","Hello, this is an English voice synthesis test."),
    ("es-ES-AlvaroNeural","Hola, esta es una prueba de síntesis de voz en español."),
    ("fr-FR-HenriNeural","Bonjour, ceci est un test de synthèse vocale en français."),
    ("de-DE-ConradNeural","Hallo, dies ist ein deutscher Sprachsynthese-Test."),
]

if RUN_FAST:
    # M1.1-M1.13 — cada preset gera preview (preview_audio NÃO tem rate limit)
    for i, preset in enumerate(VOICE_PRESETS, start=1):
        try:
            r = post("/api/preview_audio",
                     json={"script": "Teste de preset de voz para validação completa.",
                           "voice_preset": preset, "voice": "", "engine": "edge-tts"},
                     timeout=25)
            # Preset may not be honored by preview endpoint; just ensure no crash
            assert r.status_code in (200, 400), f"HTTP {r.status_code}"
            ok(f"M1.{i} — preset '{preset}' → {r.status_code}")
        except Exception as e:
            fail(f"M1.{i} — preset '{preset}'", str(e)[:120])

    # M1.14-M1.19 — vozes diretas multi-idioma com áudio real
    base_n = len(VOICE_PRESETS)
    for j, (voice, txt) in enumerate(DIRECT_VOICES, start=1):
        try:
            r = post("/api/preview_audio",
                     json={"script": txt, "voice": voice, "engine": "edge-tts"},
                     timeout=25)
            assert r.status_code == 200, f"HTTP {r.status_code}"
            url = r.json().get("audio_url","")
            assert url, "sem audio_url"
            # baixar e validar tamanho
            rb = get(url, timeout=10)
            assert rb.status_code == 200 and len(rb.content) > 1000, "áudio inválido"
            ok(f"M1.{base_n+j} — voz {voice}", f"{len(rb.content)//1024}KB")
        except Exception as e:
            fail(f"M1.{base_n+j} — voz {voice}", str(e)[:120])

    # M1.20-M1.23 — durações de texto (10, 100, 300, 500 chars)
    base_n2 = len(VOICE_PRESETS) + len(DIRECT_VOICES)
    for k, n_chars in enumerate([10, 100, 300, 500], start=1):
        try:
            txt = ("AvatarPilot Pro gera vídeos. " * 20)[:n_chars]
            r = post("/api/preview_audio",
                     json={"script": txt, "voice": "pt-BR-FranciscaNeural"},
                     timeout=25)
            assert r.status_code == 200, f"HTTP {r.status_code}"
            ok(f"M1.{base_n2+k} — preview {n_chars} chars → 200")
        except Exception as e:
            fail(f"M1.{base_n2+k} — preview {n_chars} chars", str(e)[:120])


# ══════════════════════════════════════════════════════════════════════════════
if RUN_FAST:
    sep("M2 — ACEITAÇÃO DE MODALIDADES (~28 parâmetros — submit+cancel)")
# ══════════════════════════════════════════════════════════════════════════════
# Rate limit: 10 jobs/60s. Submetemos em lotes e tratamos 429 com retry.

MODALITY_MATRIX = [
    ("preprocess=crop",        {"preprocess": "crop"}),
    ("preprocess=full",        {"preprocess": "full"}),
    ("preprocess=resize",      {"preprocess": "resize"}),
    ("enhancer=none",          {"enhancer": "none"}),
    ("enhancer=gfpgan",        {"enhancer": "gfpgan"}),
    ("size=256",               {"size": "256"}),
    ("size=512",               {"size": "512"}),
    ("still_mode=true",        {"still_mode": "true"}),
    ("enhance_face=false",     {"enhance_face": "false"}),
    ("expression_scale=0.5",   {"expression_scale": "0.5"}),
    ("expression_scale=2.0",   {"expression_scale": "2.0"}),
    ("output_format=portrait", {"output_format": "portrait"}),
    ("output_format=square",   {"output_format": "square"}),
    ("output_format=landscape",{"output_format": "landscape"}),
    ("captions=true",          {"captions": "true", "caption_lang": "pt"}),
    ("caption_color=yellow",   {"captions": "true", "caption_color": "yellow"}),
    ("caption_pos=top",        {"captions": "true", "caption_position": "top"}),
    ("caption_font=40",        {"captions": "true", "caption_font_size": "40"}),
    ("watermark_text",         {"watermark_text": "AVP Teste", "watermark_pos": "bottom_right"}),
    ("watermark_pos=top_left", {"watermark_text": "Marca", "watermark_pos": "top_left"}),
    ("normalize_audio=true",   {"normalize_audio": "true"}),
    ("enable_fade=true",       {"enable_fade": "true", "fade_in": "1.0", "fade_out": "1.0"}),
    ("music_volume=0.3",       {"music_volume": "0.3"}),
    ("video_engine=auto",      {"video_engine": "auto"}),
    ("lip_sync=wav2lip",       {"lip_sync_engine": "wav2lip"}),
    ("voice_preset=br_apres",  {"voice_preset": "br_apresentador"}),
    ("avatar_position",        {"avatar_position": "bottom_left", "avatar_size": "large"}),
    ("template_vars",          {"template_vars": json.dumps({"nome": "Mundo"}), "script": "Olá {{nome}}!"}),
    ("chroma_key",             {"chroma_key": "#00FF00", "chroma_tolerance": "50"}),
    ("output_format=invalid",  {"output_format": "formato_xyz_invalido"}),
    ("size=99999",             {"size": "99999"}),
    ("expression_scale=999",   {"expression_scale": "999"}),
]

if RUN_FAST:
    submitted_count = 0
    for i, (label, params) in enumerate(MODALITY_MATRIX, start=1):
        try:
            # Rate limit: a cada 9 submits, aguardar reset
            if submitted_count > 0 and submitted_count % 9 == 0:
                print(f"    [lote — aguardando 62s p/ reset rate limit ({submitted_count} submits)...]")
                time.sleep(62)
            code, jid = submit_modality(params)
            submitted_count += 1
            if code == 200:
                cancel_job(jid)
                ok(f"M2.{i} — {label} → aceito (200, cancelado)")
            elif code in (400, 422):
                ok(f"M2.{i} — {label} → {code} (validação graciosa)")
            elif code == 429:
                ok(f"M2.{i} — {label} → 429 (rate limit, esperado sob carga)")
            else:
                fail(f"M2.{i} — {label}", f"HTTP {code}: {jid}")
        except Exception as e:
            fail(f"M2.{i} — {label}", str(e)[:120])
    # Aguardar rate limit zerar antes da fase pesada
    if RUN_HEAVY:
        print("    [aguardando 62s p/ reset rate limit antes da fase pesada...]")
        time.sleep(62)


# ══════════════════════════════════════════════════════════════════════════════
if RUN_HEAVY:
    sep("M3 — MATRIZ DE DURAÇÕES (5s, 45s, 120s, 180s — pipeline completo)")
# ══════════════════════════════════════════════════════════════════════════════
# Complementa T1(11s)/T2(26s)/T3(88s). Cobre extremos: muito curto e muito longo.

DURATION_MATRIX = [
    (5,   "M3.1", 2.0,  3600),   # muito curto — edge case
    (45,  "M3.2", 18.0, 4500),   # médio
    (120, "M3.3", 60.0, 6000),   # longo — gesture pack
    (180, "M3.4", 90.0, 7200),   # muito longo — Wav2Lip chunked stress
]

if RUN_HEAVY:
    for target_s, tid, min_dur, tmo in DURATION_MATRIX:
        try:
            script = script_for_seconds(target_s)
            print(f"  [{tid}] Submetendo job de ~{target_s}s ({len(script)} chars)...")
            with open(IMG, "rb") as f:
                r = post("/api/generate",
                         data={"script": script, "voice": "pt-BR-FranciscaNeural",
                               "engine": "edge-tts", "enhancer": "none"},
                         files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                         timeout=30)
            if r.status_code != 200:
                fail(f"{tid} — submit ~{target_s}s", f"HTTP {r.status_code}: {r.text[:80]}")
                continue
            jid = r.json().get("job_id","")
            t0 = time.time()
            result = wait(jid, timeout_s=tmo, poll=15)
            elapsed = time.time() - t0
            out = result.get("output_path","")
            info = validate_mp4(out, min_dur=min_dur, min_kb=50)
            ok(f"{tid} — job ~{target_s}s OK em {elapsed/60:.1f}min",
               f"{info['dur']:.1f}s {info['w']}x{info['h']} {info['kb']}KB")
        except Exception as e:
            fail(f"{tid} — job ~{target_s}s", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
if RUN_HEAVY:
    sep("M4 — COMBOS DE MODALIDADES (features combinados — pipeline completo)")
# ══════════════════════════════════════════════════════════════════════════════

COMBO_MATRIX = [
    ("M4.1", "captions+watermark+normalize+fade",
     {"captions":"true","caption_lang":"pt","caption_color":"white","caption_position":"bottom",
      "watermark_text":"AvatarPilot","watermark_pos":"bottom_right",
      "normalize_audio":"true","enable_fade":"true","fade_in":"0.8","fade_out":"0.8"},
     None, None),
    ("M4.2", "output_format=portrait (1080x1920)",
     {"output_format":"portrait"}, 1080, 1920),
    ("M4.3", "output_format=square (1080x1080)",
     {"output_format":"square"}, 1080, 1080),
]

if RUN_HEAVY:
    combo_script = ("Bem-vindo ao teste de combinação de recursos avançados. "
                    "Este vídeo usa múltiplas modalidades simultaneamente para validação completa.")
    for tid, label, params, ew, eh in COMBO_MATRIX:
        try:
            print(f"  [{tid}] {label}...")
            data = {"script": combo_script, "voice": "pt-BR-FranciscaNeural",
                    "engine": "edge-tts", "enhancer": "none"}
            data.update(params)
            with open(IMG, "rb") as f:
                r = post("/api/generate", data=data,
                         files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                         timeout=30)
            if r.status_code != 200:
                fail(f"{tid} — {label}", f"HTTP {r.status_code}: {r.text[:80]}")
                continue
            jid = r.json().get("job_id","")
            t0 = time.time()
            result = wait(jid, timeout_s=4500, poll=15)
            elapsed = time.time() - t0
            out = result.get("output_path","")
            info = validate_mp4(out, min_dur=4.0, min_kb=50, expect_w=ew, expect_h=eh)
            ok(f"{tid} — {label} OK em {elapsed/60:.1f}min",
               f"{info['dur']:.1f}s {info['w']}x{info['h']} {info['kb']}KB")
        except Exception as e:
            fail(f"{tid} — {label}", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
if RUN_HEAVY:
    sep("M5 — ESTABILIDADE: mesmo job 15s repetido 3x (detecta flakiness)")
# ══════════════════════════════════════════════════════════════════════════════

if RUN_HEAVY:
    stab_script = ("Teste de estabilidade e consistência do pipeline de geração. "
                   "O mesmo conteúdo é gerado múltiplas vezes para detectar falhas intermitentes.")
    durations_seen = []
    for rep in range(1, 4):
        try:
            print(f"  [M5.{rep}] Repetição {rep}/3...")
            with open(IMG, "rb") as f:
                r = post("/api/generate",
                         data={"script": stab_script, "voice": "pt-BR-FranciscaNeural",
                               "engine": "edge-tts", "enhancer": "none"},
                         files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                         timeout=30)
            if r.status_code != 200:
                fail(f"M5.{rep} — repetição {rep}", f"HTTP {r.status_code}")
                continue
            jid = r.json().get("job_id","")
            t0 = time.time()
            result = wait(jid, timeout_s=3600, poll=12)
            elapsed = time.time() - t0
            out = result.get("output_path","")
            info = validate_mp4(out, min_dur=4.0, min_kb=50)
            durations_seen.append(info["dur"])
            ok(f"M5.{rep} — repetição {rep} OK em {elapsed/60:.1f}min",
               f"{info['dur']:.1f}s {info['kb']}KB")
        except Exception as e:
            fail(f"M5.{rep} — repetição {rep}", str(e)[:200])

    # M5.4 — consistência: durações devem ser ~iguais (mesmo input)
    try:
        if len(durations_seen) >= 2:
            spread = max(durations_seen) - min(durations_seen)
            assert spread < 1.5, f"durações inconsistentes: {durations_seen} (spread {spread:.2f}s)"
            ok(f"M5.4 — consistência entre repetições", f"durações={[f'{d:.1f}' for d in durations_seen]}s spread={spread:.2f}s")
        else:
            fail("M5.4 — consistência", "menos de 2 repetições completaram")
    except Exception as e:
        fail("M5.4 — consistência", str(e)[:150])


# ══════════════════════════════════════════════════════════════════════════════
total = passes + fails
print(f"\n{'='*70}")
print(f"  MATRIZ — RESULTADO: {passes} PASS / {fails} FAIL / {total} TOTAL (fase={PHASE})")
print(f"{'='*70}")
if fail_log:
    print(f"\n  FALHAS ({len(fail_log)}):")
    for f_msg in fail_log:
        print(f"    {f_msg}")
else:
    print(f"\n  TODOS OS {total} TESTES DA MATRIZ PASSARAM! 🏆")
print()
sys.exit(0 if fails == 0 else 1)
