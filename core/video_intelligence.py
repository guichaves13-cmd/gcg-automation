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
import threading
from datetime import datetime


class VideoIntelligence:
    """Deep video analysis engine using GLM-5.1 (NVIDIA) + Gemini fallback."""
    
    def __init__(self, google_api_key: str = ""):
        self.google_api_key = google_api_key
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
    
    def _glm_ask(self, prompt: str, temperature=0.3,
                  enable_thinking: bool = False, timeout: float = 90.0,
                  max_retries: int = 0) -> str:
        """Call GLM-5.1. Defaults:
          - enable_thinking=False (fast mode; reasoning adds 60s+ overhead)
          - timeout=90s (long enough for JSON shot lists, short enough to fall back)
          - max_retries=0 (honor timeout strictly; we have Gemini fallback anyway)
        Returns content string or empty string on error (caller falls back to Gemini).
        """
        from core.glm_agent import ask
        result, err = ask(prompt, temperature=temperature, stream=False,
                          enable_thinking=enable_thinking, timeout=timeout,
                          max_retries=max_retries)
        if err:
            print(f"    [GLM] Error ({err[:100]}) - falling back to next AI in chain")
            return ""
        return result.get("content", "") if result else ""

    # GLM call counter — limit calls per session to avoid burning NVIDIA quota
    _glm_score_calls = 0
    _glm_score_max = 50  # safety cap; reset per process

    def _glm_score_clip(self, metadata_text: str, expected_keywords: list,
                        theme: str) -> float:
        """Textual scoring via GLM reasoning model.
        Used as fallback when Gemini Vision quota is exhausted.
        Returns 0.0-1.0 score, or -1.0 if GLM unavailable/unparseable.
        Capped at 50 calls per session to protect NVIDIA quota."""
        if VideoIntelligence._glm_score_calls >= VideoIntelligence._glm_score_max:
            return -1.0
        if not metadata_text or not expected_keywords:
            return -1.0
        kw = ", ".join(str(k) for k in expected_keywords[:3])
        prompt = (
            f"Rate from 0 to 10 how well a stock clip with this metadata matches the topic.\n"
            f"Topic/theme: {theme}\n"
            f"Search keywords: {kw}\n"
            f"Clip metadata (filename/tags): {metadata_text[:200]}\n\n"
            f"10=perfect literal match; 7=related; 4=weak; 0=wrong/unrelated.\n"
            f"Reply ONLY with a single integer 0-10. No explanation."
        )
        try:
            VideoIntelligence._glm_score_calls += 1
            text = self._glm_ask(prompt, temperature=0.0)
            if not text:
                return -1.0
            import re as _re
            m = _re.search(r"\b(\d{1,2})\b", text)
            if m:
                return min(10, max(0, int(m.group(1)))) / 10.0
        except Exception as e:
            print(f"    [GLM scorer] error: {str(e)[:80]}")
        return -1.0
    
    def analyze_video(self, avatar_path: str, output_dir: str) -> dict:
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
        print(f"\n{'='*60}")
        print(f"  VIDEO INTELLIGENCE ENGINE — Deep Analysis")
        print(f"{'='*60}")
        
        # Generate unique ID for THIS specific video
        video_id = self._get_video_id(avatar_path)
        print(f"  Video ID: {video_id[:16]}...")
        print(f"  File: {os.path.basename(avatar_path)}")
        
        # Step 1: Fresh transcription (NEVER use cache)
        print(f"\n  [1/5] Transcribing video (fresh — no cache)...")
        transcription, language = self._transcribe_fresh(avatar_path, output_dir)
        full_text = " ".join(seg["text"] for seg in transcription)
        print(f"    -> {len(transcription)} segments, language: {language}")
        print(f"    -> First 100 chars: {full_text[:100]}...")
        
        # Step 2: Get video duration
        from core.video_processor import get_duration
        duration = get_duration(avatar_path)
        print(f"\n  [2/5] Duration: {duration:.1f}s ({duration/60:.1f} min)")
        
        # Step 3: Deep theme analysis with Gemini
        print(f"\n  [3/5] Deep theme analysis with AI...")
        analysis = self._deep_analyze(full_text, language)
        print(f"    -> Theme: {analysis['theme']}")
        print(f"    -> Subtopics: {', '.join(analysis['subtopics'][:5])}")
        print(f"    -> Emotions: {', '.join(analysis['emotions'][:3])}")
        
        # Step 4: Generate shot list
        print(f"\n  [4/5] Creating detailed shot list...")
        shot_list = self._create_shot_list(transcription, analysis, duration)
        print(f"    -> {len(shot_list)} shots planned")
        for shot in shot_list[:5]:
            print(f"      [{shot['start']:.0f}s-{shot['end']:.0f}s] {shot['search_terms'][0]}")
        if len(shot_list) > 5:
            print(f"      ... and {len(shot_list) - 5} more shots")
        
        # Step 5: Generate fresh SRT
        print(f"\n  [5/5] Generating fresh subtitles...")
        srt_path = os.path.join(output_dir, f"subs_{video_id[:8]}.srt")
        self._generate_srt(transcription, srt_path)
        print(f"    -> Saved: {srt_path}")
        
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
    
    def _transcribe_fresh(self, video_path: str, output_dir: str) -> tuple:
        """Transcribe video from scratch — NEVER uses cached audio/srt."""
        from core.video_processor import extract_audio
        from core.subtitle_generator import transcribe_audio
        
        # Use unique filename to prevent any cache collision
        audio_path = os.path.join(output_dir, f"_fresh_audio_{int(time.time())}.wav")
        
        # Clean any old audio files in this directory
        for f in os.listdir(output_dir):
            if f.startswith("_fresh_audio_") or f.startswith("_temp_audio"):
                try:
                    os.remove(os.path.join(output_dir, f))
                except:
                    pass
        
        # Extract fresh audio
        extract_audio(video_path, audio_path)
        
        # Transcribe fresh (30 min timeout for long videos on CPU)
        segments = transcribe_audio(audio_path, language=None, model_size="base", timeout_sec=1800)
        
        # Detect language from first segments
        language = "en"
        if segments:
            first_text = " ".join(s["text"] for s in segments[:5]).lower()
            # Simple language detection
            pt_words = sum(1 for w in ["que", "não", "como", "para", "você", "isso", "muito", "mais", "uma", "dos"] if w in first_text.split())
            es_words = sum(1 for w in ["que", "como", "para", "los", "las", "por", "una", "con", "pero", "más"] if w in first_text.split())
            en_words = sum(1 for w in ["the", "and", "that", "this", "with", "from", "have", "not", "but", "for"] if w in first_text.split())
            
            if pt_words > en_words and pt_words > es_words:
                language = "pt"
            elif es_words > en_words:
                language = "es"
        
        # Cleanup
        try:
            os.remove(audio_path)
        except:
            pass
        
        return segments, language
    
    def _deep_analyze(self, full_text: str, language: str) -> dict:
        """Use GLM-5.1 for deep video understanding (Gemini fallback)."""
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

        # Try GLM-5.1 first
        glm_text = self._glm_ask(prompt, temperature=0.3)
        if glm_text:
            try:
                if glm_text.startswith("```"):
                    glm_text = glm_text.split("\n", 1)[1] if "\n" in glm_text else glm_text
                    glm_text = glm_text.rsplit("```", 1)[0] if "```" in glm_text else glm_text
                return json.loads(glm_text.strip())
            except Exception as e:
                print(f"    GLM JSON parse error: {e}")
        
        # Fallback to Gemini
        if self.client:
            try:
                result_container = []
                def _call_gemini():
                    r = self.client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt,
                    )
                    result_container.append(r)
                t = threading.Thread(target=_call_gemini, daemon=True)
                t.start()
                t.join(timeout=60)
                if t.is_alive():
                    raise TimeoutError("Gemini analysis timed out after 60s")
                response = result_container[0] if result_container else None
                if response and response.text:
                    text = response.text.strip()
                    if text.startswith("```"):
                        text = text.split("\n", 1)[1] if "\n" in text else text
                        text = text.rsplit("```", 1)[0] if "```" in text else text
                    return json.loads(text.strip())
            except Exception as e:
                print(f"    Gemini analysis error: {e}")
        
        return self._fallback_analyze(full_text)
    
    def _fallback_analyze(self, full_text: str) -> dict:
        """Fallback analysis without AI — covers 200+ niches via theme_database."""
        import re
        text = full_text.lower()

        # Import massive theme + emotion database
        from core.theme_database import THEME_DB, EMOTION_DB

        # Score every theme category — USE WORD BOUNDARIES to avoid 'ant' in 'want'
        def _wmatch(kw, txt):
            # multi-word keywords: simple substring (already specific enough)
            if " " in kw or "-" in kw:
                return kw in txt
            # single-word: require word boundary
            return bool(re.search(r"\b" + re.escape(kw) + r"\b", txt))

        best_theme = "general documentary"
        best_score = 0
        for theme, keywords in THEME_DB.items():
            score = sum(1 for k in keywords if _wmatch(k, text))
            if score > best_score:
                best_score = score
                best_theme = theme

        # Log if fallback used (helps debug E2E issues)
        print(f"    [video_intelligence] FALLBACK theme detection: '{best_theme}' (score={best_score})")
        if best_score < 2:
            print(f"    [video_intelligence] WARN: low theme confidence ({best_score}), using 'general documentary' instead")
            best_theme = "general documentary"

        # Smart emotion detection using expanded triggers — also use word boundaries
        detected_emotions = ["informative"]
        for emotion, triggers in EMOTION_DB.items():
            if any(_wmatch(t, text) for t in triggers):
                detected_emotions.append(emotion)
        
        # Cap at 4 emotions
        detected_emotions = detected_emotions[:4]
        
        # Extract subtopics: prioritize theme keywords found in text, fallback to nouns
        # AVOID generic verbs/adverbs that pollute search_terms downstream
        words = [re.sub(r"[^\w]", "", w.lower()) for w in text.split()]
        stop = {"the","a","an","is","are","was","were","be","been","being","to","of","in",
                "for","on","with","at","by","from","and","but","or","not","this","that","it",
                "you","he","she","we","they","i","my","your","his","her","its","our","their",
                "has","have","had","do","does","did","will","would","could","should","can",
                "after","before","because","when","while","until","since","about","over",
                "under","through","between","into","onto","upon","very","more","most","much",
                "many","few","some","any","all","every","each","other","another","again",
                "also","just","only","even","still","already","always","never","often",
                "sometimes","usually","really","actually","probably","maybe","perhaps","share",
                "tell","want","need","make","take","give","know","think","seem","look","feel",
                "show","find","keep","start","stop","change","work","help","try","ask","talk",
                "happen","become","appear","matter","right","wrong","good","bad","new","old",
                "people","person","thing","things","place","time","year","years","day","days",
                "promise","exactly","desperately","really","unexpectedly","unfortunately"}
        # Theme keywords that DID match in text — these are the most semantic
        theme_kws_in_text = [k for k in THEME_DB.get(best_theme, []) if _wmatch(k, text)]
        # Multi-word noun-like phrases from text (e.g. "lymphatic system")
        bigrams = []
        for i in range(len(words)-1):
            a, b = words[i], words[i+1]
            if a and b and a not in stop and b not in stop and len(a) > 3 and len(b) > 3:
                bigrams.append(f"{a} {b}")
        meaningful = [w for w in words if len(w) > 5 and w not in stop]
        freq = {}
        for w in meaningful:
            freq[w] = freq.get(w, 0) + 1
        # Combine: theme keywords first, then bigrams, then most frequent words
        subtopics = []
        subtopics.extend(theme_kws_in_text[:3])
        # Add top bigrams
        bg_freq = {}
        for bg in bigrams: bg_freq[bg] = bg_freq.get(bg,0)+1
        subtopics.extend(sorted(bg_freq, key=bg_freq.get, reverse=True)[:3])
        # Fill rest with frequent words
        for w in sorted(freq, key=freq.get, reverse=True):
            if w not in subtopics: subtopics.append(w)
            if len(subtopics) >= 10: break
        # Dedup keep order
        seen = set(); subtopics = [x for x in subtopics if not (x in seen or seen.add(x))][:10]
        
        return {
            "theme": best_theme,
            "subtopics": subtopics,
            "emotions": detected_emotions[:4],
            "target_audience": "general",
            "visual_style": "documentary",
            "key_moments": [],
        }
    
    # ─────────────────────────────────────────────────────────────────────
    # V2: PER-CHUNK + QUOTE-ANCHORED + CHAIN-OF-THOUGHT MATCHING
    # ─────────────────────────────────────────────────────────────────────
    # Why: V1 sends WHOLE transcript and asks Gemini to chunk it. Gemini loses
    # the tight binding between WHAT IS SAID at time T and WHAT IS SHOWN at time T.
    # V2 sends ONE chunk at a time with its EXACT text and forces chain-of-thought:
    #   Step 1: Identify literal concept (no metaphors)
    #   Step 2: Imagine what viewer expects to see
    #   Step 3: Generate 2 concrete stock queries (wide + closeup)
    # This dramatically improves precision: "ancient root for memory" actually
    # gets "elderly hands holding herb root" instead of "person walking".
    # ─────────────────────────────────────────────────────────────────────

    def _chunk_transcription_aligned(self, transcription: list, target_dur: float = 8.0) -> list:
        """Group whisper segments into time chunks of ~target_dur seconds,
        respecting sentence boundaries (period/comma at end).
        Returns list of {start, end, text} ordered by time."""
        if not transcription:
            return []
        chunks = []
        current = {"start": float(transcription[0]["start"]), "end": 0.0, "text": ""}
        for seg in transcription:
            seg_text = seg.get("text", "").strip()
            seg_start = float(seg.get("start", 0))
            seg_end = float(seg.get("end", seg_start))
            chunk_dur = seg_end - current["start"]
            # Close chunk if duration target reached AND we have ended a sentence
            ends_sentence = current["text"].rstrip().endswith((".", "!", "?"))
            if current["text"] and chunk_dur >= target_dur and (ends_sentence or chunk_dur >= target_dur * 1.5):
                chunks.append(dict(current))
                current = {"start": seg_start, "end": seg_end, "text": seg_text}
            else:
                current["text"] = (current["text"] + " " + seg_text).strip()
                current["end"] = seg_end
        if current["text"].strip():
            chunks.append(current)
        return chunks

    def _parse_chunk_shot_json(self, raw: str, chunk: dict) -> dict:
        """Parse JSON response from chain-of-thought chunk matcher. Returns shot dict or None."""
        if not raw:
            return None
        text = raw.strip()
        # Strip markdown fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0] if "```" in text else text
        text = text.strip()
        # Find first { and last } (handles trailing commentary)
        if "{" in text and "}" in text:
            text = text[text.index("{"):text.rindex("}") + 1]
        try:
            data = json.loads(text)
        except Exception:
            return None
        terms = data.get("terms", [])
        if not isinstance(terms, list):
            return None
        terms = [str(t).strip() for t in terms if isinstance(t, (str, int, float)) and len(str(t).strip()) >= 3]
        if not terms:
            return None
        return {
            "start": float(chunk["start"]),
            "end": float(chunk["end"]),
            "search_terms": terms[:2],
            "shot_type": str(data.get("shot_type", "wide")).lower().strip() or "wide",
            "mood": str(data.get("mood", "informative")).lower().strip() or "informative",
            "text_preview": chunk["text"][:120],
            "visual_concept": data.get("visual_concept", "")[:200],
            "quote": data.get("quote", chunk["text"][:80])[:200],
        }

    def _match_chunk_to_visuals(self, chunk: dict, analysis: dict, avoid_shots: list = None) -> dict:
        """Per-chunk chain-of-thought matcher. Returns shot dict bound to this chunk's
        exact text. Tries GLM first, then Gemini, then heuristic fallback."""
        text = chunk["text"].strip()
        theme = analysis.get("theme", "documentary")
        emotions = analysis.get("emotions", ["informative"])
        emotion = (emotions[0] if isinstance(emotions, list) and emotions else "informative")
        avoid_list = avoid_shots or []
        avoid_hint = ""
        if avoid_list:
            avoid_hint = f"\nDO NOT use shot_type: {', '.join(avoid_list)} (used in previous beats)."

        # Chain-of-thought prompt — forces Gemini to quote + reason + then generate
        prompt = f"""You are a documentary editor matching B-roll to ONE specific narration moment.

EXACT NARRATION SAID AT THIS MOMENT:
\"\"\"{text[:600]}\"\"\"

CONTEXT (background only — DO NOT search for these): theme=\"{theme}\" | tone={emotion}

═══ THINK STEP BY STEP ═══

Step 1 — QUOTE: Copy the most VISUALLY CONCRETE phrase from the narration above.
  ✅ Good: "swollen ankle after surgery", "elderly hands counting pills"
  ❌ Bad: abstract phrases like "things change", "matters a lot"

Step 2 — VISUAL CONCEPT: Describe in ONE specific sentence what a viewer should literally SEE on screen during this moment. Pick the most TANGIBLE noun/action mentioned (not metaphors).
  Example narration: "her memory faded like a candle in the wind"
  → visual_concept: "elderly woman looking confused in a dim room, candle on table"

Step 3 — TWO STOCK QUERIES: Generate 2 search terms for Pexels/Pixabay/YouTube.
  - Each MUST be 3-6 English words describing a CONCRETE filmable scene
  - First query: WIDE shot establishing the scene
  - Second query: CLOSEUP detail of the same concept
  - Reject vague queries ("person doing thing", "nature view")
  - Specific is better: "elderly woman pill bottle kitchen" > "person taking medicine"

Reply ONLY valid JSON (no markdown, no extra text):
{{
  "quote": "<exact phrase from Step 1>",
  "visual_concept": "<one sentence from Step 2>",
  "terms": ["<wide query 3-6 words>", "<closeup query 3-6 words>"],
  "shot_type": "wide",
  "mood": "{emotion}"
}}{avoid_hint}"""

        # Try GLM-5.1 first (better quota than free Gemini)
        glm_resp = self._glm_ask(prompt, temperature=0.4, enable_thinking=False, timeout=45.0)
        if glm_resp:
            shot = self._parse_chunk_shot_json(glm_resp, chunk)
            if shot:
                return shot

        # Fallback Gemini
        if self.client:
            try:
                from google.genai import types as _gtypes
                cfg = _gtypes.GenerateContentConfig(temperature=0.4, response_mime_type="application/json")
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash", contents=prompt, config=cfg)
                if response and response.text:
                    shot = self._parse_chunk_shot_json(response.text, chunk)
                    if shot:
                        return shot
            except Exception as e:
                print(f"    [v2 chunk] Gemini fallback err: {str(e)[:80]}")

        # Last resort heuristic
        return self._heuristic_chunk_shot(chunk, analysis)

    def _heuristic_chunk_shot(self, chunk: dict, analysis: dict) -> dict:
        """Heuristic fallback when both LLMs fail for a single chunk.
        Extracts nouns and pairs with theme. Always returns a valid shot."""
        text = chunk["text"].strip()
        theme = analysis.get("theme", "documentary")
        subtopics = analysis.get("subtopics", [])
        # Use existing _extract_visual_keywords with subtopics if available
        try:
            terms = self._extract_visual_keywords_from_text(text, theme, subtopics=subtopics)
        except Exception:
            terms = [f"{theme} footage", f"{theme} documentary"]
        return {
            "start": float(chunk["start"]),
            "end": float(chunk["end"]),
            "search_terms": terms[:2] if terms else [f"{theme} closeup", f"{theme} wide"],
            "shot_type": "wide",
            "mood": "informative",
            "text_preview": text[:120],
            "visual_concept": f"[heuristic fallback for: {text[:60]}]",
            "quote": text[:80],
        }

    def _create_shot_list_v2(self, transcription: list, analysis: dict, duration: float) -> list:
        """V2 per-chunk + quote-anchored matcher. Drop-in replacement for _create_shot_list.
        Each chunk gets its OWN Gemini call with EXACT text — no global drift."""
        chunks = self._chunk_transcription_aligned(transcription, target_dur=8.0)
        if not chunks:
            return []
        print(f"    [v2] Per-chunk analysis: {len(chunks)} chunks")
        shots = []
        used_terms = set()
        for i, chunk in enumerate(chunks):
            avoid = []
            if len(shots) >= 2 and shots[-1]["shot_type"] == shots[-2]["shot_type"]:
                avoid = [shots[-1]["shot_type"]]
            shot = self._match_chunk_to_visuals(chunk, analysis, avoid_shots=avoid)
            # Dedupe terms across chunks to avoid same B-roll showing 2x
            unique_terms = []
            for t in shot.get("search_terms", []):
                tl = t.lower().strip()
                if tl and tl not in used_terms:
                    used_terms.add(tl)
                    unique_terms.append(t)
            if not unique_terms:
                # All terms duplicate — synthesize new from quote
                quote = shot.get("quote", chunk["text"][:60])
                theme = analysis.get("theme", "documentary")
                unique_terms = [f"{quote.split()[0] if quote.split() else theme} {theme}".strip()]
            shot["search_terms"] = unique_terms[:2]
            shots.append(shot)
            if (i + 1) % 5 == 0:
                print(f"    [v2] {i+1}/{len(chunks)} chunks matched")
        print(f"    [v2] -> {len(shots)} shots with quote-anchored matching")
        return shots

    def _create_shot_list(self, transcription: list, analysis: dict, duration: float) -> list:
        """SEMANTIC shot list — analyzes ENTIRE video with emotion + shot type variety.
        Uses V2 per-chunk matcher by default (use_v1_global=True to force old path)."""
        # V2 is dramatically better: per-chunk binding instead of global drift
        if not getattr(self, "use_v1_global", False):
            try:
                v2_shots = self._create_shot_list_v2(transcription, analysis, duration)
                if v2_shots and len(v2_shots) >= 3:
                    return v2_shots
                print(f"    [v2] returned only {len(v2_shots)} shots, falling back to v1 global")
            except Exception as e:
                print(f"    [v2] failed: {str(e)[:120]}, falling back to v1 global")
        # V1 global path (original)
        return self._create_shot_list_v1_global(transcription, analysis, duration)

    def _create_shot_list_v1_global(self, transcription: list, analysis: dict, duration: float) -> list:
        """V1 ORIGINAL: global shot list (legacy, fallback when v2 fails)."""
        full_text = " ".join(seg["text"] for seg in transcription)
        theme = analysis["theme"]
        emotions = ", ".join(analysis.get("emotions", ["informative"])[:3])
        subtopics = ", ".join(analysis.get("subtopics", [])[:8])
        
        # Calculate ideal segment count (5-9 seconds each, dynamic based on content)
        ideal_segment_dur = 7  # seconds avg
        num_segments = max(3, int(duration / ideal_segment_dur))
        
        prompt = f"""You are a SENIOR video editor creating a CINEMATIC shot list for a documentary.
Your goal is PERFECT VISUAL COHERENCE — every B-roll clip MUST visually match what the narrator is saying.

FULL NARRATION:
\"\"\"{full_text[:5000]}\"\"\"

VIDEO INFO:
- Theme: {theme}
- Key topics: {subtopics}
- Emotional tone: {emotions}
- Duration: {duration:.0f}s
- Target: {num_segments} segments (5-9 seconds each)

CREATE a shot list where each segment has:
1. "terms": 2 UNIQUE stock footage search queries (English, 2-5 words)
2. "shot_type": one of "wide", "closeup", "aerial", "detail", "pov", "diagram"
3. "mood": one of "dramatic", "calm", "urgent", "mysterious", "hopeful", "informative"

═══ CRITICAL: SEMANTIC VISUAL MATCHING RULES ═══

YOU MUST understand the MEANING behind words and choose visuals that a VIEWER would expect to see:

✅ CORRECT EXAMPLES:
  Narration: "millions died from the Black Plague" → "medieval plague victims illustration" (NOT "number millions")
  Narration: "the ocean is 11 kilometers deep" → "deep ocean trench submarine footage" (NOT "water surface beach")
  Narration: "ancient Egyptians built the pyramids" → "pyramid construction workers ancient Egypt" (NOT "modern construction site")
  Narration: "the human brain processes information" → "brain neural network animation medical" (NOT "person thinking")
  Narration: "stock markets crashed in 2008" → "wall street traders panic financial crisis" (NOT "falling graph")
  Narration: "these supplements contain omega-3" → "fish oil capsules laboratory closeup" (NOT "fish swimming ocean")
  Narration: "soldiers fought in the trenches" → "world war trench warfare soldiers" (NOT "modern army marching")
  Narration: "the amazon rainforest is disappearing" → "deforestation Amazon burning trees aerial" (NOT "green forest nature")

❌ NEVER DO:
  - Literal word matching (hearing "cold" → DON'T search "ice cube")
  - Generic filler (DON'T use "beautiful scenery", "people talking", "city skyline")
  - Abstract concepts (DON'T use "freedom", "hope", "power" as search terms)
  - Repeated patterns (DON'T use the same shot_type 3 times in a row)

ADDITIONAL RULES:
- Every search term must describe a CONCRETE VISUAL SCENE that exists as stock footage
- Terms must be 2-5 words, specific enough to find on Pexels/Pixabay/YouTube
- VISUAL VARIETY: cycle through shot types: wide → closeup → detail → wide → aerial → pov
- MOOD MATCHING: dramatic narration → dramatic/urgent mood, calm facts → calm/informative

Return ONLY valid JSON array (no markdown):
[
  {{"start": 0, "end": 7, "terms": ["ocean waves aerial view", "deep sea creature closeup"], "shot_type": "wide", "mood": "mysterious"}},
  ...
]"""

        # Generic/abstract terms that produce bad B-roll (filler footage).
        # If Gemini returns these, we strip and substitute with topic-aware fallbacks.
        BANNED_GENERIC_TERMS = {
            "people talking", "person talking", "beautiful scenery", "nice view",
            "city skyline", "background", "footage", "broll", "b-roll",
            "stock footage", "cinematic shot", "establishing shot",
            "freedom", "hope", "power", "love", "peace", "happiness",
            "abstract concept", "abstract visualization",
            "generic video", "generic image", "random",
        }

        def _parse_and_validate(text):
            # Strip code fences if present (Gemini sometimes wraps in ```json...```)
            raw = text.strip()
            if raw.startswith("```"):
                # Remove first fence line
                if "\n" in raw:
                    raw = raw.split("\n", 1)[1]
                # Remove trailing fence
                if "```" in raw:
                    raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()
            # Robust extraction: find first [ and last ] (handles trailing commentary from Gemini)
            if "[" in raw and "]" in raw:
                lb = raw.index("[")
                rb = raw.rindex("]")
                raw = raw[lb:rb + 1]
            # Strip BOM and stray characters
            raw = raw.lstrip("﻿​").strip()
            shot_list = json.loads(raw)
            if not isinstance(shot_list, list):
                raise ValueError("Shot list root must be a JSON array")

            validated = []
            used_terms = set()
            last_shot_types = []
            valid_types = {"wide", "closeup", "aerial", "detail", "pov", "diagram"}
            valid_moods = {"dramatic", "calm", "urgent", "mysterious", "hopeful", "informative"}

            for shot in shot_list:
                if not isinstance(shot, dict):
                    continue
                try:
                    shot["start"] = float(shot.get("start", 0))
                    shot["end"] = float(shot.get("end", shot["start"] + 8))
                except (TypeError, ValueError):
                    continue

                # Accept both "terms" and "search_terms" — Gemini sometimes uses the latter
                terms = shot.get("terms") or shot.get("search_terms") or []
                if isinstance(terms, str):
                    terms = [terms]

                # Language guard: terms must look English. Otherwise YouTube
                # CC-search matches by literal text and pulls Minecraft/random
                # PT videos for "personalizar completamente". Detect PT/ES
                # giveaways and anchor with theme as last-resort English context.
                PT_GIVEAWAYS = {
                    "voce","voces","nossa","nosso","nossos","nossas","muito","muitos",
                    "pode","podem","tudo","apenas","tambem","sera","sao","esta","esto",
                    "completamente","facilmente","rapidamente","totalmente","extremamente",
                    "qualquer","alguma","algum","quando","onde","porque","entao","assim",
                    "este","esta","aquele","aquela","isso","aquilo","esse","essa",
                }
                import re as _re_lang
                def _looks_non_english(s):
                    sl = s.lower()
                    # PT/ES accent residue (after stripping should be gone, but Gemini
                    # might emit raw PT) OR -mente/-ção suffix OR known PT function words
                    if any(ch in sl for ch in "áéíóúãõçâêôà"):
                        return True
                    words = _re_lang.findall(r"[a-zA-ZÀ-ſ]+", sl)
                    if any(w.endswith(("mente","cao","ções")) for w in words):
                        return True
                    if any(w in PT_GIVEAWAYS for w in words):
                        return True
                    return False

                theme_word = (theme or "").strip().split()[0] if theme else ""

                unique_terms = []
                for t in terms:
                    if not isinstance(t, str):
                        continue
                    t_clean = t.strip().strip('"\'').strip()
                    t_lower = t_clean.lower()
                    # Filter: length, banned generics, duplicates, no abstract concepts
                    if len(t_clean) < 3 or len(t_clean) > 80:
                        continue
                    if t_lower in used_terms:
                        continue
                    if t_lower in BANNED_GENERIC_TERMS:
                        print(f"    [shot_list] Filtered banned generic term: '{t_clean}'")
                        continue
                    # Must contain at least one noun-like word (>3 chars)
                    if not any(len(w) > 3 for w in t_clean.split()):
                        continue
                    # Language guard — append theme to non-English terms
                    if _looks_non_english(t_clean) and theme_word:
                        t_clean = f"{t_clean} {theme_word}"
                        t_lower = t_clean.lower()
                        if t_lower in used_terms:
                            continue
                    used_terms.add(t_lower)
                    unique_terms.append(t_clean)

                # If Gemini returned 0 valid terms for this shot, derive from text_preview
                if not unique_terms:
                    preview = shot.get("text_preview") or ""
                    # Pass subtopics so fallback can use semantic phrases instead of random words
                    fallback = self._extract_visual_keywords_from_text(
                        preview[:200], theme, subtopics=analysis.get("subtopics"))
                    for t in fallback:
                        tl = t.lower()
                        if tl not in used_terms and tl not in BANNED_GENERIC_TERMS:
                            used_terms.add(tl)
                            unique_terms.append(t)
                            if len(unique_terms) >= 2: break

                # Validate shot_type
                st = str(shot.get("shot_type", "wide")).lower().strip()
                if st not in valid_types:
                    st = "wide"
                # Prevent 3+ same shot types in a row
                if len(last_shot_types) >= 2 and all(x == st for x in last_shot_types[-2:]):
                    alternatives = sorted(valid_types - {st})
                    st = alternatives[len(validated) % len(alternatives)]
                last_shot_types.append(st)

                # Validate mood
                mood = str(shot.get("mood", "informative")).lower().strip()
                if mood not in valid_moods:
                    mood = "informative"

                shot["search_terms"] = unique_terms[:2]
                shot["shot_type"] = st
                shot["mood"] = mood
                # Preserve text_preview if Gemini provided it (used for picker UI)
                if "text_preview" not in shot or not shot["text_preview"]:
                    shot["text_preview"] = ""
                shot["visual_description"] = f"[{st.upper()}] {unique_terms[0] if unique_terms else 'b-roll'}"
                # Only keep shots with at least 1 valid term — others would just download garbage
                if unique_terms:
                    validated.append(shot)
            return validated, used_terms

        # === AI FALLBACK CHAIN: GLM (NVIDIA) -> Gemini -> per-chunk heuristic ===
        # GLM-5.1 (reasoning model) tried FIRST because:
        #   - More quota than Gemini free tier
        #   - Reasoning mode produces better structured output for shot lists
        #   - If GLM fails (rate limit/error), Gemini is tried automatically

        def _try_glm_shot_list():
            """Call GLM-5.1 with the same shot list prompt. Returns parsed JSON or None."""
            glm_text = self._glm_ask(prompt + "\n\nReturn STRICTLY valid JSON only.",
                                     temperature=0.3)
            if not glm_text:
                return None
            try:
                if glm_text.startswith("```"):
                    glm_text = glm_text.split("\n", 1)[1] if "\n" in glm_text else glm_text
                    glm_text = glm_text.rsplit("```", 1)[0] if "```" in glm_text else glm_text
                # Extract JSON between first [ and last ]
                start = glm_text.find("[")
                end = glm_text.rfind("]")
                if start >= 0 and end > start:
                    return glm_text[start:end+1].strip()
                return glm_text.strip()
            except Exception:
                return None

        # Try GLM first
        glm_json = _try_glm_shot_list()
        if glm_json:
            try:
                validated, used_terms = _parse_and_validate(glm_json)
                if validated:
                    print(f"    -> Global shot list (GLM): {len(validated)} segments, {len(used_terms)} unique terms")
                    return validated
                print(f"    -> GLM returned 0 valid shots, trying Gemini fallback...")
            except json.JSONDecodeError as je:
                print(f"    -> GLM JSON parse failed ({je}), trying Gemini fallback...")
            except Exception as e:
                print(f"    -> GLM error: {e}, trying Gemini fallback...")

        # Fallback: Gemini — JSON mode + 2-attempt retry
        if self.client:
            for attempt in range(2):
                try:
                    # Try with JSON response mode first (more reliable parsing)
                    try:
                        from google.genai import types as _gen_types
                        cfg = _gen_types.GenerateContentConfig(
                            temperature=0.3,
                            response_mime_type="application/json",
                        )
                        response = self.client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=prompt,
                            config=cfg,
                        )
                    except Exception:
                        # Fallback to plain mode if types not available
                        response = self.client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=prompt,
                        )
                    if response and response.text:
                        validated, used_terms = _parse_and_validate(response.text.strip())
                        if validated:
                            print(f"    -> Global shot list (Gemini): {len(validated)} segments, {len(used_terms)} unique terms")
                            return validated
                        else:
                            print(f"    -> Attempt {attempt+1}: 0 valid shots after filtering (banned/generic terms)")
                except json.JSONDecodeError as je:
                    print(f"    -> Attempt {attempt+1} JSON parse error: {je}")
                    if attempt == 0:
                        prompt = prompt + "\n\nIMPORTANT: Return STRICTLY valid JSON. No markdown, no commentary, no trailing text."
                        time.sleep(2)
                        continue
                except Exception as e:
                    print(f"    -> Attempt {attempt+1} Gemini error: {e}")
                    if attempt == 0:
                        time.sleep(3)
                        continue


        # Fallback: per-chunk analysis (with batching for efficiency)
        print(f"    -> Falling back to per-chunk analysis...")
        chunks = []
        current = {"start": 0, "end": 0, "text": ""}
        for seg in transcription:
            if seg["start"] - current["start"] >= 6 and current["text"]:
                current["end"] = seg["start"]
                chunks.append(dict(current))
                current = {"start": seg["start"], "end": seg["end"], "text": ""}
            current["text"] += " " + seg["text"]
            current["end"] = seg["end"]
        if current["text"].strip():
            current["end"] = duration
            chunks.append(current)
        
        shot_list = []
        gemini_ok = 0
        gemini_fail = 0
        used_terms = set()
        
        # Process chunks in batches of 5 for efficiency (reduces API calls by 5x)
        batch_size = 5
        for batch_start in range(0, len(chunks), batch_size):
            batch = chunks[batch_start:batch_start + batch_size]
            
            batch_timeline = "\n".join(
                f"Seg {j+1} ({c['start']:.0f}s-{c['end']:.0f}s): \"{c['text'].strip()[:200]}\""
                for j, c in enumerate(batch)
            )
            
            prompt = f"""You are a SEMANTIC B-roll matcher. Find stock footage for EACH segment below.

VIDEO THEME: {theme}

SEGMENTS:
{batch_timeline}

For EACH segment, provide exactly 2 stock footage search terms (English, 2-5 words each).
Search the VISUAL MEANING, not literal words.
Every term must be UNIQUE across all segments.
Return ONLY terms, 2 per segment, in order. No explanation, no numbers."""
            
            batch_terms = []

            # Try GLM first for per-chunk batch (faster + more quota than Gemini free)
            glm_resp = self._glm_ask(prompt, temperature=0.3)
            response = None
            if glm_resp and glm_resp.strip():
                # Wrap GLM response in a mock object with .text attribute
                class _FakeResp:
                    def __init__(self, text): self.text = text
                response = _FakeResp(glm_resp)

            for attempt in range(2):
                try:
                    if response is None:
                        # GLM didn't work for this batch, try Gemini
                        if not self.client:
                            break
                        rc = []
                        def _call_batch():
                            r = self.client.models.generate_content(
                                model="gemini-2.5-flash",
                                contents=prompt,
                            )
                            rc.append(r)
                        t = threading.Thread(target=_call_batch, daemon=True)
                        t.start()
                        # Adaptive timeout: 15s per chunk in batch
                        t.join(timeout=max(30, len(batch) * 15))
                        if t.is_alive():
                            raise TimeoutError("Gemini batch analysis timed out")
                        response = rc[0] if rc else None
                    if response and response.text:
                        raw = [t.strip().lstrip("0123456789.-) ").strip("\"'*•–—")
                               for t in response.text.strip().split("\n") if t.strip()]
                        batch_terms = [t for t in raw if 2 < len(t) < 60 and t.lower() not in used_terms]
                        if batch_terms:
                            break
                except Exception as e:
                    err_str = str(e).lower()
                    if "429" in err_str or "quota" in err_str or "rate" in err_str:
                        time.sleep(5)
                    elif attempt == 0:
                        time.sleep(2)
                    else:
                        print(f"    Batch error at {batch[0]['start']:.0f}s: {e}")
            
            # Assign terms to chunks
            term_idx = 0
            for chunk in batch:
                terms = []
                # Take up to 2 terms from the batch result
                while term_idx < len(batch_terms) and len(terms) < 2:
                    t = batch_terms[term_idx]
                    term_idx += 1
                    if t.lower() not in used_terms:
                        used_terms.add(t.lower())
                        terms.append(t)
                
                if terms:
                    gemini_ok += 1
                else:
                    gemini_fail += 1
                    terms = self._extract_visual_keywords_from_text(
                        chunk["text"].strip()[:400], theme,
                        subtopics=analysis.get("subtopics"))
                    for t in terms:
                        used_terms.add(t.lower())
                
                shot_list.append({
                    "start": chunk["start"],
                    "end": chunk["end"],
                    "text_preview": chunk["text"].strip()[:100],
                    "search_terms": terms[:2],
                    "visual_description": f"Scene for: {chunk['text'].strip()[:60]}",
                })
            
            time.sleep(0.5)  # Brief pause between batches
        
        print(f"    -> Gemini OK: {gemini_ok}, Fallback: {gemini_fail}")
        return shot_list
    
    def _extract_visual_keywords_from_text(self, text: str, theme: str,
                                            subtopics: list = None) -> list:
        """Extract visual keywords when Gemini fails. ALWAYS anchors to theme so
        searches don't hit unrelated content.

        Strategy (in order):
          1. If subtopics provided, use first 2-3 subtopics directly (they're
             already semantic phrases like 'lymphatic system function').
          2. Extract bigrams (noun phrases) from text.
          3. Pair top-2 frequency words WITH theme as anchor (last resort).

        This avoids the 'body Lymphatic' / 'tell Lymphatic' bug where random
        words from transcript get paired with theme.
        """
        # FAST PATH: if we have good subtopics, use them directly
        if subtopics:
            # Subtopics are typically 'Lymphatic system function' or
            # 'Age-related body changes' — already great B-roll queries
            result = []
            for st in subtopics[:3]:
                if isinstance(st, str) and 3 <= len(st) <= 80:
                    # Trim long phrases to 5 words max
                    words = st.split()[:5]
                    result.append(" ".join(words))
            if result:
                return result
        stop = {
            # EN
            "the","a","an","is","are","was","were","be","to","of","in","for","on",
            "with","at","by","from","and","but","or","not","this","that","it","you",
            "he","she","we","they","my","your","his","her","its","our","their","has",
            "have","had","do","does","did","will","would","could","should","can","may",
            "might","shall","about","just","also","very","really","more","most","much",
            "many","some","any","all","every","each","one","two","like","know","think",
            "going","want","need","look","make","thing","things","because","when","what",
            "how","which","where","people","something","actually","right","even","still",
            "well","because","there","here","then","than","into","over","under","such",
            # PT
            "pode","podem","posso","podemos","quer","queremos","precisa","precisam",
            "deve","devem","temos","temos","tinha","tinham","sera","serao","foi","foram",
            "vai","vao","vou","vamos","faz","fazem","feito","feita","ficar","ficam",
            "usar","usam","usado","tenta","tentam","tentar","tentando","conseguir",
            "que","com","para","por","como","mas","sem","sobre","entre","muito","mais",
            "menos","esse","essa","isso","aquele","aquela","aquilo","ser","estar","ter",
            "haver","fazer","ir","vir","ver","dar","dizer","saber","poder","querer","nao",
            "sim","tambem","apenas","cada","todo","toda","todos","todas","seu","sua","seus",
            "suas","meu","minha","meus","minhas","nosso","nossa","nossos","nossas",
            "voce","voces","ele","ela","eles","elas","quando","onde","quem","qual","porque",
            "porem","contudo","entao","assim","ainda","sera","sao","foi","foram","esta",
            "estao","sido","gente","coisa","coisas","muita","muitas","muito","muitos",
            "deve","podem","sendo","tendo","mesmo","mesma","mesmos","mesmas",
            # ES (common cognates)
            "esto","esta","estos","estas","ese","esa","esos","esas","muy","con","sin",
            "por","para","sobre","entre","como","pero","tambien","todos","todas","cada",
        }
        # Generic fluff that creates filler footage
        fluff = {"tecnologia","sistema","plataforma","forma","jeito","modo","tipo",
                 "parte","lado","caso","exemplo","problema","solucao","situacao",
                 "momento","tempo","tudo","nada","algo","alguem","alguma","algum"}

        words = [w.strip(".,!?;:\"'()-[]/").lower() for w in text.split()]
        meaningful = [w for w in words if len(w) > 3 and w not in stop and w not in fluff]

        freq = {}
        for w in meaningful:
            freq[w] = freq.get(w, 0) + 1
        top = sorted(freq, key=freq.get, reverse=True)[:4]

        # Always anchor to theme — this is the key fix vs raw narration words
        theme_anchor = (theme or "documentary").strip()
        # Take only first word of theme to keep query short
        theme_word = theme_anchor.split()[0] if theme_anchor else "documentary"

        # Dedupe and avoid 'X X' patterns
        result = []
        seen = set()
        for w in top:
            if w == theme_word or w in theme_anchor.lower():
                continue
            q = f"{w} {theme_word}"
            if q.lower() in seen:
                continue
            seen.add(q.lower())
            result.append(q)
            if len(result) >= 2:
                break

        if len(result) >= 2:
            return result
        if result:
            return result + [f"{theme_anchor} footage"]
        return [f"{theme_anchor} footage", f"{theme_anchor} documentary"]
    
    def _fallback_shot_list(self, transcription: list, analysis: dict) -> list:
        """Create shot list without AI using smart keyword extraction."""
        from core.smart_broll import _group_segments, _extract_smart_keywords
        
        chunks = _group_segments(transcription, 10.0, 
                                transcription[-1]["end"] if transcription else 60)
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
    
    def _textual_match_score(self, title_or_meta: str, expected_keywords: list, theme: str) -> float:
        """Offline textual similarity: how well does the asset title/metadata
        match the expected keywords + theme? Returns 0-1. Used as a free
        fallback when Vision API is unavailable (quota), and as a cheap
        pre-filter before spending a Vision call.
        """
        if not title_or_meta:
            return -1.0
        import re as _re, unicodedata as _ud
        def norm(s):
            s = _ud.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
            return _re.sub(r"[^a-z0-9 ]+", " ", s)
        meta_words = set(w for w in norm(title_or_meta).split() if len(w) > 2)
        kw_words = set()
        for k in expected_keywords[:5]:
            kw_words.update(w for w in norm(k).split() if len(w) > 2)
        theme_words = set(w for w in norm(theme).split() if len(w) > 2)
        all_target = kw_words | theme_words
        if not all_target or not meta_words:
            return -1.0

        # Substring/prefix matching: 'iran' matches 'iranian', 'health' matches 'healthcare'
        def _match_count(targets, candidates):
            hits = 0
            for t in targets:
                for c in candidates:
                    if t == c or (len(t) >= 4 and (t in c or c in t)):
                        hits += 1
                        break
            return hits

        # Direct keyword match weighted 2x, theme 1x
        kw_hits = _match_count(kw_words, meta_words)
        theme_hits = _match_count(theme_words, meta_words)
        score = (kw_hits * 2 + theme_hits) / max(1, len(kw_words) * 2 + len(theme_words))
        return max(0.0, min(1.0, score))

    # Class-level flag: once quota exhausted, stop calling Vision (avoid loop of 429s)
    _vision_quota_exhausted = False

    def validate_clip(self, clip_path: str, expected_keywords: list, theme: str,
                      metadata_text: str = "") -> float:
        """Validate clip matches expected content. Returns score 0-1.

        Strategy (in order):
          1. File integrity check
          2. If quota previously exhausted -> textual fallback only (no Vision)
          3. Frame extraction + Gemini Vision (if quota available)
          4. Textual fallback (title/metadata vs keywords+theme)

        Special return:
          -1.0 = validator unavailable AND no metadata to score textually.
                 Caller should accept without filtering.
        """
        import subprocess, tempfile, re

        if not os.path.exists(clip_path) or os.path.getsize(clip_path) < 1000:
            return 0.0

        # If no Gemini client OR quota already exhausted this session, skip Vision
        if not self.client or VideoIntelligence._vision_quota_exhausted:
            # Try GLM textual scoring first (more semantic than _textual_match_score)
            glm_score = self._glm_score_clip(metadata_text, expected_keywords, theme)
            if glm_score >= 0:
                return glm_score
            txt = self._textual_match_score(metadata_text, expected_keywords, theme)
            # When in fallback mode with no metadata match, accept the clip
            # (better than rejecting everything in a loop)
            return txt if txt > 0 else -1.0

        # Quick file-integrity check for videos
        is_image = clip_path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.bmp'))
        frame_path = None
        if is_image:
            frame_path = clip_path
        else:
            try:
                from core.video_processor import get_duration, get_resolution
                dur = get_duration(clip_path)
                w, h = get_resolution(clip_path)
                if dur < 2.0 or w < 320 or h < 240:
                    return 0.2  # technically bad file
            except Exception:
                pass
            # Extract midpoint frame
            try:
                dur = 3.0
                try:
                    from core.video_processor import get_duration
                    dur = max(1.0, get_duration(clip_path))
                except Exception:
                    pass
                mid = dur / 2.0
                ffmpeg = os.environ.get("FFMPEG_BIN") or "ffmpeg"
                if not os.path.isfile(ffmpeg):
                    ffmpeg = r"C:\Users\Guilherme\Music\automaçao video\ffmpeg\ffmpeg.exe"
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                    frame_path = tf.name
                cmd = [ffmpeg, "-y", "-ss", str(mid), "-i", clip_path,
                       "-frames:v", "1", "-q:v", "3",
                       "-vf", "scale=640:-1", frame_path]
                r = subprocess.run(cmd, capture_output=True, timeout=30)
                if r.returncode != 0 or not os.path.exists(frame_path) or os.path.getsize(frame_path) < 500:
                    return -1.0
            except Exception as e:
                print(f"    [validate_clip] frame extract error: {e}")
                return -1.0

        # Call Gemini Vision with retry on 429
        try:
            from google.genai import types
            with open(frame_path, "rb") as f:
                img_data = f.read()
            kw_text = ", ".join(expected_keywords[:3])
            prompt = (
                f"You are auditing whether a video B-roll visually matches narration.\n"
                f"Topic/theme: {theme}\n"
                f"Search keywords: {kw_text}\n\n"
                f"Rate how well the image visually illustrates the topic, 0-10.\n"
                f"10=perfect literal match; 7=related; 4=weak; 0=wrong/unrelated.\n"
                f"Reply ONLY with a single integer 0-10."
            )
            for attempt in range(3):
                try:
                    resp = self.client.models.generate_content(
                        model="gemini-2.5-flash",  # higher RPM than flash
                        contents=[
                            types.Part.from_bytes(data=img_data, mime_type="image/jpeg"),
                            prompt,
                        ],
                    )
                    if resp and resp.text:
                        m = re.search(r"\b(\d{1,2})\b", resp.text)
                        if m:
                            score = min(10, max(0, int(m.group(1)))) / 10.0
                            if score < 0.4:
                                print(f"    [validate_clip] LOW {score:.2f} '{kw_text}' ({resp.text.strip()[:30]})")
                            return score
                    # Vision returned no usable score — try textual fallback
                    txt_score = self._textual_match_score(metadata_text, expected_keywords, theme)
                    return txt_score
                except Exception as e:
                    msg = str(e)
                    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                        if attempt < 2:
                            import time as _t
                            _t.sleep(8 * (attempt + 1))
                            continue
                        # PERMANENT: set class-level flag so subsequent calls skip Vision
                        VideoIntelligence._vision_quota_exhausted = True
                        print(f"    [validate_clip] quota PERMANENTLY exhausted — switching to GLM textual fallback")
                        # Try GLM first, then textual heuristic
                        glm_s = self._glm_score_clip(metadata_text, expected_keywords, theme)
                        if glm_s >= 0:
                            return glm_s
                        txt = self._textual_match_score(metadata_text, expected_keywords, theme)
                        return txt if txt > 0 else -1.0  # -1 = accept without filtering
                    print(f"    [validate_clip] Vision error ({msg[:80]}) — GLM/textual fallback")
                    glm_s = self._glm_score_clip(metadata_text, expected_keywords, theme)
                    if glm_s >= 0:
                        return glm_s
                    txt = self._textual_match_score(metadata_text, expected_keywords, theme)
                    return txt if txt > 0 else -1.0
        finally:
            if frame_path and frame_path != clip_path and os.path.exists(frame_path):
                try: os.remove(frame_path)
                except Exception: pass
        return -1.0
