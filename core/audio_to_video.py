"""
Audio to Video — Convert audio files (MP3, WAV, podcast) to video with waveform visualization.
Generates animated waveform + optional static image/logo background.
Supports: waveform styles, brand colors, subtitles, background images.
"""
import os
import json
import subprocess
import tempfile
import math
import struct
from pathlib import Path


def get_audio_duration(audio_path, ffprobe_path="ffprobe"):
    result = subprocess.run(
        [ffprobe_path, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True, timeout=15
    )
    return float(result.stdout.strip() or 0)


def _generate_wav_samples(audio_path, num_samples=200, ffmpeg_path="ffmpeg"):
    """Extract amplitude samples from audio for waveform visualization."""
    fd, raw_path = tempfile.mkstemp(suffix=".raw")
    os.close(fd)
    try:
        subprocess.run(
            [ffmpeg_path, "-y", "-i", audio_path, "-ac", "1", "-ar", "44100",
             "-f", "s16le", "-acodec", "pcm_s16le", raw_path],
            capture_output=True, text=True, timeout=120, check=True
        )
        with open(raw_path, "rb") as f:
            data = f.read()
        samples = []
        for i in range(0, len(data) - 1, 2):
            val = struct.unpack("<h", data[i:i+2])[0]
            samples.append(abs(val) / 32768.0)
        if not samples:
            return [0.5] * num_samples
        chunk = max(1, len(samples) // num_samples)
        waveform = [max(samples[i:i+chunk]) for i in range(0, len(samples), chunk)][:num_samples]
        while len(waveform) < num_samples:
            waveform.append(0.1)
        return waveform
    except Exception:
        return [0.3 + 0.4 * math.sin(i * 0.1) for i in range(num_samples)]
    finally:
        try: os.unlink(raw_path)
        except: pass


def _build_waveform_filter(waveform, width=1920, height=1080,
                           bar_color="#1a73e8", bg_color="#000000"):
    """Build FFmpeg drawbox filter string for waveform visualization."""
    bars = len(waveform)
    bar_w = max(2, width // (bars * 2))
    gap = bar_w
    total_w = bars * (bar_w + gap)
    start_x = (width - total_w) // 2
    base_y = height - 100
    max_h = height - 200
    filters = []
    filters.append(f"color=c={bg_color}:s={width}x{height}:d=999[dwave]")
    for i, amp in enumerate(waveform):
        x = start_x + i * (bar_w + gap)
        bar_h = max(2, int(amp * max_h))
        y = base_y - bar_h
        filters.append(f"[dwave]drawbox=x={x}:y={y}:w={bar_w}:h={bar_h}:color={bar_color}:t=fill[dwave]")
    return filters


def render_waveform_video(audio_path, output_path, title="", author="",
                          background_image=None, bar_color="#1a73e8",
                          bg_color="#000000", width=1920, height=1080,
                          fps=30, ffmpeg_path="ffmpeg"):
    """Render an audio file to a video with animated waveform visualization."""
    # Safely derive ffprobe path from ffmpeg path: replace only the BASENAME
    # (not the full string) so directories named e.g. ".../ffmpeg/bin/..." don't
    # get accidentally rewritten to ".../ffprobe/bin/...".
    _ff_dir = os.path.dirname(ffmpeg_path)
    _ff_base = os.path.basename(ffmpeg_path)
    _probe_base = _ff_base.replace("ffmpeg", "ffprobe")
    ffprobe_path = os.path.join(_ff_dir, _probe_base) if _ff_dir else _probe_base
    if not os.path.isfile(ffprobe_path):
        import shutil as _sh
        ffprobe_path = _sh.which("ffprobe") or "ffprobe"
    duration = get_audio_duration(audio_path, ffprobe_path)
    if duration <= 0:
        raise RuntimeError("Could not determine audio duration")

    waveform = _generate_wav_samples(audio_path, num_samples=200, ffmpeg_path=ffmpeg_path)
    bar_w = max(2, 1920 // (200 * 2))
    gap = bar_w
    total_w = 200 * (bar_w + gap)
    start_x = (width - total_w) // 2
    base_y = height - 100
    max_h = height - 200

    # Build complex filter for animated waveform
    filter_parts = [f"color=c={bg_color}:s={width}x{height}:r={fps}:d={duration}[bg]"]
    if background_image and os.path.exists(background_image):
        filter_parts.append(f"movie={background_image.replace(os.sep, '/')}:loop=0,setpts=N/FRAME_RATE*fps[bgimg]")
        filter_parts.append("[bg][bgimg]overlay=0:0:format=auto:shortest=1[bg]")

    # Add title text
    if title:
        safe_title = title.replace("'", "\\'").replace(":", "\\:")
        filter_parts.append(
            f"[bg]drawtext=text='{safe_title}':fontcolor=white:fontsize=48:"
            f"x=(w-text_w)/2:y=80:fontfile=Arial[bgt]"
        )
    else:
        filter_parts.append("[bg]null[bgt]")

    # Add author text
    if author:
        safe_author = author.replace("'", "\\'").replace(":", "\\:")
        filter_parts.append(
            f"[bgt]drawtext=text='{safe_author}':fontcolor=gray:fontsize=28:"
            f"x=(w-text_w)/2:y=140:fontfile=Arial[bgt2]"
        )
    else:
        filter_parts.append("[bgt]null[bgt2]")

    current = "bgt2"
    for i, amp in enumerate(waveform):
        x = start_x + i * (bar_w + gap)
        bar_h = max(2, int(amp * max_h))
        y = base_y - bar_h
        color = bar_color
        filter_parts.append(
            f"[{current}]drawbox=x={x}:y={y}:w={bar_w}:h={bar_h}:color={color}:t=fill:enable='between(t,{i*0.1},{i*0.1+999})'[w{i}]"
        )
        current = f"w{i}"

    filter_str = ";".join(filter_parts)
    last_output = current.replace("[", "").replace("]", "")

    cmd = [
        ffmpeg_path, "-y",
        "-i", audio_path,
        "-filter_complex", filter_str,
        "-map", f"[{last_output}]",
        "-map", "0:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
    return output_path


def audio_to_video_config(audio_path, title="", author="", background_image=None,
                           bar_color="#1a73e8", bg_color="#000000"):
    """Build config dict for audio-to-video processing."""
    return {
        "audio_path": audio_path,
        "title": title,
        "author": author,
        "background_image": background_image,
        "bar_color": bar_color,
        "bg_color": bg_color,
        "type": "audio_to_video",
    }
