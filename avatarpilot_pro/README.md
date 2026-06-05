# 🎬 AvatarPilot Pro

> **AI-powered talking avatar generator** — local, offline-capable, HeyGen-quality
> Foto + texto = vídeo profissional com lip sync, captions, música e movimento natural

[![Tests](https://img.shields.io/badge/tests-280%2B%20passing-brightgreen)](#-validação)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)]()
[![GPU](https://img.shields.io/badge/GPU-NVIDIA%204GB%2B-orange)]()
[![License](https://img.shields.io/badge/license-Commercial-purple)](LICENSE.txt)

---

## ✨ Features

### Core
- **322 vozes Edge-TTS** (60+ idiomas) + ElevenLabs + F5-TTS voice cloning local
- **Lip sync HeyGen-quality** via MuseTalk (difusão latente) + Wav2Lip fallback
- **Movimento natural** via SadTalker + body sway + gesture videos (Pexels CC0)
- **Face restoration**: GFPGAN, CodeFormer (HD opt-in), RestoreFormer
- **HD encode** 1920×1080 @ 5Mbps + Real-ESRGAN upscale x2/x4

### HeyGen-level (novidades v1.1.0)
- ✨ **Karaoke captions** word-by-word com highlight color (estilo Reels/TikTok)
- 🎚️ **BGM auto-duck** — música abaixa quando avatar fala (sidechain compression)
- 👄 **Mouth HD** — Real-ESRGAN x2 SÓ na boca = **+191% sharpness mensurada** ([benchmark](MOUTH_HD_BENCHMARK.md))
- 🎭 **Multi-speaker dialogue** — `[SPEAKER]:` tags → batch + merge automático
- 🎬 **Background scenes animadas** — vídeos MP4/WEBM como fundo
- 📚 **Avatar library com metadata** — categorias business/casual/creator/etc.
- 📝 **27 templates por nicho** — review, polêmica, hack, análise, pitch, etc.
- 📊 **Loudnorm EBU R128** 2-pass — broadcast standard -16 LUFS
- ✂️ **Smart silence trim** — encurta pausas longas (>1.5s) automaticamente
- 🖼️ **Smart thumbnails** — frame com face + nitidez + brilho otimal
- ⏱️ **ETA inicial** + `/api/preflight` — usuário vê tempo estimado ao submeter
- 🔒 **`/api/launch_check`** — 21 checks pré-launch programáticos
- 🌍 **Caption translate** — SRT em 50+ idiomas (Google free)
- 🤚 **Gesture pack** — corpo inteiro com gestos reais + InsightFace face swap

### Production-ready
- Inno Setup installer + first-run setup script
- Stripe checkout + Ed25519 hardware-bound license
- SQLite WAL + watchdog 4h + auto-cleanup
- Rate limit + concorrência segura
- Atomic settings write + retry race-free

---

## 🚀 Instalação rápida

### Para usuários finais (Windows)
1. Baixe `AvatarPilotPro-Setup-1.1.0.exe`
2. Execute (não precisa admin)
3. Na primeira execução: setup automático baixa venv + modelos (~15-20 GB)
4. Servidor abre em `http://localhost:5052`

### Para desenvolvedores
```bash
git clone https://github.com/guichaves13-cmd/gcg-automation.git
cd gcg-automation/avatarpilot_pro
python -m venv venv311
venv311\Scripts\activate
pip install -r requirements.txt
scripts\download_all_models.bat
python server.py
# abre http://localhost:5052
```

---

## 🎯 Uso básico

1. **Upload foto** ou seleciona gesture video da biblioteca
2. **Digita roteiro** (até 50.000 caracteres) ou faz upload de áudio próprio
3. **Escolhe voz + enhancer + features** (karaoke, BGM, etc.)
4. **Clica Gerar** — vê ETA estimado imediatamente
5. **Download MP4 1920×1080** em alguns minutos

API REST completa em `/api/*` — ideal para integração.

---

## 🏗️ Arquitetura

```
Foto/Audio → TTS (Edge/F5/ElevenLabs)
           → Loudnorm 2-pass + Silence trim
           → SadTalker (animação cabeça) → MuseTalk (lip sync HD)
                                        OU Wav2Lip (fallback rápido)
           → GFPGAN / CodeFormer (face restoration)
           → Real-ESRGAN x2 (upscale)
           → Karaoke/Standard captions (Whisper word_timestamps)
           → Format (portrait/square/landscape)
           → BGM auto-duck (sidechain compression)
           → Watermark + Fade + Export
           → Smart thumbnail
           → MP4 1920×1080 @ 5Mbps + opcional WebM/MOV/GIF
```

---

## ✅ Validação

**226/226 testes verde** em 12 suites + cenários reais:

| Suite | Resultado |
|---|---|
| endpoints | 66/66 |
| advanced  | 20/20 |
| extreme   | 19/19 |
| matrix fast | 56/56 |
| adversarial | 10/10 (0 crashes) |
| license | 13/13 |
| stripe e2e | 14/14 |
| robustness | 14/14 |
| f5 voice clone | 4/4 |
| format quick | 2/2 |
| pipeline e2e | 2/2 |
| real-world combos | 6/6 |

**Bugs reais capturados e corrigidos:**
- silenceremove semantics (FFmpeg quirk)
- save_settings race condition (Windows atomic write)
- AI graceful degradation (Pollinations 402, Groq 429)
- cv2.imwrite Unicode path (ç) silently failing
- CodeFormer auto-refine making 30s clips take 30min (now opt-in)

---

## 💻 Requisitos

- **OS**: Windows 10/11 (64-bit)
- **GPU**: NVIDIA GeForce 4GB+ VRAM (RTX 2060 ou superior recomendado)
- **RAM**: 8 GB+ (16 GB recomendado)
- **Disco**: 50 GB livres (modelos + cache + outputs)
- **Python**: 3.11+ (instalado automaticamente pelo setup)
- **Internet**: Necessária para download inicial + Edge-TTS

---

## 📚 Documentação

- [BUILD_INSTALLER.md](BUILD_INSTALLER.md) — Como compilar o instalador
- [DEMO_VIDEO_SCRIPT.md](DEMO_VIDEO_SCRIPT.md) — Scripts de demo (60s + 3min)
- [RESUMO_AVATARPILOT.md](RESUMO_AVATARPILOT.md) — Histórico técnico do projeto

---

## 🔐 Privacidade

- **100% local** — fotos, áudio e vídeos NÃO saem da sua máquina
- Edge-TTS envia só o texto (não imagem) para Microsoft (TTS público gratuito)
- ElevenLabs/Replicate são opcionais — desligados por padrão
- License system não envia telemetria

---

## 📄 License

Commercial. Veja [LICENSE.txt](LICENSE.txt).

---

## 🤝 Suporte

- Email: guilhermechaveshistory@gmail.com
- Issues: [GitHub Issues](https://github.com/guichaves13-cmd/gcg-automation/issues)
