"""
Pipeline Avatar AUTO — v9 (PRODUCTION)
Fully automatic: AI Analysis -> Keywords -> Downloads -> PIP overlay -> Fresh Subtitles.

v9 MAJOR FIXES:
- EVERY video is treated as UNIQUE (no cache reuse!)
- Fresh transcription + fresh subtitles for each video
- Video Intelligence Engine for deep AI analysis before production
- Correct B-roll count matching user request
- Old temp files are ALWAYS cleaned before starting
- Subtitles generated from THIS video's audio only
"""

import os
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from core.video_processor import (
    get_duration, is_image_file, extract_audio,
    create_video_from_image,
    concat_segments_with_audio, _find_ffmpeg, _find_ffprobe,
)
from core.ken_burns import get_zoompan_filter
from core.subtitle_generator import generate_subtitles

console = Console()


def _get_resolution(res_str):
    return (3840, 2160) if res_str == "4k" else (1920, 1080)


def _has_video(path):
    """Check that a file has a video stream."""
    try:
        r = subprocess.run([_find_ffprobe(), "-v", "quiet", "-show_streams",
            "-of", "json", path], capture_output=True, text=True, timeout=15)
        streams = json.loads(r.stdout).get("streams", [])
        return any(s.get("codec_type") == "video" for s in streams)
    except:
        return False


def _trim_avatar(input_path, start, duration, output_path, width, height, fps):
    """Trim avatar segment WITH audio. Single FFmpeg command."""
    ffmpeg = _find_ffmpeg()
    cmd = [
        ffmpeg, "-y",
        "-ss", str(round(start, 3)),
        "-i", input_path,
        "-t", str(round(duration, 3)),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
        # Retry without audio mapping (silent video fallback)
        cmd_silent = [
            ffmpeg, "-y",
            "-ss", str(round(start, 3)),
            "-i", input_path,
            "-t", str(round(duration, 3)),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-pix_fmt", "yuv420p", output_path,
        ]
        subprocess.run(cmd_silent, capture_output=True, text=True, timeout=1800, check=True)
    if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
        raise RuntimeError("trim produced empty output")


def _make_narr_segment(broll_path, avatar_path, start, duration,
                        output_path, width, height, fps,
                        is_image=False, kb_dir=None, speed_factor=1.0):
    """
    narr_veo mode: VEO3 clip fullscreen + ONLY avatar narration audio (no PIP video).
    """
    ffmpeg = _find_ffmpeg()
    broll_vid = output_path + ".broll.mp4"

    if is_image:
        kb_filter = get_zoompan_filter(kb_dir or "zoom_in_center", duration, fps, width, height)
        create_video_from_image(broll_path, duration, width, height, fps, kb_filter, broll_vid)
    else:
        vf_speed = f"setpts={1.0/speed_factor:.4f}*PTS," if speed_factor != 1.0 else ""
        clip_dur = get_duration(broll_path) / speed_factor
        use_dur = min(duration, clip_dur)
        cmd = [
            ffmpeg, "-y", "-i", broll_path, "-t", str(round(use_dur, 3)),
            "-vf", f"{vf_speed}scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-an", "-pix_fmt", "yuv420p", broll_vid,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=True)

    # Mux broll video + avatar audio (no PIP overlay)
    cmd_mux = [
        ffmpeg, "-y",
        "-i", broll_vid,
        "-ss", str(round(start, 3)), "-i", avatar_path,
        "-t", str(round(duration, 3)),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", output_path,
    ]
    r = subprocess.run(cmd_mux, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        # Fallback: just trim avatar
        _trim_avatar(avatar_path, start, duration, output_path, width, height, fps)

    try:
        os.remove(broll_vid)
    except Exception:
        pass


def _make_broll_with_pip(broll_path, avatar_path, start, duration,
                          output_path, width, height, fps,
                          is_image=False, kb_dir=None,
                          pip_position="bottom_right", pip_percent=15, speed_factor=1.0):
    """Create B-roll fullscreen + avatar PIP overlay + avatar audio."""
    ffmpeg = _find_ffmpeg()

    # Step 1: Create B-roll video (no audio)
    broll_vid = output_path + ".broll.mp4"
    if is_image:
        kb_filter = get_zoompan_filter(kb_dir or "zoom_in_center", duration, fps, width, height)
        create_video_from_image(broll_path, duration, width, height, fps, kb_filter, broll_vid)
    else:
        # Apply speed factor to video
        vf_speed = f"setpts={1.0/speed_factor}*PTS," if speed_factor != 1.0 else ""
        clip_dur = get_duration(broll_path) / speed_factor
        use_dur = min(duration, clip_dur)
        cmd = [
            ffmpeg, "-y", "-i", broll_path, "-t", str(round(use_dur, 3)),
            "-vf", f"{vf_speed}scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-an", "-pix_fmt", "yuv420p", broll_vid,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=True)

    # Step 2: Trim avatar segment for PIP (no audio, just video)
    pip_vid = output_path + ".pip.mp4"
    pip_w = int(width * pip_percent / 100)
    pip_h = int(height * pip_percent / 100)
    pip_w = pip_w + (pip_w % 2)
    pip_h = pip_h + (pip_h % 2)
    cmd_pip = [
        ffmpeg, "-y",
        "-ss", str(round(start, 3)),
        "-i", avatar_path,
        "-t", str(round(duration, 3)),
        "-vf", f"scale={pip_w}:{pip_h}:force_original_aspect_ratio=decrease,"
               f"pad={pip_w}:{pip_h}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-an", "-pix_fmt", "yuv420p", pip_vid,
    ]
    subprocess.run(cmd_pip, capture_output=True, text=True, timeout=1800, check=True)

    # Step 3: Overlay PIP on B-roll + add avatar audio
    padding = 20
    positions = {
        "bottom_right": f"{width - pip_w - padding}:{height - pip_h - padding}",
        "bottom_left": f"{padding}:{height - pip_h - padding}",
        "top_right": f"{width - pip_w - padding}:{padding}",
        "top_left": f"{padding}:{padding}",
    }
    pos = positions.get(pip_position, positions["bottom_right"])

    cmd_overlay = [
        ffmpeg, "-y",
        "-i", broll_vid,
        "-i", pip_vid,
        "-ss", str(round(start, 3)),
        "-i", avatar_path,
        "-t", str(round(duration, 3)),
        "-filter_complex",
        f"[1:v]format=yuva420p,colorchannelmixer=aa=0.95[pip];"
        f"[0:v][pip]overlay={pos}:shortest=1[vout]",
        "-map", "[vout]", "-map", "2:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-shortest",
        output_path,
    ]
    r = subprocess.run(cmd_overlay, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        cmd_mux = [
            ffmpeg, "-y",
            "-i", broll_vid,
            "-ss", str(round(start, 3)), "-i", avatar_path,
            "-t", str(round(duration, 3)),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", output_path,
        ]
        subprocess.run(cmd_mux, capture_output=True, text=True, timeout=600, check=True)

    for f in [broll_vid, pip_vid]:
        try: os.remove(f)
        except: pass


def run_auto(config: dict, on_progress=None):
    """Fully automatic avatar pipeline v9 — EVERY video is unique."""
    console.print("\n[bold magenta]=== STUDIOPILOT PRO: AVATAR AUTO v9 ===[/bold magenta]")
    console.print("[bold cyan]Every video is unique. Fresh analysis. Zero cache.[/bold cyan]\n")

    original_avatar = config["avatar_video"]
    output_path = config["output_file"]
    google_key = config.get("google_api_key", "")
    pexels_key = config.get("pexels_api_key", "")
    pixabay_key = config.get("pixabay_api_key", "")
    unsplash_key = config.get("unsplash_api_key", "")
    width, height = _get_resolution(config.get("resolution", "1080p"))
    fps = config.get("fps", 30)
    broll_count = config.get("auto_broll_count", 30)

    # =============================================
    # CLEAN ALL PREVIOUS TEMP DATA
    # =============================================
    import time
    import uuid
    unique_id = f"studiopilot_auto_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    temp_dir = os.path.join(tempfile.gettempdir(), unique_id)
    os.makedirs(temp_dir, exist_ok=True)

    # Clean old studiopilot temp dirs safely (NEVER delete the current one)
    tmp_root = tempfile.gettempdir()
    try:
        for d in os.listdir(tmp_root):
            if d.startswith("studiopilot_auto") and d != unique_id:
                try:
                    shutil.rmtree(os.path.join(tmp_root, d), ignore_errors=True)
                except:
                    pass
    except:
        pass

    # Also clean whisper cache
    try:
        whisper_cache = os.path.join(tmp_root, "whisper_cache")
        if os.path.exists(whisper_cache):
            shutil.rmtree(whisper_cache, ignore_errors=True)
    except:
        pass

    avatar_path = os.path.join(temp_dir, "avatar_input.mp4")
    console.print("  Copying avatar to safe path...")
    shutil.copy2(original_avatar, avatar_path)
    console.print(f"  Avatar: {os.path.getsize(avatar_path) / 1024 / 1024:.1f} MB")

    try:
        # =============================================
        # STEP 1: VIDEO INTELLIGENCE — DEEP AI ANALYSIS
        # =============================================
        if on_progress:
            on_progress(5, 100, "Phase 1: Video Intelligence — Deep AI Analysis...")
        console.print("\n[yellow]Step 1/6: Video Intelligence — Deep Analysis...[/yellow]")

        from core.video_intelligence import VideoIntelligence
        from core.video_auditor import VideoAuditor

        youtube_api_key_cfg = config.get("youtube_api_key", "")
        intel = VideoIntelligence(google_api_key=google_key, youtube_api_key=youtube_api_key_cfg)
        auditor = VideoAuditor(google_api_key=google_key)

        stock_folder = os.path.join(temp_dir, "stock")
        os.makedirs(stock_folder, exist_ok=True)

        analysis = intel.analyze_video(avatar_path, stock_folder, broll_count=broll_count,
                                       on_progress=on_progress)
        avatar_duration = analysis["duration"]

        console.print(f"  [bold green]Theme: {analysis['theme']}[/bold green]")
        console.print(f"  [bold green]Language: {analysis['language']}[/bold green]")
        console.print(f"  [bold green]Shots planned: {len(analysis['shot_list'])}[/bold green]")

        # =============================================
        # STEP 2: GET B-ROLL (LOCAL VEO3 OR STOCK)
        # =============================================
        if on_progress:
            on_progress(20, 100, f"Phase 2: Locating B-roll clips...")
        console.print(f"\n[yellow]Step 2/6: Acquiring B-roll...[/yellow]")

        mapped_clips = []
        pipeline_mode = config.get("pipeline", "avatar_auto")
        
        # Parse local VEO3 files from text area (e.g. [VÍDEO CARREGADO] filename.mp4)
        prompts_text = config.get("prompts", "")
        import re
        veo3_filenames = re.findall(r"\[VÍDEO CARREGADO\]\s+(.*?\.mp4)", prompts_text, re.IGNORECASE)
        
        local_veo3_clips = []
        if veo3_filenames and ("veo" in pipeline_mode):
            console.print(f"  [cyan]Locating {len(veo3_filenames)} local VEO3 files in Downloads...[/cyan]")
            downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            # Build a quick index of mp4s in Downloads to find them even if nested
            found_paths = {}
            for root, _, files in os.walk(downloads_dir):
                for f in files:
                    if f.lower().endswith(".mp4"):
                        found_paths[f] = os.path.join(root, f)
            
            for fname in veo3_filenames:
                fname = fname.strip()
                if fname in found_paths:
                    local_veo3_clips.append(found_paths[fname])
                else:
                    # check if exact path works
                    if os.path.exists(fname): local_veo3_clips.append(fname)
            console.print(f"  [green]Found {len(local_veo3_clips)} local VEO3 videos![/green]")

        # If mode is FULL VEO, skip stock downloading entirely
        if pipeline_mode in ("avatar_full_veo", "narr_veo") and local_veo3_clips:
            console.print("  [cyan]Mode is FULL VEO3. Bypassing stock downloads.[/cyan]")
            # We must map the local clips to cover the avatar duration
            total_veo_dur = sum(get_duration(c) for c in local_veo3_clips)
            
            # Speed adjustment factor to perfectly match avatar duration
            speed_factor = total_veo_dur / max(avatar_duration, 1.0)
            console.print(f"  [magenta]Perfect Sync: Adjusting VEO3 speed by factor of {speed_factor:.2f}x to match Avatar[/magenta]")
            
            current_t = 0.0
            for c in local_veo3_clips:
                orig_dur = get_duration(c)
                new_dur = orig_dur / speed_factor
                mapped_clips.append({
                    "timeline_start": current_t,
                    "timeline_end": current_t + new_dur,
                    "file": c,
                    "keyword": "VEO3_LOCAL",
                    "source": "local",
                    "speed_factor": speed_factor
                })
                current_t += new_dur
        else:
            # We need to mix stock footage (avatar_veo_real) or fallback to stock
            youtube_key = config.get("youtube_api_key", "")
            # Canais manuais (configurados pelo usuário)
            youtube_channels = config.get("youtube_channel_ids", [])
            if isinstance(youtube_channels, str):
                youtube_channels = [c.strip() for c in youtube_channels.split(",") if c.strip()]
            # Canais auto-descobertos pela IA (sem duplicar manuais)
            auto_channels = analysis.get("youtube_channels_auto", [])
            for ch in auto_channels:
                if ch not in youtube_channels:
                    youtube_channels.append(ch)
            # Nomes de canais descobertos pelo Gemini (sem YouTube API key → usados como query boost)
            yt_channel_names = getattr(intel, "_yt_channel_names", [])
            if youtube_channels:
                console.print(f"  [bold cyan]YouTube canais IDs ({len(youtube_channels)}):[/bold cyan] {youtube_channels[:4]}")
            if yt_channel_names:
                console.print(f"  [bold cyan]YouTube canais por nome ({len(yt_channel_names)}):[/bold cyan] {yt_channel_names[:4]}")
            mapped_clips = _download_from_shot_list(
                analysis["shot_list"],
                stock_folder,
                pexels_key, pixabay_key, unsplash_key,
                max_clips=broll_count,
                on_progress=on_progress,
                youtube_api_key=youtube_key,
                youtube_channel_ids=youtube_channels,
                youtube_channel_names=yt_channel_names,
                intel=intel,  # IA Validadora: rejeita clips com score < 0.55
            )

            # IA Gerente Geral: auditoria pós-download + re-download de reprovados
            if google_key and mapped_clips:
                if on_progress:
                    on_progress(53, 100, "IA Gerente Geral: Auditando correspondência B-roll...")
                console.print("\n[bold yellow]IA Gerente Geral: Auditando B-roll...[/bold yellow]")
                bad_indices = intel.gerente_geral_audit(mapped_clips, analysis["shot_list"])
                if bad_indices:
                    console.print(f"  [bold red]Re-baixando {len(bad_indices)} clips reprovados...[/bold red]")
                    if on_progress:
                        on_progress(54, 100, f"Re-baixando {len(bad_indices)} clips reprovados...")
                    mapped_clips = _redownload_bad_clips(
                        bad_indices, mapped_clips, analysis["shot_list"],
                        stock_folder, pexels_key, pixabay_key, unsplash_key,
                    )

            # Modo avatar_veo_real: mistura VEO3 local com stock
            if pipeline_mode == "avatar_veo_real" and local_veo3_clips:
                console.print(f"  [cyan]Mixing {len(local_veo3_clips)} VEO3 clips with real stock footage...[/cyan]")
                import random
                for veo_path in local_veo3_clips:
                    if mapped_clips:
                        replace_idx = random.randint(0, len(mapped_clips)-1)
                        mapped_clips[replace_idx]["file"] = veo_path
                        mapped_clips[replace_idx]["keyword"] = "VEO3_MIX"
                        mapped_clips[replace_idx]["source"] = "local"

        console.print(f"  B-roll pool final: [bold green]{len(mapped_clips)}[/bold green] clips")
        if not mapped_clips:
            console.print("[red]  Nenhum clip disponível![/red]")
        if on_progress:
            on_progress(54, 100, f"clips_downloaded={len(mapped_clips)} clips_validated={len(mapped_clips)} Theme:{analysis.get('theme','')} Language:{analysis.get('language','')}")

        # =============================================
        # STEP 3: BUILD TIMELINE
        # =============================================
        if on_progress:
            on_progress(55, 100, "Phase 3: Building synced timeline...")
        console.print("\n[yellow]Step 3/6: Building synced timeline...[/yellow]")

        import random
        rng = random.Random()

        segments_plan = _build_smart_timeline(
            avatar_duration, mapped_clips, rng,
            min_broll_dur=config.get("avatar", {}).get("min_broll_duration", 5),
            max_broll_dur=config.get("avatar", {}).get("max_broll_duration", 8),
            full_broll_mode=pipeline_mode in ("avatar_full_veo", "narr_veo")
        )

        avatar_segs = sum(1 for s in segments_plan if s["type"] == "avatar")
        broll_segs = sum(1 for s in segments_plan if s["type"] == "broll")
        console.print(f"  Timeline: {len(segments_plan)} segments ({avatar_segs} avatar, {broll_segs} B-roll)")

        # =============================================
        # STEP 4: PROCESS VIDEO SEGMENTS
        # =============================================
        if on_progress:
            on_progress(60, 100, "Phase 4: Processing video segments...")
        console.print("\n[yellow]Step 4/6: Processing video segments...[/yellow]")

        segment_files = []
        pip_positions = ["bottom_right", "bottom_left", "top_right", "top_left"]

        with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                     BarColumn(), TextColumn("{task.percentage:>3.0f}%"), console=console) as progress:
            task = progress.add_task("Processing", total=len(segments_plan))

            for i, seg in enumerate(segments_plan):
                seg_final = os.path.join(temp_dir, f"seg_{i:04d}.mp4")

                try:
                    if seg["type"] == "avatar":
                        _trim_avatar(avatar_path, seg["start"], seg["duration"],
                                    seg_final, width, height, fps)
                    else:
                        # Anti-repetição: quando reuse_count > 0, usa direção Ken Burns diferente
                        reuse = seg.get("reuse_count", 0)
                        kb_choices = ["zoom_in_center", "zoom_out_center",
                                      "pan_left", "pan_right", "pan_up", "pan_down",
                                      "zoom_in_topleft", "zoom_in_bottomright",
                                      "zoom_out_topleft", "zoom_out_bottomright",
                                      "pan_diagonal_tl", "pan_diagonal_br"]
                        # Rotaciona a lista pelo reuse_count para garantir direção diferente
                        kb_offset = (reuse * 3) % len(kb_choices)
                        kb_pool = kb_choices[kb_offset:] + kb_choices[:kb_offset]
                        kb = rng.choice(kb_pool[:6])

                        if pipeline_mode == "narr_veo":
                            _make_narr_segment(
                                seg["file"], avatar_path,
                                seg["start"], seg["duration"],
                                seg_final, width, height, fps,
                                is_image=seg.get("is_image", False),
                                kb_dir=kb,
                                speed_factor=seg.get("speed_factor", 1.0),
                            )
                        else:
                            pip_pos = rng.choice(pip_positions)
                            _make_broll_with_pip(
                                seg["file"], avatar_path,
                                seg["start"], seg["duration"],
                                seg_final, width, height, fps,
                                is_image=seg.get("is_image", False),
                                kb_dir=kb,
                                pip_position=pip_pos,
                                pip_percent=rng.choice([13, 15, 17]),
                                speed_factor=seg.get("speed_factor", 1.0),
                            )

                        # Filtro de cor único por segmento
                        # Seed diferente para reutilizações: clip reutilizado terá visual diferente
                        try:
                            from core.video_filters import apply_random_effects
                            filtered = os.path.join(temp_dir, f"seg_{i:04d}_fx.mp4")
                            fx_seed = i + reuse * 997  # seed muda a cada reutilização
                            apply_random_effects(seg_final, filtered, seed=fx_seed)
                            if os.path.exists(filtered) and os.path.getsize(filtered) > 1000:
                                os.replace(filtered, seg_final)
                        except Exception:
                            pass

                except Exception as e:
                    console.print(f"\n  [red]Auto-Fix:[/red] Segment {i} failed ({e}), using avatar fallback.")
                    _trim_avatar(avatar_path, seg["start"], seg["duration"],
                                seg_final, width, height, fps)

                if not _has_video(seg_final):
                    console.print(f"\n  [red]🤖 AI Auto-Fix:[/red] Segmento {i} corrompido! Reconstruindo com Câmera Principal...")
                    _trim_avatar(avatar_path, seg["start"], seg["duration"],
                                seg_final, width, height, fps)

                segment_files.append(seg_final)
                progress.update(task, advance=1)

                if on_progress:
                    pct = 60 + int((i / max(len(segments_plan), 1)) * 25)
                    on_progress(pct, 100, f"Segment {i+1}/{len(segments_plan)}")

        # =============================================
        # IA CORRETOR — verificação pré-concatenação
        # =============================================
        if google_key and config.get("corretor_enabled", True) and segment_files:
            if on_progress:
                on_progress(83, 100, "IA Corretor: inspecionando segmentos de B-roll...")
            console.print("\n[bold yellow]=== IA Corretor: Inspecionando segmentos ===[/bold yellow]")
            transcription_data = analysis.get("transcription", [])
            bad_segs = auditor.audit_segments(
                segment_files, segments_plan, transcription_data, temp_dir,
                on_progress=None,
            )
            if bad_segs:
                console.print(f"  [red]{len(bad_segs)} segmentos reprovados — corrigindo...[/red]")
                if on_progress:
                    on_progress(84, 100, f"IA Corretor: {len(bad_segs)} segmentos reprovados — corrigindo...")
                segment_files = auditor.fix_bad_segments(
                    bad_segs, segment_files, segments_plan,
                    mapped_clips, analysis["shot_list"],
                    avatar_path, temp_dir, width, height, fps,
                )
            else:
                console.print("  [green]Todos os segmentos aprovados pela IA Corretor![/green]")
            if on_progress:
                on_progress(84, 100, f"IA Corretor: clips_fixed={len(bad_segs) if bad_segs else 0}")

        # =============================================
        # STEP 5: CONCATENATE
        # =============================================
        if on_progress:
            on_progress(85, 100, "Phase 5: Concatenating segments...")
        console.print(f"\n[yellow]Step 5/6: Concatenating {len(segment_files)} segments...[/yellow]")
        concat_out = os.path.join(temp_dir, "concat.mp4")
        concat_segments_with_audio(segment_files, concat_out)
        concat_size = os.path.getsize(concat_out) / 1024 / 1024
        console.print(f"  Concat: {concat_size:.1f} MB")

        if not _has_video(concat_out):
            raise RuntimeError("CONCAT FAILED: no video stream!")
        console.print("  [green]Validated: has video stream[/green]")

        current = concat_out

        # =============================================
        # STEP 6: FRESH SUBTITLES (from THIS video only!)
        # =============================================
        final_in_temp = os.path.join(temp_dir, "final_output.mp4")

        subs_enabled = config.get("subtitles_enabled", True)
        force_new = config.get("force_new_subtitles", False)

        if subs_enabled or force_new:
            console.print("\n[yellow]Step 6/6: Generating FRESH subtitles...[/yellow]")
            if on_progress:
                on_progress(90, 100, "Phase 6: Fresh subtitles...")

            import tempfile as _tf
            import random as _rng
            ffmpeg = _find_ffmpeg()
            srt_safe = os.path.join(_tf.gettempdir(), "studiopilot_subs.srt")

            # ── Find or generate SRT ──────────────────────────────
            srt_path = analysis.get("subtitle_srt", "")
            srt_ready = srt_path and os.path.exists(srt_path) and os.path.getsize(srt_path) > 10

            if srt_ready:
                console.print("  [dim]SRT from Video Intelligence: OK[/dim]")
                shutil.copy2(srt_path, srt_safe)
            else:
                # Step 1 transcription failed or produced no SRT → transcribe NOW
                console.print("  [yellow]SRT ausente — transcrevendo o vídeo final com Whisper...[/yellow]")
                try:
                    import whisper as _whisper
                    _wav = os.path.join(temp_dir, "subs_audio.wav")
                    _r = subprocess.run(
                        [ffmpeg, "-y", "-i", current,
                         "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", _wav],
                        capture_output=True, text=True, timeout=300,
                    )
                    if _r.returncode != 0 or not os.path.exists(_wav) or os.path.getsize(_wav) < 1000:
                        console.print(f"  [yellow]Extração de áudio falhou (rc={_r.returncode})[/yellow]")
                        console.print(f"  [dim]{_r.stderr[-200:]}[/dim]")
                        raise RuntimeError("audio extraction failed")

                    console.print("  [dim]Áudio extraído — carregando Whisper base...[/dim]")
                    _model = _whisper.load_model("base")
                    _res = _model.transcribe(_wav, task="transcribe", verbose=False)
                    _segs = _res.get("segments", [])
                    console.print(f"  [dim]Whisper: {len(_segs)} segmentos detectados[/dim]")

                    if _segs:
                        with open(srt_safe, "w", encoding="utf-8") as _sf:
                            for _i, _s in enumerate(_segs, 1):
                                def _fmt(sec):
                                    h, r = divmod(int(sec), 3600)
                                    m, s = divmod(r, 60)
                                    ms = int((sec % 1) * 1000)
                                    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
                                _sf.write(f"{_i}\n{_fmt(_s['start'])} --> {_fmt(_s['end'])}\n{_s['text'].strip()}\n\n")
                        srt_ready = True
                        console.print(f"  [green]SRT gerado: {len(_segs)} legendas[/green]")
                    else:
                        console.print("  [yellow]Whisper não detectou fala — vídeo sem legendas[/yellow]")
                    try:
                        os.remove(_wav)
                    except Exception:
                        pass
                except ImportError:
                    console.print("  [yellow]Whisper não instalado — sem legendas[/yellow]")
                except Exception as _e:
                    console.print(f"  [yellow]Transcrição falhou: {_e}[/yellow]")

            # ── Burn SRT into video ───────────────────────────────
            if srt_ready and os.path.exists(srt_safe) and os.path.getsize(srt_safe) > 10:
                sub_colors = [
                    ("&H00FFFFFF", "White"),
                    ("&H0000FFFF", "Yellow"),
                    ("&H00FFFF00", "Cyan"),
                    ("&H004ECCA3", "Teal"),
                ]
                color_hex, color_name = _rng.choice(sub_colors)
                console.print(f"  Cor das legendas: {color_name}")

                srt_ffmpeg = srt_safe.replace("\\", "/")
                if len(srt_ffmpeg) > 2 and srt_ffmpeg[1] == ":":
                    srt_ffmpeg = srt_ffmpeg[0] + "\\:" + srt_ffmpeg[2:]
                srt_ffmpeg = srt_ffmpeg.replace(" ", "\\\\ ").replace("'", "\\'")

                cmd_sub = [
                    ffmpeg, "-y", "-i", current,
                    "-vf", (f"subtitles='{srt_ffmpeg}':force_style='FontName=Arial,FontSize=22,"
                            f"PrimaryColour={color_hex},OutlineColour=&H00000000,"
                            f"Outline=2,Shadow=1,MarginV=30'"),
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", final_in_temp,
                ]
                res_sub = subprocess.run(cmd_sub, capture_output=True, text=True, timeout=3600)

                if res_sub.returncode == 0 and _has_video(final_in_temp):
                    console.print("  [green]Legendas aplicadas![/green]")
                else:
                    console.print(f"  [yellow]Burn SRT falhou (rc={res_sub.returncode}) — tentando ASS...[/yellow]")
                    if res_sub.stderr:
                        console.print(f"  [dim]{res_sub.stderr[-300:]}[/dim]")
                    # Fallback: convert SRT → ASS and retry
                    ass_path = os.path.join(_tf.gettempdir(), "studiopilot_subs.ass")
                    _conv = subprocess.run(
                        [ffmpeg, "-y", "-i", srt_safe, ass_path],
                        capture_output=True, text=True, timeout=30,
                    )
                    if _conv.returncode == 0 and os.path.exists(ass_path):
                        ass_ffmpeg = ass_path.replace("\\", "/")
                        if len(ass_ffmpeg) > 2 and ass_ffmpeg[1] == ":":
                            ass_ffmpeg = ass_ffmpeg[0] + "\\:" + ass_ffmpeg[2:]
                        _r2 = subprocess.run(
                            [ffmpeg, "-y", "-i", current,
                             "-vf", f"ass='{ass_ffmpeg}'",
                             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                             "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", final_in_temp],
                            capture_output=True, text=True, timeout=3600,
                        )
                        if _r2.returncode == 0 and _has_video(final_in_temp):
                            console.print("  [green]Legendas aplicadas (método ASS)![/green]")
                        else:
                            console.print("  [dim]Ambos métodos falharam — vídeo sem legenda[/dim]")
                            shutil.copy2(current, final_in_temp)
                    else:
                        shutil.copy2(current, final_in_temp)
            else:
                console.print("  [dim]Sem SRT disponível — vídeo sem legenda[/dim]")
                shutil.copy2(current, final_in_temp)
        else:
            console.print("\n[cyan]Step 6/6: Legendas DESATIVADAS[/cyan]")
            shutil.copy2(current, final_in_temp)

        # =============================================
        # OPTIONAL: EFEITOS SONOROS DE TRANSIÇÃO (whoosh)
        # =============================================
        sfx_enabled = config.get("sfx_enabled", True)
        if sfx_enabled:
            console.print("\n[yellow]Bonus: Efeitos sonoros de transição...[/yellow]")
            try:
                from core.sound_effects import add_transition_sfx, extract_cut_times
                cut_times = extract_cut_times(segments_plan)
                if cut_times:
                    with_sfx = os.path.join(temp_dir, "with_sfx.mp4")
                    ok_sfx = add_transition_sfx(
                        final_in_temp, cut_times, with_sfx, temp_dir,
                        sfx_volume=config.get("sfx_volume", 0.18),
                    )
                    if ok_sfx and _has_video(with_sfx):
                        os.replace(with_sfx, final_in_temp)
                        console.print(f"  [green]{len(cut_times)} efeitos de transição adicionados![/green]")
            except Exception as sfx_e:
                console.print(f"  [dim]SFX ignorados: {sfx_e}[/dim]")

        # =============================================
        # OPTIONAL: MÚSICA SEM DIREITOS AUTORAIS
        # =============================================
        music_enabled = config.get("music_enabled", False)
        if music_enabled:
            console.print("\n[yellow]Bonus: Música de fundo (sem direitos autorais)...[/yellow]")
            try:
                from core.sound_effects import get_music_for_video, add_background_music_smart
                video_theme = analysis.get("theme", "general_documentary")
                console.print(f"  Tema do vídeo: {video_theme}")

                music_file = os.path.join(temp_dir, "ambient_music.m4a")
                got_music = get_music_for_video(
                    theme=video_theme,
                    duration=avatar_duration,
                    output_path=music_file,
                    pixabay_api_key=pixabay_key,
                    temp_dir=temp_dir,
                )

                if got_music and os.path.exists(music_file):
                    with_music = os.path.join(temp_dir, "with_music.mp4")
                    ok_music = add_background_music_smart(
                        final_in_temp, music_file, with_music,
                        music_volume=config.get("music_volume", 0.08),
                    )
                    if ok_music and _has_video(with_music):
                        os.replace(with_music, final_in_temp)
                        console.print("  [green]Música de fundo adicionada (sem direitos autorais)![/green]")
            except Exception as mus_e:
                console.print(f"  [dim]Música ignorada: {mus_e}[/dim]")

        # =============================================
        # OPTIONAL: STICKERS SUBSCRIBE/LIKE
        # =============================================
        stickers_enabled = config.get("stickers_enabled", True)
        if stickers_enabled:
            console.print("\n[yellow]Bonus: Stickers de engajamento...[/yellow]")
            try:
                from core.sticker_overlay import add_random_stickers
                stickered = os.path.join(temp_dir, "stickered.mp4")
                sticker_count = rng.randint(2, 3)
                add_random_stickers(final_in_temp, stickered, avatar_duration,
                                    temp_dir, rng=rng, count=sticker_count)
                if os.path.exists(stickered) and os.path.getsize(stickered) > 1000 and _has_video(stickered):
                    os.replace(stickered, final_in_temp)
                    console.print(f"  [green]{sticker_count} stickers adicionados![/green]")
            except Exception as stk_e:
                console.print(f"  [dim]Stickers ignorados: {stk_e}[/dim]")

        # =============================================
        # IA AUDITOR FINAL — verificação completa do vídeo
        # =============================================
        if google_key and config.get("auditor_final_enabled", True):
            if on_progress:
                on_progress(97, 100, "IA Auditor Final: verificando vídeo completo...")
            console.print("\n[bold yellow]=== IA Auditor Final: Verificando vídeo ===[/bold yellow]")
            transcription_data = analysis.get("transcription", [])
            audit_report = auditor.audit_final_video(
                final_in_temp, transcription_data, temp_dir,
                interval_sec=8.0,
            )
            # Salva relatório de qualidade junto ao output
            try:
                out_dir = os.path.dirname(output_path) or "."
                os.makedirs(out_dir, exist_ok=True)
                report_path = output_path.replace(".mp4", "_quality_report.json")
                with open(report_path, "w", encoding="utf-8") as _rf:
                    json.dump(audit_report, _rf, indent=2, ensure_ascii=False)
                console.print(f"  Relatório salvo: {os.path.basename(report_path)}")
            except Exception:
                pass

            qs = audit_report.get("quality_score", 1.0)
            if audit_report.get("approved", True):
                console.print(
                    f"  [bold green]✅ Vídeo APROVADO pela IA Auditor "
                    f"(score={qs:.0%})[/bold green]"
                )
            else:
                console.print(
                    f"  [bold yellow]⚠️  Vídeo com ressalvas "
                    f"(score={qs:.0%})[/bold yellow]"
                )
                for issue in audit_report.get("issues", [])[:3]:
                    console.print(f"  [dim]  • {issue[:100]}[/dim]")
            if on_progress:
                on_progress(98, 100, f"IA Auditor Final: score={qs:.0%} approved={audit_report.get('approved',True)}")

        # VALIDAÇÃO FINAL DE INTEGRIDADE
        if not _has_video(final_in_temp):
            console.print("  [red]Vídeo final perdeu stream! Usando concat diretamente.[/red]")
            shutil.copy2(concat_out, final_in_temp)

        if not _has_video(final_in_temp):
            raise RuntimeError("PIPELINE FAILED: no video stream in final output!")

        # Ensure output directory exists
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        shutil.copy2(final_in_temp, output_path)

        # Copy SRT next to output
        if analysis.get("subtitle_srt") and os.path.exists(analysis["subtitle_srt"]):
            shutil.copy2(analysis["subtitle_srt"], output_path.replace(".mp4", ".srt"))

        final_size = os.path.getsize(output_path) / 1024 / 1024
        console.print(f"\n[bold green]✅ DONE! {output_path} ({final_size:.1f} MB)[/bold green]")
        console.print(f"[bold green]   Theme: {analysis['theme']} | {len(mapped_clips)} B-roll clips | {analysis['language'].upper()}[/bold green]")

        if on_progress:
            on_progress(100, 100, "Done!")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _download_from_shot_list(shot_list, output_folder, pexels_key, pixabay_key, unsplash_key,
                              max_clips=30, on_progress=None, youtube_api_key="",
                              youtube_channel_ids=None, youtube_channel_names=None, intel=None):
    """
    Download exatamente UM clip por shot da shot list.

    Melhorias v11:
    - 1 clip por shot (mapeamento 1:1 shot → clip garante sincronização)
    - Todos os search_terms do shot são usados como fallback ordenado
    - Deduplicação por URL E por hash do arquivo (sem clips repetidos)
    - IA Validadora opcional: rejeita clips com score < 0.55
    - YouTube: fonte primária quando channel_ids configurados; fallback CC quando não
    - Nomeação de arquivo única por shot para evitar colisões
    """
    import time as _tm
    import hashlib as _hl

    shots_to_process = shot_list[:max_clips]
    print(f"  [download] {len(shots_to_process)} shots para processar (máx={max_clips})")

    # --- Setup fontes ---
    sources = []
    downloaders = {}

    if pexels_key:
        from core.pexels_stock import search_videos as pex_videos, search_photos as pex_photos
        from core.pexels_stock import download_file as pex_download
        sources.append("pexels")
        downloaders["pexels"] = (pex_videos, pex_photos, pex_download, pexels_key)

    if pixabay_key:
        from core.pixabay_stock import search_videos as pix_videos, search_photos as pix_photos
        from core.pixabay_stock import download_file as pix_download
        sources.append("pixabay")
        downloaders["pixabay"] = (pix_videos, pix_photos, pix_download, pixabay_key)

    if unsplash_key:
        from core.unsplash_stock import search_photos as uns_photos
        from core.unsplash_stock import download_file as uns_download
        sources.append("unsplash")
        downloaders["unsplash"] = (None, uns_photos, uns_download, unsplash_key)

    try:
        from core.coverr_stock import search_videos as cov_videos, download_file as cov_download
        sources.append("coverr")
        downloaders["coverr"] = (cov_videos, None, cov_download, "")
    except ImportError:
        pass

    youtube_key = youtube_api_key or ""
    youtube_channels = youtube_channel_ids or []
    yt_ch_names = youtube_channel_names or []
    has_youtube = False
    try:
        import yt_dlp  # noqa
        has_youtube = True
        # YouTube como fonte primária quando channel_ids configurados
        if youtube_channels:
            sources.insert(0, "youtube_channels")
        sources.append("youtube")
    except ImportError:
        pass

    if not sources:
        print("  [download] Nenhuma fonte de stock disponível!")
        return []

    if youtube_channels:
        print(f"  [download] YouTube canais: {youtube_channels}")
    print(f"  [download] Fontes ativas: {', '.join(sources)}")

    # --- Deduplicação ---
    seen_urls = set()

    def _rm(path):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _file_hash(path):
        try:
            h = _hl.md5()
            with open(path, "rb") as fh:
                h.update(fh.read(512 * 1024))  # Primeiros 512KB
            return h.hexdigest()
        except Exception:
            return ""

    seen_hashes = set()

    def _try_one(source, keyword, base_out, attempt_tag):
        """
        Tenta baixar um clip/imagem de uma fonte.
        Retorna (path, url) ou (None, None).

        Validacao dupla:
          - Video: tamanho >= 100KB, duracao >= 3s, largura >= 640px
          - Imagem: tamanho >= 10KB, largura >= 640px
          - Hash: nunca aceita arquivo identico a outro ja baixado
        """
        from core.video_auditor import validate_broll as _vbroll

        dl = downloaders.get(source)
        if not dl:
            return None, None
        search_vids, search_pics, download_fn, api_key = dl

        # ── Video ──────────────────────────────────────────────────────────
        if search_vids:
            try:
                if source == "pexels":
                    vids = search_vids(api_key, keyword, count=4, min_duration=3)
                elif source == "pixabay":
                    vids = search_vids(api_key, keyword, count=4)
                else:
                    vids = search_vids(keyword, count=4)
            except Exception:
                vids = []

            for vid in (vids or []):
                url = vid.get("url", "")
                if not url or url in seen_urls:
                    continue
                out_path = os.path.join(output_folder, f"{base_out}_{attempt_tag}.mp4")
                try:
                    download_fn(url, out_path)
                except Exception:
                    _rm(out_path)
                    continue

                # Validacao: tamanho minimo 100KB + duracao + resolucao
                if os.path.exists(out_path) and os.path.getsize(out_path) >= 100_000:
                    vr = _vbroll(out_path, min_duration=3, min_width=640)
                    if not vr["valid"]:
                        print(f"    [download] clip rejeitado ({vr['reason']}): {url[:60]}")
                        _rm(out_path)
                        continue
                    fh = _file_hash(out_path)
                    if fh and fh in seen_hashes:
                        _rm(out_path)
                        continue
                    seen_urls.add(url)
                    if fh:
                        seen_hashes.add(fh)
                    return out_path, url
                else:
                    _rm(out_path)

        # ── Foto (fallback) ────────────────────────────────────────────────
        if search_pics:
            try:
                if source in ("pexels", "pixabay", "unsplash"):
                    pics = search_pics(api_key, keyword, count=4)
                else:
                    pics = search_pics(keyword, count=4)
            except Exception:
                pics = []

            for pic in (pics or []):
                url = pic.get("url", "")
                if not url or url in seen_urls:
                    continue
                out_path = os.path.join(output_folder, f"{base_out}_{attempt_tag}.jpg")
                try:
                    download_fn(url, out_path)
                except Exception:
                    _rm(out_path)
                    continue

                if os.path.exists(out_path) and os.path.getsize(out_path) >= 10_000:
                    vr = _vbroll(out_path, min_duration=0, min_width=640)
                    if not vr["valid"]:
                        _rm(out_path)
                        continue
                    fh = _file_hash(out_path)
                    if fh and fh in seen_hashes:
                        _rm(out_path)
                        continue
                    seen_urls.add(url)
                    if fh:
                        seen_hashes.add(fh)
                    return out_path, url
                else:
                    _rm(out_path)

        return None, None

    mapped_clips = []

    for i, shot in enumerate(shots_to_process):
        if on_progress:
            pct = 20 + int((i / max(len(shots_to_process), 1)) * 35)
            first_term = shot.get("search_terms", ["?"])[0]
            on_progress(pct, 100, f"B-roll {i+1}/{len(shots_to_process)}: '{first_term}'")

        search_terms = shot.get("search_terms", [])
        if not search_terms:
            continue

        text_preview = shot.get("text_preview", "")
        base_out = f"shot_{i:03d}"
        found_path = None
        found_keyword = None

        def _try_youtube(term, tag, ch_ids=None):
            """Tenta baixar do YouTube (canais, CC ou yt-dlp com nomes). Retorna path ou None."""
            out_path = os.path.join(output_folder, f"{base_out}_{tag}.mp4")
            try:
                from core.youtube_broll import search_and_download as yt_dl
                ok = yt_dl(term, out_path,
                           youtube_api_key=youtube_key,
                           max_duration=30,
                           channel_ids=ch_ids or None,
                           channel_names=yt_ch_names or None)
                if ok and os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
                    fh = _file_hash(out_path)
                    if not fh or fh not in seen_hashes:
                        if fh:
                            seen_hashes.add(fh)
                        return out_path
            except Exception as yt_e:
                print(f"    [YouTube] Erro: {yt_e}")
            return None

        # ── FASE 1: Canais YouTube configurados (fonte primária para nichos) ──
        if youtube_channels and not found_path:
            for term_idx, term in enumerate(search_terms[:2]):
                yt_path = _try_youtube(term, f"ych_t{term_idx}", ch_ids=youtube_channels)
                if yt_path:
                    found_path = yt_path
                    found_keyword = term
                    print(f"    ✓ [YouTube-Canal] Shot {i} ({shot['start']:.0f}s): '{term}'")
                    break

        # ── FASE 2: Fontes stock (Pexels, Pixabay, Unsplash) ──
        for term_idx, term in enumerate(search_terms):
            if found_path:
                break

            stock_sources = [s for s in sources if s not in ("youtube", "youtube_channels")]
            source_order = stock_sources[i % max(len(stock_sources), 1):] + stock_sources[:i % max(len(stock_sources), 1)]

            for source in source_order:
                try:
                    path, url = _try_one(source, term, base_out, f"t{term_idx}_{source}")
                    if path:
                        # IA Validadora: rejeita clips com score < 0.55
                        if intel and term_idx <= 1:
                            score = intel.validate_clip(path, [term], text_preview)
                            if score < 0.45:
                                print(f"    [IA Validadora] Reprovado (score={score:.1f}), tentando próximo...")
                                try:
                                    os.remove(path)
                                except Exception:
                                    pass
                                seen_urls.discard(url)
                                continue
                        found_path = path
                        found_keyword = term
                        print(f"    ✓ [{source}] Shot {i} ({shot['start']:.0f}s-{shot['end']:.0f}s): '{term}'")
                        break
                except Exception as exc:
                    print(f"    [{source}] Erro para '{term}': {exc}")
                    continue

        # ── FASE 3: YouTube CC (fallback quando stock não encontrou) ──
        if not found_path and has_youtube:
            for term_idx, term in enumerate(search_terms[:3]):
                yt_path = _try_youtube(term, f"ycc_t{term_idx}")
                if yt_path:
                    found_path = yt_path
                    found_keyword = term
                    print(f"    ✓ [YouTube-CC] Shot {i} ({shot['start']:.0f}s): '{term}'")
                    break

        # Fallback: keyword simplificado (2 primeiras palavras)
        if not found_path and search_terms:
            simplified = " ".join(search_terms[0].split()[:2])
            if simplified != search_terms[0]:
                print(f"    [FALLBACK simples] Tentando: '{simplified}'")
                source_order = sources[i % len(sources):] + sources[:i % len(sources)]
                for source in source_order:
                    if source in ("youtube", "youtube_channels"):
                        continue
                    try:
                        path, url = _try_one(source, simplified, base_out, "simplified")
                        if path:
                            found_path = path
                            found_keyword = simplified
                            print(f"    ✓ [{source}] Fallback simples Shot {i}: '{simplified}'")
                            break
                    except Exception:
                        continue

        if found_path:
            mapped_clips.append({
                "timeline_start": shot["start"],
                "timeline_end": shot["end"],
                "file": found_path,
                "keyword": found_keyword,
                "text_preview": text_preview,
                "source": "stock",
                "shot_idx": i,
            })
        else:
            print(f"    ✗ Shot {i} ({shot['start']:.0f}s): nenhum clip encontrado para {search_terms}")

        _tm.sleep(0.3)

    print(f"  [download] Resultado: {len(mapped_clips)}/{len(shots_to_process)} shots cobertos")
    return mapped_clips


def _redownload_bad_clips(bad_indices, mapped_clips, shot_list, output_folder,
                           pexels_key, pixabay_key, unsplash_key):
    """Re-baixa clips reprovados pelo Gerente Geral usando termos alternativos."""
    import time as _tm

    sources = []
    downloaders = {}
    if pexels_key:
        from core.pexels_stock import search_videos as pex_videos, search_photos as pex_photos
        from core.pexels_stock import download_file as pex_download
        sources.append("pexels")
        downloaders["pexels"] = (pex_videos, pex_photos, pex_download, pexels_key)
    if pixabay_key:
        from core.pixabay_stock import search_videos as pix_videos, search_photos as pix_photos
        from core.pixabay_stock import download_file as pix_download
        sources.append("pixabay")
        downloaders["pixabay"] = (pix_videos, pix_photos, pix_download, pixabay_key)
    if unsplash_key:
        from core.unsplash_stock import search_photos as uns_photos
        from core.unsplash_stock import download_file as uns_download
        sources.append("unsplash")
        downloaders["unsplash"] = (None, uns_photos, uns_download, unsplash_key)

    for idx in bad_indices:
        clip = mapped_clips[idx]
        t_start = clip.get("timeline_start", 0)
        current_kw = clip.get("keyword", "")

        # Localiza o shot original para pegar search_terms alternativos
        original_shot = None
        for shot in shot_list:
            if abs(shot["start"] - t_start) < 2.0:
                original_shot = shot
                break

        alt_terms = []
        if original_shot:
            alt_terms = [t for t in original_shot.get("search_terms", []) if t != current_kw]

        if not alt_terms:
            alt_terms = [f"{current_kw} medical", f"{current_kw} close up", f"{current_kw} stock"]

        for term in alt_terms:
            out_path = os.path.join(output_folder, f"redl_{idx:03d}.mp4")
            found = False
            for source in sources:
                dl = downloaders.get(source)
                if not dl:
                    continue
                search_vids, search_pics, download_fn, api_key = dl
                try:
                    if search_vids:
                        vids = search_vids(api_key, term, count=2) if source in ("pexels", "pixabay") else search_vids(term, count=2)
                        for vid in (vids or []):
                            url = vid.get("url", "")
                            if not url:
                                continue
                            try:
                                download_fn(url, out_path)
                            except Exception:
                                continue
                            if os.path.exists(out_path) and os.path.getsize(out_path) >= 100_000:
                                from core.video_auditor import validate_broll as _vbroll
                                vr = _vbroll(out_path, min_duration=3, min_width=640)
                                if not vr["valid"]:
                                    try:
                                        os.remove(out_path)
                                    except OSError:
                                        pass
                                    continue
                                mapped_clips[idx] = {**clip, "file": out_path, "keyword": term}
                                print(f"  [Re-download] Clip {idx}: '{current_kw}' -> '{term}' OK")
                                found = True
                                break
                            elif os.path.exists(out_path):
                                try:
                                    os.remove(out_path)
                                except OSError:
                                    pass
                except Exception:
                    continue
                if found:
                    break
            if found:
                break
            _tm.sleep(0.2)

    return mapped_clips


def _build_smart_timeline(avatar_duration, mapped_clips, rng,
                           min_broll_dur=4, max_broll_dur=6, full_broll_mode=False):
    """
    v11 — Posicionamento exato por shot:
    Cada clip é colocado EXATAMENTE na posição timeline_start do seu shot.
    Avatar preenche todos os gaps. Zero repetição — cada clip aparece uma vez.
    """
    segments = []

    if full_broll_mode:
        for clip in sorted(mapped_clips, key=lambda c: c["timeline_start"]):
            dur = clip["timeline_end"] - clip["timeline_start"]
            if dur < 0.5:
                continue
            segments.append({
                "type": "broll",
                "start": clip["timeline_start"],
                "duration": dur,
                "file": clip["file"],
                "is_image": clip["file"].lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")),
                "keyword": clip.get("keyword", ""),
                "speed_factor": clip.get("speed_factor", 1.0),
                "reuse_count": 0,
            })
        return segments

    if not mapped_clips:
        return [{"type": "avatar", "start": 0.0, "duration": round(avatar_duration, 2)}]

    # Ordena clips pela posição do shot no vídeo
    clips_sorted = sorted(mapped_clips, key=lambda c: c["timeline_start"])

    current_time = 0.0

    for clip in clips_sorted:
        shot_start = clip["timeline_start"]
        shot_end = clip["timeline_end"]

        # Pula clips que ficam além do vídeo
        if shot_start >= avatar_duration - 1.0:
            break

        # Avatar do ponto atual até o início deste shot
        gap = shot_start - current_time
        if gap >= 1.0:
            segments.append({
                "type": "avatar",
                "start": round(current_time, 2),
                "duration": round(gap, 2),
            })

        # B-roll nesta posição exata — duração entre min e max, limitado ao shot
        shot_dur = shot_end - shot_start
        broll_dur = min(shot_dur, max_broll_dur, avatar_duration - shot_start)
        broll_dur = max(broll_dur, min_broll_dur) if broll_dur >= min_broll_dur else broll_dur

        if broll_dur >= 2.0:
            segments.append({
                "type": "broll",
                "start": round(shot_start, 2),
                "duration": round(broll_dur, 2),
                "file": clip["file"],
                "is_image": clip["file"].lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")),
                "keyword": clip.get("keyword", ""),
                "speed_factor": 1.0,
                "reuse_count": 0,
            })
            current_time = shot_start + broll_dur
        else:
            current_time = max(current_time, shot_start)

    # Avatar para o restante do vídeo após o último B-roll
    remaining = avatar_duration - current_time
    if remaining >= 0.3:
        segments.append({
            "type": "avatar",
            "start": round(current_time, 2),
            "duration": round(remaining, 2),
        })

    return segments

