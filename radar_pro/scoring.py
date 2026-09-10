# -*- coding: utf-8 -*-
"""
================================================================================
 RADAR PRO — Scoring & classificação  (v2, calibrado no Radar Club real)
================================================================================
Reproduz o modelo de dados que engenharia-reversamos da API do Radar Club
(/api/radar/feed) e ADICIONA nossas melhorias.

Campos que espelham os deles:
  darkScore (0-100, o 🔥) · viewsPerSub (o "×inscritos") · scorePotencial ·
  facelessEstimate · monetized · badge · crescimento (texto) · categoria (14) ·
  fonte (seed/community/mined) · alinhamento ("✅ Mantido").

Nossas melhorias (que eles NÃO têm): VPH por vídeo/hit/médio, canais similares,
niche overview — tratados nos outros módulos.
================================================================================
"""
import re
import math
from datetime import datetime, timezone

# ------------------------------------------------------------------ CATEGORIAS (14 — iguais ao Radar Club)
# chave interna -> (rótulo exibido, emoji, palavras-chave EN/ES/PT)
CATEGORIES = {
    "musica":       ("Música & Relax", "🎵", ["music", "song", "beat", "musica", "música", "canción",
                                              "instrumental", "playlist", "mix", "anthem", "bolero", "jazz"]),
    "relax":        ("Música & Relax", "🎵", ["sleep", "relax", "432hz", "528hz", "meditation", "lofi",
                                              "ambient", "dormir", "relajante", "healing", "calm", "asmr"]),
    "religiao":     ("Religião & Bíblia", "✝️", ["bible", "biblia", "bíblia", "god", "jesus", "dios",
                                                "prophet", "profeta", "gospel", "faith", "fe", "sagrado",
                                                "apocalipse", "biblical", "cristiano", "espiritual"]),
    "ciencia":      ("Ciência & Espaço", "🚀", ["science", "space", "universe", "nasa", "physics", "ciencia",
                                               "espacio", "universo", "cosmos", "planet", "quantum", "voyager",
                                               "black hole", "galaxy", "átomo", "atom"]),
    "crime":        ("True Crime", "🔪", ["true crime", "murder", "killer", "police", "detective", "asesino",
                                         "crimen", "homicide", "case", "arrest", "predator", "dna", "cold case"]),
    "curiosidades": ("Curiosidades & Fatos", "🤯", ["amazing", "satisfying", "factory", "manufacturing", "process",
                                                    "recycling", "how it's made", "facts", "did you know", "trick",
                                                    "hack", "machine", "woodworking", "incredible"]),
    "historia":     ("História", "🏛️", ["history", "historia", "história", "ancient", "empire", "medieval",
                                        "roman", "egypt", "pharaoh", "civilization", "dynasty", "revolución",
                                        "century", "documentário", "documental", "imperio", "guerreros"]),
    "financas":     ("Finanças & Riqueza", "💰", ["money", "rich", "wealth", "finance", "billionaire", "dinero",
                                                 "riqueza", "millonario", "business", "investing", "ceo",
                                                 "millionaire", "fortune", "gold mine", "paint", "make money"]),
    "natureza":     ("Natureza & Animais", "🦁", ["animal", "predator", "wildlife", "nature", "dog", "cat",
                                                 "creature", "shark", "spider", "araña", "pez", "depredador",
                                                 "documentary predators", "colossal", "squid", "wild"]),
    "geopolitica":  ("Geopolítica", "🌍", ["war footage", "combat", "russia", "ukraine", "china", "mercenaries",
                                          "geopolit", "military position", "azov", "brigade", "conflict",
                                          "border", "invasion"]),
    "guerra":       ("Guerra", "⚔️", ["ww2", "wwii", "world war", "battle", "soldier", "isis", "combat",
                                      "guerra", "batalla", "batalha", "50 cal", "barrett", "trench", "regiment"]),
    "misterio":     ("Mistério & Terror", "👻", ["mystery", "scary", "creepy", "paranormal", "horror", "misterio",
                                                "terror", "haunted", "unexplained", "fosa", "marianas", "prehistoric",
                                                "trapped", "amber", "deep sea"]),
    "motivacao":    ("Motivação & Estoicismo", "💪", ["motivation", "stoic", "discipline", "mindset", "estoico",
                                                     "estoicismo", "disciplina", "success", "wisdom", "hábitos",
                                                     "self improvement", "marcus aurelius", "seneca"]),
    "geral":        ("Outros", "📦", []),
}

# emoji/rótulo rápido
def cat_label(key):
    return CATEGORIES.get(key, CATEGORIES["geral"])[0]

def cat_emoji(key):
    return CATEGORIES.get(key, CATEGORIES["geral"])[1]

# ruído (drama/novela/CJK que não é canal dark)
_NOISE_CJK = re.compile(r'[぀-ヿ가-힯]')  # japonês kana / coreano (chinês fica, muitos docs usam)
_NOISE_NAME = re.compile(r'\b(kdrama|cdrama|vietsub|shortdrama)\b', re.I)


def is_noise(name, text=""):
    return bool(_NOISE_NAME.search(name or ""))


def detect_category(name, description, titles, topic_cats=None):
    blob = " ".join([name or "", description or "", " ".join(titles or [])]).lower()
    scores = {}
    for key, (_lbl, _emo, words) in CATEGORIES.items():
        if not words:
            continue
        scores[key] = sum(blob.count(w) for w in words)
    if topic_cats:
        tc = " ".join(topic_cats).lower()
        boost = {"music": "musica", "military": "guerra", "history": "historia",
                 "religion": "religiao", "science": "ciencia", "knowledge": "historia",
                 "nature": "natureza", "pets": "natureza"}
        for k, key in boost.items():
            if k in tc:
                scores[key] = scores.get(key, 0) + 3
    best = max(scores, key=scores.get) if scores else "geral"
    return best if scores.get(best, 0) > 0 else "geral"


def detect_language(titles):
    """Detecta o idioma dominante. 10 idiomas (iguais ao Radar Club):
    EN, PT, ES, FR, DE, IT, RU, HI, AR (+ '?')."""
    blob = " ".join(titles or []).lower()
    # scripts não-latinos: contam caracteres
    ru = len(re.findall(r'[а-яё]', blob))
    hi = len(re.findall(r'[ऀ-ॿ]', blob))          # devanagari
    ar = len(re.findall(r'[؀-ۿ]', blob))          # árabe
    # idiomas latinos: palavras-função
    es = len(re.findall(r'\b(de|la|el|los|las|que|del|con|una|en|por|cómo|así|para)\b', blob))
    pt = len(re.findall(r'\b(do|dos|das|no|na|nos|nas|como|para|pelo|pela|foi|você|é|não)\b', blob))
    en = len(re.findall(r'\b(the|of|and|for|with|from|that|this|how|why|what|were|they)\b', blob))
    fr = len(re.findall(r'\b(le|la|les|des|une|dans|pour|avec|qui|est|sur|comment|pourquoi|du|au)\b', blob))
    de = len(re.findall(r'\b(der|die|das|und|ist|ein|eine|mit|von|für|wie|warum|nicht|auch|den)\b', blob))
    it = len(re.findall(r'\b(il|lo|gli|di|che|per|con|una|del|come|perché|questo|sono|nella)\b', blob))
    scores = {"RU": ru * 2, "HI": hi * 2, "AR": ar * 2, "ES": es, "PT": pt,
              "EN": en, "FR": fr, "DE": de, "IT": it}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "?"


# rótulos p/ o filtro de idioma no front
LANGUAGES = [("EN", "Inglês"), ("PT", "Português"), ("ES", "Espanhol"),
             ("FR", "Francês"), ("DE", "Alemão"), ("IT", "Italiano"),
             ("RU", "Russo"), ("HI", "Hindi"), ("AR", "Árabe")]


# ------------------------------------------------------------------ MÉTRICAS BASE
def _fmt(n):
    n = n or 0
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    if n >= 1e3:
        return f"{n/1e3:.1f}K"
    return str(int(n))


def channel_metrics(subs, video_count, created_days, videos):
    """Calcula ritmo do canal a partir dos vídeos recentes + stats."""
    subs = max(subs or 0, 0)
    best = max(videos, key=lambda v: v["views"]) if videos else None
    top_views = best["views"] if best else 0
    views_per_sub = round(top_views / max(subs, 1), 1)
    months = max((created_days or 30) / 30.0, 0.5)
    vids_per_month = round((video_count or len(videos)) / months, 1)
    total_recent_views = sum(v["views"] for v in videos) if videos else 0
    # views/mês estimada pela média recente × ritmo
    avg_recent = total_recent_views / max(len(videos), 1)
    views_per_month = round(avg_recent * min(vids_per_month, 30))
    days_since_upload = min((v["days_since"] for v in videos), default=999) if videos else 999
    return {
        "top_views": top_views, "views_per_sub": views_per_sub,
        "vids_per_month": vids_per_month, "views_per_month": views_per_month,
        "days_since_upload": round(days_since_upload, 1), "best": best,
    }


# ------------------------------------------------------------------ DARK SCORE (o 🔥, 0-100)
def dark_score(subs, video_count, created_days, videos, m=None):
    """Score viral principal (0-100), calibrado para se comportar como o darkScore
    do Radar Club: canal PEQUENO com hit recente e ritmo bom pontua ALTO."""
    if not videos:
        return 0.0
    m = m or channel_metrics(subs, video_count, created_days, videos)
    subs = max(subs or 0, 1)
    top = m["top_views"]
    vps = m["views_per_sub"]
    max_vph = max(v["vph"] for v in videos)
    # componentes (cada 0-100)
    mult = min(100, max(0, math.log10(max(vps, 0.1)) * 33 + 33))          # punch-above-weight
    absv = min(100, max(0, (math.log10(max(top, 1)) - 3) * 22))            # tamanho do hit
    speed = min(100, max(0, (math.log10(max(max_vph, 1)) - 1) * 40))       # VPH
    age = m["days_since_upload"]
    fresh = 100 if age <= 3 else 80 if age <= 7 else 55 if age <= 14 else 30 if age <= 30 else 10
    hits = sum(1 for v in videos if v["days_since"] <= 45 and v["views"] >= max(top * 0.1, 5000))
    consist = min(100, hits * 20)
    score = 0.30 * mult + 0.24 * absv + 0.18 * speed + 0.16 * fresh + 0.12 * consist
    return round(min(score, 100), 0)


def score_potencial(subs, videos, niche_median_top=None):
    """Potencial relativo ao nicho: hit do canal ÷ mediana do nicho (aprox)."""
    if not videos:
        return 0.0
    top = max(v["views"] for v in videos)
    base = niche_median_top or max(top, 1)
    return round(top / max(base, 1), 6)


# ------------------------------------------------------------------ FACELESS / MONETIZADO (estimativas)
_FACE_HINT = re.compile(r'\b(vlog|my |i tried|reaction|reacciona|face reveal|q&a|storytime)\b', re.I)
_FACELESS_CATS = {"historia", "ciencia", "misterio", "religiao", "curiosidades", "natureza",
                  "financas", "guerra", "geopolitica", "relax", "musica"}


def faceless_estimate(category, name, titles):
    """0-100: probabilidade de o canal ser faceless (sem rosto). Heurística:
    categorias documentais/dark tendem a faceless; sinais de vlog/reaction reduzem."""
    base = 70 if category in _FACELESS_CATS else 35
    blob = (name or "") + " " + " ".join(titles or [])
    if _FACE_HINT.search(blob):
        base -= 35
    if category in ("relax", "musica", "misterio", "historia", "ciencia"):
        base += 12
    return max(0, min(100, base))


def monetized_estimate(subs, video_count, created_days, top_views):
    """Estimativa de monetização (a API não expõe). Regra de elegibilidade YPP:
    >=1000 subs + histórico + tração real."""
    return bool((subs or 0) >= 1000 and (video_count or 0) >= 3
                and (created_days or 0) >= 30 and (top_views or 0) >= 10000)


# ------------------------------------------------------------------ BADGE + CRESCIMENTO (texto explicativo)
def growth_text(subs, video_count, created_days, videos, m):
    """Reproduz as frases de 'crescimento' do Radar Club."""
    top = m["top_views"]
    vc = video_count or len(videos)
    avg = top / max(vc, 1)
    if vc <= 9 and avg >= max(top * 0.5, 500000):
        return f"↑ Poucos vídeos ({vc}) com views/vídeo muito altas ({_fmt(top)}/vídeo)"
    # inscritos altos p/ idade
    if created_days and subs and subs / max(created_days, 1) > 50:
        return "↑ Inscritos em relação à idade do canal acima da média"
    if m["vids_per_month"] >= 4 and m["views_per_month"] >= 50000:
        return "↑ Canal jovem com desempenho consistente ao longo dos uploads"
    sp = score_potencial(subs, videos)
    return f"↑ Potencial relativo ao nicho (score {sp:.6f})"


def badge_for(score, m, video_count):
    if score >= 80:
        return "Explosivo"
    if score >= 65:
        return "Em Alta"
    if score >= 45:
        return "Constante"
    return "Observar"


def alinhamento(category, faceless, subs, videos):
    """'✅ Mantido' = passou no filtro de qualidade dark mesmo com sinal fraco."""
    if faceless >= 45 and category != "geral":
        return True
    return False


# ------------------------------------------------------------------ TIERS (as 8 abas)
def _days_since(iso):
    if not iso:
        return 9999
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - d).total_seconds() / 86400
    except Exception:
        return 9999


def tiers_for(ch):
    """Abas às quais o canal pertence (chaves iguais ao Radar Club)."""
    t = []
    score = ch["viral_score"] or 0
    subs = ch["subs"] or 0
    mult = ch["subs_multiplier"] or 0
    best = ch["best_views"] or 0
    fresh_add = _days_since(ch["first_seen"]) <= 7
    fresh_upl = _days_since(ch["newest_upload"]) <= 10
    if ch["is_music"]:
        t.append("musica")
    if score >= 80:
        t.append("extremo")
    if score >= 60:
        t.append("viral")
    if score >= 60 and fresh_upl:
        t.append("viral-recente")
    if subs < 100_000 and mult >= 5 and best >= 20_000:
        t.append("oportunidade")
    if 40 <= score < 60:
        t.append("bom")
    if fresh_add:
        t.append("recentes")
    t.append("todos")
    return t


TIER_LABELS = [
    ("extremo",       "🔥 Extremamente Virais"),
    ("viral",         "🚀 Virais"),
    ("viral-recente", "🌱 Virais Recentes"),
    ("oportunidade",  "💎 Oportunidades"),
    ("bom",           "✅ Bons Canais"),
    ("recentes",      "🆕 Recém Adicionados"),
    ("musica",        "🎵 Canais de Música"),
    ("todos",         "🌐 Todos"),
]

# lista para o front (chave, rótulo, emoji)
CATEGORY_LIST = [(k, v[0], v[1]) for k, v in CATEGORIES.items() if k != "geral"] + \
                [("geral", "Outros", "📦")]
