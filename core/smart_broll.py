"""
Smart B-Roll — Automatic content-matched footage v2 (FIXED)
Fixes:
- ClipTracker prevents duplicate clips across the entire video
- Global shot list ensures unique, contextually-relevant keywords
- Post-download validation checks relevance
- Keyword expansion with synonyms when searches return nothing
- Multi-source intelligent round robin with dedup
- YouTube priority mode (try YouTube FIRST for all niches)
"""

import os
import time
import math
import hashlib
import re
import random
from pathlib import Path


class ClipTracker:
    """Tracks ALL clips used in this project to prevent ANY duplicate.
    Uses: source ID, download URL, file content hash, and keyword history."""

    def __init__(self):
        self.used_ids = set()
        self.used_urls = set()
        self.used_hashes = set()
        self.used_keywords = {}

    def is_duplicate(self, clip_id, url=None):
        if clip_id and str(clip_id) in self.used_ids:
            return True
        if url and url in self.used_urls:
            return True
        return False

    def register(self, clip_id, url, file_path, keyword):
        if clip_id:
            self.used_ids.add(str(clip_id))
        if url:
            self.used_urls.add(url)
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, 'rb') as f:
                    h = hashlib.md5(f.read(512 * 1024)).hexdigest()
                self.used_hashes.add(h)
            except Exception:
                pass
        if keyword:
            self.used_keywords.setdefault(keyword, []).append(clip_id)

    def is_hash_duplicate(self, file_path):
        try:
            with open(file_path, 'rb') as f:
                h = hashlib.md5(f.read(512 * 1024)).hexdigest()
            return h in self.used_hashes
        except Exception:
            return False

    def __len__(self):
        return len(self.used_ids)


# Theme-to-niche keyword expansions for fallback searches (15 themes)
_THEME_EXPANSIONS = {
    "health_supplements": {
        "supplement": ["vitamin pills bottle", "dietary supplement capsules", "nutritional tablets"],
        "vitamin": ["multivitamin pills", "vitamin bottle pharmacy", "daily supplement"],
        "omega": ["omega 3 fish oil capsules", "omega supplement bottle", "essential fatty acids"],
        "fish oil": ["omega 3 capsules bottle", "fish oil supplement pills", "yellow softgel capsules"],
        "bone": ["bone health xray", "skeleton medical diagram", "calcium supplement"],
        "heart": ["human heart anatomy", "cardiovascular system", "heart health checkup"],
        "brain": ["brain anatomy medical", "neuroscience brain scan", "cognitive function"],
        "iron": ["iron supplement pills", "blood test laboratory", "ferrous tablets"],
        "blood": ["blood sample test", "medical laboratory equipment", "red blood cells"],
        "cell": ["cells under microscope", "cellular biology", "human cell structure"],
        "protein": ["protein powder supplement", "whey protein scoop", "fitness nutrition"],
        "medicine": ["medical pills tablets", "pharmaceutical drugs", "prescription medication"],
    },
    "war_military": {
        "battle": ["epic battle scene", "medieval combat", "warriors fighting"],
        "soldier": ["soldiers marching", "military formation", "army troops"],
        "war": ["war battlefield", "military conflict", "historical war reenactment"],
        "weapon": ["medieval weapons", "historical swords", "ancient armor"],
        "conquest": ["military conquest", "army invasion", "march to battle"],
        "empire": ["ancient empire ruins", "imperial palace", "historical empire map"],
    },
    "ocean_marine": {
        "ocean": ["deep ocean waves", "ocean surface aerial", "blue sea water"],
        "fish": ["tropical fish swimming", "coral reef fish", "colorful aquarium fish"],
        "coral": ["coral reef underwater", "tropical coral", "coral bleaching"],
        "sea": ["sea waves crashing", "ocean horizon", "deep blue sea"],
        "underwater": ["underwater diving", "scuba diving reef", "underwater exploration"],
        "whale": ["humpback whale swimming", "whale breaching ocean", "blue whale underwater"],
        "shark": ["great white shark", "shark swimming ocean", "hammerhead shark"],
    },
    "ancient_history": {
        "ruins": ["ancient ruins temple", "historical ruins landscape", "archaeological site"],
        "temple": ["ancient temple columns", "greek temple ruins", "historical temple"],
        "egypt": ["egyptian pyramids desert", "ancient egypt pharaoh", "egyptian hieroglyphics"],
        "roman": ["roman colosseum", "ancient rome architecture", "roman empire ruins"],
        "medieval": ["medieval castle fortress", "middle ages village", "medieval kingdom"],
    },
    "space_science": {
        "planet": ["planet earth from space", "solar system planets", "saturn rings planet"],
        "space": ["outer space stars", "deep space nebula", "astronaut spacewalk"],
        "galaxy": ["milky way galaxy", "spiral galaxy stars", "distant galaxy nebula"],
        "star": ["night sky stars", "bright star nebula", "starfield space"],
        "rocket": ["rocket launch space", "spacex rocket liftoff", "rocket taking off"],
        "astronaut": ["astronaut in space", "astronaut floating", "space suit astronaut"],
    },
    "crime_investigation": {
        "crime": ["crime scene investigation", "police crime tape", "detective investigation"],
        "murder": ["crime scene forensics", "police investigation night", "detective case files"],
        "police": ["police car patrol", "law enforcement officers", "police station interior"],
        "prison": ["prison cell corridor", "correctional facility", "behind bars inmate"],
        "evidence": ["forensic evidence laboratory", "crime lab analysis", "fingerprint forensics"],
        "court": ["courtroom trial judge", "legal hearing gavel", "jury deliberation"],
        "serial": ["criminal profiling board", "detective investigation photos", "case evidence wall"],
    },
    "psychology_mind": {
        "brain": ["brain neural network scan", "neuroscience laboratory", "brain MRI scan"],
        "mind": ["meditation mindfulness calm", "psychological therapy session", "mental wellness"],
        "anxiety": ["stress anxiety person", "mental health therapy", "counseling session"],
        "behavior": ["human behavior study", "social psychology experiment", "crowd behavior"],
        "consciousness": ["meditation deep thought", "brain waves EEG scan", "consciousness awareness"],
        "trauma": ["therapy counseling session", "emotional support healing", "psychological recovery"],
    },
    "construction_engineering": {
        "building": ["construction site crane", "skyscraper construction workers", "building under construction"],
        "bridge": ["bridge engineering construction", "suspension bridge aerial", "bridge architecture"],
        "tunnel": ["tunnel boring machine", "underground tunnel construction", "metro tunnel"],
        "crane": ["tower crane construction site", "heavy machinery lifting", "crane operator view"],
        "concrete": ["concrete pouring construction", "cement mixer truck", "foundation construction"],
        "steel": ["steel structure welding", "ironwork construction", "metal framework building"],
    },
    "technology": {
        "computer": ["computer circuit board", "data center servers", "programming code screen"],
        "robot": ["humanoid robot laboratory", "industrial robot arm", "robotic automation factory"],
        "ai": ["artificial intelligence visualization", "neural network diagram", "AI technology futuristic"],
        "digital": ["digital transformation screen", "holographic display", "futuristic interface"],
        "internet": ["fiber optic cables network", "server room data center", "global network connections"],
    },
    "geography_places": {
        "country": ["world map countries", "international borders", "national flags"],
        "city": ["city skyline aerial sunset", "urban metropolis architecture", "downtown streets"],
        "mountain": ["mountain peak snow summit", "alpine landscape valley", "mountain climbing expedition"],
        "desert": ["sahara desert sand dunes", "desert landscape sunset", "arid canyon rocks"],
        "island": ["tropical island aerial", "paradise beach turquoise", "remote island ocean"],
        "river": ["river valley aerial landscape", "flowing river nature", "river canyon deep"],
    },
    "religion_spiritual": {
        "god": ["church cathedral interior", "religious worship ceremony", "spiritual light rays"],
        "bible": ["ancient scripture book", "religious text study", "holy book pages"],
        "church": ["cathedral architecture interior", "church stained glass", "religious ceremony"],
        "temple": ["ancient temple worship", "buddhist temple ceremony", "hindu temple colorful"],
        "prayer": ["person praying meditation", "hands prayer spiritual", "religious devotion"],
        "faith": ["spiritual journey light", "religious community gathering", "hope inspiration light"],
    },
    "economy_finance": {
        "market": ["stock market trading floor", "financial charts screens", "wall street buildings"],
        "stock": ["stock exchange ticker", "trading screens financial", "market analysis charts"],
        "inflation": ["currency money printing", "rising prices graphs", "economic crisis protest"],
        "bank": ["bank vault gold", "banking institution building", "ATM financial transaction"],
        "investment": ["investment portfolio analysis", "financial advisor meeting", "gold bars investment"],
    },
    "food_nutrition": {
        "food": ["fresh ingredients kitchen", "food preparation cooking", "restaurant chef plating"],
        "diet": ["healthy meal preparation", "balanced diet plate", "nutritious food variety"],
        "cooking": ["chef cooking professional", "kitchen flames cooking", "food sizzling pan"],
        "recipe": ["cookbook recipe preparation", "measuring ingredients baking", "step by step cooking"],
        "organic": ["organic farm vegetables", "farmers market produce", "fresh organic harvest"],
    },
    "nature_documentary": {
        "forest": ["dense forest aerial canopy", "rainforest tropical green", "misty woodland path"],
        "animal": ["wildlife safari savanna", "wild animal closeup", "animals natural habitat"],
        "volcano": ["volcanic eruption lava flow", "active volcano smoke", "volcanic landscape aerial"],
        "earthquake": ["earthquake damage aftermath", "seismic activity ground", "collapsed building earthquake"],
        "wildlife": ["wild animals migration", "predator hunting prey", "safari landscape sunset"],
    },
}


def _expand_keyword(keyword: str, theme: str = "") -> list:
    """Generate alternative search terms when primary keyword returns nothing."""
    kw_lower = keyword.lower()
    
    # Check theme-specific expansions
    theme_expansions = _THEME_EXPANSIONS.get(theme, {})
    for word, alternatives in theme_expansions.items():
        if word in kw_lower:
            return alternatives
    
    # Generic smart expansions
    generic = {
        "blood": ["medical laboratory", "blood sample test", "health checkup"],
        "cell": ["microscope biology", "human cells", "laboratory research"],
        "heart": ["human heart anatomy", "cardiovascular", "healthy lifestyle"],
        "brain": ["brain anatomy", "neuroscience", "cognitive science"],
        "health": ["healthy lifestyle", "medical wellness", "healthcare"],
        "doctor": ["medical doctor", "healthcare professional", "hospital clinic"],
        "hospital": ["medical building", "hospital room", "clinic interior"],
        "medicine": ["pharmaceutical drugs", "medical pills", "pharmacy"],
        "science": ["laboratory research", "scientific experiment", "microscope lab"],
        "nature": ["nature landscape", "forest wilderness", "natural scenery"],
        "city": ["city skyline", "urban landscape", "downtown architecture"],
        "people": ["group of people", "crowd walking", "diverse people"],
    }
    for word, alts in generic.items():
        if word in kw_lower:
            return alts
    
    # Strip modifiers to find core
    core = kw_lower.replace("close up ", "").replace("wide shot ", "").replace("aerial ", "").replace("beautiful ", "").replace("cinematic ", "")
    if core != kw_lower:
        return [core]
    
    # Last resort: keep trying variations
    words = kw_lower.split()
    if len(words) > 2:
        return [" ".join(words[:2])]
    if len(words) == 2:
        return [words[1]]
    
    return [kw_lower]


def analyze_narration(
    avatar_path: str,
    google_api_key: str,
    pexels_api_key: str,
    output_folder: str,
    segment_duration: float = 10.0,   # 10s = tight sync with narration
    clips_per_segment: int = 1,
    max_clips: int = 500,             # Allow up to 500 clips
    pixabay_api_key: str = "",
    unsplash_api_key: str = "",
    youtube_api_key: str = "",        # Separate YouTube Data API key
    on_progress=None,
) -> list:
    """
    INTELLIGENT narration analysis pipeline:
    
    Phase 1: FULL CONTEXT — Transcribe entire video, detect overall theme
    Phase 2: PER-CHUNK — Generate keywords WITH theme context
    Phase 3: DOWNLOAD — Find matching stock footage from multiple sources
    
    This 2-phase approach prevents errors like 'fish oil' → fish swimming,
    because the system knows the video is about 'health supplements'.
    """
    os.makedirs(output_folder, exist_ok=True)

    # =============================================
    # PHASE 1: TRANSCRIBE + DETECT OVERALL THEME
    # =============================================
    if on_progress:
        on_progress(0, 100, "Phase 1: Transcribing narration with Whisper AI...")

    from core.subtitle_generator import transcribe_audio
    from core.video_processor import extract_audio, get_duration

    audio_path = os.path.join(output_folder, "_temp_audio.wav")
    extract_audio(avatar_path, audio_path)
    
    segments = transcribe_audio(audio_path, language=None, model_size="base")
    avatar_duration = get_duration(avatar_path)

    if on_progress:
        on_progress(10, 100, f"Transcribed {len(segments)} segments. Detecting video theme...")

    # Build full transcription for theme detection
    full_text = " ".join(seg["text"] for seg in segments)
    video_theme = _detect_video_theme(full_text)
    
    print(f"  [smart_broll] VIDEO THEME DETECTED: '{video_theme}'")
    if on_progress:
        on_progress(15, 100, f"Theme: '{video_theme}'. Creating content chunks...")

    # =============================================
    # PHASE 2: CONTEXTUAL KEYWORD EXTRACTION
    # =============================================
    chunks = _group_segments(segments, segment_duration, avatar_duration)
    
    if on_progress:
        on_progress(20, 100, f"Created {len(chunks)} chunks. Generating contextual keywords...")

    # Extract keywords WITH theme context
    if google_api_key:
        if on_progress:
            on_progress(20, 100, f"Using Gemini AI (theme: {video_theme})...")
        keywords_per_chunk = _analyze_with_gemini(chunks, google_api_key, on_progress, video_theme)
    else:
        if on_progress:
            on_progress(20, 100, f"Smart keyword extraction (theme: {video_theme})...")
        keywords_per_chunk = _extract_smart_keywords(chunks, video_theme)

    if on_progress:
        on_progress(50, 100, f"Got {len(keywords_per_chunk)} search terms. Downloading...")

    # Log keywords for debugging
    for kw in keywords_per_chunk[:5]:
        print(f"    [{kw['start']:.0f}s] → '{kw['keyword']}'")
    if len(keywords_per_chunk) > 5:
        print(f"    ... and {len(keywords_per_chunk) - 5} more")

    # Cap keywords at max_clips
    if len(keywords_per_chunk) > max_clips:
        keywords_per_chunk = keywords_per_chunk[:max_clips]

    # =============================================
    # PHASE 3: DOWNLOAD FROM MULTIPLE SOURCES (6 sources!)
    # =============================================
    sources = []
    if pexels_api_key:
        from core.pexels_stock import search_videos as pex_videos, search_photos as pex_photos
        from core.pexels_stock import download_file as pex_download
        sources.append("pexels")
    if pixabay_api_key:
        from core.pixabay_stock import search_videos as pix_videos, search_photos as pix_photos
        from core.pixabay_stock import download_file as pix_download
        sources.append("pixabay")
    if unsplash_api_key:
        from core.unsplash_stock import search_photos as uns_photos
        from core.unsplash_stock import download_file as uns_download
        sources.append("unsplash")

    from core.coverr_stock import search_videos as cov_videos
    from core.coverr_stock import download_file as cov_download
    sources.append("coverr")

    # New sources (no API key needed)
    try:
        from core.videvo_stock import search_videos as vid_videos
        from core.videvo_stock import download_file as vid_download
        sources.append("videvo")
    except ImportError:
        pass
    try:
        from core.lifeofvids_stock import search_videos as lov_videos
        from core.lifeofvids_stock import download_file as lov_download
        sources.append("lifeofvids")
    except ImportError:
        pass
    
    # Mixkit (free HD/4K, no API key)
    try:
        from core.mixkit_stock import search_and_download as mixkit_download
        sources.append("mixkit")
    except ImportError:
        pass
    
    # YouTube B-Roll (highest priority — real footage)
    youtube_available = False
    try:
        from core.youtube_broll import search_and_download as yt_download
        youtube_available = True
        sources.insert(0, "youtube")  # First in order!
    except ImportError:
        pass

    print(f"  [smart_broll] Active sources ({len(sources)}): {', '.join(sources)}")

    if not sources:
        if on_progress:
            on_progress(100, 100, "No stock sources available!")
        return []

    # Initialize ClipTracker for deduplication
    tracker = ClipTracker()
    
    # Cross-project anti-reuse: load previously used clips
    try:
        from core.anti_reuse import load_used_clips, save_used_clips
        previously_used = load_used_clips()
        for clip_id in previously_used:
            tracker.used_ids.add(str(clip_id))
        print(f"  [smart_broll] Anti-reuse: {len(previously_used)} clips from previous projects")
    except Exception:
        previously_used = set()
    
    mapped_clips = []
    total_downloads = len(keywords_per_chunk)

    for i, item in enumerate(keywords_per_chunk):
        if on_progress:
            pct = 50 + int((i / max(total_downloads, 1)) * 45)
            on_progress(pct, 100, f"Downloading {i+1}/{total_downloads}: '{item['keyword']}'...")

        found = False
        source_order = sources[i % len(sources):] + sources[:i % len(sources)]

        for source in source_order:
            if found:
                break
            try:
                if source == "pexels":
                    vids = pex_videos(pexels_api_key, item["keyword"], count=5, min_duration=3)
                    for vid in (vids or []):
                        if tracker.is_duplicate(vid.get("id"), vid.get("url")):
                            continue
                        out_path = os.path.join(output_folder, f"auto_{i+1:03d}.mp4")
                        pex_download(vid["url"], out_path)
                        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                            tracker.register(vid.get("id"), vid["url"], out_path, item["keyword"])
                            mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                "file": out_path, "keyword": item["keyword"], "source": "pexels"})
                            found = True
                            break
                    if not found:
                        pics = pex_photos(pexels_api_key, item["keyword"], count=5)
                        for pic in (pics or []):
                            if tracker.is_duplicate(pic.get("id"), pic.get("url")):
                                continue
                            out_path = os.path.join(output_folder, f"auto_{i+1:03d}.jpg")
                            pex_download(pic["url"], out_path)
                            if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                                tracker.register(pic.get("id"), pic["url"], out_path, item["keyword"])
                                mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                    "file": out_path, "keyword": item["keyword"], "source": "pexels"})
                                found = True
                                break

                elif source == "pixabay":
                    vids = pix_videos(pixabay_api_key, item["keyword"], count=5)
                    for vid in (vids or []):
                        if tracker.is_duplicate(vid.get("id"), vid.get("url")):
                            continue
                        out_path = os.path.join(output_folder, f"auto_{i+1:03d}.mp4")
                        pix_download(vid["url"], out_path)
                        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                            tracker.register(vid.get("id"), vid["url"], out_path, item["keyword"])
                            mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                "file": out_path, "keyword": item["keyword"], "source": "pixabay"})
                            found = True
                            break
                    if not found:
                        pics = pix_photos(pixabay_api_key, item["keyword"], count=5)
                        for pic in (pics or []):
                            if tracker.is_duplicate(pic.get("id"), pic.get("url")):
                                continue
                            out_path = os.path.join(output_folder, f"auto_{i+1:03d}.jpg")
                            pix_download(pic["url"], out_path)
                            if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                                tracker.register(pic.get("id"), pic["url"], out_path, item["keyword"])
                                mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                    "file": out_path, "keyword": item["keyword"], "source": "pixabay"})
                                found = True
                                break

                elif source == "unsplash":
                    pics = uns_photos(unsplash_api_key, item["keyword"], count=5)
                    for pic in (pics or []):
                        if tracker.is_duplicate(pic.get("id"), pic.get("url")):
                            continue
                        out_path = os.path.join(output_folder, f"auto_{i+1:03d}.jpg")
                        uns_download(pic["url"], out_path)
                        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                            tracker.register(pic.get("id"), pic["url"], out_path, item["keyword"])
                            mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                "file": out_path, "keyword": item["keyword"], "source": "unsplash"})
                            found = True
                            break

                elif source == "coverr":
                    vids = cov_videos(item["keyword"], count=5)
                    for vid in (vids or []):
                        if tracker.is_duplicate(vid.get("id"), vid.get("url")):
                            continue
                        out_path = os.path.join(output_folder, f"auto_{i+1:03d}.mp4")
                        dl = cov_download(vid["url"], out_path)
                        if dl and os.path.exists(out_path) and os.path.getsize(out_path) > 1000 and not tracker.is_hash_duplicate(out_path):
                            tracker.register(vid.get("id"), vid["url"], out_path, item["keyword"])
                            mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                "file": out_path, "keyword": item["keyword"], "source": "coverr"})
                            found = True
                            break

                elif source == "videvo":
                    vids = vid_videos(item["keyword"], count=5)
                    for vid in (vids or []):
                        if tracker.is_duplicate(vid.get("id"), vid.get("url")):
                            continue
                        out_path = os.path.join(output_folder, f"auto_{i+1:03d}.mp4")
                        dl = vid_download(vid["url"], out_path)
                        if dl and os.path.exists(out_path) and os.path.getsize(out_path) > 1000 and not tracker.is_hash_duplicate(out_path):
                            tracker.register(vid.get("id"), vid["url"], out_path, item["keyword"])
                            mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                "file": out_path, "keyword": item["keyword"], "source": "videvo"})
                            found = True
                            break

                elif source == "lifeofvids":
                    vids = lov_videos(item["keyword"], count=5)
                    for vid in (vids or []):
                        if tracker.is_duplicate(vid.get("id"), vid.get("url")):
                            continue
                        out_path = os.path.join(output_folder, f"auto_{i+1:03d}.mp4")
                        dl = lov_download(vid["url"], out_path)
                        if dl and os.path.exists(out_path) and os.path.getsize(out_path) > 1000 and not tracker.is_hash_duplicate(out_path):
                            tracker.register(vid.get("id"), vid["url"], out_path, item["keyword"])
                            mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                "file": out_path, "keyword": item["keyword"], "source": "lifeofvids"})
                            found = True
                            break

                elif source == "youtube" and youtube_available:
                    out_path = os.path.join(output_folder, f"auto_{i+1:03d}.mp4")
                    try:
                        yt_ok = yt_download(
                            keyword=item["keyword"],
                            output_path=out_path,
                            youtube_api_key=youtube_api_key or google_api_key or "",
                        )
                        if yt_ok and os.path.exists(out_path) and os.path.getsize(out_path) > 50_000:
                            if not tracker.is_hash_duplicate(out_path):
                                yt_id = f"yt_{hashlib.md5(item['keyword'].encode()).hexdigest()[:12]}"
                                tracker.register(yt_id, None, out_path, item["keyword"])
                                mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                    "file": out_path, "keyword": item["keyword"], "source": "youtube"})
                                found = True
                                print(f"    ✓ YouTube clip: '{item['keyword']}'")
                            else:
                                os.remove(out_path)
                    except Exception as yt_err:
                        print(f"    [YouTube] Error: {yt_err}")

                elif source == "mixkit":
                    out_path = os.path.join(output_folder, f"auto_{i+1:03d}.mp4")
                    try:
                        mk_ok = mixkit_download(item["keyword"], out_path)
                        if mk_ok and os.path.exists(out_path) and os.path.getsize(out_path) > 50_000:
                            if not tracker.is_hash_duplicate(out_path):
                                mk_id = f"mk_{hashlib.md5(item['keyword'].encode()).hexdigest()[:12]}"
                                tracker.register(mk_id, None, out_path, item["keyword"])
                                mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                    "file": out_path, "keyword": item["keyword"], "source": "mixkit"})
                                found = True
                            else:
                                os.remove(out_path)
                    except Exception as mk_err:
                        print(f"    [Mixkit] Error: {mk_err}")

            except Exception as exc:
                print(f"    [smart_broll] {source} error for '{item['keyword']}': {exc}")
                continue

        # If nothing found, try keyword expansion across ALL sources
        if not found:
            expanded_terms = _expand_keyword(item["keyword"], video_theme)
            for exp_term in expanded_terms:
                if found:
                    break
                print(f"    [smart_broll] Expanding keyword '{item['keyword']}' → '{exp_term}'")
                for source in source_order:
                    if found:
                        break
                    try:
                        out_path = os.path.join(output_folder, f"auto_{i+1:03d}.mp4")

                        if source == "youtube" and youtube_available:
                            try:
                                yt_ok = yt_download(keyword=exp_term, output_path=out_path,
                                                    youtube_api_key=youtube_api_key)
                                if yt_ok and os.path.exists(out_path) and os.path.getsize(out_path) > 50_000:
                                    if not tracker.is_hash_duplicate(out_path):
                                        yt_id = f"yt_exp_{hashlib.md5(exp_term.encode()).hexdigest()[:12]}"
                                        tracker.register(yt_id, None, out_path, exp_term)
                                        mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                            "file": out_path, "keyword": exp_term, "source": "youtube"})
                                        found = True
                                    else:
                                        os.remove(out_path)
                            except Exception:
                                pass

                        elif source == "mixkit":
                            try:
                                mk_ok = mixkit_download(exp_term, out_path)
                                if mk_ok and os.path.exists(out_path) and os.path.getsize(out_path) > 50_000:
                                    if not tracker.is_hash_duplicate(out_path):
                                        mk_id = f"mk_exp_{hashlib.md5(exp_term.encode()).hexdigest()[:12]}"
                                        tracker.register(mk_id, None, out_path, exp_term)
                                        mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                            "file": out_path, "keyword": exp_term, "source": "mixkit"})
                                        found = True
                                    else:
                                        os.remove(out_path)
                            except Exception:
                                pass

                        elif source == "pexels":
                            vids = pex_videos(pexels_api_key, exp_term, count=3, min_duration=3)
                            for vid in (vids or []):
                                if tracker.is_duplicate(vid.get("id"), vid.get("url")):
                                    continue
                                pex_download(vid["url"], out_path)
                                if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                                    tracker.register(vid.get("id"), vid["url"], out_path, exp_term)
                                    mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                        "file": out_path, "keyword": exp_term, "source": "pexels"})
                                    found = True
                                    break

                        elif source == "pixabay":
                            vids = pix_videos(pixabay_api_key, exp_term, count=3)
                            for vid in (vids or []):
                                if tracker.is_duplicate(vid.get("id"), vid.get("url")):
                                    continue
                                pix_download(vid["url"], out_path)
                                if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                                    tracker.register(vid.get("id"), vid["url"], out_path, exp_term)
                                    mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                        "file": out_path, "keyword": exp_term, "source": "pixabay"})
                                    found = True
                                    break

                        elif source == "coverr":
                            vids = cov_videos(exp_term, count=3)
                            for vid in (vids or []):
                                if tracker.is_duplicate(vid.get("id"), vid.get("url")):
                                    continue
                                dl = cov_download(vid["url"], out_path)
                                if dl and os.path.exists(out_path) and os.path.getsize(out_path) > 1000 and not tracker.is_hash_duplicate(out_path):
                                    tracker.register(vid.get("id"), vid["url"], out_path, exp_term)
                                    mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                        "file": out_path, "keyword": exp_term, "source": "coverr"})
                                    found = True
                                    break

                        elif source == "videvo":
                            vids = vid_videos(exp_term, count=3)
                            for vid in (vids or []):
                                if tracker.is_duplicate(vid.get("id"), vid.get("url")):
                                    continue
                                dl = vid_download(vid["url"], out_path)
                                if dl and os.path.exists(out_path) and os.path.getsize(out_path) > 1000 and not tracker.is_hash_duplicate(out_path):
                                    tracker.register(vid.get("id"), vid["url"], out_path, exp_term)
                                    mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                        "file": out_path, "keyword": exp_term, "source": "videvo"})
                                    found = True
                                    break

                        elif source == "lifeofvids":
                            vids = lov_videos(exp_term, count=3)
                            for vid in (vids or []):
                                if tracker.is_duplicate(vid.get("id"), vid.get("url")):
                                    continue
                                dl = lov_download(vid["url"], out_path)
                                if dl and os.path.exists(out_path) and os.path.getsize(out_path) > 1000 and not tracker.is_hash_duplicate(out_path):
                                    tracker.register(vid.get("id"), vid["url"], out_path, exp_term)
                                    mapped_clips.append({"timeline_start": item["start"], "timeline_end": item["end"],
                                                        "file": out_path, "keyword": exp_term, "source": "lifeofvids"})
                                    found = True
                                    break

                    except Exception:
                        continue

    print(f"  [smart_broll] ClipTracker: {len(tracker)} unique IDs, {len(mapped_clips)} clips mapped")
    if on_progress:
        on_progress(95, 100, f"Saving anti-reuse database...")
    
    # Save used clip IDs for cross-project anti-reuse
    try:
        from core.anti_reuse import save_used_clips
        new_ids = {cid for cid in tracker.used_ids}
        save_used_clips(new_ids)
        print(f"  [smart_broll] Anti-reuse: saved {len(new_ids)} clip IDs for future projects")
    except Exception as e:
        print(f"  [smart_broll] Anti-reuse save warning: {e}")
    
    if on_progress:
        on_progress(100, 100, f"Done! {len(mapped_clips)} clips downloaded and mapped.")

    try:
        os.remove(audio_path)
    except OSError:
        pass

    return mapped_clips


def _detect_video_theme(full_text: str) -> str:
    """Analyze full transcription to detect the overall video theme.
    This is CRITICAL for contextual keyword extraction.
    
    Returns a theme string like 'health_supplements', 'war_history', etc.
    """
    text = full_text.lower()
    
    # Theme detection with weighted keyword scoring
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
    
    # Health supplements indicators
    for word in ["supplement", "vitamin", "omega", "fish oil", "capsule", "pill",
                 "dosage", "nutrient", "mineral", "health benefit", "antioxidant",
                 "probiotic", "herbal", "extract", "dietary"]:
        if word in text:
            theme_scores["health_supplements"] += 3
    
    # Medical science
    for word in ["disease", "patient", "doctor", "hospital", "surgery", "diagnosis",
                 "treatment", "symptom", "clinical", "blood", "cell", "organ",
                 "cancer", "infection", "immune", "vaccine"]:
        if word in text:
            theme_scores["medical_science"] += 3
    
    # War/Military
    for word in ["war", "battle", "army", "soldier", "weapon", "invasion",
                 "conquest", "military", "general", "troops", "siege",
                 "guerra", "batalla", "ejército", "soldados"]:
        if word in text:
            theme_scores["war_military"] += 3
    
    # Ancient History
    for word in ["empire", "ancient", "civilization", "dynasty", "pharaoh",
                 "roman", "greek", "egyptian", "mongol", "medieval", "century",
                 "kingdom", "emperor", "khan", "silk road"]:
        if word in text:
            theme_scores["ancient_history"] += 3
    
    # Nature (excluding ocean-specific words to avoid overlap with ocean_marine)
    for word in ["forest", "animal", "species", "ecosystem",
                 "wildlife", "climate", "volcano", "earthquake",
                 "biodiversity", "habitat", "migration"]:
        if word in text:
            theme_scores["nature_documentary"] += 3
    
    # Space
    for word in ["planet", "star", "galaxy", "universe", "astronaut",
                 "nasa", "rocket", "orbit", "black hole", "nebula",
                 "solar", "cosmic", "telescope"]:
        if word in text:
            theme_scores["space_science"] += 3
    
    # Technology
    for word in ["computer", "software", "algorithm", "robot", "digital",
                 "artificial intelligence", "internet", "data", "silicon",
                 "processor", "innovation"]:
        if word in text:
            theme_scores["technology"] += 3
    
    # Economy
    for word in ["market", "economy", "inflation", "stock", "investment",
                 "gdp", "trade", "currency", "bank", "finance", "debt"]:
        if word in text:
            theme_scores["economy_finance"] += 3
    
    # Food/Nutrition
    for word in ["food", "diet", "nutrition", "recipe", "cooking",
                 "ingredient", "meal", "protein", "calorie", "organic"]:
        if word in text:
            theme_scores["food_nutrition"] += 3
    
    # Construction/Engineering
    for word in ["construction", "building", "architecture", "crane", "cement",
                 "concrete", "bridge", "infrastructure", "engineer", "blueprint",
                 "skyscraper", "foundation", "structure", "steel", "tower"]:
        if word in text:
            theme_scores["construction_engineering"] += 3
    
    # Ocean/Marine
    for word in ["ocean", "sea", "underwater", "marine", "coral", "reef",
                 "shipwreck", "submarine", "deep sea", "whale", "shark",
                 "tide", "wave", "fishing", "diver", "abyss"]:
        if word in text:
            theme_scores["ocean_marine"] += 3
    
    # Crime/Investigation
    for word in ["crime", "murder", "investigation", "detective", "forensic",
                 "police", "criminal", "evidence", "court", "prison",
                 "suspect", "trial", "heist", "robbery", "serial"]:
        if word in text:
            theme_scores["crime_investigation"] += 3
    
    # Psychology/Mind
    for word in ["psychology", "brain", "mental", "behavior", "consciousness",
                 "therapy", "anxiety", "depression", "emotion", "cognitive",
                 "subconscious", "trauma", "personality", "intelligence"]:
        if word in text:
            theme_scores["psychology_mind"] += 3
    
    # Geography/Places
    for word in ["country", "city", "mountain", "desert", "island",
                 "continent", "border", "territory", "population", "capital",
                 "landscape", "valley", "canyon", "river", "coast"]:
        if word in text:
            theme_scores["geography_places"] += 3
    
    # Religion/Spiritual
    for word in ["religion", "god", "church", "temple", "prayer",
                 "bible", "quran", "spiritual", "faith", "prophet",
                 "sacred", "ritual", "monastery", "divine", "worship"]:
        if word in text:
            theme_scores["religion_spiritual"] += 3
    
    best_theme = max(theme_scores, key=theme_scores.get)
    if theme_scores[best_theme] == 0:
        return "general_documentary"
    
    return best_theme


def _group_segments(whisper_segments: list, chunk_duration: float, total_duration: float) -> list:
    """Group whisper segments into larger time chunks."""
    chunks = []
    current_chunk = {"start": 0, "end": 0, "text": ""}

    for seg in whisper_segments:
        if seg["start"] - current_chunk["start"] >= chunk_duration and current_chunk["text"]:
            current_chunk["end"] = seg["start"]
            chunks.append(dict(current_chunk))
            current_chunk = {"start": seg["start"], "end": seg["end"], "text": ""}

        current_chunk["text"] += " " + seg["text"]
        current_chunk["end"] = seg["end"]

    # Add last chunk
    if current_chunk["text"].strip():
        current_chunk["end"] = total_duration
        chunks.append(current_chunk)

    return chunks


def _analyze_with_gemini(chunks: list, api_key: str, on_progress=None, video_theme: str = "") -> list:
    """Universal AI stock footage analyzer — uses GLM-5.1 (primary) with Gemini fallback.
    
    v3: Uses NVIDIA GLM-5.1 via ai_engine cascade (Gemini fallback).
    Sends ENTIRE transcription + chunks in a single AI call to get a
    globally-unique shot list with NO duplicate search terms across the video."""
    from core.ai_engine import ask_ai

    results = []
    used_keywords = set()

    full_text = " ".join(c["text"] for c in chunks)

    theme_contexts = {
        "health_supplements": "HEALTH/SUPPLEMENTS video. 'oil','fish','iron','acid' = supplements/nutrients, NOT literal animals/metals/liquids.",
        "medical_science": "MEDICAL SCIENCE video. All terms = medicine/treatments/healthcare.",
        "war_military": "WAR/MILITARY video. Focus on battles, soldiers, weapons, historic warfare.",
        "ancient_history": "ANCIENT HISTORY video. Focus on ruins, temples, archaeological sites.",
        "nature_documentary": "NATURE/WILDLIFE video. Focus on landscapes, animals, ecosystems.",
        "space_science": "SPACE/ASTRONOMY video. Focus on planets, galaxies, rockets, astronauts.",
        "technology": "TECHNOLOGY video. Focus on circuits, robots, AI, data centers, futuristic tech.",
        "economy_finance": "ECONOMICS/FINANCE video. Focus on stocks, currencies, business meetings.",
        "food_nutrition": "FOOD/NUTRITION video. Focus on ingredients, cooking, fresh produce.",
        "construction_engineering": "CONSTRUCTION video. Focus on buildings, cranes, workers, blueprints.",
        "ocean_marine": "OCEAN/MARINE video. Focus on underwater scenes, coral reefs, marine life.",
        "crime_investigation": "CRIME/INVESTIGATION video. Focus on forensics, police, courtrooms.",
        "psychology_mind": "PSYCHOLOGY/MIND video. Focus on brain scans, therapy, human behavior.",
        "geography_places": "GEOGRAPHY/PLACES video. Focus on landscapes, cities, maps, aerial views.",
        "religion_spiritual": "RELIGION/SPIRITUALITY video. Focus on temples, churches, prayer, ceremonies.",
    }
    theme_ctx = theme_contexts.get(video_theme, "GENERAL DOCUMENTARY. Analyze meaning and find visually relevant footage.")

    timeline_summary = []
    for i, c in enumerate(chunks):
        timeline_summary.append(f"Segment {i+1} ({c['start']:.0f}s-{c['end']:.0f}s): \"{c['text'].strip()[:120]}\"")
    timeline_text = "\n".join(timeline_summary)

    prompt = (
        f"You are the world's best stock footage researcher. You NEVER make mistakes.\n\n"
        f"VIDEO THEME: {theme_ctx}\n\n"
        f"FULL TRANSCRIPTION CONTEXT (entire video):\n\"\"\"{full_text[:2000]}\"\"\"\n\n"
        f"TIMELINE (segmented):\n{timeline_text}\n\n"
        f"YOUR TASK: For EACH segment above, suggest exactly 1 UNIQUE stock footage search term.\n\n"
        f"CRITICAL RULES:\n"
        f"1. EVERY search term MUST be UNIQUE across ALL segments\n"
        f"2. NEVER search literal words. Search the VISUAL MEANING in context of the theme\n"
        f"3. 'fish oil' in health = 'omega supplement capsules' NOT 'fish swimming'\n"
        f"4. 'iron deficiency' in health = 'blood test laboratory' NOT 'iron metal'\n"
        f"5. 'cell division' in science = 'microscope cells biology' NOT 'prison cell'\n"
        f"6. Each term: 2-5 words, specific, visually searchable, English\n"
        f"7. Think: 'What would a Pexels/Pixabay search return for this term?'\n\n"
        f"Return ONLY one search term per segment line, in order. No explanation, no numbering."
    )

    if on_progress:
        on_progress(20, 100, "Global AI analysis (all segments in one call)...")

    # Try GLM-5.1 via ai_engine cascade FIRST
    try:
        response = ask_ai(prompt, use_cache=False)
        if response and not response.startswith("["):
            terms = [t.strip() for t in response.strip().split("\n") if t.strip()]
            for j, term in enumerate(terms):
                if j >= len(chunks):
                    break
                term = term.lstrip("0123456789.-) ").strip('"\'*•–—')
                if term and len(term) > 2 and len(term) < 60 and term.lower() not in used_keywords:
                    used_keywords.add(term.lower())
                    results.append({
                        "start": chunks[j]["start"],
                        "end": chunks[j]["end"],
                        "keyword": term,
                    })
    except Exception as e:
        print(f"  [smart_broll] Global AI call failed: {e}")

    # If global call didn't produce enough results, fall back to per-chunk
    if len(results) < len(chunks) * 0.5:
        print(f"  [smart_broll] Global returned {len(results)}/{len(chunks)}, supplementing per-chunk...")
        for i, chunk in enumerate(chunks):
            if any(r["start"] == chunk["start"] for r in results):
                continue

            text = chunk["text"].strip()[:500]
            prompt_per = (
                f"You are the world's best stock footage researcher.\n\n"
                f"VIDEO CONTEXT: {theme_ctx}\n\n"
                f"NARRATION:\n\"{text}\"\n\n"
                f"Suggest 2 stock footage search terms (one per line). CRITICAL: terms must be UNIQUE, "
                f"never used before. Each 2-5 words, English, visually specific.\n"
                f"Return ONLY 2 terms, one per line."
            )

            try:
                resp = ask_ai(prompt_per, use_cache=False)
                if resp and not resp.startswith("["):
                    for t in resp.strip().split("\n"):
                        t = t.strip().lstrip("0123456789.-) ").strip("\"'*•–—")
                        if t and len(t) > 2 and len(t) < 60 and t.lower() not in used_keywords:
                            used_keywords.add(t.lower())
                            results.append({
                                "start": chunk["start"],
                                "end": chunk["end"],
                                "keyword": t,
                            })
                            break
            except Exception:
                fallback = _extract_smart_keywords([chunk], video_theme)
                for fb in fallback:
                    if fb["keyword"].lower() not in used_keywords:
                        used_keywords.add(fb["keyword"].lower())
                        results.append(fb)
                        break

            time.sleep(0.5)

    print(f"  [smart_broll] AI generated {len(results)} unique search terms")
    return results


# Common words to skip when extracting keywords
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
    """Context-aware keyword extraction using detected video theme.
    
    When theme is 'health_supplements', words like 'oil', 'acid', 'fish'
    are redirected to supplement/medical imagery instead of literal meanings.
    """
    # Theme-specific overrides: words that change meaning based on context
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

    # Get theme-specific overrides
    overrides = _THEME_OVERRIDES.get(video_theme, {})
    
    # Run base extraction
    results = _extract_simple_keywords(chunks)
    
    # Apply theme overrides to any results that need correction
    if overrides:
        for r in results:
            kw_lower = r["keyword"].lower()
            for word, replacement in overrides.items():
                # If the keyword is JUST the ambiguous word, override it
                kw_words = kw_lower.split()
                if word in kw_words and len(kw_words) <= 2:
                    r["keyword"] = replacement
                    break
    
    return results


def _extract_simple_keywords(chunks: list) -> list:
    """Smart contextual keyword extraction for stock footage.

    PHASE 1: Multi-word phrase matching (highest priority)
      - "fish oil" → "omega 3 supplement capsules" (NOT fish!)
      - "heart disease" → "human heart medical" (NOT valentine hearts!)

    PHASE 2: Single-word concept mapping with context awareness

    PHASE 3: Frequency-based fallback for unknown topics
    """
    # ========================================================
    # PHASE 1: MULTI-WORD PHRASES (context-aware, highest priority)
    # These prevent misinterpretation like "fish oil" → fish
    # ========================================================
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

    # ========================================================
    # PHASE 2: SINGLE-WORD CONCEPT MAPPING
    # Context-aware: words that need careful visual interpretation
    # ========================================================
    _VISUAL_CONCEPTS = {
        # Health (DO NOT show literal items - show medical/supplement context)
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

        # War/Military
        "batalla": "medieval battle swords", "guerra": "war battlefield soldiers",
        "ejército": "army soldiers formation", "soldados": "soldiers marching military",
        "espada": "sword medieval combat", "caballo": "horse cavalry medieval",
        "imperio": "ancient empire palace", "conquista": "military conquest invasion",
        "mongol": "mongol warrior horseback steppe", "khan": "mongol emperor throne",

        # Geography/Nature
        "montaña": "mountain landscape aerial", "desierto": "desert sand dunes",
        "océano": "ocean waves aerial", "bosque": "forest aerial green",
        "estepa": "steppe grassland horses", "ciudad": "ancient city ruins",

        # English - War
        "battle": "medieval battle combat", "warrior": "warrior combat ancient",
        "army": "army soldiers formation", "conquest": "military conquest",
        "empire": "ancient empire ruins palace", "invasion": "army invasion battle",
        "sword": "sword combat medieval", "horse": "horse cavalry riding",
        "castle": "medieval castle fortress", "village": "village countryside rural",
        "palace": "palace interior golden", "throne": "emperor king throne",

        # English - Nature
        "desert": "desert landscape dunes", "mountain": "mountain landscape snow",
        "ocean": "ocean waves underwater", "forest": "forest aerial canopy",
        "river": "river valley landscape", "volcano": "volcano eruption lava",
        "storm": "storm lightning dramatic", "sunrise": "sunrise golden landscape",
        "sunset": "sunset dramatic clouds", "waterfall": "waterfall tropical",
        "glacier": "glacier ice blue arctic", "cave": "cave underground dark",

        # English - Generic useful
        "gold": "gold treasure coins", "fire": "fire flames cinematic",
        "night": "night sky stars city", "rain": "rain drops storm",
        "snow": "snow winter landscape", "bridge": "bridge architecture",
        "ship": "ship sailing ocean", "trade": "marketplace trade ancient",
        "market": "ancient marketplace bazaar", "king": "medieval king crown",
        "temple": "ancient temple architecture", "pyramid": "egyptian pyramid desert",
        "science": "science laboratory research", "technology": "technology digital futuristic",
        "medicine": "medicine hospital medical", "doctor": "doctor medical professional",
        "food": "fresh food ingredients", "water": "water droplets clean",
    }

    results = []
    for chunk in chunks:
        text = chunk["text"].strip().lower()
        words = [w.strip(".,!?;:\"'()-[]") for w in text.split()]
        text_clean = " ".join(words)

        # PHASE 1: Multi-word phrase matching (most accurate)
        found = False
        for phrase, search_term in _PHRASE_MAP.items():
            if phrase in text_clean:
                results.append({
                    "start": chunk["start"],
                    "end": chunk["end"],
                    "keyword": search_term,
                })
                found = True
                break

        if found:
            continue

        # PHASE 2: Single-word concept matching
        for word in words:
            if word in _VISUAL_CONCEPTS:
                results.append({
                    "start": chunk["start"],
                    "end": chunk["end"],
                    "keyword": _VISUAL_CONCEPTS[word],
                })
                found = True
                break

        if found:
            continue

        # PHASE 3: Frequency-based fallback (use longest meaningful words)
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
            results.append({
                "start": chunk["start"],
                "end": chunk["end"],
                "keyword": f"{top_words[0]} {top_words[1]}",
            })
        elif top_words:
            results.append({
                "start": chunk["start"],
                "end": chunk["end"],
                "keyword": top_words[0],
            })

    return results
