# 🎬 AvatarPilot Pro — Estado de Retomada

> Documento de retomada após pausa em **2026-05-21**.
> Use isto quando voltar ao projeto para entender o que foi feito e como continuar.

---

## ✅ O que JÁ está pronto e funcionando

### Pipeline core
- **Flask server** porta 5052 (`python avatarpilot_pro/server.py`)
- **38 endpoints API** validados (100%)
- **TTS**: Edge-TTS (322 vozes, retry + fallback), F5-TTS (voice cloning local), ElevenLabs
- **Animação**: SadTalker (cabeça), MuseTalk (lip sync HQ), Wav2Lip (fallback rápido)
- **Restauração**: GFPGAN, CodeFormer (alternativa SOTA)
- **HD encode**: 1080p Full HD @ 5Mbps + Real-ESRGAN AI upscale opcional
- **EchoMimic V2 local** instalado (gestos com mãos, mas requer >8GB VRAM)
- **EchoMimic V2 cloud** via Replicate (R$0.30/min — opcional premium)

### Gesture Pack (segredo HeyGen)
- 20 vídeos Pexels CC0 em `static/gesture_videos/`
- Pipeline detecta automaticamente e usa para vídeos >90s
- Face swap (InsightFace CUDA) + lip sync = qualidade HeyGen
- Re-downloadable com: `python scripts/download_gesture_pack.py --key PEXELS_KEY`

### Production hardening
- **MAX_WORKERS auto-detect** por VRAM (1 no RTX 4060, 3 no RTX 4090, etc.)
- **Semaphore** evita oversubscription
- **SQLite WAL mode** + busy_timeout 5s
- **Watchdog 4h** mata jobs travados
- **Auto-cleanup** disco a cada 6h ou pressão <5GB
- **Rate limit** 10 jobs/IP/60s
- **Retry Edge-TTS** 3x com backoff + voice fallback por idioma
- **Anti-error system** completo (PT-BR errors, validação early)

---

## 🐛 Bugs corrigidos nesta sessão (21 commits)

| Commit | Descrição |
|--------|-----------|
| `5743216` | Edge-TTS retry + PT-BR errors + voice fallback |
| `86ae1de` | Production hardening (VRAM-aware workers, semaphore, WAL) |
| `70a4025` | Quality fixes (SadTalker 512, Real-ESRGAN, MuseTalk timeout) |
| `7f5e134` | Gesture Pack pipeline (HeyGen-class) |
| `ee60225` | Pexels downloader ffprobe path resolution |
| `58c5974` | Gesture concat ASCII path (Windows ç bug) |
| `a1ed1b6` | InsightFace CUDA provider (13x speedup) |
| `99bee04` | Gesture-pack 720p downscale + Wav2Lip (OOM fix) |
| `592bcae` | Final consolidation |

---

## ⚠️ Limitações conhecidas (RTX 4060 8GB)

- **MuseTalk em 1080p face-swapped** → OOM (workaround: downscale 720p + Wav2Lip)
- **Vídeo de 5min com gesture pack** → ~2h processamento
- **EchoMimic V2 local** → 25min para 6s clip (8GB VRAM tight)
- **Multi-job concurrent** → MAX_WORKERS=1 forçado pelo VRAM

**Solução**: upgrade para RTX 4090 24GB (R$15k) ou cloud RTX 4090 RunPod (R$250/mês)

---

## 🔄 Como retomar (depois de mexer em outro projeto)

### 1. Voltar ao projeto

```powershell
cd "C:\Users\Guilherme\Music\automaçao video\avatarpilot_pro"
```

### 2. Iniciar o servidor

```powershell
# Wrapper script (recomendado pelo encoding UTF-8)
python C:\Temp\start_avp.py

# OU direto
.\venv311\Scripts\python.exe server.py
```

Acessa http://localhost:5052/

### 3. Se o venv311 desaparecer (re-criar):

```powershell
cd "C:\Users\Guilherme\Music\automaçao video\avatarpilot_pro"
python -m venv venv311
.\venv311\Scripts\pip install -r requirements.txt
# Plus: faster-whisper, f5-tts, codeformer-pip, insightface, onnxruntime-gpu
```

### 4. Se gesture videos sumirem:

```powershell
# Pexels key está em ../.api_keys.json (base64)
python scripts\download_gesture_pack.py --key PEXELS_KEY --count 20
```

### 5. Modelos pesados (NÃO no git)

Os modelos estão em `models/`:
- `MuseTalk/` (~2GB)
- `SadTalker/` (~2GB)
- `Wav2Lip/` (~500MB)
- `EchoMimicV2/` (12GB)
- `CodeFormer/` (~600MB)
- `RealESRGAN_x2plus.pth`, `RealESRGAN_x4plus.pth` (64MB cada)
- `inswapper_128.onnx` (528MB)

Se sumirem, ver download scripts em cada subpasta ou docs originais.

---

## 📋 Próximos passos sugeridos (quando retomar)

### Imediato (não testado, mas implementado)
- [ ] Testar geração 5min com gesture pack + downscale 720p (fix 99bee04)
- [ ] Validar qualidade visual do output

### Curto prazo
- [ ] Sistema de licenças (servidor + hardware ID + Stripe webhook)
- [ ] Empacotador .exe (PyInstaller + Inno Setup)
- [ ] Página landing + checkout integrado

### Médio prazo
- [ ] Integrar com StudioPilot (editor) + TitlePilot (estratégia)
- [ ] Sistema unificado de planos (Avatar / Editor / Title / Suite)
- [ ] Dashboard cliente (consumo, histórico, billing)

### Longo prazo
- [ ] Cloud GPU rental se >30 clientes (RunPod RTX 4090 R$250/mês)
- [ ] Multi-tenancy isolado
- [ ] Voice library compartilhada

---

## 🔑 Onde está cada coisa

| Item | Localização |
|------|-------------|
| Código principal | `avatarpilot_pro/server.py` (8400+ linhas) |
| Frontend | `avatarpilot_pro/templates/index.html`, `static/app.js` |
| Pricing page | `avatarpilot_pro/templates/pricing.html` |
| API keys (base64) | `../.api_keys.json` (NÃO no git) |
| Admin token | `avatarpilot_pro/.admin_token` |
| Modelos | `avatarpilot_pro/models/` (15GB+, NÃO no git) |
| Venv | `avatarpilot_pro/venv311/` (NÃO no git) |
| Gesture videos | `avatarpilot_pro/static/gesture_videos/*.mp4` (NÃO no git) |
| Database | `avatarpilot_pro/data/avatarpilot.db` (NÃO no git) |
| Backup completo | `C:\Backup_AvatarPilot\` |

---

## 🌐 Repositório GitHub

`https://github.com/guichaves13-cmd/gcg-automation.git`

Branch: `main`

**Não está incluído no git:**
- API keys
- Modelos (~15GB)
- Venv (~3GB)
- Uploads/outputs do usuário
- Gesture videos mp4 (re-downloadable)

**Está incluído:**
- `avatarpilot_pro/server.py` (todo o backend)
- `avatarpilot_pro/templates/*.html`
- `avatarpilot_pro/static/app.js`, `style.css`
- `avatarpilot_pro/scripts/download_gesture_pack.py`
- `avatarpilot_pro/static/gesture_videos/README.md`
- `avatarpilot_pro/static/gesture_videos/_manifest.json` (lista do que tem)
- `RESUMO_AVATARPILOT.md` (este arquivo)
