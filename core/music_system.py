"""
Background Music & SFX System
Automatically selects and applies background music matching video themes.
Uses royalty-free sources + FFmpeg mixing.
"""
import os
import shutil
import subprocess
import random


def _find_ffmpeg():
    return shutil.which("ffmpeg") or "ffmpeg"


# Theme → search terms mapping for finding appropriate music
THEME_MUSIC_MAP = {
    # Historical/War themes
    "war": ["epic cinematic", "battle drums", "dramatic orchestral"],
    "battle": ["epic battle", "war drums", "intense action"],
    "empire": ["epic orchestral", "majestic throne", "ancient civilization"],
    "mongol": ["asian epic", "nomadic drums", "steppe music"],
    "medieval": ["medieval epic", "castle music", "knights theme"],
    "ancient": ["ancient civilization", "epic history", "dramatic orchestral"],
    "conquest": ["epic conquest", "victory march", "war horns"],
    # Nature themes
    "ocean": ["ocean ambient", "calm waves", "underwater sounds"],
    "forest": ["forest ambient", "nature sounds", "woodland"],
    "mountain": ["mountain epic", "adventure music", "wind ambient"],
    "desert": ["desert ambient", "middle eastern", "sand dunes"],
    # Science/Tech themes
    "science": ["science documentary", "technology ambient", "electronic calm"],
    "space": ["space ambient", "cosmic epic", "interstellar"],
    "technology": ["tech background", "digital ambient", "futuristic"],
    # Drama/Emotion themes
    "mystery": ["mystery suspense", "dark ambient", "thriller"],
    "danger": ["tension building", "suspense dramatic", "danger alert"],
    "hope": ["inspirational piano", "uplifting orchestral", "hope theme"],
    "sad": ["melancholy piano", "emotional strings", "sad ambient"],
    # Default
    "documentary": ["documentary background", "cinematic ambient", "neutral score"],
}

# SFX categories
SFX_MAP = {
    "transition": ["whoosh", "swoosh", "transition sweep"],
    "impact": ["boom impact", "dramatic hit", "bass drop"],
    "reveal": ["reveal shimmer", "magic sparkle", "unveil"],
    "tension": ["rising tension", "suspense build", "heartbeat"],
    "success": ["success chime", "achievement", "victory fanfare"],
}


def detect_theme_from_text(text: str) -> str:
    """Detect video theme from narration text."""
    text_lower = text.lower()
    
    # Score each theme by keyword matches
    scores = {}
    for theme, terms in THEME_MUSIC_MAP.items():
        score = 0
        if theme in text_lower:
            score += 3
        for term in terms:
            for word in term.split():
                if word.lower() in text_lower:
                    score += 1
        scores[theme] = score
    
    # Return highest scoring theme
    if scores:
        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best
    
    return "documentary"


def add_background_music(video_path, music_path, output_path,
                          music_volume=0.12, fade_out_duration=3.0,
                          enable_ducking=True):
    """Mix background music into video with intelligent audio ducking.
    
    When ducking is enabled, music volume automatically drops during speech
    and rises during silence — like InVideo/VidRush professional mixing.
    """
    ffmpeg = _find_ffmpeg()
    # Safely derive ffprobe path: replace only the BASENAME so directories
    # containing 'ffmpeg' in their name don't get rewritten to 'ffprobe'.
    _ff_dir = os.path.dirname(ffmpeg)
    _ff_base = os.path.basename(ffmpeg)
    _probe_base = _ff_base.replace("ffmpeg", "ffprobe")
    ffprobe = os.path.join(_ff_dir, _probe_base) if _ff_dir else _probe_base
    if not os.path.isfile(ffprobe):
        ffprobe = shutil.which("ffprobe") or "ffprobe"

    # Get video duration for fade-out timing
    try:
        r = subprocess.run([ffprobe,
            "-v", "quiet", "-show_format", "-of", "json", video_path],
            capture_output=True, text=True, timeout=15)
        import json
        dur = float(json.loads(r.stdout)["format"]["duration"])
    except:
        dur = 300  # fallback

    fade_start = max(0, dur - fade_out_duration)

    if enable_ducking:
        # Professional audio ducking via sidechaincompress
        # Speech (voice) triggers compression on music track
        # attack=0.5s (smooth fade down), release=1.0s (smooth fade up)
        # ratio=4:1, threshold=-30dB — standard broadcast ducking
        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-i", music_path,
            "-filter_complex",
            f"[1:a]volume={music_volume},afade=t=in:d=3,afade=t=out:st={fade_start}:d={fade_out_duration}[music];"
            f"[0:a]asplit=2[voice][voiceref];"
            f"[music][voiceref]sidechaincompress=threshold=0.015:ratio=4:attack=500:release=1000:level_in=1:level_sc=1.5[ducked];"
            f"[voice][ducked]amix=inputs=2:duration=first:dropout_transition=3:weights=1 0.8[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            output_path,
        ]
    else:
        # Simple mix without ducking (original behavior)
        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-i", music_path,
            "-filter_complex",
            f"[1:a]volume={music_volume},afade=t=in:d=3,afade=t=out:st={fade_start}:d={fade_out_duration}[music];"
            f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=3[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            output_path,
        ]
    
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        if enable_ducking:
            # Fallback: try without ducking
            print("  [Music] Ducking failed, trying simple mix...")
            return add_background_music(video_path, music_path, output_path,
                                       music_volume, fade_out_duration, enable_ducking=False)
        # Final fallback: just copy original
        shutil.copy2(video_path, output_path)
    return output_path


def add_sfx_at_timestamps(video_path, sfx_path, timestamps, output_path,
                           sfx_volume=0.5):
    """Add sound effects at specific timestamps in the video."""
    ffmpeg = _find_ffmpeg()
    
    if not timestamps or not os.path.exists(sfx_path):
        shutil.copy2(video_path, output_path)
        return output_path

    # Build filter for each timestamp
    inputs = ["-i", video_path, "-i", sfx_path]
    
    # Create delayed copies of SFX for each timestamp
    sfx_parts = []
    for i, ts in enumerate(timestamps[:10]):  # Max 10 SFX
        sfx_parts.append(f"[1:a]adelay={int(ts*1000)}|{int(ts*1000)},volume={sfx_volume}[sfx{i}]")
    
    # Mix all together
    mix_inputs = "[0:a]" + "".join(f"[sfx{i}]" for i in range(len(sfx_parts)))
    filter_str = ";".join(sfx_parts) + f";{mix_inputs}amix=inputs={len(sfx_parts)+1}:duration=first[aout]"
    
    cmd = [
        ffmpeg, "-y",
    ] + inputs + [
        "-filter_complex", filter_str,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        shutil.copy2(video_path, output_path)
    return output_path


def generate_ambient_track(output_path, duration=60, theme="documentary"):
    """Generate a simple ambient background track using FFmpeg's noise/sine generators."""
    ffmpeg = _find_ffmpeg()
    
    # Create a subtle ambient track based on theme
    themes = {
        "documentary": "sine=frequency=220:sample_rate=44100,volume=0.03,atempo=0.8",
        "war": "sine=frequency=110:sample_rate=44100,volume=0.05,atempo=1.2",
        "ocean": "anoisesrc=color=blue:sample_rate=44100,volume=0.04,lowpass=f=400",
        "space": "sine=frequency=330:sample_rate=44100,volume=0.02,atempo=0.5",
        "mystery": "sine=frequency=165:sample_rate=44100,volume=0.04,atempo=0.7",
    }
    
    audio_filter = themes.get(theme, themes["documentary"])
    
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"{audio_filter}",
        "-t", str(duration),
        "-c:a", "aac", "-b:a", "128k",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return output_path if r.returncode == 0 else None
