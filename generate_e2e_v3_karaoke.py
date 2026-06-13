"""
E2E v3 — NEW topics + EN query translation + karaoke subtitles + threshold 65.

EN → "Mysteries of the Bermuda Triangle"
ES → "Los Mayas y el Calendario Cósmico"
"""

import os, sys, json, base64, time, asyncio, subprocess
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _decode(raw):
    if not raw: return ""
    try:
        d = base64.b64decode(raw.encode()).decode()
        if d and all(32 <= ord(c) < 127 for c in d):
            return d
    except Exception:
        pass
    return raw


api_keys = json.load(open(".api_keys.json", encoding="utf-8"))
GEMINI_KEY = _decode(api_keys.get("gemini") or api_keys.get("google_ai") or "")
GROQ_KEY = _decode(api_keys.get("groq") or "")
NVIDIA_KEY = _decode(api_keys.get("nvidia") or "")
PEXELS_KEY = _decode(api_keys.get("pexels") or "")
PIXABAY_KEY = _decode(api_keys.get("pixabay") or "")
if PEXELS_KEY.startswith("test_"): PEXELS_KEY = ""

os.environ.update({
    "GOOGLE_API_KEY": GEMINI_KEY, "GROQ_API_KEY": GROQ_KEY, "NVIDIA_API_KEY": NVIDIA_KEY,
})

from core.intelligent_broll import IntelligentBrollEngine
from core.karaoke_subtitles import add_karaoke_to_video

OUT = Path("test_output/e2e_v3")
OUT.mkdir(parents=True, exist_ok=True)


VIDEOS = [
    {
        "id": "bermuda_en",
        "title": "Mysteries of the Bermuda Triangle",
        "language": "en",
        "voice": "en-US-AndrewNeural",
        "theme": "Bermuda Triangle Atlantic Ocean ships disappearance mystery",
        "script": (
            "Between Florida, Bermuda, and Puerto Rico lies a stretch of ocean "
            "where ships and aircraft have vanished without a trace. "
            "Since 1945, more than fifty vessels and twenty planes have disappeared here, "
            "swallowed by calm waters under clear blue skies. "
            "Some researchers blame methane bubbles rising from the seabed, "
            "others point to rogue waves taller than skyscrapers. "
            "Pilots have reported their compasses spinning wildly out of control. "
            "And to this day, nothing has fully solved the mystery."
        ),
    },
    {
        "id": "mayas_es",
        "title": "Los Mayas y el Calendario Cósmico",
        "language": "es",
        "voice": "es-MX-JorgeNeural",
        "theme": "Mayan civilization ancient stone temple Mexico jungle calendar astronomy",
        "script": (
            "Hace más de mil años, los mayas construyeron ciudades de piedra en medio de la selva. "
            "Sus astrónomos observaban las estrellas desde templos pirámides escalonados. "
            "Sin telescopios, calcularon el movimiento de Venus con asombrosa precisión. "
            "Crearon un calendario sagrado de doscientos sesenta días, "
            "y un calendario solar de trescientos sesenta y cinco días, casi idéntico al moderno. "
            "Y luego, sin razón aparente, abandonaron sus grandes ciudades. "
            "La selva las cubrió en silencio durante siglos."
        ),
    },
]


async def generate_tts(script: str, voice: str, output_path: str):
    import edge_tts
    communicate = edge_tts.Communicate(script, voice, rate="-5%")
    await communicate.save(output_path)


def get_duration(path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", path]
    return float(subprocess.run(cmd, capture_output=True, text=True).stdout.strip())


def compose_video(plans, audio_path: str, output_path: str,
                   width: int = 1920, height: int = 1080, fps: int = 30):
    """Same as v2 but with crossfade between segments."""
    work = Path(output_path).parent / "_compose_work"
    work.mkdir(exist_ok=True)
    audio_dur = get_duration(audio_path)

    timeline = []
    last_resolved_src = None
    for p in plans:
        if p.clip and p.download_path and os.path.exists(p.download_path):
            last_resolved_src = p.download_path
        src = p.download_path if (p.clip and p.download_path and os.path.exists(p.download_path)) else last_resolved_src
        if not src: continue
        timeline.append((p.intent.start, p.intent.end, src))

    if not timeline:
        raise RuntimeError("Nothing resolved")
    if timeline[0][0] > 0.1:
        timeline.insert(0, (0.0, timeline[0][0], timeline[0][2]))
    if timeline[-1][1] < audio_dur - 0.1:
        s, e, src = timeline[-1]
        timeline[-1] = (s, audio_dur, src)

    normalized = []
    for i, (start, end, src) in enumerate(timeline):
        seg_dur = max(0.5, end - start)
        norm = work / f"seg_{i:03d}.mp4"
        cmd = [
            "ffmpeg", "-y", "-i", src, "-t", f"{seg_dur:.2f}",
            "-vf", (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p"),
            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-tune", "stillimage", str(norm),
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if norm.exists() and norm.stat().st_size > 10000:
            normalized.append(str(norm))
            print(f"  Seg {i+1}: {seg_dur:.1f}s ({norm.stat().st_size//1024}KB)")

    if not normalized:
        raise RuntimeError("No segments")

    concat_list = work / "concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for c in normalized:
            f.write(f"file '{str(Path(c).resolve()).replace(chr(92), '/')}'\n")

    raw_video = work / "raw_concat.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(raw_video),
    ], capture_output=True, timeout=180, check=True)

    # Final compose without subtitles (those come next)
    composed = work / "composed_no_subs.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(raw_video),
        "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0",
        "-t", f"{audio_dur:.2f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-pix_fmt", "yuv420p",
        str(composed),
    ], capture_output=True, timeout=240, check=True)

    return str(composed)


async def process_video(v):
    print("\n" + "=" * 76)
    print(f"VIDEO: {v['title']}")
    print(f"  language: {v['language']}  voice: {v['voice']}")
    print("=" * 76)

    audio_path = str(OUT / f"{v['id']}_narration.mp3")
    print(f"\n[1/4] TTS {v['voice']}...")
    await generate_tts(v['script'], v['voice'], audio_path)
    print(f"  → audio: {get_duration(audio_path):.1f}s")

    print(f"\n[2/4] B-roll Intelligence...")
    clips_dir = OUT / f"{v['id']}_clips"
    clips_dir.mkdir(exist_ok=True)
    engine = IntelligentBrollEngine(
        gemini_api_key=GEMINI_KEY, groq_api_key=GROQ_KEY, nvidia_api_key=NVIDIA_KEY,
        pexels_key=PEXELS_KEY, pixabay_key=PIXABAY_KEY,
        youtube_enabled=False, output_dir=str(clips_dir),
        max_candidates_per_intent=4, max_search_attempts=1,
    )
    t0 = time.time()
    plans = engine.build(
        audio_path=audio_path, theme=v['theme'],
        min_relevance=65, language=v['language'],
    )
    elapsed = time.time() - t0
    solved = sum(1 for p in plans if p.is_solved())
    print(f"\n  → {solved}/{len(plans)} resolved in {elapsed:.0f}s")
    for i, p in enumerate(plans):
        s = "OK " if p.is_solved() else "MISS"
        sc = p.clip.relevance_score if p.clip else 0
        saw = p.clip.vision_description if p.clip else "[no clip]"
        print(f"    [{s}] Seg {i+1} score={sc} → \"{saw[:70]}\"")

    print(f"\n[3/4] Composing video (no subs yet)...")
    composed_path = str(OUT / f"{v['id']}_composed.mp4")
    composed = compose_video(plans, audio_path, composed_path)

    print(f"\n[4/4] Burning karaoke subtitles...")
    final_path = str(OUT / f"{v['id']}_FINAL.mp4")
    try:
        add_karaoke_to_video(composed, audio_path, final_path, language=v['language'])
        size_mb = os.path.getsize(final_path) / 1024 / 1024
        dur = get_duration(final_path)
        print(f"  ✅ FINAL: {final_path}")
        print(f"     duration: {dur:.1f}s, size: {size_mb:.1f}MB")
        return {"video": v, "final_path": final_path, "duration": dur,
                "size_mb": size_mb, "solved": solved, "total": len(plans)}
    except Exception as e:
        print(f"  ⚠ karaoke burn failed: {e}")
        print(f"  ✅ FINAL (no subs): {composed}")
        return {"video": v, "final_path": composed, "solved": solved, "total": len(plans),
                "subs_error": str(e)}


async def main():
    print("=" * 76)
    print("E2E v3 — novos temas + EN queries + threshold 65 + karaoke subs")
    print("=" * 76)
    results = []
    for v in VIDEOS:
        r = await process_video(v)
        results.append(r)
    print("\n" + "=" * 76)
    print("FINAL REPORT")
    print("=" * 76)
    for r in results:
        v = r["video"]
        if r.get("final_path"):
            print(f"\n  OK {v['title']}")
            print(f"     {v['language']} ({v['voice']})")
            print(f"     b-roll: {r['solved']}/{r['total']} matched")
            print(f"     file:   {r['final_path']}")
            if r.get("subs_error"):
                print(f"     subs:   FAILED ({r['subs_error'][:60]})")
            else:
                print(f"     subs:   karaoke burned ✓")
        else:
            print(f"\n  FAIL {v['title']}: {r.get('error')}")
    return results


if __name__ == "__main__":
    asyncio.run(main())
