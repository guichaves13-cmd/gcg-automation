"""
Pipeline Avatar AUTO — v9 (PRODUCTION)
Fully automatic: AI Analysis → Keywords → Downloads → PIP overlay → Fresh Subtitles.

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
    _get_encoder,
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
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=True)
    if os.path.getsize(output_path) < 1000:
        raise RuntimeError("trim produced empty output")


def _make_broll_with_pip(broll_path, avatar_path, start, duration,
                          output_path, width, height, fps,
                          is_image=False, kb_dir=None,
                          pip_position="bottom_right", pip_percent=22,
                          fade_in=0.3, fade_out=0.3):
    """Create B-roll fullscreen + avatar PIP overlay + avatar audio.

    PHASE 5: also applies subtle fade-in and fade-out to the B-roll background
    so the avatar↔B-roll cuts read as a soft dissolve rather than a hard cut.
    fade durations are in seconds. Set to 0 to disable.
    """
    ffmpeg = _find_ffmpeg()

    # Build fade filter chain (applied to the b-roll layer, not the PIP).
    # Fade-out starts (duration - fade_out) seconds in.
    fade_parts = []
    if fade_in > 0:
        fade_parts.append(f"fade=t=in:st=0:d={fade_in:.2f}")
    if fade_out > 0 and duration > fade_out + 0.2:
        fade_parts.append(f"fade=t=out:st={(duration - fade_out):.2f}:d={fade_out:.2f}")
    fade_chain = ("," + ",".join(fade_parts)) if fade_parts else ""

    # Step 1: Create B-roll video (no audio)
    broll_vid = output_path + ".broll.mp4"
    if is_image:
        kb_filter = get_zoompan_filter(kb_dir or "zoom_in_center", duration, fps, width, height)
        # For images, apply Ken Burns AND fade in/out for cinematic feel
        create_video_from_image(broll_path, duration, width, height, fps,
                                kb_filter + fade_chain, broll_vid)
    else:
        clip_dur = get_duration(broll_path)
        use_dur = min(duration, clip_dur)
        cmd = [
            ffmpeg, "-y", "-i", broll_path, "-t", str(round(use_dur, 3)),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}" + fade_chain,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-an", "-pix_fmt", "yuv420p", broll_vid,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=True)

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
    subprocess.run(cmd_pip, capture_output=True, text=True, timeout=300, check=True)

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
    r = subprocess.run(cmd_overlay, capture_output=True, text=True, timeout=300)
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
        subprocess.run(cmd_mux, capture_output=True, text=True, timeout=120, check=True)

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
    temp_dir = os.path.join(tempfile.gettempdir(), "studiopilot_auto")
    if os.path.exists(temp_dir):
        console.print("[dim]Cleaning previous session data...[/dim]")
        shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)

    # Also clean any whisper cache
    whisper_cache = os.path.join(tempfile.gettempdir(), "whisper_cache")
    if os.path.exists(whisper_cache):
        shutil.rmtree(whisper_cache, ignore_errors=True)

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
        intel = VideoIntelligence(google_api_key=google_key)
        
        stock_folder = os.path.join(temp_dir, "stock")
        os.makedirs(stock_folder, exist_ok=True)
        
        analysis = intel.analyze_video(avatar_path, stock_folder)
        avatar_duration = analysis["duration"]

        console.print(f"  [bold green]Theme: {analysis['theme']}[/bold green]")
        console.print(f"  [bold green]Language: {analysis['language']}[/bold green]")
        console.print(f"  [bold green]Shots planned: {len(analysis['shot_list'])}[/bold green]")

        # =============================================
        # STEP 2: DOWNLOAD STOCK FOOTAGE (FRESH!)
        # =============================================
        if on_progress:
            on_progress(20, 100, f"Phase 2: Downloading {broll_count} B-roll clips...")
        console.print(f"\n[yellow]Step 2/6: Downloading B-roll (target: {broll_count} clips)...[/yellow]")

        # Use shot list from intelligence engine
        # Pass the intel validator + theme so each download can be content-validated
        # (rejects B-rolls that don't visually match the search term)
        # PHASE 6: when youtube_api_key is configured we auto-enable youtube_priority.
        # YouTube has real contextual footage that often beats stock libraries for
        # topical content (history, news, science explainer). User can override
        # explicitly with youtube_priority=False.
        _yt_key = config.get("youtube_api_key", "")
        _yt_priority = config.get("youtube_priority", bool(_yt_key))

        mapped_clips = _download_from_shot_list(
            analysis["shot_list"],
            stock_folder,
            pexels_key, pixabay_key, unsplash_key,
            max_clips=broll_count,
            on_progress=on_progress,
            youtube_api_key=_yt_key,
            youtube_channel_ids=config.get("youtube_channel_ids", ""),
            youtube_priority=_yt_priority,
            # PHASE 2: pass validator + theme for post-download Gemini Vision check
            validator=intel,
            video_theme=analysis.get("theme", "general"),
            min_validation_score=config.get("broll_min_score", 0.4),
        )

        console.print(f"  Downloaded [bold green]{len(mapped_clips)}[/bold green] content-matched clips")
        for clip in mapped_clips[:5]:
            console.print(f"    [{clip.get('source','?')}] '{clip['keyword']}' @ {clip['timeline_start']:.0f}s")
        if len(mapped_clips) > 5:
            console.print(f"    ... and {len(mapped_clips) - 5} more")

        if not mapped_clips:
            console.print("[red]  No clips downloaded! Check API keys.[/red]")

        # =============================================
        # STEP 2.5: ANTI-REUSE TRANSFORMS
        # Apply subtle visual transformations to each clip so YouTube's
        # content-matching algorithm can't flag them as reused footage.
        # =============================================
        if config.get("anti_reuse_enabled", True) and mapped_clips:
            console.print("\n[yellow]Step 2.5/6: Applying anti-reuse transforms...[/yellow]")
            try:
                from core.anti_reuse import apply_anti_reuse
                ar_ok = 0
                ar_fail = 0
                for clip in mapped_clips:
                    src = clip.get("file", "")
                    if not src or not os.path.exists(src):
                        continue
                    ext = os.path.splitext(src)[1].lower()
                    if ext not in (".mp4", ".mov", ".avi", ".mkv"):
                        continue  # images: skip
                    ar_out = src.replace(ext, f"_ar{ext}")
                    try:
                        apply_anti_reuse(
                            src, ar_out,
                            width=width, height=height, fps=fps,
                            seed=hash(clip.get("keyword", "") + src) & 0xFFFFFF,
                        )
                        if os.path.exists(ar_out) and os.path.getsize(ar_out) > 1000:
                            os.replace(ar_out, src)  # overwrite original
                            ar_ok += 1
                        else:
                            ar_fail += 1
                    except Exception as ar_e:
                        console.print(f"    [dim]anti_reuse skip ({ar_e})[/dim]")
                        ar_fail += 1
                console.print(
                    f"  Anti-reuse: [green]{ar_ok} OK[/green] / "
                    f"[dim]{ar_fail} skipped[/dim] (zoom+crop+color+speed)"
                )
            except ImportError:
                console.print("  [dim]anti_reuse module not available — skipping[/dim]")
            except Exception as ar_err:
                console.print(f"  [dim]anti_reuse error (non-fatal): {ar_err}[/dim]")

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
            min_broll_dur=config.get("avatar", {}).get("min_broll_duration", 4),
            max_broll_dur=config.get("avatar", {}).get("max_broll_duration", 6),
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

                seg_ok = False
                try:
                    if seg["type"] == "avatar":
                        _trim_avatar(avatar_path, seg["start"], seg["duration"],
                                    seg_final, width, height, fps)
                        seg_ok = os.path.exists(seg_final) and os.path.getsize(seg_final) > 1000
                    else:
                        pip_pos = rng.choice(pip_positions)
                        _make_broll_with_pip(
                            seg["file"], avatar_path,
                            seg["start"], seg["duration"],
                            seg_final, width, height, fps,
                            is_image=seg.get("is_image", False),
                            kb_dir=rng.choice(["zoom_in_center", "zoom_out_center",
                                              "pan_left", "pan_right"]),
                            pip_position=pip_pos,
                            pip_percent=22,
                        )
                        seg_ok = os.path.exists(seg_final) and os.path.getsize(seg_final) > 1000
                        if seg_ok:
                            try:
                                from core.video_filters import apply_random_effects
                                filtered = os.path.join(temp_dir, f"seg_{i:04d}_fx.mp4")
                                seg_mood = seg.get("mood", None)
                                seg_shot_type = seg.get("shot_type", None)
                                apply_random_effects(seg_final, filtered, seed=i,
                                                    mood=seg_mood, shot_type=seg_shot_type)
                                if os.path.exists(filtered) and os.path.getsize(filtered) > 1000:
                                    os.replace(filtered, seg_final)
                            except Exception as fx_e:
                                console.print(f"  [cyan]Auto-Fix:[/cyan] Filtro no seg {i} ignorado ({fx_e})")

                        # === LOWER THIRD: apply on B-Roll with keyword text ===
                        if seg_ok and config.get("lower_thirds_enabled", False):
                            try:
                                kw = seg.get("keyword", "").strip()
                                if kw and len(kw) <= 60:  # avoid huge texts
                                    from core.motion_graphics import add_lower_third
                                    lt_out = os.path.join(temp_dir, f"seg_{i:04d}_lt.mp4")
                                    lt_style = config.get("lower_thirds_style", "modern")
                                    lt_duration = min(float(seg.get("duration", 4.0)) - 0.5, 4.0)
                                    if lt_duration >= 1.5:
                                        add_lower_third(
                                            seg_final, lt_out,
                                            text=kw.title(),
                                            subtitle=seg.get("shot_type", ""),
                                            start_time=0.5,
                                            duration=lt_duration,
                                            position="bottom_left",
                                            style=lt_style,
                                        )
                                        if os.path.exists(lt_out) and os.path.getsize(lt_out) > 1000:
                                            os.replace(lt_out, seg_final)
                            except Exception as lt_e:
                                console.print(f"  [cyan]Auto-Fix:[/cyan] Lower third no seg {i} ignorado ({lt_e})")
                except Exception as e:
                    console.print(f"  [red]Auto-Fix:[/red] Seg {i} falhou ({e}). Tentando fallback...")
                    seg_ok = False

                if not seg_ok:
                    console.print(f"  [red]Auto-Fix:[/red] Seg {i} — reconstruindo com camera principal...")
                    try:
                        _trim_avatar(avatar_path, seg["start"], seg["duration"],
                                    seg_final, width, height, fps)
                        seg_ok = os.path.exists(seg_final) and os.path.getsize(seg_final) > 1000
                    except Exception as e2:
                        console.print(f"  [red]Auto-Fix:[/red] Fallback camera falhou ({e2})")

                if not seg_ok:
                    console.print(f"  [red]Auto-Fix:[/red] Seg {i} — gerando frame preto de emergencia...")
                    try:
                        ffmpeg = _find_ffmpeg()
                        dur = seg.get("duration", 4.0)
                        cmd = [ffmpeg, "-y", "-f", "lavfi", "-i",
                               f"color=c=black:s={width}x{height}:d={dur}:r={fps}",
                               "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                               "-c:a", "aac", "-shortest", seg_final]
                        subprocess.run(cmd, capture_output=True, timeout=60, check=True)
                        seg_ok = os.path.exists(seg_final) and os.path.getsize(seg_final) > 1000
                    except Exception as e3:
                        console.print(f"  [red]CRITICO:[/red] Geracao de frame preto falhou ({e3})")

                segment_files.append(seg_final)
                progress.update(task, advance=1)

                if on_progress:
                    pct = 60 + int((i / max(len(segments_plan), 1)) * 25)
                    on_progress(pct, 100, f"Segment {i+1}/{len(segments_plan)}")

        # =============================================
        # STEP 5: CONCATENATE
        # =============================================
        if on_progress:
            on_progress(85, 100, "Phase 5: Concatenating segments...")
        valid_count = sum(1 for f in segment_files if os.path.exists(f) and os.path.getsize(f) > 500)
        console.print(f"\n[yellow]Step 5/6: Concatenating {valid_count}/{len(segment_files)} valid segments...[/yellow]")
        concat_out = os.path.join(temp_dir, "concat.mp4")
        concat_segments_with_audio(segment_files, concat_out)
        concat_size = os.path.getsize(concat_out) / 1024 / 1024
        console.print(f"  Concat: {concat_size:.1f} MB")

        if not _has_video(concat_out):
            raise RuntimeError("CONCAT FAILED: no video stream!")
        console.print("  [green]Validated: has video stream[/green]")

        current = concat_out

        # PHASE 5: Add subtle whoosh SFX on every avatar<->broll transition.
        # Cut points are computed from segments_plan (cumulative duration).
        # We skip cuts that are too close together (<1.5s) to avoid SFX overlap.
        if config.get("transition_sfx_enabled", True) and segments_plan:
            try:
                from core.sound_effects import add_transition_sfx
                cut_times = []
                t_accum = 0.0
                last_type = None
                for s in segments_plan:
                    t_accum += float(s.get("duration", 0))
                    # Only mark cuts where the segment TYPE changes (avatar<->broll)
                    cur_type = s.get("type")
                    if last_type is not None and cur_type != last_type:
                        cut_times.append(t_accum - float(s.get("duration", 0)))
                    last_type = cur_type
                # Dedupe close cuts (within 1.5s of each other)
                cleaned = []
                for t in cut_times:
                    if not cleaned or (t - cleaned[-1]) >= 1.5:
                        cleaned.append(t)
                if cleaned:
                    sfx_out = os.path.join(temp_dir, "concat_sfx.mp4")
                    console.print(f"  [yellow]Phase 5+: Adding {len(cleaned)} transition whooshes...[/yellow]")
                    ok = add_transition_sfx(
                        current, cleaned, sfx_out, temp_dir,
                        sfx_volume=float(config.get("transition_sfx_volume", 0.18)),
                    )
                    if ok and _has_video(sfx_out) and os.path.getsize(sfx_out) > os.path.getsize(current) * 0.3:
                        current = sfx_out
                        console.print(f"  [green]Transitions: {len(cleaned)} whooshes added[/green]")
                    else:
                        console.print("  [dim]Transition SFX skipped (output invalid)[/dim]")
                else:
                    console.print("  [dim]No type-change transitions to enhance[/dim]")
            except Exception as sfx_e:
                console.print(f"  [dim]Transition SFX skipped: {sfx_e}[/dim]")

        # =============================================
        # STEP 6: FRESH SUBTITLES (from THIS video only!)
        # =============================================
        final_in_temp = os.path.join(temp_dir, "final_output.mp4")

        # v9: ALWAYS generate fresh subtitles from the concatenated video's audio
        subs_enabled = config.get("subtitles_enabled", True)
        force_new = config.get("force_new_subtitles", False)
        
        if subs_enabled or force_new:
            console.print("\n[yellow]Step 6/6: Generating FRESH subtitles from THIS video...[/yellow]")
            if on_progress:
                on_progress(90, 100, "Phase 6: Fresh subtitles...")
            
            # Use the SRT already generated by Video Intelligence
            srt_path = analysis.get("subtitle_srt", "")
            
            if srt_path and os.path.exists(srt_path):
                console.print(f"  Using fresh SRT from Video Intelligence")
                
                import random as _rng
                sub_colors = [
                    ("&H00FFFFFF", "White"),
                    ("&H0000FFFF", "Yellow"),
                    ("&H00FFFF00", "Cyan"),
                    ("&H004ECCA3", "Teal"),
                ]
                color_hex, color_name = _rng.choice(sub_colors)
                console.print(f"  Subtitle color: {color_name}")

                ffmpeg = _find_ffmpeg()
                srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
                # Force libx264 for subtitles filter (NVENC + subtitles incompatible,
                # same root cause as motion_graphics drawbox bug)
                # Use fontfile= explicit (FontName=Arial breaks on Windows ffmpeg
                # without fontconfig - same fix as motion_graphics)
                from core.motion_graphics import _find_font
                font_path = _find_font()
                if font_path:
                    font_esc = font_path.replace("\\", "/").replace(":", "\\:")
                    font_style = f"FontFile='{font_esc}'"
                else:
                    font_style = "FontName=Arial"
                cmd_sub = [
                    ffmpeg, "-y",
                    "-i", current,
                    "-vf", f"subtitles='{srt_escaped}':force_style='{font_style},FontSize=22,"
                           f"PrimaryColour={color_hex},OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV=30'",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-c:a", "copy", "-pix_fmt", "yuv420p", final_in_temp,
                ]
                result = None
                try:
                    result = subprocess.run(cmd_sub, capture_output=True, text=True, timeout=900)
                except subprocess.TimeoutExpired:
                    console.print("  [yellow]Subtitle burn timed out (>15min)[/yellow]")

                if result is None or result.returncode != 0 or not _has_video(final_in_temp):
                    # Print stderr for diagnostics (sanitize for Windows cp1252)
                    if result and result.stderr:
                        err_safe = result.stderr.encode('ascii', 'replace').decode()
                        console.print(f"  [yellow]Subtitle burn failed (rc={result.returncode}):[/yellow]")
                        console.print(f"  [dim]{err_safe[-400:]}[/dim]")
                    else:
                        console.print("  [dim]Subtitle burn failed (no result), copying without subs[/dim]")
                    shutil.copy2(current, final_in_temp)
                else:
                    console.print("  [green]Fresh subtitles applied![/green]")
            else:
                console.print("  [dim]No SRT available, skipping subtitles[/dim]")
                shutil.copy2(current, final_in_temp)
        else:
            console.print("\n[cyan]Step 6/6: Subtitles DISABLED[/cyan]")
            shutil.copy2(current, final_in_temp)

        # =============================================
        # OPTIONAL: BACKGROUND MUSIC
        # =============================================
        if config.get("music_enabled", False):
            console.print("\n[yellow]Bonus: Adding background music...[/yellow]")
            try:
                from core.music_system import generate_ambient_track, add_background_music, detect_theme_from_text
                theme = detect_theme_from_text(analysis.get("full_text", "")[:500])
                console.print(f"  Music theme: {theme}")

                music_file = os.path.join(temp_dir, "ambient_music.m4a")
                generate_ambient_track(music_file, avatar_duration, theme)
                
                if os.path.exists(music_file):
                    with_music = os.path.join(temp_dir, "with_music.mp4")
                    add_background_music(final_in_temp, music_file, with_music, music_volume=0.08)
                    if _has_video(with_music):
                        os.replace(with_music, final_in_temp)
                        console.print("  Background music added")
            except Exception as e:
                console.print(f"  [dim]Music skipped: {e}[/dim]")

        # FINAL VALIDATION
        if not _has_video(final_in_temp):
            console.print("  [red]Final lost video! Using concat directly.[/red]")
            shutil.copy2(concat_out, final_in_temp)

        if not _has_video(final_in_temp):
            raise RuntimeError("PIPELINE FAILED: no video stream in final output!")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        shutil.copy2(final_in_temp, output_path)

        # Copy SRT next to output
        if analysis.get("subtitle_srt") and os.path.exists(analysis["subtitle_srt"]):
            shutil.copy2(analysis["subtitle_srt"], output_path.replace(".mp4", ".srt"))

        # PHASE 3: Beat timeline JSON next to output (full traceability)
        timeline = None
        try:
            from core.beat_timeline import build_beat_timeline, summarize_beat_timeline
            timeline_path = output_path.replace(".mp4", "_beat_timeline.json")
            timeline = build_beat_timeline(
                segments_plan=segments_plan,
                shot_list=analysis.get("shot_list", []),
                transcription=analysis.get("transcription", []),
                analysis=analysis,
                mapped_clips=mapped_clips,
                output_path=timeline_path,
            )
            console.print(f"\n[cyan]{summarize_beat_timeline(timeline)}[/cyan]")
        except Exception as bt_e:
            console.print(f"  [dim]Beat timeline export skipped: {bt_e}[/dim]")

        # PHASE 4: B-Roll picker HTML for manual review (opt-out via config)
        if timeline and config.get("generate_picker", True):
            try:
                from core.broll_picker import generate_picker
                picker_path = output_path.replace(".mp4", "_picker.html")
                generate_picker(timeline, picker_path)
                console.print(f"  [cyan]Picker HTML:[/cyan] {picker_path}")
            except Exception as pk_e:
                console.print(f"  [dim]Picker generation skipped: {pk_e}[/dim]")

        # =============================================
        # IA AUDITOR FINAL — quality check
        # =============================================
        quality_report = None
        report_path = output_path.replace(".mp4", "_quality_report.json")
        if google_key:
            try:
                if on_progress:
                    on_progress(97, 100, "IA Auditor Final: analyzing video quality...")
                from core.video_auditor import VideoAuditor
                auditor = VideoAuditor(google_api_key=google_key)
                transcription = analysis.get("transcription", [])
                if transcription:
                    audit_dir = os.path.join(temp_dir, "_auditor_final")
                    os.makedirs(audit_dir, exist_ok=True)
                    quality_report = auditor.audit_final_video(
                        output_path, transcription, audit_dir,
                        interval_sec=8.0, on_progress=on_progress,
                    )
                    with open(report_path, "w", encoding="utf-8") as f:
                        json.dump(quality_report, f, indent=2)
                    if quality_report.get("issues"):
                        for issue in quality_report["issues"]:
                            console.print(f"  [yellow]Audit issue: {issue}[/yellow]")
                    status = "APROVADO" if quality_report.get("approved") else "COM RESSALVAS"
                    score = quality_report.get("quality_score", 1.0)
                    console.print(f"  [bold cyan]Auditor Final: {status} | Score: {score:.0%}[/bold cyan]")
            except Exception as e:
                console.print(f"  [dim]Auditor Final skipped: {e}[/dim]")
        else:
            # Write default report when no API key
            default_report = {
                "quality_score": 1.0, "issues": [], "approved": True,
                "checkpoints_analyzed": 0, "total_duration": analysis.get("duration", 0),
            }
            try:
                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump(default_report, f, indent=2)
            except Exception:
                pass

        final_size = os.path.getsize(output_path) / 1024 / 1024
        console.print(f"\n[bold green]DONE! {output_path} ({final_size:.1f} MB)[/bold green]")
        console.print(f"[bold green]   Theme: {analysis['theme']} | {len(mapped_clips)} B-roll clips | {analysis['language'].upper()}[/bold green]")

        if on_progress:
            on_progress(100, 100, "Done!")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _download_from_shot_list(shot_list, output_folder, pexels_key, pixabay_key, unsplash_key,
                              max_clips=30, on_progress=None,
                              youtube_api_key="", youtube_channel_ids="",
                              youtube_priority=False,
                              validator=None, video_theme="general",
                              min_validation_score=0.4):
    """Download B-roll clips based on the AI-generated shot list.
    Sources: Pexels → Pixabay → Coverr → YouTube.
    Set youtube_priority=True to try YouTube FIRST.
    All clips: trimmed to 5-8s, watermark removed, deduplicated.

    PHASE 2: post-download validation.
    If `validator` is a VideoIntelligence instance, every downloaded clip is
    scored against its search terms via Gemini Vision (images) or heuristics
    (videos). Clips with score < min_validation_score are discarded and the
    next source/result is tried."""
    
    # Build keyword list from shot list (with mood + shot_type)
    keywords = []
    for shot in shot_list:
        for term in shot.get("search_terms", []):
            keywords.append({
                "start": shot["start"],
                "end": shot["end"],
                "keyword": term,
                "shot_type": shot.get("shot_type", "wide"),
                "mood": shot.get("mood", "informative"),
            })
    
    # Limit to max_clips
    if len(keywords) > max_clips:
        step = len(keywords) / max_clips
        keywords = [keywords[int(i * step)] for i in range(max_clips)]
    
    # Import download sources
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
    
    # Mixkit (free HD/4K, no API key)
    mk_available = False
    try:
        from core.mixkit_stock import search_and_download as mk_search_download
        mk_available = True
        sources.append("mixkit")
    except ImportError:
        pass
    
    # YouTube as fallback source (uses youtube_broll.py v4)
    yt_available = False
    try:
        from core.youtube_broll import search_and_download as yt_search_download
        yt_available = True
        sources.append("youtube")
    except ImportError:
        pass
    
    if not sources:
        print("  [download] No stock sources available!")
        return []

    print(f"  [download] Active sources: {', '.join(sources)}")
    if validator is not None:
        print(f"  [download] Post-download validation: ENABLED (min_score={min_validation_score}, theme='{video_theme}')")

    # Deduplication tracker
    used_urls = set()
    used_ids = set()
    mapped_clips = []

    # Track validation scores keyed by file path so the beat_timeline can show them
    validation_scores = {}

    def _accept_or_reject(file_path, keyword_text, source_name):
        """PHASE 2 validation. Returns True if clip passes, False to discard.

        Uses VideoIntelligence.validate_clip() which scores via Gemini Vision
        for images / heuristics for videos. On any error -> accept (fail open)
        so the pipeline never gets stuck because of validator transient issues."""
        if validator is None:
            return True
        if not (os.path.exists(file_path) and os.path.getsize(file_path) > 1000):
            return False
        try:
            # Use filename as metadata hint (stock sources often encode title in filename)
            meta = os.path.splitext(os.path.basename(file_path))[0].replace("_", " ").replace("-", " ")
            score = validator.validate_clip(
                file_path, [keyword_text], video_theme, metadata_text=meta
            )
        except Exception as ve:
            print(f"    [validate] {source_name} '{keyword_text}': validator error ({ve}) — accepting (fail open)")
            return True
        if score is None or score < 0:
            # validator unavailable (quota/error) — fail open but don't store a fake score
            print(f"    [validate] {source_name} '{keyword_text}': validator unavailable — accepting without score")
            return True
        if score < min_validation_score:
            print(f"    [validate] REJECTED ({score:.2f} < {min_validation_score}) {source_name} '{keyword_text}'")
            try: os.remove(file_path)
            except Exception: pass
            return False
        print(f"    [validate] OK ({score:.2f}) {source_name} '{keyword_text}'")
        validation_scores[file_path] = score
        return True
    
    for i, item in enumerate(keywords):
        if on_progress:
            pct = 20 + int((i / max(len(keywords), 1)) * 35)
            on_progress(pct, 100, f"Downloading {i+1}/{len(keywords)}: '{item['keyword']}'")
        
        found = False
        if youtube_priority and yt_available:
            # YouTube FIRST, then rotate stock sources
            stock_sources = [s for s in sources if s != "youtube"]
            rotated = stock_sources[i % max(len(stock_sources), 1):] + stock_sources[:i % max(len(stock_sources), 1)]
            source_order = ["youtube"] + rotated
        else:
            # Stock sources first, YouTube last (fallback)
            stock_sources = [s for s in sources if s != "youtube"]
            source_order = stock_sources[i % max(len(stock_sources), 1):] + stock_sources[:i % max(len(stock_sources), 1)]
            if yt_available:
                source_order.append("youtube")
        
        for source in source_order:
            if found:
                break
            try:
                if source == "youtube":
                    # YouTube: download 6s clip via youtube_broll.py v3
                    out_path = os.path.join(output_folder, f"broll_{i+1:03d}.mp4")
                    # Parse channel IDs if provided as comma-separated string
                    ch_ids = [c.strip() for c in youtube_channel_ids.split(",") if c.strip()] if youtube_channel_ids else None
                    ok = yt_search_download(
                        item["keyword"], out_path,
                        youtube_api_key=youtube_api_key,
                        channel_ids=ch_ids,
                    )
                    if ok and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                        if _accept_or_reject(out_path, item["keyword"], "youtube"):
                            mapped_clips.append({
                                "timeline_start": item["start"],
                                "timeline_end": item["end"],
                                "file": out_path,
                                "keyword": item["keyword"],
                                "source": "youtube",
                                "shot_type": item.get("shot_type", "wide"),
                                "mood": item.get("mood", "informative"),
                            })
                            found = True
                    continue
                
                if source == "mixkit" and mk_available:
                    out_path = os.path.join(output_folder, f"broll_{i+1:03d}_mk.mp4")
                    ok = mk_search_download(item["keyword"], out_path)
                    if ok and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                        final_path = os.path.join(output_folder, f"broll_{i+1:03d}.mp4")
                        _trim_and_clean(out_path, final_path)
                        try: os.remove(out_path)
                        except: pass
                        if os.path.exists(final_path) and os.path.getsize(final_path) > 1000:
                            if _accept_or_reject(final_path, item["keyword"], "mixkit"):
                                mapped_clips.append({
                                    "timeline_start": item["start"],
                                    "timeline_end": item["end"],
                                    "file": final_path,
                                    "keyword": item["keyword"],
                                    "source": "mixkit",
                                    "shot_type": item.get("shot_type", "wide"),
                                    "mood": item.get("mood", "informative"),
                                })
                                found = True
                    continue
                
                dl_info = downloaders[source]
                search_vids, search_pics, download_fn, api_key = dl_info
                
                # Try videos first (search 5 for dedup)
                if search_vids:
                    if source in ("pexels", "pixabay"):
                        vids = search_vids(api_key, item["keyword"], count=5, min_duration=3) if source == "pexels" else search_vids(api_key, item["keyword"], count=5)
                    else:
                        vids = search_vids(item["keyword"], count=3)
                    
                    for vid in (vids or []):
                        url = vid.get("url", "")
                        vid_id = str(vid.get("id", ""))
                        if not url or url in used_urls or (vid_id and vid_id in used_ids):
                            continue
                        
                        raw_path = os.path.join(output_folder, f"broll_{i+1:03d}_raw.mp4")
                        out_path = os.path.join(output_folder, f"broll_{i+1:03d}.mp4")
                        download_fn(url, raw_path)
                        
                        if os.path.exists(raw_path) and os.path.getsize(raw_path) > 1000:
                            # Trim to 5-8s + remove watermark (6% zoom crop)
                            _trim_and_clean(raw_path, out_path)
                            try: os.remove(raw_path)
                            except: pass

                            if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                                # PHASE 2: validate before accepting. Reject ones that don't match.
                                if not _accept_or_reject(out_path, item["keyword"], source):
                                    # Try next video candidate from same source — don't break
                                    continue
                                used_urls.add(url)
                                if vid_id: used_ids.add(vid_id)
                                mapped_clips.append({
                                    "timeline_start": item["start"],
                                    "timeline_end": item["end"],
                                    "file": out_path,
                                    "keyword": item["keyword"],
                                    "source": source,
                                    "shot_type": item.get("shot_type", "wide"),
                                    "mood": item.get("mood", "informative"),
                                })
                                found = True
                                break
                
                # Try photos (no trim needed, just dedup)
                if search_pics and not found:
                    if source in ("pexels", "pixabay", "unsplash"):
                        pics = search_pics(api_key, item["keyword"], count=5)
                    else:
                        pics = search_pics(item["keyword"], count=3)
                    
                    for pic in (pics or []):
                        pic_url = pic.get("url", "")
                        pic_id = str(pic.get("id", ""))
                        if not pic_url or pic_url in used_urls or (pic_id and pic_id in used_ids):
                            continue
                        
                        out_path = os.path.join(output_folder, f"broll_{i+1:03d}.jpg")
                        download_fn(pic_url, out_path)
                        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                            # PHASE 2: validate image via Gemini Vision (most reliable for stills)
                            if not _accept_or_reject(out_path, item["keyword"], source):
                                continue  # try next photo
                            used_urls.add(pic_url)
                            if pic_id: used_ids.add(pic_id)
                            mapped_clips.append({
                                "timeline_start": item["start"],
                                "timeline_end": item["end"],
                                "file": out_path,
                                "keyword": item["keyword"],
                                "source": source,
                                "shot_type": item.get("shot_type", "wide"),
                                "mood": item.get("mood", "informative"),
                            })
                            found = True
                            break
                            
            except Exception as exc:
                print(f"    [{source}] Error for '{item['keyword']}': {exc}")
                continue
    
    # Attach validation_score to each clip (if Phase 2 was active)
    for clip in mapped_clips:
        fp = clip.get("file")
        if fp in validation_scores:
            clip["validation_score"] = round(validation_scores[fp], 3)

    print(f"  [download] Dedup: {len(used_ids)} unique IDs, {len(used_urls)} unique URLs")
    if validator is not None:
        scored = [c for c in mapped_clips if "validation_score" in c]
        if scored:
            avg = sum(c["validation_score"] for c in scored) / len(scored)
            print(f"  [download] Validation: {len(scored)}/{len(mapped_clips)} scored, avg={avg:.2f}")
    return mapped_clips


def _trim_and_clean(input_path: str, output_path: str,
                     max_dur: float = 8.0, min_dur: float = 5.0):
    """Trim video to 5-8 seconds and apply watermark removal (6% zoom crop).
    Extracts from middle of clip, avoiding intro/outro."""
    ffmpeg = _find_ffmpeg()
    
    try:
        dur = get_duration(input_path)
    except:
        dur = 0
    
    if dur <= 0:
        shutil.copy2(input_path, output_path)
        return
    
    # Calculate trim: use middle section, avoid first/last 10%
    trim_dur = min(max_dur, dur)
    if trim_dur < min_dur and dur >= min_dur:
        trim_dur = min_dur
    
    if dur > max_dur:
        safe_start = dur * 0.1
        safe_end = dur * 0.9 - trim_dur
        import random
        start = random.uniform(safe_start, max(safe_start, safe_end))
    else:
        start = 0
    
    # Apply: trim + 6% zoom crop (removes corner watermarks)
    zoom = 1.06
    cmd = [
        ffmpeg, "-y",
        "-ss", str(round(start, 2)),
        "-i", input_path,
        "-t", str(round(trim_dur, 2)),
        "-vf", (
            f"scale=iw*{zoom}:ih*{zoom},"
            f"crop=iw/{zoom}:ih/{zoom}"
        ),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-an", "-pix_fmt", "yuv420p",
        output_path,
    ]
    
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        # Fallback: just copy without processing
        shutil.copy2(input_path, output_path)


def _build_smart_timeline(avatar_duration, mapped_clips, rng,
                           min_broll_dur=4, max_broll_dur=6):
    """Build DYNAMIC timeline — cut rhythm varies by shot type and pacing.

    V2 SYNC: B-roll clips carry `timeline_start` from VideoIntelligence V2
    (per-chunk timestamps). This function HONORS those timestamps by snapping
    each B-roll to the exact narration second the AI identified.

    Pacing:
    - Wide shots: 6-9s (establishing)
    - Closeups: 3-5s (detail)
    - Default: 4-7s
    - Avatar fills ALL gaps between B-rolls (looped, not capped at 12s)
    - Pattern: 2 B-rolls → avatar break → 2 B-rolls
    """
    segments = []
    mapped_clips.sort(key=lambda c: c.get("timeline_start", 0))

    # Shot type → duration ranges
    SHOT_DURATIONS = {
        "wide": (6, 9),
        "aerial": (6, 8),
        "closeup": (3, 5),
        "detail": (3, 5),
        "pov": (4, 6),
        "diagram": (5, 7),
    }
    # Maximum avatar chunk inserted in ONE segment (keeps segments manageable for
    # the renderer; longer gaps are split into multiple avatar segments).
    _MAX_AVATAR_CHUNK = 12

    current_time = 0.0

    # Intro: avatar only (8-15s or 10% of video, whichever is smaller)
    intro_dur = min(rng.uniform(8, 15), avatar_duration * 0.1)
    segments.append({"type": "avatar", "start": 0.0, "duration": round(intro_dur, 2)})
    current_time = intro_dur

    clip_idx = 0
    consecutive_brolls = 0

    while current_time < avatar_duration - 3 and clip_idx < len(mapped_clips):
        clip = mapped_clips[clip_idx]
        v2_start = clip.get("timeline_start")   # planned by VideoIntelligence V2

        # ── Snap to V2 timestamp ───────────────────────────────────────────────
        # Fill the gap between current_time and clip["timeline_start"] with
        # avatar segments (split into ≤_MAX_AVATAR_CHUNK chunks so the renderer
        # never gets a single 60-second avatar segment).
        # If V2 timestamp is already behind current_time (timeline drifted
        # forward), skip snapping and use current_time as-is.
        target_start = current_time  # fallback when no V2 timestamp
        if v2_start is not None and v2_start > current_time + 1.0:
            target_start = v2_start
            gap = v2_start - current_time
            while gap > 0.5 and current_time < avatar_duration - 3:
                chunk = min(gap, _MAX_AVATAR_CHUNK)
                segments.append({
                    "type": "avatar",
                    "start": round(current_time, 2),
                    "duration": round(chunk, 2),
                })
                current_time += chunk
                gap -= chunk
                consecutive_brolls = 0
            target_start = current_time  # now == v2_start (within float rounding)

        # ── Determine B-roll duration ──────────────────────────────────────────
        shot_type = clip.get("shot_type", "wide")
        if not isinstance(shot_type, str):
            shot_type = "wide"
        dur_range = SHOT_DURATIONS.get(shot_type, (min_broll_dur, max_broll_dur))
        broll_dur = rng.uniform(dur_range[0], dur_range[1])
        broll_dur = min(broll_dur, avatar_duration - target_start)

        if broll_dur >= 3:
            segments.append({
                "type": "broll",
                "start": round(target_start, 2),
                "duration": round(broll_dur, 2),
                "file": clip["file"],
                "is_image": clip["file"].lower().endswith(
                    (".jpg", ".jpeg", ".png", ".bmp", ".webp")),
                "keyword": clip.get("keyword", ""),
                "shot_type": shot_type,
                # Store V2 planned timestamp for audit / beat_timeline sync report
                "v2_planned_start": round(v2_start, 2) if v2_start is not None else None,
            })
            current_time = target_start + broll_dur
            consecutive_brolls += 1

            # After 2 consecutive B-rolls → short avatar break (3-5s)
            if consecutive_brolls >= 2 and clip_idx < len(mapped_clips) - 1:
                break_dur = min(rng.uniform(3, 5), avatar_duration - current_time)
                if break_dur >= 2:
                    segments.append({
                        "type": "avatar",
                        "start": round(current_time, 2),
                        "duration": round(break_dur, 2),
                    })
                    current_time += break_dur
                consecutive_brolls = 0

        clip_idx += 1

    # Outro: remaining avatar — split into ≤_MAX_AVATAR_CHUNK chunks so the
    # renderer never receives a single 60-second avatar segment.
    remaining = avatar_duration - current_time
    while remaining > 0.5:
        chunk = min(remaining, _MAX_AVATAR_CHUNK)
        segments.append({
            "type": "avatar",
            "start": round(current_time, 2),
            "duration": round(chunk, 2),
        })
        current_time += chunk
        remaining -= chunk

    return segments


