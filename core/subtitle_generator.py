"""
Subtitle Generator
Uses OpenAI Whisper to transcribe audio and generate SRT subtitle files.
"""

import os
import math
import threading
import time


def transcribe_audio(audio_path: str, language: str = "en", model_size: str = "base", timeout_sec: int = 1800) -> list:
    """
    Transcribe audio using Whisper and return list of segments.
    Each segment: {"start": float, "end": float, "text": str}

    Args:
        timeout_sec: max seconds to wait for transcription. Raises TimeoutError if exceeded.
    """
    try:
        import whisper
    except ImportError:
        raise ImportError(
            "OpenAI Whisper is required. Install with: pip install openai-whisper"
        )

    # Try GPU first for ~10x speed; fall back to CPU
    device = "cuda"
    try:
        import torch
        if not torch.cuda.is_available():
            device = "cpu"
    except ImportError:
        device = "cpu"

    print(f"  [Whisper] Loading model '{model_size}' on {device}...")
    model = whisper.load_model(model_size, device=device)

    print(f"  [Whisper] Transcribing audio ({language or 'auto-detect'})...")
    if device == "cpu":
        print(f"    Note: CPU only (no CUDA torch). For 10x speed, install: pip install torch --index-url https://download.pytorch.org/whl/cu121")

    transcribe_opts = {
        "task": "transcribe",
        "verbose": False,
        "fp16": (device == "cuda"),  # FP16 only on GPU
    }
    if language:
        transcribe_opts["language"] = language

    result_container = []
    error_container = []
    heartbeat_printed = [False]

    def _run():
        try:
            r = model.transcribe(audio_path, **transcribe_opts)
            result_container.append(r)
        except Exception as e:
            error_container.append(e)

    def _heartbeat():
        intervals = [60, 120, 300, 600]
        for t in intervals:
            time.sleep(t)
            if result_container or error_container:
                return
            if not heartbeat_printed[0]:
                print(f"    [Whisper] Still transcribing... ({t // 60} min elapsed)")
        if not result_container and not error_container:
            print(f"    [Whisper] Still working... (long video detected)")

    heartbeat = threading.Thread(target=_heartbeat, daemon=True)
    heartbeat.start()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)

    if thread.is_alive():
        raise TimeoutError(
            f"Whisper transcription timed out after {timeout_sec}s. "
            "Your video may be too long or your system may be slow. "
            "Try a shorter video or use a GPU."
        )

    if error_container:
        raise error_container[0]

    result = result_container[0]
    detected = result.get("language", language or "?")
    print(f"  [Whisper] Detected language: {detected}")

    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
        })

    print(f"  [Whisper] Transcribed {len(segments)} segments.")
    return segments


def format_srt_time(seconds: float) -> str:
    """Convert seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - math.floor(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(segments: list, output_path: str, max_chars_per_line: int = 42):
    """
    Generate SRT subtitle file from transcribed segments.
    Splits long lines for readability.
    """
    srt_lines = []
    index = 1

    for seg in segments:
        text = seg["text"]
        start = format_srt_time(seg["start"])
        end = format_srt_time(seg["end"])

        # Split long text into two lines
        if len(text) > max_chars_per_line:
            words = text.split()
            mid = len(words) // 2
            line1 = " ".join(words[:mid])
            line2 = " ".join(words[mid:])
            text = f"{line1}\n{line2}"

        srt_lines.append(f"{index}")
        srt_lines.append(f"{start} --> {end}")
        srt_lines.append(text)
        srt_lines.append("")
        index += 1

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))

    print(f"  [Subtitles] Generated SRT with {index - 1} entries: {output_path}")
    return output_path


def generate_subtitles(audio_path: str, output_srt_path: str,
                       language: str = "en", model_size: str = "base") -> str:
    """Full pipeline: transcribe audio → generate SRT file."""
    segments = transcribe_audio(audio_path, language, model_size)
    return generate_srt(segments, output_srt_path)
