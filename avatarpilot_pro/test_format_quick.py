"""Validação rápida do fix output_format portrait/square (2 jobs curtos)."""
import sys, os, time, json, requests, subprocess, shutil
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "http://localhost:5052"
UP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
def best_image():
    b, bs = None, 0
    for f in os.listdir(UP):
        if f.endswith((".jpg",".jpeg")):
            p=os.path.join(UP,f); s=os.path.getsize(p)
            if 30000<s<600000 and s>bs: bs=s; b=p
    return b
IMG = best_image()
SCRIPT = "Teste rápido de formato de saída do AvatarPilot Pro para validação."
def dims(path):
    ffp = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([ffp,"-v","quiet","-print_format","json","-show_streams",path],capture_output=True,text=True,timeout=15)
    vs = next((s for s in json.loads(r.stdout).get("streams",[]) if s.get("codec_type")=="video"),{})
    return vs.get("width",0), vs.get("height",0)
def run(fmt, ew, eh):
    with open(IMG,"rb") as f:
        r = requests.post(f"{BASE}/api/generate",
            data={"script":SCRIPT,"voice":"pt-BR-FranciscaNeural","engine":"edge-tts","enhancer":"none","output_format":fmt},
            files={"image":(os.path.basename(IMG),f,"image/jpeg")}, timeout=30)
    jid = r.json().get("job_id","")
    print(f"  [{fmt}] job {jid[:8]} submetido, aguardando...")
    t0=time.time()
    while time.time()-t0 < 2400:
        d = requests.get(f"{BASE}/api/job/{jid}",timeout=10).json()
        st = d.get("status")
        if st=="done":
            out=d.get("output_path",""); w,h=dims(out)
            okk = (w==ew and h==eh)
            print(f"  {'[PASS]' if okk else '[FAIL]'} {fmt}: {w}x{h} (esperado {ew}x{eh}) em {(time.time()-t0)/60:.1f}min")
            return okk
        if st in ("failed","error","cancelled"):
            print(f"  [FAIL] {fmt}: job {st} — {d.get('error',d.get('message',''))[:80]}"); return False
        time.sleep(8)
    print(f"  [FAIL] {fmt}: timeout"); return False
print("=== Validação output_format (fix) ===")
r1 = run("portrait", 1080, 1920)
r2 = run("square", 1080, 1080)
print(f"\nRESULTADO: portrait={'OK' if r1 else 'FALHOU'} square={'OK' if r2 else 'FALHOU'}")
sys.exit(0 if (r1 and r2) else 1)
