"""
Teste end-to-end de F5-TTS voice cloning:

1. Gera audio de referencia via Edge-TTS preview (~15s, voz pt-BR-Antonio)
2. Registra via /api/voices/f5_clone com nome 'antonio_clonado'
3. Sintetiza NOVO texto via /api/preview_audio com engine=f5_tts + voice_ref
   (ou via /api/generate se preview nao suportar f5)
4. Baixa o output, valida tamanho/duracao

Uso: python test_f5_voice_clone.py
"""
import sys, os, io, time, json, requests, subprocess, shutil

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:5052"
def G(p, **k): return requests.get(f"{BASE}{p}", timeout=k.pop("timeout", 15), **k)
def P(p, **k): return requests.post(f"{BASE}{p}", timeout=k.pop("timeout", 60), **k)

passes = fails = 0
def ok(n, d=""):
    global passes; passes += 1; print(f"  [PASS] {n}" + (f" [{d}]" if d else ""))
def fail(n, d=""):
    global fails; fails += 1; print(f"  [FAIL] {n}: {d}")

print("=== F5-TTS Voice Clone End-to-End Test ===\n")

# 1. Servidor UP
try:
    assert G("/api/healthz").status_code == 200
    ok("Servidor UP")
except Exception as e:
    print(f"servidor offline: {e}"); sys.exit(1)

# 2. Gerar audio de referencia via preview_audio (Edge-TTS) ~15s
ref_text = ("Ola, eu sou o Antonio. Esta gravacao serve como referencia para clonar minha voz "
            "usando inteligencia artificial. A qualidade depende muito do audio limpo.")
print(f"\nETAPA 1: gerar audio de referencia (Edge-TTS, voz pt-BR-Antonio)...")
try:
    r = P("/api/preview_audio",
          json={"script": ref_text, "voice": "pt-BR-AntonioNeural", "engine": "edge-tts"},
          timeout=30)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:120]}"
    audio_url = r.json().get("audio_url", "")
    assert audio_url, "sem audio_url na resposta"
    rb = G(audio_url, timeout=20)
    assert rb.status_code == 200 and len(rb.content) > 5000
    ref_audio_bytes = rb.content
    ok("Audio de referencia gerado", f"{len(ref_audio_bytes)//1024} KB via Edge-TTS")
except Exception as e:
    fail("Audio referencia", str(e)[:150]); sys.exit(1)

# 3. Registrar como voz clonada via F5
print(f"\nETAPA 2: registrar audio como voz clonada via F5-TTS...")
try:
    r = P("/api/voices/f5_clone",
          data={"name": "antonio_clonado_teste", "ref_text": ref_text},
          files={"audio": ("ref.mp3", io.BytesIO(ref_audio_bytes), "audio/mpeg")},
          timeout=30)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    voice_ref_id    = body.get("voice_ref_id", "")
    voice_ref_audio = body.get("voice_ref_audio", "")
    assert voice_ref_id and voice_ref_audio
    ok("Voz registrada", f"id={voice_ref_id} dur={body.get('duration')}s")
except Exception as e:
    fail("Registrar voz F5", str(e)[:200]); sys.exit(1)

# 4. Sintetizar NOVO texto com a voz clonada (caminho preview_audio nao suporta
#    f5, entao testamos via /api/generate em modo audio-only)
new_text = "Este novo audio foi gerado pela voz clonada via F5-TTS. Compare comigo o original."
print(f"\nETAPA 3: sintetizar '{new_text[:50]}...' com voz clonada...")
try:
    # Submeter generate em modo apenas-audio nao existe diretamente; usar tts_engine=f5_tts
    # com uma imagem dummy e cancelar logo no audio. Mas isso desperdicaria GPU.
    # Alternativa: testar a funcao f5_tts_generate diretamente via Python.
    # Como o endpoint /api/voices/f5_clone aceita registro mas nao gera sintese sozinho,
    # vou chamar o helper direto via subprocess pra evitar pipeline completo.
    print("  (chamando f5_tts_generate direto via Python — sem pipeline GPU)")
    import tempfile
    out_wav = os.path.join(tempfile.gettempdir(), f"f5_test_out.wav")
    cmd = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv311", "Scripts", "python.exe"),
        "-c", (
            "import sys, os; "
            f"sys.path.insert(0, r'{os.path.dirname(os.path.abspath(__file__))}'); "
            "from server import f5_tts_generate; "
            f"f5_tts_generate({new_text!r}, {voice_ref_audio!r}, {out_wav!r}, "
            f"ref_text={ref_text!r})"
        )
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
    elapsed = time.time() - t0
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[-500:]
        fail("F5 generate", f"code {proc.returncode} em {elapsed:.0f}s: {err[:300]}")
    elif os.path.exists(out_wav) and os.path.getsize(out_wav) > 5000:
        # ffprobe pra duracao
        ffp = shutil.which("ffprobe") or "ffprobe"
        pr = subprocess.run([ffp, "-v", "quiet", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", out_wav],
                            capture_output=True, text=True, timeout=10)
        dur = float(pr.stdout.strip() or 0)
        sz = os.path.getsize(out_wav) // 1024
        ok("F5 sintetizou audio", f"{dur:.1f}s {sz}KB em {elapsed:.0f}s")
        print(f"\n      Output: {out_wav}")
    else:
        fail("F5 generate", f"sem output ou muito pequeno")
except subprocess.TimeoutExpired:
    fail("F5 generate", "timeout 10min")
except Exception as e:
    fail("F5 generate", str(e)[:200])

# Cleanup
try:
    if voice_ref_audio and os.path.exists(voice_ref_audio):
        # nao remove — fica disponivel pra uso futuro
        pass
except: pass

total = passes + fails
print(f"\n{'='*50}\n  F5-TTS — {passes} PASS / {fails} FAIL\n{'='*50}")
sys.exit(0 if fails == 0 else 1)
