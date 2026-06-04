"""
Auditoria objetiva nível HeyGen — mede qualidade dos outputs recentes.

Métricas (todas objetivas, mensuráveis):

LIP SYNC
1. Mouth sharpness (Laplacian variance da região da boca) — >50 = nítido
2. Mouth movement variance — boca está se mexendo? (lip sync ATIVO)
3. Face position jitter (StdDev de centro de face entre frames) — <5px = estável

ÁUDIO
4. Mean loudness (dB) — alvo -16 dB (broadcast EBU R128)
5. Max peak (dB) — alvo < -1 dB (sem clipping)
6. Dynamic range — variação saudável de loudness

VÍDEO
7. Resolution + framerate
8. Bitrate (Mbps)
9. Tamanho do output (KB/segundo)

Para cada job recente, gera um relatório + scores HeyGen-grade.
"""
import os, sys, json, subprocess, glob, time
import cv2
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
CASCADE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "models", "haarcascade_frontalface_default.xml")


def get_cascade():
    import tempfile, shutil
    temp_xml = os.path.join(tempfile.gettempdir(), "haar_audit.xml")
    if not os.path.exists(temp_xml):
        try:
            shutil.copy(CASCADE_PATH, temp_xml)
        except Exception:
            return None
    casc = cv2.CascadeClassifier(temp_xml)
    return casc if not casc.empty() else None


def measure_video(path: str) -> dict:
    """Mede métricas de lip sync + video em um arquivo."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"error": f"cannot open {path}"}
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if total <= 0:
        cap.release()
        return {"error": "no frames"}

    cascade = get_cascade()
    if cascade is None:
        cap.release()
        return {"error": "no cascade"}

    # Amostra 20 frames espalhados
    sample_n = min(20, total)
    sample_idx = np.linspace(5, total - 5, sample_n).astype(int)
    mouth_sharpness_vals = []
    mouth_intensity_vals = []
    face_centers = []

    for idx in sample_idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok: continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.2, 5)
        if len(faces) == 0: continue
        # Face maior
        fx, fy, fw, fh = max(faces, key=lambda d: d[2] * d[3])
        # Mouth region: parte inferior da face (60% bottom, central 60% width)
        mx0 = fx + int(fw * 0.20)
        mx1 = fx + int(fw * 0.80)
        my0 = fy + int(fh * 0.60)
        my1 = fy + fh
        if mx1 > mx0 and my1 > my0:
            mouth = gray[my0:my1, mx0:mx1]
            if mouth.size > 100:
                # Sharpness via Laplacian
                lap = cv2.Laplacian(mouth, cv2.CV_64F)
                mouth_sharpness_vals.append(float(lap.var()))
                # Intensidade média (proxy de boca aberta/fechada)
                mouth_intensity_vals.append(float(np.mean(mouth)))
        # Face center (pra detectar jitter)
        face_centers.append((fx + fw / 2, fy + fh / 2))

    cap.release()

    if not mouth_sharpness_vals:
        return {"error": "no mouth samples (cascade not detecting face)"}

    sharpness_avg = float(np.mean(mouth_sharpness_vals))
    sharpness_std = float(np.std(mouth_sharpness_vals))
    intensity_var = float(np.var(mouth_intensity_vals)) if len(mouth_intensity_vals) > 1 else 0.0

    # Jitter normalizado pelo tamanho do frame (% do frame). Antes era em px
    # absoluto e bagunçava em outputs portrait (1080x1920 vs landscape 1920x1080).
    if len(face_centers) > 1:
        xs = [c[0] for c in face_centers]
        ys = [c[1] for c in face_centers]
        jitter_x = float(np.std(xs)) / max(1, w) * 100  # % do frame width
        jitter_y = float(np.std(ys)) / max(1, h) * 100  # % do frame height
    else:
        jitter_x = jitter_y = 0.0

    # Bitrate / file info
    fsize = os.path.getsize(path)
    duration = total / fps if fps > 0 else 0
    bitrate_kbps = (fsize * 8 / 1024 / duration) if duration > 0 else 0

    return {
        "resolution": f"{w}x{h}",
        "fps": round(fps, 1),
        "duration_s": round(duration, 1),
        "frames": total,
        "size_mb": round(fsize / 1024 / 1024, 1),
        "bitrate_mbps": round(bitrate_kbps / 1024, 2),
        "mouth_sharpness_avg": round(sharpness_avg, 1),
        "mouth_sharpness_std": round(sharpness_std, 1),
        "mouth_intensity_var": round(intensity_var, 2),
        "face_jitter_x_px": round(jitter_x, 2),
        "face_jitter_y_px": round(jitter_y, 2),
        "samples_with_face": len(face_centers),
    }


def measure_audio(path: str) -> dict:
    """Mede loudness do audio do video."""
    try:
        r = subprocess.run([
            "ffmpeg", "-y", "-i", path, "-af", "volumedetect",
            "-vn", "-f", "null", "-"
        ], capture_output=True, text=True, timeout=30)
        out = r.stderr
        mean = None; peak = None
        for line in out.split("\n"):
            if "mean_volume:" in line:
                try: mean = float(line.split("mean_volume:")[1].split("dB")[0].strip())
                except: pass
            if "max_volume:" in line:
                try: peak = float(line.split("max_volume:")[1].split("dB")[0].strip())
                except: pass
        return {"mean_db": mean, "peak_db": peak}
    except Exception as e:
        return {"error": str(e)}


def score_heygen(metrics: dict) -> dict:
    """Pontua cada métrica em escala 0-10 (10 = HeyGen-level)."""
    scores = {}
    # Lip sync sharpness — HeyGen: ~80-150. Wav2Lip raw: ~50-100. Score linear.
    s = metrics.get("mouth_sharpness_avg", 0)
    scores["sharpness"] = min(10, max(0, s / 12))

    # Mouth movement (lip sync ativo) — HeyGen: var>5. >10 muito ativo.
    v = metrics.get("mouth_intensity_var", 0)
    scores["lip_sync_activity"] = min(10, max(0, v / 2))

    # Jitter — HeyGen: <2% do frame. >5% = instável.
    j = max(metrics.get("face_jitter_x_px", 0), metrics.get("face_jitter_y_px", 0))
    if j < 1.5:    scores["face_stability"] = 10
    elif j < 3:    scores["face_stability"] = 8
    elif j < 5:    scores["face_stability"] = 5
    elif j < 10:   scores["face_stability"] = 3
    else:          scores["face_stability"] = 1

    # Audio loudness — HeyGen: -16 dB ± 2.
    m = metrics.get("audio", {}).get("mean_db")
    if m is None:
        scores["audio_loudness"] = 0
    else:
        delta = abs(m - (-16))
        scores["audio_loudness"] = max(0, 10 - delta)

    # Peak < -1 dB (no clipping)
    p = metrics.get("audio", {}).get("peak_db")
    if p is None:
        scores["audio_no_clip"] = 0
    elif p < -3:    scores["audio_no_clip"] = 10
    elif p < -1.5:  scores["audio_no_clip"] = 8
    elif p < -0.5:  scores["audio_no_clip"] = 5
    else:           scores["audio_no_clip"] = 2

    # Resolution
    res = metrics.get("resolution", "")
    if "1920x1080" in res or "1080x1920" in res or "1080x1080" in res:
        scores["resolution"] = 10
    elif "1280x720" in res:
        scores["resolution"] = 7
    else:
        scores["resolution"] = 4

    # Bitrate (>= 2 Mbps = HeyGen-equivalent)
    b = metrics.get("bitrate_mbps", 0)
    scores["bitrate"] = min(10, max(0, b * 2))

    # Overall HeyGen-level score (média ponderada)
    weights = {
        "sharpness":         0.20,
        "lip_sync_activity": 0.25,  # mais importante
        "face_stability":    0.15,
        "audio_loudness":    0.15,
        "audio_no_clip":     0.10,
        "resolution":        0.10,
        "bitrate":           0.05,
    }
    overall = sum(scores[k] * w for k, w in weights.items())
    scores["OVERALL_HEYGEN_SCORE"] = round(overall, 2)
    return scores


def main():
    # Selecionar últimos 5 _final.mp4 (recentes têm features novas)
    finals = sorted(glob.glob(os.path.join(OUTPUTS_DIR, "*_final.mp4")),
                    key=os.path.getmtime, reverse=True)[:5]
    if not finals:
        print("Nenhum *_final.mp4 encontrado.")
        sys.exit(1)
    print(f"=== AUDITORIA OBJETIVA HEYGEN-LEVEL ===")
    print(f"Analisando {len(finals)} outputs mais recentes\n")
    results = []
    for path in finals:
        name = os.path.basename(path)
        print(f"--- {name} ---")
        t0 = time.time()
        m = measure_video(path)
        if "error" in m:
            print(f"  [ERR] {m['error']}\n")
            results.append((name, None, None))
            continue
        m["audio"] = measure_audio(path)
        scores = score_heygen(m)
        elapsed = time.time() - t0
        print(f"  Resolution: {m['resolution']} @ {m['fps']}fps | dur={m['duration_s']}s | bitrate={m['bitrate_mbps']}Mbps")
        print(f"  Mouth sharpness: avg={m['mouth_sharpness_avg']} std={m['mouth_sharpness_std']}")
        print(f"  Lip movement variance: {m['mouth_intensity_var']}")
        print(f"  Face jitter: x={m['face_jitter_x_px']}px y={m['face_jitter_y_px']}px")
        a = m.get("audio", {})
        print(f"  Audio: mean={a.get('mean_db')}dB peak={a.get('peak_db')}dB")
        print(f"  --- SCORES (0-10) ---")
        for k in ["sharpness","lip_sync_activity","face_stability","audio_loudness","audio_no_clip","resolution","bitrate"]:
            print(f"    {k:25s}: {scores[k]:.1f}")
        print(f"    {'OVERALL HEYGEN SCORE':25s}: {scores['OVERALL_HEYGEN_SCORE']:.2f}/10")
        print(f"  [{elapsed:.1f}s]\n")
        results.append((name, m, scores))

    # Resumo final
    print("=" * 70)
    print(f"{'Job':<22}{'Sharp':>8}{'LipMov':>8}{'Jitter':>8}{'Audio':>8}{'OVERALL':>10}")
    print("-" * 70)
    for name, m, s in results:
        if s is None:
            print(f"{name[:20]:<22}    ERROR")
            continue
        print(f"{name[:20]:<22}{s['sharpness']:>8.1f}{s['lip_sync_activity']:>8.1f}"
              f"{s['face_stability']:>8.1f}{s['audio_loudness']:>8.1f}{s['OVERALL_HEYGEN_SCORE']:>10.2f}")

    # Médias gerais
    valid = [s for _, _, s in results if s is not None]
    if valid:
        avg_overall = sum(s["OVERALL_HEYGEN_SCORE"] for s in valid) / len(valid)
        avg_lip = sum(s["lip_sync_activity"] for s in valid) / len(valid)
        avg_sharp = sum(s["sharpness"] for s in valid) / len(valid)
        avg_stab = sum(s["face_stability"] for s in valid) / len(valid)
        print("\n" + "=" * 70)
        print(f"MÉDIAS ({len(valid)} jobs):")
        print(f"  Lip sync activity: {avg_lip:.2f}/10  {'✅ HeyGen-level' if avg_lip >= 7 else '⚠ menor que HeyGen' if avg_lip >= 5 else '❌ abaixo'}")
        print(f"  Sharpness:         {avg_sharp:.2f}/10  {'✅ HeyGen-level' if avg_sharp >= 7 else '⚠ moderado' if avg_sharp >= 5 else '❌ baixo'}")
        print(f"  Face stability:    {avg_stab:.2f}/10  {'✅ HeyGen-level' if avg_stab >= 7 else '⚠ tem jitter' if avg_stab >= 5 else '❌ instável'}")
        print(f"  OVERALL:           {avg_overall:.2f}/10  {'✅ HEYGEN-LEVEL' if avg_overall >= 7 else '⚠ próximo' if avg_overall >= 5 else '❌ longe ainda'}")


if __name__ == "__main__":
    main()
