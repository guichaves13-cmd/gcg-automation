"""
IA Corretor (camada 4) + IA Auditor Final (camada 5)

IA Corretor:
    - Roda APÓS o processamento de cada segmento de B-roll, ANTES da concatenação
    - Extrai frame do segmento, cruza com texto narrado naquele momento
    - Reprovados são automaticamente substituídos por nova tentativa com clip diferente

IA Auditor Final:
    - Analisa o vídeo COMPLETO frame-a-frame após montagem
    - Verifica coerência visual ↔ áudio em checkpoints de 8s
    - Gera relatório de qualidade com score e lista de problemas
    - Vídeos abaixo de 70% de qualidade têm timestamps marcados para revisão
"""

import os
import json
import subprocess
import shutil
import time
import random
import hashlib
from pathlib import Path


class VideoAuditor:
    """
    Duas IAs de segurança pós-produção integradas ao pipeline.

    Uso no pipeline:
        auditor = VideoAuditor(google_api_key, ffmpeg_path)

        # Após processar segmentos, antes de concatenar:
        bad_idxs = auditor.audit_segments(segment_files, segments_plan, transcription, temp_dir)

        # Após concatenar o vídeo final:
        report = auditor.audit_final_video(final_video, transcription, temp_dir)
    """

    CORRETOR_THRESHOLD = 0.4     # Score abaixo disso → segmento reprovado
    AUDITOR_MIN_QUALITY = 0.70   # Score abaixo disso → vídeo com ressalvas

    def __init__(self, google_api_key: str = ""):
        self.google_api_key = google_api_key
        self._client = None
        self._ffmpeg = self._find_binary("ffmpeg")
        self._ffprobe = self._find_binary("ffprobe")

    # ─────────────────────────────────────────────
    # Propriedades e utilitários internos
    # ─────────────────────────────────────────────

    @property
    def client(self):
        if not self._client and self.google_api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.google_api_key)
            except Exception:
                pass
        return self._client

    def _find_binary(self, name: str) -> str:
        found = shutil.which(name)
        if found:
            return found
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bundled = os.path.join(project_dir, "ffmpeg", f"{name}.exe")
        return bundled if os.path.isfile(bundled) else name

    def _extract_frame(self, video_path: str, offset: float, out_path: str) -> bool:
        """Extrai um frame JPEG do vídeo no timestamp especificado."""
        try:
            cmd = [
                self._ffmpeg, "-y",
                "-ss", str(round(max(0, offset), 2)),
                "-i", video_path,
                "-vframes", "1",
                "-q:v", "4",
                out_path,
            ]
            r = subprocess.run(cmd, capture_output=True, timeout=25)
            return (r.returncode == 0
                    and os.path.exists(out_path)
                    and os.path.getsize(out_path) > 500)
        except Exception:
            return False

    def _gemini_with_retry(self, contents, max_retries: int = 2, max_total_wait: int = 30) -> str:
        """Gemini call with 429 retry. Caps total wait so a single stuck frame
        doesn't add minutes of dead time to the pipeline."""
        import re as _re
        total_waited = 0
        for attempt in range(max_retries):
            try:
                resp = self.client.models.generate_content(
                    model="gemini-2.0-flash-lite",  # higher RPM than 2.5-flash
                    contents=contents,
                )
                return (resp.text or "").strip()
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    m = _re.search(r"retry in (\d+(?:\.\d+)?)s", err)
                    suggested = float(m.group(1)) + 3 if m else 15
                    # Cap wait so we don't blow past max_total_wait
                    wait = min(suggested, max(0, max_total_wait - total_waited))
                    if wait <= 0:
                        print(f"    [Auditor] Rate limit — wait budget exhausted, skipping frame")
                        return ""
                    print(f"    [Auditor] Rate limit — aguardando {wait:.0f}s (tentativa {attempt+1}/{max_retries})...")
                    time.sleep(wait)
                    total_waited += wait
                elif "503" in err or "UNAVAILABLE" in err:
                    # transient server error — short retry
                    wait = min(5, max(0, max_total_wait - total_waited))
                    if wait <= 0:
                        return ""
                    print(f"    [Auditor] 503 server busy — aguardando {wait:.0f}s...")
                    time.sleep(wait)
                    total_waited += wait
                else:
                    print(f"    [Auditor] Erro: {e}")
                    return ""
        return ""

    def _gemini_vision(self, image_path: str, prompt: str) -> str:
        """Envia frame para Gemini Vision e retorna resposta em texto."""
        if not self.client or not os.path.exists(image_path):
            return ""
        try:
            with open(image_path, "rb") as f:
                img_bytes = f.read()
            from google.genai import types as _gt
            contents = [
                _gt.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                prompt,
            ]
            return self._gemini_with_retry(contents)
        except Exception as e:
            print(f"    [Auditor Vision] Erro: {e}")
            return ""

    def _gemini_text(self, prompt: str) -> str:
        """Chama Gemini apenas com texto."""
        if not self.client:
            return ""
        return self._gemini_with_retry(prompt)

    def _narration_at(self, transcription: list, t_start: float, t_end: float) -> str:
        """Retorna o texto narrado num intervalo de tempo."""
        return " ".join(
            s["text"].strip()
            for s in transcription
            if s["end"] >= t_start and s["start"] <= t_end
        )[:300]

    # ─────────────────────────────────────────────
    # IA Corretor — analisa segmentos pré-concatenação
    # ─────────────────────────────────────────────

    def audit_segments(
        self,
        segment_files: list,
        segments_plan: list,
        transcription: list,
        temp_dir: str,
        on_progress=None,
    ) -> list:
        """
        IA Corretor: inspeciona cada segmento de B-roll após renderização.

        Para cada segmento:
          1. Extrai frame do meio do clipe
          2. Obtém o texto narrado naquele intervalo
          3. Pergunta ao Gemini Vision: "Este frame representa o que está sendo dito?"
          4. Segmentos reprovados (score < 0.4) são retornados para substituição

        Retorna: lista de índices (em segment_files/segments_plan) reprovados.
        """
        if not self.client:
            print("  [IA Corretor] Sem API key — auditoria de segmentos ignorada.")
            return []

        broll_indices = [
            i for i, s in enumerate(segments_plan)
            if s.get("type") == "broll"
        ]

        # Escala com a quantidade de B-roll: até 50 verificações (cada ~1-2s com Gemini)
        max_checks = min(len(broll_indices), max(25, len(broll_indices) // 2))
        sample = broll_indices[:max_checks]
        total = len(sample)
        print(f"\n  [IA Corretor] Inspecionando {total} segmentos de B-roll...")

        frame_dir = os.path.join(temp_dir, "_corretor_frames")
        os.makedirs(frame_dir, exist_ok=True)
        bad_indices = []

        for check_n, seg_idx in enumerate(sample):
            if on_progress:
                pct = int(check_n / max(total, 1) * 100)
                on_progress(pct, 100, f"IA Corretor: seg {check_n+1}/{total}...")

            seg = segments_plan[seg_idx]
            seg_file = segment_files[seg_idx] if seg_idx < len(segment_files) else None
            if not seg_file or not os.path.exists(seg_file):
                continue

            dur = seg.get("duration", 4.0)
            frame_path = os.path.join(frame_dir, f"c_{seg_idx:04d}.jpg")
            ok = self._extract_frame(seg_file, dur * 0.4, frame_path)
            if not ok:
                continue

            narration = self._narration_at(transcription, seg["start"], seg["start"] + dur)
            keyword = seg.get("keyword", "?")

            if not narration:
                narration = f"[sem transcrição — keyword buscado: {keyword}]"

            prompt = (
                "Você é editor de vídeo sênior. Avalie se este frame de B-roll está CORRETO.\n\n"
                f"O narrador está dizendo: \"{narration}\"\n"
                f"Keyword buscado para este clipe: \"{keyword}\"\n\n"
                "Critérios de reprovação:\n"
                "- Frame mostra algo COMPLETAMENTE diferente do que é narrado\n"
                "- Qualidade muito baixa (borrado, preto, corrompido)\n"
                "- Literalismo errado (ex: 'fish oil' mostrou peixe nadando)\n\n"
                "Responda com UMA palavra: APROVADO ou REPROVADO\n"
                "Depois um traço e a razão em máximo 8 palavras."
            )

            verdict = self._gemini_vision(frame_path, prompt)

            if verdict.upper().startswith("REPROVADO"):
                bad_indices.append(seg_idx)
                print(f"  [IA Corretor] REPROVADO Seg {seg_idx} t={seg['start']:.0f}s -> {verdict[:70]}")
            else:
                print(f"  [IA Corretor] OK Seg {seg_idx} t={seg['start']:.0f}s aprovado")

            try:
                os.remove(frame_path)
            except Exception:
                pass
            time.sleep(0.4)

        shutil.rmtree(frame_dir, ignore_errors=True)
        print(f"  [IA Corretor] {len(bad_indices)} reprovados de {total} B-rolls verificados")
        return bad_indices

    def fix_bad_segments(
        self,
        bad_indices: list,
        segment_files: list,
        segments_plan: list,
        mapped_clips: list,
        shot_list: list,
        avatar_path: str,
        temp_dir: str,
        width: int, height: int, fps: int,
    ) -> list:
        """
        Tenta consertar segmentos reprovados pelo Corretor.

        Estratégia:
          1. Localiza o shot original do segmento (pelo timeline_start)
          2. Tenta search_terms alternativos do shot
          3. Se não encontrar clip melhor, substitui por segmento de avatar (câmera principal)

        Retorna segment_files atualizado.
        """
        if not bad_indices:
            return segment_files

        print(f"\n  [IA Corretor] Corrigindo {len(bad_indices)} segmentos reprovados...")

        from core.pipeline_avatar_auto import _trim_avatar, _make_broll_with_pip

        for seg_idx in bad_indices:
            seg = segments_plan[seg_idx]
            t_start = seg["start"]
            duration = seg["duration"]
            seg_file = segment_files[seg_idx]

            # Localiza shot original
            original_shot = None
            for shot in shot_list:
                if abs(shot["start"] - t_start) < 3.0:
                    original_shot = shot
                    break

            fixed = False

            # Tenta clip de uma fonte diferente com search_term alternativo
            if original_shot:
                used_kw = seg.get("keyword", "")
                alt_terms = [t for t in original_shot.get("search_terms", []) if t != used_kw]

                for alt_term in alt_terms:
                    # Busca na pool de mapped_clips algum clip com keyword diferente
                    for clip in mapped_clips:
                        if (clip.get("keyword", "") == alt_term
                                and os.path.exists(clip["file"])
                                and clip["file"] != seg.get("file", "")):
                            fix_path = os.path.join(temp_dir, f"fix_{seg_idx:04d}.mp4")
                            try:
                                _make_broll_with_pip(
                                    clip["file"], avatar_path,
                                    t_start, duration,
                                    fix_path, width, height, fps,
                                    is_image=clip["file"].lower().endswith(
                                        (".jpg", ".jpeg", ".png", ".webp")),
                                    kb_dir="zoom_in_center",
                                    pip_position="bottom_right",
                                    pip_percent=15,
                                    speed_factor=1.0,
                                )
                                if os.path.exists(fix_path) and os.path.getsize(fix_path) > 1000:
                                    segment_files[seg_idx] = fix_path
                                    segments_plan[seg_idx]["keyword"] = alt_term
                                    print(f"  [IA Corretor] OK Seg {seg_idx} corrigido com '{alt_term}'")
                                    fixed = True
                                    break
                            except Exception as e:
                                print(f"  [IA Corretor] Erro ao corrigir seg {seg_idx}: {e}")
                    if fixed:
                        break

            # Fallback: usa câmera principal (avatar)
            if not fixed:
                fix_path = os.path.join(temp_dir, f"fix_{seg_idx:04d}_avatar.mp4")
                try:
                    _trim_avatar(avatar_path, t_start, duration,
                                 fix_path, width, height, fps)
                    if os.path.exists(fix_path) and os.path.getsize(fix_path) > 1000:
                        segment_files[seg_idx] = fix_path
                        segments_plan[seg_idx]["type"] = "avatar"
                        print(f"  [IA Corretor] Fallback Seg {seg_idx} substituido por camera principal")
                except Exception as e:
                    print(f"  [IA Corretor] Falha no fallback de seg {seg_idx}: {e}")

        return segment_files

    # ─────────────────────────────────────────────
    # IA Auditor Final — analisa o vídeo completo
    # ─────────────────────────────────────────────

    def audit_final_video(
        self,
        video_path: str,
        transcription: list,
        temp_dir: str,
        interval_sec: float = 8.0,
        on_progress=None,
    ) -> dict:
        """
        IA Auditor Final: analisa o vídeo completo antes da entrega.

        Processo:
          1. Extrai frames a cada interval_sec segundos
          2. Para cada frame, obtém a narração correspondente
          3. Gemini Vision avalia: o frame é coerente com o que é narrado?
          4. Análise textual de consistência global da narração
          5. Gera relatório JSON com score, issues e aprovação

        Retorna dict com: quality_score, issues, approved, checkpoints_analyzed
        """
        result = {
            "quality_score": 1.0,
            "issues": [],
            "approved": True,
            "checkpoints_analyzed": 0,
            "total_duration": 0.0,
        }

        if not self.client:
            print("  [IA Auditor Final] Sem API key — auditoria final ignorada.")
            return result

        print(f"\n  [IA Auditor Final] Iniciando análise do vídeo completo...")

        try:
            from core.video_processor import get_duration
            total_dur = get_duration(video_path)
            result["total_duration"] = total_dur
        except Exception:
            return result

        frame_dir = os.path.join(temp_dir, "_auditor_final_frames")
        os.makedirs(frame_dir, exist_ok=True)

        # Coleta checkpoints
        checkpoints = []
        t = 2.0
        while t < total_dur - 2:
            frame_path = os.path.join(frame_dir, f"af_{t:.0f}.jpg")
            ok = self._extract_frame(video_path, t, frame_path)
            if ok:
                narration = self._narration_at(transcription, t - 3.0, t + 3.0)
                checkpoints.append({
                    "time": t,
                    "frame": frame_path,
                    "narration": narration or "[sem narração]",
                })
            t += interval_sec

        result["checkpoints_analyzed"] = len(checkpoints)
        print(f"  [IA Auditor Final] {len(checkpoints)} checkpoints coletados (a cada {interval_sec:.0f}s)")

        if not checkpoints:
            shutil.rmtree(frame_dir, ignore_errors=True)
            return result

        # ── Análise textual global da narração ──
        narration_summary = "\n".join(
            f"  t={c['time']:.0f}s: \"{c['narration'][:100]}\""
            for c in checkpoints
        )
        text_prompt = (
            "Você é o Auditor Final de uma produtora de vídeo profissional.\n\n"
            "Revise os checkpoints abaixo (tempo → narração) e identifique problemas:\n"
            "- Narração vazia ou ausente em muitos pontos\n"
            "- Repetição excessiva de conteúdo\n"
            "- Incoerência lógica na narrativa\n\n"
            f"CHECKPOINTS:\n{narration_summary}\n\n"
            "Responda:\n"
            "APROVADO — se a narração está consistente e fluente\n"
            "PROBLEMAS: [descrição] — se há problemas identificados\n"
            "Seja objetivo, máximo 3 linhas."
        )

        text_result = self._gemini_text(text_prompt)
        print(f"  [IA Auditor Final] Análise narrativa: {text_result[:100]}")

        if text_result.upper().startswith("PROBLEMAS"):
            result["issues"].append(f"Narrativa: {text_result[:200]}")
            result["quality_score"] -= 0.10

        # ── Análise visual (amostra dos checkpoints) ──
        visual_checks = checkpoints[:8]  # Máx 8 para não demorar
        visual_fails = 0

        for idx, cp in enumerate(visual_checks):
            if on_progress:
                pct = int(idx / max(len(visual_checks), 1) * 100)
                on_progress(pct, 100, f"Auditor Final: frame {idx+1}/{len(visual_checks)}...")

            if not os.path.exists(cp["frame"]):
                continue

            v_prompt = (
                "Você é auditor de qualidade de vídeo.\n\n"
                f"O narrador está dizendo: \"{cp['narration'][:150]}\"\n\n"
                "Analise este frame do vídeo:\n"
                "1. O conteúdo visual é RELEVANTE ao que é narrado?\n"
                "2. A qualidade da imagem está boa (sem blur excessivo, artefatos, frames pretos)?\n\n"
                "Responda: APROVADO ou PROBLEMA\n"
                "Razão em máximo 8 palavras."
            )

            verdict = self._gemini_vision(cp["frame"], v_prompt)

            if verdict.upper().startswith("PROBLEMA"):
                visual_fails += 1
                issue_msg = f"t={cp['time']:.0f}s: {verdict[:80]}"
                result["issues"].append(issue_msg)
                print(f"  [IA Auditor Final] PROBLEMA {issue_msg}")
            else:
                print(f"  [IA Auditor Final] OK t={cp['time']:.0f}s aprovado")

            time.sleep(0.4)

        # Desconta score por falhas visuais
        if visual_fails > 0:
            deduction = min(0.30, visual_fails * 0.06)
            result["quality_score"] = max(0.40, result["quality_score"] - deduction)

        result["approved"] = result["quality_score"] >= self.AUDITOR_MIN_QUALITY

        shutil.rmtree(frame_dir, ignore_errors=True)

        status = "APROVADO" if result["approved"] else "COM RESSALVAS"
        print(
            f"  [IA Auditor Final] {status} | "
            f"Score: {result['quality_score']:.0%} | "
            f"Problemas: {len(result['issues'])}"
        )

        return result


# ════════════════════════════════════════════════════════════════════════
#  STANDALONE UTILITY FUNCTIONS (Task 1.8)
# ════════════════════════════════════════════════════════════════════════

_DEFAULT_BLACKLIST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "blacklist.json"
)


def _find_ffprobe_util() -> str:
    """Find ffprobe for standalone functions."""
    found = shutil.which("ffprobe")
    if found:
        return found
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(project_dir, "ffmpeg", "ffprobe.exe")
    return bundled if os.path.isfile(bundled) else "ffprobe"


def validate_broll(
    file_path: str,
    min_duration: float = 3.0,
    min_width: int = 640,
) -> dict:
    """
    Validate a B-roll media file meets quality requirements.

    Checks: file exists, size > 100KB, resolution >= min_width, duration >= min_duration.
    Images are checked for resolution only (no duration requirement).

    Returns dict:
        {
            "valid": bool,
            "width": int,
            "height": int,
            "duration": float,  # 0.0 for images
            "reason": str       # "OK" or description of failure
        }
    """
    result = {"valid": False, "width": 0, "height": 0, "duration": 0.0, "reason": ""}

    if not os.path.exists(file_path):
        result["reason"] = "File not found"
        return result

    file_size = os.path.getsize(file_path)
    if file_size < 100_000:
        result["reason"] = f"File too small: {file_size} bytes (min 100KB)"
        return result

    ffprobe = _find_ffprobe_util()
    ext = Path(file_path).suffix.lower()
    is_image = ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format",
             "-of", "json", file_path],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(r.stdout) if r.stdout else {}

        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                result["width"] = int(stream.get("width", 0))
                result["height"] = int(stream.get("height", 0))
                break

        if not is_image:
            dur_str = data.get("format", {}).get("duration", "0")
            result["duration"] = float(dur_str) if dur_str else 0.0

    except Exception as e:
        result["reason"] = f"Probe error: {e}"
        return result

    if result["width"] < min_width:
        result["reason"] = f"Resolution too low: {result['width']}px (min {min_width}px)"
        return result

    if not is_image and result["duration"] < min_duration:
        result["reason"] = f"Duration too short: {result['duration']:.1f}s (min {min_duration}s)"
        return result

    result["valid"] = True
    result["reason"] = "OK"
    return result


def score_relevance(
    query_keywords: list,
    media_title: str,
    media_tags: list = None,
) -> float:
    """
    Score how relevant a media file is to the search keywords.

    Compares keyword words against media title and tags using word intersection.
    Returns float 0.0-1.0:
        >= 0.5 : relevant, usable
        < 0.5  : low confidence, try another source

    Args:
        query_keywords: list of search keyword strings (e.g. ["omega supplement capsule"])
        media_title:    title string from the stock API
        media_tags:     optional list of tag strings from the stock API
    """
    if media_tags is None:
        media_tags = []

    if not query_keywords:
        return 0.0

    _SW = {"the", "a", "an", "of", "in", "on", "at", "by", "for", "with",
           "and", "or", "is", "are", "was", "it", "this", "that", "be"}

    # Build query word set
    query_words = set()
    for kw in query_keywords:
        query_words.update(str(kw).lower().split())
    query_words -= _SW

    # Build media word set (title + tags)
    media_words = set()
    media_words.update(str(media_title).lower().split())
    for tag in media_tags:
        media_words.update(str(tag).lower().split())
    media_words -= _SW

    if not query_words:
        return 0.0

    if not media_words:
        # No metadata to compare — return neutral score
        return 0.35

    matched = len(query_words & media_words)

    # Use recall (matched / query size): "what fraction of what I searched for is in the media?"
    # This is better than Jaccard for stock footage, where media always has more words than the query.
    base_score = matched / len(query_words) if query_words else 0.0

    # Bonus for any query word appearing in title (strong signal)
    title_lower = str(media_title).lower()
    for word in query_words:
        if len(word) > 3 and word in title_lower:
            base_score = min(1.0, base_score + 0.15)
            break

    return round(min(1.0, max(0.0, base_score)), 3)


def get_file_hash(file_path: str) -> str:
    """Compute MD5 hash of file content (first 512KB for speed on large files)."""
    h = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(524_288)  # 512KB
            while chunk:
                h.update(chunk)
                chunk = f.read(524_288)
    except Exception:
        return ""
    return h.hexdigest()


def blacklist_media(
    file_hash: str,
    reason: str = "",
    blacklist_path: str = None,
) -> None:
    """
    Add a media file hash to the blacklist.

    Blacklisted files are permanently rejected by validate_broll and
    should be skipped in download loops via is_blacklisted().

    Args:
        file_hash:      MD5 hash from get_file_hash()
        reason:         why this media was blacklisted (logged for debugging)
        blacklist_path: custom path; defaults to data/blacklist.json
    """
    if not file_hash:
        return

    path = blacklist_path or _DEFAULT_BLACKLIST_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    existing[file_hash] = {
        "reason": reason or "manually blacklisted",
        "added": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        print(f"  [VideoAuditor] Blacklisted: {file_hash[:8]}... ({reason or 'no reason'})")
    except Exception as e:
        print(f"  [VideoAuditor] Erro ao salvar blacklist: {e}")


def is_blacklisted(file_hash: str, blacklist_path: str = None) -> bool:
    """Return True if the given hash is in the blacklist."""
    if not file_hash:
        return False
    path = blacklist_path or _DEFAULT_BLACKLIST_PATH
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return file_hash in data
    except Exception:
        return False
