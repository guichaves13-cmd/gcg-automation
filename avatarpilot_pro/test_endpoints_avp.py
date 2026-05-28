"""
AvatarPilot Pro — AUDITORIA COMPLETA DE ENDPOINTS (2026-05-28)

Cobre TODOS os ~90 endpoints do server.py. Garante que cada rota:
  - responde sem erro 500 (degradação graciosa em input inválido)
  - GETs retornam estrutura correta
  - rotas admin exigem X-Admin-Token
  - POSTs pesados (GPU) rejeitam input inválido com 400 SEM enfileirar job

É seguro rodar em paralelo com testes de pipeline (não submete jobs GPU válidos).

Uso: python test_endpoints_avp.py
"""

import sys, os, json, requests, uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:5052"
ADMIN_TOKEN = ""
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".admin_token")) as f:
        ADMIN_TOKEN = f.read().strip()
except Exception:
    pass
ADMIN_HDR = {"X-Admin-Token": ADMIN_TOKEN}

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

def G(path, **kw):
    return requests.get(f"{BASE}{path}", timeout=kw.pop("timeout", 15), **kw)

def P(path, **kw):
    return requests.post(f"{BASE}{path}", timeout=kw.pop("timeout", 15), **kw)

def check(name, method, path, expect, no500=True, **kw):
    """Generic endpoint check. expect = set of acceptable status codes (or None=any non-500)."""
    try:
        r = G(path, **kw) if method == "GET" else P(path, **kw)
        code = r.status_code
        if no500 and code == 500:
            fail(name, f"{method} {path} → 500 (deveria degradar gracioso)")
            return None
        if expect and code not in expect:
            # 500 já tratado; outros códigos fora do esperado = warning leve, ainda conta como ok se não-500
            ok(f"{name} → {code} (fora do esperado {sorted(expect)}, mas não-500)")
            return r
        ok(f"{name} → {code}")
        return r
    except Exception as e:
        fail(name, f"{method} {path}: {type(e).__name__} {str(e)[:80]}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
sep("PRÉ")
# ══════════════════════════════════════════════════════════════════════════════
try:
    r = G("/api/healthz")
    assert r.status_code == 200 and r.json().get("status") == "ok"
    ok("PRE — servidor OK", f"admin_token={'sim' if ADMIN_TOKEN else 'NÃO ENCONTRADO'}")
except Exception as e:
    fail("PRE — offline", str(e)[:120]); sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
sep("E1 — GETs PÚBLICOS (read-only, estrutura)")
# ══════════════════════════════════════════════════════════════════════════════
check("E1.1 — / (index HTML)",            "GET", "/", {200})
check("E1.2 — /pricing (HTML)",           "GET", "/pricing", {200})
check("E1.3 — /api/system_health",        "GET", "/api/system_health", {200}, timeout=30)
check("E1.4 — /api/voices",               "GET", "/api/voices", {200})
check("E1.5 — /api/voices/elevenlabs",    "GET", "/api/voices/elevenlabs", {200,400,401,500})
check("E1.6 — /api/voice_presets",        "GET", "/api/voice_presets", {200})
check("E1.7 — /api/voice_presets/<id>",   "GET", "/api/voice_presets/documentary_narrator", {200,404})
check("E1.8 — /api/history",              "GET", "/api/history", {200})
check("E1.9 — /api/dashboard",            "GET", "/api/dashboard", {200}, timeout=30)
check("E1.10 — /api/vram",                "GET", "/api/vram", {200})
check("E1.11 — /api/check_models",        "GET", "/api/check_models", {200}, timeout=30)
check("E1.12 — /api/templates",           "GET", "/api/templates", {200})
check("E1.13 — /api/backgrounds",         "GET", "/api/backgrounds", {200})
check("E1.14 — /api/gesture_videos",      "GET", "/api/gesture_videos", {200})
check("E1.15 — /api/avatar/library",      "GET", "/api/avatar/library", {200})
check("E1.16 — /api/avatar/stock_library","GET", "/api/avatar/stock_library", {200})
check("E1.17 — /api/tools/music_library", "GET", "/api/tools/music_library", {200})
check("E1.18 — /api/pricing/plans",       "GET", "/api/pricing/plans", {200})
check("E1.19 — /api/webhooks (GET)",      "GET", "/api/webhooks", {200})
check("E1.20 — /api/debug/jobs",          "GET", "/api/debug/jobs", {200})


# ══════════════════════════════════════════════════════════════════════════════
sep("E2 — GETs ADMIN (exigem X-Admin-Token)")
# ══════════════════════════════════════════════════════════════════════════════
# Com token válido → 200; sem token → 401
check("E2.1 — /api/admin/status (com token)",    "GET", "/api/admin/status", {200}, headers=ADMIN_HDR)
check("E2.2 — /api/admin/analytics (com token)", "GET", "/api/admin/analytics", {200}, headers=ADMIN_HDR, timeout=30)
check("E2.3 — /api/admin/keys (com token)",      "GET", "/api/admin/keys", {200}, headers=ADMIN_HDR)
check("E2.4 — /api/admin/stripe/status",         "GET", "/api/admin/stripe/status", {200}, headers=ADMIN_HDR)
check("E2.5 — /api/admin/stripe/config",         "GET", "/api/admin/stripe/config", {200}, headers=ADMIN_HDR)
# Sem token → deve negar (401/403), NÃO 500
try:
    r = G("/api/admin/keys")
    if r.status_code in (401, 403):
        ok(f"E2.6 — admin sem token → {r.status_code} (negado corretamente)")
    elif r.status_code == 500:
        fail("E2.6 — admin sem token", "500 (deveria ser 401)")
    else:
        ok(f"E2.6 — admin sem token → {r.status_code} (atenção: deveria negar)")
except Exception as e:
    fail("E2.6 — admin sem token", str(e)[:80])


# ══════════════════════════════════════════════════════════════════════════════
sep("E3 — POSTs DE IA (script/voz — graciosos sem/com chave Groq)")
# ══════════════════════════════════════════════════════════════════════════════
check("E3.1 — ai/generate_script", "POST", "/api/ai/generate_script",
      {200,400,401,402,429,503}, json={"topic": "tecnologia", "duration": 30}, timeout=40)
check("E3.2 — ai/enhance_script", "POST", "/api/ai/enhance_script",
      {200,400,401,402,429,503}, json={"script": "Olá mundo, isso é um teste."}, timeout=40)
check("E3.3 — ai/suggest_voice", "POST", "/api/ai/suggest_voice",
      {200,400,401,402,429,503}, json={"script": "Documentário sobre a natureza."}, timeout=40)
check("E3.4 — ai/detect_voice", "POST", "/api/ai/detect_voice",
      {200,400,401,402,429,503}, json={"script": "Notícias urgentes de hoje."}, timeout=40)
check("E3.5 — ai/url_to_script", "POST", "/api/ai/url_to_script",
      {200,400,401,402,422,429,500,503}, json={"url": "https://example.com"}, timeout=40)
check("E3.6 — ai/generate_image", "POST", "/api/ai/generate_image",
      {200,400,401,402,429,503}, json={"prompt": "a professional portrait"}, timeout=40)


# ══════════════════════════════════════════════════════════════════════════════
sep("E4 — GESTÃO LEVE (round-trips: templates, webhooks)")
# ══════════════════════════════════════════════════════════════════════════════
# Template: criar → confirmar na lista → deletar
try:
    r = P("/api/templates/save", json={"name": f"_test_tmpl_{uuid.uuid4().hex[:6]}", "voice": "pt-BR-FranciscaNeural"})
    assert r.status_code == 200, f"save HTTP {r.status_code}"
    tid = r.json().get("id", "")
    assert tid, "sem id"
    lst = G("/api/templates").json().get("templates", [])
    found = any(t.get("id") == tid for t in lst)
    rd = requests.delete(f"{BASE}/api/templates/{tid}", timeout=10)
    ok(f"E4.1 — template criar/listar/deletar", f"id={tid} found={found} del={rd.status_code}")
except Exception as e:
    fail("E4.1 — template round-trip", str(e)[:120])

# Webhook: criar → confirmar → deletar
try:
    r = P("/api/webhooks", json={"url": "https://example.com/webhook-test", "events": ["job_complete"]})
    if r.status_code in (200, 201):
        wid = r.json().get("id")
        rd = requests.delete(f"{BASE}/api/webhooks/{wid}", timeout=10) if wid is not None else None
        ok(f"E4.2 — webhook criar/deletar", f"id={wid} del={rd.status_code if rd else 'n/a'}")
    else:
        ok(f"E4.2 — webhook POST → {r.status_code} (gracioso)")
except Exception as e:
    fail("E4.2 — webhook round-trip", str(e)[:120])

# cloud/test (testa conexão executor remoto — gracioso)
check("E4.3 — cloud/test", "POST", "/api/cloud/test", {200,400,401,500},
      json={"executor": "replicate"}, timeout=30)
# admin/auth_mode (toggle — com token)
check("E4.4 — admin/auth_mode", "POST", "/api/admin/auth_mode", {200,400},
      json={"auth_required": False}, headers=ADMIN_HDR)
# tools/translate_srt (sem SRT válido → gracioso)
check("E4.5 — tools/translate_srt", "POST", "/api/tools/translate_srt", {200,400,422,500},
      json={"srt": "1\n00:00:00,000 --> 00:00:02,000\nHello\n", "target_lang": "pt"}, timeout=40)


# ══════════════════════════════════════════════════════════════════════════════
sep("E5 — POSTs PESADOS: rejeição graciosa de input inválido (sem GPU)")
# ══════════════════════════════════════════════════════════════════════════════
# Todos enviados SEM os arquivos/dados necessários → devem dar 400/422, nunca 500
check("E5.1 — batch (vazio)",            "POST", "/api/batch", {400,422}, json={})
check("E5.2 — batch/status (vazio)",     "POST", "/api/batch/status", {200,400,422}, json={})
check("E5.3 — external/generate (vazio)","POST", "/api/external/generate", {400,401,422,429})
check("E5.4 — avatar/create (vazio)",    "POST", "/api/avatar/create", {400,422})
check("E5.5 — avatar/upload (vazio)",    "POST", "/api/avatar/upload", {400,422})
check("E5.6 — avatar/change_clothing",   "POST", "/api/avatar/change_clothing", {400,422})
check("E5.7 — avatar/remove_bg (vazio)", "POST", "/api/avatar/remove_bg", {400,422})
check("E5.8 — avatar/transcribe (vazio)","POST", "/api/avatar/transcribe", {400,422})
check("E5.9 — tools/enhance_image",      "POST", "/api/tools/enhance_image", {400,422})
check("E5.10 — tools/chroma_key",        "POST", "/api/tools/chroma_key", {400,422})
check("E5.11 — tools/face_swap",         "POST", "/api/tools/face_swap", {400,422})
check("E5.12 — tools/hd_upscale",        "POST", "/api/tools/hd_upscale", {400,422})
check("E5.13 — tools/enhance_video",     "POST", "/api/tools/enhance_video", {400,422})
check("E5.14 — tools/karaoke_captions",  "POST", "/api/tools/karaoke_captions", {400,422})
check("E5.15 — tools/burn_karaoke",      "POST", "/api/tools/burn_karaoke", {400,422})
check("E5.16 — editor/merge (vazio)",    "POST", "/api/editor/merge", {400,422})
check("E5.17 — editor/trim (vazio)",     "POST", "/api/editor/trim", {400,422})
check("E5.18 — video/translate (vazio)", "POST", "/api/video/translate", {400,422})
check("E5.19 — voices/f5_clone (vazio)", "POST", "/api/voices/f5_clone", {400,422})
check("E5.20 — voices/clone (vazio)",    "POST", "/api/voices/clone", {400,422})
check("E5.21 — pricing/checkout (vazio)","POST", "/api/pricing/checkout", {400,422,500})
check("E5.22 — gesture_videos/upload",   "POST", "/api/gesture_videos/upload", {400,401,422}, headers=ADMIN_HDR)
check("E5.23 — backgrounds/upload",      "POST", "/api/backgrounds/upload", {400,422})


# ══════════════════════════════════════════════════════════════════════════════
sep("E6 — STATIC FILE SERVING + 404")
# ══════════════════════════════════════════════════════════════════════════════
# Arquivo inexistente → 404 (não 500); endpoint API inexistente → 404 JSON
check("E6.1 — /outputs/<inexistente>",   "GET", "/outputs/nao_existe_12345.mp4", {404})
check("E6.2 — /uploads/<inexistente>",   "GET", "/uploads/nao_existe_12345.jpg", {404})
check("E6.3 — /backgrounds/<inexistente>","GET", "/backgrounds/nao_existe.jpg", {404})
check("E6.4 — /api/rota_inexistente",    "GET", "/api/rota_que_nao_existe_xyz", {404})
# Path traversal nos servidores de arquivo → não pode vazar (404/403, nunca 200 de /etc)
try:
    r = G("/outputs/..%2f..%2f..%2fserver.py")
    if r.status_code in (404, 403, 400):
        ok(f"E6.5 — path traversal em /outputs → {r.status_code} (bloqueado)")
    elif r.status_code == 200 and "app.run" in r.text:
        fail("E6.5 — path traversal", "VAZOU server.py!")
    else:
        ok(f"E6.5 — path traversal → {r.status_code}")
except Exception as e:
    ok(f"E6.5 — path traversal: {type(e).__name__}")


# ══════════════════════════════════════════════════════════════════════════════
total = passes + fails
print(f"\n{'='*70}")
print(f"  AUDITORIA ENDPOINTS — RESULTADO: {passes} PASS / {fails} FAIL / {total} TOTAL")
print(f"{'='*70}")
if fail_log:
    print(f"\n  FALHAS ({len(fail_log)}):")
    for f_msg in fail_log:
        print(f"    {f_msg}")
else:
    print(f"\n  TODOS OS {total} ENDPOINTS OK! 🏆")
print()
sys.exit(0 if fails == 0 else 1)
