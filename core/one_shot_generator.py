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


def _try_llm_chain(prompt: str, llm_keys: dict, max_groq_retries: int = 2) -> dict:
    """Try LLM providers in order: Groq → Gemini → Cerebras → OpenRouter.

    Returns parsed dict or None if all failed.
    """
    import time
    # Groq with quick retry
    if llm_keys.get("groq"):
        for attempt in range(max_groq_retries):
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {llm_keys['groq']}",
                             "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.85, "max_tokens": 4000,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=60,
                )
                if r.status_code == 429:
                    print(f"  [LLM] Groq 429 attempt {attempt+1}, trying next provider...")
                    break
                r.raise_for_status()
                data = json.loads(r.json()["choices"][0]["message"]["content"])
                return _normalize_script_output(data)
            except Exception as e:
                print(f"  [LLM] Groq attempt {attempt+1} err: {str(e)[:80]}")
                if attempt < max_groq_retries - 1:
                    time.sleep(5)

    # Gemini
    if llm_keys.get("gemini"):
        result = _write_via_gemini(prompt, llm_keys["gemini"])
        if result and result.get("script"):
            print(f"  [LLM] Via Gemini")
            return result

    # Cerebras (FREE, 60 req/min separate quota)
    if llm_keys.get("cerebras"):
        result = _write_via_cerebras(prompt, llm_keys["cerebras"])
        if result and result.get("script"):
            print(f"  [LLM] Via Cerebras")
            return result

    # OpenRouter (FREE tier models)
    if llm_keys.get("openrouter"):
        result = _write_via_openrouter(prompt, llm_keys["openrouter"])
        if result and result.get("script"):
            return result

    print(f"  [LLM] ALL providers failed")
    return None


def write_script_from_title(title: str, theme: str, language: str,
                            groq_key: str, target_sec: int = 90,
                            gemini_key: str = "",
                            cerebras_key: str = "",
                            openrouter_key: str = "",
                            max_retries: int = 3) -> dict:
    """Use Groq llama-3.3-70b (with retry + Gemini fallback) to write viral script.

    For long videos (target_sec > 240), generates in CHUNKS to stay under
    token limits and avoid Groq rate-limit drops.
    """
    target_words = int(target_sec * 2.4)

    llm_keys = {
        "groq": groq_key, "gemini": gemini_key,
        "cerebras": cerebras_key, "openrouter": openrouter_key,
    }

    # For long videos: chunk generation. Each chunk = ~3-4 min worth.
    if target_sec > 240:
        return _write_script_chunked(title, theme, language, llm_keys, target_sec)

    prompt = _SCRIPT_PROMPT.format(
        title=title, theme=theme, language=language,
        target_sec=target_sec, target_words=target_words,
    )

    result = _try_llm_chain(prompt, llm_keys)
    if result and result.get("script"):
        return result

    print(f"  [script writer] All providers failed, returning title-only fallback")
    return {"script": title, "estimated_duration_sec": 30, "key_visuals": [theme]}


def _write_via_gemini(prompt: str, gemini_key: str) -> dict:
    """Gemini-2.0-flash fallback for script writing."""
    try:
        from google import genai
        from google.genai import types as gtypes
        client = genai.Client(api_key=gemini_key)
        cfg = gtypes.GenerateContentConfig(
            temperature=0.85,
            response_mime_type="application/json",
            max_output_tokens=8000,
        )
        response = client.models.generate_content(
            model="gemini-2.0-flash", contents=prompt, config=cfg,
        )
        if response and response.text:
            data = json.loads(response.text.strip())
            return _normalize_script_output(data)
    except Exception as e:
        print(f"  [script writer] Gemini fallback err: {str(e)[:120]}")
    return None


def _write_via_cerebras(prompt: str, cerebras_key: str) -> dict:
    """Cerebras Llama-3.3-70b fallback (FREE tier: 60 req/min, 1M tokens/day).

    Same model as Groq but separate quota — perfect rate-limit failover.
    Get key at https://cloud.cerebras.ai/
    """
    if not cerebras_key:
        return None
    try:
        r = requests.post(
            "https://api.cerebras.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {cerebras_key}",
                     "Content-Type": "application/json"},
            json={
                "model": "gpt-oss-120b",  # OpenAI's open-source GPT-4 class model
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.85,
                "max_tokens": 4000,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        return _normalize_script_output(data)
    except Exception as e:
        print(f"  [script writer] Cerebras err: {str(e)[:120]}")
    return None


def _write_via_openrouter(prompt: str, openrouter_key: str) -> dict:
    """OpenRouter fallback using DeepSeek-V3 FREE tier model.

    Get key at https://openrouter.ai/
    Free tier has ~50 req/day on free models.
    """
    if not openrouter_key:
        return None
    # Try free models in order of quality (updated 2026)
    models_to_try = [
        "nvidia/nemotron-3-super-120b-a12b:free",  # 120b Nvidia super model
        "qwen/qwen3-next-80b-a3b-instruct:free",   # 80b Qwen3
        "google/gemma-4-31b-it:free",              # 31b Google Gemma
        "nvidia/nemotron-3-nano-30b-a3b:free",     # 30b Nvidia fallback
    ]
    for model in models_to_try:
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {openrouter_key}",
                         "Content-Type": "application/json",
                         "HTTP-Referer": "https://github.com/guichaves13-cmd/gcg-automation",
                         "X-Title": "GCG VideosMAX"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.85,
                    "max_tokens": 4000,
                    "response_format": {"type": "json_object"},
                },
                timeout=90,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            # OpenRouter sometimes wraps in markdown
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            data = json.loads(content.strip())
            print(f"  [script writer] OpenRouter via {model}")
            return _normalize_script_output(data)
        except Exception as e:
            print(f"  [script writer] OpenRouter ({model.split('/')[-1][:20]}) err: {str(e)[:80]}")
    return None


def _normalize_script_output(data: dict) -> dict:
    """Normalize LLM JSON output to standard schema."""
    return {
        "script": str(data.get("script", ""))[:8000],
        "hook_strategy": str(data.get("hook_strategy", "")),
        "open_loops": list(data.get("open_loops", []))[:8],
        "estimated_duration_sec": int(data.get("estimated_duration_sec", 90)),
        "key_visuals": list(data.get("key_visuals", []))[:15],
        "emotional_arc": list(data.get("emotional_arc", []))[:8],
    }


def _write_script_chunked(title: str, theme: str, language: str,
                          llm_keys: dict, target_sec: int) -> dict:
    """For long videos: write in 2-4 chunks via LLM chain (Groq→Gemini→Cerebras→OpenRouter).

    Each chunk continues seamlessly from the previous one.
    """
    import time
    n_chunks = max(2, (target_sec + 179) // 180)  # ~3 min per chunk
    chunk_sec = target_sec // n_chunks
    chunk_words = int(chunk_sec * 2.4)

    chunks = []
    hook_strategy = ""
    all_open_loops = []
    all_emotions = []
    all_visuals = []

    for i in range(n_chunks):
        if i == 0:
            role = "OPENING (first chunk — strong hook, set the stakes, plant open loops)"
        elif i == n_chunks - 1:
            role = "CONCLUSION (last chunk — pay off open loops, twist, cliffhanger)"
        else:
            role = f"MIDDLE PART {i}/{n_chunks-2} (build tension, deliver surprises)"

        prev_summary = ""
        if chunks:
            prev_text = " ".join(chunks)[-600:]
            prev_summary = f"\n\nPREVIOUS CHUNKS ENDED WITH: \"...{prev_text[-300:]}\"\nCONTINUE seamlessly from here. DO NOT repeat ideas."

        chunk_prompt = _SCRIPT_PROMPT.format(
            title=title, theme=theme, language=language,
            target_sec=chunk_sec, target_words=chunk_words,
        ) + f"\n\nCHUNK ROLE: {role}{prev_summary}\nThis is chunk {i+1} of {n_chunks}."

        # Use full LLM chain for each chunk
        chunk_data = _try_llm_chain(chunk_prompt, llm_keys, max_groq_retries=1)

        if chunk_data and chunk_data.get("script"):
            chunks.append(chunk_data["script"])
            if i == 0:
                hook_strategy = chunk_data.get("hook_strategy", "")
            all_open_loops.extend(chunk_data.get("open_loops", []))
            all_emotions.extend(chunk_data.get("emotional_arc", []))
            all_visuals.extend(chunk_data.get("key_visuals", []))
            print(f"    [chunk {i+1}/{n_chunks}] {len(chunk_data['script'].split())} words")
            time.sleep(3)  # brief pause between chunks
        else:
            print(f"    [chunk {i+1}/{n_chunks}] FAILED — all LLMs unavailable")

    full_script = " ".join(chunks)
    return {
        "script": full_script,
        "hook_strategy": hook_strategy,
        "open_loops": all_open_loops[:8],
        "estimated_duration_sec": target_sec,
        "key_visuals": all_visuals[:20],
        "emotional_arc": all_emotions[:8],
    }


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
    gemini_key = engine_kwargs.get("gemini_api_key", "")
    cerebras_key = engine_kwargs.get("cerebras_api_key", "")
    openrouter_key = engine_kwargs.get("openrouter_api_key", "")

    result = {
        "title": title, "theme": theme, "language": language, "voice": voice,
        "mode": mode, "job_id": job_id, "files": {},
    }

    # ── STEP 1: Script ───────────────────────────────────────────
    print(f"\n[1/5] Writing viral script for '{title}' ({language}, ~{target_sec}s)...")
    script_data = write_script_from_title(
        title, theme, language, groq_key,
        target_sec=target_sec,
        gemini_key=gemini_key,
        cerebras_key=cerebras_key,
        openrouter_key=openrouter_key,
    )
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
            # Pass the SCRIPT TEXT so karaoke uses correct words (forced alignment)
            add_karaoke_to_video(composed_path, audio_for_subs, final_path,
                                language=language,
                                script_text=script_data.get("script", ""))
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
