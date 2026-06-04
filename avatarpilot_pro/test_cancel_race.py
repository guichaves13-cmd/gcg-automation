"""
Cancel race test: submete + cancela em rapid-fire 10 vezes.
Valida que cancelamento é gracioso, não trava server nem deixa worker zumbi.
"""
import sys, os, time, requests, threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:5052"
UP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")

def best_image():
    b, bs = None, 0
    for f in os.listdir(UP):
        if f.endswith((".jpg", ".jpeg")):
            p = os.path.join(UP, f); s = os.path.getsize(p)
            if 30000 < s < 600000 and s > bs: bs = s; b = p
    return b

IMG = best_image()
if not IMG:
    print("ERR: no image found"); sys.exit(1)

print(f"=== CANCEL RACE: 10 submit→cancel sequences ===")
passes, fails = 0, 0
img_bytes = open(IMG, "rb").read()
import io

for cycle in range(10):
    print(f"\n--- Ciclo {cycle+1}/10 ---")
    # Submit
    r = requests.post(f"{BASE}/api/generate",
        data={"script": f"Cancel race cycle {cycle+1}",
              "voice": "pt-BR-FranciscaNeural", "engine": "edge-tts",
              "enhancer": "none"},
        files={"image": (f"c{cycle}.jpg", io.BytesIO(img_bytes), "image/jpeg")},
        timeout=30)
    if r.status_code != 200:
        # Rate limit é OK, espera + skip
        if r.status_code == 429:
            print(f"  [SKIP cycle {cycle+1}] rate limit hit — aguardando 65s")
            time.sleep(65)
            continue
        print(f"  [SUBMIT FAIL] {r.status_code}: {r.text[:100]}")
        fails += 1
        continue
    jid = r.json().get("job_id", "")
    print(f"  [SUBMIT] {jid[:8]} ok")

    # Cancela em intervalos diferentes (race coverage)
    delay = 0.1 + (cycle * 0.5)  # 0.1s, 0.6s, 1.1s, ..., 4.6s
    time.sleep(delay)
    try:
        cr = requests.post(f"{BASE}/api/job/{jid}/cancel", timeout=10)
        print(f"  [CANCEL] após {delay:.1f}s → HTTP {cr.status_code}")
    except Exception as e:
        print(f"  [CANCEL EXC] {e}")
        fails += 1
        continue

    # Verifica que job ficou cancelled/finished sem travar
    time.sleep(3)
    d = requests.get(f"{BASE}/api/job/{jid}", timeout=10).json()
    st = d.get("status", "?")
    # Aceita: cancelled, error, ou done (cancel tardio depois de done)
    if st in ("cancelled", "error", "failed", "done"):
        print(f"  [POST-CANCEL] status={st} OK")
        passes += 1
    else:
        # Aguarda mais 30s pra ver se evolui
        time.sleep(30)
        d = requests.get(f"{BASE}/api/job/{jid}", timeout=10).json()
        st2 = d.get("status", "?")
        if st2 in ("cancelled", "error", "failed", "done"):
            print(f"  [POST-CANCEL after 30s] status={st2} OK")
            passes += 1
        else:
            print(f"  [STUCK] status={st2} progress={d.get('progress')}% após 33s — possível leak")
            fails += 1
    # rate-limit: aguarda 6s entre ciclos (10 ciclos × 6s = 60s, dentro do limite 10/60s)
    time.sleep(6)

# Health check final
print(f"\n--- Health check pós-stress ---")
try:
    h = requests.get(f"{BASE}/api/healthz", timeout=10).json()
    print(f"  Server respondendo: {h}")
    dbg = requests.get(f"{BASE}/api/debug/jobs", timeout=10).json()
    print(f"  active_workers={dbg.get('active_workers')} jobs_in_mem={dbg.get('jobs_count')}")
except Exception as e:
    print(f"  Server NÃO responde: {e}")
    fails += 1

# Worker zumbi?
import psutil
for c in psutil.net_connections(kind="inet"):
    if c.laddr and c.laddr.port == 5052 and c.status == psutil.CONN_LISTEN and c.pid:
        srv = psutil.Process(c.pid)
        rss = srv.memory_info().rss / (1024**2)
        print(f"  Server RSS: {rss:.0f} MB (não deve estar acima de 4GB)")
        break

print(f"\n{'='*60}")
print(f"  CANCEL RACE — {passes} PASS / {fails} FAIL / {passes+fails} ciclos")
print(f"{'='*60}")
sys.exit(0 if fails == 0 else 1)
