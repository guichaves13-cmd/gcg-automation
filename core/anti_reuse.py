"""
Anti-Reuse Effects
Applies random visual transformations to stock footage to avoid
YouTube's "reused content" detection.
Every clip gets unique: zoom, color shift, crop position, speed.
"""

import os
import json
import random
import shutil
import subprocess
from typing import Optional

# =============================================
# CROSS-PROJECT CLIP TRACKING
# Prevents the same stock clip from appearing in multiple videos.
# =============================================
_CLIP_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "used_clips.json")

def load_used_clips() -> set:
    """Load set of previously used clip IDs from disk."""
    try:
        if os.path.exists(_CLIP_DB_PATH):
            with open(_CLIP_DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("clip_ids", []))
    except Exception:
        pass
    return set()

def save_used_clips(clip_ids: set):
    """Merge new clip IDs with existing database and save."""
    existing = load_used_clips()
    merged = existing | clip_ids
    # Keep last 5000 to prevent file from growing indefinitely
    if len(merged) > 5000:
        merged = set(list(merged)[-5000:])
    try:
        with open(_CLIP_DB_PATH, "w", encoding="utf-8") as f:
            json.dump({"clip_ids": list(merged), "total": len(merged)}, f)
    except Exception:
        pass

def clear_used_clips():
    """Reset the clip database (for testing or fresh start)."""
    try:
        if os.path.exists(_CLIP_DB_PATH):
            os.remove(_CLIP_DB_PATH)
    except Exception:
        pass


def _find_ffmpeg():
    return shutil.which("ffmpeg") or "ffmpeg"


def apply_anti_reuse(
    input_path: str,
    output_path: str,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    seed: Optional[int] = None,
):
    """
    Apply random visual transformations to make a clip unique.
    Combines multiple effects so YouTube can't match it as reused content:
    - Random zoom (105-120%)
    - Random crop position
    - Random brightness/contrast shift
    - Random color temperature
    - Random speed (95-105%)
    - Random horizontal flip (30% chance)
    """
    rng = random.Random(seed)
    ffmpeg = _find_ffmpeg()

    filters = []

    # 1. Random zoom + crop position (makes framing unique)
    zoom = rng.uniform(1.05, 1.20)
    scaled_w = int(width * zoom)
    scaled_h = int(height * zoom)
    max_x = scaled_w - width
    max_y = scaled_h - height
    crop_x = rng.randint(0, max(0, max_x))
    crop_y = rng.randint(0, max(0, max_y))
    filters.append(f"scale={scaled_w}:{scaled_h}")
    filters.append(f"crop={width}:{height}:{crop_x}:{crop_y}")

    # 2. Random brightness + contrast (subtle)
    brightness = rng.uniform(-0.03, 0.03)
    contrast = rng.uniform(0.95, 1.05)
    saturation = rng.uniform(0.90, 1.10)
    filters.append(f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:saturation={saturation:.3f}")

    # 3. Random color temperature shift (warm/cool)
    # Slight red/blue channel adjustment
    r_shift = rng.uniform(0.97, 1.03)
    b_shift = rng.uniform(0.97, 1.03)
    filters.append(f"colorbalance=rs={r_shift-1:.3f}:bs={b_shift-1:.3f}")

    # 4. Random horizontal flip (30% chance)
    if rng.random() < 0.30:
        filters.append("hflip")

    # 5. Ensure correct output format
    filters.append(f"fps={fps}")
    filters.append("setsar=1")

    filter_str = ",".join(filters)

    # 6. Random speed change (95-105%)
    speed = rng.uniform(0.95, 1.05)
    atempo = 1.0 / speed if speed != 1.0 else 1.0
    speed_filter = f"setpts={1/speed:.4f}*PTS"
    filter_str = f"{speed_filter},{filter_str}"

    # Detect encoder
    from core.video_processor import _get_encoder
    enc = _get_encoder()

    cmd = [
        ffmpeg, "-y",
        "-i", input_path,
        "-vf", filter_str,
        "-an",
    ] + enc + ["-pix_fmt", "yuv420p", output_path]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
    except subprocess.CalledProcessError:
        # Fallback: simpler filter chain if complex one fails
        simple_filters = [
            f"scale={scaled_w}:{scaled_h}",
            f"crop={width}:{height}:{crop_x}:{crop_y}",
            f"fps={fps}", "setsar=1"
        ]
        cmd_simple = [
            ffmpeg, "-y", "-i", input_path,
            "-vf", ",".join(simple_filters),
            "-an",
        ] + enc + ["-pix_fmt", "yuv420p", output_path]
        subprocess.run(cmd_simple, capture_output=True, text=True, timeout=120, check=True)
