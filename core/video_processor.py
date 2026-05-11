"""
FFmpeg Video Processor
Handles all video operations: probe, trim, scale, fade, concat, audio overlay, subtitle burn-in.
"""

import subprocess
import json
import os
import shutil
from pathlib import Path
from typing import List, Optional, Tuple


def _find_ffmpeg() -> str:
    """Find FFmpeg executable."""
    # 1. Check PATH (app.py adds bundled ffmpeg/ to PATH at startup)
    found = shutil.which("ffmpeg")
    if found:
        return found
    # 2. Try bundled path directly
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(project_dir, "ffmpeg", "ffmpeg.exe")
    if os.path.isfile(bundled):
        return bundled
    raise FileNotFoundError(
        "FFmpeg not found! Ensure the ffmpeg/ folder is inside StudioPilot directory."
    )


def _find_ffprobe() -> str:
    """Find FFprobe executable."""
    found = shutil.which("ffprobe")
    if found:
        return found
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(project_dir, "ffmpeg", "ffprobe.exe")
    if os.path.isfile(bundled):
        return bundled
    raise FileNotFoundError("FFprobe not found.")


# === GPU ACCELERATION ===
_ENCODER_CACHE = None

def _get_encoder() -> list:
    """Detect best available encoder. NVENC > CPU ultrafast."""
    global _ENCODER_CACHE
    if _ENCODER_CACHE is not None:
        return _ENCODER_CACHE
    # Using libx264 ultrafast (reliable, fast)
    # NVENC disabled: produces corrupted video on this FFmpeg build
    _ENCODER_CACHE = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"]
    print("  [CPU] Using libx264 ultrafast encoder")
    return _ENCODER_CACHE


def _run_cmd(cmd: List[str], description: str = ""):
    """Run an FFmpeg command and handle errors."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg error ({description}):\n{result.stderr[-1500:]}"
            )
        return result
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"FFmpeg timed out ({description})")


def get_media_info(filepath: str) -> dict:
    """Get media file info using ffprobe."""
    ffprobe = _find_ffprobe()
    cmd = [
        ffprobe, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        filepath
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Cannot probe {filepath}: {result.stderr}")
    return json.loads(result.stdout)


def get_duration(filepath: str) -> float:
    """Get duration of a media file in seconds."""
    info = get_media_info(filepath)
    # Try format duration first
    if "format" in info and "duration" in info["format"]:
        return float(info["format"]["duration"])
    # Try stream duration
    for stream in info.get("streams", []):
        if "duration" in stream:
            return float(stream["duration"])
    raise RuntimeError(f"Cannot determine duration of {filepath}")


def get_resolution(filepath: str) -> Tuple[int, int]:
    """Get video resolution as (width, height)."""
    info = get_media_info(filepath)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise RuntimeError(f"No video stream found in {filepath}")


def is_image_file(filepath: str) -> bool:
    """Check if a file is a static image."""
    ext = Path(filepath).suffix.lower()
    return ext in {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}


def extract_audio(video_path: str, output_path: str):
    """Extract audio track from video to WAV."""
    ffmpeg = _find_ffmpeg()
    cmd = [
        ffmpeg, "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        output_path
    ]
    _run_cmd(cmd, "extract_audio")


def extract_audio_aac(video_path: str, output_path: str):
    """Extract audio track from video to AAC (for final mix)."""
    ffmpeg = _find_ffmpeg()
    cmd = [
        ffmpeg, "-y", "-i", video_path,
        "-vn", "-acodec", "aac", "-b:a", "192k",
        output_path
    ]
    _run_cmd(cmd, "extract_audio_aac")


def trim_video(input_path: str, start: float, duration: float, output_path: str,
               width: int = 1920, height: int = 1080, fps: int = 30):
    """Trim a video segment and scale to target resolution. Uses GPU if available."""
    ffmpeg = _find_ffmpeg()
    enc = _get_encoder()
    cmd = [
        ffmpeg, "-y",
        "-ss", str(round(start, 3)),
        "-i", input_path,
        "-t", str(round(duration, 3)),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
               f"fps={fps}",
        "-an",
    ] + enc + ["-pix_fmt", "yuv420p", output_path]
    _run_cmd(cmd, f"trim_video {start}-{start+duration}")


def apply_fades(input_path: str, fade_in: float, fade_out: float, output_path: str):
    """Apply fade-in and/or fade-out to a video segment."""
    if fade_in <= 0 and fade_out <= 0:
        shutil.copy2(input_path, output_path)
        return

    ffmpeg = _find_ffmpeg()
    enc = _get_encoder()
    duration = get_duration(input_path)

    filters = []
    if fade_in > 0:
        filters.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        fade_start = max(0, duration - fade_out)
        filters.append(f"fade=t=out:st={round(fade_start, 3)}:d={fade_out}")

    filter_str = ",".join(filters)
    cmd = [
        ffmpeg, "-y", "-i", input_path,
        "-vf", filter_str,
    ] + enc + ["-pix_fmt", "yuv420p", "-an", output_path]
    _run_cmd(cmd, "apply_fades")


def create_video_from_image(
    image_path: str,
    duration: float,
    width: int,
    height: int,
    fps: int,
    ken_burns_filter: str,
    output_path: str,
):
    """Create a video clip from a static image with Ken Burns effect."""
    ffmpeg = _find_ffmpeg()
    total_frames = int(duration * fps)

    enc = _get_encoder()
    cmd = [
        ffmpeg, "-y",
        "-loop", "1", "-i", image_path,
        "-vf", f"{ken_burns_filter},scale={width}:{height},"
               f"setsar=1,fps={fps}",
        "-t", str(round(duration, 3)),
    ] + enc + ["-pix_fmt", "yuv420p", "-an", output_path]
    _run_cmd(cmd, "create_video_from_image")


def concat_segments(segment_paths: List[str], output_path: str):
    """Concatenate video segments using concat demuxer."""
    if not segment_paths:
        raise ValueError("No segments to concatenate")

    if len(segment_paths) == 1:
        shutil.copy2(segment_paths[0], output_path)
        return

    ffmpeg = _find_ffmpeg()

    # Create concat file list
    concat_file = output_path + ".concat.txt"
    with open(concat_file, "w") as f:
        for path in segment_paths:
            # Escape single quotes and use forward slashes
            escaped = path.replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c:v", "copy", "-an",
        output_path
    ]
    _run_cmd(cmd, "concat_segments")

    # Cleanup
    try:
        os.remove(concat_file)
    except OSError:
        pass


def overlay_pip(base_video: str, pip_video: str, position: str,
                pip_percent: int, output_width: int, output_height: int,
                output_path: str):
    """Overlay a small PIP (picture-in-picture) video on a base video.
    
    Args:
        position: 'top_right', 'top_left', 'bottom_right', 'bottom_left'
        pip_percent: Size of PIP as percentage of output (15-20)
    """
    ffmpeg = _find_ffmpeg()
    pip_w = int(output_width * pip_percent / 100)
    pip_h = int(output_height * pip_percent / 100)
    padding = 20

    positions = {
        "top_right": (f"{output_width - pip_w - padding}", f"{padding}"),
        "top_left": (f"{padding}", f"{padding}"),
        "bottom_right": (f"{output_width - pip_w - padding}", f"{output_height - pip_h - padding}"),
        "bottom_left": (f"{padding}", f"{output_height - pip_h - padding}"),
    }
    px, py = positions.get(position, positions["top_right"])

    # Scale PIP to exact size, add border, overlay on base
    enc = _get_encoder()
    cmd = [
        ffmpeg, "-y",
        "-i", base_video,
        "-i", pip_video,
        "-filter_complex",
        f"[1:v]scale={pip_w}:{pip_h}:force_original_aspect_ratio=decrease,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        f"pad={pip_w+6}:{pip_h+6}:(ow-iw)/2:(oh-ih)/2:black@0.7,"
        f"format=yuva420p[pip];"
        f"[0:v][pip]overlay={px}:{py}:shortest=1[vout]",
        "-map", "[vout]", "-an",
    ] + enc + ["-pix_fmt", "yuv420p", output_path]
    _run_cmd(cmd, "overlay_pip")


def trim_audio(video_path: str, start: float, duration: float, output_path: str):
    """Extract a specific audio segment from a video file (for sync)."""
    ffmpeg = _find_ffmpeg()
    cmd = [
        ffmpeg, "-y",
        "-ss", str(round(start, 3)),
        "-i", video_path,
        "-t", str(round(duration, 3)),
        "-vn", "-acodec", "aac", "-b:a", "192k",
        output_path
    ]
    _run_cmd(cmd, f"trim_audio {start}-{start+duration}")


def trim_video_with_audio(input_path: str, start: float, duration: float, output_path: str,
                          width: int = 1920, height: int = 1080, fps: int = 30):
    """Trim a video segment WITH audio and scale to target resolution."""
    ffmpeg = _find_ffmpeg()
    enc = _get_encoder()
    cmd = [
        ffmpeg, "-y",
        "-ss", str(round(start, 3)),
        "-i", input_path,
        "-t", str(round(duration, 3)),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
               f"fps={fps}",
    ] + enc + ["-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", output_path]
    _run_cmd(cmd, f"trim_video_with_audio {start}-{start+duration}")


def concat_segments_with_audio(segment_paths: List[str], output_path: str):
    """Concatenate video segments that already have audio tracks."""
    if not segment_paths:
        raise ValueError("No segments to concatenate")

    if len(segment_paths) == 1:
        shutil.copy2(segment_paths[0], output_path)
        return

    ffmpeg = _find_ffmpeg()
    concat_file = output_path + ".concat.txt"
    with open(concat_file, "w") as f:
        for path in segment_paths:
            escaped = path.replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    # Try stream copy first (fast)
    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    # Validate: check output has video stream
    needs_reencode = result.returncode != 0
    if not needs_reencode and os.path.exists(output_path):
        probe = subprocess.run(
            [_find_ffprobe(), "-v", "quiet", "-show_streams", "-of", "json", output_path],
            capture_output=True, text=True, timeout=30
        )
        try:
            streams = json.loads(probe.stdout).get("streams", [])
            has_video = any(s.get("codec_type") == "video" for s in streams)
            if not has_video:
                print("  [concat] WARNING: stream copy produced no video! Re-encoding...")
                needs_reencode = True
        except:
            needs_reencode = True

    if needs_reencode:
        # Re-encode (guaranteed to include video + audio)
        enc = _get_encoder()
        cmd2 = [
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
        ] + enc + ["-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p", output_path]
        _run_cmd(cmd2, "concat_segments_with_audio (re-encode)")

    # Final validation
    if os.path.exists(output_path):
        probe2 = subprocess.run(
            [_find_ffprobe(), "-v", "quiet", "-show_streams", "-of", "json", output_path],
            capture_output=True, text=True, timeout=30
        )
        try:
            streams2 = json.loads(probe2.stdout).get("streams", [])
            has_video2 = any(s.get("codec_type") == "video" for s in streams2)
            if not has_video2:
                raise RuntimeError("concat produced output without video stream!")
        except json.JSONDecodeError:
            raise RuntimeError("concat produced invalid output!")

    try:
        os.remove(concat_file)
    except OSError:
        pass


def overlay_audio(video_path: str, audio_path: str, output_path: str):
    """Overlay audio track onto a video, trimming to the shorter of the two."""
    ffmpeg = _find_ffmpeg()
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        output_path
    ]
    _run_cmd(cmd, "overlay_audio")


def add_background_music(
    video_path: str,
    music_path: str,
    duck_db: float,
    output_path: str,
):
    """Add background music with ducking (lower volume during narration)."""
    ffmpeg = _find_ffmpeg()
    video_dur = get_duration(video_path)

    # Mix: keep original audio at full volume, add music at ducked level
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex",
        f"[1:a]volume={duck_db}dB,afade=t=in:d=3,afade=t=out:st={max(0, video_dur-4)}:d=4[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=3[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", str(round(video_dur, 3)),
        output_path
    ]
    _run_cmd(cmd, "add_background_music")


def burn_subtitles(video_path: str, srt_path: str, font: str, font_size: int,
                   color: str, outline_color: str, outline_width: int,
                   y_offset: int, output_path: str):
    """Burn SRT subtitles into the video."""
    ffmpeg = _find_ffmpeg()

    # Escape path for FFmpeg subtitles filter (Windows needs special handling)
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")

    style = (
        f"FontName={font},"
        f"FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,"
        f"Outline={outline_width},"
        f"Shadow=1,"
        f"MarginV={20 + y_offset}"
    )

    enc = _get_encoder()
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-vf", f"subtitles='{srt_escaped}':force_style='{style}'",
    ] + enc + ["-c:a", "copy", "-pix_fmt", "yuv420p", output_path]
    _run_cmd(cmd, "burn_subtitles")


# ════════════════════════════════════════════════════════════════════════
#  LONG VIDEO SUPPORT — Chunk-based Processing (Task 1.9)
# ════════════════════════════════════════════════════════════════════════

def get_chunk_timestamps(
    total_duration: float,
    chunk_duration: float = 300.0,
) -> List[Tuple[float, float]]:
    """
    Split a video duration into (start, end) pairs for chunk processing.

    Example for a 750s video with 300s chunks:
        [(0, 300), (300, 600), (600, 750)]
    """
    chunks = []
    start = 0.0
    while start < total_duration - 0.5:
        end = min(start + chunk_duration, total_duration)
        chunks.append((round(start, 3), round(end, 3)))
        start = end
    return chunks


def split_video_into_chunks(
    input_path: str,
    chunk_duration: float,
    output_dir: str,
    on_progress=None,
) -> List[str]:
    """
    Split a long video into temporary chunk files using stream copy (fast).

    Returns list of chunk file paths. Empty if splitting failed.
    """
    os.makedirs(output_dir, exist_ok=True)
    ffmpeg = _find_ffmpeg()

    total = get_duration(input_path)
    timestamps = get_chunk_timestamps(total, chunk_duration)
    chunk_paths: List[str] = []

    for idx, (start, end) in enumerate(timestamps):
        out = os.path.join(output_dir, f"chunk_{idx:04d}.mp4")
        cmd = [
            ffmpeg, "-y",
            "-ss", str(start),
            "-i", input_path,
            "-t", str(round(end - start, 3)),
            "-c", "copy",          # stream copy: no re-encode, very fast
            "-avoid_negative_ts", "make_zero",
            out,
        ]
        try:
            _run_cmd(cmd, f"split_chunk_{idx}")
            if os.path.exists(out) and os.path.getsize(out) > 1000:
                chunk_paths.append(out)
                if on_progress:
                    pct = int((idx + 1) / len(timestamps) * 100)
                    on_progress(pct, 100, f"Split {idx+1}/{len(timestamps)}: {start:.0f}s-{end:.0f}s")
            else:
                print(f"  [video_processor] Chunk {idx} vazio -- ignorando")
        except Exception as e:
            print(f"  [video_processor] Erro ao dividir chunk {idx}: {e}")

    return chunk_paths


def merge_video_chunks(chunk_paths: List[str], output_path: str) -> None:
    """Concatenate processed chunks into final video. Wraps concat_segments."""
    if not chunk_paths:
        raise ValueError("Nenhum chunk para concatenar")
    concat_segments(chunk_paths, output_path)


def process_video_in_chunks(
    input_path: str,
    output_path: str,
    process_fn,
    chunk_duration: float = 300.0,
    on_progress=None,
    total_timeout: float = 1800.0,
) -> None:
    """
    Process a long video by splitting into chunks, processing each, then concatenating.

    This prevents memory exhaustion on videos > 15 minutes.

    Args:
        input_path:     source video file
        output_path:    destination for the final assembled video
        process_fn:     callable(chunk_path: str, chunk_idx: int, total: int) -> str
                        Must return path to the processed chunk output file.
                        Returning None or raising an exception causes the original
                        chunk to be used as fallback.
        chunk_duration: size of each chunk in seconds (default 5 minutes)
        on_progress:    optional callable(current, total, message)
        total_timeout:  hard timeout in seconds (default 30 minutes); raises
                        RuntimeError if exceeded

    Memory management:
        gc.collect() is called after each chunk and temp files are removed
        immediately, keeping peak RAM proportional to one chunk, not the full video.
    """
    import gc

    start_wall = time.time()
    tmp_dir = output_path + "_proc_chunks"
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        total_dur = get_duration(input_path)
        timestamps = get_chunk_timestamps(total_dur, chunk_duration)
        total_chunks = len(timestamps)

        print(
            f"  [video_processor] Video longo: {total_dur:.0f}s "
            f"-> {total_chunks} chunks de {chunk_duration:.0f}s"
        )

        processed: List[str] = []

        for idx, (start, end) in enumerate(timestamps):
            # Enforce total timeout
            elapsed = time.time() - start_wall
            if elapsed > total_timeout:
                raise RuntimeError(
                    f"Timeout: processamento levou {elapsed:.0f}s "
                    f"(maximo {total_timeout:.0f}s)"
                )

            if on_progress:
                pct = int(idx / total_chunks * 80)
                on_progress(pct, 100,
                            f"Chunk {idx+1}/{total_chunks} ({start:.0f}s-{end:.0f}s)...")

            # Extract this chunk (stream copy, fast)
            chunk_in = os.path.join(tmp_dir, f"in_{idx:04d}.mp4")
            ffmpeg = _find_ffmpeg()
            split_cmd = [
                ffmpeg, "-y",
                "-ss", str(start),
                "-i", input_path,
                "-t", str(round(end - start, 3)),
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                chunk_in,
            ]
            try:
                _run_cmd(split_cmd, f"chunk_split_{idx}")
            except Exception as e:
                print(f"  [video_processor] Falha ao extrair chunk {idx}: {e}")
                continue

            if not os.path.exists(chunk_in) or os.path.getsize(chunk_in) < 1000:
                print(f"  [video_processor] Chunk {idx} vazio -- pulando")
                _safe_remove_vp(chunk_in)
                continue

            # Process this chunk
            chunk_out = chunk_in  # fallback: use raw chunk
            try:
                result_path = process_fn(chunk_in, idx, total_chunks)
                if result_path and os.path.exists(result_path):
                    chunk_out = result_path
                elif result_path:
                    print(f"  [video_processor] process_fn retornou path inexistente para chunk {idx}")
            except Exception as e:
                print(f"  [video_processor] process_fn falhou no chunk {idx}: {e} -- usando fallback")

            processed.append(chunk_out)

            # Free memory and clean up input chunk
            gc.collect()
            if chunk_in != chunk_out:
                _safe_remove_vp(chunk_in)

            elapsed = time.time() - start_wall
            pct_done = (idx + 1) / total_chunks * 100
            print(
                f"  [video_processor] Chunk {idx+1}/{total_chunks} OK "
                f"({pct_done:.0f}% | {elapsed:.0f}s decorridos)"
            )

        if not processed:
            raise RuntimeError("Nenhum chunk foi processado com sucesso")

        if on_progress:
            on_progress(90, 100, f"Concatenando {len(processed)} chunks...")

        concat_segments(processed, output_path)

        if on_progress:
            on_progress(100, 100, "Video longo concluido!")

        elapsed = time.time() - start_wall
        print(
            f"  [video_processor] Concluido: {len(processed)} chunks em {elapsed:.0f}s "
            f"-> {output_path}"
        )

    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


def _safe_remove_vp(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
