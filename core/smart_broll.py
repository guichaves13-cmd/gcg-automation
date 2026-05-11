"""
Smart B-Roll v3 — Professional-grade content-matched footage
1. Transcribes avatar narration (Whisper)
2. Detects overall video theme for contextual keyword generation
3. Gemini AI analyzes each 8-10s chunk and extracts precise search terms
4. Hierarchical download: Pexels video > Pixabay video > Pexels image > Pixabay image
5. Anti-repetition: same URL never reused within a single video
6. Keyword deduplication: repeated search terms get cinematic variants
7. Relevance scoring: low-confidence keywords trigger extended search
8. B-roll log written to _broll_log.json in output_folder
"""

import os
import re
import time
import json
import math
from pathlib import Path


# ── Per-call state — reset at start of each analyze_narration() ─────────
_used_urls: set = set()
_used_keywords: set = set()
_broll_log: list = []


# ════════════════════════════════════════════════════════════════════════
#  PUBLIC INTERFACE
# ════════════════════════════════════════════════════════════════════════

def analyze_narration(
    avatar_path: str,
    google_api_key: str,
    pexels_api_key: str,
    output_folder: str,
    segment_duration: float = 9.0,
    clips_per_segment: int = 1,
    max_clips: int = 500,
    pixabay_api_key: str = "",
    unsplash_api_key: str = "",
    on_progress=None,
) -> list:
    """
    Professional B-roll analysis pipeline with semantic coherence.

    Phase 1 — TRANSCRIBE + THEME:
        Whisper transcribes the avatar narration.
        Full text fed to theme detector (health/war/science/etc).

    Phase 2 — KEYWORDS:
        Gemini AI analyzes each 8-10s chunk WITH the video theme as context.
        Fallback: multi-word phrase map -> visual concept map -> frequency.
        Every keyword tagged with source and confidence score.

    Phase 3 — HIERARCHICAL DOWNLOAD:
        For each keyword: Pexels video -> Pixabay video -> Pexels image -> Pixabay image.
        Anti-repetition: same URL never reused.
        Repeated keyword gets a cinematic variant to force a different result.
        Low-confidence keywords (score < 0.5) try all 4 sources before giving up.

    Returns list of dicts with timeline_start, timeline_end, file, keyword, source, score.
    """
    global _used_urls, _used_keywords, _broll_log
    _used_urls = set()
    _used_keywords = set()
    _broll_log = []

    os.makedirs(output_folder, exist_ok=True)

    # ── Phase 1: Transcribe ───────────────────────────────────────────────
    if on_progress:
        on_progress(0, 100, "Transcrevendo narracao com Whisper AI...")

    from core.subtitle_generator import transcribe_audio
    from core.video_processor import extract_audio, get_duration

    tmp_audio = os.path.join(os.environ.get("TEMP", output_folder), "_sb_audio_tmp.wav")
    extract_audio(avatar_path, tmp_audio)

    segments = transcribe_audio(tmp_audio, language=None, model_size="base")
    avatar_duration = get_duration(avatar_path)

    full_text = " ".join(seg["text"] for seg in segments)
    video_theme = _detect_video_theme(full_text)

    print(f"  [smart_broll] Tema detectado: '{video_theme}' | {len(segments)} segmentos Whisper")

    if on_progress:
        on_progress(15, 100, f"Tema: '{video_theme}'. Criando chunks de {segment_duration:.0f}s...")

    # ── Phase 2: Keyword extraction ───────────────────────────────────────
    chunks = _group_segments(segments, segment_duration, avatar_duration)

    if google_api_key:
        if on_progress:
            on_progress(20, 100, f"Gemini AI: analisando {len(chunks)} chunks (tema: {video_theme})...")
        keywords_per_chunk = _analyze_with_gemini(chunks, google_api_key, on_progress, video_theme)
    else:
        keywords_per_chunk = _extract_smart_keywords(chunks, video_theme)

    if len(keywords_per_chunk) > max_clips:
        keywords_per_chunk = keywords_per_chunk[:max_clips]

    if on_progress:
        on_progress(50, 100, f"{len(keywords_per_chunk)} termos gerados. Baixando B-roll...")

    print(f"  [smart_broll] {len(keywords_per_chunk)} termos de busca:")
    for kw in keywords_per_chunk[:10]:
        src = kw.get("keyword_source", "?")
        sc = kw.get("keyword_score", 0)
        print(f"    t={kw['start']:.0f}s [{src} s={sc:.2f}] '{kw['keyword']}'")
    if len(keywords_per_chunk) > 10:
        print(f"    ... e mais {len(keywords_per_chunk) - 10}")

    # ── Phase 3: Hierarchical download ────────────────────────────────────
    mapped_clips = []
    total = len(keywords_per_chunk)

    for i, item in enumerate(keywords_per_chunk):
        if on_progress:
            pct = 50 + int((i / max(total, 1)) * 45)
            on_progress(pct, 100, f"B-roll {i+1}/{total}: '{item['keyword']}'...")

        keyword = item["keyword"]
        k_score = item.get("keyword_score", 0.70)
        k_source = item.get("keyword_source", "fallback")

        # Keyword deduplication: if already used, try a cinematic variant
        kw_lower = keyword.lower()
        if kw_lower in _used_keywords:
            variants = [
                keyword + " aerial view",
                keyword + " close up",
                keyword + " cinematic",
                "professional " + keyword,
            ]
            for v in variants:
                if v.lower() not in _used_keywords:
                    keyword = v
                    print(f"  [smart_broll] Keyword duplicada -> variante: '{keyword}'")
                    break

        _used_keywords.add(keyword.lower())

        result = _download_hierarchical(
            keyword=keyword,
            index=i + 1,
            output_folder=output_folder,
            pexels_api_key=pexels_api_key,
            pixabay_api_key=pixabay_api_key,
            keyword_score=k_score,
        )

        if result:
            mapped_clips.append({
                "timeline_start": item["start"],
                "timeline_end": item["end"],
                "file": result["file"],
                "keyword": keyword,
                "source": result["source"],
                "score": result["score"],
            })
            _log_clip(keyword, result["file"], result["source"], result["score"], item, k_source)
        else:
            print(f"  [smart_broll] Sem clip para '{keyword}' t={item['start']:.0f}s -- pulando")

    # ── Write B-roll log ──────────────────────────────────────────────────
    _write_log(output_folder, video_theme, total, mapped_clips)

    if on_progress:
        on_progress(100, 100, f"Concluido! {len(mapped_clips)}/{total} clips baixados.")

    try:
        os.remove(tmp_audio)
    except OSError:
        pass

    return mapped_clips


# ════════════════════════════════════════════════════════════════════════
#  GEMINI INTEGRATION
# ════════════════════════════════════════════════════════════════════════

def _gemini_call_broll(client, prompt: str, max_retries: int = 3) -> str:
    """Gemini call with 429 retry using exact API-provided wait time."""
    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            return (resp.text or "").strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                m = re.search(r"retry in (\d+(?:\.\d+)?)s", err)
                wait = float(m.group(1)) + 5 if m else 65
                print(f"  [smart_broll] Rate limit -- aguardando {wait:.0f}s (tentativa {attempt+1}/{max_retries})...")
                time.sleep(wait)
                continue
            raise
    return ""


def _analyze_with_gemini(chunks: list, api_key: str, on_progress=None, video_theme: str = "") -> list:
    """Extract per-chunk keywords using Gemini with contextual theme awareness."""
    try:
        from google import genai
    except ImportError:
        raise ImportError("Install google-genai: pip install google-genai")

    client = genai.Client(api_key=api_key)
    results = []

    theme_context = {
        "health_supplements": "HEALTH/SUPPLEMENTS video. 'oil','fish','iron','acid' = supplements/nutrients, NOT literal animals/metals/liquids. Search for: pills, capsules, labs, medical imagery.",
        "medical_science": "MEDICAL SCIENCE video. All terms = medicine/treatments/healthcare. Search for: hospitals, microscopes, lab coats, medical equipment.",
        "war_military": "WAR/MILITARY video. Focus on: battles, soldiers, weapons, military vehicles, historic warfare, conquest imagery.",
        "ancient_history": "ANCIENT HISTORY video. Focus on: ruins, temples, ancient civilizations, archaeological sites, historical artifacts, empires.",
        "nature_documentary": "NATURE/WILDLIFE video. Focus on: landscapes, animals in habitat, ecosystems, oceans, forests, aerial nature shots.",
        "space_science": "SPACE/ASTRONOMY video. Focus on: planets, galaxies, telescopes, rockets, astronauts, cosmic phenomena.",
        "technology": "TECHNOLOGY video. Focus on: circuits, robots, AI visualizations, data centers, futuristic tech, screens.",
        "economy_finance": "ECONOMICS/FINANCE video. Focus on: stock markets, currencies, business meetings, graphs, city skylines.",
        "food_nutrition": "FOOD/NUTRITION video. Focus on: ingredients, cooking, fresh produce, kitchen scenes, restaurant imagery.",
        "construction_engineering": "CONSTRUCTION/ENGINEERING video. Focus on: buildings, cranes, workers, blueprints, infrastructure, machinery.",
        "ocean_marine": "OCEAN/MARINE video. Focus on: underwater scenes, coral reefs, ships, marine life, deep sea, waves.",
        "crime_investigation": "CRIME/INVESTIGATION video. Focus on: forensics, police, courtrooms, evidence, crime scenes, investigations.",
        "psychology_mind": "PSYCHOLOGY/MIND video. Focus on: brain scans, therapy, human behavior, emotions, mental health imagery.",
        "geography_places": "GEOGRAPHY/PLACES video. Focus on: landscapes, cities, maps, aerial views, cultural sites, remote locations.",
        "religion_spiritual": "RELIGION/SPIRITUALITY video. Focus on: temples, churches, prayer, sacred texts, ceremonies, spiritual symbols.",
    }.get(video_theme, "GENERAL DOCUMENTARY. Analyze the narration meaning and find visually relevant stock footage.")

    for i, chunk in enumerate(chunks):
        if on_progress:
            pct = 20 + int((i / max(len(chunks), 1)) * 30)
            on_progress(pct, 100, f"IA analisando chunk {i+1}/{len(chunks)}...")

        text = chunk["text"].strip()[:500]

        prompt = (
            f"You are the world's best stock footage researcher. You NEVER make mistakes.\n\n"
            f"GLOBAL VIDEO CONTEXT: {theme_context}\n\n"
            f"NARRATION (this specific 9-second segment):\n\"{text}\"\n\n"
            f"YOUR TASK: Suggest exactly 2 stock footage search terms.\n\n"
            f"REASONING PROCESS (follow these 3 steps):\n"
            f"1. UNDERSTAND: What is this segment ACTUALLY about? What concept is being discussed?\n"
            f"2. VISUALIZE: What would a professional editor show on screen during this narration?\n"
            f"3. SEARCH: What search term finds EXACTLY that visual on Pexels/Pixabay?\n\n"
            f"CRITICAL RULES:\n"
            f"- NEVER search literal words. Search the VISUAL MEANING.\n"
            f"- 'fish oil' in health = 'omega supplement capsules' NOT 'fish swimming'\n"
            f"- 'iron deficiency' in health = 'blood test laboratory' NOT 'iron metal'\n"
            f"- 'market crash' in finance = 'stock market red graph' NOT 'car crash'\n"
            f"- 'cell division' in science = 'microscope cells biology' NOT 'prison cell'\n"
            f"- Each term: 2-5 words, specific, visually searchable, English only\n"
            f"- Think: 'What would a Pexels/Pixabay search return for this term?'\n"
            f"- Prefer professional, cinematic, high-quality stock footage terms\n\n"
            f"Return ONLY 2 search terms, one per line. No numbering, no explanation."
        )

        try:
            response_text = _gemini_call_broll(client, prompt)
            if response_text:
                terms = [t.strip() for t in response_text.strip().split("\n") if t.strip()]
                for term in terms[:2]:
                    term = term.lstrip("0123456789.-) ").strip('"\'*+-~•–—')
                    if term and 2 < len(term) < 60:
                        results.append({
                            "start": chunk["start"],
                            "end": chunk["end"],
                            "keyword": term,
                            "keyword_source": "gemini",
                            "keyword_score": 0.85,
                        })
        except Exception as e:
            print(f"  [smart_broll] Gemini falhou no chunk {i+1}: {e}")
            fallback = _extract_smart_keywords([chunk], video_theme)
            results.extend(fallback)

        # Stay under 20 req/min: 1 req per 3.5s
        time.sleep(3.5)

    return results


# ════════════════════════════════════════════════════════════════════════
#  DOWNLOAD ENGINE
# ════════════════════════════════════════════════════════════════════════

def _download_hierarchical(
    keyword: str,
    index: int,
    output_folder: str,
    pexels_api_key: str,
    pixabay_api_key: str,
    keyword_score: float = 0.70,
) -> dict:
    """
    Hierarchical B-roll download with anti-repetition guard.

    Order:
        1. Pexels video   (best quality, royalty-free)
        2. Pixabay video  (good quality, royalty-free)
        3. Pexels image   (pipeline applies Ken Burns effect)
        4. Pixabay image  (last resort image)

    For each source, requests 3 candidates and picks first with unused URL.
    Low-confidence keywords (score < 0.5) exhaust all sources before giving up.

    Returns {"file": path, "source": str, "score": float} or None.
    """
    global _used_urls

    # 1 — Pexels video
    if pexels_api_key:
        result = _try_pexels_video(keyword, index, output_folder, pexels_api_key, keyword_score)
        if result:
            return result

    # 2 — Pixabay video
    if pixabay_api_key:
        result = _try_pixabay_video(keyword, index, output_folder, pixabay_api_key, keyword_score)
        if result:
            return result

    # 3 — Pexels image (Ken Burns applied by pipeline)
    if pexels_api_key:
        result = _try_pexels_image(keyword, index, output_folder, pexels_api_key, keyword_score)
        if result:
            return result

    # 4 — Pixabay image
    if pixabay_api_key:
        result = _try_pixabay_image(keyword, index, output_folder, pixabay_api_key, keyword_score)
        if result:
            return result

    return None


def _try_pexels_video(keyword, index, output_folder, api_key, k_score):
    global _used_urls
    try:
        from core.pexels_stock import search_videos, download_file
        vids = search_videos(api_key, keyword, count=3, min_duration=3) or []
        for vid in vids:
            url = vid.get("url", "")
            if not url or url in _used_urls:
                continue
            out = os.path.join(output_folder, f"broll_{index:04d}.mp4")
            try:
                download_file(url, out)
                if _validate_download(out, is_image=False):
                    _used_urls.add(url)
                    return {"file": out, "source": "pexels_video", "score": k_score}
                _safe_remove(out)
            except Exception:
                _safe_remove(out)
    except Exception as e:
        print(f"    [smart_broll] Pexels video erro: {e}")
    return None


def _try_pixabay_video(keyword, index, output_folder, api_key, k_score):
    global _used_urls
    try:
        from core.pixabay_stock import search_videos, download_file
        vids = search_videos(api_key, keyword, count=3) or []
        for vid in vids:
            url = vid.get("url", "")
            if not url or url in _used_urls:
                continue
            out = os.path.join(output_folder, f"broll_{index:04d}.mp4")
            try:
                download_file(url, out)
                if _validate_download(out, is_image=False):
                    _used_urls.add(url)
                    return {"file": out, "source": "pixabay_video", "score": k_score * 0.95}
                _safe_remove(out)
            except Exception:
                _safe_remove(out)
    except Exception as e:
        print(f"    [smart_broll] Pixabay video erro: {e}")
    return None


def _try_pexels_image(keyword, index, output_folder, api_key, k_score):
    global _used_urls
    try:
        from core.pexels_stock import search_photos, download_file
        pics = search_photos(api_key, keyword, count=3) or []
        for pic in pics:
            url = pic.get("url", "")
            if not url or url in _used_urls:
                continue
            out = os.path.join(output_folder, f"broll_{index:04d}.jpg")
            try:
                download_file(url, out)
                if _validate_download(out, is_image=True):
                    _used_urls.add(url)
                    return {"file": out, "source": "pexels_image", "score": k_score * 0.80}
                _safe_remove(out)
            except Exception:
                _safe_remove(out)
    except Exception as e:
        print(f"    [smart_broll] Pexels image erro: {e}")
    return None


def _try_pixabay_image(keyword, index, output_folder, api_key, k_score):
    global _used_urls
    try:
        from core.pixabay_stock import search_photos, download_file
        pics = search_photos(api_key, keyword, count=3) or []
        for pic in pics:
            url = pic.get("url", "")
            if not url or url in _used_urls:
                continue
            out = os.path.join(output_folder, f"broll_{index:04d}.jpg")
            try:
                download_file(url, out)
                if _validate_download(out, is_image=True):
                    _used_urls.add(url)
                    return {"file": out, "source": "pixabay_image", "score": k_score * 0.70}
                _safe_remove(out)
            except Exception:
                _safe_remove(out)
    except Exception as e:
        print(f"    [smart_broll] Pixabay image erro: {e}")
    return None


def _validate_download(path: str, is_image: bool = False) -> bool:
    """File must exist and meet minimum size threshold."""
    min_bytes = 10_000 if is_image else 100_000
    return os.path.isfile(path) and os.path.getsize(path) >= min_bytes


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _log_clip(keyword: str, file_path: str, source: str, score: float,
              chunk: dict, kw_source: str) -> None:
    global _broll_log
    entry = {
        "t_start": round(chunk["start"], 1),
        "t_end": round(chunk["end"], 1),
        "duration_s": round(chunk["end"] - chunk["start"], 1),
        "keyword": keyword,
        "keyword_source": kw_source,
        "file": os.path.basename(file_path),
        "media_source": source,
        "score": round(score, 3),
    }
    _broll_log.append(entry)
    print(f"    [B-roll t={entry['t_start']:.0f}s] {source} score={score:.2f} '{keyword}'")


def _write_log(output_folder: str, video_theme: str, total: int, mapped_clips: list) -> None:
    if not _broll_log:
        return
    log_path = os.path.join(output_folder, "_broll_log.json")
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump({
                "video_theme": video_theme,
                "requested": total,
                "downloaded": len(mapped_clips),
                "fill_rate_pct": round(len(mapped_clips) / max(total, 1) * 100, 1),
                "clips": _broll_log,
            }, f, ensure_ascii=False, indent=2)
        print(f"  [smart_broll] Log salvo: _broll_log.json ({len(_broll_log)} clips)")
    except Exception as e:
        print(f"  [smart_broll] Erro ao salvar log: {e}")


# ════════════════════════════════════════════════════════════════════════
#  THEME DETECTION
# ════════════════════════════════════════════════════════════════════════

def _detect_video_theme(full_text: str) -> str:
    """Analyze full transcription to detect the overall video theme."""
    text = full_text.lower()

    theme_scores = {
        "health_supplements": 0,
        "medical_science": 0,
        "war_military": 0,
        "ancient_history": 0,
        "nature_documentary": 0,
        "space_science": 0,
        "technology": 0,
        "economy_finance": 0,
        "food_nutrition": 0,
        "construction_engineering": 0,
        "ocean_marine": 0,
        "crime_investigation": 0,
        "psychology_mind": 0,
        "geography_places": 0,
        "religion_spiritual": 0,
    }

    for word in ["supplement", "vitamin", "omega", "fish oil", "capsule", "pill",
                 "dosage", "nutrient", "mineral", "health benefit", "antioxidant",
                 "probiotic", "herbal", "extract", "dietary"]:
        if word in text:
            theme_scores["health_supplements"] += 3

    for word in ["disease", "patient", "doctor", "hospital", "surgery", "diagnosis",
                 "treatment", "symptom", "clinical", "blood", "cell", "organ",
                 "cancer", "infection", "immune", "vaccine"]:
        if word in text:
            theme_scores["medical_science"] += 3

    for word in ["war", "battle", "army", "soldier", "weapon", "invasion",
                 "conquest", "military", "general", "troops", "siege",
                 "guerra", "batalla", "ejercito", "soldados"]:
        if word in text:
            theme_scores["war_military"] += 3

    for word in ["empire", "ancient", "civilization", "dynasty", "pharaoh",
                 "roman", "greek", "egyptian", "mongol", "medieval", "century",
                 "kingdom", "emperor", "khan", "silk road"]:
        if word in text:
            theme_scores["ancient_history"] += 3

    for word in ["ocean", "forest", "animal", "species", "ecosystem",
                 "wildlife", "climate", "volcano", "earthquake", "coral",
                 "biodiversity", "habitat", "migration"]:
        if word in text:
            theme_scores["nature_documentary"] += 3

    for word in ["planet", "star", "galaxy", "universe", "astronaut",
                 "nasa", "rocket", "orbit", "black hole", "nebula",
                 "solar", "cosmic", "telescope"]:
        if word in text:
            theme_scores["space_science"] += 3

    for word in ["computer", "software", "algorithm", "robot", "digital",
                 "artificial intelligence", "internet", "data", "silicon",
                 "processor", "innovation"]:
        if word in text:
            theme_scores["technology"] += 3

    for word in ["market", "economy", "inflation", "stock", "investment",
                 "gdp", "trade", "currency", "bank", "finance", "debt"]:
        if word in text:
            theme_scores["economy_finance"] += 3

    for word in ["food", "diet", "nutrition", "recipe", "cooking",
                 "ingredient", "meal", "protein", "calorie", "organic"]:
        if word in text:
            theme_scores["food_nutrition"] += 3

    for word in ["construction", "building", "architecture", "crane", "cement",
                 "concrete", "bridge", "infrastructure", "engineer", "blueprint",
                 "skyscraper", "foundation", "structure", "steel", "tower"]:
        if word in text:
            theme_scores["construction_engineering"] += 3

    for word in ["ocean", "sea", "underwater", "marine", "coral", "reef",
                 "shipwreck", "submarine", "deep sea", "whale", "shark",
                 "tide", "wave", "fishing", "diver", "abyss"]:
        if word in text:
            theme_scores["ocean_marine"] += 3

    for word in ["crime", "murder", "investigation", "detective", "forensic",
                 "police", "criminal", "evidence", "court", "prison",
                 "suspect", "trial", "heist", "robbery", "serial"]:
        if word in text:
            theme_scores["crime_investigation"] += 3

    for word in ["psychology", "brain", "mental", "behavior", "consciousness",
                 "therapy", "anxiety", "depression", "emotion", "cognitive",
                 "subconscious", "trauma", "personality", "intelligence"]:
        if word in text:
            theme_scores["psychology_mind"] += 3

    for word in ["country", "city", "mountain", "desert", "island",
                 "continent", "border", "territory", "population", "capital",
                 "landscape", "valley", "canyon", "river", "coast"]:
        if word in text:
            theme_scores["geography_places"] += 3

    for word in ["religion", "god", "church", "temple", "prayer",
                 "bible", "quran", "spiritual", "faith", "prophet",
                 "sacred", "ritual", "monastery", "divine", "worship"]:
        if word in text:
            theme_scores["religion_spiritual"] += 3

    best_theme = max(theme_scores, key=theme_scores.get)
    if theme_scores[best_theme] == 0:
        return "general_documentary"
    return best_theme


# ════════════════════════════════════════════════════════════════════════
#  CHUNK GROUPING
# ════════════════════════════════════════════════════════════════════════

def _group_segments(whisper_segments: list, chunk_duration: float, total_duration: float) -> list:
    """Group Whisper word-level segments into larger time chunks."""
    chunks = []
    current_chunk = {"start": 0, "end": 0, "text": ""}

    for seg in whisper_segments:
        if seg["start"] - current_chunk["start"] >= chunk_duration and current_chunk["text"]:
            current_chunk["end"] = seg["start"]
            chunks.append(dict(current_chunk))
            current_chunk = {"start": seg["start"], "end": seg["end"], "text": ""}

        current_chunk["text"] += " " + seg["text"]
        current_chunk["end"] = seg["end"]

    if current_chunk["text"].strip():
        current_chunk["end"] = total_duration
        chunks.append(current_chunk)

    return chunks


# ════════════════════════════════════════════════════════════════════════
#  KEYWORD EXTRACTION (FALLBACK — NO AI)
# ════════════════════════════════════════════════════════════════════════

_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "need", "dare",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "and", "but", "or", "nor", "not", "so", "yet", "both",
    "either", "neither", "each", "every", "all", "any", "few", "more",
    "most", "other", "some", "such", "no", "only", "own", "same", "than",
    "too", "very", "just", "also", "now", "here", "there", "when", "where",
    "why", "how", "what", "which", "who", "whom", "this", "that", "these",
    "those", "i", "you", "he", "she", "it", "we", "they", "me", "him",
    "her", "us", "them", "my", "your", "his", "its", "our", "their",
    "about", "up", "down", "if", "because", "while", "until", "although",
    "even", "though", "since", "unless", "like", "well", "really", "many",
    "much", "one", "two", "three", "four", "five", "first", "second",
    "new", "old", "great", "good", "bad", "long", "right", "left",
    "make", "made", "get", "got", "know", "think", "say", "said",
    "take", "come", "go", "see", "look", "find", "give", "tell",
    "thing", "things", "way", "time", "people", "world", "something",
}


def _extract_smart_keywords(chunks: list, video_theme: str = "") -> list:
    """
    Context-aware keyword extraction using detected video theme.
    Assigns confidence scores based on extraction method.
    """
    _THEME_OVERRIDES = {
        "health_supplements": {
            "oil": "supplement oil capsule bottle",
            "fish": "omega 3 supplement capsule",
            "acid": "supplement capsule pharmaceutical",
            "iron": "blood test medical laboratory",
            "zinc": "mineral supplement tablet",
            "extract": "herbal medicine natural supplement",
            "root": "herbal medicine root extract",
            "leaf": "herbal tea natural remedy",
            "berry": "antioxidant fresh berries superfood",
            "seed": "healthy seeds nutrition food",
            "powder": "supplement powder scoop fitness",
            "pill": "medicine pills pharmaceutical",
            "dose": "medical dosage pharmaceutical",
            "liver": "liver health medical organ",
            "gut": "digestive system healthy gut",
            "bone": "bone health skeleton medical",
            "skin": "skincare beauty healthy skin",
            "hair": "hair health treatment beauty",
            "eye": "eye health vision medical",
            "brain": "brain health neuroscience scan",
            "heart": "heart health cardiovascular medical",
            "muscle": "fitness muscle exercise gym",
            "joint": "joint health physiotherapy medical",
            "blood": "blood test medical laboratory",
            "cell": "human cells microscope medical",
        },
        "medical_science": {
            "heart": "human heart cardiology medical",
            "cell": "human cells microscope laboratory",
            "blood": "blood cells microscope medical",
            "iron": "blood test iron deficiency medical",
        },
        "war_military": {
            "iron": "iron weapons medieval forge",
            "fire": "fire battlefield warfare",
            "blood": "battlefield dramatic war",
        },
        "ancient_history": {
            "gold": "gold treasure ancient coins",
            "iron": "iron age historical tools",
            "fire": "ancient fire torch ceremony",
        },
    }

    overrides = _THEME_OVERRIDES.get(video_theme, {})
    results = _extract_simple_keywords(chunks)

    if overrides:
        for r in results:
            kw_lower = r["keyword"].lower()
            for word, replacement in overrides.items():
                kw_words = kw_lower.split()
                if word in kw_words and len(kw_words) <= 2:
                    r["keyword"] = replacement
                    r["keyword_source"] = "theme_override"
                    r["keyword_score"] = 0.72
                    break

    return results


def _extract_simple_keywords(chunks: list) -> list:
    """
    Smart contextual keyword extraction.

    Phase 1: Multi-word phrase matching (score 0.80) — highest priority
    Phase 2: Single-word concept mapping (score 0.70)
    Phase 3: Frequency-based fallback (score 0.40) — lowest confidence
    """
    _PHRASE_MAP = {
        # Health / Supplements / Medicine
        "fish oil": "omega 3 supplement capsules",
        "cod liver": "vitamin supplement pills",
        "vitamin d": "sunlight vitamin supplement",
        "vitamin c": "citrus fruit orange vitamin",
        "omega 3": "omega supplement pills yellow",
        "blood pressure": "blood pressure monitor medical",
        "heart disease": "human heart cardiology medical",
        "heart attack": "emergency hospital cardiac",
        "blood sugar": "diabetes glucose meter medical",
        "weight loss": "fitness healthy body exercise",
        "immune system": "immune cells microscope medical",
        "mental health": "meditation mindfulness calm",
        "brain health": "brain neuroscience scan medical",
        "side effects": "medicine pills pharmaceutical",
        "clinical trial": "laboratory research scientist",
        "health benefits": "healthy lifestyle wellness",
        "anti inflammatory": "medicine capsules pharmaceutical",
        "high cholesterol": "blood test laboratory medical",
        "fatty acids": "supplement capsule yellow oil",
        "dietary supplement": "supplement bottle pills pharmacy",
        "blood cells": "blood cells microscope medical",
        "nervous system": "brain neurons microscope",
        "digestive system": "stomach anatomy medical",
        "muscle pain": "physical therapy rehabilitation",
        "joint pain": "knee joint medical xray",
        "skin care": "skincare beauty dermatology",
        "green tea": "green tea cup ceremony",
        "olive oil": "olive oil bottle mediterranean",
        "coconut oil": "coconut tropical natural",
        "essential oils": "aromatherapy essential oils bottles",
        # War / Military / History
        "world war": "world war soldiers battlefield",
        "civil war": "civil war historical battle",
        "cold war": "cold war nuclear missile",
        "ancient rome": "roman colosseum architecture",
        "ancient greece": "greek parthenon temple",
        "ancient egypt": "egyptian pyramids sphinx",
        "middle ages": "medieval castle knights",
        "silk road": "silk road caravan desert trade",
        "genghis khan": "mongol warrior horseback steppe",
        "roman empire": "roman legions soldiers formation",
        "ottoman empire": "ottoman palace istanbul",
        "british empire": "british colonial victorian",
        "mongol empire": "mongol army horseback steppe",
        # Science / Space / Technology
        "black hole": "black hole space nebula",
        "solar system": "planets solar system space",
        "climate change": "climate change glacier melting",
        "global warming": "earth temperature atmosphere",
        "artificial intelligence": "AI robot technology futuristic",
        "machine learning": "data visualization technology",
        "deep sea": "deep ocean underwater dark",
        "outer space": "space astronaut nebula stars",
        # Nature / Geography
        "rain forest": "tropical rainforest aerial green",
        "coral reef": "coral reef underwater tropical",
        "ice age": "glacier ice landscape frozen",
        "volcanic eruption": "volcano eruption lava fire",
        "natural disaster": "storm hurricane disaster",
        "wild animal": "wildlife safari african animal",
        # Economy / Society
        "stock market": "stock market trading finance",
        "real estate": "modern architecture building city",
        "social media": "smartphone social media apps",
    }

    _VISUAL_CONCEPTS = {
        # Health
        "supplement": "supplement capsules pills bottle",
        "vitamin": "vitamin supplement colorful pills",
        "protein": "protein supplement fitness gym",
        "calcium": "milk calcium healthy bones",
        "antioxidant": "fresh berries fruits colorful",
        "inflammation": "medical treatment laboratory",
        "cholesterol": "blood test medical laboratory",
        "hormone": "medical science laboratory",
        "metabolism": "fitness exercise healthy body",
        "nutrient": "fresh vegetables healthy food",
        "probiotic": "yogurt healthy gut food",
        "collagen": "skincare beauty youthful",
        "turmeric": "turmeric spice golden powder",
        "ginger": "ginger root herbal medicine",
        # War/Military (Spanish)
        "batalla": "medieval battle swords",
        "guerra": "war battlefield soldiers",
        "ejercito": "army soldiers formation",
        "soldados": "soldiers marching military",
        "espada": "sword medieval combat",
        "caballo": "horse cavalry medieval",
        "imperio": "ancient empire palace",
        "conquista": "military conquest invasion",
        "mongol": "mongol warrior horseback steppe",
        "khan": "mongol emperor throne",
        # Geography (Spanish)
        "montana": "mountain landscape aerial",
        "desierto": "desert sand dunes",
        "oceano": "ocean waves aerial",
        "bosque": "forest aerial green",
        "estepa": "steppe grassland horses",
        "ciudad": "ancient city ruins",
        # English — War
        "battle": "medieval battle combat cinematic",
        "warrior": "warrior combat ancient",
        "army": "army soldiers formation",
        "conquest": "military conquest invasion",
        "empire": "ancient empire ruins palace",
        "invasion": "army invasion battle",
        "sword": "sword combat medieval",
        "horse": "horse cavalry riding",
        "castle": "medieval castle fortress",
        "village": "village countryside rural",
        "palace": "palace interior golden",
        "throne": "emperor king throne",
        # English — Nature
        "desert": "desert landscape dunes",
        "mountain": "mountain landscape snow aerial",
        "ocean": "ocean waves underwater cinematic",
        "forest": "forest aerial canopy green",
        "river": "river valley landscape",
        "volcano": "volcano eruption lava fire",
        "storm": "storm lightning dramatic",
        "sunrise": "sunrise golden landscape cinematic",
        "sunset": "sunset dramatic clouds orange",
        "waterfall": "waterfall tropical lush",
        "glacier": "glacier ice blue arctic",
        "cave": "cave underground dark",
        # English — Generic
        "gold": "gold treasure coins shining",
        "fire": "fire flames cinematic dramatic",
        "night": "night sky stars city lights",
        "rain": "rain drops storm window",
        "snow": "snow winter landscape peaceful",
        "bridge": "bridge architecture skyline",
        "ship": "ship sailing ocean horizon",
        "trade": "marketplace trade ancient",
        "market": "ancient marketplace bazaar",
        "king": "medieval king crown",
        "temple": "ancient temple architecture",
        "pyramid": "egyptian pyramid desert",
        "science": "science laboratory research",
        "technology": "technology digital futuristic",
        "medicine": "medicine hospital medical",
        "doctor": "doctor medical professional",
        "food": "fresh food ingredients colorful",
        "water": "water droplets clean pure",
    }

    results = []
    for chunk in chunks:
        text = chunk["text"].strip().lower()
        words = [w.strip(".,!?;:\"'()-[]") for w in text.split()]
        text_clean = " ".join(words)

        # Phase 1: multi-word phrase (highest confidence)
        found = False
        for phrase, search_term in _PHRASE_MAP.items():
            if phrase in text_clean:
                results.append({
                    "start": chunk["start"],
                    "end": chunk["end"],
                    "keyword": search_term,
                    "keyword_source": "phrase_map",
                    "keyword_score": 0.80,
                })
                found = True
                break

        if found:
            continue

        # Phase 2: single-word visual concept
        for word in words:
            if word in _VISUAL_CONCEPTS:
                results.append({
                    "start": chunk["start"],
                    "end": chunk["end"],
                    "keyword": _VISUAL_CONCEPTS[word],
                    "keyword_source": "visual_concepts",
                    "keyword_score": 0.70,
                })
                found = True
                break

        if found:
            continue

        # Phase 3: frequency fallback (lowest confidence — score < 0.5 triggers retry)
        meaningful = [w for w in words if len(w) > 4 and w not in _STOP_WORDS]
        if not meaningful:
            meaningful = [w for w in words if len(w) > 3 and w not in _STOP_WORDS]
        if not meaningful:
            continue

        freq = {}
        for w in meaningful:
            freq[w] = freq.get(w, 0) + 1

        top_words = sorted(freq, key=freq.get, reverse=True)[:2]

        if len(top_words) >= 2:
            keyword = f"{top_words[0]} {top_words[1]}"
        elif top_words:
            keyword = top_words[0]
        else:
            continue

        results.append({
            "start": chunk["start"],
            "end": chunk["end"],
            "keyword": keyword,
            "keyword_source": "frequency_fallback",
            "keyword_score": 0.40,
        })

    return results
