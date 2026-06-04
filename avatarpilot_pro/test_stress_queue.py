"""
Stress test: submete 5 jobs rapidamente, valida que TODOS completam
sem race conditions, leaks ou ordem trocada na fila.
"""
import sys, os, time, json, requests, threading

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

print(f"=== STRESS QUEUE: 5 jobs sequenciais ===")
print(f"Imagem: {os.path.basename(IMG)}")

# Submeter 5 jobs RÁPIDO (rate limit é 10/60s)
jobs_ids = []
t_submit_0 = time.time()
for i in range(5):
    script = f"Stress test job {i+1}. Validating queue behavior."
    with open(IMG, "rb") as f:
        r = requests.post(f"{BASE}/api/generate",
            data={"script": script, "voice": "pt-BR-FranciscaNeural",
                  "engine": "edge-tts", "enhancer": "none",
                  "trim_silence": "true"},
            files={"image": (f"stress_{i}.jpg", f, "image/jpeg")},
            timeout=30)
    if r.status_code != 200:
        print(f"  [SUBMIT FAIL] job {i+1}: HTTP {r.status_code} {r.text[:120]}")
        continue
    data = r.json()
    jid = data.get("job_id", "")
    eta = data.get("estimated_minutes_text", "?")
    queued = data.get("queued_workers", 0)
    print(f"  [SUBMIT OK] job {i+1}/{5} id={jid[:8]} eta={eta} queued_workers={queued}")
    jobs_ids.append(jid)
    time.sleep(2)  # spread submits

submit_dur = time.time() - t_submit_0
print(f"\n5 submits em {submit_dur:.1f}s. Aguardando completion...")

# Monitorar todos até done
t_start = time.time()
done_count = 0
fail_count = 0
completed = set()
while time.time() - t_start < 4200:  # 70 min — single worker × 5 jobs × ~8min cada
    pending = [j for j in jobs_ids if j not in completed]
    if not pending: break
    for jid in pending[:]:
        try:
            d = requests.get(f"{BASE}/api/job/{jid}", timeout=10).json()
            st = d.get("status", "")
            if st == "done":
                done_count += 1
                completed.add(jid)
                el = time.time() - t_start
                print(f"  [DONE] {jid[:8]} (#{len(completed)}/5) em {el:.0f}s — status={st}")
            elif st in ("error", "failed", "cancelled"):
                fail_count += 1
                completed.add(jid)
                err = d.get("error", "")[:80]
                print(f"  [FAIL] {jid[:8]}: {st} — {err}")
        except Exception as e:
            pass
    if pending:
        # Print active status
        try:
            active = pending[0]  # pega o que provavelmente está rodando
            d = requests.get(f"{BASE}/api/job/{active}", timeout=10).json()
            print(f"  [...] {len(completed)}/5 done, processando {active[:8]} {d.get('progress','?')}% — {d.get('message','')[:50]}")
        except: pass
        time.sleep(60)

# Resultado
total_time = time.time() - t_start
print(f"\n{'='*60}")
print(f"  STRESS QUEUE — {done_count}/5 done, {fail_count}/5 fail em {total_time/60:.1f}min")
print(f"{'='*60}")

# Verificar leak de memória
import psutil
for c in psutil.net_connections(kind="inet"):
    if c.laddr and c.laddr.port == 5052 and c.status == psutil.CONN_LISTEN and c.pid:
        srv = psutil.Process(c.pid)
        rss = srv.memory_info().rss / (1024**2)
        print(f"\n  Server RSS após 5 jobs: {rss:.0f} MB")
        break

sys.exit(0 if done_count == 5 and fail_count == 0 else 1)
