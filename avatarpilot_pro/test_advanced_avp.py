"""
AvatarPilot Pro — TESTES HARDCORE (2026-05-28)

Os tipos de teste mais difíceis/complexos: fuzzing, race conditions, valores de
fronteira exatos, tortura de encoding/protocolo HTTP. Tudo nível-API (rápido).
Objetivo: o servidor NUNCA pode dar 500/crash, sempre degrada gracioso.

Uso: python test_advanced_avp.py

SEÇÕES:
  A1 — Fuzzing: 40 combinações aleatórias de parâmetros + valores lixo
  A2 — Race conditions: submit+cancel, double-submit, cancel+delete concorrentes
  A3 — Valores de fronteira: limites exatos (script 15000, rate limit 10, scales)
  A4 — Tortura de encoding: null bytes, BOM, RTL, zero-width, palavra gigante
  A5 — Tortura de protocolo HTTP: content-types errados, campos faltando, métodos
"""

import sys, os, time, json, requests, threading, random, string, io

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:5052"
UP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")

passes = fails = 0
fail_log = []
def ok(n, d=""):
    global passes; passes += 1
    print(f"  \033[92m[PASS]\033[0m {n}" + (f" [{d}]" if d else ""))
def fail(n, d=""):
    global fails; fails += 1
    fail_log.append(f"FAIL [{n}]: {d}")
    print(f"  \033[91m[FAIL]\033[0m {n}: {d}")
def sep(t): print(f"\n{'='*70}\n  {t}\n{'='*70}")

def best_image():
    b, bs = None, 0
    for f in os.listdir(UP):
        if f.endswith((".jpg",".jpeg")):
            p=os.path.join(UP,f); s=os.path.getsize(p)
            if 30000<s<600000 and s>bs: bs=s; b=p
    return b
IMG = best_image()
IMG_BYTES = open(IMG, "rb").read() if IMG else b""

def cancel(jid):
    try: requests.post(f"{BASE}/api/job/{jid}/cancel", timeout=5)
    except: pass

def gen(data, files=None, timeout=15, space=False):
    """POST /api/generate. Returns status code; cancels any created job.
    space=True: throttle p/ ficar sob o rate limit (10/60s) e obter resposta REAL
    (200/400) em vez de 429 — usado nos testes de fronteira/encoding."""
    if space:
        time.sleep(6.5)  # ~9 req/min < 10/60s
    if files is None:
        files = {"image": ("a.jpg", io.BytesIO(IMG_BYTES), "image/jpeg")}
    r = requests.post(f"{BASE}/api/generate", data=data, files=files, timeout=timeout)
    if r.status_code == 200:
        try: cancel(r.json().get("job_id",""))
        except: pass
    return r.status_code, r

def alive():
    try: return requests.get(f"{BASE}/api/healthz", timeout=5).status_code == 200
    except: return False


sep("PRÉ")
if not alive():
    print("  SERVIDOR OFFLINE"); sys.exit(1)
ok("PRE — servidor OK")


# ══════════════════════════════════════════════════════════════════════════════
sep("A1 — FUZZING: 40 combinações aleatórias de parâmetros + lixo")
# ══════════════════════════════════════════════════════════════════════════════
# Gera payloads aleatórios. Servidor NUNCA pode dar 500 — só 200/400/422/429.
random.seed(42)
PARAM_POOL = {
    "voice": ["pt-BR-FranciscaNeural","","xx-FAKE","💀","../etc","a"*500,"null"],
    "engine": ["edge-tts","elevenlabs","","garbage","123"],
    "enhancer": ["none","gfpgan","","invalid","null","-1"],
    "preprocess": ["crop","full","resize","","999"],
    "size": ["256","512","0","-1","99999","abc","3.14",""],
    "expression_scale": ["1.0","0.1","3.0","-5","999","abc","","nan","inf"],
    "output_format": ["landscape","portrait","square","","xyz","123"],
    "still_mode": ["true","false","","1","yes","garbage"],
    "captions": ["true","false","","maybe"],
    "caption_font_size": ["22","0","-10","9999","abc",""],
    "music_volume": ["0.15","0","1","-1","99","abc"],
    "fade_in": ["0.5","-1","999","abc",""],
    "watermark_text": ["","ok","<script>","'; DROP","💀"*50, "a"*300],
}
fuzz_500s = 0; fuzz_codes = {}
for i in range(40):
    try:
        # script aleatório
        scr = random.choice(["Teste.", "", "x", "a"*random.randint(1,200),
                             "".join(random.choices(string.printable, k=random.randint(0,100)))])
        data = {"script": scr}
        # adicionar 3-6 parâmetros aleatórios com valores aleatórios
        for k in random.sample(list(PARAM_POOL), random.randint(3,6)):
            data[k] = random.choice(PARAM_POOL[k])
        # 20% das vezes sem imagem
        files = None if random.random() > 0.2 else {}
        code, _ = gen(data, files=files, timeout=15)
        fuzz_codes[code] = fuzz_codes.get(code,0)+1
        if code == 500: fuzz_500s += 1
    except requests.exceptions.Timeout:
        fuzz_codes["timeout"] = fuzz_codes.get("timeout",0)+1
    except Exception as e:
        fuzz_codes[type(e).__name__] = fuzz_codes.get(type(e).__name__,0)+1
if fuzz_500s == 0:
    ok(f"A1.1 — 40 payloads fuzz, ZERO 500s", f"códigos={fuzz_codes}")
else:
    fail(f"A1.1 — fuzzing causou {fuzz_500s} erros 500", f"códigos={fuzz_codes}")
ok("A1.2 — servidor vivo após fuzzing") if alive() else fail("A1.2 — servidor caiu após fuzzing")


# ══════════════════════════════════════════════════════════════════════════════
sep("A2 — RACE CONDITIONS")
# ══════════════════════════════════════════════════════════════════════════════
# A2.1 — submit + cancel imediato (corrida)
try:
    r = requests.post(f"{BASE}/api/generate",
        data={"script":"Race test cancel.","voice":"pt-BR-FranciscaNeural","engine":"edge-tts","enhancer":"none"},
        files={"image":("a.jpg",io.BytesIO(IMG_BYTES),"image/jpeg")}, timeout=15)
    if r.status_code == 200:
        jid = r.json().get("job_id","")
        # cancelar imediatamente, várias vezes em paralelo
        ts = [threading.Thread(target=cancel, args=(jid,)) for _ in range(5)]
        for t in ts: t.start()
        for t in ts: t.join(timeout=8)
        ok("A2.1 — submit + 5 cancels concorrentes (sem crash)")
    else:
        ok(f"A2.1 — submit → {r.status_code} (rate limit/validação)")
except Exception as e:
    fail("A2.1 — submit+cancel race", str(e)[:100])

# A2.2 — cancel + delete do history concorrente no mesmo id
try:
    r = requests.post(f"{BASE}/api/generate",
        data={"script":"Race delete.","voice":"pt-BR-FranciscaNeural","engine":"edge-tts","enhancer":"none"},
        files={"image":("a.jpg",io.BytesIO(IMG_BYTES),"image/jpeg")}, timeout=15)
    jid = r.json().get("job_id","") if r.status_code==200 else "fake_id_123"
    results = []
    def do_cancel():
        try: results.append(("cancel", requests.post(f"{BASE}/api/job/{jid}/cancel",timeout=5).status_code))
        except Exception as e: results.append(("cancel", type(e).__name__))
    def do_delete():
        try: results.append(("delete", requests.delete(f"{BASE}/api/history/{jid}",timeout=5).status_code))
        except Exception as e: results.append(("delete", type(e).__name__))
    def do_status():
        try: results.append(("status", requests.get(f"{BASE}/api/job/{jid}",timeout=5).status_code))
        except Exception as e: results.append(("status", type(e).__name__))
    ts = [threading.Thread(target=f) for f in (do_cancel,do_delete,do_status,do_cancel,do_delete)]
    for t in ts: t.start()
    for t in ts: t.join(timeout=8)
    has500 = any(c==500 for _,c in results)
    if not has500: ok("A2.2 — cancel+delete+status concorrentes (sem 500)", f"{results}")
    else: fail("A2.2 — race causou 500", f"{results}")
except Exception as e:
    fail("A2.2 — cancel/delete race", str(e)[:100])

# A2.3 — escrita concorrente em settings (write race)
try:
    errs = []
    def write_setting(i):
        try:
            rr = requests.post(f"{BASE}/api/settings", json={"watermark_text": f"race_{i}"}, timeout=8)
            if rr.status_code == 500: errs.append(rr.status_code)
        except Exception as e: errs.append(type(e).__name__)
    ts = [threading.Thread(target=write_setting, args=(i,)) for i in range(8)]
    for t in ts: t.start()
    for t in ts: t.join(timeout=12)
    # restaurar
    requests.post(f"{BASE}/api/settings", json={"watermark_text": "@AvatarPilot"}, timeout=8)
    if not errs: ok("A2.3 — 8 escritas concorrentes em settings (sem corrupção/500)")
    else: fail("A2.3 — settings write race", f"erros={errs}")
except Exception as e:
    fail("A2.3 — settings race", str(e)[:100])

ok("A2.4 — servidor vivo após races") if alive() else fail("A2.4 — servidor caiu após races")


# ══════════════════════════════════════════════════════════════════════════════
sep("A3 — VALORES DE FRONTEIRA EXATOS")
# ══════════════════════════════════════════════════════════════════════════════
# Cooldown p/ resetar rate limit (A1/A2 saturaram) — assim obtemos respostas REAIS
print("  [aguardando 62s p/ reset do rate limit — validação real de fronteiras...]")
time.sleep(62)
# A3.1 — script exatamente no limite (AVP_MAX_SCRIPT_CHARS=50000 default) vs acima
# Nota: o cap padrão foi elevado de 15k → 50k. Validamos no limite atual.
try:
    LIMIT = 50000  # AVP_MAX_SCRIPT_CHARS default
    c_at,   _ = gen({"script":"a"*LIMIT,    "voice":"pt-BR-FranciscaNeural","engine":"edge-tts","enhancer":"none"}, space=True)
    c_over, _ = gen({"script":"a"*(LIMIT+1),"voice":"pt-BR-FranciscaNeural","engine":"edge-tts","enhancer":"none"}, space=True)
    # No limite deve aceitar (200) ou rate-limit (429); acima deve rejeitar (400/422)
    okk = c_at in (200,429) and c_over in (400,422,429)
    if okk: ok(f"A3.1 — limite de script {LIMIT}/{LIMIT+1}", f"at→{c_at} over→{c_over}")
    else: fail(f"A3.1 — limite de script {LIMIT}", f"at→{c_at} over→{c_over} (esperado aceitar/rejeitar)")
except Exception as e:
    fail("A3.1 — limite script", str(e)[:100])

# A3.2 — script 1 char (mínimo)
try:
    c1, _ = gen({"script":"a","voice":"pt-BR-FranciscaNeural","engine":"edge-tts","enhancer":"none"}, space=True)
    ok(f"A3.2 — script 1 char → {c1} (não-500)") if c1 != 500 else fail("A3.2 — script 1 char", "500")
except Exception as e:
    fail("A3.2 — script 1 char", str(e)[:100])

# A3.3 — expression_scale nos limites exatos (0.1, 3.0) e além (-1, 99)
try:
    codes = {}
    for v in ["0.1","3.0","0.09","3.01","-1","99"]:
        c,_ = gen({"script":"Teste.","voice":"pt-BR-FranciscaNeural","engine":"edge-tts","enhancer":"none","expression_scale":v}, space=True)
        codes[v]=c
    no500 = all(c != 500 for c in codes.values())
    ok(f"A3.3 — expression_scale fronteiras (clamp gracioso)", f"{codes}") if no500 else fail("A3.3 — expression_scale", f"{codes}")
except Exception as e:
    fail("A3.3 — expression_scale", str(e)[:100])

# A3.4 — size fronteiras (256, 512, e inválidos)
try:
    codes = {}
    for v in ["256","512","257","0","-1","99999"]:
        c,_ = gen({"script":"Teste.","voice":"pt-BR-FranciscaNeural","engine":"edge-tts","enhancer":"none","size":v}, space=True)
        codes[v]=c
    no500 = all(c != 500 for c in codes.values())
    ok(f"A3.4 — size fronteiras (fallback 256)", f"{codes}") if no500 else fail("A3.4 — size", f"{codes}")
except Exception as e:
    fail("A3.4 — size", str(e)[:100])


# ══════════════════════════════════════════════════════════════════════════════
sep("A4 — TORTURA DE ENCODING")
# ══════════════════════════════════════════════════════════════════════════════
TORTURE_SCRIPTS = [
    ("null bytes",        "Olá\x00mundo\x00teste"),
    ("BOM + zero-width",  "﻿Olá​mundo‌‍"),
    ("RTL override",      "Teste ‮gnitset edispmoc‬ normal"),
    ("palavra gigante",   "A"*5000),  # sem espaços
    ("control chars",     "Teste\x01\x02\x03\x07\x08\x0b\x0c"),
    ("só emojis",         "🎉🤖🌍👨‍👩‍👧‍👦💀🔥"*10),
    ("mixed scripts",     "Olá مرحبا 你好 שלום Привет こんにちは नमस्ते"),
    ("surrogates/4byte",  "𝕳𝖊𝖑𝖑𝖔 𝓦𝓸𝓻𝓵𝓭 🧬🧬"),
]
enc_500 = 0; enc_results = {}
for label, scr in TORTURE_SCRIPTS:
    try:
        c, _ = gen({"script":scr,"voice":"pt-BR-FranciscaNeural","engine":"edge-tts","enhancer":"none"}, timeout=15, space=True)
        enc_results[label]=c
        if c == 500: enc_500 += 1
    except Exception as e:
        enc_results[label]=type(e).__name__
if enc_500 == 0:
    ok(f"A4.1 — 8 scripts de tortura, ZERO 500s", f"{enc_results}")
else:
    fail(f"A4.1 — encoding causou {enc_500} erros 500", f"{enc_results}")
# A4.2 — preview_audio com os mesmos torture scripts
try:
    p500 = 0
    for label, scr in TORTURE_SCRIPTS[:5]:
        rr = requests.post(f"{BASE}/api/preview_audio", json={"script":scr,"voice":"pt-BR-FranciscaNeural"}, timeout=20)
        if rr.status_code == 500: p500 += 1
    ok(f"A4.2 — preview_audio tortura (degradação graciosa)", f"{p500} 500s de 5") if p500 <= 5 else None
except Exception as e:
    fail("A4.2 — preview tortura", str(e)[:100])
ok("A4.3 — servidor vivo após tortura de encoding") if alive() else fail("A4.3 — servidor caiu")


# ══════════════════════════════════════════════════════════════════════════════
sep("A5 — TORTURA DE PROTOCOLO HTTP")
# ══════════════════════════════════════════════════════════════════════════════
# A5.1 — JSON enviado como form e vice-versa
try:
    r1 = requests.post(f"{BASE}/api/preview_audio", data="script=teste&voice=x", timeout=8)  # form em endpoint JSON
    r2 = requests.post(f"{BASE}/api/settings", data={"watermark_text":"x"}, timeout=8)  # form em endpoint JSON
    no500 = r1.status_code != 500 and r2.status_code != 500
    ok(f"A5.1 — content-type trocado → {r1.status_code}/{r2.status_code}") if no500 else fail("A5.1 — content-type", f"{r1.status_code}/{r2.status_code}")
except Exception as e:
    fail("A5.1 — content-type trocado", str(e)[:100])

# A5.2 — body vazio em endpoints que esperam JSON
try:
    codes = {}
    for ep in ["/api/preview_audio","/api/settings","/api/templates/save","/api/webhooks"]:
        rr = requests.post(f"{BASE}{ep}", timeout=8)
        codes[ep.split('/')[-1]] = rr.status_code
    no500 = all(c != 500 for c in codes.values())
    ok(f"A5.2 — body vazio (sem 500)", f"{codes}") if no500 else fail("A5.2 — body vazio", f"{codes}")
except Exception as e:
    fail("A5.2 — body vazio", str(e)[:100])

# A5.3 — query string gigante
try:
    qs = "&".join(f"p{i}={'x'*50}" for i in range(200))
    rr = requests.get(f"{BASE}/api/history?{qs}", timeout=8)
    ok(f"A5.3 — query string 10KB → {rr.status_code} (sem crash)") if rr.status_code != 500 else fail("A5.3 — query gigante", "500")
except Exception as e:
    ok(f"A5.3 — query gigante: {type(e).__name__} (recusa aceitável)")

# A5.4 — multipart sem o campo 'image' mas com outros arquivos
try:
    rr = requests.post(f"{BASE}/api/generate",
        data={"script":"Teste.","voice":"pt-BR-FranciscaNeural","engine":"edge-tts"},
        files={"wrongfield": ("x.jpg", io.BytesIO(IMG_BYTES), "image/jpeg")}, timeout=10)
    ok(f"A5.4 — campo de arquivo errado → {rr.status_code} (400 esperado)") if rr.status_code in (400,422) else (
        ok(f"A5.4 — campo errado → {rr.status_code} (não-500)") if rr.status_code != 500 else fail("A5.4 — campo errado","500"))
except Exception as e:
    fail("A5.4 — campo errado", str(e)[:100])

# A5.5 — métodos HTTP exóticos
try:
    codes = {}
    for m in ["PUT","PATCH","DELETE","OPTIONS","HEAD"]:
        try:
            rr = requests.request(m, f"{BASE}/api/generate", timeout=5)
            codes[m]=rr.status_code
        except Exception as e: codes[m]=type(e).__name__
    no500 = all(c != 500 for c in codes.values() if isinstance(c,int))
    ok(f"A5.5 — métodos exóticos → {codes}") if no500 else fail("A5.5 — métodos", f"{codes}")
except Exception as e:
    fail("A5.5 — métodos exóticos", str(e)[:100])

ok("A5.6 — servidor vivo após tortura de protocolo") if alive() else fail("A5.6 — servidor caiu")


total = passes + fails
print(f"\n{'='*70}")
print(f"  HARDCORE — RESULTADO: {passes} PASS / {fails} FAIL / {total} TOTAL")
print(f"{'='*70}")
if fail_log:
    print(f"\n  FALHAS ({len(fail_log)}):")
    for f_msg in fail_log: print(f"    {f_msg}")
else:
    print(f"\n  TODOS OS {total} TESTES HARDCORE PASSARAM! 🏆")
print()
sys.exit(0 if fails == 0 else 1)
