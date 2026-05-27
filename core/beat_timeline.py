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
        """Find the shot_list entry for a time range.

        Priority:
        1. Exact V2 match: shot whose `start` is within 1.5s of segment start
           (shot list built per-chunk so starts are narration-anchored).
        2. Best overlap: largest time overlap with the segment window.
        3. Empty dict as safe fallback.
        """
        exact_best = None
        exact_dist = 999.0
        overlap_best = None
        overlap_best_ov = 0.0
        for shot in shot_list:
            s = float(shot.get("start", 0))
            e = float(shot.get("end", 0))
            # Priority 1: proximity of shot start to segment start
            dist = abs(s - start)
            if dist < exact_dist:
                exact_dist = dist
                exact_best = shot
            # Priority 2: overlap area
            ov = max(0.0, min(e, end) - max(s, start))
            if ov > overlap_best_ov:
                overlap_best_ov = ov
                overlap_best = shot
        # Use exact match if within 1.5s, otherwise use overlap
        if exact_best is not None and exact_dist <= 1.5:
            return exact_best
        return overlap_best or {}

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
        if not isinstance(seg, dict):
            # Skip None / lists / strings — defensive against malformed inputs
            continue
        try:
            start = float(seg.get("start", 0) or 0)
            duration = float(seg.get("duration", 0) or 0)
        except (TypeError, ValueError):
            continue
        # Reject pathological values (inf / nan / negative duration)
        import math
        if not (math.isfinite(start) and math.isfinite(duration)):
            continue
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
            # V2 sync: planned timestamp from _build_smart_timeline
            if seg.get("v2_planned_start") is not None:
                beat["v2_planned_start"] = seg["v2_planned_start"]
                beat["sync_drift_seconds"] = round(abs(start - seg["v2_planned_start"]), 2)
            # validation_score attached by Phase 2 (Gemini Vision post-download)
            if "validation_score" in clip_meta:
                beat["validation_score"] = clip_meta["validation_score"]
        else:
            avatar_count += 1

        beats.append(beat)

    # ── Sync Report: V2 planned vs actual placement ───────────────────────
    # Each B-roll segment may carry `v2_planned_start` (set by _build_smart_timeline).
    # We measure drift and emit a report so the picker and auditor can flag
    # beats that drifted more than 2s from the V2 plan.
    sync_issues = []
    drift_values = []
    for b in beats:
        if b["type"] != "broll":
            continue
        v2_planned = b.get("v2_planned_start")
        if v2_planned is not None:
            drift = round(abs(b["start"] - v2_planned), 2)
            drift_values.append(drift)
            if drift > 2.0:
                sync_issues.append({
                    "beat_id": b["id"],
                    "keyword": b.get("keyword", ""),
                    "planned_start": v2_planned,
                    "actual_start": b["start"],
                    "drift_seconds": drift,
                })
    avg_drift = round(sum(drift_values) / len(drift_values), 2) if drift_values else 0.0
    max_drift = round(max(drift_values), 2) if drift_values else 0.0
    sync_ok = max_drift <= 2.0

    sync_report = {
        "measured_beats": len(drift_values),
        "avg_drift_seconds": avg_drift,
        "max_drift_seconds": max_drift,
        "sync_ok": sync_ok,
        "threshold_seconds": 2.0,
        "beats_with_drift": sync_issues,
    }
    if not sync_ok:
        print(f"  [beat_timeline] ⚠ Sync drift detected: max={max_drift}s avg={avg_drift}s "
              f"({len(sync_issues)} beat(s) >2s off V2 plan)")
    else:
        print(f"  [beat_timeline] ✓ Sync OK: max_drift={max_drift}s avg={avg_drift}s "
              f"({len(drift_values)} B-roll beats measured)")

    timeline = {
        "schema_version": "1.1",
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
        "sync_report": sync_report,
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
