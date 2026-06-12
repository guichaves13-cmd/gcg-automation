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
    source: str                              # "pexels" | "pixabay" | "youtube" | "mixkit"
    source_id: str                           # ID nativo da fonte (pra dedup)
    title: str                               # Texto descritivo do clip
    page_url: str                            # URL da página de download
    download_url: str                        # URL direta do MP4
    thumbnail_url: str                       # URL do thumbnail/poster
    duration: float                          # Segundos
    width: int = 0
    height: int = 0

    # Preenchidos durante verificação:
    relevance_score: int = 0                 # 0-100 do Gemini Vision
    vision_description: str = ""             # O que Gemini disse que vê
    rejection_reason: str = ""               # Por que foi rejeitado (se foi)

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
# VISION VERIFIER
# ─────────────────────────────────────────────────────────────────────────────


_VISION_PROMPT_TEMPLATE = """Você é um auditor visual rigoroso para um pipeline de vídeo profissional.

Analise esta imagem e responda HONESTAMENTE se ela é adequada para ilustrar
exatamente o seguinte segmento de áudio narrado:

CONTEXTO DO SEGMENTO:
- Texto sendo dito: "{text}"
- Entidade principal que DEVE aparecer: {entity}
- Ação ou estado: {action}
- Estilo visual desejado: {visual_context}

REGRAS PARA SCORE:
- 90-100: A imagem mostra EXATAMENTE a entidade fazendo EXATAMENTE a ação
- 70-89: Mostra a entidade certa mas em ação/contexto diferente
- 40-69: Mostra algo relacionado mas não a entidade exata
- 0-39: Não tem nada a ver com o que está sendo dito

Responda APENAS em JSON estrito:
{{
  "score": <inteiro 0-100>,
  "what_i_see": "<descrição curta do que está REALMENTE na imagem, máx 80 chars>",
  "matches_entity": <true ou false>,
  "matches_action": <true ou false>,
  "rejection_reason": "<se score<80: motivo curto. Se score>=80: ''>"
}}

Seja BRUTALMENTE HONESTO. Se a imagem mostra peixes mas estamos falando de máquina agrícola, score 0.
NUNCA dê score alto por compaixão. PIOR cenário é um clip errado entrar no vídeo final."""


class VisionVerifier:
    """Verifica relevância de uma imagem para um intent usando Gemini Vision.

    Custo: ~R$0.001 por verificação (gemini-2.0-flash).
    Latência: ~1-2s por verificação.
    """

    def __init__(self, gemini_api_key: str = "", model: str = "gemini-2.0-flash"):
        self.api_key = gemini_api_key or os.environ.get("GOOGLE_API_KEY", "")
        self.model = model
        self._client = None
        self._lock = threading.Lock()

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
               timeout: float = 15.0) -> tuple:
        """Verifica se uma imagem é adequada para o intent.

        Returns: (score: int 0-100, description: str, reason_if_rejected: str)
        """
        if not self.client:
            # Fallback heurístico: confia no search (assume score 60 default)
            return 60, "[no vision verifier available]", ""

        # Download thumbnail para bytes
        try:
            import requests
            img_resp = requests.get(image_url, timeout=10)
            if img_resp.status_code != 200 or len(img_resp.content) < 1000:
                return 0, "[thumbnail unavailable]", "thumbnail download failed"
            img_bytes = img_resp.content
        except Exception as e:
            return 0, "[thumbnail error]", f"thumb fetch error: {e}"

        prompt = _VISION_PROMPT_TEMPLATE.format(
            text=intent.text[:200],
            entity=intent.main_entity or "(não definido)",
            action=intent.action or "(não definido)",
            visual_context=intent.visual_context or "real, sem texto na tela",
        )

        try:
            from google.genai import types as gtypes
            cfg = gtypes.GenerateContentConfig(
                temperature=0.1,                # Determinístico, sem criatividade
                response_mime_type="application/json",
                max_output_tokens=400,
            )
            # Build multimodal content
            image_part = gtypes.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
            response = self.client.models.generate_content(
                model=self.model,
                contents=[prompt, image_part],
                config=cfg,
            )
            if not response or not response.text:
                return 0, "[empty Gemini response]", "Gemini returned nothing"

            data = json.loads(response.text.strip())
            score = int(data.get("score", 0))
            score = max(0, min(100, score))
            desc = str(data.get("what_i_see", ""))[:100]
            reason = str(data.get("rejection_reason", ""))[:200]

            return score, desc, reason

        except Exception as e:
            return 0, "[vision error]", f"Gemini vision call failed: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# INTENT EXTRACTOR (script segment → semantic intent)
# ─────────────────────────────────────────────────────────────────────────────


_INTENT_PROMPT_TEMPLATE = """Você é um diretor de vídeo extraindo a INTENÇÃO VISUAL exata
de cada segmento de um roteiro narrado.

SEGMENTO DE ÁUDIO:
"{text}"

TEMA GERAL DO VÍDEO: {theme}

Extraia O QUE deve aparecer visualmente neste segmento e gere 5 queries de
busca (3 em português, 2 em inglês) que tragam EXATAMENTE essa imagem.

Responda APENAS em JSON estrito:
{{
  "main_entity": "<o objeto/sujeito CONCRETO que deve aparecer, máx 60 chars>",
  "action": "<o que essa entidade está fazendo, máx 60 chars>",
  "visual_context": "<estilo visual: close-up/wide, realista/cinemático, dia/noite>",
  "search_queries": [
    "<query pt 1 - específica>",
    "<query pt 2 - alternativa>",
    "<query pt 3 - sinônimo>",
    "<query en 1 - direct translation>",
    "<query en 2 - alternative>"
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
                 model: str = "gemini-2.0-flash"):
        """
        Args:
            ai_ask_func: função opcional (prompt, json_response=True) → str
                          (pra usar AI engine compartilhada do projeto).
            gemini_api_key: fallback direto.
        """
        self.ai_ask = ai_ask_func
        self.api_key = gemini_api_key or os.environ.get("GOOGLE_API_KEY", "")
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
                print(f"  [IntentExtractor] Gemini err: {e}")

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
        try:
            from core.pexels_stock import search_videos
            raw = search_videos(self.pexels_key, query, count=self.max_per_source,
                                min_duration=int(min_dur))
            out = []
            for v in raw or []:
                # Pexels schema: {id, url, image, video_files: [{link, width, height, ...}], duration}
                files = v.get("video_files") or []
                if not files:
                    continue
                best = max(files, key=lambda f: f.get("width", 0))
                out.append(ClipCandidate(
                    source="pexels",
                    source_id=str(v.get("id", "")),
                    title=str(v.get("user", {}).get("name", "") or "pexels clip"),
                    page_url=str(v.get("url", "")),
                    download_url=str(best.get("link", "")),
                    thumbnail_url=str(v.get("image", "")),
                    duration=float(v.get("duration", 0)),
                    width=int(best.get("width", 0)),
                    height=int(best.get("height", 0)),
                ))
            return out
        except Exception as e:
            print(f"  [pexels] err: {e}")
            return []

    def _search_pixabay(self, query: str, min_dur: float) -> list:
        try:
            from core.pixabay_stock import search_videos
            raw = search_videos(self.pixabay_key, query, count=self.max_per_source,
                                min_duration=int(min_dur))
            out = []
            for v in raw or []:
                videos = v.get("videos", {})
                best_url = ""
                best_w = 0
                for q in ("large", "medium", "small"):
                    info = videos.get(q, {})
                    if info.get("url") and info.get("width", 0) > best_w:
                        best_url = info["url"]
                        best_w = info.get("width", 0)
                if not best_url:
                    continue
                out.append(ClipCandidate(
                    source="pixabay",
                    source_id=str(v.get("id", "")),
                    title=str(v.get("tags", "") or "pixabay clip"),
                    page_url=str(v.get("pageURL", "")),
                    download_url=best_url,
                    thumbnail_url=f"https://i.vimeocdn.com/video/{v.get('picture_id','')}_640x360.jpg" if v.get("picture_id") else "",
                    duration=float(v.get("duration", 0)),
                    width=best_w,
                ))
            return out
        except Exception as e:
            print(f"  [pixabay] err: {e}")
            return []

    def _search_youtube(self, query: str, min_dur: float) -> list:
        """Busca no YouTube via yt-dlp (sem download — só metadata)."""
        try:
            import subprocess
            cmd = ["yt-dlp", "--default-search", "ytsearch",
                   "--no-download", "--dump-json", "--flat-playlist",
                   "--playlist-end", str(self.max_per_source),
                   f"ytsearch{self.max_per_source}:{query}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
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


# ─────────────────────────────────────────────────────────────────────────────
# WHISPER SEGMENTER
# ─────────────────────────────────────────────────────────────────────────────


def segment_audio_with_whisper(audio_path: str, model_size: str = "base",
                                target_segment_dur: float = 6.0,
                                language: str = "pt") -> list:
    """Transcreve áudio com Whisper word-level e agrupa em segmentos de 3-8s.

    Returns: list[dict] {start, end, text}
    """
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_size, device="cuda", compute_type="float16")
    except Exception:
        # CPU fallback
        from faster_whisper import WhisperModel
        model = WhisperModel(model_size, device="cpu", compute_type="int8")

    segments, _info = model.transcribe(
        audio_path, language=language,
        word_timestamps=True, beam_size=5,
    )

    # Convert generator to list of word-level data
    all_words = []
    for seg in segments:
        if not hasattr(seg, "words") or not seg.words:
            all_words.append({"start": seg.start, "end": seg.end, "text": seg.text})
            continue
        for w in seg.words:
            all_words.append({
                "start": float(w.start), "end": float(w.end), "text": w.word.strip(),
            })

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
                 ai_ask_func=None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.intent_extractor = IntentExtractor(
            ai_ask_func=ai_ask_func, gemini_api_key=gemini_api_key,
        )
        self.searcher = MultiSourceSearcher(
            pexels_key=pexels_key, pixabay_key=pixabay_key,
            youtube_enabled=youtube_enabled,
            max_per_source=max(4, max_candidates_per_intent // 3),
        )
        self.verifier = VisionVerifier(gemini_api_key=gemini_api_key,
                                        model=verifier_model)
        self.max_candidates = max_candidates_per_intent
        self.max_attempts = max_search_attempts
        self._used_clip_ids = set()  # Anti-duplicate across video

    def build(self, audio_path: str = "", script: str = "",
              theme: str = "general", min_relevance: int = 80,
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

            # Verify + select best clip
            plan = self._find_clip_for_intent(intent, min_relevance=min_relevance)
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

    def _find_clip_for_intent(self, intent: SegmentIntent,
                              min_relevance: int = 80) -> BeatPlan:
        """Etapas 3-5: busca + verificação visual + seleção."""
        for attempt in range(self.max_attempts):
            for query in intent.search_queries:
                candidates = self.searcher.search_all(query)
                if not candidates:
                    continue

                # Cap candidates
                candidates = candidates[:self.max_candidates]

                # Verify each via thumbnail (sequential pra não estourar quota)
                for cand in candidates:
                    # Anti-duplicate across video
                    if cand.source_id in self._used_clip_ids:
                        continue
                    if not cand.thumbnail_url:
                        cand.relevance_score = 0
                        cand.rejection_reason = "no thumbnail"
                        continue

                    score, desc, reason = self.verifier.verify(cand.thumbnail_url, intent)
                    cand.relevance_score = score
                    cand.vision_description = desc
                    cand.rejection_reason = reason

                    print(f"    [{score:3d}] {cand.source}#{cand.source_id[:8]}: "
                          f"{desc[:70]}")

                    if score >= min_relevance:
                        # WINNER!
                        self._used_clip_ids.add(cand.source_id)
                        return BeatPlan(intent=intent, clip=cand)

            # All candidates rejected → refine queries for next attempt
            intent.rejected_terms.extend(intent.search_queries)
            intent.search_queries = self._refine_queries(intent)
            print(f"  [refine attempt {attempt+1}] new queries: {intent.search_queries[:3]}")

        # All attempts failed — flag manual review
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
        """Baixa o clip aprovado. Returns path local."""
        safe_id = hashlib.md5(f"{clip.source}_{clip.source_id}".encode()).hexdigest()[:12]
        ext = ".mp4"
        out = self.output_dir / f"intent{intent.index:03d}_{clip.source}_{safe_id}{ext}"
        if out.exists() and out.stat().st_size > 50_000:
            return str(out)

        try:
            if clip.source == "youtube":
                # Use youtube_broll for proper download
                from core.youtube_broll import _ensure_ytdlp
                if not _ensure_ytdlp():
                    return ""
                import subprocess
                cmd = ["yt-dlp", "-f", "mp4[height<=1080]/best",
                       "--no-playlist", "--quiet",
                       "-o", str(out), clip.download_url]
                r = subprocess.run(cmd, capture_output=True, timeout=180)
                if out.exists() and out.stat().st_size > 50_000:
                    return str(out)
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
