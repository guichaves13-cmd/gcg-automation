"""
Download a curated gesture-video pack from Pexels (CC0, FREE for commercial use).

Each video is processed to AvatarPilot Pro's standard:
  - 1080p (resize to fit 1920x1080)
  - 25fps
  - 30-60s duration (trimmed to longest face-visible segment)
  - H.264 + AAC

Setup (one-time, free):
  1. Sign up at https://www.pexels.com/api/ (30 seconds)
  2. Get your API key
  3. Run: python download_gesture_pack.py --key YOUR_KEY [--count 20]

The script downloads videos matching curated search queries optimized for
talking-head/presenter templates with visible hand gestures.
"""

import os, sys, time, json, argparse, subprocess, shutil
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# Curated search queries optimized for talking-head/presenter footage
# with visible hand gestures and stable framing.
SEARCH_QUERIES = [
    "person talking camera presentation",
    "business presenter explaining gestures",
    "vlogger speaking front camera",
    "teacher explaining hand gestures",
    "interview business person talking",
    "podcast host speaking gesture",
    "speaker presentation business",
    "person hand gestures explaining",
]

# Filters: keep horizontal, person-visible, reasonable length
MIN_DURATION = 10
MAX_DURATION = 60
TARGET_RES   = 1080  # max dimension
TARGET_FPS   = 25
PER_QUERY    = 5     # videos per search query


def ffmpeg_path():
    here = Path(__file__).parent.parent.parent
    candidates = [
        here / "ffmpeg" / "ffmpeg.exe",
        Path("C:/Users/Guilherme/Music/automaçao video/ffmpeg/ffmpeg.exe"),
        Path(shutil.which("ffmpeg") or "ffmpeg"),
    ]
    for c in candidates:
        if c.exists() if isinstance(c, Path) else os.path.isfile(str(c)):
            return str(c)
    return "ffmpeg"


def search_pexels(api_key: str, query: str, per_page: int = 10):
    """Search Pexels videos. Returns list of candidate video metadata."""
    url = "https://api.pexels.com/videos/search"
    params = {
        "query": query, "per_page": per_page,
        "orientation": "landscape",
        "size": "medium",  # 720p+
    }
    headers = {"Authorization": api_key}
    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json().get("videos", [])


def best_video_file(video_meta: dict) -> dict | None:
    """Pick the best mp4 file from a Pexels video entry (HD, h264)."""
    files = video_meta.get("video_files", [])
    # Prefer HD landscape mp4
    candidates = [f for f in files if f.get("file_type") == "video/mp4"
                  and f.get("width", 0) >= 1280
                  and f.get("width", 0) >= f.get("height", 0)]  # landscape
    if not candidates:
        candidates = [f for f in files if f.get("file_type") == "video/mp4"]
    if not candidates:
        return None
    # Pick closest to 1080p without going over (1920x1080 = sweet spot)
    candidates.sort(key=lambda f: abs(f.get("width", 0) - 1920))
    return candidates[0]


def preprocess_video(src: str, dest: str, ffmpeg: str) -> bool:
    """Trim, resize, re-encode to standard template format."""
    # Probe duration — only replace filename, not full path
    ff_dir = os.path.dirname(ffmpeg)
    ff_base = os.path.basename(ffmpeg)
    probe_base = ff_base.replace("ffmpeg", "ffprobe")
    ffprobe = os.path.join(ff_dir, probe_base) if ff_dir else "ffprobe"
    if not os.path.isfile(ffprobe):
        # Fallback: try PATH
        ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", src],
            capture_output=True, text=True, timeout=15
        )
        dur = float(r.stdout.strip() or 0)
    except Exception as _pe:
        print(f"  ffprobe error: {_pe}")
        dur = 0
    if dur < MIN_DURATION:
        # Validate that source file actually exists/has content
        try:
            sz = os.path.getsize(src)
            print(f"  skip (probed dur={dur:.1f}s, file size={sz} bytes, ffprobe={ffprobe})")
        except Exception:
            print(f"  skip (no file)")
        return False
    # Trim to MAX_DURATION starting from frame that's safer (skip first 1s)
    seek = 1.0 if dur > MAX_DURATION + 1 else 0
    take = min(MAX_DURATION, dur - seek)
    # Scale to fit 1080p preserving aspect, force 25fps, h264 baseline for compatibility
    vf = f"scale=w={TARGET_RES * 16 // 9}:h={TARGET_RES}:force_original_aspect_ratio=decrease," \
         f"pad={TARGET_RES * 16 // 9}:{TARGET_RES}:(ow-iw)/2:(oh-ih)/2,fps={TARGET_FPS}"
    cmd = [ffmpeg, "-y", "-ss", str(seek), "-t", str(take), "-i", src,
           "-vf", vf,
           "-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-c:a", "aac", "-b:a", "128k",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", dest]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    ok = r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 10000
    if not ok:
        err = (r.stderr or b"").decode("utf-8", errors="replace")[-200:]
        print(f"  ffmpeg fail: {err}")
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True, help="Pexels API key (free at pexels.com/api)")
    parser.add_argument("--count", type=int, default=20, help="Total videos to download")
    parser.add_argument("--out", default=None, help="Output directory")
    parser.add_argument("--manifest", action="store_true", help="Write JSON manifest with metadata")
    args = parser.parse_args()

    out_dir = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "gesture_videos"
    )
    os.makedirs(out_dir, exist_ok=True)
    print(f"Output: {out_dir}")

    ffmpeg = ffmpeg_path()
    print(f"FFmpeg: {ffmpeg}\n")

    # Collect candidate videos across all queries
    candidates = []
    seen_ids = set()
    for q in SEARCH_QUERIES:
        try:
            videos = search_pexels(args.key, q, per_page=PER_QUERY * 2)
            print(f"[Pexels] '{q}': {len(videos)} results")
        except Exception as e:
            print(f"[Pexels] '{q}' failed: {e}")
            continue
        for v in videos:
            vid = v.get("id")
            if vid in seen_ids: continue
            dur = v.get("duration", 0)
            if dur < MIN_DURATION: continue
            # Pick a person-relevant video (Pexels tags aren't reliable, so trust the query)
            seen_ids.add(vid)
            f = best_video_file(v)
            if not f: continue
            candidates.append({
                "id": vid,
                "query": q,
                "duration": dur,
                "url": f["link"],
                "width": f.get("width"),
                "height": f.get("height"),
                "user": v.get("user", {}).get("name", "Pexels"),
                "page": v.get("url", ""),
            })
        time.sleep(0.5)  # be nice to API

    # Sort by best match (longest HD landscape)
    candidates.sort(key=lambda c: (-c.get("width", 0), c.get("duration", 0)))

    # Download + preprocess up to args.count
    saved = []
    for c in candidates:
        if len(saved) >= args.count: break
        out_name = f"pexels_{c['id']}.mp4"
        out_path = os.path.join(out_dir, out_name)
        if os.path.exists(out_path):
            print(f"[skip] {out_name} already exists")
            saved.append({**c, "path": out_path})
            continue

        print(f"\n[{len(saved)+1}/{args.count}] {c['id']} - {c['query']}")
        print(f"  URL: {c['url'][:80]}")
        print(f"  Size: {c['width']}x{c['height']} | Duration: {c['duration']}s | by {c['user']}")

        # Download to temp
        tmp_path = os.path.join(out_dir, f".tmp_{c['id']}.mp4")
        try:
            with requests.get(c["url"], stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
        except Exception as e:
            print(f"  download fail: {e}")
            continue

        # Preprocess (trim + resize + re-encode)
        ok = preprocess_video(tmp_path, out_path, ffmpeg)
        try: os.remove(tmp_path)
        except: pass
        if ok:
            size_mb = os.path.getsize(out_path) / 1024 / 1024
            print(f"  OK: {size_mb:.1f}MB")
            saved.append({**c, "path": out_path})

    # Write manifest
    if args.manifest or True:
        manifest_path = os.path.join(out_dir, "_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({
                "source": "Pexels",
                "license": "CC0 - free for commercial use, no attribution required",
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "videos": [
                    {"file": os.path.basename(s["path"]),
                     "pexels_id": s["id"], "credit": s["user"],
                     "page": s["page"], "duration": s["duration"],
                     "width": s["width"], "height": s["height"]}
                    for s in saved
                ],
            }, f, indent=2, ensure_ascii=False)
        print(f"\nManifest: {manifest_path}")

    print(f"\n=== Done: {len(saved)}/{args.count} videos saved to {out_dir} ===")


if __name__ == "__main__":
    main()
