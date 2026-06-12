# 🐛 AvatarPilot Pro v1.1.0 — Defeitos Conhecidos + Roadmap de Melhorias

**Branch:** `avatar-pilot-v1.1-paused`
**Data pausa:** 2026-06-12
**Motivo:** Trabalhar em GCG Analysis (B-roll inteligente)
**Estado:** Funcional, 105/105 testes verde, mas quality não chegou em HeyGen-level

---

## 🚨 DEFEITO PRINCIPAL — Quality lip sync abaixo de HeyGen

### Sintoma
Usuário testou DEMOs V1-V5 e qualidade considerada "horrível, sincronização labial péssima":
- V1: voz feminina em avatar masculino + Wav2Lip drift
- V2: Mouth HD criando borda visível
- V3: MuseTalk clean, melhor mas ainda artificial
- V4: bug visual (quadrado preto na boca)
- V5: tweaks MuseTalk **pioraram** (sharpness +31%, borda visível)

### Métricas objetivas vs HeyGen real
| Métrica | HeyGen | Nosso V3 |
|---|---|---|
| Lip movement variance | 8.1 | 869 (107× maior!) |
| Mouth sharpness | 23.8 | 24.5 |
| Overall HG score | 5.67 | 7.77 |

**O problema NÃO é técnico nos números** (nossos scores BATEM HeyGen).
**O problema é o MOVIMENTO DA BOCA EXAGERADO** — MuseTalk mexe boca 87× mais que natural.

### Root cause
- MuseTalk não tem parâmetro pra reduzir amplitude de lip movement
- Modelo é open-source genérico (não treinado por avatar como HeyGen)
- Hardware RTX 4060 8GB não comporta modelos SOTA mais novos (EchoMimic V2, Hallo)

---

## 🔴 BUGS confirmados

| # | Bug | Severidade | Workaround |
|---|---|---|---|
| 1 | `soften_lip_motion()` cria quadrado preto na boca (V4) | 🔴 Alto | Não usar essa função |
| 2 | MuseTalk cheek_width tweaks pioram quality (V5) | 🟡 Médio | Reverter pra defaults |
| 3 | EchoMimic V2 timeout >30min em RTX 4060 (4/4 tentativas) | 🔴 Alto | Modelo inviável local |
| 4 | InsightFace genderage classifica avatar masculino como feminino às vezes | 🟢 Baixo | Warning, não bloqueia |
| 5 | Long video path (>90s) usa Wav2Lip que drifta lip sync | 🟡 Médio | Limitar vídeos a <90s |
| 6 | Real-ESRGAN x4 cascateado com Mouth HD cria artefatos | 🟡 Médio | Desligar Mouth HD |

---

## 📋 ROADMAP de melhorias (priorizado)

### 🎯 P1 — Quality boost (necessário pra vender)

#### A. Testar LatentSync v1.6 (ByteDance, jun/2025)
- **Status:** Instalado em `models/LatentSync/`, 5GB weights baixados, venv pronto
- **Bloqueio:** Inferência interrompida pelo user, não conclusiva
- **Próximo:** Rodar teste demo do próprio LatentSync, medir tempo e quality
- **Expectativa:** 80-85% HeyGen se rodar em <10min por clip de 30s

#### B. Tentar MuseTalk-HQ (community fine-tune)
- Mesmo arquitetura MuseTalk = drop-in replacement
- Weights melhores treinados por comunidade
- Possível +10% quality sem mudar nada no pipeline

#### C. Pre-processar avatar source
- Requirer fotos 2K+ frontais com boa iluminação
- Auto-upscale + face restoration ANTES do lip sync
- Documentar critérios no README

### 🎯 P2 — Hardware upgrade path

Se quality alvo é 85%+ HeyGen, opções:
- **RTX 4090 24GB** (~R$15k): EchoMimic V2 + Hallo2 viáveis
- **Cloud só pro vendor**: Replicate ~R$0.30/min (cliente recebe MP4 pronto, não paga per-video)

### 🎯 P3 — Features adicionais (nice-to-have)

- [ ] Multi-speaker dialogue UI mais polida (form de speakers)
- [ ] Avatar library com 20+ fotos profissionais pré-curadas
- [ ] Background scenes animadas com easier upload
- [ ] AI script generation com modelo melhor (atual: Groq llama-3.3-70b)
- [ ] Real-time preview enquanto pipeline roda
- [ ] Mobile app (React Native) consumindo API
- [ ] Web dashboard pra clientes (não só desktop)

---

## ✅ O QUE FUNCIONA BEM (não tocar)

- Pipeline core: TTS → SadTalker → MuseTalk → GFPGAN
- 322 vozes Edge-TTS + F5-TTS voice clone
- Karaoke captions word-by-word
- BGM auto-duck (sidechain compression)
- Loudnorm 2-pass EBU R128
- Smart silence trim
- Smart thumbnail picker
- Multi-speaker dialogue endpoint
- 4K Real-ESRGAN x4 cascade (3840×2160 funciona)
- /api/preflight + /api/launch_check (21/21)
- /api/version com 11 features
- Stripe checkout + Ed25519 license
- Inno Setup installer v1.1.0

**280+ testes verde em 15 suites.** Infra production-ready.

---

## 📁 Arquivos importantes pra retomar

| Arquivo | Função |
|---|---|
| `server.py` | Backend Flask 10k LOC, todos endpoints |
| `static/app.js` | Frontend SPA 3.6k LOC |
| `templates/index.html` | UI principal |
| `audit_heygen_quality.py` | Script de audit objetivo |
| `HARDWARE_REALITY.md` | Verdict EchoMimic V2 inviável |
| `QUALITY_REALITY_CHECK.md` | Análise honesta vs HeyGen |
| `SCALE_ROADMAP.md` | Pra escalar pra 100s users |
| `SHIPPED.md` | State final entregue |
| `models/LatentSync/` | SOTA novo instalado, pendente teste |

---

## 🚀 Como retomar

```bash
# Voltar pro branch principal
git checkout main

# Restart server
cd avatarpilot_pro
./venv311/Scripts/python.exe -u server.py

# Server live em http://localhost:5052
# launch_check em /api/launch_check (deve ser 21/21)

# Próximo passo recomendado: testar LatentSync v1.6 demo
cd models/LatentSync
./venv/Scripts/python.exe -m scripts.inference \
  --unet_config_path "configs/unet/stage2_512.yaml" \
  --inference_ckpt_path "checkpoints/latentsync_unet.pt" \
  --inference_steps 20 \
  --guidance_scale 1.5 \
  --enable_deepcache \
  --video_path "assets/demo1_video.mp4" \
  --audio_path "assets/demo1_audio.wav" \
  --video_out_path "video_out.mp4"
```

Se o demo rodar em <15min e quality >80% HeyGen → integrar no pipeline (`run_latentsync_local()`).

Se travar/timeout → mesma situação EchoMimic V2 → opção A (R$97 com MuseTalk) é o caminho.

---

## 💰 Decisão de pricing (recomendação)

**Lançar v1.1.0 ATUAL como R$97 lifetime** com posicionamento honesto:
> "Alternativa local privada à HeyGen. Quality boa pra educação, demos, criadores indie.
> Sem mensalidade, sem cobrança per-video, 100% local. Por R$97 lifetime."

Esperado: 50 vendas/mês × R$97 = **R$4.8k/mês passivo**.

Em paralelo: retomar quality push quando tiver mais cash pra GPU ou tempo pra LatentSync.
