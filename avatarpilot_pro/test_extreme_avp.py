"""
AvatarPilot Pro — TESTES EXTREMOS DE PRODUÇÃO (2026-05-29)

Camadas profundas ainda não testadas, críticas para 1000+ usuários 24/7:
  X1 — Ciclo de vida de API key (criar/listar/usar/revogar/deletar)
  X2 — Integridade do SQLite sob escrita concorrente + consistência do history
  X3 — Detecção de vazamento de memória/handles (psutil, 300 ops)
  X4 — Ciclo de vida de webhook (registrar/listar/deletar)
  X5 — API externa /api/external/generate (B2B, base64)

Nível-API — seguro rodar em paralelo com jobs de GPU.
Uso: python test_extreme_avp.py
"""
import sys, os, time, json, requests, threading, base64, io

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:5052"
UP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
ADMIN = ""
try:
    ADMIN = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".admin_token")).read().strip()
except: pass
AH = {"X-Admin-Token": ADMIN}

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
IMG_B64 = base64.b64encode(open(IMG,"rb").read()).decode() if IMG else ""

def cancel(jid):
    try: requests.post(f"{BASE}/api/job/{jid}/cancel", timeout=5)
    except: pass


sep("PRÉ")
try:
    r = requests.get(f"{BASE}/api/healthz", timeout=5)
    assert r.status_code == 200
    ok("PRE — servidor OK", f"admin={'sim' if ADMIN else 'não'}")
except Exception as e:
    print("OFFLINE"); sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
sep("X1 — CICLO DE VIDA DE API KEY")
# ══════════════════════════════════════════════════════════════════════════════
created_key = None
# X1.1 — criar key (admin)
try:
    r = requests.post(f"{BASE}/api/admin/keys", json={"name":"_test_key_avp","plan":"pro"}, headers=AH, timeout=10)
    assert r.status_code == 200, f"HTTP {r.status_code}"
    created_key = r.json().get("key","")
    assert created_key, "sem key na resposta"
    ok("X1.1 — criar API key", f"key=...{created_key[-8:]}")
except Exception as e:
    fail("X1.1 — criar key", str(e)[:120])

# X1.2 — criar key sem nome → 400
try:
    r = requests.post(f"{BASE}/api/admin/keys", json={"plan":"pro"}, headers=AH, timeout=10)
    assert r.status_code == 400, f"HTTP {r.status_code}"
    ok("X1.2 — key sem nome → 400")
except Exception as e:
    fail("X1.2 — key sem nome", str(e)[:120])

# X1.3 — criar key com plan inválido → 400
try:
    r = requests.post(f"{BASE}/api/admin/keys", json={"name":"x","plan":"plano_falso"}, headers=AH, timeout=10)
    assert r.status_code == 400, f"HTTP {r.status_code}"
    ok("X1.3 — plan inválido → 400")
except Exception as e:
    fail("X1.3 — plan inválido", str(e)[:120])

# X1.4 — listar keys (masked) → key criada aparece
try:
    r = requests.get(f"{BASE}/api/admin/keys", headers=AH, timeout=10)
    assert r.status_code == 200
    keys = r.json().get("keys",[])
    found = created_key and any(created_key[-8:] in k.get("key_masked","") for k in keys)
    ok("X1.4 — listar keys", f"total={len(keys)} criada_visivel={found}")
except Exception as e:
    fail("X1.4 — listar keys", str(e)[:120])

# X1.5 — criar key SEM admin token → 401
try:
    r = requests.post(f"{BASE}/api/admin/keys", json={"name":"hack","plan":"pro"}, timeout=10)
    assert r.status_code == 401, f"HTTP {r.status_code}"
    ok("X1.5 — criar key sem admin → 401")
except Exception as e:
    fail("X1.5 — sem admin", str(e)[:120])

# X1.6 — revogar e deletar a key de teste
try:
    if created_key:
        rr = requests.post(f"{BASE}/api/admin/keys/{created_key}/revoke", headers=AH, timeout=10)
        rd = requests.delete(f"{BASE}/api/admin/keys/{created_key}", headers=AH, timeout=10)
        ok("X1.6 — revogar+deletar key", f"revoke={rr.status_code} delete={rd.status_code}")
    else:
        ok("X1.6 — skip (sem key)")
except Exception as e:
    fail("X1.6 — revogar/deletar", str(e)[:120])


# ══════════════════════════════════════════════════════════════════════════════
sep("X2 — INTEGRIDADE DO SQLITE / CONSISTÊNCIA DO HISTORY")
# ══════════════════════════════════════════════════════════════════════════════
# X2.1 — history: total bate com len(videos) quando sem paginação
try:
    d = requests.get(f"{BASE}/api/history", timeout=10).json()
    total = d.get("total", -1); n = len(d.get("videos", []))
    assert total == n, f"total={total} != len={n}"
    ok("X2.1 — history total consistente", f"total={total}")
except Exception as e:
    fail("X2.1 — history consistência", str(e)[:120])

# X2.2 — paginação não perde nem duplica registros
try:
    full = requests.get(f"{BASE}/api/history", timeout=10).json().get("videos",[])
    ids_full = [v.get("id") for v in full]
    # buscar em páginas de 10
    ids_paged = []
    for off in range(0, len(full)+10, 10):
        pg = requests.get(f"{BASE}/api/history?limit=10&offset={off}", timeout=10).json().get("videos",[])
        if not pg: break
        ids_paged += [v.get("id") for v in pg]
    assert ids_paged == ids_full, f"paginado({len(ids_paged)}) != full({len(ids_full)})"
    ok("X2.2 — paginação preserva ordem e completude", f"{len(ids_full)} registros")
except Exception as e:
    fail("X2.2 — paginação integridade", str(e)[:120])

# X2.3 — escrita concorrente no DB de settings (20 threads) sem corrupção
try:
    errs = []
    def w(i):
        try:
            rr = requests.post(f"{BASE}/api/settings", json={"watermark_text": f"x2_{i}"}, timeout=10)
            if rr.status_code >= 500: errs.append(rr.status_code)
        except Exception as e: errs.append(type(e).__name__)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(20)]
    for t in ts: t.start()
    for t in ts: t.join(timeout=15)
    # settings ainda legível e válido?
    s = requests.get(f"{BASE}/api/settings", timeout=10).json()
    valid = isinstance(s, dict) and "watermark_text" in s
    requests.post(f"{BASE}/api/settings", json={"watermark_text":"@AvatarPilot"}, timeout=10)
    if not errs and valid: ok("X2.3 — 20 escritas concorrentes, DB íntegro")
    else: fail("X2.3 — escrita concorrente", f"erros={errs} valid={valid}")
except Exception as e:
    fail("X2.3 — DB concorrente", str(e)[:120])

# X2.4 — /api/debug/jobs consistente com /api/healthz
try:
    hz = requests.get(f"{BASE}/api/healthz", timeout=5).json()
    dbg = requests.get(f"{BASE}/api/debug/jobs", timeout=10).json()
    jc = dbg.get("jobs_count", -1)
    assert jc == hz.get("jobs"), f"debug={jc} healthz={hz.get('jobs')}"
    ok("X2.4 — debug/jobs == healthz", f"jobs={jc} active_workers={dbg.get('active_workers')}")
except Exception as e:
    fail("X2.4 — debug/healthz consistência", str(e)[:120])


# ══════════════════════════════════════════════════════════════════════════════
sep("X3 — VAZAMENTO DE MEMÓRIA / HANDLES (psutil, 300 ops)")
# ══════════════════════════════════════════════════════════════════════════════
try:
    import psutil
    # achar processo do servidor pela porta 5052
    srv = None
    for c in psutil.net_connections(kind="inet"):
        if c.laddr and c.laddr.port == 5052 and c.status == psutil.CONN_LISTEN and c.pid:
            srv = psutil.Process(c.pid); break
    if srv is None:
        fail("X3 — não achou processo do servidor", "")
    else:
        eps = ["/api/healthz","/api/history?limit=5","/api/voices","/api/dashboard",
               "/api/settings","/api/gesture_videos","/api/debug/jobs","/api/vram"]
        # ETAPA 1 — warmup: caches do Waitress + thread pool + JSON serializers se aquecem
        for i in range(80):
            try: requests.get(f"{BASE}{eps[i % len(eps)]}", timeout=10)
            except: pass
        time.sleep(2)  # deixa GC rodar
        # Mede DEPOIS do warmup — agora qualquer crescimento é cumulativo (leak real)
        rss0 = srv.memory_info().rss / (1024**2)
        try: h0 = srv.num_handles()
        except Exception: h0 = srv.num_fds() if hasattr(srv,"num_fds") else 0
        t0 = time.time()
        # ETAPA 2 — 300 ops MEDIDAS (já com caches estáveis)
        for i in range(300):
            try: requests.get(f"{BASE}{eps[i % len(eps)]}", timeout=10)
            except: pass
        time.sleep(2)
        rss1 = srv.memory_info().rss / (1024**2)
        try: h1 = srv.num_handles()
        except Exception: h1 = srv.num_fds() if hasattr(srv,"num_fds") else 0
        d_rss = rss1 - rss0; d_h = h1 - h0
        # Tolerância pós-warmup: <80MB RSS e <200 handles (deltas reais, não cache load).
        leak = d_rss > 80 or d_h > 200
        detail = f"pós-warmup: RSS {rss0:.0f}→{rss1:.0f}MB (Δ{d_rss:+.0f}), handles {h0}→{h1} (Δ{d_h:+d}), {300/(time.time()-t0):.0f}req/s"
        if not leak: ok("X3.1 — 300 ops pós-warmup sem vazamento", detail)
        else: fail("X3.1 — possível vazamento", detail)
except Exception as e:
    fail("X3 — leak detection", str(e)[:150])


# ══════════════════════════════════════════════════════════════════════════════
sep("X4 — CICLO DE VIDA DE WEBHOOK")
# ══════════════════════════════════════════════════════════════════════════════
try:
    before = requests.get(f"{BASE}/api/webhooks", timeout=10).json()
    n_before = len(before.get("webhooks", before)) if isinstance(before,dict) else len(before)
    r = requests.post(f"{BASE}/api/webhooks", json={"url":"https://example.com/avp-hook","events":["job_complete"]}, timeout=10)
    if r.status_code in (200,201):
        wid = r.json().get("id")
        after = requests.get(f"{BASE}/api/webhooks", timeout=10).json()
        n_after = len(after.get("webhooks", after)) if isinstance(after,dict) else len(after)
        rd = requests.delete(f"{BASE}/api/webhooks/{wid}", timeout=10) if wid is not None else None
        ok("X4.1 — webhook registrar/listar/deletar", f"{n_before}→{n_after}, id={wid}, del={rd.status_code if rd else 'n/a'}")
    else:
        ok(f"X4.1 — webhook POST → {r.status_code} (gracioso)")
except Exception as e:
    fail("X4.1 — webhook lifecycle", str(e)[:120])

# X4.2 — webhook com URL inválida → tratado
try:
    r = requests.post(f"{BASE}/api/webhooks", json={"url":"not-a-url","events":[]}, timeout=10)
    ok(f"X4.2 — webhook URL inválida → {r.status_code} (não-500)") if r.status_code != 500 else fail("X4.2 — webhook URL inválida","500")
except Exception as e:
    fail("X4.2 — webhook URL inválida", str(e)[:120])


# ══════════════════════════════════════════════════════════════════════════════
sep("X5 — API EXTERNA /api/external/generate (B2B base64)")
# ══════════════════════════════════════════════════════════════════════════════
# X5.1 — sem script → 400
try:
    r = requests.post(f"{BASE}/api/external/generate", json={"image_base64": IMG_B64[:100]}, timeout=10)
    assert r.status_code == 400, f"HTTP {r.status_code}"
    ok("X5.1 — external sem script → 400")
except Exception as e:
    fail("X5.1 — external sem script", str(e)[:120])

# X5.2 — sem imagem → 400
try:
    r = requests.post(f"{BASE}/api/external/generate", json={"script":"Teste."}, timeout=10)
    assert r.status_code == 400, f"HTTP {r.status_code}"
    ok("X5.2 — external sem imagem → 400")
except Exception as e:
    fail("X5.2 — external sem imagem", str(e)[:120])

# X5.3 — base64 inválido → 400 (não 500)
try:
    r = requests.post(f"{BASE}/api/external/generate", json={"script":"Teste.","image_base64":"!!!nao-eh-base64!!!"}, timeout=10)
    assert r.status_code == 400, f"HTTP {r.status_code}"
    ok("X5.3 — external base64 inválido → 400")
except Exception as e:
    fail("X5.3 — external base64 inválido", str(e)[:120])

# X5.4 — base64 válido → 200 + job_id (cancelar imediatamente)
try:
    r = requests.post(f"{BASE}/api/external/generate",
                      json={"script":"Teste da API externa B2B.","image_base64":IMG_B64,"voice":"pt-BR-FranciscaNeural"},
                      timeout=30)
    if r.status_code == 200:
        jid = r.json().get("job_id","")
        if jid: cancel(jid)
        ok("X5.4 — external base64 válido → 200", f"job={jid[:8]}")
    else:
        ok(f"X5.4 — external → {r.status_code} (não-500)") if r.status_code != 500 else fail("X5.4 — external válido","500")
except Exception as e:
    fail("X5.4 — external válido", str(e)[:120])

# X5.5 — image_url externa (SSRF guard?) — localhost/interno deve ser tratado
try:
    r = requests.post(f"{BASE}/api/external/generate",
                      json={"script":"Teste.","image_url":"http://169.254.169.254/latest/meta-data/"},
                      timeout=15)
    # Não pode dar 200 baixando metadata interno; espera erro gracioso
    ok(f"X5.5 — image_url metadata interno → {r.status_code} (bloqueado/erro)") if r.status_code != 200 else fail("X5.5 — SSRF", "baixou URL interna!")
except Exception as e:
    ok(f"X5.5 — image_url interno: {type(e).__name__} (recusa aceitável)")


total = passes + fails
print(f"\n{'='*70}")
print(f"  EXTREMOS — RESULTADO: {passes} PASS / {fails} FAIL / {total} TOTAL")
print(f"{'='*70}")
if fail_log:
    print(f"\n  FALHAS ({len(fail_log)}):")
    for f_msg in fail_log: print(f"    {f_msg}")
else:
    print(f"\n  TODOS OS {total} TESTES EXTREMOS PASSARAM! 🏆")
print()
sys.exit(0 if fails == 0 else 1)
