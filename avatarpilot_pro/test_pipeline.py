"""
AvatarPilot Pro — Pipeline Test Script
Tests: crop logic, composite, full pipeline end-to-end
"""
import sys, os, time, json, requests

# Fix cp1252 console crash on Windows com chars Unicode (✓ ✗)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:5052"
UPLOADS  = os.path.join(os.path.dirname(__file__), "uploads")
TESTS    = []

def log(msg): print(f"  {msg}")
def ok(msg):  print(f"  [OK] {msg}")
def err(msg): print(f"  [ERR] {msg}"); sys.exit(1)

def submit_job(image_path: str, text: str, voice="en-US-GuyNeural", label="") -> str:
    print(f"\n[TEST] {label or image_path}")
    with open(image_path, "rb") as f:
        r = requests.post(f"{BASE_URL}/api/generate", data={
            "script": text,
            "voice": voice,
            "engine": "edge-tts",
            "enhancer": "gfpgan",
            "preprocess": "full",
        }, files={"image": (os.path.basename(image_path), f, "image/jpeg")}, timeout=30)
    if r.status_code != 200:
        err(f"Submit failed: {r.status_code} {r.text[:200]}")
    job_id = r.json().get("job_id", "")
    if not job_id: err(f"No job_id: {r.text}")
    log(f"Job submitted: {job_id}")
    return job_id

def wait_job(job_id: str, timeout_s=1200) -> dict:
    t0 = time.time()
    last_msg = ""
    while time.time() - t0 < timeout_s:
        r = requests.get(f"{BASE_URL}/api/job/{job_id}", timeout=10)
        d = r.json()
        status = d.get("status", "?")
        prog   = d.get("progress", 0)
        msg    = d.get("message", "")
        if msg != last_msg:
            log(f"  [{status}] {prog}% — {msg}")
            last_msg = msg
        if status == "done":
            ok(f"DONE in {time.time()-t0:.0f}s | output: {d.get('output_path','?')}")
            return d
        if status == "failed":
            err(f"FAILED: {d.get('error','?')}")
        time.sleep(5)
    err(f"Timeout after {timeout_s}s")

def check_output(result: dict, min_size_kb=100):
    out = result.get("output_path", "")
    if not out or not os.path.exists(out):
        err(f"Output missing: {out}")
    size_kb = os.path.getsize(out) // 1024
    if size_kb < min_size_kb:
        err(f"Output too small: {size_kb}KB < {min_size_kb}KB")
    ok(f"Output OK: {size_kb}KB → {out}")
    return out

# ── Find test images ──────────────────────────────────────────────────────────
def find_image(keywords: list) -> str:
    for f in sorted(os.listdir(UPLOADS)):
        if f.endswith(".jpg") and any(k in f for k in keywords):
            return os.path.join(UPLOADS, f)
    # Fallback: any jpg
    for f in sorted(os.listdir(UPLOADS)):
        if f.endswith(".jpg"):
            return os.path.join(UPLOADS, f)
    err("No images in uploads/")

# ── TEST 1: Short audio (10s) with any portrait image ────────────────────────
SHORT_TEXT = "Testing the avatar system. The lip synchronization must be perfect. One two three four five."

print("\n" + "="*60)
print("TEST 1: Short audio, portrait image")
print("="*60)
img1 = find_image(["test_face", "enh_", "avatar_c5"])
log(f"Using image: {img1}")
job1 = submit_job(img1, SHORT_TEXT, label="Short test (portrait)")
result1 = wait_job(job1, timeout_s=1800)  # 30min — CodeFormer/GFPGAN podem demorar
out1 = check_output(result1, min_size_kb=200)

# ── TEST 2: Medium audio (30s) ────────────────────────────────────────────────
MEDIUM_TEXT = (
    "Welcome to AvatarPilot Pro. "
    "This revolutionary platform uses advanced artificial intelligence "
    "to create incredibly realistic talking avatar videos. "
    "Whether you need content for marketing, education, or entertainment, "
    "our system delivers professional quality results every time. "
    "Experience the future of video creation today."
)

print("\n" + "="*60)
print("TEST 2: Medium audio (~30s)")
print("="*60)
img2 = find_image(["avatar_03", "avatar_2f", "avatar_8a"])
log(f"Using image: {img2}")
job2 = submit_job(img2, MEDIUM_TEXT, label="Medium test (30s)")
result2 = wait_job(job2, timeout_s=1800)  # 30min — pipeline completo
out2 = check_output(result2, min_size_kb=500)

print("\n" + "="*60)
print("ALL TESTS PASSED")
print("="*60)
print(f"Test 1 output: {out1}")
print(f"Test 2 output: {out2}")
print("\nPlease review the videos manually for quality:")
print("  - Face visible (not covered)")
print("  - Lip sync accurate")
print("  - Natural body movement")
print("  - Full resolution 1280x720")
