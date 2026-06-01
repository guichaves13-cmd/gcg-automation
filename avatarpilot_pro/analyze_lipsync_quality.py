"""
Análise quantitativa de qualidade lip sync — compara compare_musetalk.mp4 vs
compare_wav2lip.mp4 em métricas OBJETIVAS:

1. Mouth region SHARPNESS (Laplacian variance) — quanto mais nítido, melhor
2. Mouth MOVEMENT variance — boca se mexe ao longo do tempo? (lip sync ativo)
3. Face position STABILITY — face fica no mesmo lugar entre frames? (sem jitter)
4. Color/contrast metrics — média de brilho/saturação

Uso: python analyze_lipsync_quality.py
"""
import os, sys, json, subprocess, tempfile, shutil
import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None

OUTPUTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
CASCADE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "models", "haarcascade_frontalface_default.xml")

def get_cascade():
    """Carrega cascade — copia p/ temp ASCII-safe (opencv XML parser falha em path com ç)."""
    import tempfile, shutil
    temp_xml = os.path.join(tempfile.gettempdir(), "haarcascade_frontalface_default.xml")
    if not os.path.exists(temp_xml):
        # Tentar fontes em ordem
        candidates = [
            CASCADE_PATH,
            os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
                if hasattr(cv2, "data") else None,
        ]
        for src in candidates:
            if src and os.path.exists(src) and os.path.getsize(src) > 10000:
                try: shutil.copy2(src, temp_xml); break
                except: continue
    if os.path.exists(temp_xml):
        c = cv2.CascadeClassifier(temp_xml)
        if not c.empty(): return c
    return None

CASCADE = get_cascade()

def extract_frames(video_path, n_samples=10):
    """Extrai N frames uniformemente espaçados do vídeo."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened(): return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0: cap.release(); return []
    frames = []
    indices = [int(total * (i + 0.5) / n_samples) for i in range(n_samples)]
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok: frames.append(frame)
    cap.release()
    return frames

def detect_face_and_mouth_region(frame):
    """Retorna (face_bbox, mouth_bbox) ou (None, None) se não detectar."""
    if CASCADE is None: return None, None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = CASCADE.detectMultiScale(gray, 1.2, 5)
    if len(faces) == 0: return None, None
    # Maior face
    fx, fy, fw, fh = max(faces, key=lambda b: b[2]*b[3])
    # Boca ~ 2/3 inferiores do rosto, centralizada
    mx = fx + int(fw * 0.20)
    my = fy + int(fh * 0.60)
    mw = int(fw * 0.60)
    mh = int(fh * 0.30)
    return (fx, fy, fw, fh), (mx, my, mw, mh)

def laplacian_variance(img):
    """Mede nitidez — variância do laplaciano. Alto = nítido, baixo = borrado."""
    if img.size == 0: return 0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())

def analyze_video(path):
    """Retorna dict com métricas."""
    if not os.path.exists(path): return {"error": "missing"}
    print(f"\n--- {os.path.basename(path)} ({os.path.getsize(path)//1024} KB) ---")
    frames = extract_frames(path, n_samples=20)
    print(f"  frames amostrados: {len(frames)}")
    if not frames: return {"error": "no frames"}

    # Métricas globais
    avg_brightness = []
    mouth_sharpness = []
    mouth_areas = []
    face_positions = []  # (cx, cy) p/ medir estabilidade
    mouth_patches = []   # p/ medir variância de movimento

    for f in frames:
        # brightness
        avg_brightness.append(float(np.mean(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))))
        face, mouth = detect_face_and_mouth_region(f)
        if mouth is None: continue
        mx, my, mw, mh = mouth
        # Clamp
        mx, my = max(0, mx), max(0, my)
        mx2 = min(f.shape[1], mx + mw)
        my2 = min(f.shape[0], my + mh)
        mouth_crop = f[my:my2, mx:mx2]
        if mouth_crop.size == 0: continue
        mouth_sharpness.append(laplacian_variance(mouth_crop))
        # Patch normalizado p/ medir variância entre frames
        try:
            mp = cv2.resize(cv2.cvtColor(mouth_crop, cv2.COLOR_BGR2GRAY), (64, 32))
            mouth_patches.append(mp.astype(np.float32))
        except Exception: pass
        mouth_areas.append(mw * mh)
        if face is not None:
            fx, fy, fw, fh = face
            face_positions.append((fx + fw/2, fy + fh/2))

    # Variância de movimento da boca (diferença frame-a-frame)
    movement_variance = 0
    if len(mouth_patches) >= 2:
        diffs = []
        for i in range(1, len(mouth_patches)):
            d = float(np.mean(np.abs(mouth_patches[i] - mouth_patches[i-1])))
            diffs.append(d)
        movement_variance = float(np.std(diffs))

    # Estabilidade da face (desvio padrão das posições)
    face_jitter = 0
    if len(face_positions) >= 2:
        xs = [p[0] for p in face_positions]
        ys = [p[1] for p in face_positions]
        face_jitter = float(np.sqrt(np.var(xs) + np.var(ys)))

    return {
        "frames_analyzed":   len(frames),
        "faces_detected":    len(face_positions),
        "avg_brightness":    round(np.mean(avg_brightness), 1) if avg_brightness else 0,
        "mouth_sharpness":   round(np.mean(mouth_sharpness), 1) if mouth_sharpness else 0,
        "mouth_sharpness_std": round(np.std(mouth_sharpness), 1) if mouth_sharpness else 0,
        "mouth_area_avg":    int(np.mean(mouth_areas)) if mouth_areas else 0,
        "movement_variance": round(movement_variance, 3),  # alto = boca se mexe bem
        "face_jitter":       round(face_jitter, 1),        # baixo = face estável
    }


def main():
    if CASCADE is None:
        print("ERRO: Haar cascade não disponível"); sys.exit(1)
    print("=== ANALISE LIP SYNC QUALITATIVA ===")
    print(f"Cascade: {'OK' if CASCADE else 'FAIL'}")

    mu_path = os.path.join(OUTPUTS, "compare_musetalk.mp4")
    w2l_path = os.path.join(OUTPUTS, "compare_wav2lip.mp4")

    mu = analyze_video(mu_path)
    w2l = analyze_video(w2l_path)

    print("\n" + "="*70)
    print("  RESULTADO COMPARATIVO")
    print("="*70)
    print(f"\n{'Métrica':<28}{'MuseTalk':>15}{'Wav2Lip':>15}{'Vencedor':>15}")
    print("-" * 73)
    rows = [
        ("frames analisados",     mu.get("frames_analyzed"),    w2l.get("frames_analyzed"),    "—"),
        ("faces detectadas",      mu.get("faces_detected"),     w2l.get("faces_detected"),     "—"),
        ("brilho médio (0-255)",  mu.get("avg_brightness"),     w2l.get("avg_brightness"),     "—"),
        ("nitidez boca (Lapl)",   mu.get("mouth_sharpness"),    w2l.get("mouth_sharpness"),    "↑maior"),
        ("variação nitidez",      mu.get("mouth_sharpness_std"), w2l.get("mouth_sharpness_std"),"—"),
        ("área boca (px²)",       mu.get("mouth_area_avg"),     w2l.get("mouth_area_avg"),     "—"),
        ("variância movimento",   mu.get("movement_variance"),  w2l.get("movement_variance"),  "↑maior=lip sync ativo"),
        ("face jitter (px)",      mu.get("face_jitter"),        w2l.get("face_jitter"),        "↓menor=estável"),
    ]
    for name, m, w, hint in rows:
        winner = ""
        try:
            if "↑maior" in hint:
                winner = "🎯 MuseTalk" if float(m) > float(w) else "⚡ Wav2Lip" if float(w) > float(m) else "tie"
            elif "↓menor" in hint:
                winner = "🎯 MuseTalk" if float(m) < float(w) else "⚡ Wav2Lip" if float(w) < float(m) else "tie"
        except Exception: pass
        print(f"{name:<28}{str(m):>15}{str(w):>15}{winner:>15}")

    print("\n" + "="*70)
    print("  INTERPRETAÇÃO:")
    print("="*70)
    print("  • Nitidez boca: variância do Laplaciano — maior = boca mais definida")
    print("  • Variância movimento: quanto a boca muda frame-a-frame — alto = lip sync ativo")
    print("  • Face jitter: estabilidade da posição — baixo = face não 'pula'")
    print()
    # Score simples
    mu_wins = w2l_wins = 0
    if mu.get("mouth_sharpness", 0) > w2l.get("mouth_sharpness", 0): mu_wins += 1
    else: w2l_wins += 1
    if mu.get("movement_variance", 0) > w2l.get("movement_variance", 0): mu_wins += 1
    else: w2l_wins += 1
    if mu.get("face_jitter", 999) < w2l.get("face_jitter", 999): mu_wins += 1
    else: w2l_wins += 1
    print(f"  PLACAR: 🎯 MuseTalk {mu_wins} × {w2l_wins} ⚡ Wav2Lip")
    print()

if __name__ == "__main__":
    main()
