"""Comparativo visual MuseTalk vs Wav2Lip — submete mesmo job, retorna URL.
Uso: python compare_engines.py <label>  (label vira parte do output filename)"""
import sys, os, io, time, shutil, requests
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "http://localhost:5052"
UP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
LABEL = sys.argv[1] if len(sys.argv) > 1 else "test"

def best():
    b, bs = None, 0
    for f in os.listdir(UP):
        if f.endswith((".jpg", ".jpeg")):
            p = os.path.join(UP, f); s = os.path.getsize(p)
            if 30000 < s < 600000 and s > bs: bs = s; b = p
    return b

IMG = best()
# Script CURTO (~15s) — força MuseTalk se disponível (cabe em chunk único)
SCRIPT = ("Olá, eu sou um avatar gerado por inteligência artificial. "
          "Este vídeo demonstra a qualidade da sincronização labial. "
          "Compare os movimentos da minha boca com a fala.")
print(f"=== {LABEL.upper()} ===  IMG={os.path.basename(IMG)}")
with open(IMG, "rb") as f:
    r = requests.post(BASE + "/api/generate",
        data={"script": SCRIPT, "voice": "pt-BR-FranciscaNeural",
              "engine": "edge-tts", "enhancer": "gfpgan",
              "preprocess": "crop", "still_mode": "false"},
        files={"image": (os.path.basename(IMG), f, "image/jpeg")},
        timeout=30)
jid = r.json().get("job_id", "")
print(f"  job {jid[:8]} submetido")
t0 = time.time()
last_msg = ""
while time.time() - t0 < 1800:
    d = requests.get(f"{BASE}/api/job/{jid}", timeout=10).json()
    st, msg = d.get("status"), d.get("message", "")[:75]
    if msg != last_msg:
        print(f"  [{st}] {d.get('progress', 0)}% {msg}")
        last_msg = msg
    if st == "done":
        out = d.get("output_path", "")
        # Renomear pra compare_<LABEL>.mp4 pra fácil identificação
        new_path = os.path.join(os.path.dirname(out), f"compare_{LABEL}.mp4")
        shutil.copy2(out, new_path)
        url = f"http://localhost:5052/outputs/compare_{LABEL}.mp4"
        elapsed = (time.time() - t0) / 60
        sz = os.path.getsize(new_path) // 1024
        print(f"\n  PRONTO em {elapsed:.1f}min — {sz}KB")
        print(f"  URL: {url}")
        sys.exit(0)
    if st in ("failed", "error", "cancelled"):
        print(f"  FAIL: {d.get('error', msg)}"); sys.exit(1)
    time.sleep(6)
print("TIMEOUT"); sys.exit(1)
