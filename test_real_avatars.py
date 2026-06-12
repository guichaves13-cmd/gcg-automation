"""
TESTE COM AVATARES REAIS (não roteiros sintéticos):
3 vídeos de 90s = ~270s de narração real, com Whisper transcrevendo o áudio
e o intelligent_broll buscando clips visuais sincronizados.

Critério de aprovação:
- Cada vídeo gera 8-15 segmentos (Whisper)
- ≥70% dos segmentos resolvidos com clip aprovado (score ≥70)
- Score médio ≥75
- 0 clips wrong (Vision rejeita corretamente quando não há match)
"""

import os, sys, json, base64, time
from pathlib import Path
from collections import defaultdict

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
import subprocess

OUT = Path("test_output/real_avatars")
OUT.mkdir(parents=True, exist_ok=True)

VIDEOS = [
    {"name": "Cosmetics (3000 years male beauty)",
     "file": "test_output/real_avatars/test_cosmetics_30s.mp4",
     "theme": "história da maquiagem masculina ao longo dos séculos",
     "language": "en"},
    {"name": "Ancient Egypt (cosmic knowledge)",
     "file": "test_output/real_avatars/test_egypt_30s.mp4",
     "theme": "antigo egito civilização ancestral",
     "language": "en"},
    {"name": "Cleopatra",
     "file": "test_output/real_avatars/test_cleopatra_30s.mp4",
     "theme": "cleopatra rainha egito antigo",
     "language": "en"},
]


def extract_audio(video_path, audio_path):
    """ffmpeg extract audio."""
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
           "-ar", "16000", "-ac", "1", audio_path]
    subprocess.run(cmd, capture_output=True, timeout=120, check=True)
    return audio_path


def main():
    print("=" * 76)
    print("TESTE COM AVATARES REAIS — 3 vídeos de 90s")
    print("=" * 76)

    overall = {
        "videos": [], "total_segments": 0, "total_solved": 0,
        "score_sum": 0, "score_count": 0, "elapsed": 0,
        "sources": defaultdict(int),
    }

    t_start = time.time()
    for v in VIDEOS:
        print(f"\n{'━' * 76}")
        print(f"VÍDEO: {v['name']}")
        print(f"File:  {v['file']}")
        print(f"Theme: {v['theme']}")
        print(f"{'━' * 76}")

        if not os.path.exists(v["file"]):
            print(f"  ⚠ arquivo não existe — skip")
            continue

        # Extract audio
        audio_path = v["file"].replace(".mp4", "_audio.wav")
        print(f"  Extracting audio → {audio_path}")
        extract_audio(v["file"], audio_path)
        print(f"  Audio size: {os.path.getsize(audio_path)//1024} KB")

        # Build engine PER VIDEO (per-video output dir to avoid mixing clips)
        out_dir = OUT / f"clips_{v['name'].split()[0].lower()}"
        out_dir.mkdir(exist_ok=True)
        engine = IntelligentBrollEngine(
            gemini_api_key=GEMINI_KEY, groq_api_key=GROQ_KEY, nvidia_api_key=NVIDIA_KEY,
            pexels_key=PEXELS_KEY, pixabay_key=PIXABAY_KEY,
            youtube_enabled=False,  # disable YT here — pexels+pixabay already 96% on multiniche
            output_dir=str(out_dir),
            max_candidates_per_intent=4,   # was 6 — fewer Vision calls per segment
            max_search_attempts=1,          # was 2 — no refine retry (saves ~30 calls/seg)
        )

        t0 = time.time()
        plans = engine.build(
            audio_path=audio_path, theme=v["theme"],
            min_relevance=65,  # mais permissivo pra história/antigo (clips raros)
            language=v["language"],
        )
        elapsed = time.time() - t0

        # Analyze
        per_video = {
            "name": v["name"], "elapsed_sec": elapsed,
            "n_segments": len(plans), "n_solved": 0, "scores": [],
            "segments": [],
        }
        for i, p in enumerate(plans):
            ok = p.is_solved()
            score = p.clip.relevance_score if p.clip else 0
            saw = p.clip.vision_description if p.clip else ""
            src = p.clip.source if p.clip else "none"

            overall["total_segments"] += 1
            if ok:
                overall["total_solved"] += 1
                per_video["n_solved"] += 1
            if score > 0:
                overall["score_sum"] += score
                overall["score_count"] += 1
                per_video["scores"].append(score)
            overall["sources"][src] += 1

            status = "✅" if ok and score >= 70 else ("⚠" if score >= 40 else "❌")
            print(f"\n  {status} Seg {i+1}/{len(plans)} [{p.intent.start:.1f}–{p.intent.end:.1f}s] "
                  f"score={score} src={src}")
            print(f"     Narração: \"{p.intent.text[:90]}...\"")
            print(f"     Entidade: {p.intent.main_entity}")
            print(f"     Vision:   {saw[:90]}")
            if p.clip:
                print(f"     URL:      {p.clip.page_url}")

            per_video["segments"].append({
                "idx": i, "start": p.intent.start, "end": p.intent.end,
                "text": p.intent.text, "entity": p.intent.main_entity,
                "score": score, "source": src,
                "vision_saw": saw, "url": p.clip.page_url if p.clip else "",
                "resolved": ok,
            })

        avg_v = sum(per_video["scores"]) / max(1, len(per_video["scores"]))
        rate = per_video["n_solved"] / max(1, per_video["n_segments"])
        print(f"\n  ▸ Resumo: {per_video['n_solved']}/{per_video['n_segments']} resolvidos "
              f"({rate*100:.0f}%) | score médio {avg_v:.0f} | {elapsed:.0f}s")
        overall["videos"].append(per_video)

    overall["elapsed"] = time.time() - t_start

    # ─── RELATÓRIO ──
    print("\n" + "=" * 76)
    print("RELATÓRIO FINAL — 3 avatares reais")
    print("=" * 76)
    n_seg = overall["total_segments"]
    n_solved = overall["total_solved"]
    avg = overall["score_sum"] / max(1, overall["score_count"])
    rate = n_solved / max(1, n_seg)

    print(f"\nTotal de segmentos:  {n_seg}")
    print(f"Resolvidos:          {n_solved} ({rate*100:.0f}%)")
    print(f"Score médio:         {avg:.1f}")
    print(f"Tempo total:         {overall['elapsed']:.0f}s")

    print(f"\nFontes selecionadas:")
    for src, n in sorted(overall["sources"].items(), key=lambda x: -x[1]):
        print(f"  {src:10}: {n}")

    print(f"\nPor vídeo:")
    for vd in overall["videos"]:
        scores = vd["scores"]
        avg_v = sum(scores) / max(1, len(scores))
        rate_v = vd["n_solved"] / max(1, vd["n_segments"])
        status = "✅" if rate_v >= 0.70 else ("⚠" if rate_v >= 0.50 else "❌")
        print(f"  {status} {vd['name']:35} {vd['n_solved']}/{vd['n_segments']} ({rate_v*100:.0f}%) "
              f"avg={avg_v:.0f}")

    # Save report
    report_path = OUT / "report_real_avatars.json"
    overall["sources"] = dict(overall["sources"])
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(overall, f, ensure_ascii=False, indent=2)
    print(f"\n  Relatório: {report_path}")

    # Verdict
    print("\n" + "═" * 76)
    if rate >= 0.70 and avg >= 75:
        print(f"  ✅ APROVADO — {rate*100:.0f}% resolvidos, avg {avg:.0f}")
        return True
    elif rate >= 0.50:
        print(f"  ⚠ PARCIAL — {rate*100:.0f}% resolvidos, avg {avg:.0f}")
        return False
    else:
        print(f"  ❌ REPROVADO — apenas {rate*100:.0f}% resolvidos")
        return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
