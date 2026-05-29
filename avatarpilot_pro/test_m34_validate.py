"""Valida o fix do M3.4: vídeo de 180s (gesture pack) deve completar via Wav2Lip
sem travar, saindo 1920x1080."""
import sys, os, time, json, requests, subprocess, shutil
if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8",errors="replace")
BASE="http://localhost:5052"
UP=os.path.join(os.path.dirname(os.path.abspath(__file__)),"uploads")
def best_image():
    b,bs=None,0
    for f in os.listdir(UP):
        if f.endswith((".jpg",".jpeg")):
            p=os.path.join(UP,f); s=os.path.getsize(p)
            if 30000<s<600000 and s>bs: bs=s;b=p
    return b
IMG=best_image()
base=("A inteligência artificial transforma a criação de conteúdo digital com qualidade "
      "profissional e rapidez impressionante todos os dias. ")
script=(base*((180*14)//len(base)+1))[:180*14]  # ~180s
print(f"=== M3.4 validação — vídeo ~180s ({len(script)} chars) ===")
with open(IMG,"rb") as f:
    r=requests.post(f"{BASE}/api/generate",
        data={"script":script,"voice":"pt-BR-FranciscaNeural","engine":"edge-tts","enhancer":"none"},
        files={"image":(os.path.basename(IMG),f,"image/jpeg")},timeout=30)
jid=r.json().get("job_id","")
print(f"  job {jid[:8]} submetido. Aguardando (max 90min)...")
t0=time.time(); last=""
while time.time()-t0<5400:
    d=requests.get(f"{BASE}/api/job/{jid}",timeout=10).json()
    st,msg=d.get("status"),d.get("message","")
    if msg!=last: print(f"    [{st}] {d.get('progress',0)}% — {msg}"); last=msg
    if st=="done":
        out=d.get("output_path","")
        ffp=shutil.which("ffprobe") or "ffprobe"
        pr=subprocess.run([ffp,"-v","quiet","-print_format","json","-show_streams","-show_format",out],capture_output=True,text=True,timeout=15)
        info=json.loads(pr.stdout); vs=next((s for s in info.get("streams",[]) if s.get("codec_type")=="video"),{})
        w,h=vs.get("width",0),vs.get("height",0); dur=float(info.get("format",{}).get("duration",0))
        okk=(w==1920 and h==1080 and dur>=150)
        print(f"\n  {'[PASS]' if okk else '[FAIL]'} M3.4 — {dur:.1f}s {w}x{h} em {(time.time()-t0)/60:.1f}min")
        sys.exit(0 if okk else 1)
    if st in ("failed","error","cancelled"):
        print(f"\n  [FAIL] M3.4 — job {st}: {d.get('error',d.get('message',''))[:100]}"); sys.exit(1)
    time.sleep(20)
print("\n  [FAIL] M3.4 — timeout 90min"); sys.exit(1)
