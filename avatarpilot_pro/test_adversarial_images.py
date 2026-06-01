"""
Suite adversarial — testa o pipeline com imagens DIFÍCEIS reais:

1. Original (baseline) — controle
2. Rotacionada 45° — face oblíqua
3. Rotacionada 90° — face deitada
4. Face minúscula (5% do frame) — desafio detection
5. Face gigante (zoom >80%) — desafio crop
6. Heavy darkening (gamma 0.3) — sub-exposição
7. Heavy brightening (gamma 3.0) — super-exposição
8. Grayscale — preto-e-branco
9. Espelho horizontal — orientação invertida
10. Imagem MUITO pequena (200x200 upscaled) — baixa res

Para cada: submete via /api/generate, captura HTTP status, primeira mensagem de
progresso (indica path escolhido), e cancela em seguida pra liberar GPU.
Reporta tabela com resultado.

Uso: python test_adversarial_images.py
"""
import os, sys, time, io, tempfile, requests
import cv2
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:5052"
UP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
TMP = tempfile.mkdtemp(prefix="adv_test_")

def best_image():
    b, bs = None, 0
    for f in os.listdir(UP):
        if f.endswith((".jpg", ".jpeg")):
            p = os.path.join(UP, f); s = os.path.getsize(p)
            if 30000 < s < 600000 and s > bs: bs = s; b = p
    return b

SRC = best_image()
if not SRC:
    print("Nenhuma imagem-base encontrada"); sys.exit(1)
print(f"=== Suite Adversarial ===\nImagem-base: {os.path.basename(SRC)} ({os.path.getsize(SRC)//1024} KB)\n")

# cv2.imread falha em paths com ç (Windows quirk). Le bytes + imdecode.
_raw = open(SRC, "rb").read()
orig = cv2.imdecode(np.frombuffer(_raw, dtype=np.uint8), cv2.IMREAD_COLOR)
if orig is None:
    print(f"Falha ao decodificar {SRC}"); sys.exit(1)
h, w = orig.shape[:2]

# Gera as 10 variações
cases = []

def save(name, img):
    p = os.path.join(TMP, name + ".jpg")
    cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return p

# 1. Original
cases.append(("01_original", SRC, "controle"))

# 2. Rotacionada 45°
M = cv2.getRotationMatrix2D((w//2, h//2), 45, 0.8)
rot45 = cv2.warpAffine(orig, M, (w, h), borderValue=(0,0,0))
cases.append(("02_rotacao_45", save("rot45", rot45), "face inclinada 45°"))

# 3. Rotacionada 90°
M = cv2.getRotationMatrix2D((w//2, h//2), 90, 1.0)
rot90 = cv2.warpAffine(orig, M, (w, h), borderValue=(0,0,0))
cases.append(("03_rotacao_90", save("rot90", rot90), "face deitada"))

# 4. Face minúscula (padding gigante)
canvas = np.zeros((h*4, w*4, 3), dtype=np.uint8)
canvas[h*2 - h//2:h*2 + h//2, w*2 - w//2:w*2 + w//2] = cv2.resize(orig, (w, h))
small = cv2.resize(canvas, (w, h))
cases.append(("04_face_minuscula", save("small", small), "rosto ~5% do frame"))

# 5. Face gigante (crop só no rosto, sem corpo/contexto)
fy, fx = h//4, w//4
giant = orig[fy:fy + h//2, fx:fx + w//2]
giant = cv2.resize(giant, (w, h))
cases.append(("05_face_gigante", save("giant", giant), "zoom apenas rosto"))

# 6. Heavy darkening (gamma 0.3 = subexposição)
dark = np.power(orig.astype(np.float32) / 255.0, 1/0.3) * 255
dark = np.clip(dark, 0, 255).astype(np.uint8)
cases.append(("06_muito_escura", save("dark", dark), "gamma 0.3"))

# 7. Heavy brightening (gamma 3.0 = superexposição)
bright = np.power(orig.astype(np.float32) / 255.0, 1/3.0) * 255
bright = np.clip(bright, 0, 255).astype(np.uint8)
cases.append(("07_muito_clara", save("bright", bright), "gamma 3.0"))

# 8. Grayscale
gray = cv2.cvtColor(orig, cv2.COLOR_BGR2GRAY)
gray_3ch = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
cases.append(("08_preto_e_branco", save("gray", gray_3ch), "B&W"))

# 9. Espelhada horizontalmente
flip = cv2.flip(orig, 1)
cases.append(("09_espelhada", save("flip", flip), "horizontal flip"))

# 10. Muito pequena (200x200 upscaled)
tiny = cv2.resize(orig, (200, 200), interpolation=cv2.INTER_AREA)
tiny_up = cv2.resize(tiny, (w, h), interpolation=cv2.INTER_LINEAR)
cases.append(("10_baixa_res", save("tiny", tiny_up), "downscale 200x200 e upscale"))

print(f"{len(cases)} variações criadas em {TMP}\n")

# Submete cada, observa 1ª progress msg, cancela
SCRIPT = "Teste adversarial de input para o avatar."
results = []
for label, path, desc in cases:
    img_bytes = open(path, "rb").read()
    t0 = time.time()
    try:
        r = requests.post(BASE + "/api/generate",
            data={"script": SCRIPT, "voice": "pt-BR-FranciscaNeural",
                  "engine": "edge-tts", "enhancer": "none"},
            files={"image": (label + ".jpg", io.BytesIO(img_bytes), "image/jpeg")},
            timeout=30)
        if r.status_code != 200:
            results.append((label, desc, r.status_code, f"REJECT: {r.text[:80]}", ""))
            continue
        jid = r.json().get("job_id", "")
        # Espera até a 1ª mensagem informativa (até 30s)
        first_msg = ""
        for _ in range(15):
            time.sleep(2)
            d = requests.get(f"{BASE}/api/job/{jid}", timeout=10).json()
            msg = d.get("message", "")
            st = d.get("status", "")
            if msg and msg != "Aguardando vaga (max 1 jobs, 1 ativos)..." and "Generating voice audio" not in msg:
                first_msg = f"[{st}] {msg[:60]}"
                break
            if st in ("done", "error", "failed", "cancelled"):
                first_msg = f"[{st}] {msg[:60]}"
                break
        # Cancela imediatamente
        try: requests.post(f"{BASE}/api/job/{jid}/cancel", timeout=5)
        except: pass
        elapsed = int(time.time() - t0)
        results.append((label, desc, r.status_code, first_msg or "<no progress>", f"{elapsed}s"))
        print(f"  {label}: {r.status_code} | {first_msg[:70]}")
    except Exception as e:
        results.append((label, desc, "EXC", str(e)[:80], ""))
        print(f"  {label}: EXC {e}")

# Resumo
print("\n" + "="*88)
print(f"{'#':<3}{'Caso':<22}{'HTTP':>6}{'Path/Erro':<45}{'Time':>10}")
print("-" * 88)
for label, desc, http, path_info, elapsed in results:
    print(f"{label[:2]:<3}{desc[:22]:<22}{str(http):>6}  {path_info[:43]:<45}{elapsed:>10}")
print()

# Limpa temp
import shutil
shutil.rmtree(TMP, ignore_errors=True)

# Análise
no_crash = all(isinstance(r[2], int) and r[2] != 500 for r in results)
accepted = sum(1 for r in results if r[2] == 200)
rejected = sum(1 for r in results if isinstance(r[2], int) and 400 <= r[2] < 500)
exceptions = sum(1 for r in results if r[2] == "EXC")
print(f"Aceitos (200): {accepted}/{len(results)}")
print(f"Rejeitados gracioso (4xx): {rejected}")
print(f"Exceções/crashes: {exceptions}")
print(f"\n{'✅' if no_crash else '❌'} Pipeline {'NUNCA' if no_crash else 'SOMETIMES'} crasha em inputs adversariais")
sys.exit(0 if no_crash else 1)
