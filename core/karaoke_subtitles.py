"""
Karaoke-style subtitles generator using Whisper word-level timestamps.

Produces an ASS file with per-word highlighting (current word colored yellow,
others white), TikTok/Reels style. Then burns into video via ffmpeg.
"""

import os
import subprocess
from pathlib import Path


# ASS template — styles for narration captions
_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat,64,&H00FFFFFF,&H00FFFF00,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,2,2,80,80,140,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _t(seconds: float) -> str:
    """Format seconds as H:MM:SS.cc (ASS time format)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_karaoke_ass(words: list, output_path: str,
                       words_per_line: int = 6) -> str:
    """Build an ASS file with karaoke-style word-by-word highlighting.

    Args:
        words: list[dict] {start, end, text} — Whisper word-level output.
        output_path: where to write the .ass file.
        words_per_line: how many words to show on screen at once.

    Returns: path to .ass file.
    """
    # Group words into "lines" of N words
    lines = []
    current_line = []
    for w in words:
        text = (w.get("text") or "").strip()
        if not text:
            continue
        current_line.append(w)
        if len(current_line) >= words_per_line:
            lines.append(current_line)
            current_line = []
    if current_line:
        lines.append(current_line)

    events = []
    for line_words in lines:
        line_start = line_words[0]["start"]
        line_end = line_words[-1]["end"]

        # Build one Dialogue event per "current word" state
        # During each word's window: that word is highlighted yellow, others white
        for i, current_word in enumerate(line_words):
            w_start = current_word["start"]
            # Each word's "active" window ends when the next word starts
            if i + 1 < len(line_words):
                w_end = line_words[i + 1]["start"]
            else:
                w_end = current_word["end"]

            # Build text with inline color overrides
            parts = []
            for j, w in enumerate(line_words):
                clean = (w.get("text") or "").strip().replace("{", "").replace("}", "")
                if j == i:
                    # Highlighted: yellow + slight scale up (karaoke effect)
                    parts.append(r"{\c&H00FFFF&\fscx115\fscy115}" + clean + r"{\c&HFFFFFF&\fscx100\fscy100}")
                else:
                    parts.append(r"{\c&HFFFFFF&}" + clean)
            text = " ".join(parts)
            events.append(
                f"Dialogue: 0,{_t(w_start)},{_t(w_end)},Default,,0,0,0,,{text}"
            )

    content = _ASS_HEADER + "\n".join(events) + "\n"
    Path(output_path).write_text(content, encoding="utf-8")
    return output_path


def burn_subtitles(video_path: str, ass_path: str, output_path: str,
                    timeout: int = 600) -> str:
    """Burn ASS karaoke subtitles into video using ffmpeg libass filter."""
    # ffmpeg subtitles filter needs escaped path on Windows
    ass_path_abs = str(Path(ass_path).resolve()).replace("\\", "/").replace(":", r"\:")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"subtitles='{ass_path_abs}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if not os.path.exists(output_path) or os.path.getsize(output_path) < 10000:
        raise RuntimeError(f"Subtitle burn failed: {r.stderr.decode(errors='replace')[-300:]}")
    return output_path


def extract_word_timestamps(audio_path: str, language: str = "en",
                            script_text: str = "") -> list:
    """Get word-level timestamps from audio.

    If `script_text` is provided (recommended when we generated the TTS from a
    known script), we use Whisper's `initial_prompt` to anchor transcription to
    the correct vocabulary, then ALIGN the script's actual words to Whisper's
    timing. This eliminates transcription errors like "Sintelescopios" for
    "Sin telescopios" or "Tallah me" for "Ptolemy".

    Returns: list[dict] {start, end, text}.
    """
    try:
        import whisper
        model = whisper.load_model("base")

        # Use script as initial prompt to bias Whisper toward correct vocabulary
        kwargs = {
            "language": language,
            "word_timestamps": True,
            "verbose": False,
            "temperature": 0.0,  # Deterministic = less hallucination
        }
        if script_text:
            # Trim initial_prompt — Whisper has 224-token limit
            kwargs["initial_prompt"] = script_text[:800]
            kwargs["condition_on_previous_text"] = True

        result = model.transcribe(audio_path, **kwargs)

        # Collect Whisper's words with timing
        whisper_words = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                whisper_words.append({
                    "start": float(w["start"]),
                    "end": float(w["end"]),
                    "text": (w.get("word") or "").strip(),
                })

        # If no script provided, return Whisper's transcription directly
        if not script_text or not whisper_words:
            return whisper_words

        # FORCED ALIGNMENT: map script words onto Whisper's timing
        # Strategy: split script into words, distribute over Whisper's word
        # timestamps proportionally. This gives correct words + correct timing.
        return _align_script_to_timestamps(script_text, whisper_words)

    except Exception as e:
        print(f"  [karaoke] whisper failed: {e}")
        return []


def _align_script_to_timestamps(script: str, whisper_words: list) -> list:
    """Map script's words onto Whisper's word timestamps proportionally.

    Handles cases where Whisper produces more or fewer words than the script
    (due to merged words, hallucinations, or missed words).
    """
    import re
    # Tokenize script into words (preserving punctuation as part of word)
    script_tokens = re.findall(r"\S+", script.strip())
    if not script_tokens or not whisper_words:
        return whisper_words

    n_script = len(script_tokens)
    n_whisper = len(whisper_words)

    # Get total span from Whisper
    total_start = whisper_words[0]["start"]
    total_end = whisper_words[-1]["end"]
    total_dur = max(0.1, total_end - total_start)

    # Map script words proportionally based on cumulative length
    # (longer words tend to take more time)
    cum_len = 0
    total_len = sum(len(t) + 1 for t in script_tokens)
    aligned = []

    for i, token in enumerate(script_tokens):
        word_len = len(token) + 1
        start_frac = cum_len / total_len
        end_frac = (cum_len + word_len) / total_len

        # Use Whisper's timing as anchor when available, fall back to proportion
        if n_whisper >= n_script:
            # Use exact Whisper timing of nearest word
            idx = min(int(i * n_whisper / n_script), n_whisper - 1)
            start = whisper_words[idx]["start"]
            end = whisper_words[idx]["end"]
        else:
            # Proportional fallback
            start = total_start + start_frac * total_dur
            end = total_start + end_frac * total_dur

        aligned.append({
            "start": start,
            "end": end,
            "text": token,
        })
        cum_len += word_len

    # Smooth out timings: end of word N = start of word N+1
    for i in range(len(aligned) - 1):
        if aligned[i]["end"] > aligned[i + 1]["start"]:
            mid = (aligned[i]["end"] + aligned[i + 1]["start"]) / 2
            aligned[i]["end"] = mid
            aligned[i + 1]["start"] = mid

    return aligned


def add_karaoke_to_video(video_path: str, audio_path: str,
                         output_path: str, language: str = "en",
                         words_per_line: int = 6,
                         script_text: str = "") -> str:
    """One-shot: extract timestamps, build ASS, burn into video.

    When `script_text` is provided, uses forced alignment to ensure CORRECT
    words appear in subtitles (eliminates Whisper transcription errors).
    """
    words = extract_word_timestamps(audio_path, language=language,
                                     script_text=script_text)
    if not words:
        print(f"  [karaoke] no words extracted — skipping subtitle burn")
        return video_path

    work = Path(output_path).parent / "_karaoke_work"
    work.mkdir(exist_ok=True)
    ass_path = work / "karaoke.ass"
    build_karaoke_ass(words, str(ass_path), words_per_line=words_per_line)
    src_method = "forced-aligned" if script_text else "whisper-transcribed"
    print(f"  [karaoke] {len(words)} words ({src_method}) → ASS file "
          f"({ass_path.stat().st_size} bytes)")

    burn_subtitles(video_path, str(ass_path), output_path)
    return output_path
