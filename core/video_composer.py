"""
Video Composer — assembly engine for IntelligentBrollEngine outputs.

Three composition modes:
1. tts_only       — TTS audio + B-roll timeline, full screen
2. avatar_corner  — Avatar persona in 20% corner (PIP), B-roll fills rest, audio from avatar
3. avatar_fullbg  — Avatar full-screen with B-roll briefly cutting in (HeyGen style)

All modes support karaoke subtitles burn-in via core.karaoke_subtitles.
"""

import os
import subprocess
from pathlib import Path


def _ffprobe_duration(path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", path]
    return float(subprocess.run(cmd, capture_output=True, text=True).stdout.strip() or 0)


def _build_broll_timeline(plans, work_dir: Path, audio_dur: float,
                          width: int = 1920, height: int = 1080, fps: int = 30) -> str:
    """Normalize each segment's clip + concat into a single timeline video.

    Anti-repetition gap-fill:
    - Collect ALL resolved clips into a pool
    - When a segment has no clip, pull from pool with cycling that avoids
      reusing the SAME clip back-to-back or twice in a row
    - As a last resort, use the closest-temporal clip
    """
    # Build pool of all resolved clip paths (preserves order = relevance order)
    resolved_pool = [p.download_path for p in plans
                    if p.clip and p.download_path and os.path.exists(p.download_path)]
    if not resolved_pool:
        raise RuntimeError("No clips resolved")

    timeline = []
    # Cycle index for unresolved segments
    pool_idx = 0
    last_used_paths = []   # track what we've shown to enforce diversity

    for p in plans:
        if p.clip and p.download_path and os.path.exists(p.download_path):
            src = p.download_path
        else:
            # GAP-FILL: pick a clip from the pool that wasn't used in last 2 segments
            candidates = [c for c in resolved_pool if c not in last_used_paths[-2:]]
            if not candidates:
                # All recent clips already shown — fall back to oldest
                candidates = resolved_pool
            src = candidates[pool_idx % len(candidates)]
            pool_idx += 1

        timeline.append((p.intent.start, p.intent.end, src))
        last_used_paths.append(src)

    if not timeline:
        raise RuntimeError("No clips resolved")
    if timeline[0][0] > 0.1:
        timeline.insert(0, (0.0, timeline[0][0], timeline[0][2]))
    if timeline[-1][1] < audio_dur - 0.1:
        s, e, src = timeline[-1]
        timeline[-1] = (s, audio_dur, src)

    normalized = []
    for i, (start, end, src) in enumerate(timeline):
        seg_dur = max(0.5, end - start)
        norm = work_dir / f"seg_{i:03d}.mp4"

        # Alternate zoom direction per segment for visual variety:
        # even = slow zoom-in, odd = slow zoom-out → keeps eyes engaged
        if i % 2 == 0:
            zoom_filter = f"zoompan=z='min(zoom+0.0008,1.15)':d={int(seg_dur*fps)}:s={width}x{height}:fps={fps}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        else:
            zoom_filter = f"zoompan=z='if(eq(on,0),1.15,max(1.0,zoom-0.0008))':d={int(seg_dur*fps)}:s={width}x{height}:fps={fps}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"

        # Scale up 2× before zoompan for higher quality result
        cmd = [
            "ffmpeg", "-y", "-i", src, "-t", f"{seg_dur:.2f}",
            "-vf", (
                f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,"
                f"crop={width*2}:{height*2},"
                f"{zoom_filter},"
                f"setsar=1,format=yuv420p"
            ),
            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            str(norm),
        ]
        subprocess.run(cmd, capture_output=True, timeout=180)
        if norm.exists() and norm.stat().st_size > 10000:
            normalized.append(str(norm))

    if not normalized:
        raise RuntimeError("All segments failed to normalize")

    concat_list = work_dir / "concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for c in normalized:
            f.write(f"file '{str(Path(c).resolve()).replace(chr(92), '/')}'\n")

    raw = work_dir / "broll_timeline.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(raw),
    ], capture_output=True, timeout=180, check=True)
    return str(raw)


def compose_tts_only(plans, audio_path: str, output_path: str,
                     width: int = 1920, height: int = 1080, fps: int = 30) -> str:
    """Mode 1: B-roll full screen + TTS audio overlay."""
    work = Path(output_path).parent / "_compose_work"
    work.mkdir(exist_ok=True)
    audio_dur = _ffprobe_duration(audio_path)
    broll = _build_broll_timeline(plans, work, audio_dur, width, height, fps)

    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", broll,
        "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0",
        "-t", f"{audio_dur:.2f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-pix_fmt", "yuv420p",
        output_path,
    ], capture_output=True, timeout=300, check=True)
    return output_path


def compose_avatar_corner(plans, avatar_video: str, output_path: str,
                          corner: str = "top_right",
                          avatar_scale: float = 0.25,
                          add_shadow: bool = True,
                          add_border: bool = True,
                          width: int = 1920, height: int = 1080, fps: int = 30) -> str:
    """Mode 2: Avatar persona ANCHORED in corner overlay (covers ~20-25% of screen).

    Avatar is positioned flush against the corner edge (small ~12px gap) and is
    sized to be clearly visible — not a tiny floating PIP. This is the
    "broadcast" or "explainer" style: persona always present in fixed corner,
    B-roll behind illustrates what they're saying.

    Args:
        avatar_video: path to MP4 with persona narrating (audio used as track).
        corner: 'top_right', 'top_left', 'bottom_right', 'bottom_left'.
        avatar_scale: fraction of width (0.25 = avatar is 25% wide → covers
                      ~14% of screen area). For mobile/vertical use 0.30.
    """
    work = Path(output_path).parent / "_compose_work"
    work.mkdir(exist_ok=True)

    avatar_dur = _ffprobe_duration(avatar_video)
    broll = _build_broll_timeline(plans, work, avatar_dur, width, height, fps)

    # Compute avatar size + position
    a_w = int(width * avatar_scale)
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0", avatar_video]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    try:
        aw, ah = out.split(",")[:2]
        avatar_aspect = float(aw) / float(ah)
    except Exception:
        avatar_aspect = 16 / 9
    a_h = int(a_w / avatar_aspect)

    # Position FLUSH against corner — small gap only (12px) for visual breathing.
    # Margin from screen edge — tight against the corner so it reads as "anchored".
    edge_gap = 12
    if corner == "top_right":
        ax, ay = width - a_w - edge_gap, edge_gap
    elif corner == "top_left":
        ax, ay = edge_gap, edge_gap
    elif corner == "bottom_right":
        ax, ay = width - a_w - edge_gap, height - a_h - edge_gap
    else:  # bottom_left
        ax, ay = edge_gap, height - a_h - edge_gap

    # Build filter graph — avatar gets thicker white border + drop shadow for
    # clear visual separation from B-roll.
    avatar_chain = f"[1:v]scale={a_w}:{a_h}:flags=lanczos,format=yuva420p"
    if add_border:
        # Thicker frame: 6px dark outer + 3px white inner = "broadcast" look
        avatar_chain += f",pad=w=iw+12:h=ih+12:x=6:y=6:color=black@0.75"
        avatar_chain += f",pad=w=iw+6:h=ih+6:x=3:y=3:color=white@0.95"
    avatar_chain += "[avatar]"

    if add_shadow:
        # Larger, softer shadow for depth
        shadow_w = a_w + 30
        shadow_h = a_h + 30
        filtergraph = (
            f"color=c=black@0.6:s={shadow_w}x{shadow_h}:d={avatar_dur}[shadow];"
            f"{avatar_chain};"
            f"[0:v][shadow]overlay={ax+10}:{ay+10}:enable='gte(t,0)'[bg];"
            f"[bg][avatar]overlay={ax}:{ay}:enable='gte(t,0)'[v]"
        )
    else:
        filtergraph = (
            f"{avatar_chain};"
            f"[0:v][avatar]overlay={ax}:{ay}:enable='gte(t,0)'[v]"
        )

    subprocess.run([
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", broll,   # input 0: broll (looped if shorter than avatar)
        "-i", avatar_video,                   # input 1: avatar
        "-filter_complex", filtergraph,
        "-map", "[v]", "-map", "1:a:0",       # video from composite, audio from avatar
        "-t", f"{avatar_dur:.2f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-pix_fmt", "yuv420p",
        output_path,
    ], capture_output=True, timeout=600, check=True)
    return output_path


def compose_avatar_fullbg(plans, avatar_video: str, output_path: str,
                          broll_cutaway_interval: float = 8.0,
                          broll_cutaway_duration: float = 4.0,
                          width: int = 1920, height: int = 1080, fps: int = 30) -> str:
    """Mode 3: Avatar full-screen with brief B-roll cutaways (HeyGen style).

    Every `broll_cutaway_interval` seconds, cut to B-roll for `broll_cutaway_duration`
    seconds while keeping the avatar's audio playing.
    """
    work = Path(output_path).parent / "_compose_work"
    work.mkdir(exist_ok=True)

    avatar_dur = _ffprobe_duration(avatar_video)
    broll = _build_broll_timeline(plans, work, avatar_dur, width, height, fps)

    # Build segment plan: alternate avatar and broll
    segments_plan = []
    t = 0.0
    on_avatar = True
    while t < avatar_dur:
        if on_avatar:
            seg_dur = min(broll_cutaway_interval, avatar_dur - t)
            segments_plan.append((t, t + seg_dur, "avatar"))
        else:
            seg_dur = min(broll_cutaway_duration, avatar_dur - t)
            segments_plan.append((t, t + seg_dur, "broll"))
        t += seg_dur
        on_avatar = not on_avatar

    # Cut each piece
    pieces = []
    for i, (start, end, kind) in enumerate(segments_plan):
        out = work / f"piece_{i:03d}_{kind}.mp4"
        src = avatar_video if kind == "avatar" else broll
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", src,
            "-t", f"{end - start:.2f}",
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps},format=yuv420p",
            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            str(out),
        ]
        subprocess.run(cmd, capture_output=True, timeout=180)
        if out.exists() and out.stat().st_size > 5000:
            pieces.append(str(out))

    # Concat
    concat_list = work / "fullbg_concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in pieces:
            f.write(f"file '{str(Path(p).resolve()).replace(chr(92), '/')}'\n")
    concat_video = work / "fullbg_concat.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(concat_video),
    ], capture_output=True, timeout=180, check=True)

    # Add avatar audio
    subprocess.run([
        "ffmpeg", "-y", "-i", str(concat_video), "-i", avatar_video,
        "-map", "0:v:0", "-map", "1:a:0", "-t", f"{avatar_dur:.2f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-pix_fmt", "yuv420p",
        output_path,
    ], capture_output=True, timeout=300, check=True)
    return output_path
