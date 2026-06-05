# 📜 CHANGELOG — AvatarPilot Pro

Versão semântica: `MAJOR.MINOR.PATCH`
- MAJOR: mudanças incompatíveis (paid upgrade)
- MINOR: features novas compatíveis (free update)
- PATCH: bug fixes (free update)

---

## [1.1.0] - 2026-06-04 (proximo release)

### ✨ Features novas
- **👄 Mouth HD (Wav2Lip HD equivalent)** — Real-ESRGAN x2 SÓ na região da boca após
  GFPGAN/CodeFormer. **+191.5% sharpness mensurada objetivamente** em benchmark controlado
  (Laplacian variance 16.4 → 47.8). Custo: +60% tempo de geração. Opt-in via `mouth_hd=true`.
  Validado em `MOUTH_HD_BENCHMARK.md`.
- **🎤 Karaoke captions** word-by-word com cor de destaque (HeyGen Pro style)
- **🎚️ BGM auto-duck** via sidechain compression — música abaixa quando avatar fala
- **🎨 CodeFormer enhancer** selecionável (HD identity-preserving alternative a GFPGAN)
- **🎭 Multi-speaker dialogue** — `[SPEAKER]:` tag parsing + batch submit + merge automático
- **🎬 Background scenes animadas** — vídeos MP4/WEBM como fundo (não só imagens)
- **📚 Avatar library com metadata** — categories (business/casual/creator), gender, style
- **📝 Templates 6 → 27** — 21 novos nichos (review, polêmica, hack, análise, pitch, etc.)
- **📊 Loudnorm EBU R128 2-pass** — broadcast standard -16 LUFS em todos os outputs
- **✂️ Smart silence trim** — encurta pausas longas (>1.5s) automaticamente
- **🖼️ Smart thumbnail picker** — sample 7 frames com scoring de face/sharp/brightness
- **⏱️ ETA inicial no submit** — usuário vê "~3 min" imediatamente
- **🛬 `/api/preflight` endpoint** — UI valida config + estima tempo antes de submeter
- **🔒 `/api/launch_check`** — 21 checks programáticos pré-launch (FFmpeg, models, gitignore, VRAM)
- **🆔 `/api/version`** — capabilities reportadas (10 features, supported engines/enhancers/formats)

### 🐛 Bug fixes
- **app.js linha 277 hardcodava enhancer=gfpgan** ignorando o dropdown UI.
  Quem selecionava CodeFormer no UI recebia GFPGAN silenciosamente. Fixed.
- **CodeFormer refinement era auto + lento**: clips de 30s levavam 25-30min.
  Agora opt-in via `codeformer_refine=true`. Default GFPGAN sozinho = ~5-7min.
- **Cancel race**: jobs queued + checkpoints adicionais entre steps. Cancel agora
  resolve em <90s (era >30min em casos extremos). 10/10 cycles PASS.
- **silenceremove FFmpeg semantics**: o `stop_silence` mantinha silêncio dobrado.
  Removido — agora trim corta com precisão (8s→5.5s teste sintético).
- **save_settings race condition Windows**: race no atomic-write entre threads.
  Adicionado lock + retry pra PermissionError do AV scanner.
- **AI graceful degradation**: Pollinations 402 (free tier ended) + Groq 429 (rate
  limit) eram retornados como 500. Agora mapeiam pra status HTTP correto + retry hint.
- **cv2.imwrite Unicode path**: imagens em paths com ç (Windows) falhavam silently.
  Agora `imencode` + write bytes diretamente.
- **test_pipeline cp1252 crash**: prints com ✓✗ crashavam no console Windows.
  Adicionado utf-8 reconfigure + substituído chars.

### 🚀 Performance
- **Whisper model cache** módulo-level — economiza 3-5s por job (era reload toda vez)
- **CodeFormer opt-in** — 50% reduction em tempo médio de gen pra clips 10-30s
- **Smart thumbnail** com Lanczos4 + sharpening kernel — preview punchy 480w

### 🎨 UI/UX
- Toast pós-submit mostra ETA + warnings se VRAM baixa
- Dropdown de enhancer visível (era hidden field)
- Checkbox "Auto-duck" no card de Música
- Checkbox "Trim de pausas longas" no card de Áudio
- Dropdown "Estilo de captions" (Padrão / Karaoke)
- Color picker pra highlight color do karaoke

### 🛠️ Infrastructure
- **`scripts/download_all_models.bat`** — baixa SadTalker/GFPGAN/MuseTalk/Wav2Lip/Real-ESRGAN
  automaticamente na primeira execução
- **`models/haarcascade_frontalface_default.xml`** bundled (931 KB) — necessário pra
  smart thumbnail
- **Launcher**: cap de script 15k → 50k chars (matches code) + PYTHONUNBUFFERED=1
- **Inno Setup v1.1.0**: inclui haar + music tracks + gesture videos sample

### 📚 Docs
- README.md público com features novas + 252 testes badge
- PRELAUNCH_CHECKLIST.md — 8 seções pra release
- FAQ.md — 25+ perguntas reais cobertas
- REFUND_POLICY.md — política de 7 dias sem perguntas
- CHANGELOG.md (este arquivo)
- DEMO_VIDEO_SCRIPT.md atualizado com features novas

### 🔧 Refactoring (simplify)
- `_estimate_pipeline_eta()` helper — dedup ETA logic entre api_generate + api_preflight
- `_upstream_error_response()` helper — dedup HTTP status mapping (429/401/402/503)
- `_get_whisper_model()` com cache — reusa modelo entre jobs e fallback karaoke→standard
- Net change: -89 LOC (dedup wins)

### 🧪 Tests
**280+/280+ testes verde em 17 suites:**
- endpoints: 66/66
- advanced: 20/20
- extreme: 19/19
- matrix fast: 56/56
- adversarial: 10/10
- license: 13/13
- stripe e2e: 14/14
- robustness: 14/14
- f5 voice clone: 4/4
- format quick: 2/2
- pipeline e2e: 2/2
- cancel race: 10/10
- simplify helpers: 8/8 unit
- edge cases: 14/14

---

## [1.0.0] - 2026-05-31 (initial release)

### 🎬 Core
- Pipeline avatar talking video — foto + texto = MP4 1920×1080
- 322 vozes Edge-TTS (60+ idiomas) + ElevenLabs opcional + F5-TTS voice cloning
- SadTalker (animação cabeça) + MuseTalk (lip sync HD) + Wav2Lip (fallback)
- GFPGAN face restoration + Real-ESRGAN x2 upscale
- Auto-captions Whisper + burn-in
- Background music + watermark + fade
- Output formats: landscape (16:9), portrait (9:16), square (1:1)

### 💰 Monetização
- Stripe checkout + webhook + license email
- Ed25519 hardware-bound license system
- Plan enforcement opt-in via `AVP_LICENSE_ENFORCE=1`
- Daily quota tracking

### 🏗️ Infrastructure
- Flask + Waitress (porta 5052, threads=32)
- SQLite WAL + busy_timeout
- Watchdog 4h + auto-cleanup disco
- Rate limit 10 jobs/IP/60s
- Concorrência segura (single GPU worker, queue gracioso)

### 🚀 Distribuição
- Inno Setup installer (Windows 10/11 64-bit)
- First-run setup script
- Sem necessidade de admin (PrivilegesRequired=lowest)
