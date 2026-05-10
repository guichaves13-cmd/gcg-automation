"""
TitlePilot Pro — Backend Server
Premium viral title analysis with Gemini AI + YouTube Data API.
"""
import os, sys, json, re, time, math, webbrowser, threading
from datetime import datetime
from collections import Counter
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS

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
# GEMINI AI ENGINE
# =============================================
_gemini_client = None

def get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    return _gemini_client

def ask_gemini(prompt, max_retries=2):
    """Send prompt to Gemini with retry logic and model fallback."""
    models = ["gemini-2.5-flash", "gemini-2.0-flash"]
    for model in models:
        for attempt in range(max_retries + 1):
            try:
                client = get_gemini()
                r = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
                return r.text if r and r.text else ""
            except Exception as e:
                err = str(e).lower()
                if "429" in err or "quota" in err or "rate" in err:
                    time.sleep(3 * (attempt + 1))
                elif "404" in err or "not found" in err:
                    break  # Try next model
                else:
                    if attempt == max_retries:
                        break  # Try next model
                    time.sleep(2)
    return "[AI temporarily unavailable - rate limited]"

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
    
    # Length scoring
    if len(title) < 40:
        result["issues"].append("Too short — aim for 70-100 characters")
    elif len(title) > 100:
        result["issues"].append("Too long — YouTube truncates after ~100 chars")
    elif 65 <= len(title) <= 95:
        result["score"] += 12
    elif 50 <= len(title) <= 100:
        result["score"] += 8
    
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
    
    prompt = f"""You are the world's best YouTube title strategist. You have analyzed millions of viral videos.

TOPIC: {topic}
LANGUAGE: {language}
{"NICHE CONTEXT: " + niche if niche else ""}

Generate exactly 15 viral YouTube title variants for this topic.

MANDATORY RULES:
1. Every title MUST be between 60-100 characters (STRICT LIMIT)
2. Every title MUST use at least ONE viral structure:
   - Curiosity Gap: "Why Nobody...", "The Real Reason...", "What They Don't Tell..."
   - Superlative: "The Most Dangerous...", "The Deadliest...", "The Worst..."  
   - Authority + Emotion: "Why Scientists Are TERRIFIED...", "NASA Just Discovered..."
   - Specific Numbers: "$3.2 Billion...", "15 Things That..."
   - Forbidden: "The FORBIDDEN...", "Why This Was BANNED..."
   - Permanence: "...That Will NEVER Be Repeated", "...FOREVER Changed"
3. Use 1-2 ALL CAPS words per title (TERRIFYING, NEVER, FORBIDDEN, etc.)
4. Use emotional trigger words (deadly, shocking, terrifying, forbidden, etc.)
5. Be SPECIFIC — avoid generic phrasing
6. Make the viewer UNABLE to NOT click

IMPORTANT: Return ONLY the 15 titles, one per line, numbered 1-15. No explanations."""

    result = ask_gemini(prompt)
    
    # Parse titles
    titles = []
    for line in result.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Remove numbering
        line = re.sub(r'^\d+[\.\)\-\s]+', '', line).strip()
        line = line.strip('"\'')
        if line and len(line) > 10:
            analysis = analyze_title(line)
            titles.append({
                "title": line,
                "length": len(line),
                "score": analysis["score"],
                "grade": analysis["grade"],
                "structures": [s["name"] for s in analysis["structures"]],
            })
    
    titles.sort(key=lambda x: x["score"], reverse=True)
    return jsonify({"titles": titles, "topic": topic})

@app.route("/api/subniche", methods=["POST"])
def api_subniche():
    data = request.json
    theme = data.get("theme", "")
    language = data.get("language", "English")
    
    # STEP 1: Get REAL trending videos from YouTube
    trending_videos = []
    yt_key = YOUTUBE_API_KEY
    
    if yt_key and theme:
        try:
            from core.youtube_api import search_trending
            from datetime import timedelta
            
            week_ago = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
            videos = search_trending(theme, yt_key, max_results=30,
                                     published_after=week_ago, region="US")
            
            if videos:
                for v in videos:
                    trending_videos.append({
                        "title": v.get("title", ""),
                        "channel": v.get("channel_title", v.get("channel", "")),
                        "channel_id": v.get("channel_id", ""),
                        "subs": v.get("channel_subs", 0),
                        "views": v.get("views", 0),
                        "likes": v.get("likes", 0),
                        "vph": round(v.get("vph", 0), 1),
                        "days_ago": v.get("days_ago", 0),
                        "duration_text": v.get("duration_text", ""),
                        "video_id": v.get("id", ""),
                        "engagement": v.get("engagement", 0),
                    })
                
                trending_videos.sort(key=lambda x: x["vph"], reverse=True)
                trending_videos = trending_videos[:20]
        except Exception as e:
            print(f"  [subniche] YouTube error: {e}")
    
    # STEP 2: Use Gemini to analyze trends and suggest subniches
    yt_context = ""
    if trending_videos:
        yt_context = "\nREAL YOUTUBE DATA (last 14 days, sorted by VPH):\n"
        for i, v in enumerate(trending_videos[:15], 1):
            yt_context += f'{i}. \"{v["title"]}\" by {v["channel"]} ({v["subs"]:,} subs) - {v["views"]:,} views, {v["vph"]} VPH\n'
    
    prompt = f"""You are the world's #1 YouTube niche strategist.

SEARCH THEME: {theme}
LANGUAGE: {language}
{yt_context}

Based on the REAL trending videos above, suggest 6 NEW subniches/perspectives.
These should be FRESH angles that nobody is doing yet, inspired by what's trending.

For each subniche:
1. NAME (specific new angle)
2. DEMAND (1-10)
3. SUPPLY (1-10)  
4. OPPORTUNITY (demand minus supply)
5. WHY_NOW (why this is trending based on the data)
6. REFERENCE_CHANNEL (from the real data above)
7. TARGET_AUDIENCE
8. CONTENT_ANGLE
9. 3 EXAMPLE_TITLES (60-100 chars each, viral structures)
10. 2 TITLE_STRUCTURES (reusable formulas)
11. KEYWORDS

Return JSON array:
[{{"name":"","demand":9,"supply":2,"opportunity":7,"why_now":"","reference_channel":"","target_audience":"","content_angle":"","example_titles":["","",""],"title_structures":["",""],"keywords":["","",""],"estimated_views":"50K-200K"}}]

Only valid JSON. No markdown."""
    
    result = ask_gemini(prompt)
    
    try:
        match = re.search(r'\[.*\]', result, re.DOTALL)
        niches = json.loads(match.group()) if match else json.loads(result)
        niches.sort(key=lambda x: x.get("opportunity", 0), reverse=True)
    except:
        niches = []
    
    return jsonify({
        "videos": trending_videos,
        "niches": niches,
        "theme": theme,
        "youtube_active": bool(trending_videos),
    })

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
    """AI-powered channel strategy with REAL YouTube data."""
    data = request.json
    channel_type = data.get("channel_type", "")
    current_titles = data.get("titles", [])
    target_audience = data.get("target_audience", "")
    language = data.get("language", "English")
    
    titles_text = "\n".join(f"- {t}" for t in current_titles[:20]) if current_titles else "No titles provided"
    
    # STEP 1: Search YouTube for REAL trending data in this niche
    yt_context = ""
    viral_videos = []
    viral_structures = []
    yt_key = YOUTUBE_API_KEY
    
    if yt_key and channel_type:
        try:
            from core.youtube_api import search_trending
            from datetime import timedelta
            
            week_ago = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
            videos = search_trending(channel_type, yt_key, max_results=30,
                                     published_after=week_ago, region="US")
            
            if videos:
                # Sort by VPH
                videos.sort(key=lambda v: v.get("vph", 0), reverse=True)
                
                for v in videos[:20]:
                    viral_videos.append({
                        "title": v.get("title", ""),
                        "channel": v.get("channel_title", v.get("channel", "")),
                        "subs": v.get("channel_subs", 0),
                        "views": v.get("views", 0),
                        "vph": round(v.get("vph", 0), 1),
                        "days_ago": v.get("days_ago", 0),
                    })
                
                # Extract title structures from viral videos
                top_titles = [v["title"] for v in viral_videos[:15]]
                
                yt_context = "\nREAL YOUTUBE TRENDING DATA (last 14 days):\n"
                for i, v in enumerate(viral_videos[:15], 1):
                    yt_context += f'{i}. \"{v["title"]}\" by {v["channel"]} ({v["subs"]:,} subs) - {v["views"]:,} views, {v["vph"]} VPH\n'
                
                yt_context += "\nTITLE STRUCTURES CURRENTLY WORKING (extracted from viral videos above):\n"
                yt_context += "Analyze the patterns in those titles and identify which structures drive the most VPH.\n"
        except Exception as e:
            print(f"  [strategy] YouTube error: {e}")
    
    prompt = f"""You are a YouTube growth strategist who has scaled channels from 0 to 1M subscribers.
You have access to REAL trending YouTube data below. Use it to create a DATA-DRIVEN strategy.

CHANNEL NICHE/THEME: {channel_type}
TARGET AUDIENCE: {target_audience}
LANGUAGE: {language}

CURRENT TITLES (if any):
{titles_text}
{yt_context}

PROVIDE A COMPLETE DATA-DRIVEN STRATEGY:

1. TRENDING ANALYSIS
   - What themes are exploding RIGHT NOW based on the YouTube data above?
   - Which channels are winning and WHY?
   - What VPH patterns indicate opportunity?

2. VALIDATED TITLE STRUCTURES (from real data)
   - Extract the 5 best title structures from the viral videos above
   - For each: the formula, why it works, VPH performance
   - Example: "Why [Authority] NEVER [Action] [Topic]" -> avg 400 VPH

3. NEW PERSPECTIVES & SUBNICHES
   - 5 FRESH angles that nobody is doing yet in this theme
   - Each must be inspired by what's trending but with a NEW twist
   - For each: name, why it would work, 2 title examples using validated structures

4. CONTENT PILLARS (4 types of videos)
   For each: name, why it works, 2 title examples (60-100 chars), expected VPH

5. GROWTH ROADMAP
   - First 10 videos (with full titles using validated structures)
   - Posting schedule
   - Month 1-3 strategy
   - Month 3-6 scaling

6. TOP 15 VIDEO IDEAS (ready to produce)
   - Full titles using VALIDATED structures from the data
   - 60-100 chars each
   - Why each would perform based on trending data

Be EXTREMELY specific. Use the REAL data. No generic advice."""
    
    result = ask_gemini(prompt)
    return jsonify({
        "strategy": result,
        "channel_type": channel_type,
        "viral_videos": viral_videos[:10],
        "youtube_data_used": bool(yt_context),
    })

@app.route("/api/trend_scanner", methods=["POST"])
def api_trend_scanner():
    """Scan for trending topics and emerging niches."""
    data = request.json
    category = data.get("category", "all")
    language = data.get("language", "English")
    
    prompt = f"""You are a YouTube trend analyst with access to the latest data.

CATEGORY: {category if category != 'all' else 'All categories'}
LANGUAGE: {language}
DATE: {datetime.now().strftime('%B %Y')}

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

    result = ask_gemini(prompt)
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
    key = YOUTUBE_API_KEY
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
    
    # VPH multiplier for each video
    for v in videos:
        v["vph_multiplier"] = round(v["vph"] / max(avg_vph, 0.1), 1)
    
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
    key = YOUTUBE_API_KEY
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
    key = YOUTUBE_API_KEY
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
    key = YOUTUBE_API_KEY
    if not key:
        return jsonify({"error": "YouTube API key not set"}), 400
    
    results = compare_channels(channels, key)
    return jsonify({"comparisons": results})

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

    result = ask_gemini(prompt)
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
# STARTUP
# =============================================
def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://localhost:5050")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  TitlePilot Pro — Viral Title Analysis Engine")
    print("  http://localhost:5050")
    print("="*50 + "\n")
    
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5050, debug=False)
