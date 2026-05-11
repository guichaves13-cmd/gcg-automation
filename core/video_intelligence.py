"""
Video Intelligence Engine — Deep AI Analysis for StudioPilot Pro
Analyzes avatar video BEFORE any processing to create a perfect production plan.

This engine:
1. Transcribes the ENTIRE video fresh (no cache)
2. Uses Gemini AI to deeply understand theme, subtopics, emotions, visual needs
3. Creates a detailed "Shot List" with exact timestamps
4. Validates each B-roll clip against the shot list
5. Generates proper subtitles from scratch
"""

import os
import json
import hashlib
import time
from datetime import datetime


class VideoIntelligence:
    """Deep video analysis engine using Gemini AI."""
    
    def __init__(self, google_api_key: str = "", youtube_api_key: str = ""):
        self.google_api_key = google_api_key
        self._youtube_api_key = youtube_api_key
        self._client = None
        
    @property
    def client(self):
        if not self._client and self.google_api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.google_api_key)
            except ImportError:
                print("[VideoIntelligence] google-genai not installed")
        return self._client
    
    def analyze_video(self, avatar_path: str, output_dir: str, broll_count: int = 30,
                      on_progress=None) -> dict:
        """
        COMPLETE video analysis pipeline. Returns a production plan.

        Returns:
            {
                "video_id": unique hash of the video file,
                "duration": float,
                "transcription": [{"start": 0, "end": 5, "text": "..."}],
                "full_text": "complete transcription",
                "language": "en",
                "theme": "health_supplements",
                "subtopics": ["omega 3", "vitamin D", ...],
                "emotions": ["informative", "urgent", ...],
                "shot_list": [
                    {"start": 0, "end": 10, "search_terms": ["..."], "visual_description": "..."},
                    ...
                ],
                "subtitle_srt": "path/to/fresh/subs.srt",
            }
        """
        def _prog(pct, msg):
            if on_progress:
                on_progress(pct, 100, msg)

        print(f"\n{'='*60}")
        print(f"  VIDEO INTELLIGENCE ENGINE — Deep Analysis")
        print(f"{'='*60}")

        # Generate unique ID for THIS specific video
        video_id = self._get_video_id(avatar_path)
        print(f"  Video ID: {video_id[:16]}...")
        print(f"  File: {os.path.basename(avatar_path)}")

        # Step 1: Fresh transcription (NEVER use cache)
        _prog(5, "Whisper: transcrevendo narração (pode levar 1-3 min)...")
        print(f"\n  [1/5] Transcribing video (fresh — no cache)...")
        transcription, language = self._transcribe_fresh(avatar_path, output_dir, on_progress=on_progress)
        full_text = " ".join(seg["text"] for seg in transcription)
        print(f"    -> {len(transcription)} segments, language: {language}")
        print(f"    -> First 100 chars: {full_text[:100]}...")

        # Step 2: Get video duration
        from core.video_processor import get_duration
        duration = get_duration(avatar_path)
        _prog(8, f"Duracao: {duration:.0f}s ({duration/60:.1f} min). Analisando tema com IA...")
        print(f"\n  [2/5] Duration: {duration:.1f}s ({duration/60:.1f} min)")

        # Step 3: Deep theme analysis with Gemini
        print(f"\n  [3/5] Deep theme analysis with AI...")
        analysis = self._deep_analyze(full_text, language)
        _prog(10, f"Tema: {analysis['theme']}. Criando shot list com IA Diretora...")
        print(f"    -> Theme: {analysis['theme']}")
        print(f"    -> Subtopics: {', '.join(analysis['subtopics'][:5])}")
        print(f"    -> Emotions: {', '.join(analysis['emotions'][:3])}")

        # Step 4: Generate shot list (works even without transcription)
        _prog(11, f"IA Diretora: planejando {broll_count} shots...")
        print(f"\n  [4/5] Creating detailed shot list...")
        analysis["_broll_count"] = broll_count
        shot_list = self._create_shot_list(transcription, analysis, duration, broll_count=broll_count,
                                           on_progress=on_progress)
        # If still empty (no transcription + no AI), generate duration-based fallback
        if not shot_list:
            theme = analysis["theme"]
            subtopics = analysis.get("subtopics", [])
            shot_dur = duration / max(broll_count, 1)
            shot_list = []
            for i in range(broll_count):
                start = i * shot_dur
                end = min((i + 1) * shot_dur, duration)
                # Rotate through subtopics to at least get varied terms
                sub = subtopics[i % len(subtopics)] if subtopics else theme
                shot_list.append({
                    "start": start, "end": end,
                    "text_preview": "",
                    "search_terms": [sub, f"{theme} {sub}", f"{sub} documentary"],
                    "visual_description": sub,
                })
        _prog(17, f"{len(shot_list)} shots planejados. Descobrindo canais YouTube do nicho...")
        print(f"    -> {len(shot_list)} shots planned")
        for shot in shot_list[:5]:
            print(f"      [{shot['start']:.0f}s-{shot['end']:.0f}s] {shot['search_terms'][0]}")
        if len(shot_list) > 5:
            print(f"      ... and {len(shot_list) - 5} more shots")

        # Step 4b: Auto-discover YouTube channels for this niche
        print(f"\n  [4b] Discovering YouTube channels for niche '{analysis['theme']}'...")
        yt_channels_auto = self._discover_yt_channels(
            theme=analysis["theme"],
            subtopics=analysis.get("subtopics", []),
            target_audience=analysis.get("target_audience", ""),
            youtube_api_key=getattr(self, "_youtube_api_key", ""),
        )
        if yt_channels_auto:
            print(f"    -> {len(yt_channels_auto)} canais descobertos automaticamente")
        else:
            print(f"    -> Sem API YouTube — buscando direto no yt-dlp")

        # Step 5: Generate fresh SRT (only if we have transcription)
        _prog(19, "Gerando legendas SRT...")
        srt_path = ""
        if transcription:
            print(f"\n  [5/5] Generating fresh subtitles...")
            srt_path = os.path.join(output_dir, f"subs_{video_id[:8]}.srt")
            self._generate_srt(transcription, srt_path)
            print(f"    -> Saved: {srt_path}")
        else:
            print(f"\n  [5/5] No transcription available, skipping subtitles.")

        result = {
            "video_id": video_id,
            "duration": duration,
            "transcription": transcription,
            "full_text": full_text,
            "language": language,
            "theme": analysis["theme"],
            "subtopics": analysis["subtopics"],
            "emotions": analysis["emotions"],
            "target_audience": analysis.get("target_audience", "general"),
            "shot_list": shot_list,
            "subtitle_srt": srt_path,
            "youtube_channels_auto": yt_channels_auto,
            "analyzed_at": datetime.now().isoformat(),
        }
        
        # Save analysis report
        report_path = os.path.join(output_dir, f"analysis_{video_id[:8]}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n  Analysis saved: {report_path}")
        print(f"{'='*60}\n")
        
        return result
    
    def _get_video_id(self, path: str) -> str:
        """Generate unique hash from video file content."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            # Read first 10MB + last 10MB for fast hashing
            h.update(f.read(10 * 1024 * 1024))
            f.seek(max(0, os.path.getsize(path) - 10 * 1024 * 1024))
            h.update(f.read())
        h.update(str(os.path.getsize(path)).encode())
        return h.hexdigest()
    
    def _transcribe_fresh(self, video_path: str, output_dir: str, on_progress=None) -> tuple:
        """
        Transcribe video from scratch — NEVER uses cached audio/srt.

        Auto-selects Whisper model based on video duration:
            <= 8 min  -> "base"  (good accuracy)
            8-20 min  -> "tiny"  (fast, acceptable accuracy)
            > 20 min  -> "tiny"  (fast, avoids OOM)

        Hard timeout: 10 minutes. If Whisper hangs (OOM, GPU deadlock, etc.),
        the thread is abandoned and pipeline continues without transcription.
        """
        import tempfile as _tf
        import threading as _th
        import subprocess as _sp
        import shutil as _sh

        # Always write audio to %TEMP% to avoid special-char path issues
        audio_path = os.path.join(_tf.gettempdir(), f"sp_audio_{int(time.time())}.wav")

        # Clean stale temp files
        for f in os.listdir(output_dir):
            if f.startswith("_fresh_audio_") or f.startswith("_temp_audio"):
                try:
                    os.remove(os.path.join(output_dir, f))
                except Exception:
                    pass

        # Extract audio
        ffmpeg_bin = _sh.which("ffmpeg") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ffmpeg", "ffmpeg.exe"
        )
        _r = _sp.run(
            [ffmpeg_bin, "-y", "-i", video_path,
             "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path],
            capture_output=True, text=True, timeout=300,
        )
        if _r.returncode != 0 or not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
            print(f"  [WARN] Audio extraction failed (rc={_r.returncode}): {_r.stderr[-200:]}")
            return [], "en"

        audio_size_kb = os.path.getsize(audio_path) // 1024
        print(f"    -> Audio extracted: {audio_size_kb}KB at {audio_path}")

        # Choose model based on duration (16000 Hz mono WAV: 1 min ≈ 1920KB)
        estimated_minutes = audio_size_kb / 1920
        if estimated_minutes <= 8:
            model_size = "base"
            whisper_timeout = 360   # 6 min
        elif estimated_minutes <= 20:
            model_size = "tiny"
            whisper_timeout = 480   # 8 min
        else:
            model_size = "tiny"
            whisper_timeout = 600   # 10 min

        print(f"    -> Duracao estimada: {estimated_minutes:.1f} min | Whisper: '{model_size}' | Timeout: {whisper_timeout}s")
        if on_progress:
            on_progress(6, 100, f"Whisper '{model_size}': transcrevendo {estimated_minutes:.1f} min de audio...")

        # Run Whisper in a thread with timeout so it can never freeze the pipeline
        result_holder = [[], "en"]
        error_holder = [None]

        def _run_whisper():
            try:
                from core.subtitle_generator import transcribe_audio_with_language
                segs, lang = transcribe_audio_with_language(audio_path, model_size=model_size)
                result_holder[0] = segs
                result_holder[1] = lang
            except ImportError:
                error_holder[0] = "ImportError: Whisper nao instalado. Execute: pip install openai-whisper"
            except Exception as exc:
                error_holder[0] = str(exc)

        t = _th.Thread(target=_run_whisper, daemon=True)
        t.start()

        # Tick progress every 15s while waiting
        elapsed = 0
        interval = 15
        while t.is_alive() and elapsed < whisper_timeout:
            t.join(timeout=interval)
            elapsed += interval
            if t.is_alive() and on_progress:
                pct_done = min(95, int(elapsed / whisper_timeout * 100))
                on_progress(6, 100,
                            f"Whisper transcrevendo... {elapsed}s / {whisper_timeout}s ({pct_done}%)")

        if t.is_alive():
            # Whisper hung — abandon and continue without transcription
            print(f"  [WARN] Whisper timeout ({whisper_timeout}s) — continuando sem transcricao")
            print(f"         O video sera processado sem legendas automaticas.")
            try:
                os.remove(audio_path)
            except Exception:
                pass
            return [], "en"

        if error_holder[0]:
            print(f"  [WARN] Transcricao falhou: {error_holder[0]}")
            try:
                os.remove(audio_path)
            except Exception:
                pass
            return [], "en"

        segments, language = result_holder[0], result_holder[1]
        print(f"    -> {len(segments)} segmentos, idioma: {language}")

        try:
            os.remove(audio_path)
        except Exception:
            pass

        return segments, language
    
    def _deep_analyze(self, full_text: str, language: str) -> dict:
        """Use Gemini AI for deep video understanding."""
        if not self.client:
            return self._fallback_analyze(full_text)
        
        prompt = f"""You are an expert video producer and content analyst.

TRANSCRIPTION OF THE VIDEO:
\"\"\"{full_text[:3000]}\"\"\"

LANGUAGE: {language}

Analyze this video deeply and return a JSON object with:
{{
    "theme": "main theme in 2-3 words (e.g., 'health supplements', 'ancient history', 'tech innovation')",
    "subtopics": ["list of 5-10 specific subtopics mentioned"],
    "emotions": ["list of 3-5 emotional tones (e.g., 'informative', 'dramatic', 'urgent')"],
    "target_audience": "who would watch this (e.g., 'health-conscious adults', 'history enthusiasts')",
    "visual_style": "recommended visual style (e.g., 'cinematic documentary', 'modern medical', 'historical epic')",
    "key_moments": [
        {{"text": "key phrase", "visual": "what should be shown on screen"}}
    ]
}}

Return ONLY valid JSON, no markdown, no explanation."""

        try:
            text = self._gemini_call(prompt)
            if text:
                # Clean markdown if present
                if text.startswith("```"):
                    text = text.split("\n", 1)[1] if "\n" in text else text
                    text = text.rsplit("```", 1)[0] if "```" in text else text
                return json.loads(text.strip())
        except Exception as e:
            print(f"    Gemini analysis error: {e}")
        
        return self._fallback_analyze(full_text)
    
    def _fallback_analyze(self, full_text: str) -> dict:
        """Fallback analysis without AI."""
        text = full_text.lower()
        
        # Simple theme detection
        themes = {
            "health supplements": ["supplement", "vitamin", "omega", "capsule", "nutrient", "health", "body"],
            "eye health": ["eye", "vision", "cataract", "retina", "ophthalmologist", "glaucoma", "blur"],
            "sleep health": ["sleep", "insomnia", "rest", "bed", "night", "melatonin", "dream"],
            "gut health": ["gut", "bacteria", "microbiome", "intestine", "probiotic", "digest"],
            "heart health": ["heart", "cardiac", "blood", "pressure", "cholesterol", "artery"],
            "war history": ["war", "battle", "soldier", "army", "conquest", "military"],
            "ancient civilization": ["empire", "ancient", "civilization", "pharaoh", "roman", "egypt"],
            "nature wildlife": ["ocean", "forest", "animal", "ecosystem", "wildlife", "marine"],
            "space science": ["planet", "galaxy", "universe", "astronaut", "star", "nasa"],
            "technology": ["computer", "software", "robot", "digital", "internet", "AI"],
            "finance economy": ["market", "economy", "stock", "investment", "bank", "money"],
            "food nutrition": ["food", "diet", "fruit", "vegetable", "cooking", "recipe"],
            "crime investigation": ["crime", "police", "detective", "murder", "prison", "court"],
            "psychology": ["brain", "mind", "anxiety", "depression", "therapy", "mental"],
            "construction": ["building", "construction", "architect", "concrete", "steel", "house"],
        }
        
        best_theme = "general documentary"
        best_score = 0
        for theme, keywords in themes.items():
            score = sum(1 for k in keywords if k in text)
            if score > best_score:
                best_score = score
                best_theme = theme
        
        # Extract subtopics (most frequent meaningful words)
        words = text.split()
        stop = {"the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "in", 
                "for", "on", "with", "at", "by", "from", "and", "but", "or", "not", "this",
                "that", "it", "you", "he", "she", "we", "they", "i", "my", "your", "his",
                "her", "its", "our", "their", "has", "have", "had", "do", "does", "did"}
        meaningful = [w for w in words if len(w) > 4 and w not in stop]
        freq = {}
        for w in meaningful:
            freq[w] = freq.get(w, 0) + 1
        subtopics = sorted(freq, key=freq.get, reverse=True)[:10]
        
        return {
            "theme": best_theme,
            "subtopics": subtopics,
            "emotions": ["informative"],
            "target_audience": "general",
            "visual_style": "documentary",
            "key_moments": [],
        }
    
    def _discover_yt_channels(self, theme: str, subtopics: list,
                               target_audience: str = "", youtube_api_key: str = "") -> list:
        """
        Usa Gemini para descobrir os melhores nomes de canais YouTube para este nicho,
        depois busca os IDs reais desses canais via YouTube Data API.
        Retorna lista de channel IDs prontos para usar como fonte de B-roll.
        """
        if not self.client:
            return []

        # 1. Gemini sugere os melhores tipos/nomes de canais para o nicho
        subtopics_str = ", ".join(subtopics[:6]) if subtopics else theme
        prompt = f"""You are a YouTube expert helping find B-roll footage channels.

VIDEO NICHE: {theme}
SUBTOPICS: {subtopics_str}
AUDIENCE: {target_audience}

List the 6 best YouTube CHANNEL NAMES that would have authentic, high-quality video footage
for this exact niche. These channels should publish documentary-style, educational, or
demonstrative videos — NOT reaction videos, vlogs, or opinion content.

Think: what channels would a filmmaker use to find B-roll footage for a video about "{theme}"?

Return ONLY a JSON array of channel name strings (no IDs, just names), for example:
["Mayo Clinic", "Harvard Medical School", "National Geographic", "BBC Earth"]

Return ONLY valid JSON array, no markdown, no explanation."""

        channel_names = []
        try:
            text = self._gemini_call(prompt)
            text = text.strip("`").strip()
            if text.startswith("json"):
                text = text[4:].strip()
            channel_names = json.loads(text)
            if not isinstance(channel_names, list):
                channel_names = []
            channel_names = [str(n).strip() for n in channel_names if n][:6]
        except Exception as e:
            print(f"    [yt_discover] Gemini erro: {e}")
            return []

        if not channel_names:
            return []

        print(f"    -> Gemini sugeriu canais: {channel_names}")

        # Sempre armazena os nomes para uso como boost nas queries yt-dlp
        self._yt_channel_names = channel_names

        # 2. Sem YouTube API key → retorna vazio (nomes ficam em self._yt_channel_names)
        if not youtube_api_key:
            return []

        # 3. Com YouTube API key → busca os IDs reais via YouTube Data API
        import requests
        channel_ids = []
        for name in channel_names:
            try:
                r = requests.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "part": "snippet",
                        "q": name,
                        "type": "channel",
                        "maxResults": 1,
                        "key": youtube_api_key,
                    },
                    timeout=8,
                )
                items = r.json().get("items", [])
                if items:
                    cid = items[0].get("id", {}).get("channelId", "")
                    if cid:
                        channel_ids.append(cid)
                        print(f"      ✓ '{name}' → {cid}")
            except Exception as e:
                print(f"      [yt_discover] Erro buscando '{name}': {e}")

        return channel_ids

    def _gemini_call(self, prompt: str, model: str = "gemini-2.5-flash", max_retries: int = 3) -> str:
        """Gemini call with automatic 429 retry using the API-provided wait time."""
        import re as _re
        for attempt in range(max_retries):
            try:
                resp = self.client.models.generate_content(model=model, contents=prompt)
                return (resp.text or "").strip()
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    # Parse "Please retry in X.Xs" from the error message
                    m = _re.search(r"retry in (\d+(?:\.\d+)?)s", err_str)
                    wait = float(m.group(1)) + 5 if m else 65
                    print(f"  [Gemini] Rate limit — aguardando {wait:.0f}s antes de tentar novamente (tentativa {attempt+1}/{max_retries})...")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError(f"Gemini falhou após {max_retries} tentativas")

    def _create_shot_list(self, transcription: list, analysis: dict, duration: float,
                          broll_count: int = 30, on_progress=None) -> list:
        """
        Sistema de 3 IAs em cadeia para B-roll 100% coerente:
        1. IA Diretora (Gemini 2.5 Flash) — le narracao completa, cria shot list como editor profissional
        2. IA Revisora (Gemini 2.5 Flash) — revisa termos, corrige literalismo/metaforas/abstracoes
        3. Fallback: individual por shot se necessario
        """
        if not self.client:
            return self._fallback_shot_list(transcription, analysis)

        theme = analysis["theme"]
        subtopics = ", ".join(analysis.get("subtopics", [])[:8])
        target_audience = analysis.get("target_audience", "general audience")
        shot_interval = duration / max(broll_count, 1)

        # Transcrição completa com timestamps exatos
        lines = []
        for seg in transcription:
            lines.append(f"[{seg['start']:.1f}s-{seg['end']:.1f}s] {seg['text'].strip()}")
        full_transcript = "\n".join(lines)
        if len(full_transcript) > 10000:
            full_transcript = full_transcript[:10000] + "\n[... truncated]"

        print(f"  [IA Diretora] Iniciando análise: {broll_count} shots, {duration:.0f}s...")

        # ══════════════════════════════════════════════════════════
        # IA 1 — DIRETORA: pensa como editor profissional de vídeo
        # ══════════════════════════════════════════════════════════
        ex_end1 = round(shot_interval)
        ex_end2 = round(shot_interval * 2)

        prompt_diretor = f"""You are a SENIOR VIDEO EDITOR with 20 years of experience in documentary and health/educational content. You are building a B-roll shot list for a video.

VIDEO TOPIC: {theme}
TARGET AUDIENCE: {target_audience}
SUBTOPICS: {subtopics}
TOTAL DURATION: {duration:.0f}s | SHOTS NEEDED: {broll_count} | INTERVAL: ~{shot_interval:.0f}s per shot

FULL NARRATION WITH TIMESTAMPS:
{full_transcript}

━━━ YOUR JOB ━━━
For each time window, read the EXACT words being spoken and decide what a professional editor would CUT TO.
Think: "What does a real stock footage LIBRARIAN search for when this line is spoken?"

━━━ CRITICAL: METAPHOR TRANSLATION RULE ━━━
When narration uses NON-LITERAL language to describe biology/health, you MUST translate to what ACTUALLY EXISTS:
- "electric system in the brain" → NEVER "electrical equipment" → ALWAYS "brain neuron scan", "MRI brain scan doctor"
- "brain on fire" → NEVER "fire flames" → ALWAYS "brain inflammation MRI", "neurologist examining brain scan"
- "gut bacteria army" → NEVER "army soldiers" → ALWAYS "probiotic capsule", "digestive health supplement"
- "heart is a pump" → NEVER "water pump" → ALWAYS "heart ultrasound echocardiogram", "cardiologist patient"
- "nerve highways" → NEVER "highway road" → ALWAYS "spinal cord MRI", "neurology clinic doctor"
- "brain fog" → NEVER "fog mist" → ALWAYS "elderly confused face", "senior memory difficulty"
- "domino effect in body" → NEVER "dominos falling" → ALWAYS "doctor explaining diagnosis", "medical chart"
- "silent killer" → NEVER "person hiding" → ALWAYS "blood pressure cuff", "hypertension medical check"
- "aging clock" → NEVER "clock watch" → ALWAYS "elderly person face closeup", "senior aging skin"
- "body signals" → NEVER "traffic signal" → ALWAYS "doctor reading test results", "medical diagnosis"

━━━ WHAT EXISTS ON PEXELS/PIXABAY (USE THESE) ━━━
People: elderly person, senior couple, doctor patient, scientist lab, nurse hospital
Medical: MRI scanner, stethoscope, blood pressure cuff, microscope, pill bottle, x-ray
Actions: doctor examining patient, blood test, pill taking, elderly walking, brain scan screen
Anatomy shown: brain MRI image on screen, x-ray lightbox, anatomy chart poster
Supplements: vitamin bottle, fish oil capsule, omega-3 pill, probiotic yogurt
Lifestyle: elderly confused, senior exercise, older adult cooking, couple outdoors

━━━ WHAT DOES NOT EXIST (NEVER USE) ━━━
Abstract: "silent damage", "misfiring", "chain reaction", "brain fog"
Metaphors taken literally: "electric wires", "dominos", "fire in brain", "army bacteria"
Specific institutions: "Harvard study", "Mayo Clinic building", "Lancet journal"
Animations: "myelin animation", "molecular diagram", "signal pathway"
Feelings: "urgent concern", "hidden epidemic", "mental slowness"

━━━ OUTPUT FORMAT — JSON ARRAY ONLY ━━━
[
  {{"start": 0, "end": {ex_end1}, "narration": "exact words spoken here", "terms": ["most specific filmable scene", "second option same topic", "third option broader", "fourth broader fallback", "fifth generic fallback"]}},
  {{"start": {ex_end1}, "end": {ex_end2}, "narration": "exact words spoken here", "terms": ["...", "...", "...", "...", "..."]}},
  ... exactly {broll_count} objects covering 0s to {duration:.0f}s
]

Each term = what a CAMERA CAN PHOTOGRAPH. Not concepts. Not metaphors. Real scenes."""

        if on_progress:
            on_progress(12, 100, "IA Diretora: analisando narracao completa...")

        shot_list = []
        try:
            raw1 = self._gemini_call(prompt_diretor)
            print(f"  [IA Diretora] Resposta: {len(raw1)} chars")

            import json as _json, re as _re
            raw1_clean = _re.sub(r'```(?:json)?\s*', '', raw1).strip().strip('`')
            m1 = _re.search(r'\[.*\]', raw1_clean, _re.DOTALL)
            if m1:
                data1 = _json.loads(m1.group())
                for item in data1:
                    s = float(item.get("start", 0))
                    e = float(item.get("end", s + shot_interval))
                    raw_terms = item.get("terms", [])
                    seen_shot = set()
                    terms = []
                    for t in raw_terms:
                        t = str(t).strip().strip('"\'')
                        if 3 < len(t) < 80 and t.lower() not in seen_shot:
                            terms.append(t)
                            seen_shot.add(t.lower())
                    if not terms:
                        continue
                    narration_at_shot = item.get("narration", "") or " ".join(
                        seg["text"] for seg in transcription
                        if seg.get("end", 0) > s and seg.get("start", 0) < e
                    )[:150]
                    shot_list.append({
                        "start": s, "end": e,
                        "text_preview": narration_at_shot[:150],
                        "search_terms": terms[:5],
                        "visual_description": terms[0],
                    })
                print(f"  [IA Diretora] {len(shot_list)} shots gerados")
        except Exception as e:
            print(f"  [IA Diretora] Erro: {e}")

        # ══════════════════════════════════════════════════════════
        # IA 2 — REVISORA: detecta e corrige termos errados
        # ══════════════════════════════════════════════════════════
        if shot_list:
            if on_progress:
                on_progress(14, 100, f"IA Revisora: validando {len(shot_list)} shots...")
            shot_list = self._ia_revisora(shot_list, theme)

        # Valida cobertura
        if shot_list:
            last_end = max(s["end"] for s in shot_list)
            coverage_ok = last_end >= duration * 0.75
            count_ok = len(shot_list) >= broll_count * 0.80
            if count_ok and coverage_ok:
                print(f"  [shot_list] OK: {len(shot_list)} shots (cobertura {last_end:.0f}s/{duration:.0f}s)")
                for s in shot_list[:8]:
                    print(f"    [{s['start']:.0f}s] \"{s['text_preview'][:50]}\" → {s['search_terms'][0]}")
                return shot_list
            print(f"  [shot_list] Insuficiente ({len(shot_list)}/{broll_count}, {last_end:.0f}s/{duration:.0f}s) → fallback individual")

        # Fallback: shot por shot com IA individual
        print("  [shot_list] Fallback: IA individual por shot")
        if on_progress:
            on_progress(14, 100, "Fallback: gerando keywords por shot individualmente...")
        chunks = []
        for i in range(broll_count):
            start = i * shot_interval
            end = min((i + 1) * shot_interval, duration)
            text = " ".join(
                seg["text"] for seg in transcription
                if seg.get("end", 0) > start and seg.get("start", 0) < end
            ).strip()
            chunks.append({"start": start, "end": end, "text": text})
        return self._individual_generate_search_terms(chunks, theme, analysis, on_progress=on_progress)

    def _ia_revisora(self, shot_list: list, theme: str) -> list:
        """
        IA 2 — Revisora: verifica cada termo gerado pela IA Diretora.
        Detecta e corrige: termos literais de metáforas, abstrações não filmáveis,
        termos genéricos sem relação com a narração.
        Processa em lotes de 15 shots por chamada Gemini.
        """
        if not self.client or not shot_list:
            return shot_list

        print(f"  [IA Revisora] Revisando {len(shot_list)} shots...")

        # Monta lista compacta para revisão
        batch_size = 15
        revised_shots = []

        for batch_start in range(0, len(shot_list), batch_size):
            batch = shot_list[batch_start: batch_start + batch_size]
            shots_text = ""
            for i, s in enumerate(batch):
                shots_text += (
                    f"SHOT {batch_start+i}: [{s['start']:.0f}s] "
                    f"narration=\"{s['text_preview'][:80]}\" "
                    f"terms={s['search_terms'][:3]}\n"
                )

            prompt_rev = f"""You are a QUALITY CONTROL AI for a video production system. Review these B-roll search terms and FIX any that are WRONG.

VIDEO THEME: {theme}

SHOTS TO REVIEW:
{shots_text}

WHAT IS WRONG (must fix):
1. LITERAL METAPHOR: narration="electric system in brain" but term="electrical equipment" → FIX to "brain MRI scan", "neuron brain activity"
2. ABSTRACT/UNFINDABLE: "silent killer", "domino effect", "brain fog", "misfiring neurons" → FIX to real filmable scene
3. GENERIC/UNRELATED: narration talks about supplements but term="nature landscape" → FIX to match narration
4. INSTITUTION NAME: "Harvard research building", "Mayo Clinic exterior" → FIX to "medical research lab", "scientist microscope"

WHAT IS CORRECT (keep as is):
- "elderly person confused", "doctor MRI scan", "vitamin pill bottle", "blood test tube" — these are filmable

For EACH shot, output the corrected terms. If terms are already correct, keep them unchanged.

Return ONLY a JSON array — one entry per shot in the same order:
[
  {{"shot": 0, "terms": ["corrected term 1", "corrected term 2", "corrected term 3", "broader fallback", "broadest fallback"]}},
  {{"shot": 1, "terms": ["...", "...", "...", "...", "..."]}},
  ...
]"""

            try:
                # max_retries=1: nao espera 65s em rate limit — pula o lote se necessario
                raw = self._gemini_call(prompt_rev, max_retries=1)
                import json as _json, re as _re
                raw_clean = _re.sub(r'```(?:json)?\s*', '', raw).strip().strip('`')
                m = _re.search(r'\[.*\]', raw_clean, _re.DOTALL)
                if m:
                    revisions = _json.loads(m.group())
                    for rev in revisions:
                        idx = rev.get("shot", -1)
                        if 0 <= idx < len(shot_list):
                            new_terms = [str(t).strip().strip('"\'') for t in rev.get("terms", []) if str(t).strip()]
                            if new_terms:
                                old = shot_list[idx]["search_terms"][0]
                                shot_list[idx]["search_terms"] = new_terms[:5]
                                shot_list[idx]["visual_description"] = new_terms[0]
                                if new_terms[0] != old:
                                    print(f"    [IA Revisora] Shot {idx}: '{old}' -> '{new_terms[0]}'")
                    revised_shots.extend(batch)
            except Exception as e:
                print(f"  [IA Revisora] Lote {batch_start} pulado ({e}) — usando termos originais")
                revised_shots.extend(batch)

        print(f"  [IA Revisora] Revisão concluída")
        return shot_list

    def _individual_generate_search_terms(self, chunks: list, theme: str, analysis: dict,
                                           on_progress=None) -> list:
        """Fallback individual: uma chamada Gemini por shot com foco em traducao semantica.
        Respeita rate limit de 20 req/min com 4s de delay entre chamadas.
        """
        import re as _re
        shot_list = []
        CALL_DELAY = 4.0  # 20 req/min = 1 req per 3s; 4s para margem de seguranca
        total = len(chunks)

        for i, chunk in enumerate(chunks):
            if on_progress:
                pct = 14 + int((i / max(total, 1)) * 5)
                on_progress(pct, 100, f"Shot {i+1}/{total}: '{chunk.get('text','')[:40]}'...")

            text = chunk["text"].strip()
            text_display = text[:300] if text else f"(sem narracao em {chunk['start']:.0f}s)"

            prompt = f"""You are a professional video editor choosing B-roll footage.

The narrator says at {chunk['start']:.0f}s-{chunk['end']:.0f}s:
"{text_display}"

VIDEO THEME: {theme}

TASK: Write 5 Pexels/Pixabay search terms for footage to show during this narration.

CRITICAL RULES:
1. If narration uses METAPHORS (electric brain, fire in gut, army of bacteria), translate to MEDICAL REALITY:
   - "electric system brain" → "brain MRI scan doctor", "neurology clinic patient"
   - "gut bacteria army" → "probiotic capsule gut", "digestive supplement pill"
   - "heart pump" → "heart ultrasound", "cardiologist patient"
2. Terms must describe something a CAMERA CAN FILM (people, objects, places, actions)
3. Terms must DIRECTLY relate to what is being SAID RIGHT NOW — not the general topic

Return ONLY 5 lines, one term per line, no numbers, no explanation:"""

            try:
                raw = self._gemini_call(prompt)
                terms = []
                for ln in raw.split("\n"):
                    t = ln.strip().lstrip("0123456789.-) *#").strip().strip('"\'`')
                    t = _re.sub(r'^[\*\-]+\s*', '', t).strip()
                    if 3 < len(t) < 80 and t.lower() not in [x.lower() for x in terms]:
                        terms.append(t)
                terms = terms[:5]
                if not terms:
                    words = text.split()[:4] if text else [theme]
                    terms = [" ".join(words), f"{theme} doctor patient", f"{theme} medical"]
            except Exception as e:
                print(f"    [individual] Shot {i}: Gemini indisponível — usando keywords da transcrição")
                words = text.split()[:3] if text else [theme]
                terms = [" ".join(words) or theme, f"{theme} doctor", f"{theme} medical"]

            shot_list.append({
                "start": chunk["start"],
                "end": chunk["end"],
                "text_preview": text[:120],
                "search_terms": terms,
                "visual_description": terms[0],
            })
            print(f"    [{chunk['start']:.0f}s-{chunk['end']:.0f}s] → {terms[0]}")
            time.sleep(CALL_DELAY)

        return shot_list

    def _fallback_shot_list(self, transcription: list, analysis: dict) -> list:
        """Fallback shot list sem IA — usa chunks de 6s para consistência."""
        from core.smart_broll import _group_segments, _extract_smart_keywords

        total_dur = transcription[-1]["end"] if transcription else 60
        chunks = _group_segments(transcription, 6.0, total_dur)
        keywords = _extract_smart_keywords(chunks, analysis["theme"].replace(" ", "_"))
        
        shot_list = []
        for kw in keywords:
            shot_list.append({
                "start": kw["start"],
                "end": kw["end"],
                "text_preview": "",
                "search_terms": [kw["keyword"]],
                "visual_description": f"B-roll for: {kw['keyword']}",
            })
        
        return shot_list
    
    def _generate_srt(self, transcription: list, srt_path: str):
        """Generate fresh SRT file from transcription segments."""
        with open(srt_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(transcription, 1):
                start = self._format_srt_time(seg["start"])
                end = self._format_srt_time(seg["end"])
                text = seg["text"].strip()
                f.write(f"{i}\n{start} --> {end}\n{text}\n\n")
    
    def _format_srt_time(self, seconds: float) -> str:
        """Format seconds to SRT timestamp."""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    
    def generate_veo3_prompts(self, shot_list: list, theme: str, language: str = "en") -> list:
        """
        Generate VEO3 video generation prompts from the shot list.
        Each prompt describes a specific visual scene to generate with AI video.

        Returns list of {"start", "end", "prompt"} dicts.
        """
        if not self.client or not shot_list:
            return self._fallback_veo3_prompts(shot_list, theme)

        segments_text = ""
        for i, shot in enumerate(shot_list):
            segments_text += f"SEGMENT {i+1} [{shot['start']:.0f}s-{shot['end']:.0f}s]: {shot.get('text_preview','')[:150]}\n"

        prompt = f"""You are a professional AI video director creating prompts for Google VEO3 video generation.

VIDEO THEME: {theme}
NARRATION LANGUAGE: {language}

TASK: For each narration segment, write a VEO3 video generation prompt that creates a visually stunning B-roll clip.

VEO3 PROMPT RULES:
- Start with camera movement: "Close-up shot of...", "Aerial view of...", "Cinematic tracking shot of..."
- Describe specific objects/people/places relevant to the narration
- Include lighting: "golden hour lighting", "soft medical lighting", "dramatic shadows"
- Include mood: "cinematic", "documentary style", "4K ultra-realistic"
- Keep it 15-30 words per prompt
- Make it visually specific, NOT conceptual
- NEVER mention text, subtitles, or voiceover in the prompt

ANTI-GENERIC RULES:
- Health video: "Close-up of pharmacist's hands counting omega-3 capsules on white surface, soft studio lighting" NOT "healthy person"
- History video: "Cinematic aerial view of Roman Colosseum at sunset, dramatic clouds" NOT "ancient ruins"
- Science video: "Macro shot of DNA helix model rotating slowly, blue laboratory lighting" NOT "science"

SEGMENTS:
{segments_text}

OUTPUT FORMAT:
SEGMENT 1: [VEO3 prompt here]
SEGMENT 2: [VEO3 prompt here]
[...for ALL segments]

Return ONLY the formatted output."""

        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            if not response or not response.text:
                return self._fallback_veo3_prompts(shot_list, theme)

            result = []
            for line in response.text.strip().split("\n"):
                line = line.strip()
                if line.upper().startswith("SEGMENT"):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        try:
                            num = int(''.join(c for c in parts[0] if c.isdigit())) - 1
                            if 0 <= num < len(shot_list):
                                shot = shot_list[num]
                                veo_prompt = parts[1].strip().strip('"\'[]')
                                if len(veo_prompt) > 10:
                                    result.append({
                                        "start": shot["start"],
                                        "end": shot["end"],
                                        "prompt": veo_prompt,
                                    })
                        except Exception:
                            continue

            return result if result else self._fallback_veo3_prompts(shot_list, theme)

        except Exception as e:
            print(f"  [VEO3 prompts] Error: {e}")
            return self._fallback_veo3_prompts(shot_list, theme)

    def _fallback_veo3_prompts(self, shot_list: list, theme: str) -> list:
        """Generate basic VEO3 prompts from shot search terms without AI."""
        result = []
        camera_moves = [
            "Close-up shot of", "Cinematic aerial view of", "Tracking shot of",
            "Wide establishing shot of", "Macro close-up of", "Slow motion shot of",
        ]
        lighting = ["golden hour lighting", "soft studio lighting", "dramatic side lighting",
                    "natural daylight", "blue medical lighting", "cinematic warm tones"]
        import random
        rng = random.Random(42)
        for i, shot in enumerate(shot_list):
            terms = shot.get("search_terms", [theme])
            term = terms[0] if terms else theme
            move = camera_moves[i % len(camera_moves)]
            light = rng.choice(lighting)
            result.append({
                "start": shot["start"],
                "end": shot["end"],
                "prompt": f"{move} {term}, {light}, 4K ultra-realistic documentary style",
            })
        return result

    def validate_clip(self, clip_path: str, expected_keywords: list,
                      text_preview: str = "", theme: str = "") -> float:
        """
        IA Validadora — usa Gemini Vision para verificar se o clip baixado
        corresponde à narração que está sendo dita. Retorna score 0.0-1.0.
        """
        if not os.path.exists(clip_path) or os.path.getsize(clip_path) < 1000:
            return 0.0

        if not self.client:
            return 0.8  # Sem IA, confia nos resultados de busca

        ext = os.path.splitext(clip_path)[1].lower()
        frame_path = clip_path + "_val.jpg"
        is_temp_frame = False

        try:
            if ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
                try:
                    from core.video_processor import get_duration, _find_ffmpeg
                    import subprocess as _sp
                    dur = get_duration(clip_path)
                    t = max(0.5, dur * 0.4)
                    cmd = [_find_ffmpeg(), "-y", "-ss", str(round(t, 2)),
                           "-i", clip_path, "-vframes", "1", "-q:v", "4", frame_path]
                    _sp.run(cmd, capture_output=True, timeout=20)
                    is_temp_frame = os.path.exists(frame_path) and os.path.getsize(frame_path) > 500
                except Exception:
                    return 0.7
            elif ext in (".jpg", ".jpeg", ".png", ".webp"):
                frame_path = clip_path
            else:
                return 0.7

            if not os.path.exists(frame_path) or os.path.getsize(frame_path) < 500:
                return 0.6

            with open(frame_path, "rb") as f:
                image_bytes = f.read()

            keyword = expected_keywords[0] if expected_keywords else "?"
            context_line = f'Narração sendo dita: "{text_preview}"' if text_preview else ""

            prompt = (
                f"Você é um editor de vídeo profissional verificando se um clipe de B-roll corresponde à narração.\n\n"
                f"Keyword buscado: \"{keyword}\"\n"
                f"{context_line}\n\n"
                f"Analise este frame do clipe baixado.\n"
                f"Ele representa visualmente o que está sendo descrito?\n\n"
                f"Responda APENAS com uma linha:\n"
                f"MATCH - se o frame mostra claramente o conteúdo relevante\n"
                f"PARTIAL - se é relacionado mas não ideal\n"
                f"MISMATCH - se mostra algo não relacionado ou incorreto\n\n"
                f"Depois um traço e razão muito curta (max 8 palavras)."
            )

            try:
                from google.genai import types as _gtypes
                # Try with image frame first, fall back to text-only
                try:
                    import re as _re2
                    text_response = ""
                    for attempt in range(3):
                        try:
                            resp_v = self.client.models.generate_content(
                                model="gemini-2.5-flash",
                                contents=[
                                    _gtypes.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                                    prompt,
                                ],
                            )
                            text_response = (resp_v.text or "").strip()
                            break
                        except Exception as _ev:
                            if "429" in str(_ev) or "RESOURCE_EXHAUSTED" in str(_ev):
                                m429 = _re2.search(r"retry in (\d+(?:\.\d+)?)s", str(_ev))
                                wait429 = float(m429.group(1)) + 3 if m429 else 65
                                print(f"    [IA Validadora] Rate limit — aguardando {wait429:.0f}s...")
                                time.sleep(wait429)
                            else:
                                raise
                    raw_verdict = text_response
                except Exception:
                    raw_verdict = self._gemini_call(prompt)
            except Exception:
                raw_verdict = self._gemini_call(prompt)

            if raw_verdict:
                verdict = raw_verdict.strip().upper()
                if verdict.startswith("MATCH"):
                    score = 1.0
                elif verdict.startswith("PARTIAL"):
                    score = 0.6
                elif verdict.startswith("MISMATCH"):
                    score = 0.1
                else:
                    score = 0.7
                short = raw_verdict.strip().split("\n")[0][:60]
                print(f"    [IA Validadora] '{keyword}' → {short} (score={score:.1f})")
                return score

        except Exception as e:
            print(f"    [IA Validadora] Erro: {e}")
        finally:
            if is_temp_frame and os.path.exists(frame_path):
                try:
                    os.remove(frame_path)
                except Exception:
                    pass

        return 0.7

    def gerente_geral_audit(self, mapped_clips: list, shot_list: list) -> list:
        """
        IA Gerente Geral — revisa TODAS as atribuições de B-roll de uma vez
        e retorna lista de índices problemáticos para re-download.
        """
        if not self.client or not mapped_clips:
            return []

        print("\n  [IA Gerente Geral] Auditando todas as atribuições de B-roll...")

        lines = []
        for i, clip in enumerate(mapped_clips):
            t = clip.get("timeline_start", 0)
            kw = clip.get("keyword", "?")
            txt = clip.get("text_preview", "")[:80]
            lines.append(f"[{i:02d}] t={t:.0f}s | Narração: \"{txt}\" | Keyword buscado: \"{kw}\"")

        assignments_text = "\n".join(lines)

        prompt = f"""Você é o Gerente Geral de uma produtora de vídeo profissional de alto nível.
Sua função: auditar atribuições de B-roll e identificar INCOMPATIBILIDADES ÓBVIAS.

REGRAS DE INCOMPATIBILIDADE (sinalize apenas erros claros):
- "fish oil" / "óleo de peixe" → deve mostrar cápsulas/suplemento, NÃO peixe nadando
- "eye pressure" / "pressão ocular" → deve mostrar oftalmologista, NÃO olho genérico
- "gut bacteria" / "bactéria intestinal" → deve mostrar microscópio/intestino, NÃO bactéria em placa
- "blood pressure" → monitor de pressão, NÃO sangue/violência
- "omega 3" → cápsulas de suplemento, NÃO peixe
- Narração médica → sempre contexto médico/laboratorial
- Narração histórica → visuais da era específica (Roma = legionários/coliseu)
- Narração de natureza → natureza OK
- Narração de tecnologia → tecnologia OK
- SINALIZE apenas onde o keyword é SEMANTICAMENTE ERRADO para a narração

ATRIBUIÇÕES:
{assignments_text}

Retorne APENAS os números de índice das atribuições problemáticas separados por vírgula.
Se tudo estiver correto, responda: NENHUM
Exemplos: 3, 7, 15  ou  NENHUM"""

        try:
            text = self._gemini_call(prompt)
            if text:
                if "NENHUM" in text.upper() or not any(c.isdigit() for c in text):
                    print("  [IA Gerente Geral] ✅ Todas as atribuições aprovadas!")
                    return []

                bad_indices = []
                for token in text.replace(",", " ").split():
                    digits = "".join(c for c in token if c.isdigit())
                    if digits:
                        try:
                            idx = int(digits)
                            if 0 <= idx < len(mapped_clips):
                                bad_indices.append(idx)
                        except ValueError:
                            continue

                bad_indices = sorted(set(bad_indices))
                print(f"  [IA Gerente Geral] ⚠️  {len(bad_indices)} atribuições reprovadas: {bad_indices}")
                return bad_indices
        except Exception as e:
            print(f"  [IA Gerente Geral] Erro: {e}")

        return []
