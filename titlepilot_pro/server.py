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

def ask_gemini(prompt, user_api_key=None, max_retries=None, image_b64=None):
    """Send prompt to AI with automatic model failover.
    Uses 3 Gemini models with smart rotation on rate limits.
    """
    key = user_api_key if user_api_key and len(user_api_key) > 10 else GOOGLE_API_KEY
    return _ask_ai_engine(prompt, api_key=key or None, max_retries=max_retries, image_b64=image_b64)

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
    "absolute_negative": {
        "pattern": r"(?:never|stop|don't|quit|avoid|ruin|destroy|fail|fake|lie)",
        "name": "Absolute Negative",
        "ctr_boost": 1.35,
        "desc": "Negative framing prevents viewers from making mistakes",
    },
    "timeline_urgency": {
        "pattern": r"(?:in the last|just|finally|minutes|seconds|imminent|too late|now|before it's too late)",
        "name": "Timeline Urgency",
        "ctr_boost": 1.30,
        "desc": "Creates immediate FOMO (Fear Of Missing Out)",
    },
    "antagonist": {
        "pattern": r"(?:vs|versus|against|enemy|villain|monster|predator|killer|scam|fraud)",
        "name": "Antagonist / Conflict",
        "ctr_boost": 1.35,
        "desc": "Conflict naturally drives human curiosity",
    },
    "scientific_breakthrough": {
        "pattern": r"(?:discovered|solved|proved|breakthrough|unlocked|found|revealed)",
        "name": "Scientific Breakthrough",
        "ctr_boost": 1.25,
        "desc": "Satisfies intellectual curiosity and truth-seeking",
    },
}

EMOTIONAL_WORDS = {
    # TIER 1 - Extreme (Score 9-10)
    "terrifying": 10, "deadly": 10, "forbidden": 10, "brutal": 10, "horrifying": 10, 
    "terrified": 10, "fatal": 10, "doomed": 9, "banned": 10, "chilling": 9, "illegal": 9,
    # TIER 2 - High (Score 7-8)
    "shocking": 8, "insane": 8, "dangerous": 8, "secret": 8, "impossible": 8, 
    "cursed": 8, "savage": 8, "catastrophic": 8, "nightmare": 8, "destroyed": 8,
    "unstoppable": 8, "speechless": 8, "unbelievable": 7, "hidden": 7, "extreme": 7, 
    "mysterious": 7, "haunted": 7, "abandoned": 7, "genius": 7, "bizarre": 7, "creepy": 7,
    # TIER 3 - Medium (Score 5-6)
    "incredible": 6, "ancient": 6, "legendary": 6, "massive": 6, "epic": 6, 
    "weird": 5, "strange": 5, "lost": 6, "unsolved": 6, "dark": 5, "wild": 5,
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

@app.route("/api/ab_battle", methods=["POST"])
def api_ab_battle():
    data = request.json
    title_a = data.get("title_a", "")[:300]
    title_b = data.get("title_b", "")[:300]
    language = data.get("language", "English")[:50]
    
    if not title_a or not title_b:
        return jsonify({"error": "Both titles must be provided for a battle."}), 400
        
    prompt = f"""You are the ultimate 2026 YouTube Algorithm Simulator.
Two titles are fighting for impressions on the homepage. Evaluate them head-to-head based on high-CTR psychological triggers (Curiosity Gap, Negative Framing, Fear of Missing Out, Professional Villains, Concrete Data).

Title A: "{title_a}"
Title B: "{title_b}"
Language: {language}

Output MUST be a valid JSON with the following schema exactly (no markdown formatting, no backticks, just the raw JSON object):
{{
  "winner": "A or B",
  "ctr_delta": "+X% CTR difference prediction",
  "reasoning": "A concise paragraph explaining why the winner commands more attention and clicks.",
  "breakdown_a": "Strengths/weaknesses of A",
  "breakdown_b": "Strengths/weaknesses of B"
}}
"""
    try:
        result = ask_gemini(prompt, request.json.get("ai_api_key"))
        import re
        
        # Strip markdown fences if present
        clean_result = result.replace("```json", "").replace("```", "").strip()
        
        match = re.search(r'\{.*\}', clean_result, re.DOTALL)
        if match:
            return jsonify({"battle": json.loads(match.group())})
        return jsonify({"battle": json.loads(clean_result)})
    except Exception as e:
        return jsonify({"error": f"AI Battle Failed: {str(e)}"}), 500

@app.route("/api/thumb_prompt", methods=["POST"])
def api_thumb_prompt():
    data = request.json
    title = data.get("title", "")[:300]
    
    if not title:
        return jsonify({"error": "Title required for thumbnail generation."}), 400
        
    prompt = f"""You are an elite YouTube Thumbnail Director in 2026.
You are tasked with designing the perfect, high-CTR thumbnail for this title: "{title}"

Generate 3 distinct Midjourney/Ideogram v2 image generation prompts. The thumbnails must strictly follow these rules:
1. Low visual clutter (max 3 elements)
2. High contrast, cinematic lighting, dramatic mood
3. Must perfectly complement the title by leaving a "Curiosity Gap" (don't show the answer, show the mystery/danger)

Output MUST be a valid JSON with the following schema exactly (no markdown formatting, no backticks, just the raw JSON object):
{{
  "prompts": [
    {{
      "style": "e.g., Hyper-realistic Documentary",
      "visuals": "Describe exactly what is shown (e.g., 'Close up of a giant shadow underwater, dark blue lighting...')",
      "text_overlay": "Optional 1-3 words of text for the thumbnail",
      "midjourney_prompt": "The exact English prompt string for Midjourney v6 (e.g., 'Hyper realistic photo of a giant shadow... --ar 16:9 --v 6.0')"
    }},
    ... (2 more)
  ]
}}
"""
    try:
        result = ask_gemini(prompt, request.json.get("ai_api_key"))
        import re
        
        # Strip markdown fences if present
        clean_result = result.replace("```json", "").replace("```", "").strip()
        
        match = re.search(r'\{.*\}', clean_result, re.DOTALL)
        if match:
            return jsonify({"thumbs": json.loads(match.group())})
        return jsonify({"thumbs": json.loads(clean_result)})
    except Exception as e:
        return jsonify({"error": f"AI Thumb Gen Failed: {str(e)}"}), 500

@app.route("/api/vision_audit", methods=["POST"])
def api_vision_audit():
    data = request.json
    image_b64 = data.get("image", "")
    title = data.get("title", "")[:200]
    
    if not image_b64:
        return jsonify({"error": "No image provided"}), 400
        
    prompt = f"""You are the world's most brutal YouTube Thumbnail Designer and CTR Expert.
I am providing you with a YouTube thumbnail image.

Title Context: "{title}"

Analyze this thumbnail rigorously. Provide EXACTLY the following structure using Markdown:

### 🖼️ Instant Visual Impression (0.5s Rule)
[Can a viewer understand the thumbnail in 0.5 seconds? Is it too cluttered?]

### 🎯 Composition & Focal Point
[What is the main subject? Does it stand out? Is the Rule of Thirds respected?]

### 🎨 Color Psychology & Contrast
[Do the colors pop? Is there good contrast between the foreground and background?]

### 🔤 Typography & Readability (if applicable)
[Is the text legible on a small mobile screen? Are there too many words? Max 3 words recommended.]

### 💡 The "Curiosity Gap" Verdict
[Does this image actually make someone want to click? Why or why not?]

### 🛠️ 3 Brutal Suggestions to Improve CTR
1. [Suggestion 1]
2. [Suggestion 2]
3. [Suggestion 3]

Be highly critical. If it looks amateur, say exactly why."""

    try:
        # Strip header (e.g. data:image/jpeg;base64,...)
        if "," in image_b64:
            image_b64 = image_b64.split(",")[1]
            
        result = ask_gemini(prompt, request.json.get("ai_api_key"), image_b64=image_b64)
        return jsonify({"audit": result})
    except Exception as e:
        return jsonify({"error": f"Vision Audit failed: {str(e)}"}), 500


@app.route("/api/analyze_hook", methods=["POST"])
def api_analyze_hook():
    data = request.json
    hook_script = data.get("hook", "")[:2500]  # Max ~500 words
    video_title = data.get("title", "")[:300]
    language = data.get("language", "English")[:50]
    
    if not hook_script or len(hook_script) < 20:
        return jsonify({"error": "Script is too short to analyze."}), 400
        
    prompt = f"""You are an elite YouTube Retention Architect who has studied thousands of videos with >70% AVD (Average View Duration).
Your goal is to tear apart the FIRST 30 SECONDS (the hook) of this script and rebuild it for maximum psychological grip.

VIDEO TITLE: "{video_title}"
TARGET LANGUAGE: {language}

SCRIPT HOOK:
"{hook_script}"

Do a brutal, surgical analysis of why this hook will succeed or fail. Look for pacing issues, lack of pattern interrupts, missing visual cues, and weak curiosity loops.

Provide EXACTLY the following structure (Use Markdown formatting, no code blocks):

### 📊 Predicted 30s Retention: [XX]%

### 🧠 Psychological Grip Analysis
- **Curiosity Loop:** [Is the core question established immediately?]
- **Pacing & Rhythm:** [Is it too slow? Too fast? Too much exposition?]
- **The "So What" Factor:** [Why should the viewer care right now?]

### ⚠️ Retention Killers (Drop-off Points)
- [Identify the exact sentence where viewers will click away and why]
- [Identify wasted words or boring context]

### ✍️ The "Viral" Re-Write (Script + B-Roll)
[Rewrite the hook to be incredibly punchy, visceral, and fast-paced. Format it as a two-column table or clear list showing AUDIO (What to say) and VISUAL (What to show on screen to create pattern interrupts).]

### 💡 Secret Sauce
[1 advanced psychological tip to hold attention from second 30 to second 60]
"""
    result = ask_gemini(prompt, request.json.get("ai_api_key"))
    return jsonify({"analysis": result})

@app.route("/api/seo_optimize", methods=["POST"])
def api_seo_optimize():
    data = request.json
    title = data.get("title", "")[:300]
    context = data.get("context", "")[:1500]
    language = data.get("language", "English")[:50]
    
    if not title:
        return jsonify({"error": "Title is required"}), 400
        
    prompt = f"""You are an elite YouTube SEO & Algorithm Expert who has ranked hundreds of videos on page 1 of YouTube Search.
Your goal is to write the ultimate YouTube metadata package to trigger algorithmic recommendations and search discovery.

VIDEO TITLE: "{title}"
VIDEO CONTEXT/HOOK: "{context}"
TARGET LANGUAGE: {language}

Provide EXACTLY the following structure (Use Markdown formatting):

### 📝 Optimized Description (The "Above The Fold" 3 Lines)
[Write the critical first 3 lines of the description. This is what shows above the "Show More" button. It MUST contain the primary keyword in the first sentence naturally, create curiosity, and establish authority. DO NOT just repeat the title.]

### 📌 Pinned Comment Strategy
[Write exactly what the creator should pin in the comments to drive massive engagement (e.g., a controversial question, a poll, or an extension of the hook).]

### 🕒 Algorithmic Chapters (Timestamps)
0:00 - [Hook/Intro name]
[Suggest 4 to 6 logical timestamp chapters based on the context. Make the chapter titles highly searchable (LSI keywords) but still curiosity-driven.]

### 🏷️ Top 500-Character Tags (Short & Long-Tail)
[Provide a comma-separated list of highly searched tags. Start with broad terms, then niche down into specific long-tail phrases that people actually type. Do not exceed 500 characters total.]

### 💡 Search Ranking Strategy & CTR Boost
[Give 2 specific, aggressive tips on how to push this exact video into YouTube's "Suggested Videos" sidebar based on current 2026 algorithmic trends.]
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
    topic = data.get("topic", "")[:500]
    language = data.get("language", "English")[:50]
    niche = data.get("niche", "")[:300]
    
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
    theme = data.get("theme", "")[:300]
    language = data.get("language", "English")[:50]
    
    prompt = f"""You are a master YouTube Niche Strategist using the "Blue Ocean Strategy". 
Instead of finding saturated markets, you find UNTAPPED intersections and micro-niches where competition is irrelevant.

{"MAIN THEME: " + theme if theme else "Analyze ALL trending YouTube themes."}
TARGET LANGUAGE: {language}

Your task: Find the 8 most PROFITABLE and EXPLOSIVE subniches that have:
- MASSIVE viewer curiosity (people are desperately searching for this)
- ZERO strong creator supply (the current videos are old, boring, or non-existent)
- HIGH RPM potential (topics that attract adult, premium advertisers)
- CROSS-NICHE appeal (e.g., mixing History with Psychology, or Finance with True Crime)

For each subniche provide:
1. SUBNICHE NAME (Be highly specific, e.g., "Financial Disasters of the 1900s" instead of "Finance History")
2. DEMAND LEVEL (1-10): Search volume and curiosity depth
3. SUPPLY LEVEL (1-10): How saturated it is (should be low)
4. OPPORTUNITY SCORE: demand minus supply
5. TARGET AUDIENCE: Psychographics (not just demographics)
6. AUDIENCE PAIN: What specific intellectual itch or problem this solves
7. CONTENT ANGLE: The "Blue Ocean" approach to dominate this space instantly
8. 3 EXAMPLE VIRAL TITLES: Must be 70-100 chars, highly emotional
9. KEYWORDS: 3 highly searched tags
10. ESTIMATED VIEWS PER VIDEO: Realistic viral baseline

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
    channel_type = data.get("channel_type", "")[:200]
    current_titles = data.get("titles", [])
    target_audience = data.get("target_audience", "")[:300]
    language = data.get("language", "English")[:50]
    
    titles_text = "\n".join(f"- {t}" for t in current_titles[:20]) if current_titles else "No titles provided"
    
    prompt = f"""You are a master YouTube Channel Architect who has scaled channels from 0 to 1M+ subscribers in record time.
Your superpower is identifying what everyone else in the niche is doing wrong and exploiting it.

CHANNEL TYPE: {channel_type}
TARGET AUDIENCE: {target_audience}
LANGUAGE: {language}

CURRENT TITLES (if any):
{titles_text}

Provide a MASTERCLASS channel strategy. You MUST use Markdown formatting, bolding, and extreme specificity.

### 🎯 1. THE BLUE OCEAN POSITIONING
- **The Core Identity:** What exact micro-niche should this channel own?
- **The Competitor Gap:** What are the biggest channels in this niche failing to do?
- **The "Unfair Advantage":** What makes this channel's angle impossible to copy?

### 🏛️ 2. THE 4 CONTENT PILLARS
For each of the 4 pillars provide:
- **Pillar Name & Goal:** (e.g., "The 'Expose' Pillar - Built for Virality")
- **The Psychological Hook:** Why it works on human nature
- **2 Example Titles:** (Must be 70-100 characters, extremely optimized)
- **Thumbnail Synergy:** Exactly what the thumbnail should show to compliment the titles

### 💡 3. THE VIRAL PACKAGING FORMULA
- The 3 absolute best title structures for this specific audience.
- 5 "Power Words" that trigger clicks in this specific niche.
- The optimal pacing structure (e.g., "Hook -> Context -> Conflict -> Resolution").

### 🧠 4. AUDIENCE PSYCHOGRAPHICS
- **Demographics:** Age, gender, interests.
- **The Burning Pain Point:** The deep intellectual or emotional itch they need scratched.
- **Retention Anchors:** 3 specific editing/scripting techniques to keep them watching past 3 minutes.

### 📈 5. THE 6-MONTH EXPLOSION ROADMAP
- **Phase 1 (Videos 1-10):** The "Broad Net" strategy (What to publish first).
- **Phase 2 (Months 2-3):** The "Authority" strategy (Doubling down on what works).
- **Phase 3 (Months 4-6):** The "Market Dominance" strategy (Scaling and cross-pollination).

### 🏆 6. TOP 10 IMMEDIATE VIDEO IDEAS
- Provide 10 fully optimized titles (70-100 chars).
- Explain exactly *why* each one is structurally guaranteed to get a high CTR.

Be brutal, highly specific, and actionable. Zero generic advice."""

    result = ask_gemini(prompt)
    return jsonify({"strategy": result, "channel_type": channel_type})

@app.route("/api/strategy_remix", methods=["POST"])
def api_strategy_remix():
    """Generates a dual-path A/B Strategy for titles."""
    data = request.json
    topic = data.get("topic", "")[:300]
    language = data.get("language", "English")[:50]
    path_a = data.get("path_a", "Curiosity & Mystery")[:100]
    path_b = data.get("path_b", "Fear & Consequence")[:100]
    
    prompt = f"""You are the world's #1 YouTube A/B Testing Strategist and Behavioral Psychologist.

TOPIC: {topic}
LANGUAGE: {language}

Your task is to create a "Strategy Remix" (A/B Test) for this topic. You must generate two ENTIRELY DIFFERENT psychological paths to hook the audience.

PATH A: {path_a}
PATH B: {path_b}

For EACH PATH, provide:

### 🧠 1. THE PSYCHOLOGICAL ANGLE
- **The Click Trigger:** What specific human emotion (fear, greed, curiosity, status) is being manipulated here?
- **Why it works:** A 2-sentence explanation of why this angle forces the viewer to click immediately.

### 🖼️ 2. THUMBNAIL SYNERGY RULES
- **Visual Subject:** What exactly must be shown on screen?
- **Lighting & Color Psychology:** (e.g., "Dark and desaturated with a neon red arrow").
- **Text on Thumbnail:** (Maximum 3 words. What should it say?)

### ✍️ 3. 5 VIRAL TITLES (70-100 chars)
- Must be exactly 70-100 characters long.
- Must use 1-2 ALL CAPS power words (e.g., TERRIFYING, NEVER, ILLEGAL).
- Must perfectly compliment the thumbnail without repeating the text on the thumbnail.

### 🏆 4. THE WINNING SCENARIO
- **Use Path A if:** [Describe the exact scenario, channel size, or audience mood where this wins].
- **Use Path B if:** [Describe the exact scenario where this path dominates].

Provide a highly structured, brutal, and aggressive comparison. Your goal is to make the creator understand exactly how to manipulate viewer psychology for maximum CTR."""

    result = ask_gemini(prompt)
    return jsonify({"remix": result, "topic": topic, "path_a": path_a, "path_b": path_b})

@app.route("/api/trend_scanner", methods=["POST"])
def api_trend_scanner():
    """Scan for trending topics and emerging niches."""
    data = request.json
    category = data.get("category", "all")[:200]
    language = data.get("language", "English")[:50]
    
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

@app.route("/api/youtube/sync_channels", methods=["POST"])
def yt_sync_channels():
    """Auto-Sync: Checks if tracked channels posted any explosive new video (High VPH)."""
    from core.youtube_api import _get
    from datetime import datetime
    data = request.json
    channel_ids = data.get("channels", [])
    key = data.get("yt_api_key") or YOUTUBE_API_KEY
    if not key:
        return jsonify({"error": "YouTube API key not set"}), 400
    if not channel_ids:
        return jsonify({"sync_results": []})
        
    try:
        results = []
        for cid in channel_ids[:20]:  # Limit to 20 tracked channels to avoid huge latency
            sr = _get("search", {
                "part": "snippet", "channelId": cid, "type": "video",
                "order": "date", "maxResults": 2
            }, key)
            
            video_ids = [i["id"]["videoId"] for i in sr.get("items", []) if "videoId" in i.get("id", {})]
            if not video_ids: continue
                
            stats_r = _get("videos", {
                "part": "snippet,statistics", "id": ",".join(video_ids)
            }, key)
            
            for item in stats_r.get("items", []):
                snip = item.get("snippet", {})
                stat = item.get("statistics", {})
                pub = snip.get("publishedAt", "")
                views = int(stat.get("viewCount", 0))
                hours_ago = 1
                try:
                    pub_dt = datetime.strptime(pub[:19], "%Y-%m-%dT%H:%M:%S")
                    hours_ago = max(1, (datetime.utcnow() - pub_dt).total_seconds() / 3600)
                except: pass
                
                vph = round(views / hours_ago, 1)
                
                # Only alert if it's a hot video (VPH > 50 or newer than 48h)
                if hours_ago <= 48 and vph > 10:
                    results.append({
                        "channel_id": cid,
                        "channel_name": snip.get("channelTitle", ""),
                        "video_id": item["id"],
                        "title": snip.get("title", ""),
                        "vph": vph,
                        "views": views,
                        "hours_ago": int(hours_ago)
                    })
                    
        results.sort(key=lambda x: x["vph"], reverse=True)
        return jsonify({"sync_results": results})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/youtube/newborn_virals", methods=["POST"])
def yt_newborn_virals():
    """Scans for extremely viral videos from NEW or SMALL channels (Newborn Virals)."""
    from core.youtube_api import _get
    from datetime import datetime, timedelta
    data = request.json
    query = data.get("query", "")
    key = data.get("yt_api_key") or YOUTUBE_API_KEY
    if not key:
        return jsonify({"error": "YouTube API key not set"}), 400
    
    # Search for highly viewed videos in the last 40 days
    published_after = (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    try:
        sr = _get("search", {
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": "viewCount",
            "maxResults": 30,
            "publishedAfter": published_after
        }, key)
        
        video_ids = [i["id"]["videoId"] for i in sr.get("items", []) if "videoId" in i.get("id", {})]
        if not video_ids:
            return jsonify({"virals": [], "query": query})
            
        # Get video stats to calculate VPH
        stats_r = _get("videos", {
            "part": "snippet,statistics", "id": ",".join(video_ids)
        }, key)
        
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
            ch_r = _get("channels", {
                "part": "snippet,statistics", "id": ",".join(batch)
            }, key)
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
    
    prompt = f"""You are a master YouTube Viral Architect. The user just discovered a "Newborn Viral" video (a video that exploded on a very small, new channel).
    
VIRAL TITLE: "{video_title}"
NICHE CONTEXT: "{niche}"
TARGET LANGUAGE: {language}

Your objective is to extract the exact structural and psychological DNA of this title and CLONE IT into 3 different sub-niches.

Provide exactly this Markdown structure:

### 🧬 The Viral DNA Extracted
- **Psychological Trigger:** Why exactly did people click this? (Fear, Greed, Identity, Curiosity gap?)
- **The Structural Syntax:** (e.g., "[Negative Word] + [Subject] + [Consequence]")
- **Thumbnail Hypothesis:** What visual element likely accompanied this title to make it viral?

### 🔄 The Cloning Process (3 New Subniches)
Provide 3 new, unsaturated subniches where this exact same psychological trigger will work perfectly.

**1. [Subniche Name 1]**
- Why this works here: [1 sentence]
- ⚡ Title 1: [Cloned title 70-100 chars]
- ⚡ Title 2: [Cloned title 70-100 chars]
- ⚡ Title 3: [Cloned title 70-100 chars]

**2. [Subniche Name 2]**
- Why this works here: [1 sentence]
- ⚡ Title 1: [Cloned title 70-100 chars]
- ⚡ Title 2: [Cloned title 70-100 chars]
- ⚡ Title 3: [Cloned title 70-100 chars]

**3. [Subniche Name 3]**
- Why this works here: [1 sentence]
- ⚡ Title 1: [Cloned title 70-100 chars]
- ⚡ Title 2: [Cloned title 70-100 chars]
- ⚡ Title 3: [Cloned title 70-100 chars]

Be brutal, aggressive, and highly strategic. Use power words in ALL CAPS."""
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
    
    prompt = f"""You are the ultimate YouTube Trend Hacker and Blue Ocean Strategist. 
Your goal is to extract the exact "DNA" of trending channels and clone their psychological mechanics into UNTAPPED subniches with maximum demand and minimum competition.

=== CHANNEL DNA & CONTEXT ===
NAME: {channel.get('name', '')}
CURRENT NICHE: {channel.get('niche', '')}
CURRENT MICRO-NICHE: {channel.get('micro_niche', '')}
LANGUAGE: {channel.get('language', 'English')}

=== REFERENCE TITLE STRUCTURES (From Trending Competitors) ===
{ref_text}

=== TRENDING THEMES IN THIS NICHE ===
{trend_text}

=== YOUR ALGORITHMIC MISSION ===

**STEP 1: REVERSE ENGINEER TRENDING STRUCTURES**
- Analyze the "Reference Title Structures" provided above.
- Identify the exact psychological trigger that is making these competitor structures go viral (e.g., "The Negative Authority Hook" or "The Hidden Cost Hook").
- Select the 3 most lethal, high-CTR structures from the list.

**STEP 2: BLUE OCEAN SUBNICHE DISCOVERY**
Do NOT recommend generic subniches. You must cross-reference the "Trending Themes" with High Demand / Low Supply logic to invent 5 BRAND NEW, unsaturated subthemes.
- **Rule of Demand/Supply:** The subniche must have a massive existing audience (Demand: 9/10) but almost zero modern, high-quality channels covering it specifically (Supply: 2/10).
- **Example of Cloning:** If the trending structure is "Why Nobody Lives In [Place]", and the trending theme is "Ocean", the new Blue Ocean subniche is "Deep Sea Infrastructure" -> "Why Nobody Dares To Build In The Mariana Trench".

**STEP 3: CLONING THE DNA (THE OUTPUT)**
For each of the 5 new Blue Ocean Subniches, provide:
### 🌊 Subniche #[X]: [Name of Subniche]
- **Market Viability:** Why does this have HIGHEST DEMAND and LOWEST SUPPLY? (Be specific).
- **The Audience Hunger:** What exact question are they desperately searching for?
- **The Cloned Strategy:** How are we applying the trending competitor structures to this new subtheme?

**Generate 3 Viral Titles for this Subniche:**
- MUST use the exact syntax of the trending structures you reverse-engineered.
- MUST be 70-100 characters long.
- 1. [Title 1]
- 2. [Title 2]
- 3. [Title 3]

**STEP 4: IMMEDIATE EXECUTION PLAN**
- Which of these 5 subniches has the absolute highest "Opportunity Score" (Demand minus Supply) right now?
- Give the creator a direct, 3-step instruction on what their next video should be based on this data.

Provide the analysis in beautiful, structured Markdown. Be aggressive, highly strategic, and focus exclusively on data-driven CTR manipulation."""

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

