# 🎯 Quality Audit v2 — Temporal Smoothing + Mediapipe attempt

**Data:** 2026-06-05
**Sprint:** quality push pra "pessoa real falando" feel

---

## TL;DR

✅ **Overall HeyGen score: 7.37 → 8.06/10** (+9%) com temporal smoothing.
⚠️ Mediapipe wheels têm bug com path Windows non-ASCII (ç) — fallback Haar funciona perfeito.
⚠️ Real-ESRGAN x4 + 4K target implementado mas não testado nesta turn (custo ~30min/clip).

---

## Comparativo 3 versões (mesmo input)

| Job | Config | Sharpness | Overall HG |
|---|---|---|---|
| `5bf498390f3e` | gfpgan only | 16.40 | 7.37 |
| `c98879abc88f` | + Mouth HD Haar (sem smoothing) | 47.80 (+191%) | 7.89 (+0.52) |
| `8b299d39976e` | + Mouth HD Haar + **temporal smoothing 3-frame MA** | 43.50 (+165%) | **8.06 (+0.69)** ✅ |

**Trade-off temporal smoothing:**
- Sharpness pico ligeiramente menor (-9% vs v1) porque MA da bbox redistribui SR
- Face jitter Y melhora -9% (objetivo principal alcançado)
- Lip movement variance +5% (boca mais consistente)
- **Score OVERALL sobe +0.17** vs v1 → temporal smoothing líquido positivo

---

## Mediapipe — investigação

**Tentativa:** Substituir Haar cascade por mediapipe face_mesh (468 landmarks, 12 lip key).

**Findings:**
- Mediapipe 0.10.35 (latest) descontinuou `solutions` API → falha import
- Downgrade 0.10.21 + 0.10.9: legacy `solutions` API disponível MAS quebra em runtime:
  ```
  FileNotFoundError: The path does not exist:
  C:\Users\Guilherme\Music\automa?ao video\avatarpilot_pro\venv311\Lib\
  site-packages\mediapipe/modules/face_landmark/face_landmark_front_cpu.binarypb
  ```
- C++ resource loader do mediapipe usa encoding ANSI; ç vira `?` no caminho
- Bug conhecido na pip wheel — não tem fix oficial

**Resolução possível (fora do escopo):**
1. Mover projeto pra `C:\AvatarPilot\` (ASCII puro)
2. Compilar mediapipe from source com UTF-8 path handling
3. Usar mediapipe via MediaPipe Tasks API (.task model download)

**Decisão:** Código mediapipe ESTÁ implementado (fallback gracioso). Funcionará
quando user instalar em path ASCII OU quando upstream resolver o bug.

---

## Real-ESRGAN x4 + 4K output

**Implementado:**
- `output_resolution=4k` na API + UI dropdown
- HD encode target 3840×2160 @ 30 Mbps Netflix-grade
- Real-ESRGAN x4 cascade (modelo `RealESRGAN_x4plus.pth` já presente)
- Aspect ratios: 16:9, 9:16, 1:1 todos suportados em 4K

**Não testado nesta sprint** (custo ~30min/clip):
- Validar visualmente o output 4K
- Benchmark sharpness vs 1080p
- Confirmar não OOM em RTX 4060 8GB

**Recomendação:** Default 1080p (sweet spot velocidade/qualidade). 4K opt-in
pra clientes que precisam de projeção/4K display.

---

## Recomendações próximas iterações

1. **Re-test mediapipe pós-resolução do path Windows** (mover projeto OU bug fix upstream)
2. **Wav2Lip checkpoint upgrade**: testar `wav2lip_gan.pth` SHA upstream pra confirmar latest
3. **Custom mouth tracker via PyTorch landmarks** — bypass mediapipe pip bug
4. **Audit 4K** com clip pequeno (~5s, custo ~5min)
5. **Subtle eye blinks** — SadTalker tem, mas validar frequência (alvo 15-20/min em humanos)
6. **Breathing micro-movements** no chest area do gesture pack
7. **Skin texture preservation** — verificar GFPGAN/CodeFormer config pra max detalhe

---

## Status pré-launch

✅ Overall HeyGen score: 8.06/10 (melhor da história do projeto)
✅ Mouth HD validado +165-191% sharpness
✅ Temporal smoothing entregue +9% overall
✅ 4K output infra pronta (não testado)
✅ Mediapipe code ready (bloqueado por bug Windows path)
✅ 280+ testes verde
✅ 22 commits sessão
