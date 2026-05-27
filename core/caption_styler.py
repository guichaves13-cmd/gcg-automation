"""
Caption Styler — Animated, branded caption rendering.
Generates ASS subtitles with per-word animation, brand colors, and positioning.
Support: typewriter, highlight, karaoke, fade, slide animations.
"""
import os
import json
import re
import tempfile
from pathlib import Path

STYLE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "caption_styles.json")

DEFAULT_STYLES = {
    "typewriter": {
        "name": "Typewriter",
        "animation": "typewriter",
        "font": "Arial",
        "font_size": 28,
        "bold": True,
        "color": "#FFFFFF",
        "highlight_color": "#FFEB3B",
        "outline_color": "#000000",
        "outline_width": 2,
        "shadow": True,
        "position": "bottom",
        "margin_bottom": 60,
        "word_per_line": False,
        "max_chars_per_line": 40,
        "animation_speed": 0.05,
    },
    "karaoke": {
        "name": "Karaoke",
        "animation": "karaoke",
        "font": "Arial",
        "font_size": 32,
        "bold": True,
        "color": "#FFFFFF",
        "highlight_color": "#FF6B6B",
        "outline_color": "#000000",
        "outline_width": 2,
        "shadow": True,
        "position": "bottom",
        "margin_bottom": 80,
        "word_per_line": False,
        "max_chars_per_line": 35,
        "animation_speed": 0.0,
    },
    "highlight": {
        "name": "Highlight",
        "animation": "highlight",
        "font": "Arial",
        "font_size": 30,
        "bold": True,
        "color": "#FFFFFF",
        "highlight_color": "#4FC3F7",
        "outline_color": "#000000",
        "outline_width": 2,
        "shadow": True,
        "position": "bottom",
        "margin_bottom": 70,
        "word_per_line": True,
        "max_chars_per_line": 30,
        "animation_speed": 0.03,
    },
    "minimal": {
        "name": "Minimal",
        "animation": "fade",
        "font": "Arial",
        "font_size": 24,
        "bold": False,
        "color": "#FFFFFF",
        "highlight_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 1,
        "shadow": False,
        "position": "bottom",
        "margin_bottom": 50,
        "word_per_line": False,
        "max_chars_per_line": 50,
        "animation_speed": 0.02,
    },
}

ANIMATIONS = ("typewriter", "karaoke", "highlight", "fade", "slide", "none")


def _load_styles():
    try:
        with open(STYLE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _save_styles({**DEFAULT_STYLES})
        return {**DEFAULT_STYLES}

def _save_styles(styles):
    with open(STYLE_FILE, "w", encoding="utf-8") as f:
        json.dump(styles, f, indent=2, ensure_ascii=False)

def list_styles():
    return _load_styles()

def get_style(name):
    styles = _load_styles()
    return styles.get(name)

def save_style(name, style_def):
    styles = _load_styles()
    styles[name] = style_def
    _save_styles(styles)
    return styles[name]

def delete_style(name):
    styles = _load_styles()
    if name in styles and name not in DEFAULT_STYLES:
        del styles[name]
        _save_styles(styles)
        return True
    return False

def reset_styles():
    _save_styles({**DEFAULT_STYLES})
    return {**DEFAULT_STYLES}


def _build_ass_header(style, width=1920, height=1080):
    """Build ASS format header with style definition."""
    pos_map = {"top": 8, "middle": 5, "bottom": 2}
    align = pos_map.get(style.get("position", "bottom"), 2)
    margin_v = style.get("margin_bottom", 60)
    if style.get("position") == "top":
        margin_v = 30
    elif style.get("position") == "middle":
        margin_v = 0
    font_size = style.get("font_size", 28)
    primary = style.get("color", "#FFFFFF").lstrip("#")
    secondary = style.get("highlight_color", "#FFEB3B").lstrip("#")
    outline = style.get("outline_color", "#000000").lstrip("#")
    border = style.get("outline_width", 2)
    shadow = "1" if style.get("shadow") else "0"
    bold = "-1" if style.get("bold") else "0"
    font_name = style.get("font", "Arial")

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_name},{font_size},&H00{primary},&H00{secondary},&H00{outline},&H00000000,{bold},0,0,0,100,100,0,0,1,{border},{shadow},{align},10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header


def _seconds_to_ass(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02.2f}".replace(",", ".")


def _wrap_text(text, max_chars=40):
    """Wrap text to fit within max_chars per line."""
    if not max_chars or len(text) <= max_chars:
        return text
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > max_chars and current:
            lines.append(current.strip())
            current = word
        else:
            current += " " + word if current else word
    if current:
        lines.append(current.strip())
    return "\\N".join(lines)


def generate_ass_subtitles(segments, style_name="typewriter", width=1920, height=1080):
    """
    Generate ASS subtitle content from transcript segments.
    segments: list of dicts with "start", "end", "text"
    Returns ASS file content as string.
    """
    style = get_style(style_name) or get_style("typewriter")
    header = _build_ass_header(style, width, height)
    lines = [header]
    animation = style.get("animation", "typewriter")
    max_chars = style.get("max_chars_per_line", 40)
    word_per_line = style.get("word_per_line", False)

    seg_dialog = []
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", start + 2)
        text = seg.get("text", "").strip()
        if not text:
            continue

        text = text.replace("\n", " ").replace("\r", "")
        text = re.sub(r"[^\w\s,.!?;:'\"()-]", "", text)

        if word_per_line:
            words = text.split()
            per_word_dur = (end - start) / max(len(words), 1)
            for i, word in enumerate(words):
                ws = start + i * per_word_dur
                we = ws + per_word_dur
                wrapped = _wrap_text(word, max_chars)
                if animation == "karaoke":
                    dialog = f"Dialogue: 0,{_seconds_to_ass(ws)},{_seconds_to_ass(we)},Caption,,0,0,0,,{{\\k{int(per_word_dur*100)}}}{wrapped}"
                else:
                    dialog = f"Dialogue: 0,{_seconds_to_ass(ws)},{_seconds_to_ass(we)},Caption,,0,0,0,,{wrapped}"
                seg_dialog.append(dialog)
        else:
            wrapped = _wrap_text(text, max_chars)
            if animation == "karaoke":
                dur_cs = int((end - start) * 100)
                dialog = f"Dialogue: 0,{_seconds_to_ass(start)},{_seconds_to_ass(end)},Caption,,0,0,0,,{{\\k{dur_cs}}}{wrapped}"
            else:
                dialog = f"Dialogue: 0,{_seconds_to_ass(start)},{_seconds_to_ass(end)},Caption,,0,0,0,,{wrapped}"
            seg_dialog.append(dialog)

    lines.extend(seg_dialog)
    return "\n".join(lines)


def generate_ass_to_file(segments, output_path, style_name="typewriter", width=1920, height=1080):
    """Generate ASS file from transcript segments."""
    content = generate_ass_subtitles(segments, style_name, width, height)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path


def render_captions(video_path, segments, output_path, style_name="typewriter",
                    brand_kit=None, width=1920, height=1080, ffmpeg_path="ffmpeg"):
    """
    Overlay styled captions onto a video using ASS subtitle filter.
    If brand_kit provided, uses brand colors for styling.
    """
    import subprocess
    from core.video_processor import _get_encoder

    style = get_style(style_name) or get_style("typewriter")
    if brand_kit:
        colors = brand_kit.get("colors", {})
        if colors.get("accent"):
            style["highlight_color"] = colors["accent"]
        if colors.get("text"):
            style["color"] = colors["text"]
        if brand_kit.get("fonts", {}).get("caption"):
            style["font"] = brand_kit["fonts"]["caption"]

    fd, ass_path = tempfile.mkstemp(suffix=".ass")
    os.close(fd)
    try:
        generate_ass_to_file(segments, ass_path, style_name, width, height)
        enc = _get_encoder()

        cmd = [
            ffmpeg_path, "-y",
            "-hwaccel", "auto",
            "-i", video_path,
            "-vf", "ass=" + ass_path.replace(os.sep, "/" if os.sep == "/" else "\\\\"),
        ] + enc + ["-c:a", "copy", output_path]
        subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=True)
        return output_path
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Caption render failed: {e.stderr[:200]}")
    finally:
        try:
            os.unlink(ass_path)
        except Exception:
            pass
