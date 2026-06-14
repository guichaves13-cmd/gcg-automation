""" 
TitlePilot Pro — Backend Server v2.1
Viral title analysis powered by Groq AI + YouTube Data API.
"""
import os, sys, json, re, time, math, webbrowser, threading
from datetime import datetime
from collections import Counter
import werkzeug
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
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max request

# =============================================
# INPUT SANITIZATION & VALIDATION
# =============================================
MAX_INPUT_LEN = 500

def san(val, maxlen=MAX_INPUT_LEN, default=""):
    """Sanitize a single value: coerce to str, strip tags, truncate."""
    if val is None:
        return default
    s = str(val).strip()
    # Remove HTML/script tags
    s = re.sub(r'<[^>]+>', '', s)
    return s[:maxlen]

def san_list(lst, maxlen=MAX_INPUT_LEN, max_items=50):
    """Sanitize a list of strings."""
    if not isinstance(lst, list):
        return []
    return [san(x, maxlen) for x in lst[:max_items]]

def require(data, *fields):
    """Return (ok, error_response) — check required fields are non-empty."""
    for f in fields:
        if not san(data.get(f, '')):
            return False, jsonify({"error": f"Campo obrigatório ausente: '{f}'"}), 400
    return True, None, None

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, werkzeug.exceptions.HTTPException):
        # Return JSON for HTTP errors too
        return jsonify({"error": f"{e.name}: {e.description}"}), e.code
    import traceback
    err_str = str(e)[:200]
    return jsonify({"error": f"Erro interno: {err_str}"}), 500


# Load API key — suporta modo standalone e integrado
def _load_key():
    # 1. Try core module (works in both modes)
    try:
        from core.api_keys import load_api_key
        key = load_api_key("google_ai")
        if key:
            return key
    except Exception:
        pass
    
    # 2. Try .api_keys.json in same dir (standalone)
    for search_dir in [BASE_DIR, PARENT_DIR]:
        kf = os.path.join(search_dir, ".api_keys.json")
        if os.path.exists(kf):
            try:
                import base64
                with open(kf) as f:
                    data = json.load(f)
                encoded = data.get("gemini") or data.get("google_ai") or ""
                if encoded:
                    return base64.b64decode(encoded).decode()
            except Exception:
                pass
        # Also try without dot prefix
        kf2 = os.path.join(search_dir, "api_keys.json")
        if os.path.exists(kf2):
            try:
                with open(kf2) as f:
                    return json.load(f).get("google_ai", "")
            except Exception:
                pass
    
    # 3. Try environment variable
    return os.environ.get("GEMINI_API_KEY", "")

GOOGLE_API_KEY = _load_key()

# =============================================
# GROQ AI ENGINE (Primary — embedded key, no user config needed)
# =============================================
# Embedded key — friends don't need to configure anything
# Key loaded from embedded config (compiled into exe) or environment variable
try:
    from _embedded_config import GROQ_API_KEY as _GROQ_KEY
except ImportError:
    _GROQ_KEY = os.environ.get("GROQ_API_KEY", "")


def ask_groq(prompt, timeout=90, max_retries=3):
    """Call Groq API — Llama 3.3 70B with automatic retry on rate limits."""
    if not _GROQ_KEY or _GROQ_KEY == "GROQ_KEY_PLACEHOLDER":
        return None
    try:
        from groq import Groq
        import time as _time
        client = Groq(api_key=_GROQ_KEY)

        for attempt in range(max_retries):
            result = [None]
            error = [None]

            def _run():
                try:
                    resp = client.chat.completions.create(
                        model="llama-3.1-8b-instant",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        max_tokens=4096,
                    )
                    result[0] = resp.choices[0].message.content
                except Exception as e:
                    error[0] = e

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=timeout)

            if t.is_alive():
                # Thread timed out — try again or give up
                if attempt < max_retries - 1:
                    _time.sleep(5)
                    continue
                return None

            if error[0]:
                err_str = str(error[0]).lower()
                # Rate limit — wait and retry
                if "429" in err_str or "rate_limit" in err_str or "rate limit" in err_str:
                    wait = min(30, 10 * (attempt + 1))  # 10s, 20s, 30s
                    import re as _re
                    m = _re.search(r'try again in ([\d.]+)s', str(error[0]))
                    if m:
                        wait = min(60, float(m.group(1)) + 2)
                    if attempt < max_retries - 1:
                        _time.sleep(wait)
                        continue
                    return None
                # Auth error — don't retry
                if "401" in err_str or "invalid_api_key" in err_str or "authentication" in err_str:
                    return None
                # Other errors — retry once
                if attempt < max_retries - 1:
                    _time.sleep(3)
                    continue
                return None

            if result[0]:
                return result[0]

        return None
    except Exception as e:
        return None


# =============================================
# GEMINI AI ENGINE (Fallback — only if key configured)
# =============================================
_gemini_client = None

def get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    return _gemini_client

def ask_gemini_fallback(prompt, timeout=90):
    """Gemini fallback — only used if Groq fails and Gemini key is configured."""
    if not GOOGLE_API_KEY:
        return None
    models = ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite", "gemini-2.0-flash"]
    for model in models:
        try:
            result = [None]
            error = [None]
            def _run():
                try:
                    client = get_gemini()
                    r = client.models.generate_content(model=model, contents=prompt)
                    result[0] = r.text if r and r.text else ""
                except Exception as e:
                    error[0] = e
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=timeout)
            if t.is_alive() or error[0]:
                continue
            return result[0]
        except Exception:
            continue
    return None

def ask_gemini(prompt, max_retries=2, timeout=90):
    """Main AI function — tries Groq first, then Gemini fallback."""
    # 1. Try Groq (primary)
    if _GROQ_KEY and _GROQ_KEY != "GROQ_KEY_PLACEHOLDER":
        resp = ask_groq(prompt, timeout=timeout)
        if resp:
            return resp

    # 2. Try Gemini (fallback)
    resp = ask_gemini_fallback(prompt, timeout=timeout)
    if resp:
        return resp

    return "[AI Error: Could not reach AI service. Check your internet connection.]"


def safe_parse_json(text, expected_type="dict"):
    """
    Ultra-robust JSON extractor — handles ALL AI response variations:
    - Markdown code blocks (```json...```)
    - Text before/after JSON
    - Trailing commas, single quotes, unquoted keys
    - Nested structures, multi-line
    expected_type: 'dict' for {...} or 'list' for [...]
    """
    if not text:
        raise ValueError("Empty response from AI")
    if text.startswith("[AI Error"):
        raise ValueError(text)

    def try_parse(s):
        """Attempt json.loads with common fixups."""
        for attempt in [
            s,
            s.replace(",\n}", "\n}").replace(",\n]", "\n]"),  # trailing commas
            re.sub(r",\s*([}\]])", r"\1", s),                  # trailing commas anywhere
        ]:
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
        return None

    def extract_by_brackets(s, open_char, close_char):
        """Find outermost balanced JSON block."""
        start = s.find(open_char)
        if start == -1:
            return None
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(s)):
            c = s[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_str:
                escape = True
                continue
            if c == '"' and not escape:
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == open_char:
                depth += 1
            elif c == close_char:
                depth -= 1
                if depth == 0:
                    return s[start:i+1]
        return None

    # PASS 1: Strip markdown code fences
    cleaned = text.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```\s*$', '', cleaned)
    cleaned = cleaned.strip()

    # PASS 2: Direct parse
    result = try_parse(cleaned)
    if result is not None:
        if expected_type == "list" and isinstance(result, list):
            return result
        if expected_type == "dict" and isinstance(result, dict):
            return result
        if expected_type == "list" and isinstance(result, dict):
            # AI wrapped list in a dict key
            for v in result.values():
                if isinstance(v, list) and len(v) > 0:
                    return v
        if expected_type == "dict" and isinstance(result, list):
            # AI returned [{...}] — unwrap single item
            if len(result) == 1 and isinstance(result[0], dict):
                return result[0]
            # Multiple items — return first dict found
            for item in result:
                if isinstance(item, dict):
                    return item
        return result

    # PASS 3: Bracket-balanced extraction
    open_c = '{' if expected_type == 'dict' else '['
    close_c = '}' if expected_type == 'dict' else ']'
    # Also try the other bracket type as fallback
    for oc, cc in [(open_c, close_c), ('{', '}'), ('[', ']')]:
        extracted = extract_by_brackets(cleaned, oc, cc)
        if extracted:
            result = try_parse(extracted)
            if result is not None:
                return result

    # PASS 4: Aggressive regex extraction
    for pattern in [r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', r'\[.*?\]', r'\{.*?\}']:
        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            result = try_parse(match.group())
            if result is not None:
                return result

    raise ValueError(f"Could not parse AI response as {expected_type}. First 100 chars: {cleaned[:100]}")


# =============================================
# VIRAL ANALYSIS ENGINE — Multi-Language
# =============================================
_VIRAL_STRUCTURES_BY_LANG = {
    "en": {
        "curiosity_gap": {
            "pattern": r"(?:why|how|what|the real reason|the truth|nobody|secret|hidden|no one|they don|didn't|never told|nobody knows)",
            "name": "Curiosity Gap", "ctr_boost": 1.40,
            "desc": "Creates information gap the viewer MUST close",
        },
        "superlative": {
            "pattern": r"(?:\b\w+est\b|most|largest|biggest|deadliest|worst|best|extreme|impossible|insane|craziest|greatest|strongest|fastest|deepest|tallest|oldest|scariest|rarest|strangest|weirdest|richest|poorest)",
            "name": "Superlative", "ctr_boost": 1.35,
            "desc": "Extreme claims that demand attention",
        },
        "specific_number": {
            "pattern": r"(?:\$[\d,.]+|\b\d+\b)",
            "name": "Specific Number", "ctr_boost": 1.30,
            "desc": "Numbers add credibility and specificity",
        },
        "permanence": {
            "pattern": r"(?:never|forever|always|still|remains|eternal|no longer|ended|changed everything|will never)",
            "name": "Permanence Claim", "ctr_boost": 1.25,
            "desc": "Permanence creates urgency and weight",
        },
        "authority_emotion": {
            "pattern": r"(?:scientist|government|nasa|expert|doctor|military|fbi|cia|researcher|study|studies|discovered|found|reveals)",
            "name": "Authority + Emotion", "ctr_boost": 1.45,
            "desc": "Authority figures + emotional reaction = highest CTR",
        },
        "contrast": {
            "pattern": r"(?:but|however|yet|despite|instead|actually|turns out|thought.*wrong|what really|the truth about)",
            "name": "Contrast Hook", "ctr_boost": 1.30,
            "desc": "Unexpected twist creates cognitive dissonance",
        },
        "forbidden": {
            "pattern": r"(?:forbidden|banned|illegal|restricted|classified|censored|deleted|covered up|they don't want)",
            "name": "Forbidden Content", "ctr_boost": 1.40,
            "desc": "Restricted = must-see content",
        },
        "discovery": {
            "pattern": r"(?:discovered|found|drilled|excavated|uncovered|revealed|happened|inside|beneath|under|world's)",
            "name": "Discovery", "ctr_boost": 1.25,
            "desc": "Discovery framing creates compelling watch reason",
        },
    },
    "pt": {
        "curiosity_gap": {
            "pattern": r"(?:por que|como|o que|a verdade|ningu[eé]m|segredo|oculto|escondido|não te contou|você não sab|ninguém sabe|não conta|realmente)",
            "name": "Curiosity Gap", "ctr_boost": 1.40,
            "desc": "Cria lacuna de informação que o espectador PRECISA fechar",
        },
        "superlative": {
            "pattern": r"(?:mais|maior|pior|melhor|extremo|impossível|insano|incrível|absurdo|ridículo|bizarro|chocante|impressionante)",
            "name": "Superlative", "ctr_boost": 1.35,
            "desc": "Afirmações extremas que exigem atenção",
        },
        "specific_number": {
            "pattern": r"(?:R\$[\d,.]+|\b\d+[\s\w]*(?:anos?|meses?|dias?|semanas?|horas?|minutos?|mil|milhões?|bilhões?|por cento|%)?\b|\b\d{2,}\b)",
            "name": "Specific Number", "ctr_boost": 1.30,
            "desc": "Números adicionam credibilidade",
        },
        "permanence": {
            "pattern": r"(?:nunca|sempre|jamais|para sempre|ainda|eternamente|mudou|acabou|não existe mais|permanece)",
            "name": "Permanence Claim", "ctr_boost": 1.25,
            "desc": "Permanência cria urgência e peso",
        },
        "authority_emotion": {
            "pattern": r"(?:cientista|governo|nasa|especialista|médico|militar|historiador|arqueólogo|pesquisador|vaticano|papa|igreja|estado|cia|fbi|banco central|congresso|senado|onu|otan|exército|polícia|tribunal|judiciário)",
            "name": "Authority + Emotion", "ctr_boost": 1.45,
            "desc": "Autoridades + reação emocional = maior CTR",
        },
        "contrast": {
            "pattern": r"(?:mas|porém|ainda assim|apesar|na verdade|acontece que|você pensava|não era como)",
            "name": "Contrast Hook", "ctr_boost": 1.30,
            "desc": "Twist inesperado cria dissonância cognitiva",
        },
        "forbidden": {
            "pattern": r"(?:proibid[oa]|banid[oa]|ilegal|restrit[oa]|classificad[oa]|censurad[oa]|apagad[oa]|escondid[oa]|oculto|escond|ocult|suprim|silenciad|encobr|omit)",
            "name": "Forbidden Content", "ctr_boost": 1.40,
            "desc": "Restrito = conteúdo obrigatório",
        },
    },
    "es": {
        "curiosity_gap": {
            "pattern": r"(?:por qué|cómo|qué|la verdad|nadie|secreto|oculto|escondido|no te contaron|realmente)",
            "name": "Curiosity Gap", "ctr_boost": 1.40,
            "desc": "Crea brecha de información que el espectador DEBE cerrar",
        },
        "superlative": {
            "pattern": r"(?:más|mayor|peor|mejor|extremo|imposible|insano|increíble|absurdo|ridículo|bizarro|impactante)",
            "name": "Superlative", "ctr_boost": 1.35,
            "desc": "Afirmaciones extremas que exigen atención",
        },
        "specific_number": {
            "pattern": r"(?:\$[\d,.]+|\b\d+[\s\w]*(?:años?|meses?|días?|semanas?|horas?|minutos?|mil|millones?|por ciento|%)?\b|\b\d{2,}\b)",
            "name": "Specific Number", "ctr_boost": 1.30,
            "desc": "Los números añaden credibilidad",
        },
        "permanence": {
            "pattern": r"(?:nunca|siempre|jamás|para siempre|aún|eternamente|cambió|terminó|ya no existe|permanece)",
            "name": "Permanence Claim", "ctr_boost": 1.25,
            "desc": "Permanencia crea urgencia y peso",
        },
        "authority_emotion": {
            "pattern": r"(?:científico|gobierno|nasa|experto|médico|militar|historiador|arqueólogo|vaticano|papa|iglesia|estado|cia|fbi|banco central|congreso|senado|onu|otan|ejército|policía|tribunal)",
            "name": "Authority + Emotion", "ctr_boost": 1.45,
            "desc": "Autoridades + reacción emocional = mayor CTR",
        },
        "contrast": {
            "pattern": r"(?:pero|sin embargo|aún así|a pesar|en realidad|resulta que|pensabas|no era como)",
            "name": "Contrast Hook", "ctr_boost": 1.30,
            "desc": "Giro inesperado crea disonancia cognitiva",
        },
        "forbidden": {
            "pattern": r"(?:prohibid[oa]|ilegal|restringid[oa]|clasificad[oa]|censurad[oa]|borrad[oa]|oculto)",
            "name": "Forbidden Content", "ctr_boost": 1.40,
            "desc": "Restringido = contenido obligatorio",
        },
    },
}

_EMOTIONAL_WORDS_BY_LANG = {
    "en": {
        "terrifying": 9, "shocking": 8, "insane": 8, "unbelievable": 7,
        "deadly": 9, "dangerous": 8, "forbidden": 9, "secret": 8,
        "hidden": 7, "impossible": 8, "extreme": 7, "incredible": 6,
        "mysterious": 7, "ancient": 6, "cursed": 8, "haunted": 7,
        "brutal": 9, "savage": 8, "horrifying": 9, "catastrophic": 8,
        "abandoned": 7, "destroyed": 7, "unstoppable": 7, "legendary": 6,
        "massive": 6, "terrified": 9, "speechless": 7, "nightmare": 8,
    },
    "pt": {
        "chocante": 8, "brutal": 9, "terrível": 9, "inacreditável": 7,
        "mortal": 9, "perigoso": 8, "proibido": 9, "segredo": 8,
        "oculto": 7, "impossível": 8, "extremo": 7, "incrível": 6,
        "misterioso": 7, "antigo": 6, "amaldiçoado": 8, "assombrado": 7,
        "selvagem": 8, "horripilante": 9, "catastrófico": 8,
        "abandonado": 7, "destruído": 7, "imparável": 7, "lendário": 6,
        "gigantesco": 6, "aterrorizado": 9, "pesadelo": 8,
        "verdade": 7, "ridículo": 7, "bizarro": 7, "insano": 8,
        "obscuro": 7, "sombrio": 7, "sanguinário": 9, "cruel": 8,
        "assustador": 8, "perturbador": 8, "surpreender": 7, "surpreendente": 7,
        "impressionante": 7, "absurdo": 7, "sinistro": 8, "macabro": 8,
        "épico": 6, "impenetrável": 7, "invencível": 7, "sangrento": 8,
        "mistérios": 7, "secretos": 8, "escondido": 7, "crua": 7,
    },
    "es": {
        "impactante": 8, "brutal": 9, "terrible": 9, "increíble": 7,
        "mortal": 9, "peligroso": 8, "prohibido": 9, "secreto": 8,
        "oculto": 7, "imposible": 8, "extremo": 7, "misterioso": 7,
        "antiguo": 6, "maldito": 8, "embrujado": 7, "salvaje": 8,
        "escalofriante": 9, "catastrófico": 8, "abandonado": 7,
        "destruido": 7, "imparable": 7, "legendario": 6,
        "aterrador": 9, "pesadilla": 8, "verdad": 7, "ridículo": 7,
        "bizarro": 7, "insano": 8, "oscuro": 7, "siniestro": 8,
        "sanguinario": 9, "cruel": 8, "perturbador": 8, "sorprendente": 7,
        "épico": 6, "sangriento": 8, "misterios": 7, "secretos": 8,
    },
}

def _detect_title_language(title: str) -> str:
    """Auto-detect language from title text."""
    t = title.lower()
    pt_markers = ["não", "que", "como", "sobre", "você", "para", "uma", "dos", "das",
                  "pelo", "pela", "são", "foi", "era", "seu", "sua", "nos", "nas",
                  "mais", "também", "ainda", "muito", "nunca", "ninguém", "verdade"]
    es_markers = ["qué", "cómo", "por qué", "sobre", "los", "las", "una", "del",
                  "por", "pero", "más", "nunca", "nadie", "verdad", "fue", "era",
                  "sin", "hay", "están", "puede", "tiempo", "mundo"]
    en_markers = ["the", "why", "how", "what", "that", "this", "with", "from",
                  "never", "nobody", "truth", "most", "was", "were", "are"]
    words = set(re.split(r'\W+', t))
    pt_score = sum(1 for w in pt_markers if w in words or w in t)
    es_score = sum(1 for w in es_markers if w in words or w in t)
    en_score = sum(1 for w in en_markers if w in words or w in t)
    if pt_score > en_score and pt_score >= es_score:
        return "pt"
    if es_score > en_score and es_score > pt_score:
        return "es"
    return "en"

def _get_structures_for_lang(language: str) -> dict:
    """Get viral structures for the given language."""
    lang = language.lower()[:2] if language else "en"
    lang_map = {"portuguese": "pt", "pt": "pt", "spanish": "es", "es": "es",
                "english": "en", "en": "en"}
    lang = lang_map.get(language.lower(), lang)
    return _VIRAL_STRUCTURES_BY_LANG.get(lang, _VIRAL_STRUCTURES_BY_LANG["en"])

def _get_emotions_for_lang(language: str) -> dict:
    """Get emotional words for the given language."""
    lang = language.lower()[:2] if language else "en"
    lang_map = {"portuguese": "pt", "pt": "pt", "spanish": "es", "es": "es",
                "english": "en", "en": "en"}
    lang = lang_map.get(language.lower(), lang)
    return _EMOTIONAL_WORDS_BY_LANG.get(lang, _EMOTIONAL_WORDS_BY_LANG["en"])

# Backwards compat aliases
VIRAL_STRUCTURES = _VIRAL_STRUCTURES_BY_LANG["en"]
EMOTIONAL_WORDS = _EMOTIONAL_WORDS_BY_LANG["en"]

def analyze_title(title, language=None):
    """Deep analysis of a single title. Auto-detects language if not provided."""
    if not language:
        language = _detect_title_language(title)
    
    structures = _get_structures_for_lang(language)
    emotions = _get_emotions_for_lang(language)
    
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
        "language_detected": language,
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
    
    # CAPS words (works for all languages with Latin alphabet)
    caps = re.findall(r'\b[A-ZÀÁÂÃÉÊÍÓÔÕÚÇ]{3,}\b', title)
    if caps:
        result["power_words"] = caps
        result["score"] += min(len(caps) * 6, 18)
    elif not re.search(r'[A-ZÀÁÂÃÉÊÍÓÔÕÚÇ]{3,}', title):
        result["suggestions"].append("Add 1-2 CAPS words for emphasis")
    
    # Emotional words (language-aware)
    for word, val in emotions.items():
        if word in t_lower:
            result["emotional_words"].append(word)
            result["score"] += val
    
    if not result["emotional_words"]:
        result["suggestions"].append("Add emotional trigger words")
    
    # Structure detection (language-aware)
    for sid, sdata in structures.items():
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
    
    # Numbers (universal)
    if re.search(r'[\$R]?\$?[\d,.]+', title):
        result["score"] += 8
    else:
        result["suggestions"].append("Consider adding specific numbers")
    
    # Question mark / Exclamation (universal)
    if "?" in title:
        result["score"] += 5
    if "!" in title:
        result["score"] += 3
    
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
    data = request.json or {}
    title = san(data.get("title", ""), maxlen=500)
    if not title:
        return jsonify({"score": 0, "grade": "F", "structures": [],
                        "title_length": 0, "word_count": 0, "language_detected": "unknown",
                        "warnings": ["Título vazio — insira um título para analisar"]})
    return jsonify(analyze_title(title))

@app.route("/api/analyze_batch", methods=["POST"])
def api_analyze_batch():
    data = request.json or {}
    raw = data.get("titles", [])
    titles = san_list(raw, maxlen=300, max_items=100)
    results = [analyze_title(t) for t in titles if t.strip()]
    
    if not results:
        return jsonify({"error": "Nenhum título fornecido para análise"}), 400
    
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
    data = request.json or {}
    topic = data.get("topic", "")
    language = data.get("language", "English")
    niche = data.get("niche", "")
    persona = data.get("persona", "The Strategist")
    
    persona_rules = {
        "The Strategist": "Use balanced, data-driven structures with high curiosity gaps. Professional but extremely clickable.",
        "The Showman": "Use extreme MrBeast-style energy! High stakes, crazy numbers, exaggerated reactions, money, time limits.",
        "The Detective": "Use True Crime style. Missing details, unsolved mysteries, 'interrogation' tactics, cold cases, psychological angles.",
        "The Professor": "Educational but authoritative. Use 'Why Scientists are...', 'The Unknown History of...', complex topics made urgent.",
        "The Doomer": "Survivalist style. Impending doom, economic collapse, forbidden locations, urgent warnings, terrifying truths."
    }
    
    style_rule = persona_rules.get(persona, persona_rules["The Strategist"])
    
    prompt = f"""You are an elite AI Persona for YouTube Strategy. Your current active persona is: {persona}.

TOPIC: {topic}
LANGUAGE: {language}
{"NICHE CONTEXT: " + niche if niche else ""}

Generate exactly 15 viral YouTube title variants for this topic.

PERSONA DIRECTIVE:
{style_rule}

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
    if result.startswith("[AI Error"):
        return jsonify({"error": result})
        
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
    data = request.json or {}
    theme = data.get("theme", "")
    language = data.get("language", "English")
    
    prompt = f"""You are an expert YouTube niche analyst who has studied thousands of channels.

{"MAIN THEME: " + theme if theme else "Analyze ALL trending YouTube themes."}
TARGET LANGUAGE: {language}

Your task: Find the most PROFITABLE sub-niches that have:
- HIGH viewer demand (people actively searching)
- LOW creator supply (few channels covering it well)
- VIRAL potential (emotional, curiosity-driven topics)

For each sub-niche return a JSON object with EXACTLY these fields:
{{
  "name": "Sub-niche Name (specific)",
  "demand": 9,
  "supply": 2,
  "opportunity": 7,
  "blue_ocean_score": 85,
  "target_audience": "Who watches this content",
  "audience_pain": "The core problem/curiosity they have",
  "content_angle": "The unique approach to dominate this sub-niche",
  "first_video_idea": "Exact title and concept for the FIRST video you should make to start this channel",
  "rpm_estimate": "$3-6 RPM",
  "example_titles": ["Viral Title 1 (max 100 chars)", "Viral Title 2", "Viral Title 3"],
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4"],
  "estimated_views_per_video": "50K-200K"
}}

blue_ocean_score: 0-100. 100 = completely untapped (no competition, high demand). 0 = red ocean (oversaturated).
first_video_idea: Be SPECIFIC — not just a topic, but the exact title and why it will work as a first video.
rpm_estimate: Real CPM/RPM range for this niche.

Return exactly 8 sub-niches as a valid JSON array. NO markdown, NO explanation."""

    result = ask_gemini(prompt)
    
    if result.startswith("[AI Error"):
        return jsonify({"niches": [], "raw": result, "theme": theme, "error": result})
    
    try:
        niches = safe_parse_json(result, "list")
        # Defensive: if AI returned dict instead of list, try to extract
        if isinstance(niches, dict):
            for v in niches.values():
                if isinstance(v, list) and len(v) > 0:
                    niches = v
                    break
            else:
                niches = [niches]
        
        valid_niches = []
        for n in niches:
            if not isinstance(n, dict):
                continue
            n.setdefault("demand", 5)
            n.setdefault("supply", 5)
            n.setdefault("opportunity", n.get("demand", 5) - n.get("supply", 5))
            n.setdefault("blue_ocean_score", max(0, min(100, int((n.get("opportunity", 5) / 10) * 100))))
            n.setdefault("keywords", [])
            n.setdefault("example_titles", [])
            n.setdefault("target_audience", "")
            n.setdefault("audience_pain", "")
            n.setdefault("content_angle", "")
            n.setdefault("first_video_idea", "")
            n.setdefault("rpm_estimate", "")
            n.setdefault("estimated_views_per_video", "")
            valid_niches.append(n)
        
        valid_niches.sort(key=lambda x: x.get("blue_ocean_score", x.get("opportunity", 0)), reverse=True)
        return jsonify({"niches": valid_niches, "theme": theme})
    except Exception as e:
        return jsonify({"niches": [], "raw": result[:200], "theme": theme,
                        "error": f"Erro ao processar resposta da IA: {str(e)[:80]}"})

@app.route("/api/micronicho", methods=["POST"])
def api_micronicho():
    """Level 3: Find micro-niches within a sub-niche."""
    data = request.json or {}
    subniche = data.get("subniche", data.get("name", ""))
    parent = data.get("parent_niche", data.get("theme", ""))
    language = data.get("language", "English")

    if not subniche:
        return jsonify({"error": "subniche is required"}), 400

    prompt = f"""You are an elite YouTube micro-niche specialist. Your job is to find hyper-specific, UNTAPPED micro-niches.

PARENT NICHE: {parent or 'General'}
SUB-NICHE: {subniche}
LANGUAGE: {language}

Find 6 MICRO-NICHES within "{subniche}". Each must be:
- Extremely specific (not broad)
- Searchable and monetizable
- Underserved by existing content creators
- Emotionally compelling for the viewer

For each micro-niche, return:
{{
  "name": "Micro-niche Name",
  "blue_ocean_score": 92,
  "competition_level": "Very Low",
  "target_avatar": "Precise description of the viewer (age, interests, pain)",
  "unique_angle": "The specific content angle that NOBODY is doing",
  "content_gap": "What content is missing that people desperately want",
  "first_3_videos": ["Video title 1", "Video title 2", "Video title 3"],
  "estimated_rpm": "$4-8",
  "time_to_monetize": "3-6 months",
  "why_now": "Why this micro-niche is at its peak opportunity RIGHT NOW"
}}

competition_level options: "Very Low", "Low", "Medium", "High", "Very High"

Return a JSON array of 6 micro-niches. ONLY valid JSON, no markdown."""

    result = ask_gemini(prompt)
    if result.startswith("[AI Error"):
        return jsonify({"error": result, "micronichos": []})
    
    try:
        micronichos = safe_parse_json(result, "list")
        for m in micronichos:
            m.setdefault("blue_ocean_score", 75)
            m.setdefault("competition_level", "Low")
            m.setdefault("target_avatar", "")
            m.setdefault("unique_angle", "")
            m.setdefault("content_gap", "")
            m.setdefault("first_3_videos", [])
            m.setdefault("estimated_rpm", "")
            m.setdefault("time_to_monetize", "")
            m.setdefault("why_now", "")
        micronichos.sort(key=lambda x: x.get("blue_ocean_score", 0), reverse=True)
        return jsonify({"micronichos": micronichos, "subniche": subniche, "parent": parent})
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}", "micronichos": [], "raw": result})

@app.route("/api/niche_strategy_complete", methods=["POST"])
def api_niche_strategy_complete():
    """Complete 3-level niche strategy: Niche > Sub-niches > Micro-niches"""
    data = request.json or {}
    niche = data.get("niche", "")
    language = data.get("language", "English")

    if not niche:
        return jsonify({"error": "niche is required"}), 400

    prompt = f"""You are the world's #1 YouTube channel strategist. Generate a COMPLETE 3-level niche strategy map.

NICHE: {niche}
LANGUAGE: {language}

Generate a complete strategy JSON with this structure:
{{
  "main_niche": {{
    "name": "{niche}",
    "competition": "Medium",
    "opportunity_summary": "Why this niche has massive potential right now",
    "avg_rpm": "$3-7",
    "market_size": "~2.4B searches/month",
    "best_content_types": ["Documentary", "Listicle", "Deep Dive"]
  }},
  "subniches": [
    {{
      "name": "Sub-niche 1",
      "demand": 9,
      "supply": 3,
      "opportunity": 6,
      "blue_ocean_score": 78,
      "rpm_estimate": "$4-8",
      "content_angle": "Specific angle to dominate",
      "first_video_idea": "Exact first video concept and why it works",
      "example_titles": ["Viral title 1", "Viral title 2"],
      "micronichos": [
        {{
          "name": "Micro-niche 1",
          "blue_ocean_score": 90,
          "competition_level": "Very Low",
          "target_avatar": "Who watches this",
          "unique_angle": "What nobody is doing",
          "first_3_videos": ["Title 1", "Title 2", "Title 3"],
          "estimated_rpm": "$5-10",
          "time_to_monetize": "2-4 months"
        }},
        {{
          "name": "Micro-niche 2",
          "blue_ocean_score": 85,
          "competition_level": "Low",
          "target_avatar": "Who watches this",
          "unique_angle": "What nobody is doing",
          "first_3_videos": ["Title 1", "Title 2", "Title 3"],
          "estimated_rpm": "$4-8",
          "time_to_monetize": "3-5 months"
        }},
        {{
          "name": "Micro-niche 3",
          "blue_ocean_score": 80,
          "competition_level": "Low",
          "target_avatar": "Who watches this",
          "unique_angle": "What nobody is doing",
          "first_3_videos": ["Title 1", "Title 2", "Title 3"],
          "estimated_rpm": "$3-6",
          "time_to_monetize": "4-6 months"
        }}
      ]
    }}
  ]
}}

Provide 5 sub-niches each with 3 micro-niches = 15 micro-niche opportunities total.
Return ONLY valid JSON. No markdown. No explanation."""

    result = ask_gemini(prompt)
    if result.startswith("[AI Error"):
        return jsonify({"error": result, "strategy": None})
    
    try:
        strategy = safe_parse_json(result, "dict")
        # Defensive: AI sometimes returns [{...}] instead of {...}
        if isinstance(strategy, list):
            if len(strategy) == 1 and isinstance(strategy[0], dict):
                strategy = strategy[0]  # unwrap single-item list
            else:
                strategy = {"subniches": strategy}  # bare list of subniches
        # Ensure structure
        strategy.setdefault("main_niche", {"name": niche})
        strategy.setdefault("subniches", [])
        for sub in strategy.get("subniches", []):
            if not isinstance(sub, dict):
                continue
            sub.setdefault("blue_ocean_score", 70)
            sub.setdefault("demand", 7)
            sub.setdefault("supply", 4)
            sub.setdefault("opportunity", sub.get("demand", 7) - sub.get("supply", 4))
            sub.setdefault("rpm_estimate", "")
            sub.setdefault("content_angle", "")
            sub.setdefault("first_video_idea", "")
            sub.setdefault("example_titles", [])
            sub.setdefault("micronichos", [])
            for mc in sub.get("micronichos", []):
                if not isinstance(mc, dict):
                    continue
                mc.setdefault("blue_ocean_score", 75)
                mc.setdefault("competition_level", "Low")
                mc.setdefault("target_avatar", "")
                mc.setdefault("unique_angle", "")
                mc.setdefault("first_3_videos", [])
                mc.setdefault("estimated_rpm", "")
                mc.setdefault("time_to_monetize", "")
        return jsonify({"strategy": strategy, "niche": niche})
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}", "strategy": None, "raw": result})



@app.route("/api/deep_analysis", methods=["POST"])
def api_deep_analysis():
    """Deep AI analysis of a title with full breakdown."""
    data = request.json or {}
    title = data.get("title", "")
    
    basic = analyze_title(title)
    
    prompt = f"""You are the world's #1 YouTube retention and strategy expert.

TITLE TO ANALYZE: "{title}"

Provide a COMPREHENSIVE highly-structured JSON analysis of this title.

Return ONLY a valid JSON object in this exact format:
{{
  "verdict": "Will this go viral? Why or why not? (1-2 sentences)",
  "emotional_mapping": {{
    "primary_emotion": "Fear, Curiosity, Greed, etc.",
    "intensity": 9
  }},
  "retention_prediction": {{
    "hook_dropoff": "Predicted dropoff at 0:30 (e.g., 65%)",
    "retention_risk": "What will make people click off?"
  }},
  "thumbnail_concept": {{
    "visual": "Describe the main visual element",
    "text": "The exact text to overlay"
  }},
  "improved_versions": [
    "Better Version 1",
    "Better Version 2",
    "Better Version 3"
  ]
}}

Be brutally honest. No generic advice. Return ONLY valid JSON."""

    result = ask_gemini(prompt)
    if result.startswith("[AI Error"):
        basic["ai_deep_analysis"] = {"error": result}
        return jsonify(basic)
        
    try:
        cleaned = result.strip()
        cleaned = re.sub(r'^```json\s*', '', cleaned)
        ai_json = safe_parse_json(result, "dict")
        basic["ai_deep_analysis"] = ai_json
    except Exception as e:
        basic["ai_deep_analysis"] = {"error": f"JSON parse error: {str(e)[:80]}", "raw": result}
        
    return jsonify(basic)

@app.route("/api/crossover_engine", methods=["POST"])
def api_crossover_engine():
    """Generates viral crossover concepts linking two completely different niches."""
    data = request.json or {}
    niche_a = data.get("niche_a", "")
    niche_b = data.get("niche_b", "")
    mechanic = data.get("mechanic", "No specific mechanic")
    language = data.get("language", "English")

    if not niche_a or not niche_b:
        return jsonify({"error": "You must provide two niches to crossover."}), 400

    prompt = f"""You are the ultimate YouTube growth hacker of 2026. Your secret weapon is the "CROSSOVER ENGINE".
You link two completely different scientific, economic, or entertainment themes into a cascading cause-and-effect narrative. This hijacks two audiences at once.

NICHE A: {niche_a}
NICHE B: {niche_b}
VIRAL MECHANIC TO INJECT: {mechanic} (e.g. Specific Pricing, Permanence Claim, Professional Villain)
LANGUAGE: {language}

Create a highly structured JSON response linking these two niches into a mind-blowing viral video concept.

Return ONLY a valid JSON object in this exact format:
{{
  "crossover_concept": "Explain the mind-blowing link between Niche A and Niche B (1-2 paragraphs)",
  "cascading_narrative": [
    "Step 1: The trigger in Niche A",
    "Step 2: The hidden escalation",
    "Step 3: The catastrophic/massive effect on Niche B"
  ],
  "audience_psychology": "Why this specific combination creates an irresistible curiosity gap",
  "viral_crossover_titles": [
    {{
      "title": "Title 1 (60-100 chars)",
      "structure": "The structure used"
    }},
    {{
      "title": "Title 2 (60-100 chars)",
      "structure": "The structure used"
    }},
    {{
      "title": "Title 3 (60-100 chars)",
      "structure": "The structure used"
    }}
  ]
}}

Make the connection logical but shocking. Return ONLY valid JSON. No markdown outside the JSON."""

    result = ask_gemini(prompt)
    if result.startswith("[AI Error"):
        return jsonify({"error": result})
        
    try:
        cleaned = result.strip()
        cleaned = re.sub(r'^```json\s*', '', cleaned)
        crossover_json = safe_parse_json(result, "dict")
        return jsonify({"crossover_data": crossover_json})
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}", "raw": result})

@app.route("/api/trend_hijacker", methods=["POST"])
def api_trend_hijacker():
    """Converts a breaking news event into evergreen documentary-style video concepts."""
    data = request.json or {}
    breaking_news = data.get("news_event", "")
    target_niche = data.get("target_niche", "Psychology")
    language = data.get("language", "English")

    if not breaking_news:
        return jsonify({"error": "News event is required."}), 400

    prompt = f"""You are an elite YouTube growth hacker. Your specialty is "Newsjacking" - taking temporary trending news and turning it into timeless, high-retention evergreen concepts.

BREAKING NEWS: {breaking_news}
TARGET NICHE FOR ADAPTATION: {target_niche}
LANGUAGE: {language}

Extract the core psychological or systemic truth behind this news, and create an evergreen video concept for the Target Niche.

Return ONLY a valid JSON object:
{{
  "evergreen_concept": "The timeless, highly searchable topic extracted from the news (1-2 paragraphs)",
  "psychological_trigger": "What deep human emotion does this tap into?",
  "titles": [
    {{ "title": "Title 1 (60-100 chars)", "structure": "The structure used" }},
    {{ "title": "Title 2 (60-100 chars)", "structure": "The structure used" }},
    {{ "title": "Title 3 (60-100 chars)", "structure": "The structure used" }}
  ]
}}
Return ONLY valid JSON."""
    result = ask_gemini(prompt)
    if result.startswith("[AI Error"): return jsonify({"error": result})
    try:
        return jsonify({"hijack_data": safe_parse_json(result, "dict")})
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}"})

@app.route("/api/hook_blueprint", methods=["POST"])
def api_hook_blueprint():
    """Generates a highly-retentive 60-second hook pacing script."""
    data = request.json or {}
    title = data.get("title", "")
    if not title: return jsonify({"error": "Title required."}), 400

    prompt = f"""You are the world's highest-paid YouTube scriptwriter and retention architect.
Generate a 60-second "Hook Blueprint" for this title: "{title}"

Return ONLY a valid JSON object:
{{
  "hook_analysis": "Why this specific hook works to trap the viewer",
  "script_blocks": [
    {{ "timestamp": "0:00 - 0:05", "visual": "What we see", "audio": "What is said", "retention_tactic": "Curiosity, Stakes, etc." }},
    {{ "timestamp": "0:05 - 0:15", "visual": "What we see", "audio": "What is said", "retention_tactic": "Pattern interrupt" }},
    {{ "timestamp": "0:15 - 0:30", "visual": "What we see", "audio": "What is said", "retention_tactic": "Establishing the villain/problem" }},
    {{ "timestamp": "0:30 - 0:60", "visual": "What we see", "audio": "What is said", "retention_tactic": "The payoff promise" }}
  ]
}}
Return ONLY valid JSON."""
    result = ask_gemini(prompt)
    if result.startswith("[AI Error"): return jsonify({"error": result})
    try:
        return jsonify({"blueprint_data": safe_parse_json(result, "dict")})
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}"})

@app.route("/api/outlier_finder", methods=["POST"])
def api_outlier_finder():
    """Finds viral outlier videos using Nexlev-style math and decodes their secret."""
    data = request.json or {}
    niche = data.get("niche", "")
    language = data.get("language", "English")

    if not niche: return jsonify({"error": "Niche required."}), 400

    key = YOUTUBE_API_KEY
    if not key:
        return jsonify({"error": "YouTube API key not set. Please set it in the YouTube Scanner tab."}), 400

    from core.youtube_api import search_outliers
    outliers = search_outliers(niche, key)
    
    if not outliers:
        return jsonify({"error": "No significant outliers found for this niche in the last 30 days."})

    # Pass the top 3 outliers to Gemini to extract the "Outlier Secret"
    top_3 = outliers[:3]
    outlier_text = "\n".join([f"- Title: {v['title']} (Views: {v['views']}, Subs: {v['subscribers']}, Score: {v['outlier_score']}x)" for v in top_3])

    prompt = f"""You are an elite YouTube growth hacker. I just found 3 massive 'Outlier' videos in the "{niche}" niche.
These videos got drastically more views than the channel's subscriber count.

OUTLIER VIDEOS FOUND:
{outlier_text}

Analyze WHY these specific titles broke the algorithm for small channels.
Return ONLY a valid JSON object:
{{
  "outlier_secret": "The psychological reason these small channels went viral (1 paragraph)",
  "cloned_titles": [
    {{ "title": "Adapted Title 1 (60-100 chars)", "structure": "Structure used" }},
    {{ "title": "Adapted Title 2 (60-100 chars)", "structure": "Structure used" }}
  ]
}}
Return ONLY valid JSON."""

    result = ask_gemini(prompt)
    if result.startswith("[AI Error"): return jsonify({"error": result})
    try:
        ai_data = safe_parse_json(result, "dict")
        
        return jsonify({
            "outliers_found": top_3,
            "ai_analysis": ai_data
        })
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}"})

@app.route("/api/competitor_xray", methods=["POST"])
def api_competitor_xray():
    """Nexlev-style Competitor Deep Dive: RPM, Velocity, and Secret Sauce."""
    data = request.json or {}
    channel_handle = data.get("channel", "").strip()
    
    key = YOUTUBE_API_KEY
    if not key:
        return jsonify({"error": "YouTube API key not set."}), 400
        
    if not channel_handle:
        return jsonify({"error": "Channel handle required (e.g., @MrBeast)."}), 400

    from core.youtube_api import get_channel_info, get_channel_videos
    
    # 1. Fetch channel info
    ch_info = get_channel_info(channel_handle, key)
    if not ch_info or "error" in ch_info:
        return jsonify({"error": "Could not find channel. Make sure to use the exact handle or URL."})
        
    ch_id = ch_info.get("id")
    
    # 2. Fetch last 30 videos
    videos = get_channel_videos(ch_id, key, max_videos=30)
    if not videos:
        return jsonify({"error": "No public videos found for this channel."})
        
    # 3. Calculate Nexlev Metrics
    from datetime import datetime, timezone
    
    total_recent_views = 0
    now = datetime.now(timezone.utc)
    
    outlier_video = videos[0]
    highest_views = -1
    
    for v in videos:
        v_views = v.get("views", 0)
        total_recent_views += v_views
        if v_views > highest_views:
            highest_views = v_views
            outlier_video = v
            
    avg_views = total_recent_views // len(videos) if videos else 0
    
    # Calculate velocity (videos per month based on last 30)
    try:
        oldest_date = datetime.strptime(videos[-1]["published_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        days_diff = max(1, (now - oldest_date).days)
        videos_per_month = round((len(videos) / days_diff) * 30, 1)
    except:
        videos_per_month = "N/A"
        
    # Estimate Monthly Revenue (Nexlev uses varying RPMs, we'll use a conservative $4.00 RPM)
    # Revenue = (Total views in a month / 1000) * 4
    # To get views in a month, we calculate average views per day from the recent videos, then * 30
    views_per_day = total_recent_views / days_diff if 'days_diff' in locals() else 0
    estimated_monthly_views = views_per_day * 30
    est_revenue = round((estimated_monthly_views / 1000) * 4)
    
    # 4. Extract Secret Sauce via Gemini
    titles_list = "\n".join([f"- {v['title']} ({v.get('views', 0)} views)" for v in videos[:15]])
    
    prompt = f"""You are an elite YouTube strategist. Analyze this competitor's recent performance.
CHANNEL: {ch_info.get('title')}
AVG VIEWS: {avg_views}
RECENT TITLES:
{titles_list}

Analyze their "Secret Sauce".
Return ONLY a valid JSON object:
{{
  "content_pillars": ["Pillar 1", "Pillar 2"],
  "title_framework": "Explain their core psychological title formula (1 paragraph)",
  "weakness": "What are they not doing that someone else could exploit?"
}}
Return ONLY valid JSON."""

    result = ask_gemini(prompt)
    if result.startswith("[AI Error"): return jsonify({"error": result})
    try:
        ai_data = safe_parse_json(result, "dict")
        
        return jsonify({
            "channel_stats": {
                "title": ch_info.get("title"),
                "subscribers": ch_info.get("subscriber_count", 0),
                "total_views": ch_info.get("view_count", 0),
                "thumbnail": ch_info.get("thumbnail", "")
            },
            "nexlev_metrics": {
                "avg_views": avg_views,
                "upload_velocity": f"{videos_per_month} videos/mo",
                "est_revenue": f"${est_revenue:,}/mo",
                "top_recent_video": {
                    "title": outlier_video.get("title", ""),
                    "views": outlier_video.get("views", 0),
                    "url": outlier_video.get("url", "")
                }
            },
            "ai_analysis": ai_data
        })
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}"})

@app.route("/api/niche_scorer", methods=["POST"])
def api_niche_scorer():
    """NexLev-style AI Niche Profitability & Saturation Scorer."""
    data = request.json or {}
    niche = data.get("niche", "").strip()
    
    if not niche:
        return jsonify({"error": "Niche required."}), 400

    key = YOUTUBE_API_KEY

    # ── AI-ONLY MODE (no YouTube key) ──────────────────────────────────
    if not key:
        ai_prompt = f"""You are an elite YouTube niche analyst.
NICHE: {niche}
LANGUAGE: {language}

Evaluate this niche for a faceless YouTube automation channel.
Return ONLY a valid JSON object:
{{
  "verdict": "2-3 sentences on whether to enter this niche.",
  "monetization_strategy": "How to monetize beyond AdSense.",
  "subniche_pivot": "A micro-niche with less competition.",
  "estimated_saturation": "Low / Medium / High",
  "opportunity_score": 75
}}
Return ONLY valid JSON."""
        ai_result = ask_gemini(ai_prompt)
        if ai_result.startswith("[AI Error"):
            return jsonify({"error": ai_result})
        try:
            ai_data = safe_parse_json(ai_result, "dict")
            return jsonify({
                "metrics": {
                    "avg_views": 0, "avg_subs": 0,
                    "saturation_label": ai_data.get("estimated_saturation", "Unknown"),
                    "color": "#4ecca3",
                    "score": ai_data.get("opportunity_score", 70),
                    "ratio": 0, "note": "AI estimate (no YouTube key set)"
                },
                "ai_analysis": ai_data
            })
        except Exception as e:
            return jsonify({"error": f"JSON parse: {str(e)[:80]}"})
    # ── YOUTUBE DATA MODE ──────────────────────────────────────────────
    from core.youtube_api import _get, search_outliers
    from datetime import datetime, timezone, timedelta
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "part": "snippet", "q": niche, "type": "video",
        "order": "viewCount", "maxResults": 30, "regionCode": "US",
        "publishedAfter": month_ago
    }
    search_data = _get("search", params, key)
    items = search_data.get("items", [])
    if not items:
        return jsonify({"error": "Not enough data found for this niche."})
    video_ids = [item["id"]["videoId"] for item in items if item["id"].get("videoId")]
    channel_ids = list(set([item["snippet"]["channelId"] for item in items if item["snippet"].get("channelId")]))
    
    # 2. Get Video Stats (views)
    v_stats = _get("videos", {"part": "statistics", "id": ",".join(video_ids)}, key)
    views_list = []
    for v in v_stats.get("items", []):
        views_list.append(int(v.get("statistics", {}).get("viewCount", 0)))
        
    # 3. Get Channel Stats (subscribers)
    c_stats = _get("channels", {"part": "statistics", "id": ",".join(channel_ids[:50])}, key)
    subs_list = []
    for c in c_stats.get("items", []):
        subs_list.append(int(c.get("statistics", {}).get("subscriberCount", 0)))
        
    # 4. Calculate Saturation & Opportunity
    avg_views = sum(views_list) / len(views_list) if views_list else 0
    avg_subs = sum(subs_list) / len(subs_list) if subs_list else 0
    
    # How many "small" channels (< 50k subs) are getting > 50k views?
    small_channel_wins = 0
    # To do this accurately we need to match them, but we'll approximate for the scorer
    # If the average views is high but average subs is low, it's a great niche.
    
    ratio = avg_views / max(1, avg_subs)
    
    if avg_subs > 1000000:
        saturation = "High (Dominated by Giants)"
        color = "#e94560" # Red
        score = 30
    elif avg_subs > 500000:
        saturation = "Medium-High (Competitive)"
        color = "#f59e0b" # Orange
        score = 50
    elif ratio > 2.0:
        saturation = "Low (Blue Ocean - High Outlier Potential)"
        color = "#10b981" # Green
        score = 95
    elif ratio > 0.5:
        saturation = "Medium (Balanced)"
        color = "#4ecca3" # Light Green
        score = 75
    else:
        saturation = "Dead / Low Demand"
        color = "#555" # Gray
        score = 20
        
    prompt = f"""You are an elite YouTube analyst. 
NICHE: {niche}
AVG RECENT VIEWS: {avg_views}
AVG COMPETITOR SUBS: {avg_subs}
SATURATION: {saturation}

Analyze the profitability of this niche for a faceless automation channel.
Return ONLY a valid JSON object:
{{
  "verdict": "1-2 sentences on whether to enter this niche.",
  "monetization_strategy": "How to monetize this beyond AdSense (Sponsorships, Affiliates, etc)",
  "subniche_pivot": "A micro-niche within this topic that has even less competition."
}}
Return ONLY valid JSON."""

    result = ask_gemini(prompt)
    if result.startswith("[AI Error"): return jsonify({"error": result})
    try:
        ai_data = safe_parse_json(result, "dict")
        
        return jsonify({
            "metrics": {
                "avg_views": round(avg_views),
                "avg_subs": round(avg_subs),
                "saturation_label": saturation,
                "color": color,
                "score": score,
                "ratio": round(ratio, 2)
            },
            "ai_analysis": ai_data
        })
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}"})

@app.route("/api/shorts_engine", methods=["POST"])
def api_shorts_engine():
    """Generates viral loops and script pacing for YouTube Shorts / TikTok."""
    data = request.json or {}
    topic = data.get("topic", "").strip()
    niche = data.get("niche", "").strip()
    language = data.get("language", "English")
    
    if not topic or not niche:
        return jsonify({"error": "Topic and Niche required."}), 400

    prompt = f"""You are an elite YouTube Shorts / TikTok strategist.
Your goal is to engineer a 60-second vertical video script about '{topic}' in the '{niche}' niche.
Shorts rely on 3 things: The Visual Hook, High Pacing, and the "Perfect Loop" (the end of the video flows seamlessly into the first word of the video).

Respond in {language}.
Return ONLY a valid JSON object:
{{
  "scroll_stopper": "The 3-second visual/text hook to stop the user from scrolling.",
  "script_structure": [
    {{"time": "0s-3s", "action": "Hook / Audio Cue"}},
    {{"time": "3s-15s", "action": "Context buildup"}},
    {{"time": "15s-45s", "action": "The meat / revelation"}},
    {{"time": "45s-58s", "action": "The cliffhanger / payoff"}}
  ],
  "perfect_loop_phrase": "The exact sentence to say at the end that perfectly connects to the first sentence of the video.",
  "viral_audio_vibe": "What type of trending audio or sound effects to use (e.g., 'Phonk + Bass drop' or 'Eerie synth')."
}}
Return ONLY valid JSON."""

    result = ask_gemini(prompt)
    if result.startswith("[AI Error"): return jsonify({"error": result})
    try:
        ai_data = safe_parse_json(result, "dict")
        return jsonify(ai_data)
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}"})

@app.route("/api/channel_strategy", methods=["POST"])
def api_channel_strategy():
    """AI-powered channel strategy analysis."""
    data = request.json or {}
    # Accept 'niche' OR 'channel_type' interchangeably
    channel_type = data.get("channel_type") or data.get("niche", "")
    current_titles = data.get("titles", [])
    target_audience = data.get("target_audience", "")
    language = data.get("language", "English")
    if not channel_type:
        return jsonify({"error": "niche or channel_type is required"}), 400
    
    titles_text = "\n".join(f"- {t}" for t in current_titles[:20]) if current_titles else "No titles provided"
    
    prompt = f"""You are an elite YouTube growth strategist who has scaled channels from 0 to 1M subscribers.

MAIN THEME/CHANNEL TYPE: {channel_type}
TARGET AUDIENCE: {target_audience}
LANGUAGE: {language}

CURRENT TITLES (if any):
{titles_text}

Provide a COMPLETE, highly specific strategy in JSON format. Do not use generic advice. Be extremely actionable.
You must provide recommendations in these exact 4 categories:

1. recommended_subthemes: Subthemes within the main theme that are trending and profitable.
2. new_perspectives: Completely new angles or ways to look at the main theme that disrupt the current market.
3. adjacent_subniches: Entirely different subniches that have strong audience crossover and high opportunity.
4. best_structures: The exact viral title formulas that work best for this specific theme.

Return ONLY a valid JSON object in this format:
{{
  "recommended_subthemes": [
    {{"name": "Subtheme name", "why_it_works": "Psychological reason", "example_titles": ["Title 1", "Title 2"]}}
  ],
  "new_perspectives": [
    {{"concept": "Perspective name", "why_it_works": "Why it stands out", "example_titles": ["Title 1", "Title 2"]}}
  ],
  "adjacent_subniches": [
    {{"niche_name": "Niche name", "crossover_reason": "Why the audience will watch it", "example_titles": ["Title 1", "Title 2"]}}
  ],
  "best_structures": [
    {{"name": "Structure name (e.g. Curiosity Gap)", "template": "The [Adjective] [Topic] That [Action]", "why_it_works": "Reason", "example_titles": ["Title 1", "Title 2"]}}
  ],
  "audience_insight": "One powerful paragraph about what this audience secretly wants."
}}

Return ONLY valid JSON. No markdown formatting outside the JSON."""

    result = ask_gemini(prompt)
    
    if result.startswith("[AI Error"):
        return jsonify({"error": result})
        
    try:
        cleaned = result.strip()
        cleaned = re.sub(r'^```json\s*', '', cleaned)
        strategy_json = safe_parse_json(result, "dict")
        
        return jsonify({"strategy_data": strategy_json, "channel_type": channel_type})
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}", "raw": result})

# =============================================
# STRATEGY REMIX ENGINE — Dual Path System
# =============================================
@app.route("/api/scan_viral_structures", methods=["POST"])
def api_scan_viral_structures():
    """Scan subniches for viral title structures — uses YouTube data if key set, AI-only otherwise."""
    data = request.json or {}
    niche = data.get("niche", "")
    subniches = data.get("subniches", [])
    language = data.get("language", "English")

    if not subniches:
        subniches = [niche]

    key = YOUTUBE_API_KEY

    # ── AI-ONLY MODE (no YouTube key) ──────────────────────────────────
    if not key:
        subs_text = ", ".join(subniches[:4])
        ai_prompt = f"""You are a YouTube analytics expert.
MAIN NICHE: {niche}
SUBNICHES: {subs_text}
LANGUAGE: {language}

Generate 8 realistic viral title structures for these subniches (simulate real YouTube data).
Return ONLY a valid JSON array:
[
  {{
    "subniche": "subniche name",
    "title": "Example viral title that could get millions of views (60-100 chars)",
    "tema": "main theme",
    "subtema": "specific angle",
    "structure": "psychological formula used (e.g., Curiosity Gap + Authority)",
    "views": "750K",
    "url": "https://youtube.com/watch?v=example",
    "channel": "Example Channel",
    "language": "{language}"
  }}
]
Return ONLY valid JSON array."""
        ai_result = ask_gemini(ai_prompt)
        if ai_result.startswith("[AI Error"):
            return jsonify({"structures": [], "error": ai_result})
        try:
            structures = safe_parse_json(ai_result, "list")
            return jsonify({"structures": structures, "niche": niche, "mode": "ai_estimate"})
        except Exception as e:
            return jsonify({"structures": [], "error": f"JSON parse: {str(e)[:80]}"})

    # ── YOUTUBE DATA MODE ──────────────────────────────────────────────
    from core.youtube_api import search_trending
    real_videos = []
    for sub in subniches[:4]:
        query = f"{niche} {sub}".strip()
        videos = search_trending(query, key, max_results=10)
        filtered = [v for v in videos if v["views"] > 1000]
        if not filtered and videos:
            filtered = videos[:3]
        for v in filtered:
            real_videos.append({
                "subniche": sub, "title": v["title"], "views": v["views"],
                "channel": v["channel_title"], "url": f"https://youtube.com/watch?v={v['id']}"
            })
    if not real_videos:
        return jsonify({"error": "No videos found for these subniches on YouTube. Try broader terms."}), 400
        
    videos_text = ""
    for i, v in enumerate(real_videos[:15]):
        videos_text += f"[{i+1}] Subniche: {v['subniche']} | Title: \"{v['title']}\" | Views: {v['views']} | URL: {v['url']} | Channel: {v['channel']}\n"
    
    prompt = f"""You are a YouTube analytics expert. I have fetched real trending videos from the YouTube API.

MAIN NICHE: {niche}
LANGUAGE: {language}

=== REAL YOUTUBE VIDEOS ===
{videos_text}

For EACH video listed above, analyze its title and extract:
1. The TEMA (main broad theme, e.g., "Edad Media", "Egito Antigo")
2. The SUBTEMA (specific angle, e.g., "higiene", "alimentação", "castelos")
3. The STRUCTURE (the psychological formula pattern, e.g., "How X lived in Y", "Why nobody survives X")

Return a JSON array where each object has:
[
  {{
    "subniche": "from the list above",
    "title": "the exact title from the list",
    "tema": "extracted main theme",
    "subtema": "extracted specific angle",
    "structure": "extracted psychological formula",
    "views": "formatted views (e.g., 757K)",
    "url": "the exact URL from the list",
    "channel": "the exact channel from the list",
    "language": "{language}"
  }}
]

Return ONLY valid JSON array."""

    result = ask_gemini(prompt)
    
    if result.startswith("[AI Error"):
        return jsonify({"structures": [], "error": result})
    
    try:
        structures = safe_parse_json(result, "list")
        # Normalize each structure object
        if isinstance(structures, dict):
            # AI wrapped list in a dict key
            for v in structures.values():
                if isinstance(v, list):
                    structures = v
                    break
            else:
                structures = [structures]  # single item
        for s in structures:
            if not isinstance(s, dict):
                continue
            s.setdefault("subniche", "")
            s.setdefault("title", "")
            s.setdefault("tema", "")
            s.setdefault("subtema", "")
            s.setdefault("structure", "")
            s.setdefault("views", "")
            s.setdefault("url", "")
            s.setdefault("channel", "")
        
        return jsonify({"structures": structures, "niche": niche})
    except Exception as e:
        # Graceful fallback — return empty list with info, not error
        return jsonify({"structures": [], "niche": niche,
                        "note": "AI returned unexpected format. Try again.",
                        "error": f"JSON parse: {str(e)[:80]}"})



@app.route("/api/strategy_remix", methods=["POST"])
def api_strategy_remix():
    """DUAL PATH strategy engine — works standalone (niche_a+niche_b) or with viral_structures."""
    data = request.json or {}
    viral_structures = data.get("viral_structures", [])
    # Standalone mode: accept niche_a + niche_b directly
    niche_a = data.get("niche_a", "")
    niche_b = data.get("niche_b", "")
    niche = data.get("niche") or niche_a or ""
    subniches = data.get("subniches", [])
    language = data.get("language", "English")
    channel_name = data.get("channel_name", "")
    channel_ids = data.get("channel_ids", [])

    # If no viral_structures but niche_a+niche_b given → auto-generate structures via AI
    if not viral_structures and (niche_a or niche):
        auto_niche = niche_a or niche
        auto_prompt = f"""Generate 6 fictional but realistic viral YouTube title examples for the niche '{auto_niche}' in {language}.
Return ONLY a JSON array:
[
  {{"subniche": "{niche_b or auto_niche}", "title": "Example viral title", "tema": "main theme",
    "subtema": "specific angle", "structure": "Curiosity Gap", "views": "500K",
    "url": "https://youtube.com", "channel": "Example", "language": "{language}"}}
]
Make 6 items. Return ONLY valid JSON array."""
        auto_result = ask_gemini(auto_prompt)
        if not auto_result.startswith("[AI Error"):
            try:
                viral_structures = safe_parse_json(auto_result, "list")
            except Exception:
                pass

    structures_text = ""
    for s in viral_structures[:20]:
        structures_text += f"\n- [{s.get('subniche','')}] \"{s.get('title','')}\" (TEMA: {s.get('tema','')}, SUBTEMA: {s.get('subtema','')}, STRUCTURE: {s.get('structure','')}, Views: {s.get('views','')})"
    
    subniches_text = ", ".join(subniches[:10]) if subniches else niche
    
    prompt = f"""You are the world's #1 viral title strategist. You use a PROVEN dual-path system.

CHANNEL: {channel_name}
NICHE: {niche}
SUBNICHES: {subniches_text}
LANGUAGE: {language}

=== VIRAL TITLE DATABASE (real titles that went viral) ===
{structures_text}

=== YOUR MISSION: CREATE NEW VIRAL TITLES USING 2 STRATEGIES ===

**STRATEGY A — SWAP TEMA (keep SUBTEMA)**
Take a viral title structure and its SUBTEMA, but CHANGE the TEMA to another trending theme.
Example:
  Original: "1348: Sin duchas - La vida en la Edad Media" (TEMA=Edad Media, SUBTEMA=higiene)
  → Keep SUBTEMA (higiene) + swap TEMA (→ Tiempo de Jesús)
  → NEW: "Sin jabón ni agua: La higiene en tiempos de Jesús" = 757K views 🔥
  
WHY THIS WORKS: The SUBTEMA is validated (people love hygiene content), and the new TEMA brings fresh curiosity.

**STRATEGY B — SWAP SUBTEMA (keep TEMA)**  
Take a competitor's title structure and its TEMA, but CHANGE the SUBTEMA to something MORE viral.
Example:
  Original: "Como era viver em Tebas no Egito Antigo" (TEMA=Egito Antigo, SUBTEMA=viver em Tebas)
  → Keep TEMA (Egito Antigo) + swap SUBTEMA (→ higiene)
  → NEW: "Como era a higiene no Egito Antigo" = new title, same niche, viral subtema

WHY THIS WORKS: The TEMA is the channel's territory, and the new SUBTEMA is proven to get clicks.

=== OUTPUT FORMAT ===

Return a JSON object:
{{
  "strategy_a": [
    {{
      "original_title": "the viral title used as base",
      "original_tema": "original theme",
      "kept_subtema": "the subtema we're keeping",
      "new_tema": "the NEW theme we're applying",
      "new_title": "the generated viral title (60-100 chars)",
      "why_it_works": "brief explanation",
      "estimated_potential": "Low/Medium/High/Explosive"
    }}
  ],
  "strategy_b": [
    {{
      "original_title": "the competitor title used as base",
      "original_subtema": "original subtopic",
      "kept_tema": "the tema we're keeping",
      "new_subtema": "the NEW viral subtema we're applying",
      "new_title": "the generated viral title (60-100 chars)",
      "why_it_works": "brief explanation",
      "estimated_potential": "Low/Medium/High/Explosive"
    }}
  ]
}}

Generate AT LEAST 5 titles for Strategy A and 5 for Strategy B.
Each title MUST be 60-100 characters.
Use 1-2 CAPS words per title.
Every title must use a PROVEN viral structure from the database.

Return ONLY valid JSON. No markdown, no explanation."""
    
    result = ask_gemini(prompt)
    
    if result.startswith("[AI Error"):
        return jsonify({"error": result})
    
    try:
        # AI returns {strategy_a:[...], strategy_b:[...]} — parse as dict
        parsed = safe_parse_json(result, "dict")
        # Defensive: if AI returned a list instead of dict, wrap it
        if isinstance(parsed, list):
            parsed = {"strategy_a": parsed[:len(parsed)//2], "strategy_b": parsed[len(parsed)//2:]}
        
        # Analyze each generated title
        for strategy in ["strategy_a", "strategy_b"]:
            for item in parsed.get(strategy, []):
                if not isinstance(item, dict):
                    continue
                title = item.get("new_title", "")
                if title:
                    analysis = analyze_title(title)
                    item["score"] = analysis["score"]
                    item["grade"] = analysis["grade"]
                    item["length"] = len(title)
                    item["structures_detected"] = [s["name"] for s in analysis["structures"]]
        
        return jsonify(parsed)
    except Exception as e:
        return jsonify({"error": f"JSON parse: {str(e)[:80]}", "raw": result})

@app.route("/api/trend_scanner", methods=["POST"])
def api_trend_scanner():
    """Scan for trending topics and emerging niches."""
    data = request.json or {}
    category = data.get("category", "all")
    language = data.get("language", "English")
    
    prompt = f"""You are a YouTube trend analyst with access to the latest data.

CATEGORY: {category if category != 'all' else 'All categories'}
LANGUAGE: {language}
DATE: {datetime.now().strftime('%B %Y')}

Analyze current YouTube trends and provide a structured JSON response.

Return ONLY a valid JSON object in this exact format:
{{
  "trending_themes": [
    {{
      "name": "Theme name",
      "why_trending": "Why it is popular right now",
      "demand": 9,
      "competition": 4,
      "best_angle": "The best way to approach this",
      "example_title": "Example viral title"
    }}
  ],
  "emerging_niches": [
    {{
      "name": "Micro-niche name",
      "opportunity_score": 8,
      "target_audience": "Who watches this",
      "example_titles": ["Title 1", "Title 2"]
    }}
  ],
  "dying_niches": [
    {{
      "name": "Niche name",
      "reason": "Why it is losing traction"
    }}
  ]
}}

Provide 8 trending themes, 4 emerging niches, and 2 dying niches.
Return ONLY valid JSON. No markdown outside the JSON."""

    result = ask_gemini(prompt)
    
    if result.startswith("[AI Error"):
        return jsonify({"error": result})
        
    try:
        cleaned = result.strip()
        cleaned = re.sub(r'^```json\s*', '', cleaned)
        trends_json = safe_parse_json(result, "dict")
        
        return jsonify({"trends_data": trends_json, "category": category, "date": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}", "raw": result})

# =============================================
# YOUTUBE DATA API — Real metrics
# =============================================
def _load_yt_key():
    # 1. Try core module
    try:
        from core.api_keys import load_api_key
        k = load_api_key("youtube")
        if k: return k
    except Exception:
        pass
    # 2. Try .api_keys.json directly
    import base64
    for search_dir in [BASE_DIR, PARENT_DIR]:
        kf = os.path.join(search_dir, ".api_keys.json")
        if os.path.exists(kf):
            try:
                with open(kf) as f:
                    data = json.load(f)
                encoded = data.get("youtube", "")
                if encoded:
                    return base64.b64decode(encoded).decode()
            except Exception:
                pass
    # 3. Environment variable
    return os.environ.get("YOUTUBE_API_KEY", "")

YOUTUBE_API_KEY = _load_yt_key()

@app.route("/api/youtube/save_key", methods=["POST"])
def save_yt_key():
    global YOUTUBE_API_KEY
    data = request.json or {}
    key = data.get("key", "").strip()
    if not key:
        return jsonify({"error": "No key"}), 400
    # Try core module first
    try:
        from core.api_keys import save_api_key
        save_api_key("youtube", key)
    except Exception:
        # Fallback: save directly to .api_keys.json
        import base64
        for search_dir in [BASE_DIR, PARENT_DIR]:
            kf = os.path.join(search_dir, ".api_keys.json")
            try:
                data_existing = {}
                if os.path.exists(kf):
                    with open(kf) as f:
                        data_existing = json.load(f)
                data_existing["youtube"] = base64.b64encode(key.encode()).decode()
                with open(kf, "w") as f:
                    json.dump(data_existing, f, indent=2)
                break
            except Exception:
                pass
    YOUTUBE_API_KEY = key
    return jsonify({"status": "ok", "key_saved": True})


@app.route("/api/youtube/channel", methods=["POST"])
def yt_channel():
    """Analyze a YouTube channel with real data."""
    from core.youtube_api import get_channel_info, get_channel_videos
    data = request.json or {}
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
    data = request.json or {}
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
    data = request.json or {}
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
    data = request.json or {}
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
    data = request.json or {}
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
    data = request.json or {}
    cid = data.get("id")
    channels = [c for c in _load_channels() if c.get("id") != cid]
    _save_channels(channels)
    return jsonify({"status": "ok"})

@app.route("/api/channels/analyze", methods=["POST"])
def analyze_channel():
    """AI analysis — discovers NEW subniches by combining validated trends + validated structures."""
    data = request.json or {}
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

Analyze this channel and provide a completely structured JSON response.

Return ONLY a valid JSON object in this exact format:
{{
  "dna_analysis": "One concise paragraph explaining what works for this channel's DNA.",
  "new_subniches": [
    {{
      "name": "New Subniche Name",
      "why_it_works": "Why it has demand but low supply",
      "competition": "Low",
      "pain_point": "Target audience pain point",
      "example_titles": ["Title 1", "Title 2", "Title 3"]
    }}
  ],
  "action_plan": [
    {{
      "topic": "Video topic",
      "priority_subniche": "Subniche to target",
      "structure_used": "Which viral structure is applied"
    }}
  ]
}}

Provide 5 NEW subniches (not currently used) and a 5-video action plan.
Return ONLY valid JSON. No markdown outside the JSON."""

    result = ask_gemini(prompt)
    
    if result.startswith("[AI Error"):
        return jsonify({"error": result})
        
    try:
        cleaned = result.strip()
        cleaned = re.sub(r'^```json\s*', '', cleaned)
        analysis_json = safe_parse_json(result, "dict")
        
        return jsonify({"analysis_data": analysis_json, "channel": channel})
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}", "raw": result})

@app.route("/api/channels/update_metrics", methods=["POST"])
def update_channel_metrics():
    """Update channel with performance metrics for AI analysis."""
    data = request.json or {}
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
    data = request.json or {}
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

@app.route("/api/vph_radar", methods=["POST"])
def api_vph_radar():
    """Live VPH Niche Analysis - fetches real videos from the last 14 days and extracts themes/structures."""
    from core.youtube_api import search_trending
    from datetime import datetime, timezone, timedelta
    
    data = request.json or {}
    niche = data.get("niche", "")
    language = data.get("language", "English")
    key = YOUTUBE_API_KEY
    
    if not key:
        return jsonify({"error": "YouTube API key not set. Go to Settings tab."}), 400
        
    two_weeks_ago = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    try:
        videos = search_trending(niche, key, max_results=30, published_after=two_weeks_ago)
    except Exception as e:
        return jsonify({"error": f"Failed to fetch from YouTube: {str(e)}"}), 500
        
    if not videos:
        return jsonify({"error": f"No recent videos found for '{niche}' in the last 14 days."}), 404
        
    videos.sort(key=lambda x: x.get("vph", 0), reverse=True)
    top_videos = videos[:15]
    
    video_list_str = "\n".join([f"- Title: {v['title']} (Views: {v.get('views', 0)}, VPH: {v.get('vph', 0)})" for v in top_videos])
    
    prompt = f"""You are an elite YouTube data analyst.
I have fetched the top 15 highest Views-Per-Hour (VPH) videos published in the last 14 days for the niche: "{niche}".
Language target: {language}

Here is the raw data (REAL high-performance videos right now):
{video_list_str}

Analyze this live data and provide a strictly structured JSON response.

Return ONLY a valid JSON object in this exact format:
{{
  "viral_structures": [
    {{
      "name": "Structure Name",
      "pattern": "Why this specific structure is getting clicks right now",
      "example_from_data": "Quote a title from the data"
    }}
  ],
  "viral_themes": [
    {{
      "theme": "Core theme driving VPH",
      "why_its_hot": "Why audiences are obsessed with this right now"
    }}
  ],
  "new_perspectives": [
    {{
      "new_angle": "A completely new perspective/subtheme based on the hot themes",
      "why_it_will_win": "Why this avoids competition but keeps the viral DNA",
      "generated_titles": ["Title 1", "Title 2"]
    }}
  ]
}}

Provide 3 viral structures, 3 viral themes, and 4 new perspectives.
Return ONLY valid JSON. No markdown outside the JSON."""

    result = ask_gemini(prompt)
    if result.startswith("[AI Error"):
        return jsonify({"error": result})
        
    try:
        cleaned = result.strip()
        cleaned = re.sub(r'^```json\s*', '', cleaned)
        radar_json = safe_parse_json(result, "dict")
        
        # Attach the raw videos for the UI
        radar_json["top_videos"] = top_videos
        
        return jsonify({"radar_data": radar_json})
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}", "raw": result})

@app.route("/api/ab_simulate", methods=["POST"])
def api_ab_simulate():
    """Simulate an A/B test between two titles and generate a thumbnail concept."""
    data = request.json or {}
    title_a = san(data.get("title_a", ""))
    title_b = san(data.get("title_b", ""))
    niche    = san(data.get("niche", ""))
    language = san(data.get("language", "English"))
    
    if not title_a:
        return jsonify({"error": "Título A é obrigatório"}), 400
    if not title_b:
        return jsonify({"error": "Título B é obrigatório"}), 400
        
    prompt = f"""You are the ultimate YouTube A/B testing algorithm. 
I am going to give you two titles for the exact same video.

NICHE: {niche}
LANGUAGE: {language}
TITLE A: "{title_a}"
TITLE B: "{title_b}"

You must predict the winner based on human psychology, curiosity gaps, clarity, and viral structures.
Provide a strictly structured JSON response.

Return ONLY a valid JSON object in this exact format:
{{
  "winner": "A or B",
  "winner_score": 95,
  "loser_score": 75,
  "analysis": "A concise paragraph explaining exactly why the winner triggers more clicks.",
  "thumbnail_concept": {{
    "visual": "Describe the main visual element (e.g., Extreme close up of...)",
    "text": "The exact text to overlay on the thumbnail (max 3 words)",
    "emotion": "The core emotion it should evoke"
  }},
  "video_hook": "Write the first 15 seconds (script) of the video that perfectly delivers on the winning title's promise."
}}

Return ONLY valid JSON. No markdown outside the JSON."""

    result = ask_gemini(prompt)
    if result.startswith("[AI Error"):
        return jsonify({"error": result})
        
    try:
        cleaned = result.strip()
        cleaned = re.sub(r'^```json\s*', '', cleaned)
        ab_json = safe_parse_json(result, "dict")
        
        return jsonify({"ab_data": ab_json})
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {str(e)[:80]}", "raw": result})

# =============================================
# HEALTH CHECK & GLOBAL ERROR HANDLERS
# =============================================
_SERVER_START_TIME = time.time()

@app.route("/api/health", methods=["GET"])
def api_health():
    """Health check endpoint — always returns JSON, never crashes."""
    try:
        uptime = round(time.time() - _SERVER_START_TIME)
    except Exception:
        uptime = 0
    try:
        ai_ok = bool(_GROQ_KEY and _GROQ_KEY != "GROQ_KEY_PLACEHOLDER")
    except Exception:
        ai_ok = False
    try:
        yt_key = bool(globals().get("YOUTUBE_API_KEY", ""))
    except Exception:
        yt_key = False
    try:
        # Count channels from file directly — no function dependency
        ch_file = os.path.join(BASE_DIR, "channels.json")
        channels_n = len(json.load(open(ch_file)).get("channels", [])) if os.path.exists(ch_file) else 0
    except Exception:
        channels_n = 0
    return jsonify({
        "status": "ok",
        "uptime_seconds": uptime,
        "ai_status": "ok" if ai_ok else "no_key",
        "model": "llama-3.1-8b-instant",
        "youtube_key": yt_key,
        "channels_loaded": channels_n,
        "version": "2.1"
    })

@app.errorhandler(404)
def err_404(e):
    return jsonify({"error": f"Route not found: {request.path}"}), 404

@app.errorhandler(405)
def err_405(e):
    return jsonify({"error": f"Method not allowed: {request.method} {request.path}"}), 405

@app.errorhandler(500)
def err_500(e):
    return jsonify({"error": f"Internal server error: {str(e)[:100]}"}), 500

@app.errorhandler(Exception)
def err_generic(e):
    import traceback
    tb = traceback.format_exc()[-300:]
    return jsonify({"error": f"Unexpected error: {str(e)[:100]}", "trace": tb}), 500

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
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
