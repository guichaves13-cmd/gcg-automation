"""
Beat Timeline — structured JSON describing every beat (avatar + b-roll) in the final video.

Generated at the END of the pipeline so we have full traceability:
- Which narration text triggered each B-roll
- Which search terms were used
- Which source delivered the clip
- Validation score (from Gemini Vision) if available
- File path for picker/audit

Output:
    {
      "video_id": "...",
      "theme": "...",
      "language": "pt",
      "duration": 245.0,
      "total_beats": 38,
      "broll_count": 18,
      "avatar_count": 20,
      "beats": [
        {
          "id": 1,
          "type": "broll" | "avatar",
          "start": 12.0,
          "end": 19.0,
          "duration": 7.0,
          "narration_text": "omega 3 é essencial para a saúde cardiovascular",
          "search_terms": ["fish oil capsules pharmacy", "cardiovascular health"],
          "shot_type": "closeup",
          "mood": "informative",
          "source": "pexels",
          "file": "C:/.../broll_005.mp4",
          "is_image": false,
          "validation_score": 0.85,
          "status": "applied"
        },
        ...
      ]
    }
"""

import json
import os
from datetime import datetime


def build_beat_timeline(
    segments_plan: list,
    shot_list: list,
    transcription: list,
    analysis: dict,
    mapped_clips: list = None,
    output_path: str = None,
) -> dict:
    """
    Consolidate the final pipeline state into a single JSON.

    Args:
        segments_plan: output of _build_smart_timeline() (avatar + broll segs)
        shot_list:     analysis["shot_list"] (search terms, mood, shot_type)
        transcription: analysis["transcription"] (per-segment Whisper output)
        analysis:      full VideoIntelligence.analyze_video() result
        mapped_clips:  list of downloaded clips with source/score (optional)
        output_path:   if provided, dump JSON to this path

    Returns:
        dict ready to serialize to JSON
    """
    mapped_clips = mapped_clips or []

    def _narration_at(start: float, end: float) -> str:
        """Get the narration text overlapping a time range."""
        out = []
        for seg in transcription:
            seg_s = float(seg.get("start", 0))
            seg_e = float(seg.get("end", 0))
            # Any overlap counts
            if seg_e >= start and seg_s <= end:
                txt = seg.get("text", "").strip()
                if txt:
                    out.append(txt)
        return " ".join(out).strip()

    def _shot_at(start: float, end: float) -> dict:
        """Find the shot_list entry that overlaps a time range."""
        best = None
        best_overlap = 0
        for shot in shot_list:
            s = float(shot.get("start", 0))
            e = float(shot.get("end", 0))
            ov = max(0, min(e, end) - max(s, start))
            if ov > best_overlap:
                best_overlap = ov
                best = shot
        return best or {}

    def _clip_for(start: float, file_path: str) -> dict:
        """Match a segment back to its source clip entry (for source + score)."""
        if file_path:
            for c in mapped_clips:
                if c.get("file") == file_path:
                    return c
        # Fallback by timeline_start proximity
        best = None
        best_d = 999
        for c in mapped_clips:
            d = abs(c.get("timeline_start", -999) - start)
            if d < best_d and d < 2.5:
                best_d = d
                best = c
        return best or {}

    beats = []
    broll_count = 0
    avatar_count = 0

    for i, seg in enumerate(segments_plan):
        start = float(seg.get("start", 0))
        duration = float(seg.get("duration", 0))
        end = start + duration

        narration = _narration_at(start, end)
        shot = _shot_at(start, end)

        beat = {
            "id": i + 1,
            "type": seg.get("type", "avatar"),
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": round(duration, 2),
            "narration_text": narration[:500],
            "search_terms": shot.get("search_terms", []),
            "shot_type": seg.get("shot_type") or shot.get("shot_type", ""),
            "mood": shot.get("mood", ""),
            "status": "applied",
        }

        if seg.get("type") == "broll":
            broll_count += 1
            f = seg.get("file", "")
            clip_meta = _clip_for(start, f)
            beat["file"] = f
            beat["is_image"] = bool(seg.get("is_image", False))
            beat["source"] = clip_meta.get("source", "")
            beat["keyword"] = seg.get("keyword") or clip_meta.get("keyword", "")
            # validation_score may have been attached by Phase 2 (future: store it on the clip)
            if "validation_score" in clip_meta:
                beat["validation_score"] = clip_meta["validation_score"]
        else:
            avatar_count += 1

        beats.append(beat)

    timeline = {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "video_id": analysis.get("video_id", ""),
        "theme": analysis.get("theme", ""),
        "language": analysis.get("language", ""),
        "target_audience": analysis.get("target_audience", ""),
        "visual_style": analysis.get("visual_style", ""),
        "duration": round(float(analysis.get("duration", 0)), 2),
        "total_beats": len(beats),
        "broll_count": broll_count,
        "avatar_count": avatar_count,
        "beats": beats,
    }

    if output_path:
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(timeline, f, indent=2, ensure_ascii=False)
            print(f"  [beat_timeline] Saved: {output_path}")
        except Exception as e:
            print(f"  [beat_timeline] Save failed: {e}")

    return timeline


def load_beat_timeline(path: str) -> dict:
    """Load a previously-saved beat_timeline.json (used by the picker)."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def summarize_beat_timeline(timeline: dict) -> str:
    """Plain-text summary for console output."""
    lines = []
    lines.append(f"Beat Timeline — {timeline.get('theme','?')} ({timeline.get('language','?')})")
    lines.append(
        f"  {timeline.get('total_beats',0)} beats | "
        f"{timeline.get('avatar_count',0)} avatar | "
        f"{timeline.get('broll_count',0)} B-roll | "
        f"{timeline.get('duration',0):.0f}s"
    )
    for b in timeline.get("beats", []):
        if b["type"] != "broll":
            continue
        terms = ", ".join(b.get("search_terms", [])[:2]) or "?"
        src = b.get("source") or "?"
        score = b.get("validation_score")
        score_s = f" v={score:.2f}" if score is not None else ""
        lines.append(
            f"    [{b['start']:6.1f}s-{b['end']:6.1f}s] {b['shot_type']:8s} "
            f"src={src:8s}{score_s}  {terms[:60]}"
        )
    return "\n".join(lines)
