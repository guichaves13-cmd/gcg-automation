"""
B-Roll Auditor — aplica picker_decisions.json a um beat_timeline existente.

Fluxo completo:
  1. Carregar beat_timeline.json (run anterior)
  2. Carregar picker_decisions.json (decisoes do usuario no Picker HTML)
  3. Construir amended_segments_plan:
       - approved  → reutiliza arquivo existente (sem re-download)
       - rejected  → converte em segmento avatar
       - replace   → re-busca com termos alternativos ou usa arquivo fornecido
       - (sem dec) → mantém como estava
  4. Re-renderizar apenas os segmentos que mudaram (faster than full pipeline)
  5. Salvar novo beat_timeline + picker HTML atualizados

Decision JSON format (exportado pelo Picker HTML):
  {
    "3": "approved",
    "5": "rejected",
    "7": "replace",
    "7_replacement": "/caminho/para/novo_clip.mp4"   # opcional para replace manual
  }

Ou formato extendido (com paths de substituicao):
  {
    "decisions": {"3": "approved", "5": "rejected", "7": "replace"},
    "replacements": {"7": "/path/to/file.mp4"}
  }
"""

import os
import json
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────
# Carregamento
# ─────────────────────────────────────────────────────────────────────────

def load_decisions(decisions_path: str) -> tuple:
    """
    Carrega picker_decisions.json.

    Suporta dois formatos:
      Simples: {"1": "approved", "3": "rejected"}
      Extendido: {"decisions": {...}, "replacements": {"5": "/path/file.mp4"}}

    Returns:
        (decisions_dict, replacements_dict)
        decisions_dict:    {beat_id_str: "approved"|"rejected"|"replace"}
        replacements_dict: {beat_id_str: "/abs/path/to/file.mp4"}
    """
    with open(decisions_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if "decisions" in raw and isinstance(raw["decisions"], dict):
        decisions = {str(k): v for k, v in raw["decisions"].items()}
        replacements = {str(k): v for k, v in raw.get("replacements", {}).items()}
    else:
        # Formato simples: tudo num nivel, separa "_replacement" suffix
        decisions = {}
        replacements = {}
        for k, v in raw.items():
            sk = str(k)
            if sk.endswith("_replacement"):
                beat_id = sk[: sk.index("_replacement")]
                replacements[beat_id] = v
            elif isinstance(v, str) and v in ("approved", "rejected", "replace"):
                decisions[sk] = v

    return decisions, replacements


def load_timeline(timeline_path: str) -> dict:
    """Carrega _beat_timeline.json."""
    with open(timeline_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────
# Analise das decisoes
# ─────────────────────────────────────────────────────────────────────────

def analyze_decisions(timeline: dict, decisions: dict, replacements: dict) -> dict:
    """
    Analisa quais beats precisam de acao e retorna um sumario.

    Returns dict com:
        total_broll: int
        approved: list de beat IDs
        rejected: list de beat IDs
        replace_manual: {beat_id: replacement_path}   # arquivo fornecido
        replace_auto:   list de beat IDs              # re-busca automatica
        unchanged: list de beat IDs                   # sem decisao
        invalid_files: list de beat IDs               # decisao keep mas arquivo sumiu
    """
    result = {
        "total_broll": 0,
        "approved": [],
        "rejected": [],
        "replace_manual": {},
        "replace_auto": [],
        "unchanged": [],
        "invalid_files": [],
    }

    for beat in timeline.get("beats", []):
        if beat.get("type") != "broll":
            continue
        result["total_broll"] += 1
        bid = str(beat["id"])
        dec = decisions.get(bid)
        file_path = beat.get("file", "")

        if dec == "approved":
            if file_path and os.path.isfile(file_path):
                result["approved"].append(bid)
            else:
                # Aprovado mas arquivo sumiu → trata como replace_auto
                result["replace_auto"].append(bid)
                result["invalid_files"].append(bid)
        elif dec == "rejected":
            result["rejected"].append(bid)
        elif dec == "replace":
            rep_path = replacements.get(bid, "")
            if rep_path and os.path.isfile(rep_path):
                result["replace_manual"][bid] = rep_path
            else:
                result["replace_auto"].append(bid)
        else:
            # Sem decisao
            if file_path and os.path.isfile(file_path):
                result["unchanged"].append(bid)
            else:
                result["replace_auto"].append(bid)
                if file_path:
                    result["invalid_files"].append(bid)

    return result


def print_analysis(analysis: dict):
    print(f"\n  [Auditor] Analise de decisoes:")
    print(f"    Total B-Roll beats: {analysis['total_broll']}")
    print(f"    Aprovados (reutilizados): {len(analysis['approved'])}")
    print(f"    Rejeitados (-> avatar):   {len(analysis['rejected'])}")
    print(f"    Substituir (manual):      {len(analysis['replace_manual'])}")
    print(f"    Re-buscar automaticamente:{len(analysis['replace_auto'])}")
    print(f"    Sem decisao (mantidos):   {len(analysis['unchanged'])}")
    if analysis["invalid_files"]:
        print(f"    Arquivos ausentes:        {len(analysis['invalid_files'])}")


# ─────────────────────────────────────────────────────────────────────────
# Construir segments_plan corrigido
# ─────────────────────────────────────────────────────────────────────────

def build_amended_plan(timeline: dict, decisions: dict, replacements: dict,
                        new_clips: dict = None) -> list:
    """
    Constrói segments_plan com as decisoes aplicadas.

    Args:
        timeline:     beat_timeline.json carregado
        decisions:    {beat_id_str: "approved"|"rejected"|"replace"}
        replacements: {beat_id_str: "/path/file.mp4"}   # manual replacement
        new_clips:    {beat_id_str: "/path/downloaded.mp4"}  # auto-downloaded

    Returns:
        list de segmentos no formato de pipeline_avatar_auto._build_smart_timeline()
    """
    new_clips = new_clips or {}
    plan = []

    for beat in timeline.get("beats", []):
        bid = str(beat["id"])
        btype = beat.get("type", "avatar")
        start = beat.get("start", 0)
        duration = beat.get("duration", 0)

        if btype == "avatar":
            plan.append({
                "type": "avatar",
                "start": start,
                "duration": duration,
            })
            continue

        # B-Roll beat
        dec = decisions.get(bid)

        if dec == "rejected":
            # Converte para avatar
            plan.append({
                "type": "avatar",
                "start": start,
                "duration": duration,
                "_auditor": f"rejected_beat_{bid}",
            })

        elif dec == "replace" and bid in replacements and os.path.isfile(replacements[bid]):
            # Substituicao manual
            plan.append({
                "type": "broll",
                "start": start,
                "duration": duration,
                "file": replacements[bid],
                "keyword": beat.get("keyword", ""),
                "shot_type": beat.get("shot_type", "wide"),
                "mood": beat.get("mood", "informative"),
                "is_image": _is_image(replacements[bid]),
                "_auditor": f"replaced_beat_{bid}",
                "_decision": "replace_manual",
            })

        elif bid in new_clips and os.path.isfile(new_clips[bid]):
            # Auto re-baixado
            plan.append({
                "type": "broll",
                "start": start,
                "duration": duration,
                "file": new_clips[bid],
                "keyword": beat.get("keyword", ""),
                "shot_type": beat.get("shot_type", "wide"),
                "mood": beat.get("mood", "informative"),
                "is_image": _is_image(new_clips[bid]),
                "_auditor": f"redownloaded_beat_{bid}",
                "_decision": "replace_auto",
            })

        else:
            # Approved, unchanged, ou nao conseguiu re-baixar → mantém original
            orig_file = beat.get("file", "")
            if orig_file and os.path.isfile(orig_file):
                plan.append({
                    "type": "broll",
                    "start": start,
                    "duration": duration,
                    "file": orig_file,
                    "keyword": beat.get("keyword", ""),
                    "shot_type": beat.get("shot_type", "wide"),
                    "mood": beat.get("mood", "informative"),
                    "is_image": _is_image(orig_file),
                    "_auditor": f"kept_beat_{bid}",
                    "_decision": dec or "unchanged",
                })
            else:
                # Arquivo original sumiu → converte para avatar (safe fallback)
                plan.append({
                    "type": "avatar",
                    "start": start,
                    "duration": duration,
                    "_auditor": f"missing_file_beat_{bid}",
                })

    return plan


def _is_image(path: str) -> bool:
    return path.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp"))


# ─────────────────────────────────────────────────────────────────────────
# Re-download automatico para beats "replace_auto"
# ─────────────────────────────────────────────────────────────────────────

def redownload_beats(beat_ids: list, timeline: dict, output_folder: str,
                     pexels_key: str = "", pixabay_key: str = "",
                     unsplash_key: str = "", on_progress=None) -> dict:
    """
    Tenta re-baixar clips para os beats marcados como replace_auto.

    Usa os search_terms do beat mas com shuffle diferente para evitar
    o mesmo clip rejeitado. Se houver termos alternativos no shot_list,
    usa-os prioritariamente.

    Returns:
        {beat_id_str: downloaded_file_path}
    """
    if not beat_ids:
        return {}

    os.makedirs(output_folder, exist_ok=True)
    beat_map = {str(b["id"]): b for b in timeline.get("beats", [])}
    downloaded = {}

    total = len(beat_ids)
    for idx, bid in enumerate(beat_ids):
        if on_progress:
            on_progress(idx, total, f"Re-baixando beat {bid} ({idx+1}/{total})...")

        beat = beat_map.get(str(bid), {})
        terms = beat.get("search_terms", [])
        if not terms:
            print(f"    [redownload] Beat {bid}: sem termos para re-busca")
            continue

        # Shuffle terms to try different result page (offset)
        import random
        search_terms_to_try = list(terms)
        random.shuffle(search_terms_to_try)

        # Try to get an alternative clip
        clip_out = None

        for term in search_terms_to_try[:3]:
            if pexels_key:
                try:
                    from core.pexels_stock import search_videos, download_file
                    results = search_videos(term, pexels_key, per_page=15, orientation="landscape")
                    # Skip first 5 to get fresh results
                    if results and len(results) > 5:
                        results = results[5:]
                    for vid in (results or []):
                        vid_id = str(vid.get("id", ""))
                        dl_path = os.path.join(output_folder, f"repl_{bid}_{vid_id}.mp4")
                        if download_file(vid.get("video_files", [{}])[0].get("link", ""), dl_path):
                            if os.path.getsize(dl_path) > 10_000:
                                clip_out = dl_path
                                break
                except Exception as e:
                    print(f"    [redownload] Pexels error: {e}")

            if clip_out:
                break

            if pixabay_key and not clip_out:
                try:
                    from core.pixabay_stock import search_videos, download_file
                    results = search_videos(term, pixabay_key, per_page=15)
                    for vid in (results or []):
                        vid_url = vid.get("videos", {}).get("medium", {}).get("url", "")
                        if vid_url:
                            vid_id = str(vid.get("id", ""))
                            dl_path = os.path.join(output_folder, f"repl_{bid}_{vid_id}.mp4")
                            if download_file(vid_url, dl_path):
                                if os.path.getsize(dl_path) > 10_000:
                                    clip_out = dl_path
                                    break
                except Exception as e:
                    print(f"    [redownload] Pixabay error: {e}")

            if clip_out:
                break

        if clip_out:
            downloaded[str(bid)] = clip_out
            print(f"    [redownload] Beat {bid}: OK ({os.path.basename(clip_out)})")
        else:
            print(f"    [redownload] Beat {bid}: falhou (sera avatar fallback)")

    return downloaded


# ─────────────────────────────────────────────────────────────────────────
# Re-renderizacao dos segmentos
# ─────────────────────────────────────────────────────────────────────────

def rerender_video(
    avatar_path: str,
    amended_plan: list,
    output_path: str,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    pip_position: str = "bottom_right",
    pip_percent: int = 22,
    subtitles_srt: str = "",
    on_progress=None,
    lower_thirds_enabled: bool = False,
    lower_thirds_style: str = "modern",
) -> bool:
    """
    Re-renderiza o video a partir do segments_plan corrigido.

    Segmentos aprovados (arquivo existente) sao processados diretamente.
    Segmentos rejeitados (→ avatar) usam _trim_avatar.

    Returns True se bem-sucedido.
    """
    from core.pipeline_avatar_auto import _trim_avatar, _make_broll_with_pip, _has_video
    from core.video_processor import concat_segments_with_audio, _find_ffmpeg, _get_encoder

    temp_dir = tempfile.mkdtemp(prefix="auditor_rerender_")
    print(f"\n  [Auditor] Re-renderizando {len(amended_plan)} segmentos em {temp_dir}...")

    try:
        import random
        rng = random.Random()
        pip_positions = ["bottom_right", "bottom_left", "top_right", "top_left"]

        segment_files = []
        total = len(amended_plan)

        for i, seg in enumerate(amended_plan):
            if on_progress:
                pct = int((i / max(total, 1)) * 80)
                on_progress(pct, 100, f"Segmento {i+1}/{total} ({seg['type']})...")

            seg_out = os.path.join(temp_dir, f"seg_{i:04d}.mp4")
            seg_ok = False

            try:
                if seg["type"] == "avatar":
                    _trim_avatar(avatar_path, seg["start"], seg["duration"],
                                 seg_out, width, height, fps)
                    seg_ok = os.path.isfile(seg_out) and os.path.getsize(seg_out) > 1000

                else:  # broll
                    broll_file = seg.get("file", "")
                    if not (broll_file and os.path.isfile(broll_file)):
                        raise FileNotFoundError(f"B-Roll file missing: {broll_file}")

                    pos = rng.choice(pip_positions)
                    kb_dir = rng.choice(["zoom_in_center", "zoom_out_center",
                                         "pan_left", "pan_right"])
                    _make_broll_with_pip(
                        broll_path=broll_file,
                        avatar_path=avatar_path,
                        start=seg["start"],
                        duration=seg["duration"],
                        output_path=seg_out,
                        width=width, height=height, fps=fps,
                        is_image=seg.get("is_image", False),
                        kb_dir=kb_dir,
                        pip_position=pos,
                        pip_percent=pip_percent,
                        fade_in=0.3, fade_out=0.3,
                    )
                    seg_ok = os.path.isfile(seg_out) and os.path.getsize(seg_out) > 1000

                    # Apply video filters for mood/shot_type
                    if seg_ok:
                        try:
                            from core.video_filters import apply_random_effects
                            filt_out = seg_out + ".fx.mp4"
                            apply_random_effects(seg_out, filt_out, seed=i,
                                                 mood=seg.get("mood"),
                                                 shot_type=seg.get("shot_type"))
                            if os.path.isfile(filt_out) and os.path.getsize(filt_out) > 1000:
                                os.replace(filt_out, seg_out)
                        except Exception:
                            pass

                    # Apply lower third with keyword (preserves auditor's keyword field)
                    if seg_ok and lower_thirds_enabled:
                        try:
                            kw = (seg.get("keyword") or "").strip()
                            if kw and len(kw) <= 60:
                                from core.motion_graphics import add_lower_third
                                lt_out = seg_out + ".lt.mp4"
                                lt_dur = min(float(seg.get("duration", 4.0)) - 0.5, 4.0)
                                if lt_dur >= 1.5:
                                    add_lower_third(
                                        seg_out, lt_out,
                                        text=kw.title(),
                                        subtitle=seg.get("shot_type", ""),
                                        start_time=0.5,
                                        duration=lt_dur,
                                        position="bottom_left",
                                        style=lower_thirds_style,
                                    )
                                    if os.path.isfile(lt_out) and os.path.getsize(lt_out) > 1000:
                                        os.replace(lt_out, seg_out)
                        except Exception:
                            pass

            except Exception as seg_e:
                print(f"  [Auditor] Seg {i} falhou ({seg_e}), fallback → avatar")
                try:
                    _trim_avatar(avatar_path, seg["start"], seg["duration"],
                                 seg_out, width, height, fps)
                    seg_ok = os.path.isfile(seg_out) and os.path.getsize(seg_out) > 1000
                except Exception as fb_e:
                    print(f"  [Auditor] Fallback avatar falhou ({fb_e})")

            segment_files.append(seg_out)

        # Concatenar
        if on_progress:
            on_progress(82, 100, "Concatenando segmentos...")

        valid = [f for f in segment_files if os.path.isfile(f) and os.path.getsize(f) > 500]
        print(f"  [Auditor] Concatenando {len(valid)}/{len(segment_files)} segmentos validos...")

        concat_out = os.path.join(temp_dir, "concat.mp4")
        concat_segments_with_audio(valid, concat_out)

        if not _has_video(concat_out):
            raise RuntimeError("Concat nao tem stream de video!")

        current = concat_out

        # Subtitles
        if subtitles_srt and os.path.isfile(subtitles_srt):
            if on_progress:
                on_progress(90, 100, "Aplicando legendas...")
            sub_out = os.path.join(temp_dir, "with_subs.mp4")
            ffmpeg = _find_ffmpeg()
            srt_esc = subtitles_srt.replace("\\", "/").replace(":", "\\:")
            enc = _get_encoder()
            cmd = [ffmpeg, "-y", "-hwaccel", "auto", "-i", current,
                   "-vf", f"subtitles='{srt_esc}':force_style="
                          f"'FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,"
                          f"OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV=30'",
                   ] + enc + ["-c:a", "copy", "-pix_fmt", "yuv420p", sub_out]
            r = subprocess.run(cmd, capture_output=True, timeout=900)
            if r.returncode == 0 and _has_video(sub_out):
                current = sub_out

        # Copiar para output final
        if on_progress:
            on_progress(97, 100, "Finalizando...")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        shutil.copy2(current, output_path)

        final_size = os.path.getsize(output_path) / 1024 / 1024
        print(f"  [Auditor] Re-render concluido: {output_path} ({final_size:.1f} MB)")
        if on_progress:
            on_progress(100, 100, "Re-render concluido!")
        return True

    except Exception as e:
        print(f"  [Auditor] Re-render FALHOU: {e}")
        return False

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────
# Timeline atualizada apos re-render
# ─────────────────────────────────────────────────────────────────────────

def build_amended_timeline(original_timeline: dict, amended_plan: list,
                            decisions: dict, replacements: dict,
                            new_clips: dict) -> dict:
    """
    Constrói novo beat_timeline refletindo as mudancas do re-render.

    Cada beat tem campo '_auditor' indicando o que aconteceu.
    """
    beat_map = {str(b["id"]): b for b in original_timeline.get("beats", [])}
    new_beats = []
    broll_count = 0
    avatar_count = 0

    for i, seg in enumerate(amended_plan):
        bid_orig = seg.get("_auditor", "").split("_")[-1]  # extrai numero original
        orig_beat = beat_map.get(bid_orig, {})
        decision_key = seg.get("_decision", "unchanged")

        if seg["type"] == "avatar":
            avatar_count += 1
            beat = {
                "id": i + 1,
                "type": "avatar",
                "start": round(float(seg["start"]), 2),
                "end": round(float(seg["start"]) + float(seg["duration"]), 2),
                "duration": round(float(seg["duration"]), 2),
                "narration_text": orig_beat.get("narration_text", ""),
                "search_terms": [],
                "shot_type": "",
                "mood": "",
                "status": "avatar_fallback" if "_auditor" in seg else "avatar",
                "_auditor_action": seg.get("_auditor", ""),
                "_decision": decision_key,
            }
        else:
            broll_count += 1
            beat = {
                "id": i + 1,
                "type": "broll",
                "start": round(float(seg["start"]), 2),
                "end": round(float(seg["start"]) + float(seg["duration"]), 2),
                "duration": round(float(seg["duration"]), 2),
                "narration_text": orig_beat.get("narration_text", ""),
                "search_terms": seg.get("search_terms", orig_beat.get("search_terms", [])),
                "shot_type": seg.get("shot_type", ""),
                "mood": seg.get("mood", ""),
                "source": orig_beat.get("source", ""),
                "file": seg.get("file", ""),
                "is_image": seg.get("is_image", False),
                "keyword": seg.get("keyword", orig_beat.get("keyword", "")),
                "validation_score": orig_beat.get("validation_score"),
                "status": "applied",
                "_auditor_action": seg.get("_auditor", ""),
                "_decision": decision_key,
            }

        new_beats.append(beat)

    amended = dict(original_timeline)
    amended.update({
        "schema_version": "1.1",
        "generated_at": datetime.now().isoformat(),
        "auditor_applied_at": datetime.now().isoformat(),
        "total_beats": len(new_beats),
        "broll_count": broll_count,
        "avatar_count": avatar_count,
        "beats": new_beats,
        "auditor_summary": {
            "decisions_applied": len(decisions),
            "approved": sum(1 for v in decisions.values() if v == "approved"),
            "rejected": sum(1 for v in decisions.values() if v == "rejected"),
            "replaced": sum(1 for v in decisions.values() if v == "replace"),
        },
    })
    return amended


# ─────────────────────────────────────────────────────────────────────────
# Funcao principal — run_auditor()
# ─────────────────────────────────────────────────────────────────────────

def run_auditor(
    timeline_path: str,
    decisions_path: str,
    avatar_path: str,
    output_path: str,
    *,
    pexels_key: str = "",
    pixabay_key: str = "",
    unsplash_key: str = "",
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    pip_percent: int = 22,
    subtitles_srt: str = "",
    on_progress=None,
    lower_thirds_enabled: bool = False,
    lower_thirds_style: str = "modern",
) -> dict:
    """
    Pipeline completo do auditor:
      1. Carrega timeline + decisions
      2. Analisa quais beats precisam de acao
      3. Re-baixa clips necessarios (replace_auto)
      4. Constroi segments_plan corrigido
      5. Re-renderiza o video
      6. Salva novo beat_timeline + picker HTML

    Returns dict com resultado:
        {
          "ok": bool,
          "output_path": str,
          "timeline_path": str,
          "picker_html": str,
          "stats": {approved, rejected, replaced_manual, replaced_auto, unchanged},
          "error": str (se falhou)
        }
    """
    print(f"\n{'='*60}")
    print(f"  B-ROLL AUDITOR — Aplicando Decisoes do Picker")
    print(f"{'='*60}")

    result = {
        "ok": False,
        "output_path": output_path,
        "timeline_path": "",
        "picker_html": "",
        "stats": {},
        "error": "",
    }

    # ── 1. Carregar ─────────────────────────────────────────────────────
    try:
        timeline = load_timeline(timeline_path)
        decisions, replacements = load_decisions(decisions_path)
        print(f"  Timeline: {len(timeline.get('beats', []))} beats")
        print(f"  Decisoes: {len(decisions)} ({', '.join(set(decisions.values()))})")
    except Exception as e:
        result["error"] = f"Erro ao carregar arquivos: {e}"
        return result

    # ── 2. Analisar ──────────────────────────────────────────────────────
    analysis = analyze_decisions(timeline, decisions, replacements)
    print_analysis(analysis)

    if on_progress:
        on_progress(5, 100, f"Analise: {analysis['total_broll']} B-Roll beats processados...")

    # ── 3. Re-baixar clips para replace_auto ─────────────────────────────
    new_clips = {}
    replace_auto_ids = analysis["replace_auto"]

    if replace_auto_ids:
        if on_progress:
            on_progress(10, 100, f"Re-baixando {len(replace_auto_ids)} clips alternativos...")

        repl_folder = os.path.join(
            os.path.dirname(output_path), "_auditor_repl"
        )
        print(f"\n  [Auditor] Re-baixando {len(replace_auto_ids)} clips...")
        new_clips = redownload_beats(
            replace_auto_ids, timeline, repl_folder,
            pexels_key=pexels_key,
            pixabay_key=pixabay_key,
            unsplash_key=unsplash_key,
            on_progress=on_progress,
        )

    # ── 4. Construir plano corrigido ──────────────────────────────────────
    if on_progress:
        on_progress(30, 100, "Construindo plano de segmentos corrigido...")

    amended_plan = build_amended_plan(timeline, decisions, replacements, new_clips)
    print(f"\n  [Auditor] Plano corrigido: {len(amended_plan)} segmentos")
    n_broll = sum(1 for s in amended_plan if s["type"] == "broll")
    n_avatar = sum(1 for s in amended_plan if s["type"] == "avatar")
    print(f"    B-Roll: {n_broll}  |  Avatar: {n_avatar}")

    # ── 5. Re-renderizar ─────────────────────────────────────────────────
    if on_progress:
        on_progress(35, 100, "Re-renderizando video...")

    ok_render = rerender_video(
        avatar_path=avatar_path,
        amended_plan=amended_plan,
        output_path=output_path,
        width=width, height=height, fps=fps,
        pip_percent=pip_percent,
        subtitles_srt=subtitles_srt,
        on_progress=on_progress,
        lower_thirds_enabled=lower_thirds_enabled,
        lower_thirds_style=lower_thirds_style,
    )

    if not ok_render:
        result["error"] = "Re-render falhou (ver logs)"
        return result

    # ── 6. Salvar timeline + picker atualizados ───────────────────────────
    if on_progress:
        on_progress(98, 100, "Salvando timeline e picker atualizados...")

    amended_tl = build_amended_timeline(timeline, amended_plan, decisions, replacements, new_clips)

    tl_out = output_path.replace(".mp4", "_beat_timeline.json")
    try:
        with open(tl_out, "w", encoding="utf-8") as f:
            json.dump(amended_tl, f, indent=2, ensure_ascii=False)
        print(f"  [Auditor] Timeline salvo: {tl_out}")
    except Exception as e:
        print(f"  [Auditor] Falha ao salvar timeline: {e}")

    picker_out = output_path.replace(".mp4", "_picker.html")
    try:
        from core.broll_picker import generate_picker
        generate_picker(amended_tl, picker_out)
    except Exception as e:
        print(f"  [Auditor] Falha ao gerar picker: {e}")

    # ── Resultado ─────────────────────────────────────────────────────────
    stats = {
        "total_broll": analysis["total_broll"],
        "approved":          len(analysis["approved"]),
        "rejected":          len(analysis["rejected"]),
        "replaced_manual":   len(analysis["replace_manual"]),
        "replaced_auto":     len(new_clips),
        "replace_auto_failed": len(replace_auto_ids) - len(new_clips),
        "unchanged":         len(analysis["unchanged"]),
    }

    result.update({
        "ok": True,
        "timeline_path": tl_out,
        "picker_html": picker_out,
        "stats": stats,
    })

    print(f"\n  [Auditor] CONCLUIDO!")
    print(f"    Aprovados:       {stats['approved']}")
    print(f"    Rejeitados:      {stats['rejected']}")
    print(f"    Substituidos:    {stats['replaced_manual'] + stats['replaced_auto']}")
    print(f"    Mantidos:        {stats['unchanged']}")
    print(f"{'='*60}\n")
    if on_progress:
        on_progress(100, 100, "Auditor concluido!")

    return result
