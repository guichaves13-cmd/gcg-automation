"""
Audit completo do DEMO 5-min — métricas objetivas + relatório pronto pra usuário.
"""
import sys, os, subprocess, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

JOB_ID = sys.argv[1] if len(sys.argv) > 1 else "8007047d307a"
PATH = f"outputs/{JOB_ID}_final.mp4"

if not os.path.exists(PATH):
    print(f"ERRO: {PATH} ainda não existe.")
    sys.exit(1)

from audit_heygen_quality import measure_video, measure_audio, score_heygen

print("=" * 64)
print(f"  AUDIT DEMO 5-MIN — job {JOB_ID}")
print("=" * 64)

m = measure_video(PATH)
m["audio"] = measure_audio(PATH)
s = score_heygen(m)

print(f"\nVÍDEO")
print(f"  Resolution:    {m['resolution']} @ {m['fps']}fps")
print(f"  Duration:      {m['duration_s']}s ({m['duration_s']/60:.1f} min)")
print(f"  Frames:        {m['frames']}")
print(f"  File size:     {m['size_mb']} MB")
print(f"  Bitrate:       {m['bitrate_mbps']} Mbps")

print(f"\nLIP SYNC")
print(f"  Mouth sharpness:      {m['mouth_sharpness_avg']:.1f} (baseline 16.4)")
print(f"  Mouth sharpness std:  {m['mouth_sharpness_std']:.1f}")
print(f"  Lip movement var:     {m['mouth_intensity_var']:.1f} (HeyGen target >100)")

print(f"\nFACE STABILITY")
print(f"  Jitter X (%frame):    {m['face_jitter_x_px']:.2f}%")
print(f"  Jitter Y (%frame):    {m['face_jitter_y_px']:.2f}%")
print(f"  Samples with face:    {m['samples_with_face']}/20")

print(f"\nÁUDIO")
print(f"  Mean (dB):  {m['audio']['mean_db']}")
print(f"  Peak (dB):  {m['audio']['peak_db']}  ({'safe' if m['audio']['peak_db'] < -3 else 'warning'})")

print(f"\nSCORES (0-10)")
for k in ["sharpness", "lip_sync_activity", "face_stability",
          "audio_loudness", "audio_no_clip", "resolution", "bitrate"]:
    bar = "#" * int(s[k])
    print(f"  {k:25s}: {s[k]:.1f}  [{bar:<10}]")
print(f"  {'OVERALL HEYGEN SCORE':25s}: {s['OVERALL_HEYGEN_SCORE']:.2f}/10")

print("\n" + "=" * 64)
b = 16.4
print(f"  Mouth sharpness vs baseline: +{(m['mouth_sharpness_avg']-b)/b*100:.0f}%")
print(f"  Overall: {s['OVERALL_HEYGEN_SCORE']:.2f}/10 "
      f"({'HEYGEN-LEVEL' if s['OVERALL_HEYGEN_SCORE'] >= 7 else 'BELOW'})")
print("=" * 64)
