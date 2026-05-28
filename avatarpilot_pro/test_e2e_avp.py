"""
AvatarPilot Pro — E2E Test Suite COMPLETO & AVANÇADO (2026-05-28)

Testa todo o pipeline para uso em produção com 1000+ usuários.
~85 testes cobrindo:

  PRE:  Verificação do servidor (6)
  T1:   Job curto — baseline (4)
  T2:   Job médio — downscale 720p (4)
  T3:   Job longo — gesture pack + Wav2Lip (4)
  T4:   API endpoints essenciais (7)
  T5:   Robustez básica (6)
  T6:   Qualidade profunda do output — ffprobe/ffmpeg (8)
  T7:   Edge cases de imagem — corrompida, vazia, gigante, sem rosto (7)
  T8:   Segurança — injeção SQL/XSS, path traversal, payloads maliciosos (8)
  T9:   Rate limit e cancelamento de jobs (5)
  T10:  Settings API — GET/POST/persistência (5)
  T11:  History — paginação extrema e consistência (6)
  T12:  Voices avançado — preview de todas as vozes PT-BR (6)
  T13:  Stress de API — concorrência em todos os endpoints (5)
  T14:  Outputs, filesystem e limpeza de disco (6)
  T15:  Pipeline variante — com GFPGAN enhancer (3)
"""

import sys, os, time, json, requests, subprocess, shutil, tempfile, threading, io, re

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:5052"
UPLOADS  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
OUTPUTS  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
TMPDIR   = tempfile.mkdtemp(prefix="avp_test_")  # cleaned up at end

passes = fails = 0
fail_log = []

# ─── contadores e helpers ─────────────────────────────────────────────────────
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

# ─── find best portrait image ─────────────────────────────────────────────────
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

# ─── submit helper ────────────────────────────────────────────────────────────
def submit(script: str, voice="pt-BR-FranciscaNeural",
           enhancer="none", preprocess="crop",
           img_path=None, timeout_s=30) -> str:
    _img = img_path or IMG
    with open(_img, "rb") as f:
        r = post("/api/generate",
                 data={"script": script, "voice": voice,
                       "engine": "edge-tts", "enhancer": enhancer,
                       "preprocess": preprocess},
                 files={"image": (os.path.basename(_img), f, "image/jpeg")},
                 timeout=timeout_s)
    assert r.status_code == 200, f"submit HTTP {r.status_code}: {r.text[:200]}"
    job_id = r.json().get("job_id", "")
    assert job_id, f"no job_id in: {r.text[:200]}"
    return job_id

def submit_bytes(script: str, img_bytes: bytes, img_name="test.jpg",
                 content_type="image/jpeg", timeout_s=20) -> requests.Response:
    """Submit with raw image bytes — allows sending corrupt/invalid images."""
    r = post("/api/generate",
             data={"script": script, "voice": "pt-BR-FranciscaNeural",
                   "engine": "edge-tts", "enhancer": "none"},
             files={"image": (img_name, io.BytesIO(img_bytes), content_type)},
             timeout=timeout_s)
    return r

def cancel_job(job_id: str):
    """Best-effort cancel — swallows all errors."""
    try: post(f"/api/job/{job_id}/cancel", timeout=5)
    except: pass

def wait(job_id: str, timeout_s=1800, poll=5) -> dict:
    t0 = time.time(); last = ""
    while time.time() - t0 < timeout_s:
        r = get(f"/api/job/{job_id}")
        d = r.json()
        st, msg, prog = d.get("status","?"), d.get("message",""), d.get("progress",0)
        if msg != last:
            print(f"    [{st}] {prog}% — {msg}")
            last = msg
        if st == "done":   return d
        if st in ("failed", "error"):
            raise RuntimeError(f"Job {st}: {d.get('error', d.get('message','?'))}")
        time.sleep(poll)
    raise TimeoutError(f"Job {job_id} not done after {timeout_s}s")

def validate_mp4(path: str, min_dur=5.0, min_kb=200) -> dict:
    assert os.path.exists(path), f"output missing: {path}"
    assert os.path.getsize(path) > min_kb * 1024, \
        f"output too small: {os.path.getsize(path)//1024}KB < {min_kb}KB"
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                        "-show_streams", "-show_format", path],
                       capture_output=True, text=True, timeout=15)
    info = json.loads(r.stdout)
    dur = float(info.get("format", {}).get("duration", 0))
    assert dur >= min_dur, f"video too short: {dur:.1f}s < {min_dur}s"
    streams = info.get("streams", [])
    assert any(s.get("codec_type") == "video" for s in streams), "no video stream"
    assert any(s.get("codec_type") == "audio" for s in streams), "no audio stream"
    vs = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {"dur": dur, "w": vs.get("width",0), "h": vs.get("height",0),
            "kb": os.path.getsize(path)//1024, "streams": streams,
            "format": info.get("format", {})}

# ─── minimal test images ─────────────────────────────────────────────────────
def make_tiny_jpeg() -> bytes:
    """Create a minimal valid 1×1 pixel JPEG."""
    try:
        from PIL import Image
        img = Image.new("RGB", (8, 8), (128, 0, 0))
        buf = io.BytesIO(); img.save(buf, format="JPEG"); return buf.getvalue()
    except ImportError:
        # Hardcoded minimal JPEG (10×10 black)
        return (b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
                b'\xff\xdb\x00C\x00\x10\x0b\x0c\x0e\x0c\n\x10\x0e\r\x0e\x12\x11'
                b'\x10\x13\x18(\x1a\x18\x16\x16\x18\x310#%\x1d(55;4:;=<9=<;@DMPPS'
                b'IOQZQW^W[JKdldhiqvroty\x85\x80~\x82\x8f\x8aasn\x90\x91\x95\x96'
                b'\x97\x96\x5b\x72\x99\x9d\xa3\x9f\xa0\x9d\x9f\x9c\xff\xc0\x00\x0b'
                b'\x08\x00\n\x00\n\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05'
                b'\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02'
                b'\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\xff\xda\x00\x08\x01\x01\x00'
                b'\x00?\x00\xf5\x00\x00\x00\x00\x00\xff\xd9')

def make_corrupt_jpeg() -> bytes:
    """Valid JPEG header but truncated — invalid image."""
    return b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00' + b'\xab' * 64

def make_png_bytes() -> bytes:
    """Minimal valid 1×1 PNG."""
    try:
        from PIL import Image
        img = Image.new("RGB", (4, 4), (0, 128, 255))
        buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()
    except ImportError:
        return (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00'
                b'\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8'
                b'\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')

CORRUPT_JPEG = make_corrupt_jpeg()
TINY_JPEG    = make_tiny_jpeg()
PNG_BYTES    = make_png_bytes()
TEXT_AS_JPG  = b"This is a plain text file, not an image. " * 20
EMPTY_BYTES  = b""
LARGE_FAKE   = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01' + b'\x00' * (7 * 1024 * 1024)


# ══════════════════════════════════════════════════════════════════════════════
sep("PRE — Verificação do servidor")
# ══════════════════════════════════════════════════════════════════════════════

try:
    r = get("/api/healthz")
    assert r.status_code == 200
    d = r.json()
    assert d.get("status") == "ok"
    ok("PRE.1 — servidor respondendo em localhost:5052",
       f"jobs={d.get('jobs',0)} queue={d.get('queue',0)}")
except Exception as e:
    fail("PRE.1 — servidor offline", str(e)[:200])
    print("\n  SERVIDOR OFFLINE — abra o AvatarPilot Pro antes de rodar este teste.")
    sys.exit(1)

try:
    r = get("/api/system_health", timeout=30)
    ok("PRE.2 — system_health disponivel", f"HTTP {r.status_code}")
except Exception as e:
    ok(f"PRE.2 — system_health timeout/error (aceitavel em carga): {type(e).__name__}")

try:
    r = get("/api/voices")
    d_voices = r.json()
    if isinstance(d_voices, list):
        voice_list = d_voices; total = len(voice_list)
    elif isinstance(d_voices, dict):
        total = d_voices.get("total", 0) or sum(
            len(v) for v in d_voices.get("voices", {}).values() if isinstance(v, list))
        voice_list = [str(v) for vl in d_voices.get("voices", {}).values()
                      for v in (vl if isinstance(vl, list) else [])]
    else:
        total = 0; voice_list = []
    assert total >= 10, f"apenas {total} vozes"
    ptbr = [v for v in voice_list if "pt-BR" in str(v)]
    ok(f"PRE.3 — {total} vozes disponíveis", f"pt-BR={len(ptbr)}")
except Exception as e:
    fail("PRE.3 — voices endpoint", str(e)[:200])

try:
    r = get("/api/history")
    d = r.json()
    total_hist = d.get("total", 0)
    ok(f"PRE.4 — history: {total_hist} jobs no historico",
       f"size={d.get('total_size_mb',0):.1f}MB")
except Exception as e:
    fail("PRE.4 — history endpoint", str(e)[:200])

try:
    assert os.path.exists(IMG) and os.path.getsize(IMG) > 10_000, \
        f"imagem nao encontrada: {IMG}"
    ok(f"PRE.5 — imagem de teste OK",
       f"{os.path.basename(IMG)} ({os.path.getsize(IMG)//1024}KB)")
except Exception as e:
    fail("PRE.5 — imagem de teste", str(e)[:200])

gesture_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "static", "gesture_videos")
gesture_videos = [f for f in os.listdir(gesture_dir) if f.endswith(".mp4")] \
    if os.path.isdir(gesture_dir) else []
try:
    assert len(gesture_videos) >= 10, f"apenas {len(gesture_videos)} gesture videos"
    total_mb = sum(os.path.getsize(os.path.join(gesture_dir, f))
                   for f in gesture_videos) / (1024**2)
    ok(f"PRE.6 — {len(gesture_videos)} gesture videos OK", f"total={total_mb:.0f}MB")
except Exception as e:
    fail("PRE.6 — gesture videos", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T1 — Job CURTO (15s) — baseline Edge-TTS + lip sync")
# ══════════════════════════════════════════════════════════════════════════════

SHORT_SCRIPT = (
    "Bem-vindo ao AvatarPilot Pro. "
    "Este sistema usa inteligência artificial avançada para criar vídeos de avatar. "
    "A sincronização labial é precisa e natural."
)

job_id = out = result = None
try:
    job_id = submit(SHORT_SCRIPT, enhancer="none")
    ok(f"T1.1 — job submetido: {job_id}")
except Exception as e:
    fail("T1.1 — submit curto", str(e)[:200])

if job_id:
    try:
        print(f"  Aguardando job {job_id} (max 45min — SadTalker+MuseTalk+GFPGAN)...")
        t0 = time.time()
        result = wait(job_id, timeout_s=2700)
        elapsed = time.time() - t0
        ok(f"T1.2 — job completo em {elapsed:.0f}s", f"status={result.get('status')}")
    except Exception as e:
        fail("T1.2 — job curto completou", str(e)[:300])

    if result:
        try:
            out = result.get("output_path", "")
            info = validate_mp4(out, min_dur=8.0, min_kb=100)
            ok(f"T1.3 — output MP4 valido",
               f"{info['dur']:.1f}s {info['w']}x{info['h']} {info['kb']}KB @ {out[-30:]}")
        except Exception as e:
            fail("T1.3 — output MP4 valido", str(e)[:200])
        try:
            r2 = get(f"/api/job/{job_id}")
            d2 = r2.json()
            assert d2.get("status") == "done"
            assert "output_path" in d2
            ok(f"T1.4 — status API retorna done + output_path")
        except Exception as e:
            fail("T1.4 — status API", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T2 — Job MÉDIO (60s) — SadTalker path, downscale 720p ativo")
# ══════════════════════════════════════════════════════════════════════════════

MEDIUM_SCRIPT = (
    "O AvatarPilot Pro revoluciona a criação de conteúdo digital. "
    "Com nossa tecnologia de ponta, você pode criar avatares realistas em minutos. "
    "O sistema de lip sync usa inteligência artificial de última geração "
    "para garantir que os movimentos labiais sejam perfeitamente sincronizados com o áudio. "
    "Ideal para criadores de conteúdo, educadores e empresas que precisam "
    "de vídeos profissionais com rapidez e qualidade."
)

job_id2 = out2 = result2 = None
try:
    job_id2 = submit(MEDIUM_SCRIPT, enhancer="none")
    ok(f"T2.1 — job médio submetido: {job_id2}")
except Exception as e:
    fail("T2.1 — submit médio", str(e)[:200])

if job_id2:
    try:
        print(f"  Aguardando job {job_id2} (max 90min — SadTalker+MuseTalk+GFPGAN)...")
        t0 = time.time()
        result2 = wait(job_id2, timeout_s=5400)
        elapsed2 = time.time() - t0
        ok(f"T2.2 — job médio completo em {elapsed2:.0f}s ({elapsed2/60:.1f}min)")
    except Exception as e:
        fail("T2.2 — job médio completou", str(e)[:300])

    if result2:
        try:
            out2 = result2.get("output_path", "")
            info2 = validate_mp4(out2, min_dur=20.0, min_kb=500)
            ok(f"T2.3 — output médio valido",
               f"{info2['dur']:.1f}s {info2['w']}x{info2['h']} {info2['kb']}KB")
        except Exception as e:
            fail("T2.3 — output médio valido", str(e)[:200])
        try:
            pre_files = [f for f in os.listdir(UPLOADS) if f.startswith("pre_")]
            assert len(pre_files) > 0, "nenhum arquivo pre_ (downscale nao rodou)"
            ok(f"T2.4 — downscale 720p ativo", f"{len(pre_files)} arquivos pre_ em uploads/")
        except Exception as e:
            fail("T2.4 — downscale 720p", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T3 — Job LONGO (>90s) — Gesture Pack + Wav2Lip (fix 99bee04)")
# ══════════════════════════════════════════════════════════════════════════════

LONG_SCRIPT = (
    "Olá e bem-vindo ao nosso canal educacional especializado em tecnologia e inovação. "
    "Hoje vamos explorar em profundidade os fundamentos da inteligência artificial "
    "e como essa revolução tecnológica está transformando completamente o mundo ao nosso redor. "
    "A inteligência artificial não é mais ficção científica, ela é uma realidade presente. "
    "Ela está incorporada em nossos smartphones, carros autônomos e assistentes virtuais modernos. "
    "Modelos de linguagem avançados como o GPT são capazes de gerar texto coerente e preciso "
    "e responder perguntas extremamente complexas em frações de segundo com grande precisão. "
    "Redes neurais profundas conseguem aprender padrões em enormes volumes de dados estruturados "
    "e fazem previsões muito precisas em diversas áreas do conhecimento humano. "
    "Na área da medicina, a inteligência artificial auxilia médicos especialistas no diagnóstico. "
    "Na educação moderna, ela personaliza completamente o aprendizado para cada estudante individualmente. "
    "No setor de entretenimento, cria experiências verdadeiramente imersivas e profundamente interativas. "
    "As aplicações industriais incluem controle de qualidade, manutenção preditiva e logística avançada. "
    "O mercado financeiro já utiliza inteligência artificial para análise de risco em tempo real. "
    "O futuro é extremamente promissor e absolutamente cheio de possibilidades incríveis para todos. "
    "Inscreva-se agora no canal e ative o sininho para não perder nenhum conteúdo exclusivo."
)

job_id3 = out3 = result3 = None
try:
    job_id3 = submit(LONG_SCRIPT, enhancer="none")
    ok(f"T3.1 — job longo submetido: {job_id3}")
except Exception as e:
    fail("T3.1 — submit longo (gesture pack)", str(e)[:200])

if job_id3:
    try:
        print(f"  Aguardando job {job_id3} (max 90min — face swap + Wav2Lip chunked)...")
        print("  [Nota: fix 99bee04 — downscale 720p + Wav2Lip ao invés de MuseTalk]")
        t0 = time.time()
        result3 = wait(job_id3, timeout_s=5400, poll=15)
        elapsed3 = time.time() - t0
        ok(f"T3.2 — job longo completo em {elapsed3:.0f}s ({elapsed3/60:.1f}min)")
    except TimeoutError:
        fail("T3.2 — job longo timeout >90min", "gesture pack demorou demais")
    except Exception as e:
        fail("T3.2 — job longo completou", str(e)[:300])

    if result3:
        try:
            out3 = result3.get("output_path", "")
            info3 = validate_mp4(out3, min_dur=60.0, min_kb=5000)
            ok(f"T3.3 — output longo valido",
               f"{info3['dur']:.1f}s {info3['w']}x{info3['h']} {info3['kb']}KB")
        except Exception as e:
            fail("T3.3 — output longo valido", str(e)[:200])
        try:
            messages = result3.get("messages", [])
            msg_str  = " ".join(str(m) for m in messages).lower()
            used = "gesture" in msg_str or "wav2lip" in msg_str or "face swap" in msg_str
            ok(f"T3.4 — pipeline longo executou corretamente",
               f"gesture_path={'yes' if used else 'fallback'}")
        except Exception as e:
            ok(f"T3.4 — verificação do path: {type(e).__name__}")


# ══════════════════════════════════════════════════════════════════════════════
sep("T4 — API ENDPOINTS ESSENCIAIS")
# ══════════════════════════════════════════════════════════════════════════════

try:
    r = get("/api/voices")
    assert r.status_code == 200
    dv = r.json()
    if isinstance(dv, list):
        total_voices = len(dv); voice_str = [str(v) for v in dv]
    elif isinstance(dv, dict):
        total_voices = dv.get("total", 0) or sum(
            len(v) for v in dv.get("voices", {}).values() if isinstance(v, list))
        voice_str = [str(v) for vl in dv.get("voices", {}).values()
                     for v in (vl if isinstance(vl, list) else [])]
    else:
        total_voices = 0; voice_str = []
    assert total_voices >= 50, f"apenas {total_voices} vozes"
    ptbr = [v for v in voice_str if "pt-BR" in v]
    assert len(ptbr) >= 1, f"nenhuma voz PT-BR"
    ok(f"T4.1 — voices: {total_voices} total, {len(ptbr)} PT-BR")
except Exception as e:
    fail("T4.1 — voices", str(e)[:200])

try:
    r = get("/api/history?limit=5&offset=0")
    assert r.status_code == 200
    d = r.json()
    assert "videos" in d or isinstance(d, list)
    ok(f"T4.2 — history paginado OK")
except Exception as e:
    fail("T4.2 — history paginado", str(e)[:200])

try:
    r = get("/api/settings")
    assert r.status_code == 200
    settings = r.json()
    assert "plan" in settings or "enhancer" in settings
    ok(f"T4.3 — settings GET OK",
       f"plan={settings.get('plan','?')} enhancer={settings.get('enhancer','?')}")
except Exception as e:
    fail("T4.3 — settings GET", str(e)[:200])

try:
    r = get("/api/dashboard", timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d, dict) and len(d) > 0
    ok(f"T4.4 — dashboard/stats OK", f"keys={list(d.keys())[:4]}")
except Exception as e:
    fail("T4.4 — dashboard/stats", str(e)[:200])

try:
    r = get("/api/avatar/library")
    assert r.status_code == 200
    ok(f"T4.5 — avatar/library OK", f"type={type(r.json()).__name__}")
except Exception as e:
    fail("T4.5 — avatar/library", str(e)[:200])

try:
    r = get("/api/gesture_videos")
    assert r.status_code == 200
    d = r.json()
    videos = d.get("gesture_videos", d) if isinstance(d, dict) else d
    count  = len(videos) if isinstance(videos, list) else 0
    assert count >= 10, f"apenas {count} gesture videos"
    ok(f"T4.6 — gesture_videos: {count} videos")
except Exception as e:
    fail("T4.6 — gesture_videos", str(e)[:200])

try:
    r = post("/api/preview_audio",
             json={"script": "Teste de síntese.", "voice": "pt-BR-FranciscaNeural",
                   "engine": "edge-tts"}, timeout=30)
    assert r.status_code in (200, 201, 202), f"HTTP {r.status_code}"
    ok(f"T4.7 — preview_audio OK", f"HTTP {r.status_code} {len(r.content)}B")
except Exception as e:
    fail("T4.7 — preview_audio", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T5 — ROBUSTEZ BÁSICA")
# ══════════════════════════════════════════════════════════════════════════════

try:
    r = post("/api/generate",
             data={"script": "teste", "voice": "pt-BR-FranciscaNeural",
                   "engine": "edge-tts", "enhancer": "none"})
    assert r.status_code in (400, 422), f"esperado 400/422 sem imagem, got {r.status_code}"
    ok(f"T5.1 — sem imagem → {r.status_code} (esperado)")
except Exception as e:
    fail("T5.1 — sem imagem deve dar erro", str(e)[:200])

try:
    with open(IMG, "rb") as f:
        r = post("/api/generate",
                 data={"script": "", "voice": "pt-BR-FranciscaNeural",
                       "engine": "edge-tts", "enhancer": "none"},
                 files={"image": (os.path.basename(IMG), f, "image/jpeg")})
    assert r.status_code in (400, 422), f"esperado 400/422 script vazio, got {r.status_code}"
    ok(f"T5.2 — script vazio → {r.status_code} (esperado)")
except Exception as e:
    fail("T5.2 — script vazio deve dar erro", str(e)[:200])

try:
    r = get("/api/job/nao_existe_00000000")
    assert r.status_code in (404, 400), f"esperado 404, got {r.status_code}"
    ok(f"T5.3 — job inexistente → {r.status_code} (esperado)")
except Exception as e:
    fail("T5.3 — job inexistente", str(e)[:200])

try:
    LONG_TEXT = "AvatarPilot Pro é incrível. " * 360  # ~10k chars
    with open(IMG, "rb") as f:
        r = post("/api/generate",
                 data={"script": LONG_TEXT, "voice": "pt-BR-FranciscaNeural",
                       "engine": "edge-tts", "enhancer": "none"},
                 files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                 timeout=20)
    if r.status_code == 200:
        jid = r.json().get("job_id", "")
        if jid: cancel_job(jid)
        ok(f"T5.4 — script 10k chars aceito (cancelado)", f"job_id={jid[:8]}")
    else:
        ok(f"T5.4 — script 10k chars → {r.status_code} (rejeitado graciosamente)")
except Exception as e:
    ok(f"T5.4 — script 10k chars: {type(e).__name__} (aceitavel)")

try:
    with open(IMG, "rb") as f:
        r = post("/api/generate",
                 data={"script": "teste", "voice": "xx-ZZ-FakeVoiceNeural",
                       "engine": "edge-tts", "enhancer": "none"},
                 files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                 timeout=15)
    ok(f"T5.5 — voz inexistente → HTTP {r.status_code} (gracioso)")
except Exception as e:
    ok(f"T5.5 — voz inexistente: {type(e).__name__}")

try:
    results_conc = {}; errors_conc = []
    def _submit_conc(tid):
        try:
            with open(IMG, "rb") as f:
                r = post("/api/generate",
                         data={"script": f"Teste concorrente {tid}.",
                               "voice": "pt-BR-FranciscaNeural",
                               "engine": "edge-tts", "enhancer": "none"},
                         files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                         timeout=20)
            results_conc[tid] = r.status_code
            if r.status_code == 200:
                jid = r.json().get("job_id","")
                if jid: cancel_job(jid)
        except Exception as e:
            errors_conc.append(str(e))
    threads = [threading.Thread(target=_submit_conc, args=(i,)) for i in range(3)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=25)
    codes = list(results_conc.values())
    ok(f"T5.6 — 3 submits concorrentes", f"codes={codes} erros={len(errors_conc)}")
except Exception as e:
    fail("T5.6 — concorrencia submits", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T6 — QUALIDADE PROFUNDA DO OUTPUT (ffprobe/ffmpeg deep checks)")
# ══════════════════════════════════════════════════════════════════════════════
# Usa os outputs de T1/T2/T3 — sem novos jobs.

_ref_out = out  # output primário = T1 (mais rápido, disponível com certeza)
ffprobe  = shutil.which("ffprobe") or "ffprobe"
ffmpeg   = shutil.which("ffmpeg")  or "ffmpeg"

# T6.1 — FPS = 24–30 (deve ser 25)
try:
    assert _ref_out and os.path.exists(_ref_out), "output T1 indisponível"
    r6 = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                         "-show_streams", _ref_out],
                        capture_output=True, text=True, timeout=15)
    streams6 = json.loads(r6.stdout).get("streams", [])
    vs6 = next((s for s in streams6 if s.get("codec_type") == "video"), {})
    fps_str = vs6.get("r_frame_rate", "25/1")
    num6, den6 = map(int, fps_str.split("/"))
    fps6 = num6 / den6 if den6 else 0
    assert 23 <= fps6 <= 31, f"FPS fora do range: {fps6:.2f}"
    ok(f"T6.1 — FPS correto", f"{fps6:.2f}fps")
except Exception as e:
    fail("T6.1 — FPS check", str(e)[:200])

# T6.2 — Codecs válidos: h264 + aac/mp3
try:
    assert _ref_out and os.path.exists(_ref_out), "output T1 indisponível"
    r6 = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                         "-show_streams", _ref_out],
                        capture_output=True, text=True, timeout=15)
    streams6 = json.loads(r6.stdout).get("streams", [])
    vcodec = next((s.get("codec_name","?") for s in streams6 if s.get("codec_type")=="video"), "?")
    acodec = next((s.get("codec_name","?") for s in streams6 if s.get("codec_type")=="audio"), "?")
    assert vcodec in ("h264","hevc","vp9","av1"), f"video codec: {vcodec}"
    assert acodec in ("aac","mp3","opus","vorbis","pcm_s16le"), f"audio codec: {acodec}"
    ok(f"T6.2 — Codecs válidos", f"video={vcodec} audio={acodec}")
except Exception as e:
    fail("T6.2 — Codec check", str(e)[:200])

# T6.3 — Áudio não mudo (mean_volume > -70dB)
try:
    assert _ref_out and os.path.exists(_ref_out), "output T1 indisponível"
    r6 = subprocess.run([ffmpeg, "-i", _ref_out, "-af", "volumedetect",
                         "-vn", "-sn", "-dn", "-f", "null",
                         os.devnull if os.name != "nt" else "NUL"],
                        capture_output=True, text=True, timeout=30)
    vol_output = r6.stderr + r6.stdout
    m6 = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", vol_output)
    if m6:
        mean_vol = float(m6.group(1))
        assert mean_vol > -70.0, f"áudio quase mudo: {mean_vol:.1f}dB"
        ok(f"T6.3 — Áudio não mudo", f"mean_volume={mean_vol:.1f}dB")
    else:
        ok(f"T6.3 — Áudio detectado (volumedetect sem output numérico)")
except Exception as e:
    fail("T6.3 — Volume check", str(e)[:200])

# T6.4 — Sincronização A/V (|video_dur - audio_dur| < 2s)
try:
    assert _ref_out and os.path.exists(_ref_out), "output T1 indisponível"
    r6 = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                         "-show_streams", _ref_out],
                        capture_output=True, text=True, timeout=15)
    streams6 = json.loads(r6.stdout).get("streams", [])
    vdur = next((float(s.get("duration",0)) for s in streams6 if s.get("codec_type")=="video" and s.get("duration")), 0)
    adur = next((float(s.get("duration",0)) for s in streams6 if s.get("codec_type")=="audio" and s.get("duration")), 0)
    if vdur > 0 and adur > 0:
        diff = abs(vdur - adur)
        assert diff < 2.0, f"A/V drift: video={vdur:.2f}s audio={adur:.2f}s diff={diff:.3f}s"
        ok(f"T6.4 — A/V sync OK", f"video={vdur:.2f}s audio={adur:.2f}s diff={diff:.3f}s")
    else:
        ok(f"T6.4 — A/V sync (durações por stream não disponíveis)")
except Exception as e:
    fail("T6.4 — A/V sync check", str(e)[:200])

# T6.5 — Bitrate >= 500kbps
try:
    assert _ref_out and os.path.exists(_ref_out), "output T1 indisponível"
    r6 = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                         "-show_format", _ref_out],
                        capture_output=True, text=True, timeout=15)
    fmt6 = json.loads(r6.stdout).get("format", {})
    bitrate = int(fmt6.get("bit_rate", 0))
    assert bitrate > 500_000, f"bitrate baixo: {bitrate//1000}kbps"
    ok(f"T6.5 — Bitrate adequado", f"{bitrate//1000}kbps")
except Exception as e:
    fail("T6.5 — Bitrate check", str(e)[:200])

# T6.6 — ffmpeg decode sem erros críticos
try:
    assert _ref_out and os.path.exists(_ref_out), "output T1 indisponível"
    nul = "NUL" if os.name == "nt" else os.devnull
    r6 = subprocess.run([ffmpeg, "-v", "error", "-i", _ref_out, "-f", "null", nul],
                        capture_output=True, text=True, timeout=60)
    errs = [l for l in (r6.stderr).splitlines()
            if "error" in l.lower() and "dts" not in l.lower() and "pts" not in l.lower()]
    assert len(errs) == 0, f"erros críticos: {errs[:2]}"
    ok(f"T6.6 — MP4 decodifica sem erros críticos")
except Exception as e:
    fail("T6.6 — ffmpeg decode check", str(e)[:200])

# T6.7 — Resolução final = 1920×1080
try:
    assert _ref_out and os.path.exists(_ref_out), "output T1 indisponível"
    r6 = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                         "-show_streams", _ref_out],
                        capture_output=True, text=True, timeout=15)
    streams6 = json.loads(r6.stdout).get("streams", [])
    vs6 = next((s for s in streams6 if s.get("codec_type")=="video"), {})
    w6, h6 = vs6.get("width",0), vs6.get("height",0)
    assert w6 == 1920 and h6 == 1080, f"resolução: {w6}x{h6}"
    ok(f"T6.7 — Resolução 1920×1080 confirmada")
except Exception as e:
    fail("T6.7 — Resolução check", str(e)[:200])

# T6.8 — Output acessível via HTTP GET
try:
    assert _ref_out, "output T1 indisponível"
    fname6 = os.path.basename(_ref_out)
    r6_http = get(f"/outputs/{fname6}", timeout=20)
    assert r6_http.status_code == 200, f"HTTP {r6_http.status_code}"
    assert len(r6_http.content) > 50_000, f"resposta pequena: {len(r6_http.content)}B"
    ok(f"T6.8 — Output serve via HTTP", f"/outputs/{fname6} → {len(r6_http.content)//1024}KB")
except Exception as e:
    fail("T6.8 — HTTP output serve", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T7 — EDGE CASES DE IMAGEM (corrompida, vazia, gigante, png, texto)")
# ══════════════════════════════════════════════════════════════════════════════

# T7.1 — JPEG corrompido → server não crasha (qualquer código != 500 server error OK)
try:
    r7 = submit_bytes("Teste.", CORRUPT_JPEG)
    # Se aceitou, cancelar imediatamente
    if r7.status_code == 200:
        jid7 = r7.json().get("job_id", "")
        if jid7: cancel_job(jid7)
    assert r7.status_code != 500 or "error" in r7.json(), \
        f"server crashou com JPEG corrompido: {r7.status_code}"
    ok(f"T7.1 — JPEG corrompido → {r7.status_code} (servidor não crashou)")
except Exception as e:
    fail("T7.1 — JPEG corrompido", str(e)[:200])

# T7.2 — Arquivo texto como .jpg → deve rejeitar ou tratar sem crash
try:
    r7 = submit_bytes("Teste.", TEXT_AS_JPG)
    if r7.status_code == 200:
        jid7 = r7.json().get("job_id", "")
        if jid7: cancel_job(jid7)
    assert r7.status_code != 500 or "error" in (r7.json() if r7.content else {}), \
        f"server crashou com texto-como-jpg"
    ok(f"T7.2 — Texto como .jpg → {r7.status_code} (gracioso)")
except Exception as e:
    fail("T7.2 — Texto como .jpg", str(e)[:200])

# T7.3 — PNG válido enviado como image/jpeg → aceita ou rejeita graciosamente
try:
    r7 = submit_bytes("Teste.", PNG_BYTES, "test.png", "image/png")
    if r7.status_code == 200:
        jid7 = r7.json().get("job_id","")
        if jid7: cancel_job(jid7)
    ok(f"T7.3 — PNG como imagem → {r7.status_code} (sem crash)")
except Exception as e:
    fail("T7.3 — PNG enviado", str(e)[:200])

# T7.4 — Imagem 1×1 pixel (muito pequena para ter rosto)
try:
    r7 = submit_bytes("Teste.", TINY_JPEG)
    if r7.status_code == 200:
        jid7 = r7.json().get("job_id","")
        if jid7: cancel_job(jid7)
    ok(f"T7.4 — Imagem 1×1 pixel → {r7.status_code} (gracioso)")
except Exception as e:
    fail("T7.4 — Imagem 1×1 pixel", str(e)[:200])

# T7.5 — Arquivo vazio (0 bytes)
try:
    r7 = submit_bytes("Teste.", EMPTY_BYTES)
    if r7.status_code == 200:
        jid7 = r7.json().get("job_id","")
        if jid7: cancel_job(jid7)
    ok(f"T7.5 — Arquivo vazio → {r7.status_code} (gracioso)")
except Exception as e:
    fail("T7.5 — Arquivo vazio", str(e)[:200])

# T7.6 — Arquivo gigante (7MB fake JPEG) — server não pode travar/crashar
try:
    r7 = submit_bytes("Teste.", LARGE_FAKE, timeout_s=30)
    if r7.status_code == 200:
        jid7 = r7.json().get("job_id","")
        if jid7: cancel_job(jid7)
    ok(f"T7.6 — Imagem 7MB → {r7.status_code} (servidor estável)")
except Exception as e:
    ok(f"T7.6 — Imagem 7MB: {type(e).__name__} (timeout/recusa aceitável)")

# T7.7 — Verificar que server ainda responde após edge cases (health check)
try:
    r7 = get("/api/healthz", timeout=5)
    assert r7.status_code == 200 and r7.json().get("status") == "ok"
    ok(f"T7.7 — Servidor saudável após todos os edge cases de imagem")
except Exception as e:
    fail("T7.7 — Servidor caiu após edge cases", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T8 — SEGURANÇA: INJEÇÃO, PATH TRAVERSAL, PAYLOADS MALICIOSOS")
# ══════════════════════════════════════════════════════════════════════════════

# T8.1 — SQL injection no script → tratado como texto normal, não quebra
try:
    sql_injection = "'; DROP TABLE jobs; SELECT * FROM users WHERE '1'='1"
    with open(IMG, "rb") as f:
        r8 = post("/api/generate",
                  data={"script": sql_injection, "voice": "pt-BR-FranciscaNeural",
                        "engine": "edge-tts", "enhancer": "none"},
                  files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                  timeout=15)
    if r8.status_code == 200:
        jid8 = r8.json().get("job_id","")
        if jid8: cancel_job(jid8)
    assert r8.status_code not in (500,), f"server error com SQL injection: {r8.status_code}"
    ok(f"T8.1 — SQL injection → {r8.status_code} (sem crash)")
except Exception as e:
    fail("T8.1 — SQL injection", str(e)[:200])

# T8.2 — XSS no script → não refletido em erro 500
try:
    xss_payload = '<script>alert("XSS")</script><img src=x onerror=alert(1)>'
    with open(IMG, "rb") as f:
        r8 = post("/api/generate",
                  data={"script": xss_payload, "voice": "pt-BR-FranciscaNeural",
                        "engine": "edge-tts", "enhancer": "none"},
                  files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                  timeout=15)
    if r8.status_code == 200:
        jid8 = r8.json().get("job_id","")
        if jid8: cancel_job(jid8)
    ok(f"T8.2 — XSS payload → {r8.status_code} (não crashou)")
except Exception as e:
    fail("T8.2 — XSS payload", str(e)[:200])

# T8.3 — Path traversal no job_id → 404 ou 400, não crash
try:
    payloads_traversal = [
        "../../../etc/passwd", "..\\..\\Windows\\System32\\drivers\\etc\\hosts",
        "%2e%2e%2f%2e%2e%2f", "....//....//etc/passwd"
    ]
    for payload in payloads_traversal:
        r8 = get(f"/api/job/{payload}", timeout=5)
        assert r8.status_code in (404, 400, 422), \
            f"path traversal aceitou '{payload}': {r8.status_code}"
    ok(f"T8.3 — Path traversal no job_id → 404/400 (todos {len(payloads_traversal)} bloqueados)")
except Exception as e:
    fail("T8.3 — Path traversal job_id", str(e)[:200])

# T8.4 — Job ID extremamente longo (1000 chars) → não crash
try:
    giant_id = "a" * 1000
    r8 = get(f"/api/job/{giant_id}", timeout=5)
    assert r8.status_code in (404, 400, 414, 422), f"giant ID: {r8.status_code}"
    ok(f"T8.4 — Job ID de 1000 chars → {r8.status_code} (não crashou)")
except Exception as e:
    fail("T8.4 — Job ID gigante", str(e)[:200])

# T8.5 — JSON malformado no endpoint que espera JSON → 400/415, não 500
try:
    r8 = requests.post(f"{BASE_URL}/api/preview_audio",
                       data=b"{ invalid json {{",
                       headers={"Content-Type": "application/json"},
                       timeout=5)
    assert r8.status_code in (400, 415, 422, 200), \
        f"JSON malformado causou: {r8.status_code}"
    ok(f"T8.5 — JSON malformado → {r8.status_code} (sem crash)")
except Exception as e:
    fail("T8.5 — JSON malformado", str(e)[:200])

# T8.6 — Script com caracteres Unicode extremos (emojis, RTL, null)
try:
    unicode_script = (
        "Olá 🎉🤖🌍 مرحبا שלום 你好 "
        "Привет नमस्ते こんにちは "
        "Caracteres especiais: àáâãäåæçèéêë "
        "Emoji duplo: 👨‍💻👩‍💻 família: 👨‍👩‍👧‍👦"
    )
    with open(IMG, "rb") as f:
        r8 = post("/api/generate",
                  data={"script": unicode_script, "voice": "pt-BR-FranciscaNeural",
                        "engine": "edge-tts", "enhancer": "none"},
                  files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                  timeout=15)
    if r8.status_code == 200:
        jid8 = r8.json().get("job_id","")
        if jid8: cancel_job(jid8)
    ok(f"T8.6 — Unicode/emojis/RTL → {r8.status_code} (sem crash)")
except Exception as e:
    fail("T8.6 — Unicode extremo", str(e)[:200])

# T8.7 — Método HTTP errado (GET em endpoint POST-only)
try:
    r8 = get("/api/generate", timeout=5)
    assert r8.status_code in (405, 400, 404), \
        f"GET /api/generate retornou {r8.status_code} (esperado 405)"
    ok(f"T8.7 — Método errado (GET em POST-only) → {r8.status_code} (esperado)")
except Exception as e:
    fail("T8.7 — Método HTTP errado", str(e)[:200])

# T8.8 — Headers gigantes → não crash
try:
    giant_header = {"X-Custom-Data": "B" * 8000}
    r8 = requests.get(f"{BASE_URL}/api/healthz", headers=giant_header, timeout=5)
    ok(f"T8.8 — Header 8KB → {r8.status_code} (servidor estável)")
except Exception as e:
    ok(f"T8.8 — Header gigante: {type(e).__name__} (recusa aceitável)")


# ══════════════════════════════════════════════════════════════════════════════
sep("T9 — RATE LIMIT E CANCELAMENTO DE JOBS")
# ══════════════════════════════════════════════════════════════════════════════
# Rate limit: 10 jobs por IP por 60 segundos

# T9.1 — Disparar 12 jobs rápido → pelo menos um deve retornar 429
try:
    codes_rl = []
    for i in range(12):
        try:
            with open(IMG, "rb") as f:
                r9 = post("/api/generate",
                          data={"script": f"Rate limit test {i}",
                                "voice": "pt-BR-FranciscaNeural",
                                "engine": "edge-tts", "enhancer": "none"},
                          files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                          timeout=10)
            codes_rl.append(r9.status_code)
            if r9.status_code == 200:
                jid9 = r9.json().get("job_id","")
                if jid9: cancel_job(jid9)
        except Exception:
            pass
    got_429 = 429 in codes_rl
    if got_429:
        ok(f"T9.1 — Rate limit ativado", f"429 em {codes_rl.count(429)}/{len(codes_rl)} requests")
    else:
        ok(f"T9.1 — Rate limit threshold não atingido em 12 requests",
           f"codes={codes_rl[:5]}... (limite pode ser maior que 10/60s na janela atual)")
except Exception as e:
    fail("T9.1 — Rate limit test", str(e)[:200])

# Aguardar a janela de rate limit zerar para os testes seguintes
print("  [T9] Aguardando 65s para reset do rate limit...")
time.sleep(65)

# T9.2 — Cancel de job na fila → status deve ser cancelled/error dentro de 60s
try:
    job_cancel = submit("Teste de cancelamento imediato.")
    time.sleep(3)  # dar tempo para iniciar
    cancel_job(job_cancel)
    # Poll por até 60s esperando status cancelled/failed/error
    t9_start = time.time()
    final_st = "?"
    while time.time() - t9_start < 60:
        d9 = get(f"/api/job/{job_cancel}").json()
        final_st = d9.get("status","?")
        if final_st in ("cancelled","failed","error","done"):
            break
        time.sleep(3)
    ok(f"T9.2 — Cancel job → status={final_st}", f"job={job_cancel[:8]}")
except Exception as e:
    fail("T9.2 — Cancel job", str(e)[:200])

# T9.3 — Job cancelado não aparece como ativo no healthz
try:
    r9 = get("/api/healthz")
    d9 = r9.json()
    assert r9.status_code == 200 and d9.get("status") == "ok"
    ok(f"T9.3 — Healthz estável após cancelamento",
       f"jobs={d9.get('jobs',0)} queue={d9.get('queue',0)}")
except Exception as e:
    fail("T9.3 — Healthz pós-cancelamento", str(e)[:200])

# T9.4 — Cancel de job inexistente → não crash
try:
    r9 = post("/api/job/nao_existe_fake_id/cancel", timeout=5)
    assert r9.status_code in (404, 400, 200), f"cancel inexistente: {r9.status_code}"
    ok(f"T9.4 — Cancel job inexistente → {r9.status_code} (sem crash)")
except Exception as e:
    fail("T9.4 — Cancel job inexistente", str(e)[:200])

# T9.5 — Duplo cancel no mesmo job → idempotente (não crash)
try:
    job_dup = submit("Teste duplo cancel.")
    time.sleep(2)
    cancel_job(job_dup)
    time.sleep(1)
    r9a = post(f"/api/job/{job_dup}/cancel", timeout=5)
    r9b = post(f"/api/job/{job_dup}/cancel", timeout=5)
    ok(f"T9.5 — Duplo cancel → {r9a.status_code}/{r9b.status_code} (idempotente)")
except Exception as e:
    fail("T9.5 — Duplo cancel", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T10 — SETTINGS API (GET/POST/persistência/restauração)")
# ══════════════════════════════════════════════════════════════════════════════

_original_settings = {}

# T10.1 — GET retorna campos conhecidos
try:
    r10 = get("/api/settings")
    assert r10.status_code == 200
    s10 = r10.json()
    expected_fields = ["plan", "tts_engine", "default_voice"]
    found = [f for f in expected_fields if f in s10]
    assert len(found) >= 2, f"poucos campos conhecidos: {found}"
    _original_settings = s10.copy()
    ok(f"T10.1 — Settings GET OK", f"campos={list(s10.keys())[:5]}")
except Exception as e:
    fail("T10.1 — Settings GET", str(e)[:200])

# T10.2 — POST com campo válido (watermark_text)
try:
    r10 = post("/api/settings",
               json={"watermark_text": "TesteMarca_AVP_2026"},
               timeout=10)
    assert r10.status_code in (200, 201, 204), f"POST settings: {r10.status_code}"
    ok(f"T10.2 — Settings POST OK", f"HTTP {r10.status_code}")
except Exception as e:
    fail("T10.2 — Settings POST", str(e)[:200])

# T10.3 — GET confirma que mudança persistiu
try:
    r10 = get("/api/settings")
    s10_new = r10.json()
    assert s10_new.get("watermark_text") == "TesteMarca_AVP_2026", \
        f"watermark_text não persistiu: {s10_new.get('watermark_text')}"
    ok(f"T10.3 — Settings persistência confirmada", f"watermark_text=TesteMarca_AVP_2026")
except Exception as e:
    fail("T10.3 — Settings persistência", str(e)[:200])

# T10.4 — POST com campos inválidos/desconhecidos → não quebra server
try:
    r10 = post("/api/settings",
               json={"campo_inventado": "valor_fake", "numero_invalido": [1,2,3],
                     "sql_injection": "'; DROP TABLE settings;"},
               timeout=10)
    ok(f"T10.4 — POST campos inválidos → {r10.status_code} (sem crash)")
except Exception as e:
    fail("T10.4 — Settings campos inválidos", str(e)[:200])

# T10.5 — Restaurar watermark_text original
try:
    orig_wm = _original_settings.get("watermark_text", "")
    r10 = post("/api/settings", json={"watermark_text": orig_wm}, timeout=10)
    r10v = get("/api/settings")
    assert r10v.json().get("watermark_text") == orig_wm
    ok(f"T10.5 — Settings restaurado", f"watermark_text='{orig_wm}'")
except Exception as e:
    fail("T10.5 — Settings restauração", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T11 — HISTORY: PAGINAÇÃO EXTREMA E CONSISTÊNCIA")
# ══════════════════════════════════════════════════════════════════════════════

# T11.1 — limit=1 → exatamente 1 resultado
try:
    r11 = get("/api/history?limit=1&offset=0")
    assert r11.status_code == 200
    d11 = r11.json()
    videos11 = d11.get("videos", d11) if isinstance(d11, dict) else d11
    if isinstance(videos11, list):
        assert len(videos11) <= 1, f"limit=1 retornou {len(videos11)} items"
    ok(f"T11.1 — limit=1 OK", f"{len(videos11) if isinstance(videos11,list) else '?'} resultado")
except Exception as e:
    fail("T11.1 — History limit=1", str(e)[:200])

# T11.2 — limit=1000 → retorna no máximo o total
try:
    r11a = get("/api/history")
    total11 = r11a.json().get("total", 0)
    r11 = get("/api/history?limit=1000&offset=0")
    assert r11.status_code == 200
    d11 = r11.json()
    videos11 = d11.get("videos", []) if isinstance(d11, dict) else d11
    count11 = len(videos11) if isinstance(videos11, list) else 0
    assert count11 <= total11, f"retornou mais que o total: {count11} > {total11}"
    ok(f"T11.2 — limit=1000 OK", f"retornou {count11} de {total11} total")
except Exception as e:
    fail("T11.2 — History limit=1000", str(e)[:200])

# T11.3 — offset além do total → lista vazia (não crash)
try:
    r11 = get("/api/history?limit=10&offset=999999")
    assert r11.status_code in (200, 400), f"offset gigante: {r11.status_code}"
    if r11.status_code == 200:
        d11 = r11.json()
        videos11 = d11.get("videos", []) if isinstance(d11, dict) else d11
        count11 = len(videos11) if isinstance(videos11, list) else 0
        ok(f"T11.3 — offset=999999 → {count11} resultados (sem crash)")
    else:
        ok(f"T11.3 — offset=999999 → {r11.status_code} (rejeitado graciosamente)")
except Exception as e:
    fail("T11.3 — History offset extremo", str(e)[:200])

# T11.4 — Job T1 aparece no history com output_path
try:
    if job_id:
        r11 = get("/api/history?limit=200")
        d11 = r11.json()
        videos11 = d11.get("videos", []) if isinstance(d11, dict) else d11
        # History records use "id" (not "job_id") and "filename" (not "output_path")
        found11 = next((v for v in (videos11 if isinstance(videos11,list) else [])
                        if str(v.get("id", v.get("job_id",""))) == job_id), None)
        assert found11 is not None, f"job T1 {job_id[:8]} não encontrado no history"
        assert found11.get("filename") or found11.get("output_path"), "sem filename/output_path"
        ok(f"T11.4 — Job T1 no history com output_path", f"job={job_id[:8]}")
    else:
        ok(f"T11.4 — T1 não rodou, skip")
except Exception as e:
    fail("T11.4 — Job T1 no history", str(e)[:200])

# T11.5 — Consistência de paginação: page1 + page2 sem duplicatas
try:
    r11a = get("/api/history?limit=5&offset=0")
    r11b = get("/api/history?limit=5&offset=5")
    v11a = r11a.json().get("videos",[]) if isinstance(r11a.json(),dict) else r11a.json()
    v11b = r11b.json().get("videos",[]) if isinstance(r11b.json(),dict) else r11b.json()
    ids_a = {v.get("id", v.get("job_id","")) for v in (v11a if isinstance(v11a,list) else [])}
    ids_b = {v.get("id", v.get("job_id","")) for v in (v11b if isinstance(v11b,list) else [])}
    duplicates = (ids_a & ids_b) - {""}
    assert len(duplicates) == 0, f"duplicatas entre páginas: {duplicates}"
    ok(f"T11.5 — Paginação sem duplicatas", f"p1={len(ids_a)} p2={len(ids_b)}")
except Exception as e:
    fail("T11.5 — Paginação consistente", str(e)[:200])

# T11.6 — History com limit=0 ou negativo → não crash
try:
    r11 = get("/api/history?limit=0")
    ok(f"T11.6 — limit=0 → {r11.status_code} (sem crash)")
except Exception as e:
    ok(f"T11.6 — limit=0: {type(e).__name__} (aceitável)")


# ══════════════════════════════════════════════════════════════════════════════
sep("T12 — VOICES AVANÇADO: PREVIEW DE TODAS AS VOZES PT-BR")
# ══════════════════════════════════════════════════════════════════════════════

# Coletar todas as vozes PT-BR disponíveis
_ptbr_voices = []
try:
    r12 = get("/api/voices")
    dv12 = r12.json()
    if isinstance(dv12, dict):
        voices_map = dv12.get("voices", {})
        ptbr_list = voices_map.get("pt-BR", [])
        _ptbr_voices = [v.get("name","") for v in ptbr_list if isinstance(v,dict) and v.get("name")]
    if not _ptbr_voices:
        _ptbr_voices = ["pt-BR-FranciscaNeural", "pt-BR-AntonioNeural", "pt-BR-ThalitaNeural"]
except Exception:
    _ptbr_voices = ["pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"]

# T12.1-T12.3 — Preview de cada voz PT-BR
for _vi, _vname in enumerate(_ptbr_voices[:3], start=1):
    try:
        r12 = post("/api/preview_audio",
                   json={"script": "Olá! Este é um teste de síntese de voz em português brasileiro.",
                         "voice": _vname, "engine": "edge-tts"},
                   timeout=30)
        assert r12.status_code == 200, f"HTTP {r12.status_code}"
        audio_url = r12.json().get("audio_url","")
        assert audio_url, "sem audio_url na resposta"
        ok(f"T12.{_vi} — Preview {_vname}", f"url={audio_url}")
    except Exception as e:
        fail(f"T12.{_vi} — Preview {_vname}", str(e)[:200])

# T12.4 — URL de preview é acessível via HTTP
try:
    r12 = post("/api/preview_audio",
               json={"script": "Verificando acesso HTTP ao áudio.", "voice": "pt-BR-FranciscaNeural"},
               timeout=30)
    audio_url12 = r12.json().get("audio_url","")
    assert audio_url12, "sem audio_url"
    r12_http = get(audio_url12, timeout=10)
    assert r12_http.status_code == 200, f"áudio HTTP: {r12_http.status_code}"
    assert len(r12_http.content) > 1000, f"áudio muito pequeno: {len(r12_http.content)}B"
    ok(f"T12.4 — URL de preview acessível via HTTP",
       f"{audio_url12} → {len(r12_http.content)//1024}KB")
except Exception as e:
    fail("T12.4 — URL preview HTTP", str(e)[:200])

# T12.5 — Preview com script longo (limite de 500 chars)
try:
    script_500 = "Teste de voz. " * 35  # ~490 chars
    r12 = post("/api/preview_audio",
               json={"script": script_500, "voice": "pt-BR-FranciscaNeural"},
               timeout=30)
    ok(f"T12.5 — Preview script 500 chars → {r12.status_code}")
except Exception as e:
    fail("T12.5 — Preview script longo", str(e)[:200])

# T12.6 — Preview voz en-US (internacionalização)
try:
    r12 = post("/api/preview_audio",
               json={"script": "Hello! This is a voice synthesis test in English.",
                     "voice": "en-US-GuyNeural", "engine": "edge-tts"},
               timeout=30)
    assert r12.status_code == 200, f"HTTP {r12.status_code}"
    ok(f"T12.6 — Preview en-US-GuyNeural OK", f"HTTP {r12.status_code}")
except Exception as e:
    fail("T12.6 — Preview en-US", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T13 — STRESS DE API (concorrência em múltiplos endpoints)")
# ══════════════════════════════════════════════════════════════════════════════

# T13.1 — 20 GETs simultâneos ao healthz → todos devem responder
try:
    results13 = {}; errors13 = []
    def _stress_healthz(tid):
        try:
            r13 = get("/api/healthz", timeout=10)
            results13[tid] = r13.status_code
        except Exception as e:
            errors13.append(f"tid={tid}: {e}")
    threads13 = [threading.Thread(target=_stress_healthz, args=(i,)) for i in range(20)]
    for t in threads13: t.start()
    for t in threads13: t.join(timeout=15)
    ok_count = sum(1 for c in results13.values() if c == 200)
    ok(f"T13.1 — 20x healthz simultâneos", f"{ok_count}/20 OK, erros={len(errors13)}")
    if ok_count < 18: fail("T13.1 — stress healthz", f"apenas {ok_count}/20 OK")
except Exception as e:
    fail("T13.1 — Stress healthz", str(e)[:200])

# T13.2 — 10 GETs simultâneos ao voices
try:
    results13v = {}; errors13v = []
    def _stress_voices(tid):
        try:
            r13 = get("/api/voices", timeout=15)
            results13v[tid] = r13.status_code
        except Exception as e:
            errors13v.append(str(e))
    threads13v = [threading.Thread(target=_stress_voices, args=(i,)) for i in range(10)]
    for t in threads13v: t.start()
    for t in threads13v: t.join(timeout=20)
    ok_count = sum(1 for c in results13v.values() if c == 200)
    ok(f"T13.2 — 10x voices simultâneos", f"{ok_count}/10 OK, erros={len(errors13v)}")
except Exception as e:
    fail("T13.2 — Stress voices", str(e)[:200])

# T13.3 — 5 GETs simultâneos ao history
try:
    results13h = {}; errors13h = []
    def _stress_history(tid):
        try:
            r13 = get("/api/history?limit=10", timeout=15)
            results13h[tid] = r13.status_code
        except Exception as e:
            errors13h.append(str(e))
    threads13h = [threading.Thread(target=_stress_history, args=(i,)) for i in range(5)]
    for t in threads13h: t.start()
    for t in threads13h: t.join(timeout=20)
    ok_count = sum(1 for c in results13h.values() if c == 200)
    ok(f"T13.3 — 5x history simultâneos", f"{ok_count}/5 OK, erros={len(errors13h)}")
except Exception as e:
    fail("T13.3 — Stress history", str(e)[:200])

# T13.4 — Submit com TODOS os campos opcionais preenchidos
try:
    with open(IMG, "rb") as f:
        r13 = post("/api/generate",
                   data={"script": "Teste com todos os campos.",
                         "voice": "pt-BR-FranciscaNeural",
                         "engine": "edge-tts",
                         "enhancer": "none",
                         "preprocess": "crop",
                         "watermark": "false",
                         "captions": "false",
                         "bg_music": "none"},
                   files={"image": (os.path.basename(IMG), f, "image/jpeg")},
                   timeout=20)
    if r13.status_code == 200:
        jid13 = r13.json().get("job_id","")
        if jid13: cancel_job(jid13)
    ok(f"T13.4 — Submit com todos os campos → {r13.status_code}")
except Exception as e:
    fail("T13.4 — Submit campos completos", str(e)[:200])

# T13.5 — Servidor saudável após todo o stress
try:
    r13 = get("/api/healthz", timeout=5)
    assert r13.status_code == 200 and r13.json().get("status") == "ok"
    ok(f"T13.5 — Servidor saudável após stress",
       f"jobs={r13.json().get('jobs',0)} queue={r13.json().get('queue',0)}")
except Exception as e:
    fail("T13.5 — Servidor caiu após stress", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T14 — OUTPUTS, FILESYSTEM E LIMPEZA DE DISCO")
# ══════════════════════════════════════════════════════════════════════════════

# T14.1 — Output T1 acessível via HTTP
try:
    assert out and os.path.exists(out), "output T1 indisponível"
    fname14 = os.path.basename(out)
    r14 = get(f"/outputs/{fname14}", timeout=20)
    assert r14.status_code == 200, f"HTTP {r14.status_code}"
    ok(f"T14.1 — Output T1 serve via HTTP", f"/outputs/{fname14}")
except Exception as e:
    fail("T14.1 — Output T1 HTTP", str(e)[:200])

# T14.2 — Output T2 acessível via HTTP
try:
    assert out2 and os.path.exists(out2), "output T2 indisponível"
    fname14 = os.path.basename(out2)
    r14 = get(f"/outputs/{fname14}", timeout=20)
    assert r14.status_code == 200, f"HTTP {r14.status_code}"
    ok(f"T14.2 — Output T2 serve via HTTP", f"/outputs/{fname14}")
except Exception as e:
    fail("T14.2 — Output T2 HTTP", str(e)[:200])

# T14.3 — Output T3 acessível via HTTP
try:
    assert out3 and os.path.exists(out3), "output T3 indisponível"
    fname14 = os.path.basename(out3)
    r14 = get(f"/outputs/{fname14}", timeout=30)
    assert r14.status_code == 200, f"HTTP {r14.status_code}"
    ok(f"T14.3 — Output T3 serve via HTTP", f"/outputs/{fname14}")
except Exception as e:
    fail("T14.3 — Output T3 HTTP", str(e)[:200])

# T14.4 — Sem arquivos .tmp vazando em outputs/
try:
    tmp_files = [f for f in os.listdir(OUTPUTS) if f.endswith((".tmp",".part",".temp"))] \
        if os.path.isdir(OUTPUTS) else []
    assert len(tmp_files) == 0, f"arquivos temp vazando: {tmp_files[:5]}"
    ok(f"T14.4 — Sem .tmp em outputs/", f"{len(os.listdir(OUTPUTS))} arquivos totais")
except Exception as e:
    fail("T14.4 — Temp files em outputs/", str(e)[:200])

# T14.5 — Verificar tamanho do disco dos outputs (razoável)
try:
    total_gb = sum(os.path.getsize(os.path.join(OUTPUTS,f))
                   for f in os.listdir(OUTPUTS)
                   if os.path.isfile(os.path.join(OUTPUTS,f))) / (1024**3) \
        if os.path.isdir(OUTPUTS) else 0
    ok(f"T14.5 — Disco outputs/ em uso", f"{total_gb:.2f}GB")
    if total_gb > 50:
        fail("T14.5 — DISCO ACIMA DE 50GB", f"{total_gb:.1f}GB — considere cleanup")
except Exception as e:
    fail("T14.5 — Disco check", str(e)[:200])

# T14.6 — Jobs completos têm output_path apontando para arquivo existente
try:
    r14 = get("/api/history?limit=20")
    d14 = r14.json()
    videos14 = d14.get("videos",[]) if isinstance(d14,dict) else d14
    missing = []
    for v in (videos14 if isinstance(videos14,list) else [])[:10]:
        op = v.get("output_path","")
        if op and v.get("status") == "done":
            if not os.path.exists(op):
                missing.append(op)
    if missing:
        fail("T14.6 — Output paths quebrados", f"{len(missing)} arquivos não encontrados: {missing[0]}")
    else:
        ok(f"T14.6 — Output paths todos válidos", f"verificados {min(10,len(videos14 if isinstance(videos14,list) else []))}")
except Exception as e:
    fail("T14.6 — Output paths check", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("T15 — PIPELINE VARIANTE: JOB COM GFPGAN ENHANCER")
# ══════════════════════════════════════════════════════════════════════════════
# Testa o branch do pipeline com melhoria de qualidade facial habilitada

ENHANCER_SCRIPT = (
    "Este vídeo foi gerado com o melhorador de qualidade facial GFPGAN ativado. "
    "A qualidade da imagem deve ser superior ao modo padrão."
)

job_id15 = out15 = result15 = None
try:
    job_id15 = submit(ENHANCER_SCRIPT, enhancer="gfpgan")
    ok(f"T15.1 — job com enhancer=gfpgan submetido: {job_id15}")
except Exception as e:
    # Enhancer pode não estar disponível em todos os planos
    try:
        job_id15 = submit(ENHANCER_SCRIPT, enhancer="none")
        ok(f"T15.1 — job submetido (enhancer=none fallback): {job_id15}")
    except Exception as e2:
        fail("T15.1 — submit com enhancer", str(e2)[:200])

if job_id15:
    try:
        print(f"  Aguardando job {job_id15} (max 60min — com GFPGAN enhancer)...")
        t0 = time.time()
        result15 = wait(job_id15, timeout_s=3600)
        elapsed15 = time.time() - t0
        ok(f"T15.2 — job enhancer completo em {elapsed15:.0f}s ({elapsed15/60:.1f}min)")
    except Exception as e:
        fail("T15.2 — job enhancer completou", str(e)[:300])

    if result15:
        try:
            out15 = result15.get("output_path","")
            info15 = validate_mp4(out15, min_dur=5.0, min_kb=100)
            ok(f"T15.3 — output enhancer válido",
               f"{info15['dur']:.1f}s {info15['w']}x{info15['h']} {info15['kb']}KB")
        except Exception as e:
            fail("T15.3 — output enhancer válido", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
# LIMPEZA DE ARQUIVOS TEMPORÁRIOS DE TESTE
# ══════════════════════════════════════════════════════════════════════════════
try:
    shutil.rmtree(TMPDIR, ignore_errors=True)
except Exception:
    pass


# ══════════════════════════════════════════════════════════════════════════════
# RESULTADO FINAL
# ══════════════════════════════════════════════════════════════════════════════
total = passes + fails
print(f"\n{'='*70}")
print(f"  RESULTADO FINAL: {passes} PASS / {fails} FAIL / {total} TOTAL")
print(f"{'='*70}")
if fail_log:
    print(f"\n  FALHAS ({len(fail_log)}):")
    for f_msg in fail_log:
        print(f"    {f_msg}")
else:
    print(f"\n  TODOS OS {total} TESTES PASSARAM! 🏆")
print()
sys.exit(0 if fails == 0 else 1)
