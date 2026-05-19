"""
TitlePilot Pro — Backend Server
Premium viral title analysis with Gemini AI + YouTube Data API.
"""
import os, sys, json, re, time, math, webbrowser, threading
from datetime import datetime
from collections import Counter
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS

# Global file lock for multi-user safety
file_lock = threading.Lock()

# =============================================
# CONFIG
# =============================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, PARENT_DIR)

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# Load API key
def _load_key():
    try:
        from core.api_keys import load_api_key
        return load_api_key("google_ai")
    except:
        kf = os.path.join(PARENT_DIR, "api_keys.json")
        if os.path.exists(kf):
            with open(kf) as f:
                return json.load(f).get("google_ai", "")
    return ""

GOOGLE_API_KEY = _load_key()

# =============================================
# AI ENGINE — Multi-Model Failover
# =============================================
from core.ai_engine import ask_ai as _ask_ai_engine, get_model_status, clear_cooldowns

def ask_gemini(prompt, user_api_key=None, max_retries=None):
    """Send prompt to AI with automatic model failover.
    Uses 3 Gemini models with smart rotation on rate limits.
    """
    key = user_api_key if user_api_key and len(user_api_key) > 10 else GOOGLE_API_KEY
    return _ask_ai_engine(prompt, api_key=key or None, max_retries=max_retries)

# =============================================
# VIRAL ANALYSIS ENGINE
# =============================================
VIRAL_STRUCTURES = {
    "curiosity_gap": {
        "pattern": r"(?:why|how|what|the real reason|the truth|nobody|secret|hidden|no one)",
        "name": "Curiosity Gap",
        "ctr_boost": 1.40,
        "desc": "Creates information gap the viewer MUST close",
    },
    "superlative": {
        "pattern": r"(?:most|largest|biggest|deadliest|worst|best|extreme|impossible|insane|craziest)",
        "name": "Superlative",
        "ctr_boost": 1.35,
        "desc": "Extreme claims that demand attention",
    },
    "specific_number": {
        "pattern": r"(?:\$[\d,.]+|\b\d+\b).*(?:that|which|reason|way|thing|fact|place)",
        "name": "Specific Number",
        "ctr_boost": 1.30,
        "desc": "Numbers add credibility and specificity",
    },
    "permanence": {
        "pattern": r"(?:never|forever|always|still|remains|eternal|no longer|ended)",
        "name": "Permanence Claim",
        "ctr_boost": 1.25,
        "desc": "Permanence creates urgency and weight",
    },
    "authority_emotion": {
        "pattern": r"(?:scientist|government|nasa|expert|doctor|military|fbi|cia).*(?:hid|warn|afraid|shock|terrif|speechless|panic)",
        "name": "Authority + Emotion",
        "ctr_boost": 1.45,
        "desc": "Authority figures + emotional reaction = highest CTR",
    },
    "contrast": {
        "pattern": r"(?:but|however|yet|despite|instead|actually|turns out|thought.*wrong)",
        "name": "Contrast Hook",
        "ctr_boost": 1.30,
        "desc": "Unexpected twist creates cognitive dissonance",
    },
    "forbidden": {
        "pattern": r"(?:forbidden|banned|illegal|restricted|classified|censored|deleted)",
        "name": "Forbidden Content",
        "ctr_boost": 1.40,
        "desc": "Restricted = must-see content",
    },
}

EMOTIONAL_WORDS = {
    "terrifying": 9, "shocking": 8, "insane": 8, "unbelievable": 7,
    "deadly": 9, "dangerous": 8, "forbidden": 9, "secret": 8,
    "hidden": 7, "impossible": 8, "extreme": 7, "incredible": 6,
    "mysterious": 7, "ancient": 6, "cursed": 8, "haunted": 7,
    "brutal": 9, "savage": 8, "horrifying": 9, "catastrophic": 8,
    "abandoned": 7, "destroyed": 7, "unstoppable": 7, "legendary": 6,
    "massive": 6, "terrified": 9, "speechless": 7, "nightmare": 8,
}

def analyze_title(title):
    """Deep analysis of a single title."""
    result = {
        "title": title,
        "length": len(title),
        "words": len(title.split()),
        "score": 0,
        "structures": [],
        "emotional_words": [],
        "power_words": [],
        "issues": [],
        "suggestions": [],
    }
    
    t_lower = title.lower()
    
    # Length scoring (optimal: 70-100 chars for maximum CTR)
    tlen = len(title)
    if tlen < 40:
        result["issues"].append("Too short — aim for 70-100 characters for maximum CTR")
        result["score"] -= 10
    elif tlen < 55:
        result["issues"].append("Short title — 70-100 chars performs 40% better")
        result["score"] -= 3
    elif tlen > 100:
        result["issues"].append("Too long — YouTube truncates after ~100 chars")
        result["score"] -= 5
    elif 75 <= tlen <= 95:
        result["score"] += 15  # Sweet spot
    elif 65 <= tlen <= 100:
        result["score"] += 12
    elif 55 <= tlen <= 64:
        result["score"] += 5
    
    # CAPS words
    caps = re.findall(r'\b[A-Z]{3,}\b', title)
    if caps:
        result["power_words"] = caps
        result["score"] += min(len(caps) * 6, 18)
    elif not re.search(r'[A-Z]{3,}', title):
        result["suggestions"].append("Add 1-2 CAPS words for emphasis (e.g., TERRIFYING, NEVER)")
    
    # Emotional words
    for word, val in EMOTIONAL_WORDS.items():
        if word in t_lower:
            result["emotional_words"].append(word)
            result["score"] += val
    
    if not result["emotional_words"]:
        result["suggestions"].append("Add emotional trigger words (terrifying, deadly, forbidden, etc.)")
    
    # Structure detection
    for sid, sdata in VIRAL_STRUCTURES.items():
        if re.search(sdata["pattern"], t_lower):
            result["structures"].append({
                "id": sid,
                "name": sdata["name"],
                "ctr_boost": sdata["ctr_boost"],
                "desc": sdata["desc"],
            })
            result["score"] += int(sdata["ctr_boost"] * 10)
    
    if not result["structures"]:
        result["suggestions"].append("Use a viral structure: Curiosity Gap, Superlative, or Authority + Emotion")
    
    # Numbers
    if re.search(r'\$?[\d,.]+', title):
        result["score"] += 8
    else:
        result["suggestions"].append("Consider adding specific numbers ($2.5 Billion, 15 Places, etc.)")
    
    # Question mark
    if "?" in title:
        result["score"] += 5
    
    result["score"] = min(result["score"], 100)
    
    if result["score"] >= 80: result["grade"] = "S"
    elif result["score"] >= 65: result["grade"] = "A"
    elif result["score"] >= 50: result["grade"] = "B"
    elif result["score"] >= 35: result["grade"] = "C"
    elif result["score"] >= 20: result["grade"] = "D"
    else: result["grade"] = "F"
    
    return result

# =============================================
# ROUTES
# =============================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.json
    title = data.get("title", "")
    if not title:
        return jsonify({"error": "No title provided"}), 400
    return jsonify(analyze_title(title))

@app.route("/api/analyze_hook", methods=["POST"])
def api_analyze_hook():
    data = request.json
    hook_script = data.get("hook", "")
    video_title = data.get("title", "")
    language = data.get("language", "English")
    
    if not hook_script or len(hook_script) < 20:
        return jsonify({"error": "Script is too short to analyze."}), 400
        
    prompt = f"""You are a master YouTube retention analyst. 
Analyze the FIRST 30 SECONDS (the hook) of a YouTube script.

VIDEO TITLE: "{video_title}"
TARGET LANGUAGE: {language}

SCRIPT HOOK:
"{hook_script}"

Do a brutal, honest analysis of why this hook will succeed or fail at retaining the viewer past 30 seconds.
Provide EXACTLY the following structure (Use Markdown formatting for readability, but no JSON or code blocks):

### 📊 Predicted 30s Retention: [XX]%

### 🔍 What Works (Strengths)
- [Strength 1]
- [Strength 2]

### ⚠️ What Kills Retention (Weaknesses)
- [Weakness 1]
- [Weakness 2]

### ✍️ The "Viral" Re-Write
[Provide a completely rewritten, incredibly punchy, curiosity-driven version of their hook that guarantees >75% retention. Make it fast-paced, visceral, and directly bridge the gap set by the title.]
"""
    result = ask_gemini(prompt, request.json.get("ai_api_key"))
    return jsonify({"analysis": result})

@app.route("/api/seo_optimize", methods=["POST"])
def api_seo_optimize():
    data = request.json
    title = data.get("title", "")
    context = data.get("context", "")
    language = data.get("language", "English")
    
    if not title:
        return jsonify({"error": "Title is required"}), 400
        
    prompt = f"""You are an elite YouTube SEO expert.
Your goal is to write the ultimate YouTube metadata package for this video.

VIDEO TITLE: "{title}"
VIDEO CONTEXT/HOOK: "{context}"
TARGET LANGUAGE: {language}

Provide EXACTLY the following structure (Use Markdown formatting):

### 📝 Optimized Description (First 3 Lines)
[Write the critical first 3 lines of the description. This is what shows above the "Show More" button. It must hook the viewer, contain the main keyword organically, and NOT just repeat the title.]

### 🕒 Suggested Chapters (Timestamps)
0:00 - [Hook/Intro name]
[Suggest 3 to 5 logical timestamp chapters based on the context. Make the chapter titles curiosity-driven, not boring.]

### 🏷️ Top 500-Character Tags
[Provide a comma-separated list of highly searched, long-tail and short-tail tags related to this topic. Do not exceed 500 characters total.]

### 💡 Search Ranking Strategy
[Give 2 brief tips on how to rank this specific video higher based on current YouTube algorithm trends.]
"""
    result = ask_gemini(prompt, request.json.get("ai_api_key"))
    return jsonify({"seo": result})

@app.route("/api/analyze_batch", methods=["POST"])
def api_analyze_batch():
    data = request.json
    titles = data.get("titles", [])
    results = [analyze_title(t) for t in titles if t.strip()]
    
    if not results:
        return jsonify({"error": "No titles"}), 400
    
    scores = [r["score"] for r in results]
    struct_count = Counter()
    word_freq = Counter()
    for r in results:
        for s in r["structures"]:
            struct_count[s["name"]] += 1
        skip = {"the","a","an","is","in","on","of","and","to","that","this","for","with","are","was"}
        for w in r["title"].lower().split():
            w = w.strip(".,!?;:'\"()-[]|")
            if w and len(w) > 2 and w not in skip:
                word_freq[w] += 1
    
    return jsonify({
        "count": len(results),
        "avg_score": round(sum(scores)/len(scores), 1),
        "best": max(results, key=lambda r: r["score"]),
        "worst": min(results, key=lambda r: r["score"]),
        "structures": dict(struct_count.most_common(10)),
        "top_words": dict(word_freq.most_common(25)),
        "results": sorted(results, key=lambda r: r["score"], reverse=True),
    })

@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.json
    topic = data.get("topic", "")
    language = data.get("language", "English")
    niche = data.get("niche", "")
    
    prompt = f"""You are the #1 YouTube title engineer. You've studied every 100M+ view viral video.

TOPIC: {topic}
LANGUAGE: {language}
{"NICHE CONTEXT: " + niche if niche else ""}

Generate exactly 15 EXTREMELY viral, persuasive, impactful YouTube titles.

=== CRITICAL: LENGTH REQUIREMENT ===
EVERY title MUST be 70-95 characters long. Count EVERY character including spaces.
If a title is under 70 chars, ADD more specific details until it reaches 70+.
Titles under 65 characters = AUTOMATIC FAILURE.

=== TITLE FORMULA ===
[Hook/Opener] + [Core Topic with CAPS word] + [Consequence/Extension That Adds Length]

EXAMPLES OF PERFECT LENGTH (study these carefully):
- "Inside The FORBIDDEN $2.8 Billion Underground City That Nobody Was Supposed To Find" (84c)
- "Why Scientists Are TERRIFIED Of What They Just Discovered Deep Under The Ocean" (78c)
- "The Most DANGEROUS $4.5 Billion Bridge That Engineers REFUSE To Cross Anymore" (78c)
- "7 ABANDONED Megaprojects That Cost Billions Of Dollars And Were NEVER Completed" (80c)
- "The DEADLIEST Place On Earth Where Nobody Has Ever Survived More Than 3 Minutes" (81c)
- "NASA Just CONFIRMED What Scientists Were Desperately Afraid Of For The Last 50 Years" (85c)
- "Why Nobody Is Allowed To Visit This FORBIDDEN Island In The Middle Of The Ocean" (81c)

=== VIRAL STRUCTURES (combine 2+ per title) ===
- CURIOSITY GAP + CONSEQUENCE: "The Real Reason Why Nobody..."
- SUPERLATIVE + SPECIFICS: "The Most DANGEROUS $3.2 Billion..."
- AUTHORITY + TERROR: "Why Scientists Are TERRIFIED Of..."
- NUMBER + FORBIDDEN: "7 FORBIDDEN Places Where Nobody Has Ever..."
- PROFESSION + REFUSAL: "Why Engineers REFUSE To Enter This..."

=== MANDATORY ===
- 1-2 ALL CAPS words per title (TERRIFYING, FORBIDDEN, NEVER, DEADLIEST, SHOCKING, IMPOSSIBLE)
- Emotional triggers (deadly, terrifying, forbidden, abandoned, catastrophic)
- Specificity (dollar amounts, distances, time periods, locations, professions)
- MINIMUM 70 characters per title (THIS IS THE MOST IMPORTANT RULE)

Return exactly 15 results. Format each line like this:
[Title] | Thumbnail Concept: [Brief visual idea for the thumbnail]

No explanations. VERIFY each title is 70+ characters."""

    result = ask_gemini(prompt, request.json.get("ai_api_key"))
    
    # Parse titles from AI response
    raw_titles = []
    for line in result.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        line = re.sub(r'^\d+[\.\)\-\s]+', '', line).strip()
        line = line.strip('"\'')
        if line and len(line) > 20:
            raw_titles.append(line)
    
    # Second pass: expand titles that are too short (< 65 chars)
    short_titles = [t for t in raw_titles if len(t) < 65]
    if short_titles:
        expand_list = "\n".join(f"- {t} ({len(t)} chars)" for t in short_titles[:10])
        expand_prompt = f"""These YouTube titles are TOO SHORT. Expand each to 75-90 characters by adding specificity.

TITLES TO EXPAND:
{expand_list}

RULES:
- Each expanded title MUST be 75-90 characters long
- Keep same emotional tone and ALL CAPS words
- Add: dollar amounts, distances, time periods, locations, or consequences
- Make them MORE specific and MORE clickable

Return ONLY the expanded titles, one per line, numbered. No explanations."""
        
        expanded_result = ask_gemini(expand_prompt, request.json.get("ai_api_key"))
        expanded_titles = []
        for line in expanded_result.strip().split("\n"):
            line = line.strip()
            line = re.sub(r'^\d+[\.\)\-\s]+', '', line).strip().strip('"\'')
            if line and len(line) > 30:
                expanded_titles.append(line)
        
        # Replace short titles with expanded versions
        exp_idx = 0
        for i, t in enumerate(raw_titles):
            if len(t) < 65 and exp_idx < len(expanded_titles):
                raw_titles[i] = expanded_titles[exp_idx]
                exp_idx += 1
    
    titles = []
    for line in raw_titles:
        if len(line) < 30:
            continue
        
        # Extract thumbnail concept if present
        parts = line.split("| Thumbnail Concept:")
        actual_title = parts[0].strip()
        thumb_concept = parts[1].strip() if len(parts) > 1 else ""
        
        analysis = analyze_title(actual_title)
        titles.append({
            "title": actual_title,
            "length": len(actual_title),
            "score": analysis["score"],
            "grade": analysis["grade"],
            "structures": [s["name"] for s in analysis["structures"]],
            "thumbnail_concept": thumb_concept
        })
    
    titles.sort(key=lambda x: x["score"], reverse=True)
    return jsonify({"titles": titles, "topic": topic})

@app.route("/api/subniche", methods=["POST"])
def api_subniche():
    data = request.json
    theme = data.get("theme", "")
    language = data.get("language", "English")
    
    prompt = f"""You are an expert YouTube niche analyst who has studied thousands of channels.

{"MAIN THEME: " + theme if theme else "Analyze ALL trending YouTube themes."}
TARGET LANGUAGE: {language}

Your task: Find the most PROFITABLE subniches that have:
- HIGH viewer demand (people actively searching)
- LOW creator supply (few channels covering it well)
- VIRAL potential (emotional, curiosity-driven topics)

For each subniche provide:
1. SUBNICHE NAME (specific, not broad)
2. DEMAND LEVEL (1-10): How much viewers want this content
3. SUPPLY LEVEL (1-10): How many creators already do this well
4. OPPORTUNITY SCORE: demand minus supply
5. TARGET AUDIENCE: Who watches this
6. AUDIENCE PAIN: What problem/curiosity they have
7. CONTENT ANGLE: The unique approach to stand out
8. 3 EXAMPLE VIRAL TITLES: Using proven structures, max 100 chars each

Return exactly 8 subniches in this JSON format:
[
  {{
    "name": "Subniche Name",
    "demand": 9,
    "supply": 2,
    "opportunity": 7,
    "target_audience": "Description",
    "audience_pain": "What they want to know",
    "content_angle": "How to stand out",
    "example_titles": ["Title 1", "Title 2", "Title 3"],
    "keywords": ["keyword1", "keyword2", "keyword3"],
    "estimated_views_per_video": "50K-200K"
  }}
]

Return ONLY valid JSON array. No markdown, no explanation."""

    result = ask_gemini(prompt, request.json.get("ai_api_key"))
    
    # Parse JSON
    try:
        # Find JSON array in response
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if match:
            niches = json.loads(match.group())
        else:
            niches = json.loads(result)
        
        # Sort by opportunity
        niches.sort(key=lambda x: x.get("opportunity", 0), reverse=True)
        return jsonify({"niches": niches, "theme": theme})
    except:
        return jsonify({"niches": [], "raw": result, "theme": theme})

@app.route("/api/niche_list", methods=["GET"])
def api_niche_list():
    try:
        import core.niche_database as ndb
        return jsonify({"niches": ndb.NICHE_DATABASE})
    except:
        return jsonify({"niches": {}})

# =============================================
# SUBNICHE VALIDATED — YouTube Data API Lookup
# =============================================
@app.route("/api/subniche_validate", methods=["POST"])
def api_subniche_validate():
    """Fetch real YouTube data to validate a subniche — trending channels, views/hour, recent videos."""
    from core.api_keys import load_api_key
    import requests as _req
    from datetime import datetime, timedelta
    
    data = request.json
    subniche_name = data.get("name", "")
    keywords = data.get("keywords", [])
    
    if not subniche_name:
        return jsonify({"error": "No subniche name"}), 400
    
    yt_key = load_api_key("youtube")
    if not yt_key:
        return jsonify({"error": "YouTube API key not configured. Go to Settings and add your key."}), 400
    
    # Search query from subniche name + keywords
    search_q = subniche_name + " " + " ".join(keywords[:3])
    
    # 1. Search for recent videos in this subniche (last 30 days)
    published_after = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    search_url = "https://www.googleapis.com/youtube/v3/search"
    search_params = {
        "part": "snippet",
        "q": search_q,
        "type": "video",
        "order": "viewCount",
        "maxResults": 15,
        "publishedAfter": published_after,
        "key": yt_key
    }
    
    try:
        sr = _req.get(search_url, params=search_params, timeout=10)
        search_data = sr.json()
    except Exception as e:
        return jsonify({"error": f"YouTube API error: {e}"}), 500
    
    if "error" in search_data:
        return jsonify({"error": search_data["error"].get("message", "Unknown API error")}), 400
    
    video_ids = [item["id"]["videoId"] for item in search_data.get("items", []) if "videoId" in item.get("id", {})]
    
    if not video_ids:
        return jsonify({"channels": [], "videos": [], "subniche": subniche_name})
    
    # 2. Get video statistics
    stats_url = "https://www.googleapis.com/youtube/v3/videos"
    stats_params = {
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(video_ids),
        "key": yt_key
    }
    stats_r = _req.get(stats_url, params=stats_params, timeout=10)
    stats_data = stats_r.json()
    
    videos = []
    channel_map = {}  # channel_id -> channel info
    
    for item in stats_data.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        channel_id = snippet.get("channelId", "")
        
        # Calculate VPH (views per hour)
        views = int(stats.get("viewCount", 0))
        published = snippet.get("publishedAt", "")
        hours_ago = 1
        try:
            pub_dt = datetime.strptime(published[:19], "%Y-%m-%dT%H:%M:%S")
            hours_ago = max(1, (datetime.utcnow() - pub_dt).total_seconds() / 3600)
        except:
            pass
        
        vph = round(views / hours_ago, 1)
        
        video_info = {
            "id": item["id"],
            "title": snippet.get("title", ""),
            "channel_name": snippet.get("channelTitle", ""),
            "channel_id": channel_id,
            "views": views,
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
            "published": published[:10],
            "vph": vph,
            "hours_ago": round(hours_ago),
            "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", "")
        }
        videos.append(video_info)
        
        if channel_id not in channel_map:
            channel_map[channel_id] = {
                "id": channel_id,
                "name": snippet.get("channelTitle", ""),
                "videos_found": 0,
                "total_views": 0,
                "avg_vph": 0
            }
        channel_map[channel_id]["videos_found"] += 1
        channel_map[channel_id]["total_views"] += views
    
    # 3. Get channel details (subscriber counts)
    if channel_map:
        ch_url = "https://www.googleapis.com/youtube/v3/channels"
        ch_params = {
            "part": "statistics,snippet",
            "id": ",".join(list(channel_map.keys())[:10]),
            "key": yt_key
        }
        ch_r = _req.get(ch_url, params=ch_params, timeout=10)
        ch_data = ch_r.json()
        
        for ch in ch_data.get("items", []):
            cid = ch["id"]
            if cid in channel_map:
                ch_stats = ch.get("statistics", {})
                channel_map[cid]["subscribers"] = int(ch_stats.get("subscriberCount", 0))
                channel_map[cid]["total_channel_views"] = int(ch_stats.get("viewCount", 0))
                channel_map[cid]["video_count"] = int(ch_stats.get("videoCount", 0))
                channel_map[cid]["thumbnail"] = ch.get("snippet", {}).get("thumbnails", {}).get("default", {}).get("url", "")
    
    # Sort videos by VPH
    videos.sort(key=lambda v: v["vph"], reverse=True)
    
    # Calc avg VPH per channel
    for cid, ch in channel_map.items():
        ch_videos = [v for v in videos if v["channel_id"] == cid]
        ch["avg_vph"] = round(sum(v["vph"] for v in ch_videos) / max(1, len(ch_videos)), 1)
    
    channels = sorted(channel_map.values(), key=lambda c: c.get("avg_vph", 0), reverse=True)
    
    return jsonify({
        "subniche": subniche_name,
        "videos": videos,
        "channels": channels,
        "total_videos": len(videos),
        "total_channels": len(channels),
        "avg_vph": round(sum(v["vph"] for v in videos) / max(1, len(videos)), 1)
    })

# =============================================
# SAVED TREND CHANNELS
# =============================================
import os as _os
_SAVED_CHANNELS_PATH = _os.path.join(_os.path.dirname(__file__), "saved_channels.json")

@app.route("/api/saved_channels", methods=["GET"])
def api_get_saved_channels():
    try:
        with open(_SAVED_CHANNELS_PATH, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except:
        return jsonify({"channels": []})

@app.route("/api/saved_channels", methods=["POST"])
def api_save_channel():
    data = request.json
    try:
        try:
            with open(_SAVED_CHANNELS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except:
            saved = {"channels": []}
        
        # Don't duplicate
        existing_ids = {c.get("id") for c in saved["channels"]}
        if data.get("id") not in existing_ids:
            saved["channels"].append({
                "id": data.get("id", ""),
                "name": data.get("name", ""),
                "subscribers": data.get("subscribers", 0),
                "avg_vph": data.get("avg_vph", 0),
                "subniche": data.get("subniche", ""),
                "saved_at": __import__("datetime").datetime.now().isoformat(),
                "thumbnail": data.get("thumbnail", "")
            })
            with file_lock:
                with open(_SAVED_CHANNELS_PATH, "w", encoding="utf-8") as f:
                    json.dump(saved, f, indent=2)
        
        return jsonify({"ok": True, "total": len(saved["channels"])})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/deep_analysis", methods=["POST"])
def api_deep_analysis():
    """Deep AI analysis of a title with full breakdown."""
    data = request.json
    title = data.get("title", "")
    
    basic = analyze_title(title)
    
    prompt = f"""You are the world's #1 YouTube title optimization expert.

TITLE TO ANALYZE: "{title}"

Provide a COMPREHENSIVE analysis:

1. FIRST IMPRESSION (1-2 sentences): What a viewer thinks seeing this
2. MENTAL IMAGE TEST: Does the title create a clear mental image? What image?
3. CLICK TRIGGER: Why would someone click? What curiosity does it create?
4. EMOTIONAL HOOK: What emotion does it trigger? How strong (1-10)?
5. SPECIFICITY SCORE (1-10): How specific vs generic is this title?
6. COMPETITION CHECK: How saturated is this exact angle?
7. THUMBNAIL COMPATIBILITY: What thumbnail would pair well?
8. 3 IMPROVED VERSIONS: Better titles (60-100 chars, with viral structures)
9. WEAKNESS: The #1 thing holding this title back
10. VERDICT: Would this go viral? Why or why not?

Be brutally honest. Be specific. No generic advice."""

    ai_analysis = ask_gemini(prompt)
    basic["ai_deep_analysis"] = ai_analysis
    return jsonify(basic)

@app.route("/api/channel_strategy", methods=["POST"])
def api_channel_strategy():
    """AI-powered channel strategy analysis."""
    data = request.json
    channel_type = data.get("channel_type", "")
    current_titles = data.get("titles", [])
    target_audience = data.get("target_audience", "")
    language = data.get("language", "English")
    
    titles_text = "\n".join(f"- {t}" for t in current_titles[:20]) if current_titles else "No titles provided"
    
    prompt = f"""You are a YouTube growth strategist who has scaled channels from 0 to 1M subscribers.

CHANNEL TYPE: {channel_type}
TARGET AUDIENCE: {target_audience}
LANGUAGE: {language}

CURRENT TITLES (if any):
{titles_text}

Provide a COMPLETE channel strategy:

1. NICHE POSITIONING
   - What exact micro-niche should this channel own?
   - What's the unique value proposition?
   - Name 3 successful reference channels and what they do right

2. CONTENT PILLARS (4 types of videos to make)
   For each pillar:
   - Pillar name
   - Why it works
   - 2 example titles (60-100 chars, viral structures)
   - Expected performance

3. TITLE FORMULA
   - The 3 best title structures for this niche
   - 5 power words that work best in this niche
   - Ideal title length

4. AUDIENCE ANALYSIS
   - Demographics (age, gender, interests)
   - Primary pain point / curiosity
   - What makes them subscribe
   - Best posting schedule

5. GROWTH ROADMAP
   - First 10 videos: what to publish
   - Months 1-3: strategy
   - Months 3-6: scaling approach

6. TOP 10 VIDEO IDEAS
   - Full titles (60-100 chars each)
   - Why each would perform

Be extremely specific. No generic advice. Real actionable strategy."""

    result = ask_gemini(prompt)
    return jsonify({"strategy": result, "channel_type": channel_type})

@app.route("/api/strategy_remix", methods=["POST"])
def api_strategy_remix():
    """Generates a dual-path A/B Strategy for titles."""
    data = request.json
    topic = data.get("topic", "")
    language = data.get("language", "English")
    path_a = data.get("path_a", "Curiosity & Mystery")
    path_b = data.get("path_b", "Fear & Consequence")
    
    prompt = f"""You are the world's #1 YouTube growth strategist and title engineer.

TOPIC: {topic}
LANGUAGE: {language}

Your task is to create a "Strategy Remix" (A/B Test) for this topic. You must generate two entirely different strategic paths to hook the audience.

PATH A: {path_a}
PATH B: {path_b}

For EACH PATH, provide:
1. THE ANGLE: A 2-sentence explanation of why this psychological angle works for this topic.
2. THE HOOK: The core emotional trigger being used.
3. 5 VIRAL TITLES: 
   - Must be 70-100 characters long
   - Must use 1-2 ALL CAPS power words
   - Must fit the psychological angle of the path perfectly

4. THE WINNING SCENARIO: When to use this path (e.g., "Use Path A if your thumbnail is dark and mysterious").

Provide a clear, highly structured comparison between Path A and Path B. Be specific, aggressive with the copy, and highly persuasive."""

    result = ask_gemini(prompt)
    return jsonify({"remix": result, "topic": topic, "path_a": path_a, "path_b": path_b})

@app.route("/api/trend_scanner", methods=["POST"])
def api_trend_scanner():
    """Scan for trending topics and emerging niches."""
    data = request.json
    category = data.get("category", "all")
    language = data.get("language", "English")
    
    prompt = f"""You are a YouTube trend analyst with access to the latest data.

CATEGORY: {category if category != 'all' else 'All categories'}
LANGUAGE: {language}
"""
    # Using the same prompt text logic
    prompt = prompt + """
Analyze current YouTube trends and provide:

1. TOP 10 TRENDING THEMES RIGHT NOW
   For each theme:
   - Theme name
   - Why it's trending
   - Demand level (1-10)
   - Competition level (1-10)
   - Best sub-angle
   - Viral title example (max 100 chars)

2. EMERGING MICRO-NICHES (not yet saturated)
   - 5 micro-niches with huge potential
   - For each: name, opportunity score, target audience, 2 title examples

3. DYING NICHES TO AVOID
   - 3 niches losing traction and why

4. CROSS-NICHE OPPORTUNITIES
   - 3 ways to combine trending themes into unique angles
   - Example: "Ancient History + Science" = "The Science Behind Ancient Mysteries"

5. VIRAL MECHANICS THAT WORK NOW
   - Top 3 title structures getting clicks right now
   - Top 5 emotional triggers performing best
   - Optimal title length trend

Be specific with real examples. Focus on actionable insights."""

    result = ask_gemini(prompt, request.json.get("ai_api_key"))
    return jsonify({"trends": result, "category": category, "date": datetime.now().isoformat()})

# =============================================
# YOUTUBE DATA API — Real metrics
# =============================================
def _load_yt_key():
    try:
        from core.api_keys import load_api_key
        k = load_api_key("youtube")
        if k: return k
    except: pass
    return ""

YOUTUBE_API_KEY = _load_yt_key()

@app.route("/api/youtube/save_key", methods=["POST"])
def save_yt_key():
    global YOUTUBE_API_KEY
    data = request.json
    key = data.get("key", "").strip()
    if not key:
        return jsonify({"error": "No key"}), 400
    from core.api_keys import save_api_key
    save_api_key("youtube", key)
    YOUTUBE_API_KEY = key
    return jsonify({"status": "ok"})

@app.route("/api/youtube/channel", methods=["POST"])
def yt_channel():
    """Analyze a YouTube channel with real data."""
    from core.youtube_api import get_channel_info, get_channel_videos
    data = request.json
    channel_input = data.get("channel", "")
    key = data.get("yt_api_key") or YOUTUBE_API_KEY
    if not key:
        return jsonify({"error": "YouTube API key not set. Go to Settings tab."}), 400
    
    info = get_channel_info(channel_input, key)
    if not info:
        return jsonify({"error": f"Channel not found: {channel_input}"}), 404
    
    videos = get_channel_videos(info["id"], key, max_videos=int(data.get("max_videos", 30)))
    
    # Calculate channel metrics
    vph_list = [v["vph"] for v in videos if v["vph"] > 0]
    views_list = [v["views"] for v in videos]
    eng_list = [v["engagement"] for v in videos if v["engagement"] > 0]
    
    avg_vph = round(sum(vph_list) / max(len(vph_list), 1), 1)
    avg_views = round(sum(views_list) / max(len(views_list), 1))
    
    # VPH multiplier and Outlier Score for each video
    for v in videos:
        v["vph_multiplier"] = round(v["vph"] / max(avg_vph, 0.1), 1)
        v["outlier_score"] = round(v["views"] / max(avg_views, 1), 1)
    
    # Title word frequency
    from collections import Counter
    word_freq = Counter()
    skip = {"the","a","an","is","in","on","of","and","to","that","this","for","with","are","was","you","your","it"}
    for v in videos:
        for w in v["title"].lower().split():
            w = w.strip(".,!?;:'\"()-[]|#")
            if w and len(w) > 2 and w not in skip:
                word_freq[w] += 1
    
    return jsonify({
        "channel": info,
        "videos": sorted(videos, key=lambda v: v["vph"], reverse=True),
        "metrics": {
            "avg_vph": avg_vph,
            "max_vph": max(vph_list) if vph_list else 0,
            "avg_views": round(sum(views_list) / max(len(views_list), 1)),
            "avg_engagement": round(sum(eng_list) / max(len(eng_list), 1), 2),
            "total_analyzed": len(videos),
        },
        "top_words": dict(word_freq.most_common(25)),
    })

@app.route("/api/youtube/niche", methods=["POST"])
def yt_niche():
    """Deep niche analysis with real YouTube data."""
    from core.youtube_api import analyze_niche
    data = request.json
    query = data.get("query", "")
    region = data.get("region", "US")
    key = data.get("yt_api_key") or YOUTUBE_API_KEY
    if not key:
        return jsonify({"error": "YouTube API key not set"}), 400
    if not query:
        return jsonify({"error": "No query provided"}), 400
    
    result = analyze_niche(query, key, region=region)
    return jsonify(result)

@app.route("/api/youtube/trending", methods=["POST"])
def yt_trending():
    """Get trending/popular videos."""
    from core.youtube_api import get_most_popular, search_trending
    data = request.json
    query = data.get("query", "")
    region = data.get("region", "US")
    category = data.get("category_id", "")
    key = data.get("yt_api_key") or YOUTUBE_API_KEY
    if not key:
        return jsonify({"error": "YouTube API key not set"}), 400
    
    if query:
        from datetime import timedelta
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        videos = search_trending(query, key, max_results=25,
                                 published_after=week_ago, region=region,
                                 category_id=category or None)
    else:
        videos = get_most_popular(key, region=region,
                                  category_id=category or None, max_results=25)
    
    return jsonify({"videos": videos, "query": query, "region": region})

@app.route("/api/youtube/compare", methods=["POST"])
def yt_compare():
    """Compare multiple channels."""
    from core.youtube_api import compare_channels
    data = request.json
    channels = data.get("channels", [])
    key = data.get("yt_api_key") or YOUTUBE_API_KEY
    if not key:
        return jsonify({"error": "YouTube API key not set"}), 400
    
    results = compare_channels(channels, key)
    return jsonify({"comparisons": results})

@app.route("/api/youtube/newborn_virals", methods=["POST"])
def yt_newborn_virals():
    """Scans for extremely viral videos from NEW or SMALL channels (Newborn Virals)."""
    import requests as _req
    from datetime import datetime, timedelta
    data = request.json
    query = data.get("query", "")
    key = data.get("yt_api_key") or YOUTUBE_API_KEY
    if not key:
        return jsonify({"error": "YouTube API key not set"}), 400
    
    # Search for highly viewed videos in the last 40 days
    published_after = (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    search_url = "https://www.googleapis.com/youtube/v3/search"
    search_params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": "viewCount",
        "maxResults": 30,
        "publishedAfter": published_after,
        "key": key
    }
    
    try:
        sr = _req.get(search_url, params=search_params, timeout=10).json()
        if "error" in sr:
            return jsonify({"error": sr["error"].get("message", "Unknown error")}), 400
        
        video_ids = [i["id"]["videoId"] for i in sr.get("items", []) if "videoId" in i.get("id", {})]
        if not video_ids:
            return jsonify({"virals": [], "query": query})
            
        # Get video stats to calculate VPH
        stats_r = _req.get("https://www.googleapis.com/youtube/v3/videos", params={
            "part": "snippet,statistics", "id": ",".join(video_ids), "key": key
        }).json()
        
        videos_data = []
        channel_ids = []
        for item in stats_r.get("items", []):
            snip = item.get("snippet", {})
            stat = item.get("statistics", {})
            cid = snip.get("channelId", "")
            
            pub = snip.get("publishedAt", "")
            views = int(stat.get("viewCount", 0))
            hours_ago = 1
            try:
                pub_dt = datetime.strptime(pub[:19], "%Y-%m-%dT%H:%M:%S")
                hours_ago = max(1, (datetime.utcnow() - pub_dt).total_seconds() / 3600)
            except: pass
            
            videos_data.append({
                "video_id": item["id"],
                "title": snip.get("title", ""),
                "channel_id": cid,
                "channel_name": snip.get("channelTitle", ""),
                "views": views,
                "vph": round(views / hours_ago, 1),
                "published": pub[:10],
                "thumbnail": snip.get("thumbnails", {}).get("medium", {}).get("url", "")
            })
            if cid and cid not in channel_ids:
                channel_ids.append(cid)
                
        # Get channel stats to find NEW / SMALL channels
        channels_info = {}
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i:i+50]
            ch_r = _req.get("https://www.googleapis.com/youtube/v3/channels", params={
                "part": "snippet,statistics", "id": ",".join(batch), "key": key
            }).json()
            for ch in ch_r.get("items", []):
                stat = ch.get("statistics", {})
                snip = ch.get("snippet", {})
                channels_info[ch["id"]] = {
                    "subscribers": int(stat.get("subscriberCount", 0)),
                    "video_count": int(stat.get("videoCount", 0)),
                    "published_at": snip.get("publishedAt", "")[:10],
                    "thumbnail": snip.get("thumbnails", {}).get("default", {}).get("url", "")
                }
        
        # Filter newborn virals (few videos OR created recently, high VPH)
        newborns = []
        for v in videos_data:
            c = channels_info.get(v["channel_id"])
            if not c: continue
            
            # Logic: If video count is low (< 30) AND VPH is solid (> 50)
            if c["video_count"] <= 35 and v["vph"] >= 50:
                v["channel_stats"] = c
                newborns.append(v)
                
        newborns.sort(key=lambda x: x["vph"], reverse=True)
        return jsonify({"virals": newborns, "query": query})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/strategy_from_viral", methods=["POST"])
def api_strategy_from_viral():
    data = request.json
    video_title = data.get("title", "")
    niche = data.get("niche", "")
    language = data.get("language", "English")
    
    prompt = f"""You are a master YouTube strategist. The user found a VIRAL NEWBORN video with this title:
    
VIRAL TITLE: "{video_title}"
NICHE CONTEXT: "{niche}"
TARGET LANGUAGE: {language}

This title just went viral for a small channel. We want to extract its DNA and remix it for our channel.

Provide:
1. THE ANGLE EXPLAINED: Why exactly did this video go viral? What psychological triggers were used?
2. 3 NEW SUBNICHES: Give 3 different subniches/topics where this exact SAME psychological angle would work perfectly.
3. 9 REMIXED TITLES: Create 3 viral titles for EACH of the 3 new subniches (Total 9 titles).
   - They MUST mimic the structure and emotion of the original viral title.
   - They MUST be 70-100 characters long.
   - Use CAPS for power words.
"""
    result = ask_gemini(prompt, request.json.get("ai_api_key"))
    return jsonify({"strategy": result, "original_title": video_title})

# =============================================
# MY CHANNELS — Save & analyze your channels
# =============================================
CHANNELS_FILE = os.path.join(BASE_DIR, "channels.json")

def _load_channels():
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def _save_channels(channels):
    with file_lock:
        with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
            json.dump(channels, f, indent=2, ensure_ascii=False)

@app.route("/api/channels", methods=["GET"])
def get_channels():
    return jsonify({"channels": _load_channels()})

@app.route("/api/channels/add", methods=["POST"])
def add_channel():
    data = request.json
    channels = _load_channels()
    channel = {
        "id": len(channels) + 1,
        "name": data.get("name", ""),
        "url": data.get("url", ""),
        "niche": data.get("niche", ""),
        "micro_niche": data.get("micro_niche", ""),
        "subniches": data.get("subniches", []),
        "keywords": data.get("keywords", []),
        "language": data.get("language", "English"),
        "titles": data.get("titles", []),
        "reference_structures": data.get("reference_structures", []),
        "trending_themes": data.get("trending_themes", []),
        "metrics": [],
        "created": datetime.now().isoformat(),
    }
    channels.append(channel)
    _save_channels(channels)
    return jsonify({"status": "ok", "channel": channel})

@app.route("/api/channels/delete", methods=["POST"])
def delete_channel():
    data = request.json
    cid = data.get("id")
    channels = [c for c in _load_channels() if c.get("id") != cid]
    _save_channels(channels)
    return jsonify({"status": "ok"})

@app.route("/api/channels/analyze", methods=["POST"])
def analyze_channel():
    """AI analysis — discovers NEW subniches by combining validated trends + validated structures."""
    data = request.json
    channel = data.get("channel", {})
    reference_structures = data.get("reference_structures", [])
    trending_themes = data.get("trending_themes", [])
    
    ref_text = "\n".join(f"- {s}" for s in reference_structures[:15]) if reference_structures else "None provided yet"
    trend_text = "\n".join(f"- {t}" for t in trending_themes[:10]) if trending_themes else "Use your knowledge of current YouTube trends"
    titles_text = "\n".join(f"- {t}" for t in channel.get("titles", [])[:20]) if channel.get("titles") else "None"
    metrics_text = json.dumps(channel.get("metrics", [])[:10], indent=2) if channel.get("metrics") else "No metrics yet"
    
    prompt = f"""You are the world's #1 YouTube channel strategist. You specialize in finding UNTAPPED subniches.

=== CHANNEL DNA ===
NAME: {channel.get('name', '')}
URL: {channel.get('url', '')}
CURRENT NICHE: {channel.get('niche', '')}
CURRENT MICRO-NICHE: {channel.get('micro_niche', '')}
SUBNICHES BEING USED: {', '.join(channel.get('subniches', []))}
KEYWORDS: {', '.join(channel.get('keywords', []))}
LANGUAGE: {channel.get('language', 'English')}

=== EXISTING TITLES ===
{titles_text}

=== REFERENCE TITLE STRUCTURES (from successful channels) ===
{ref_text}

=== TRENDING THEMES IN THE MARKET ===
{trend_text}

=== PERFORMANCE METRICS ===
{metrics_text}

=== YOUR MISSION ===

**STEP 1: CHANNEL DNA ANALYSIS**
- Identify the channel's EXACT DNA: what themes work, what structures get clicks
- Analyze what's working and what's NOT based on metrics (if available)

**STEP 2: NEW SUBNICHE DISCOVERY** (THIS IS THE KEY)
The goal is NOT to copy existing subniches. The goal is to CREATE NEW ONES.

METHOD:
1. Take a VALIDATED TRENDING THEME (e.g., "construction" is trending)
2. Take a VALIDATED TITLE STRUCTURE (e.g., "Why Nobody Lives In..." works)
3. CHANGE THE SUBNICHE to something NEW with the same theme
   - Example: Construction is trending → "cities" is the obvious subniche (too much competition)
   - NEW subniche: "underground bunkers", "underwater tunnels", "impossible bridges"
   - Same theme (construction), same validated structure, but DIFFERENT angle = less competition

For each new subniche:
- Name it precisely
- Explain WHY it has demand but low supply
- Show 3 viral titles using VALIDATED structures (max 100 chars each)
- Estimated competition level (low/medium/high)
- Target audience pain point

**STEP 3: TITLE OPTIMIZATION**
- Take the validated structures and adapt them to each new subniche
- Every title MUST be 60-100 characters
- Use 1-2 CAPS words per title
- Use emotional triggers

**STEP 4: WEEKLY ACTION PLAN**
Based on the analysis, suggest:
- 5 video topics for THIS WEEK
- Which subniche to prioritize
- What title structure to use for each

Provide 5 NEW subniches with 3 titles each.
Be extremely specific. No generic advice. Real differentiated opportunities."""

    result = ask_gemini(prompt, request.json.get("ai_api_key"))
    return jsonify({"analysis": result, "channel": channel})

@app.route("/api/channels/update_metrics", methods=["POST"])
def update_channel_metrics():
    """Update channel with performance metrics for AI analysis."""
    data = request.json
    channel_id = data.get("id")
    metrics = data.get("metrics", {})  # {title, views, ctr, likes, comments}
    
    channels = _load_channels()
    for c in channels:
        if c.get("id") == channel_id:
            if "metrics" not in c:
                c["metrics"] = []
            c["metrics"].append({
                **metrics,
                "date": datetime.now().isoformat(),
            })
            # Keep last 50 entries
            c["metrics"] = c["metrics"][-50:]
            break
    
    _save_channels(channels)
    return jsonify({"status": "ok"})

@app.route("/api/channels/update", methods=["POST"])
def update_channel():
    """Update channel data (add new structures, themes, titles, etc.)."""
    data = request.json
    channel_id = data.get("id")
    updates = data.get("updates", {})
    
    channels = _load_channels()
    for c in channels:
        if c.get("id") == channel_id:
            # Merge arrays (don't replace, add new items)
            for key in ["subniches", "keywords", "titles"]:
                if key in updates and updates[key]:
                    existing = c.get(key, [])
                    new_items = [i for i in updates[key] if i not in existing]
                    c[key] = existing + new_items
            # Replace simple fields
            for key in ["niche", "micro_niche", "name", "url", "language"]:
                if key in updates and updates[key]:
                    c[key] = updates[key]
            # Add reference structures
            if "reference_structures" in updates:
                existing = c.get("reference_structures", [])
                new_items = [s for s in updates["reference_structures"] if s not in existing]
                c["reference_structures"] = existing + new_items
            # Add trending themes
            if "trending_themes" in updates:
                existing = c.get("trending_themes", [])
                new_items = [t for t in updates["trending_themes"] if t not in existing]
                c["trending_themes"] = existing + new_items
            c["last_updated"] = datetime.now().isoformat()
            break
    
    _save_channels(channels)
    return jsonify({"status": "ok"})

# =============================================
# AI ENGINE STATUS & DIAGNOSTICS
# =============================================
@app.route("/api/ai/status")
def ai_status():
    """Get current AI engine status (models, cooldowns, cache)."""
    return jsonify(get_model_status())

@app.route("/api/ai/reset", methods=["POST"])
def ai_reset():
    """Reset all AI model cooldowns and cache."""
    clear_cooldowns()
    from core.ai_engine import clear_cache
    clear_cache()
    return jsonify({"status": "ok", "message": "All cooldowns and cache cleared"})

# =============================================
# STARTUP
# =============================================
def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://localhost:5050")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  TitlePilot Pro — Viral Title Analysis Engine")
    print("  Multi-AI Engine: 3 Gemini models with failover")
    print("  http://localhost:5050")
    print("="*50 + "\n")
    
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5050, debug=False)

