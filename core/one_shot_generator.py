"""
One-shot video generator — user provides only title + theme + language;
system writes script via LLM, then runs the full pipeline.

Pipeline:
1. LLM (Groq) writes engaging 60-120s script from title
2. Edge-TTS narrates with chosen voice
3. IntelligentBrollEngine builds B-roll plan
4. video_composer assembles video (TTS-only or avatar-corner mode)
5. karaoke_subtitles burns word-level captions

Two modes:
- mode="tts_only": no avatar, full-screen B-roll
- mode="avatar_corner": real persona video in 20% corner
"""

import os
import json
import requests
import asyncio
from pathlib import Path


# Map language → default voice
_DEFAULT_VOICES = {
    "en": "en-US-AndrewNeural",
    "es": "es-MX-JorgeNeural",
    "pt": "pt-BR-AntonioNeural",
    "fr": "fr-FR-HenriNeural",
    "de": "de-DE-ConradNeural",
    "it": "it-IT-DiegoNeural",
}


_SCRIPT_PROMPT = """You are an ELITE viral scriptwriter for top documentary channels
(MrBeast tier: Veritasium, History Channel, Real Stories). Write a narration script
that DOMINATES retention for the first 2 minutes. Topic:

TITLE: {title}
THEME: {theme}
LANGUAGE: {language}
TARGET LENGTH: {target_sec} seconds (~{target_words} words)

═══════════════════════════════════════════════════════════════════════════════
RETENTION ENGINEERING RULES (apply ALL of them):
═══════════════════════════════════════════════════════════════════════════════

1. HOOK (first 8 seconds — MAKE OR BREAK)
   Open with ONE of these patterns:
   - SHOCKING NUMBER ("In 1923, a single rope killed 167 people…")
   - CONTROVERSIAL CLAIM ("Everything you learned about Cleopatra was a lie.")
   - IMPOSSIBLE QUESTION ("How can a 200-ton block float on air?")
   - VISUAL MYSTERY ("The men in this photo all vanished within 24 hours.")
   - DIRECT CHALLENGE ("If you can name the largest empire in history, you're wrong.")
   ❌ NEVER start with: "Today we'll talk about…" / "Hello everyone" / "Welcome".
   ✅ Start IN MEDIA RES — drop the viewer mid-action.

2. OPEN LOOPS (curiosity gaps)
   Every 15-20 seconds, plant a PROMISE you'll deliver later:
   - "But the real shock came when…"
   - "What happened next no one expected."
   - "And there's one detail historians refuse to discuss."
   These keep viewers watching to "close the loop".

3. PATTERN INTERRUPTS
   Vary sentence rhythm: short. Then medium length. Then sudden long ones
   that build tension carefully before cracking like a whip. Then short again.
   Change pace every 8-15 seconds. Use one-word sentences for impact: "Gone."

4. CONCRETE VISUAL ANCHORS
   Every sentence should be FILMABLE. Replace abstractions with objects:
   - ❌ "His power grew enormously"
   - ✅ "His army of 50,000 swordsmen marched into 47 cities"
   Use: numbers, names, dates, weights, distances, colors, body counts.

5. EMOTIONAL TRIGGERS (rotate them)
   Curiosity → Awe → Fear → Disgust → Empathy → Anger → Hope → Shock
   Hit at least 4 different emotions in 60s.

6. THE TWIST
   Around 60-70% of the way through, deliver an UNEXPECTED REVELATION
   that recontextualizes everything before it.

7. CLIFFHANGER ENDING
   End with a question or implication that makes viewers think for hours.
   ❌ NO "thanks for watching" / "subscribe".
   ✅ "And to this day, no one knows where the bodies went."

═══════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (JSON only, no markdown):
═══════════════════════════════════════════════════════════════════════════════
{{
  "hook_strategy": "<which hook pattern you used>",
  "script": "<the full narration, target ~{target_words} words, in {language}>",
  "open_loops": ["<promise 1>", "<promise 2>", "..."],
  "estimated_duration_sec": <integer>,
  "key_visuals": ["<concrete visual 1>", "<concrete visual 2>", "..."],
  "emotional_arc": ["<emotion 1>", "<emotion 2>", "..."]
}}

LANGUAGE LOCK: write the script EXCLUSIVELY in {language}. No code-mixing.
TONE: serious, cinematic, documentary-grade. Like Ken Burns or BBC Earth.
NO MARKDOWN. NO BULLETS. Clean sentences only."""


def write_script_from_title(title: str, theme: str, language: str,
                            groq_key: str, target_sec: int = 90) -> dict:
    """Use Groq llama-3.3-70b to write a viral retention-optimized script.

    Args:
        target_sec: target narration duration in seconds (controls word count).
            Use 60-90 for shorts, 120-180 for medium, 300+ for long-form.
    """
    if not groq_key:
        return {"script": title, "estimated_duration_sec": 30, "key_visuals": [theme]}

    # Edge-TTS at -5% rate ≈ 2.4 words/sec → estimate word target
    target_words = int(target_sec * 2.4)

    prompt = _SCRIPT_PROMPT.format(
        title=title, theme=theme, language=language,
        target_sec=target_sec, target_words=target_words,
    )
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}",
                     "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.85,
                "max_tokens": min(4000, int(target_words * 4)),
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        return {
            "script": data.get("script", title)[:8000],
            "hook_strategy": data.get("hook_strategy", ""),
            "open_loops": data.get("open_loops", []),
            "estimated_duration_sec": int(data.get("estimated_duration_sec", target_sec)),
            "key_visuals": data.get("key_visuals", [])[:15],
            "emotional_arc": data.get("emotional_arc", []),
        }
    except Exception as e:
        print(f"  [script writer] err: {e}")
        return {"script": title, "estimated_duration_sec": 30, "key_visuals": [theme]}


async def _generate_tts(script: str, voice: str, output_path: str, rate: str = "-5%"):
    import edge_tts
    c = edge_tts.Communicate(script, voice, rate=rate)
    await c.save(output_path)


def generate_one_shot_video(
    title: str,
    theme: str,
    language: str = "en",
    voice: str = "",
    mode: str = "tts_only",        # 'tts_only' | 'avatar_corner' | 'avatar_fullbg'
    avatar_video: str = "",         # required if mode != 'tts_only'
    avatar_corner: str = "top_right",
    output_dir: str = "test_output/one_shot",
    add_karaoke: bool = True,
    target_sec: int = 90,           # target narration duration
    engine_kwargs: dict = None,
) -> dict:
    """Full one-shot generation: title → finished MP4.

    Returns dict with all paths + metadata.
    """
    import time
    from core.intelligent_broll import IntelligentBrollEngine
    from core.video_composer import (
        compose_tts_only, compose_avatar_corner, compose_avatar_fullbg,
    )
    from core.karaoke_subtitles import add_karaoke_to_video

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    job_id = f"{title[:30].replace(' ', '_').replace('/', '_')}_{language}"

    # Resolve voice
    if not voice:
        voice = _DEFAULT_VOICES.get(language[:2], "en-US-AndrewNeural")

    engine_kwargs = engine_kwargs or {}
    groq_key = engine_kwargs.get("groq_api_key", "")

    result = {
        "title": title, "theme": theme, "language": language, "voice": voice,
        "mode": mode, "job_id": job_id, "files": {},
    }

    # ── STEP 1: Script ───────────────────────────────────────────
    print(f"\n[1/5] Writing viral script for '{title}' ({language}, ~{target_sec}s)...")
    script_data = write_script_from_title(title, theme, language, groq_key,
                                          target_sec=target_sec)
    result["script"] = script_data["script"]
    result["key_visuals"] = script_data["key_visuals"]
    result["hook_strategy"] = script_data.get("hook_strategy", "")
    result["open_loops"] = script_data.get("open_loops", [])
    result["emotional_arc"] = script_data.get("emotional_arc", [])
    print(f"  → {len(script_data['script'].split())} words, "
          f"~{script_data['estimated_duration_sec']}s estimated")
    if script_data.get("hook_strategy"):
        print(f"  → hook strategy: {script_data['hook_strategy']}")

    # ── STEP 2: TTS ──────────────────────────────────────────────
    print(f"\n[2/5] TTS with {voice}...")
    audio_path = str(output_dir / f"{job_id}_audio.mp3")
    asyncio.run(_generate_tts(script_data["script"], voice, audio_path))
    result["files"]["audio"] = audio_path

    # ── STEP 3: B-roll Intelligence ──────────────────────────────
    print(f"\n[3/5] Building B-roll with IntelligentBrollEngine...")
    clips_dir = output_dir / f"{job_id}_clips"
    clips_dir.mkdir(exist_ok=True)
    engine = IntelligentBrollEngine(
        output_dir=str(clips_dir),
        max_candidates_per_intent=engine_kwargs.get("max_candidates_per_intent", 4),
        max_search_attempts=engine_kwargs.get("max_search_attempts", 1),
        youtube_enabled=engine_kwargs.get("youtube_enabled", False),
        **{k: v for k, v in engine_kwargs.items()
           if k in {"gemini_api_key", "groq_api_key", "nvidia_api_key",
                    "pexels_key", "pixabay_key"}},
    )
    t0 = time.time()
    plans = engine.build(
        audio_path=audio_path, theme=theme,
        min_relevance=60, language=language,
    )
    elapsed = time.time() - t0
    solved = sum(1 for p in plans if p.is_solved())
    result["broll_solved"] = solved
    result["broll_total"] = len(plans)
    result["broll_elapsed_sec"] = elapsed
    print(f"  → {solved}/{len(plans)} segments resolved in {elapsed:.0f}s")

    # ── STEP 4: Compose ──────────────────────────────────────────
    print(f"\n[4/5] Composing in '{mode}' mode...")
    composed_path = str(output_dir / f"{job_id}_composed.mp4")
    if mode == "tts_only":
        compose_tts_only(plans, audio_path, composed_path)
    elif mode == "avatar_corner":
        if not avatar_video or not os.path.exists(avatar_video):
            raise ValueError(f"avatar_video required for avatar_corner mode: {avatar_video}")
        compose_avatar_corner(plans, avatar_video, composed_path, corner=avatar_corner)
    elif mode == "avatar_fullbg":
        if not avatar_video or not os.path.exists(avatar_video):
            raise ValueError(f"avatar_video required for avatar_fullbg mode")
        compose_avatar_fullbg(plans, avatar_video, composed_path)
    else:
        raise ValueError(f"Unknown mode: {mode}")
    result["files"]["composed"] = composed_path

    # ── STEP 5: Karaoke subs ─────────────────────────────────────
    final_path = str(output_dir / f"{job_id}_FINAL.mp4")
    if add_karaoke:
        print(f"\n[5/5] Burning karaoke subtitles...")
        try:
            # For avatar modes, use original audio for word timestamps
            audio_for_subs = audio_path if mode == "tts_only" else avatar_video
            add_karaoke_to_video(composed_path, audio_for_subs, final_path,
                                language=language)
            result["files"]["final"] = final_path
            result["karaoke"] = True
        except Exception as e:
            print(f"  [karaoke err] {e}")
            result["files"]["final"] = composed_path
            result["karaoke"] = False
            result["karaoke_error"] = str(e)
    else:
        result["files"]["final"] = composed_path
        result["karaoke"] = False

    return result
