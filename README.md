# GCG Automation Suite — StudioPilot Pro / VideosMAX

Sistema completo de automação de produção de vídeos com IA. Foco principal: **B-Roll Auditor com revisão humana** + **pipeline avatar automático** com matching semântico de stock footage.

> **Status:** 770/770 tests passing · 14 bugs de produção fixados · UI validada em browser real

---

## 📋 Índice

1. [Suite Completa](#-suite-completa)
2. [O que faz o VideosMAX (5051)](#-o-que-faz-o-videosmax-5051)
3. [Quick Start](#-quick-start)
4. [Configuração de API Keys](#-configuração-de-api-keys)
5. [Fluxo Completo](#-fluxo-completo)
6. [B-Roll Auditor + Picker UI](#-b-roll-auditor--picker-ui)
7. [Configs do Pipeline](#-configs-do-pipeline)
8. [Chain de Fallback IA](#-chain-de-fallback-ia-glm--gemini--heurística)
9. [Testes](#-testes)
10. [Troubleshooting](#-troubleshooting)
11. [Arquitetura](#-arquitetura)

---

## 🛠️ Suite Completa

| Porta | Ferramenta | Descrição |
|-------|-----------|-----------|
| 5050 | **TitlePilot Pro** | Inteligência de títulos virais + Subniche Finder |
| 5051 | **VideosMAX** ⭐ | Pipeline autônomo de produção de vídeos + B-Roll Auditor |
| 5052 | **AvatarPilot Pro** | Gerador de avatares IA com lip sync (pausado) |

Este README foca no **VideosMAX (5051)** — o componente principal e ativamente desenvolvido.

---

## 🎬 O que faz o VideosMAX (5051)

Entrega um vídeo final 1080p (ou 4K/vertical) a partir de um avatar falante:

1. **Whisper** transcreve o áudio do avatar
2. **Gemini 2.5 Flash / GLM-5.1** analisa o conteúdo e gera um **shot list semântico** (queries B-Roll inteligentes — ex: ouve "fish oil after 60" → busca "elderly hands taking supplement capsules", não "fish swimming ocean")
3. **Pexels / Pixabay / Unsplash / Mixkit / YouTube** baixam B-Roll real
4. **Gemini Vision** valida cada clip (rejeita os que não combinam visualmente)
5. **`_make_broll_with_pip`** renderiza B-Roll fullscreen + avatar em PIP (4 posições, fade in/out, Ken Burns)
6. **`add_lower_third`** adiciona texto sobre B-Roll (3 estilos: modern/minimal/bold)
7. **`_picker.html`** é gerado para revisão humana opcional
8. **B-Roll Auditor** aplica decisões do picker e re-renderiza só o que mudou

---

## 🚀 Quick Start

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Configurar API keys (via UI ou script)
python -c "from core.api_keys import save_api_key; save_api_key('gemini', 'AIza...')"
python -c "from core.api_keys import save_api_key; save_api_key('pexels', 'sua_key...')"

# 3. Subir todos os servers (ou só o 5051)
start_all.bat
# Ou apenas VideosMAX:
python -m studiopilot_web.server
# → http://localhost:5051

# 4. Upload avatar pela UI e clicar "Auto Pipeline"
# OU via Python:
python -c "
from core.pipeline_avatar_auto import run_auto
from core.api_keys import load_api_key
run_auto({
  'avatar_video': 'avatar.mp4',
  'output_file': 'output.mp4',
  'google_api_key': load_api_key('gemini'),
  'pexels_api_key': load_api_key('pexels'),
  'pixabay_api_key': load_api_key('pixabay'),
  'generate_picker': True,
  'lower_thirds_enabled': True,
  'lower_thirds_style': 'modern',
})
"
```

### Requisitos
- Python 3.10+ (testado em 3.14)
- FFmpeg (incluído em `ffmpeg/` no Windows)
- NVIDIA GPU opcional (Whisper roda em CPU, motion_graphics força libx264)
- Pacotes: `flask`, `whisper`, `google-genai`, `openai` (para GLM), `requests`, `yt-dlp`

---

## 🔑 Configuração de API Keys

| API | Obrigatória? | Onde obter | Free tier |
|-----|--------------|------------|-----------|
| **Gemini 2.5 Flash** | ✅ Sim (análise) | https://aistudio.google.com/apikey | 1500 req/dia |
| **NVIDIA (GLM-5.1)** | Recomendada (fallback) | https://build.nvidia.com/ | gratuito |
| **Pexels** | ✅ Sim (vídeos B-Roll) | https://www.pexels.com/api/new/ | 200/h, 20k/mês |
| **Pixabay** | Recomendada | https://pixabay.com/api/docs/ | 5k/h |
| **Unsplash** | Opcional (fotos) | https://unsplash.com/developers | 50/h |
| **YouTube Data API** | Opcional (yt-dlp já funciona) | Google Cloud Console | 10k unidades/dia |

```python
from core.api_keys import save_api_key, load_api_key

save_api_key("gemini", "AIzaSy...")
save_api_key("nvidia", "nvapi-...")
save_api_key("pexels", "...")
save_api_key("pixabay", "...")
```

---

## 🔄 Fluxo Completo

```
┌─────────────────┐
│  Avatar.mp4     │ ←── input
└────────┬────────┘
         ▼
┌─────────────────────────────────┐
│ Whisper Transcribe (CPU/GPU)    │
└────────┬────────────────────────┘
         ▼
┌─────────────────────────────────┐
│ VideoIntelligence.analyze_video │
│  - GLM-5.1 (1°) → Gemini (2°)   │ ◄── chain de fallback
│  - Theme, subtopics, shot_list  │
└────────┬────────────────────────┘
         ▼
┌─────────────────────────────────┐
│ Stock Downloads                 │
│  Pexels → Pixabay → Unsplash    │
│  → Mixkit → Coverr → YouTube    │
└────────┬────────────────────────┘
         ▼
┌─────────────────────────────────┐
│ Gemini Vision validates clips   │
│  (rejeita se não combinar)      │
└────────┬────────────────────────┘
         ▼
┌─────────────────────────────────┐
│ Smart timeline (avatar+broll)   │
│  pacing wide/closeup, max 2     │
│  broll consecutivos             │
└────────┬────────────────────────┘
         ▼
┌─────────────────────────────────┐
│ Render: _make_broll_with_pip    │
│  + add_lower_third              │
│  + add_transition_sfx           │
│  + concat + subtitles SRT       │
└────────┬────────────────────────┘
         ▼
┌─────────────────────────────────┐
│ Output: video.mp4 +             │
│  video_beat_timeline.json +     │
│  video_picker.html              │ ← revisão humana
└─────────────────────────────────┘
```

---

## 🎨 B-Roll Auditor + Picker UI

Após o pipeline rodar, abra `*_picker.html` no browser para revisar e re-renderizar.

### Funcionalidades do Picker

| Função | Botão | O que faz |
|--------|-------|-----------|
| **Aprovar** beat | verde | Mantém o clip atual |
| **Rejeitar** beat | vermelho | Substitui por segmento de avatar |
| **Substituir** beat | amarelo | Mostra input pra colar path de clip manual |
| **Aprovar todos** | topo | Aprova todos os broll de uma vez |
| **Rejeitar score < 0.5** | topo | Bulk reject de baixo score |
| **Resetar tudo** | topo | Limpa decisões |
| **Mostrar todos / Apenas B-roll / Score baixo** | topo | Filtros |
| **Exportar Decisoes** | rodapé | Download `picker_decisions.json` |
| **Aplicar no Re-render** | rodapé direito | Abre painel de re-render |

### Painel "Aplicar no Re-render"

Ao clicar, abre painel com:

- 📁 Timeline JSON path
- 📁 Avatar/video base path
- 📁 Legendas SRT (opcional)
- 📁 Nome do output
- ☑️ **Checkbox** "Lower thirds (texto sobre B-Roll)"
- 📋 **Dropdown style**: Modern (azul gradient) · Minimal (sutil) · Bold (vermelho impactante)
- 👁️ **Live preview** do estilo selecionado (atualiza em real-time):
  - **Modern**: bg azul `rgb(30,144,255)`, peso 700
  - **Minimal**: bg `rgba(255,255,255,0.1)` sutil, peso 400
  - **Bold**: bg vermelho `rgb(255,68,68)`, **TEXTO MAIÚSCULO**
  - **Disabled**: cinza, opacity 0.3
- 🔍 **Pre-visualizar impacto** → mostra `0 aprov | 1 rej | 5 re-busca | ...` sem renderizar
- 🚀 **Re-renderizar agora** → dispara auditor + progress bar polling

### Persistência

Decisões e replacements ficam em `localStorage` (2 keys: `broll_picker_decisions` + `broll_picker_replacements`). Reload preserva tudo, incluindo o path do replacement.

---

## ⚙️ Configs do Pipeline

```python
config = {
    # Obrigatorios
    "avatar_video": "avatar.mp4",
    "output_file": "output.mp4",
    "google_api_key": "AIza...",
    "pexels_api_key": "...",

    # B-Roll quality control
    "broll_min_score": 0.4,           # min score Gemini Vision (0-1)
    "auto_broll_count": 30,           # alvo de clips

    # Visual
    "resolution": "1080p",            # ou "4k", "vertical", "square"
    "fps": 30,
    "transition_sfx_enabled": True,
    "transition_sfx_volume": 0.18,

    # Lower thirds (NEW v3)
    "lower_thirds_enabled": True,
    "lower_thirds_style": "modern",   # modern | minimal | bold

    # Picker / Auditor
    "generate_picker": True,          # gera _picker.html

    # YouTube fallback
    "youtube_priority": None,         # None = auto (on se youtube_api_key)
}
```

---

## 🤖 Chain de Fallback IA (GLM → Gemini → Heurística)

Bug crítico descoberto em produção: **Gemini free tier (limit=0 ocasional)** travava o pipeline. Solução: chain de 3 níveis.

```
1. GLM-5.1 (NVIDIA reasoning)  ← TENTA PRIMEIRO
   - thinking=False (10s) para shot lists
   - thinking=True (60s+) para análise profunda
   - max_retries=0 (falha rápido p/ ir pro Gemini)
        │
        ├── sucesso → usa
        │
        └── falha (timeout/quota/error)
               ▼
2. Gemini 2.5 Flash  ← FALLBACK
   - JSON response mode
   - 2 attempts com retry
   - flag _vision_quota_exhausted impede loop infinito
        │
        ├── sucesso → usa
        │
        └── falha
               ▼
3. Heurística local  ← ÚLTIMO RECURSO
   - THEME_DB (195 themes) word-boundary match
   - Stop list 100+ words + bigram extraction
   - _textual_match_score para validation
```

**Bugs reais evitados pelo chain:**
- `theme="insects"` (substring `ant` em `want`) → word boundary fix
- Search terms `body insects` → subtopics semânticos via bigrams
- Quota loop infinito → flag class-level pula Vision
- GLM timeout 30s → `max_retries=0` + `thinking=False`

---

## 🧪 Testes

```bash
# Suite completa (770 tests, ~5min)
python test_ti_full.py              # 74/74  - Title Intelligence
python test_gcg_full.py             # 146/146 - GCG general
python test_pipeline_broll_full.py  # 107/107 - 4 modos pipeline
python test_broll_auditor.py        # 241/241 - Auditor + UI + GLM
python test_all_modules_smoke.py    # 35/35  - todos core/ modulos
python test_all_endpoints.py        # 167/167 - todos server endpoints
```

### Cobertura

- **`core/`: 54/54 módulos** importam OK (lint + smoke)
- **`server.py`: 167/167 endpoints** respondem (0 HTTP 500)
- **`broll_auditor` + `motion_graphics` + GLM chain**: 100% incluindo **SSIM visual**
- **UI**: validada em browser real via Claude Preview (Aprovar/Rejeitar/Substituir/filtros/persistence/rerender real)

### Tipos de teste

- **Unit / smoke** (import + basic call) - rápidos
- **Sintéticos** (mockam APIs) - todos os caminhos de código
- **Visual SSIM** (ffmpeg compara frames) - confirma que filtros aparecem no output
- **Multilíngue** (chinês/árabe/hindi) - sem crash com Unicode
- **Stress** (5000 beats, 10x concorrente) - performance
- **Segurança** (injection, XSS, path traversal) - hardening
- **E2E real** (APIs pagas) - validação ponta-a-ponta
- **Browser** (Claude Preview) - clicks reais na UI

---

## 🛠️ Troubleshooting

### `Fontconfig error: Cannot load default config file` no Windows
**Causa:** FFmpeg estático do Windows não tem fontconfig.
**Solução:** `motion_graphics._font_arg()` agora usa `fontfile=` explícito com path absoluto pra `arial.ttf`.

### `Error reinitializing filters` com NVENC
**Causa:** NVENC espera CUDA frames, drawbox produz CPU frames.
**Solução:** `motion_graphics._get_encoder()` força `libx264` (hw accel não ajuda em filtros 2D).

### `Error when evaluating the expression 'H-140'` em drawbox
**Causa:** `drawbox` não aceita `H-N`, só `drawtext` aceita.
**Solução:** `y_pos='ih-N'` (drawbox) e `.replace('ih','H')` p/ drawtext.

### Gemini retorna `429 RESOURCE_EXHAUSTED`
**Causa:** Free tier estourado.
**Solução automática:** Chain de fallback usa GLM-5.1 (NVIDIA) primeiro.

### Pipeline trava em loop "validate REJECTED"
**Causa:** Vision quota esgotada + search terms ruins → cada clip rejeitado loop infinito.
**Solução:** flag `_vision_quota_exhausted` class-level pula Vision após primeiro 429. `_textual_match_score` retorna `-1` (accept) sem metadata.

### Picker botões "Substituir" / "Aplicar" não fazem nada
**Causa antiga:** `style.display = ''` revertia ao CSS `none`.
**Fix:** Use `'block'` explícito. Regen picker com `generate_picker()` da versão atual.

### Theme detectado como "insects" em vídeo de saúde
**Causa:** `'ant' in 'want'` matching substring em THEME_DB.
**Solução:** `re.search(r'\b' + kw + r'\b', text)` word boundary em `_fallback_analyze`.

### Search terms como "body insects", "tell Lymphatic"
**Causa:** `_extract_visual_keywords_from_text` combinava `palavra_random + theme`.
**Solução:** Agora aceita `subtopics` param e usa frases semânticas direto (ex: `'Lymphatic system function'`).

---

## 🏗️ Arquitetura

```
core/
├── pipeline_avatar_auto.py    ← orquestrador principal (run_auto)
├── video_intelligence.py      ← Whisper + GLM/Gemini analysis
├── glm_agent.py               ← GLM-5.1 via NVIDIA
├── pexels_stock.py            ← Pexels API
├── pixabay_stock.py           ← Pixabay API
├── unsplash_stock.py          ← Unsplash API
├── coverr_stock.py            ← Coverr API
├── mixkit_stock.py            ← Mixkit scraper (free)
├── youtube_broll.py           ← yt-dlp fallback
├── video_filters.py           ← apply_random_effects (mood/shot_type)
├── motion_graphics.py         ← lower_third, title_card, chapter, progress
├── video_processor.py         ← concat, get_duration, get_resolution
├── beat_timeline.py           ← shot_list → timeline JSON
├── broll_picker.py            ← gera _picker.html (UI revisão)
├── broll_auditor.py           ← aplica picker_decisions, re-renderiza
├── subtitle_generator.py      ← Whisper + SRT
├── api_keys.py                ← persistência de keys
├── theme_database.py          ← 195 themes + 10 emotions
├── self_healing.py            ← startup checks + auto-fix
├── auto_recovery.py           ← _suggest_fix para erros
└── ... (38 outros módulos)

studiopilot_web/
└── server.py                  ← Flask 5051, 167 endpoints
    ├── /api/pipeline/auto              ← upload + run_auto
    ├── /api/pipeline/rerender          ← auditor com decisions
    ├── /api/pipeline/auditor/analyze   ← preview impacto
    ├── /api/pipeline/auditor/load_timeline
    ├── /output/<file>                  ← serve picker HTML, videos
    └── ... (160 outros endpoints)

test_*.py                      ← 6 test suites (770/770)
├── test_ti_full.py             # Title Intelligence
├── test_gcg_full.py            # GCG general (rotas + AI)
├── test_pipeline_broll_full.py # 4 modos pipeline
├── test_broll_auditor.py       # Auditor + UI + GLM brutal
├── test_all_modules_smoke.py   # smoke 54 modulos
└── test_all_endpoints.py       # smoke 167 endpoints
```

---

## 📜 Histórico de Versões

- **v3.1** (atual) — UI picker style selector + GLM fallback + 14 bugs corrigidos
- **v3.0** — B-Roll Auditor com revisão humana
- **v2.0** — VideoIntelligence + matching semântico
- **v1.0** — Pipeline básico avatar + B-Roll

## 🐛 Bugs corrigidos nesta versão (14 total)

1. `_esc` NULL byte sanitize (motion_graphics)
2. Server validation 400 vs 409 ordem (rerender route)
3. `→` Unicode em prints crashava cp1252 (multiple)
4. Fontconfig missing → `fontfile` explícito
5. NVENC + drawbox conflict → libx264
6. `H-N` vs `ih-N` drawbox/drawtext expression
7. GLM `max_retries=2` × `timeout=10s` = 30s loop
8. Theme `THEME_DB` substring (`ant` em `want`)
9. Subtopics palavras random → stop list + bigrams
10. Gemini Vision quota loop infinito → flag class-level
11. `_extract_visual_keywords` word+theme → subtopics
12. GLM timeout em shot_list (reasoning) → `enable_thinking=False`
13. `toggleApplyPanel` JS broken → `getComputedStyle`
14. `replace-file-row` JS `display=''` → `'block'` explicit

## 📄 Licença

Privado / Uso interno.

## 🙏 Créditos

- **Gemini 2.5 Flash** + **GLM-5.1** (análise IA)
- **Pexels** + **Pixabay** + **Unsplash** + **Mixkit** + **Coverr** (stock footage)
- **Whisper** (transcrição)
- **FFmpeg** (renderização)

---

**Built with [Claude Code](https://claude.com/claude-code)** · 21 commits · 14 bugs fixed · 770 tests passing
