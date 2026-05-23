"""
Motion Graphics System — FFmpeg-based animated overlays.
Creates professional lower thirds, title cards, and text animations
without requiring Node.js/Remotion.
"""
import os
import shutil
import subprocess


def _find_ffmpeg():
    return shutil.which("ffmpeg") or "ffmpeg"

def _get_encoder():
    """Detect best available encoder. Reuses logic from video_processor."""
    try:
        from core.video_processor import _get_encoder as _gp
        return _gp()
    except Exception:
        return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"]


def add_lower_third(video_path, output_path, text, subtitle="",
                     start_time=5.0, duration=4.0,
                     position="bottom_left", style="modern"):
    """Add an animated lower third text overlay.
    
    Styles: 'modern' (gradient bar), 'minimal' (clean text), 'bold' (full width)
    """
    ffmpeg = _find_ffmpeg()
    
    # Font settings per style
    styles = {
        "modern": {
            "bg": "black@0.6",
            "title_size": 28,
            "sub_size": 18,
            "bar_h": 70 if subtitle else 50,
            "bar_color": "0x1E90FF@0.8",
            "y_pos": "H-140",
            "x_pos": "30",
        },
        "minimal": {
            "bg": "black@0.4",
            "title_size": 24,
            "sub_size": 16,
            "bar_h": 60 if subtitle else 40,
            "bar_color": "white@0.1",
            "y_pos": "H-120",
            "x_pos": "30",
        },
        "bold": {
            "bg": "0xFF4444@0.85",
            "title_size": 32,
            "sub_size": 20,
            "bar_h": 80 if subtitle else 55,
            "bar_color": "0xFF4444@0.85",
            "y_pos": "H-150",
            "x_pos": "0",
        },
    }
    s = styles.get(style, styles["modern"])
    end_time = start_time + duration
    fade_dur = 0.5

    # Build filter: background bar + title text + optional subtitle
    # Animate: fade in, hold, fade out
    bar_filter = (
        f"drawbox=x={s['x_pos']}:y={s['y_pos']}:w=600:h={s['bar_h']}:"
        f"color={s['bar_color']}:t=fill:"
        f"enable='between(t,{start_time},{end_time})'"
    )

    title_y = f"{s['y_pos']}+10" if not subtitle else f"{s['y_pos']}+8"
    title_filter = (
        f"drawtext=text='{_esc(text)}':"
        f"fontsize={s['title_size']}:fontcolor=white:"
        f"x=40:y={title_y}:"
        f"font=Arial:borderw=1:bordercolor=black@0.5:"
        f"enable='between(t,{start_time},{end_time})'"
    )

    filters = [bar_filter, title_filter]

    if subtitle:
        sub_filter = (
            f"drawtext=text='{_esc(subtitle)}':"
            f"fontsize={s['sub_size']}:fontcolor=white@0.8:"
            f"x=40:y={s['y_pos']}+{s['title_size']+14}:"
            f"font=Arial:"
            f"enable='between(t,{start_time},{end_time})'"
        )
        filters.append(sub_filter)

    vf = ",".join(filters)

    enc = _get_encoder()
    cmd = [
        ffmpeg, "-y",
        "-hwaccel", "auto",
        "-i", video_path,
        "-vf", vf,
    ] + enc + ["-c:a", "copy", "-pix_fmt", "yuv420p", output_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        shutil.copy2(video_path, output_path)
    return output_path


def add_title_card(video_path, output_path, title, subtitle="",
                    start_time=0.0, duration=5.0,
                    bg_opacity=0.7):
    """Add a full-screen title card overlay with fade animation."""
    ffmpeg = _find_ffmpeg()
    end_time = start_time + duration
    fade_in = start_time + 0.3
    fade_out = end_time - 0.5

    filters = [
        # Dark overlay
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{bg_opacity}:t=fill:"
        f"enable='between(t,{start_time},{end_time})'",
        # Main title (centered)
        f"drawtext=text='{_esc(title)}':"
        f"fontsize=48:fontcolor=white:"
        f"x=(w-tw)/2:y=(h-th)/2-20:"
        f"font=Arial:borderw=2:bordercolor=black:"
        f"enable='between(t,{fade_in},{fade_out})'",
    ]

    if subtitle:
        filters.append(
            f"drawtext=text='{_esc(subtitle)}':"
            f"fontsize=24:fontcolor=white@0.8:"
            f"x=(w-tw)/2:y=(h/2)+30:"
            f"font=Arial:"
            f"enable='between(t,{fade_in},{fade_out})'"
        )

    cmd = [
        ffmpeg, "-y", "-i", video_path,
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "copy", "-pix_fmt", "yuv420p",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        shutil.copy2(video_path, output_path)


def add_chapter_marker(video_path, output_path, chapter_text,
                        start_time=0.0, duration=3.0):
    """Add a chapter transition marker (number + title)."""
    ffmpeg = _find_ffmpeg()
    enc = _get_encoder()
    end_time = start_time + duration

    filters = [
        f"drawbox=x=0:y=0:w=iw:h=4:color=0x1E90FF@0.9:t=fill:"
        f"enable='between(t,{start_time},{end_time})'",
        f"drawtext=text='{_esc(chapter_text)}':"
        f"fontsize=22:fontcolor=white:"
        f"x=20:y=15:"
        f"font=Arial:borderw=2:bordercolor=black@0.7:"
        f"enable='between(t,{start_time},{end_time})'",
    ]

    cmd = [
        ffmpeg, "-y",
        "-hwaccel", "auto",
        "-i", video_path,
        "-vf", ",".join(filters),
    ] + enc + ["-c:a", "copy", "-pix_fmt", "yuv420p", output_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        shutil.copy2(video_path, output_path)


def add_progress_bar(video_path, output_path, total_duration,
                      color="0x1E90FF", height=4, position="bottom"):
    """Add an animated progress bar to the video."""
    ffmpeg = _find_ffmpeg()
    y = "H-6" if position == "bottom" else "2"

    vf = (
        f"drawbox=x=0:y={y}:w=trunc(iw*t/{total_duration}):h={height}:"
        f"color={color}@0.8:t=fill"
    )

    enc = _get_encoder()
    cmd = [
        ffmpeg, "-y",
        "-hwaccel", "auto",
        "-i", video_path,
        "-vf", vf,
    ] + enc + ["-c:a", "copy", "-pix_fmt", "yuv420p", output_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        shutil.copy2(video_path, output_path)


def _esc(text):
    """Escape text for FFmpeg drawtext filter.
    Sanitizes NULL bytes and other control chars that crash subprocess."""
    if text is None:
        return ""
    # Remove NULL bytes and control chars (except common whitespace)
    text = str(text)
    text = "".join(c for c in text if c == "\t" or c == "\n" or c == "\r" or ord(c) >= 0x20)
    return (text
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace(":", "\\:")
            .replace("%", "%%"))
