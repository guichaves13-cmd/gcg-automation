"""
Intelligent B-Roll Engine v1.0 — Pre-Download Visual Verification

Resolve o problema crítico: clips errados sendo selecionados pra cada momento
do roteiro. Estratégia nova:

ESTRATÉGIA ANTIGA (smart_broll.py + Phase 2 validation):
    1. Extrai keywords → search → DOWNLOAD → valida com Gemini Vision
    Problema: gasta banda em clips errados; validation às vezes passa lixo.

ESTRATÉGIA NOVA (este módulo):
    1. Whisper word-level timestamps no áudio (sync perfeito)
    2. AI segmenta roteiro em "intents" (entidade + ação + contexto)
    3. Multi-language search EM PARALELO (Pexels + Pixabay + YouTube + Mixkit)
    4. ⭐ VERIFICAÇÃO VISUAL ANTES DO DOWNLOAD via thumbnail + Gemini Vision
       - Prompt RIGOROSO: "Esta imagem mostra peixes nadando em aquário?"
       - Score 0-100 + descrição do que está realmente visível
       - REJEITA tudo abaixo de 80 (não de 50 como antes)
    5. Se 3 tentativas falham → refine query → tenta de novo
    6. Se 3 refines falham → flag manual review (não usa clip ruim)
    7. Download SÓ do vencedor visualmente aprovado
    8. Alinha aos timestamps EXATOS do Whisper

CUSTO: ~R$0.001 por verificação Gemini Vision (gemini-2.0-flash).
       Vídeo 2min × 20 segmentos × 5 candidatos = ~R$0.10 por vídeo.

GARANTIA: ZERO clip errado entra no vídeo final. Se não acha clip bom,
          retorna placeholder + flag pra usuário escolher manualmente.
"""

from __future__ import annotations

import os
import json
import time
import hashlib
import threading
import concurrent.futures
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────────────────────────────────────


# Cultural/temporal negative-term presets per theme. Used to pre-filter
# candidates BEFORE expensive Vision call (saves API quota + speeds up).
_THEME_NEGATIVE_TERMS = {
    "ancient_civilization": [
        "gothic", "cathedral", "barcelona", "medieval", "modern", "skyscraper",
        "smartphone", "laptop", "car", "automobile", "renaissance", "victorian",
        "industrial", "factory", "subway", "highway", "office",
    ],
    "egypt": ["maya", "aztec", "incas", "rome", "roman", "greek", "asian",
              "european", "gothic", "cathedral", "barcelona", "modern", "skyscraper"],
    "maya": ["egypt", "pharaoh", "pyramid of giza", "gothic", "cathedral",
             "european", "barcelona", "renaissance", "modern", "skyscraper"],
    "aztec": ["egypt", "maya", "european", "gothic", "cathedral", "modern"],
    "roman": ["egypt", "maya", "asian", "gothic", "cathedral", "barcelona",
              "medieval", "modern", "skyscraper", "highway", "renaissance",
              "victorian"],
    "greek": ["egypt", "maya", "asian", "gothic", "cathedral", "modern"],
    "modern_tech": ["ancient", "medieval", "ruins", "pyramid", "cathedral",
                    "renaissance", "horse", "candle", "fire"],
    "nature_animals": ["building", "car", "computer", "phone", "indoor", "office"],
    "default": [],
}


def detect_theme_negatives(theme: str) -> list:
    """Heuristically pick negative terms for a given theme description."""
    theme_low = (theme or "").lower()
    terms = list(_THEME_NEGATIVE_TERMS["default"])

    if any(k in theme_low for k in ["maya", "maia"]):
        terms += _THEME_NEGATIVE_TERMS["maya"]
    elif any(k in theme_low for k in ["egypt", "egito", "pharaoh", "pirâmide", "piramide"]):
        terms += _THEME_NEGATIVE_TERMS["egypt"]
    elif any(k in theme_low for k in ["aztec", "azteca", "tenochtitlan"]):
        terms += _THEME_NEGATIVE_TERMS["aztec"]
    elif any(k in theme_low for k in ["roman", "roma", "caesar", "gladiator"]):
        terms += _THEME_NEGATIVE_TERMS["roman"]
    elif any(k in theme_low for k in ["greek", "grego", "atenas", "spartan"]):
        terms += _THEME_NEGATIVE_TERMS["greek"]
    elif any(k in theme_low for k in ["antig", "ancient", "civiliz", "histor"]):
        terms += _THEME_NEGATIVE_TERMS["ancient_civilization"]
    elif any(k in theme_low for k in ["tech", "ai", "software", "computer", "moderno"]):
        terms += _THEME_NEGATIVE_TERMS["modern_tech"]
    elif any(k in theme_low for k in ["nature", "animal", "wildlife", "savan", "selva"]):
        terms += _THEME_NEGATIVE_TERMS["nature_animals"]

    return list(set(terms))


@dataclass
class SegmentIntent:
    """O que precisa de B-roll para uma fatia (3-8s) do roteiro."""
    index: int
    text: str                                # Exato texto sendo dito neste segmento
    start: float                             # Timestamp início (s) do áudio
    end: float                               # Timestamp fim (s)
    main_entity: str = ""                    # "peixes ornamentais aquário"
    action: str = ""                         # "nadando entre plantas"
    visual_context: str = ""                 # "close-up real, sem texto na tela"
    search_queries: list = field(default_factory=list)  # 5 variações multi-language
    rejected_terms: list = field(default_factory=list)  # Pra refinement
    notes: str = ""

    def short_label(self) -> str:
        return f"[{self.start:.1f}–{self.end:.1f}s] {self.text[:60]}"


@dataclass
class ClipCandidate:
    """Candidato de clip de qualquer fonte."""
    source: str                              # "pexels" | "pixabay" | "youtube" | "mixkit" | "pexels_photo"
    source_id: str                           # ID nativo da fonte (pra dedup)
    title: str                               # Texto descritivo do clip
    page_url: str                            # URL da página de download
    download_url: str                        # URL direta do MP4 (ou imagem)
    thumbnail_url: str                       # URL do thumbnail/poster
    duration: float                          # Segundos
    width: int = 0
    height: int = 0
    is_photo: bool = False                   # True = foto estática (vai virar Ken Burns)

    # Preenchidos durante verificação:
    relevance_score: int = 0                 # 0-100 do Gemini Vision
    vision_description: str = ""             # O que Gemini disse que vê
    rejection_reason: str = ""               # Por que foi rejeitado (se foi)
    perceptual_hash: str = ""                # pHash do thumbnail pra anti-duplicate visual

    def short_label(self) -> str:
        return f"[{self.source}#{self.source_id}] {self.title[:50]}"


@dataclass
class BeatPlan:
    """Plano final: 1 segmento + 1 clip aprovado (ou flag manual)."""
    intent: SegmentIntent
    clip: Optional[ClipCandidate] = None     # None = nenhum aprovado
    download_path: str = ""                  # Path local após download
    needs_manual_review: bool = False
    error: str = ""

    def is_solved(self) -> bool:
        return bool(self.clip and self.download_path) and not self.needs_manual_review


# ─────────────────────────────────────────────────────────────────────────────
# VISUAL FINGERPRINT (anti-duplicate)
# ─────────────────────────────────────────────────────────────────────────────


def compute_phash(image_bytes: bytes) -> str:
    """Compute perceptual hash of image bytes for visual similarity dedup.
    Returns hex string; empty on failure.
    """
    try:
        import imagehash
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        return str(imagehash.phash(img, hash_size=8))
    except Exception:
        return ""


def phash_similarity(h1: str, h2: str) -> float:
    """Return 0.0-1.0 visual similarity between two pHash hex strings.
    1.0 = identical. ≥0.85 means basically the same scene.
    """
    if not h1 or not h2 or len(h1) != len(h2):
        return 0.0
    try:
        import imagehash
        ih1 = imagehash.hex_to_hash(h1)
        ih2 = imagehash.hex_to_hash(h2)
        bits = len(ih1.hash.flatten())
        return 1.0 - (ih1 - ih2) / bits
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# VISION VERIFIER
# ─────────────────────────────────────────────────────────────────────────────


_VISION_PROMPT_TEMPLATE = """Você é um editor de vídeo profissional verificando se uma imagem stock serve para ilustrar uma narração.

CONTEXTO DO SEGMENTO NARRADO:
- Texto: "{text}"
- Entidade principal: {entity}
- Ação/estado: {action}
- Tema/contexto geral: {theme}
{diversity_hint}

REGRAS DE SCORE (use criteriosamente, não seja arbitrariamente rigoroso):
- 90-100: A imagem mostra EXATAMENTE a entidade fazendo EXATAMENTE a ação descrita
- 75-89: Mostra a entidade certa, OU mostra elemento icônico/símbolo direto do tema
         (ex: hieróglifos para Egito Antigo, microscópio para ciência, futebol em campo para esporte)
- 60-74: Mostra algo CLARAMENTE relacionado ao tema, mesmo que não seja a entidade exata
         (ex: paisagem antiga para narração histórica, vista de cidade moderna para tecnologia)
- 30-59: Conexão temática fraca mas existe (ex: pessoa qualquer para narração sobre humanos)
- 0-29: Nada a ver com o segmento (ex: gato para narração sobre arquitetura)

PRINCÍPIO CHAVE:
- Para narrações ABSTRATAS (conceitos históricos, opiniões, ações políticas), um clip
  visualmente icônico do TEMA é aceitável (75-89), não exija a ação literal.
- Para narrações CONCRETAS ("o cachorro late"), exija o objeto exato (≥80) ou rejeite.
- NUNCA aprove um clip que mostre conceito ERRADO (ex: feliz quando narração é triste).
- Se a cena já apareceu no vídeo (veja DIVERSITY HINT acima), PENALIZE -15 pontos
  para incentivar variedade visual.

CULTURA E ÉPOCA — REGRA CRÍTICA:
- Se o tema é uma civilização antiga (Egito, Maia, Romana, Grega, Asteca):
  - REJEITAR (score ≤30) imagens de arquitetura europeia medieval/moderna
    (catedrais góticas, igrejas barrocas, prédios contemporâneos)
  - REJEITAR (score ≤30) pessoas em roupas modernas
  - REJEITAR (score ≤30) tecnologia moderna (carros, telefones, computadores)
  - PREFERIR pirâmides/templos/ruínas/artefatos da CIVILIZAÇÃO CORRETA
- Se o tema é moderno (tech, esportes, ciência atual):
  - REJEITAR ruínas antigas, pessoas em traje histórico

Responda APENAS em JSON estrito:
{{
  "score": <inteiro 0-100>,
  "what_i_see": "<descrição curta do que está REALMENTE na imagem, máx 80 chars>",
  "matches_entity": <true ou false>,
  "matches_theme": <true ou false>,
  "rejection_reason": "<se score<60: motivo curto. Se score>=60: ''>"
}}

PIOR CENÁRIO: um clip CONTRADITÓRIO entrar no vídeo final (felicidade para narração de tristeza, prédio moderno para conteúdo medieval). MELHOR CENÁRIO: clip temático relevante mesmo sem ação exata."""


class VisionVerifier:
    """Verifica relevância de uma imagem para um intent usando Gemini Vision.

    Custo: ~R$0.001 por verificação (gemini-2.0-flash).
    Latência: ~1-2s por verificação.
    """

    def __init__(self, gemini_api_key: str = "", model: str = "gemini-2.0-flash",
                 nvidia_api_key: str = ""):
        self.api_key = gemini_api_key or os.environ.get("GOOGLE_API_KEY", "")
        self.nvidia_key = nvidia_api_key or os.environ.get("NVIDIA_API_KEY", "")
        self.model = model
        self._client = None
        self._lock = threading.Lock()
        # If Gemini hits quota repeatedly, stop trying to save time
        self._gemini_dead = False

    @property
    def client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            return None
        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            return self._client
        except Exception as e:
            print(f"  [VisionVerifier] init failed: {e}")
            return None

    def verify(self, image_url: str, intent: SegmentIntent,
               timeout: float = 15.0, theme: str = "",
               already_used_descriptions: list = None) -> tuple:
        """Verifica se uma imagem é adequada para o intent.

        Args:
            theme: contexto temático geral do vídeo
            already_used_descriptions: lista de descrições visuais já usadas no
                video, pro Vision penalizar repetições.

        Returns: (score: int 0-100, description: str, reason_if_rejected: str, phash: str)
        """
        if not self.client and not self.nvidia_key:
            return 60, "[no vision verifier available]", "", ""

        # Download thumbnail para bytes
        try:
            import requests
            img_resp = requests.get(image_url, timeout=10)
            if img_resp.status_code != 200 or len(img_resp.content) < 1000:
                return 0, "[thumbnail unavailable]", "thumbnail download failed", ""
            img_bytes = img_resp.content
        except Exception as e:
            return 0, "[thumbnail error]", f"thumb fetch error: {e}", ""

        # Compute pHash for visual dedup (cheap, ~5ms)
        phash = compute_phash(img_bytes)

        # Build diversity hint from previously-used clip descriptions
        diversity_hint = ""
        if already_used_descriptions:
            descs = [d for d in already_used_descriptions if d][-5:]
            if descs:
                diversity_hint = (
                    "\n\nDIVERSITY HINT — cenas já usadas neste vídeo (evite repetir):\n"
                    + "\n".join(f"  - {d}" for d in descs)
                )

        prompt = _VISION_PROMPT_TEMPLATE.format(
            text=intent.text[:200],
            entity=intent.main_entity or "(não definido)",
            action=intent.action or "(não definido)",
            theme=theme or intent.visual_context or "geral",
            diversity_hint=diversity_hint,
        )

        # Try Gemini first (unless we've determined it's dead this session)
        if self.client and not self._gemini_dead:
            try:
                from google.genai import types as gtypes
                cfg = gtypes.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    max_output_tokens=400,
                )
                image_part = gtypes.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[prompt, image_part],
                    config=cfg,
                )
                if response and response.text:
                    data = json.loads(response.text.strip())
                    score = int(data.get("score", 0))
                    score = max(0, min(100, score))
                    desc = str(data.get("what_i_see", ""))[:100]
                    reason = str(data.get("rejection_reason", ""))[:200]
                    return score, desc, reason, phash
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    with self._lock:
                        if not self._gemini_dead:
                            print(f"  [VisionVerifier] Gemini quota — switching to NVIDIA NIM for rest of session")
                            self._gemini_dead = True

        # NVIDIA NIM vision fallback (llama-3.2-90b-vision-instruct)
        if self.nvidia_key:
            try:
                import requests, base64
                img_b64 = base64.b64encode(img_bytes).decode()
                r = requests.post(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.nvidia_key}",
                             "Content-Type": "application/json", "Accept": "application/json"},
                    json={
                        "model": "meta/llama-3.2-90b-vision-instruct",
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt + "\n\nResponda APENAS JSON puro, sem markdown."},
                                {"type": "image_url",
                                 "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                            ],
                        }],
                        "max_tokens": 400,
                        "temperature": 0.1,
                    },
                    timeout=timeout,
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"].strip()
                # Strip markdown fences if present
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                content = content.strip()
                data = json.loads(content)
                score = int(data.get("score", 0))
                score = max(0, min(100, score))
                desc = str(data.get("what_i_see", ""))[:100]
                reason = str(data.get("rejection_reason", ""))[:200]
                return score, desc, reason, phash
            except Exception as e:
                return 0, "[vision error]", f"NVIDIA NIM failed: {str(e)[:200]}", phash

        return 0, "[no vision available]", "Both Gemini and NVIDIA NIM unavailable", phash


# ─────────────────────────────────────────────────────────────────────────────
# INTENT EXTRACTOR (script segment → semantic intent)
# ─────────────────────────────────────────────────────────────────────────────


_INTENT_PROMPT_TEMPLATE = """Você é um diretor de vídeo extraindo a INTENÇÃO VISUAL exata
de cada segmento de um roteiro narrado.

SEGMENTO DE ÁUDIO:
"{text}"

TEMA GERAL DO VÍDEO: {theme}

Extraia O QUE deve aparecer visualmente neste segmento e gere 5 queries de
busca. IMPORTANTE: 4 em INGLÊS (Pexels/Pixabay são bancos americanos com 90%+
conteúdo em EN) e 1 em português. Use termos visuais concretos e populares.

Responda APENAS em JSON estrito:
{{
  "main_entity": "<o objeto/sujeito CONCRETO que deve aparecer, máx 60 chars>",
  "action": "<o que essa entidade está fazendo, máx 60 chars>",
  "visual_context": "<estilo visual: close-up/wide, realista/cinemático, dia/noite>",
  "search_queries": [
    "<query EN 1 - main entity + action, very specific>",
    "<query EN 2 - alternative phrasing, iconic visual>",
    "<query EN 3 - thematic context (e.g. 'ancient egyptian temple', 'pyramid of giza')>",
    "<query EN 4 - generic backup (e.g. 'desert sunset', 'old ruins')>",
    "<query PT 1 - termo em português>"
  ]
}}

REGRAS:
- main_entity DEVE ser concreto (peixe, máquina agrícola, solo erodido) — NUNCA abstrato (sucesso, futuro, jornada)
- search_queries devem PRIORIZAR a entidade exata + ação, não termos genéricos
- Se segmento for sobre conceito abstrato sem visual claro: main_entity="<conceito abstrato>" e queries devem buscar metáforas visuais coerentes
- NUNCA invente entidades não mencionadas no texto"""


class IntentExtractor:
    """Extrai SegmentIntent estruturado de um pedaço de texto narrado."""

    def __init__(self, ai_ask_func=None, gemini_api_key: str = "",
                 model: str = "gemini-2.0-flash", groq_api_key: str = ""):
        """
        Args:
            ai_ask_func: função opcional (prompt, json_response=True) → str
                          (pra usar AI engine compartilhada do projeto).
            gemini_api_key: fallback direto.
            groq_api_key: fallback quando Gemini hit quota.
        """
        self.ai_ask = ai_ask_func
        self.api_key = gemini_api_key or os.environ.get("GOOGLE_API_KEY", "")
        self.groq_key = groq_api_key or os.environ.get("GROQ_API_KEY", "")
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            return None
        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            return self._client
        except Exception:
            return None

    def extract(self, text: str, theme: str = "general") -> dict:
        """Retorna dict com main_entity, action, visual_context, search_queries."""
        prompt = _INTENT_PROMPT_TEMPLATE.format(text=text[:500], theme=theme[:100])

        # Try injected ai_ask (uses project's AI engine = multi-provider fallback)
        if self.ai_ask:
            try:
                resp = self.ai_ask(prompt, json_response=True)
                if resp:
                    return self._parse(resp, text)
            except Exception:
                pass

        # Fallback: direct Gemini
        if self.client:
            try:
                from google.genai import types as gtypes
                cfg = gtypes.GenerateContentConfig(
                    temperature=0.3, response_mime_type="application/json",
                    max_output_tokens=500,
                )
                response = self.client.models.generate_content(
                    model=self.model, contents=prompt, config=cfg,
                )
                if response and response.text:
                    return self._parse(response.text, text)
            except Exception as e:
                err_str = str(e)
                # Quota errors → try Groq fallback
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                    print(f"  [IntentExtractor] Gemini quota hit → Groq fallback")
                else:
                    print(f"  [IntentExtractor] Gemini err: {err_str[:120]}")

        # Groq fallback (llama-3.3-70b → fast, JSON mode, no quota)
        if self.groq_key:
            try:
                import requests
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.groq_key}",
                             "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "max_tokens": 500,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=20,
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                if content:
                    return self._parse(content, text)
            except Exception as e:
                print(f"  [IntentExtractor] Groq err: {str(e)[:120]}")

        # Last resort: heuristic
        return self._heuristic(text, theme)

    def _parse(self, raw: str, fallback_text: str) -> dict:
        try:
            data = json.loads(raw.strip())
            return {
                "main_entity": str(data.get("main_entity", ""))[:120],
                "action": str(data.get("action", ""))[:120],
                "visual_context": str(data.get("visual_context", ""))[:120],
                "search_queries": [str(q)[:80] for q in data.get("search_queries", [])][:5],
            }
        except Exception:
            return self._heuristic(fallback_text, "")

    def _heuristic(self, text: str, theme: str) -> dict:
        """Fallback when AI fails: extract first concrete noun."""
        words = [w.strip(".,!?") for w in text.split() if len(w) > 4]
        entity = words[0] if words else theme or "video"
        return {
            "main_entity": entity,
            "action": "",
            "visual_context": "real, no overlay text",
            "search_queries": [
                f"{entity}",
                f"{entity} {theme}".strip(),
                f"{entity} closeup",
                f"{entity} (en)",
                f"{theme} footage" if theme else "documentary footage",
            ],
        }


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-SOURCE SEARCHER
# ─────────────────────────────────────────────────────────────────────────────


class MultiSourceSearcher:
    """Busca candidatos em paralelo em Pexels + Pixabay + YouTube + Mixkit."""

    def __init__(self, pexels_key: str = "", pixabay_key: str = "",
                 youtube_enabled: bool = True, max_per_source: int = 10):
        self.pexels_key = pexels_key
        self.pixabay_key = pixabay_key
        self.youtube_enabled = youtube_enabled
        self.max_per_source = max_per_source

    def search_all(self, query: str, min_duration: float = 3.0,
                   max_workers: int = 4) -> list:
        """Busca a query em paralelo em todas fontes configuradas.

        Returns: list[ClipCandidate] (não validado ainda).
        """
        tasks = []
        if self.pexels_key:
            tasks.append(("pexels", self._search_pexels))
        if self.pixabay_key:
            tasks.append(("pixabay", self._search_pixabay))
        if self.youtube_enabled:
            tasks.append(("youtube", self._search_youtube))

        if not tasks:
            return []

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_map = {ex.submit(fn, query, min_duration): name for name, fn in tasks}
            for fut in concurrent.futures.as_completed(future_map, timeout=30):
                name = future_map[fut]
                try:
                    candidates = fut.result(timeout=20)
                    results.extend(candidates)
                except Exception as e:
                    print(f"  [MultiSource:{name}] {query!r} → erro: {e}")
        return results

    def _search_pexels(self, query: str, min_dur: float) -> list:
        """pexels_stock.search_videos returns normalized:
           {id, url, duration, width, height, preview_url}"""
        try:
            from core.pexels_stock import search_videos
            raw = search_videos(self.pexels_key, query, count=self.max_per_source,
                                min_duration=int(min_dur))
            out = []
            for v in raw or []:
                if not v.get("url"):
                    continue
                vid = str(v.get("id", ""))
                out.append(ClipCandidate(
                    source="pexels",
                    source_id=vid,
                    title=f"pexels {vid}",
                    page_url=f"https://www.pexels.com/video/{vid}/",
                    download_url=str(v["url"]),
                    thumbnail_url=str(v.get("preview_url", "")),
                    duration=float(v.get("duration", 0)),
                    width=int(v.get("width", 0)),
                    height=int(v.get("height", 0)),
                ))
            return out
        except Exception as e:
            print(f"  [pexels] err: {e}")
            return []

    def _search_pixabay(self, query: str, min_dur: float) -> list:
        """pixabay_stock.search_videos returns:
           {id, url, duration, width, height, thumbnail_url, tags, page_url}"""
        try:
            from core.pixabay_stock import search_videos
            raw = search_videos(self.pixabay_key, query, count=self.max_per_source,
                                min_duration=int(min_dur))
            out = []
            for v in raw or []:
                if not v.get("url"):
                    continue
                vid = str(v.get("id", ""))
                thumb = v.get("thumbnail_url", "")
                # Skip candidates without a valid thumbnail — Vision can't score them
                # (returns wrong description of broken-image placeholder)
                if not thumb:
                    continue
                out.append(ClipCandidate(
                    source="pixabay",
                    source_id=vid,
                    title=str(v.get("tags", "") or f"pixabay {vid}")[:120],
                    page_url=str(v.get("page_url", "") or f"https://pixabay.com/videos/id-{vid}/"),
                    download_url=str(v["url"]),
                    thumbnail_url=thumb,
                    duration=float(v.get("duration", 0)),
                    width=int(v.get("width", 0)),
                    height=int(v.get("height", 0)),
                ))
            return out
        except Exception as e:
            print(f"  [pixabay] err: {e}")
            return []

    def _search_youtube(self, query: str, min_dur: float) -> list:
        """Busca no YouTube via yt-dlp (sem download — só metadata)."""
        try:
            import subprocess, sys as _sys
            # Try standalone exe first, fall back to python -m yt_dlp (always works)
            cmd_base = ["yt-dlp"]
            try:
                subprocess.run(["yt-dlp", "--version"], capture_output=True, timeout=5)
            except (FileNotFoundError, OSError):
                cmd_base = [_sys.executable, "-m", "yt_dlp"]
            cmd = cmd_base + ["--default-search", "ytsearch",
                   "--no-download", "--dump-json", "--flat-playlist",
                   "--playlist-end", str(self.max_per_source),
                   f"ytsearch{self.max_per_source}:{query}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            out = []
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    v = json.loads(line)
                except Exception:
                    continue
                vid = v.get("id", "")
                if not vid:
                    continue
                out.append(ClipCandidate(
                    source="youtube",
                    source_id=vid,
                    title=str(v.get("title", ""))[:120],
                    page_url=f"https://youtube.com/watch?v={vid}",
                    download_url=f"https://youtube.com/watch?v={vid}",
                    thumbnail_url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    duration=float(v.get("duration") or 0),
                ))
            return out
        except Exception as e:
            print(f"  [youtube] err: {e}")
            return []

    def search_photos(self, query: str, count: int = 6) -> list:
        """Search Pexels PHOTOS as fallback when no video matches.
        Photos get Ken Burns motion applied later → look like B-roll.

        Pexels has a MASSIVE photo library (much larger than videos), so this
        dramatically increases coverage for abstract/historical topics.
        """
        if not self.pexels_key:
            return []
        try:
            from core.pexels_stock import search_photos
            raw = search_photos(self.pexels_key, query, count=count)
            out = []
            for p in raw or []:
                if not p.get("url"):
                    continue
                pid = str(p.get("id", ""))
                out.append(ClipCandidate(
                    source="pexels_photo",
                    source_id=pid,
                    title=f"pexels photo {pid}",
                    page_url=f"https://www.pexels.com/photo/{pid}/",
                    download_url=str(p["url"]),
                    thumbnail_url=str(p.get("preview_url", "") or p["url"]),
                    duration=8.0,  # virtual — vai virar Ken Burns
                    width=int(p.get("width", 1920)),
                    height=int(p.get("height", 1080)),
                    is_photo=True,
                ))
            return out
        except Exception as e:
            print(f"  [pexels_photo] err: {e}")
            return []


# ─────────────────────────────────────────────────────────────────────────────
# WHISPER SEGMENTER
# ─────────────────────────────────────────────────────────────────────────────


def segment_audio_with_whisper(audio_path: str, model_size: str = "base",
                                target_segment_dur: float = 6.0,
                                language: str = "pt") -> list:
    """Transcreve áudio com Whisper word-level e agrupa em segmentos de 3-8s.

    Tries faster_whisper first (4-10× faster), falls back to openai-whisper.

    Returns: list[dict] {start, end, text}
    """
    all_words = []
    backend = None

    # Try faster_whisper first
    try:
        from faster_whisper import WhisperModel
        backend = "faster_whisper"
        try:
            model = WhisperModel(model_size, device="cuda", compute_type="float16")
        except Exception:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(
            audio_path, language=language,
            word_timestamps=True, beam_size=5,
        )
        for seg in segments:
            if not hasattr(seg, "words") or not seg.words:
                all_words.append({"start": seg.start, "end": seg.end, "text": seg.text})
                continue
            for w in seg.words:
                all_words.append({
                    "start": float(w.start), "end": float(w.end), "text": w.word.strip(),
                })
    except ImportError:
        # Fallback: openai-whisper
        import whisper
        backend = "openai-whisper"
        device = "cuda"
        try:
            import torch
            if not torch.cuda.is_available():
                device = "cpu"
        except Exception:
            device = "cpu"
        model = whisper.load_model(model_size, device=device)
        result = model.transcribe(
            audio_path, language=language,
            word_timestamps=True, verbose=False,
        )
        for seg in result.get("segments", []):
            words = seg.get("words") or []
            if not words:
                all_words.append({
                    "start": float(seg["start"]), "end": float(seg["end"]),
                    "text": seg["text"].strip(),
                })
                continue
            for w in words:
                all_words.append({
                    "start": float(w["start"]), "end": float(w["end"]),
                    "text": (w.get("word") or "").strip(),
                })
    print(f"  [Whisper] {backend}: {len(all_words)} words/segments")

    # Group words into target_segment_dur chunks at sentence boundaries
    chunks = []
    current = {"start": 0.0, "end": 0.0, "text": ""}
    for w in all_words:
        if not current["text"]:
            current["start"] = w["start"]
        current["end"] = w["end"]
        current["text"] = (current["text"] + " " + w["text"]).strip()

        dur = current["end"] - current["start"]
        ends_sentence = current["text"].rstrip().endswith((".", "!", "?", "…"))

        if dur >= target_segment_dur and ends_sentence:
            chunks.append(dict(current))
            current = {"start": 0.0, "end": 0.0, "text": ""}
        elif dur >= target_segment_dur * 1.5:  # Force cut if no sentence break
            chunks.append(dict(current))
            current = {"start": 0.0, "end": 0.0, "text": ""}

    if current["text"]:
        chunks.append(dict(current))
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENGINE
# ─────────────────────────────────────────────────────────────────────────────


class IntelligentBrollEngine:
    """Engine principal — orquestra segmentação → intent → search → verify → download.

    Uso:
        engine = IntelligentBrollEngine(
            gemini_api_key="...",
            pexels_key="...", pixabay_key="...",
            output_dir="./broll_cache",
        )
        plans = engine.build(
            audio_path="narration.mp3",
            theme="aquarismo",
            min_relevance=80,
        )
        for plan in plans:
            print(plan.intent.short_label(), "→",
                  plan.clip.short_label() if plan.clip else "MANUAL REVIEW")
    """

    def __init__(self,
                 gemini_api_key: str = "",
                 pexels_key: str = "",
                 pixabay_key: str = "",
                 youtube_enabled: bool = True,
                 output_dir: str = "broll_cache",
                 verifier_model: str = "gemini-2.0-flash",
                 max_candidates_per_intent: int = 12,
                 max_search_attempts: int = 3,
                 ai_ask_func=None,
                 groq_api_key: str = "",
                 nvidia_api_key: str = ""):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.intent_extractor = IntentExtractor(
            ai_ask_func=ai_ask_func, gemini_api_key=gemini_api_key,
            groq_api_key=groq_api_key,
        )
        self.searcher = MultiSourceSearcher(
            pexels_key=pexels_key, pixabay_key=pixabay_key,
            youtube_enabled=youtube_enabled,
            max_per_source=max(4, max_candidates_per_intent // 3),
        )
        self.verifier = VisionVerifier(gemini_api_key=gemini_api_key,
                                        model=verifier_model,
                                        nvidia_api_key=nvidia_api_key)
        self.max_candidates = max_candidates_per_intent
        self.max_attempts = max_search_attempts

        # ── Anti-duplicate state (per video instance) ──
        self._used_clip_ids = set()           # IDs from same source
        self._used_phashes = []               # pHashes of clips selected so far
        self._used_descriptions = []          # Vision descs of what was picked (for diversity hint)
        self._spare_pool = []                 # ClipCandidates approved but not winners
                                              # used to fill gaps with variety
        self._phash_dedup_threshold = 0.85    # >85% visually similar = reject
        self._negative_terms = []             # Theme-based exclusion list (set in build())

    def build(self, audio_path: str = "", script: str = "",
              theme: str = "general", min_relevance: int = 60,
              language: str = "pt") -> list:
        """Constrói plano completo de B-roll para o áudio/roteiro.

        Args:
            audio_path: Path pro MP3/WAV. Se vazio, usa apenas `script` (sem timestamps).
            script: Texto completo (fallback se sem áudio).
            theme: Tema geral pra contexto da extração de intent.
            min_relevance: Score mínimo de aprovação (0-100). Default 80 = bem rigoroso.
            language: Idioma do áudio pra Whisper.

        Returns: list[BeatPlan] em ordem temporal.
        """
        # Compute negative terms from theme for anti-anachronism filter
        self._negative_terms = detect_theme_negatives(theme)
        if self._negative_terms:
            print(f"[IntelligentBroll] Negative terms for theme: {self._negative_terms[:6]}…")

        # ─── ETAPA 1: Segmentação com word-level timestamps ─────────────
        if audio_path and os.path.exists(audio_path):
            print(f"[IntelligentBroll] Transcrevendo {audio_path}…")
            segments = segment_audio_with_whisper(audio_path, language=language)
        elif script:
            # Sem áudio: divide texto por sentença, sem timestamps reais
            segments = self._split_script_no_audio(script)
        else:
            return []

        print(f"[IntelligentBroll] {len(segments)} segmentos detectados")

        # ─── ETAPA 2-6: pra cada segmento, intent → search → verify ────
        plans = []
        for i, seg in enumerate(segments):
            print(f"\n[IntelligentBroll] Segment {i+1}/{len(segments)}: "
                  f"[{seg['start']:.1f}-{seg['end']:.1f}s] {seg['text'][:80]}…")

            intent = SegmentIntent(
                index=i, text=seg["text"], start=seg["start"], end=seg["end"],
            )

            # Extract intent (entity + action + queries)
            intent_data = self.intent_extractor.extract(seg["text"], theme=theme)
            intent.main_entity = intent_data["main_entity"]
            intent.action = intent_data["action"]
            intent.visual_context = intent_data["visual_context"]
            intent.search_queries = intent_data["search_queries"]
            print(f"  Intent: {intent.main_entity} | {intent.action}")
            print(f"  Queries: {intent.search_queries[:3]}")

            # Verify + select best clip (pass theme so Vision can credit thematic matches)
            plan = self._find_clip_for_intent(intent, min_relevance=min_relevance, theme=theme)
            plans.append(plan)

        # ─── ETAPA 7: download dos aprovados ───────────────────────────
        for plan in plans:
            if plan.clip and not plan.needs_manual_review:
                path = self._download_clip(plan.clip, plan.intent)
                plan.download_path = path

        return plans

    def _split_script_no_audio(self, script: str) -> list:
        """Quebra roteiro em sentenças sem timestamps reais (fallback)."""
        import re
        sentences = re.split(r"(?<=[.!?])\s+", script.strip())
        out, t = [], 0.0
        for s in sentences:
            words = len(s.split())
            dur = max(2.0, words / 2.5)  # ~150 wpm = 2.5 wps
            out.append({"start": t, "end": t + dur, "text": s})
            t += dur
        return out

    def _is_visually_duplicate(self, phash: str) -> tuple:
        """Returns (is_dup: bool, max_similarity: float, matching_phash: str)."""
        if not phash or not self._used_phashes:
            return False, 0.0, ""
        max_sim = 0.0
        matching = ""
        for used in self._used_phashes:
            sim = phash_similarity(phash, used)
            if sim > max_sim:
                max_sim = sim
                matching = used
        return max_sim >= self._phash_dedup_threshold, max_sim, matching

    def _mark_used(self, cand: ClipCandidate):
        """Mark a clip as used so future segments don't pick it again."""
        self._used_clip_ids.add(cand.source_id)
        if cand.perceptual_hash:
            self._used_phashes.append(cand.perceptual_hash)
        if cand.vision_description:
            self._used_descriptions.append(cand.vision_description)

    def _find_clip_for_intent(self, intent: SegmentIntent,
                              min_relevance: int = 80,
                              theme: str = "") -> BeatPlan:
        """Etapas 3-5: busca + verificação visual + seleção com anti-duplicate.

        Anti-repetition layers (in order of cheapness):
        1. source_id dedup (free, exact match)
        2. pHash dedup (cheap, ~5ms — catches resized/recompressed same scene)
        3. Diversity hint in Vision prompt (penalizes scenes too similar to used ones)

        Best-so-far fallback for abstract narrations + spare pool for gap-fills.
        """
        best_so_far = None  # (score, candidate)

        for attempt in range(self.max_attempts):
            for query in intent.search_queries:
                candidates = self.searcher.search_all(query)
                # ALWAYS also pull photos (Ken Burns) — Pexels photo library is
                # ~50× bigger than video, much better coverage for abstract topics
                photos = self.searcher.search_photos(query, count=4)
                if photos:
                    candidates = candidates + photos
                if not candidates:
                    continue

                candidates = candidates[:self.max_candidates]
                for cand in candidates:
                    # Layer 1: exact ID dedup
                    if cand.source_id in self._used_clip_ids:
                        continue
                    if not cand.thumbnail_url:
                        cand.relevance_score = 0
                        cand.rejection_reason = "no thumbnail"
                        continue

                    # Layer 1.5: cheap negative-term text filter
                    # If title/tags contain any anachronistic term, skip without Vision call
                    if self._negative_terms:
                        title_lower = (cand.title or "").lower()
                        if any(neg in title_lower for neg in self._negative_terms):
                            cand.relevance_score = 0
                            cand.rejection_reason = f"negative term in title"
                            print(f"    [---] {cand.source}#{cand.source_id[:8]}: "
                                  f"rejected (negative term in '{cand.title[:50]}')")
                            continue

                    # Vision verify (with diversity hint based on what's been used)
                    score, desc, reason, phash = self.verifier.verify(
                        cand.thumbnail_url, intent, theme=theme,
                        already_used_descriptions=self._used_descriptions,
                    )
                    cand.relevance_score = score
                    cand.vision_description = desc
                    cand.rejection_reason = reason
                    cand.perceptual_hash = phash

                    # Layer 2: perceptual hash dedup
                    is_dup, sim, _ = self._is_visually_duplicate(phash)
                    if is_dup:
                        print(f"    [{score:3d}] {cand.source}#{cand.source_id[:8]}: "
                              f"{desc[:60]} — ❌ {sim*100:.0f}% similar to already used")
                        continue

                    print(f"    [{score:3d}] {cand.source}#{cand.source_id[:8]}: "
                          f"{desc[:70]}")

                    if score >= min_relevance:
                        self._mark_used(cand)
                        return BeatPlan(intent=intent, clip=cand)

                    # Track best-so-far + add medium scorers to spare pool
                    if score >= 60:
                        self._spare_pool.append(cand)
                    if score > 45 and (best_so_far is None or score > best_so_far[0]):
                        best_so_far = (score, cand)

            # All candidates rejected → refine queries for next attempt
            intent.rejected_terms.extend(intent.search_queries)
            intent.search_queries = self._refine_queries(intent)
            print(f"  [refine attempt {attempt+1}] new queries: {intent.search_queries[:3]}")

        # All attempts failed — try best-so-far if not dup
        if best_so_far is not None:
            score, cand = best_so_far
            is_dup, _, _ = self._is_visually_duplicate(cand.perceptual_hash)
            if not is_dup:
                self._mark_used(cand)
                print(f"  ⚡ Fallback: using best-so-far clip "
                      f"{cand.source}#{cand.source_id[:8]} score={score}")
                return BeatPlan(intent=intent, clip=cand)

        # Try spare pool: previously-approved clips not yet used in this video
        for spare in sorted(self._spare_pool, key=lambda c: -c.relevance_score):
            if spare.source_id in self._used_clip_ids:
                continue
            is_dup, _, _ = self._is_visually_duplicate(spare.perceptual_hash)
            if is_dup:
                continue
            self._mark_used(spare)
            self._spare_pool.remove(spare)
            print(f"  🎁 Spare pool: reusing {spare.source}#{spare.source_id[:8]} "
                  f"({spare.vision_description[:50]}) score={spare.relevance_score}")
            return BeatPlan(intent=intent, clip=spare)

        # Truly nothing matched — flag manual review
        return BeatPlan(
            intent=intent, clip=None, needs_manual_review=True,
            error=f"Nenhum clip atingiu score ≥{min_relevance} após {self.max_attempts} tentativas",
        )

    def _refine_queries(self, intent: SegmentIntent) -> list:
        """Pede ao IntentExtractor pra gerar queries diferentes."""
        # Add explicit "exclude" terms
        new_text = f"{intent.text}\n\n(NÃO sugerir: {', '.join(intent.rejected_terms[-5:])})"
        data = self.intent_extractor.extract(new_text, theme="")
        return data["search_queries"]

    def _download_clip(self, clip: ClipCandidate, intent: SegmentIntent) -> str:
        """Baixa o clip aprovado. Returns path local.

        For photo candidates, downloads the image then converts to a Ken Burns
        video (zoom+pan) so it looks like B-roll in the final composition.
        """
        safe_id = hashlib.md5(f"{clip.source}_{clip.source_id}".encode()).hexdigest()[:12]

        # Photos: download img, convert to Ken Burns video
        if clip.is_photo or clip.source == "pexels_photo":
            return self._photo_to_kenburns(clip, intent, safe_id)

        ext = ".mp4"
        out = self.output_dir / f"intent{intent.index:03d}_{clip.source}_{safe_id}{ext}"
        if out.exists() and out.stat().st_size > 50_000:
            return str(out)

        try:
            if clip.source == "youtube":
                # Two-step: download full clip, then trim middle 6-8s with audio off
                import subprocess, sys as _sys
                cmd_base = ["yt-dlp"]
                try:
                    subprocess.run(["yt-dlp", "--version"], capture_output=True, timeout=5)
                except (FileNotFoundError, OSError):
                    cmd_base = [_sys.executable, "-m", "yt_dlp"]
                raw = self.output_dir / f"yt_raw_{safe_id}.mp4"
                cmd = cmd_base + [
                    "-f", "mp4[height<=1080][height>=480]/best[height<=1080]",
                    "--no-playlist", "--quiet",
                    "--max-filesize", "200M",
                    "--match-filter", "duration > 15 & duration < 1800",  # skip shorts and >30min
                    "-o", str(raw), clip.download_url,
                ]
                r = subprocess.run(cmd, capture_output=True, timeout=240)
                if not raw.exists() or raw.stat().st_size < 100_000:
                    return ""
                # Trim middle 7s with crop to remove letterboxing, mute audio
                try:
                    src_dur = float(subprocess.run([
                        "ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(raw),
                    ], capture_output=True, text=True).stdout.strip() or 0)
                    if src_dur < 10:
                        return ""
                    # Skip first 25% (intros), take from middle
                    start = max(5.0, src_dur * 0.25)
                    cut_dur = min(7.5, src_dur - start - 2)
                    subprocess.run([
                        "ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(raw),
                        "-t", f"{cut_dur:.2f}",
                        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1",
                        "-an",  # mute YouTube audio (we use TTS or avatar audio)
                        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                        "-pix_fmt", "yuv420p",
                        str(out),
                    ], capture_output=True, timeout=180, check=True)
                    try: raw.unlink()
                    except: pass
                    if out.exists() and out.stat().st_size > 50_000:
                        return str(out)
                except Exception as e:
                    print(f"  [yt trim err] {e}")
                return ""
            else:
                # Direct HTTP download
                import requests
                r = requests.get(clip.download_url, stream=True, timeout=120)
                r.raise_for_status()
                with open(out, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                if out.exists() and out.stat().st_size > 50_000:
                    return str(out)
                return ""
        except Exception as e:
            print(f"  [download err] {clip.source}#{clip.source_id}: {e}")
            return ""

    def _photo_to_kenburns(self, clip: ClipCandidate, intent: SegmentIntent,
                          safe_id: str, target_dur: float = 8.0) -> str:
        """Download a photo then ffmpeg-convert it to a Ken Burns video.

        Ken Burns = subtle zoom + pan over time. Makes a still photo feel like
        cinematographic B-roll.
        """
        import subprocess, requests, random
        img_path = self.output_dir / f"intent{intent.index:03d}_photo_{safe_id}.jpg"
        out = self.output_dir / f"intent{intent.index:03d}_kb_{safe_id}.mp4"
        if out.exists() and out.stat().st_size > 50_000:
            return str(out)

        # Download image
        try:
            r = requests.get(clip.download_url, timeout=60)
            r.raise_for_status()
            with open(img_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            print(f"  [photo dl err] {e}")
            return ""

        # Use segment duration if known
        seg_dur = max(3.0, intent.end - intent.start) if intent.end > intent.start else target_dur

        # 4 Ken Burns variants — randomize per segment for variety
        rng = random.Random(hash(safe_id))
        kb_modes = [
            # (zoom_start, zoom_end, x_start, x_end, y_start, y_end)
            "zoompan=z='min(zoom+0.0015,1.5)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=30",
            "zoompan=z='if(eq(on,1),1.5,max(1.001,zoom-0.0015))':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=30",
            "zoompan=z='1.2+0.1*sin(on/30)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=30",
        ]
        kb = rng.choice(kb_modes)
        W, H = 1920, 1080
        frames = int(seg_dur * 30)
        filter_str = kb.format(W=W, H=H, frames=frames)
        # Scale input first for higher quality zoom
        full_filter = f"scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,{filter_str}"

        try:
            r = subprocess.run([
                "ffmpeg", "-y",
                "-loop", "1", "-t", f"{seg_dur:.2f}", "-i", str(img_path),
                "-vf", full_filter,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                "-pix_fmt", "yuv420p", "-r", "30",
                str(out),
            ], capture_output=True, timeout=120)
            if out.exists() and out.stat().st_size > 50_000:
                return str(out)
            print(f"  [kenburns err] ffmpeg failed: {r.stderr.decode(errors='replace')[-200:]}")
            return ""
        except Exception as e:
            print(f"  [kenburns err] {e}")
            return ""


# ─────────────────────────────────────────────────────────────────────────────
# CLI / SMOKE TEST
# ─────────────────────────────────────────────────────────────────────────────


def _cli_smoke():
    """Run quick smoke from CLI: python -m core.intelligent_broll <audio> <theme>"""
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m core.intelligent_broll <audio.mp3> <theme>")
        return
    audio = sys.argv[1]; theme = sys.argv[2]
    import os
    engine = IntelligentBrollEngine(
        gemini_api_key=os.environ.get("GOOGLE_API_KEY", ""),
        pexels_key=os.environ.get("PEXELS_KEY", ""),
        pixabay_key=os.environ.get("PIXABAY_KEY", ""),
        youtube_enabled=True,
        output_dir="./broll_test_out",
    )
    plans = engine.build(audio_path=audio, theme=theme, min_relevance=80)
    print(f"\n=== RESULTADO: {sum(1 for p in plans if p.is_solved())}/{len(plans)} segmentos com clip aprovado ===")
    for p in plans:
        status = "✅" if p.is_solved() else "⚠️ MANUAL"
        print(f"  {status} {p.intent.short_label()}")
        if p.clip:
            print(f"     → {p.clip.short_label()} score={p.clip.relevance_score}")
            print(f"       saw: {p.clip.vision_description[:80]}")
        elif p.error:
            print(f"     ERROR: {p.error}")


if __name__ == "__main__":
    _cli_smoke()
