# TitlePilot Pro

<div align="center">

![TitlePilot Pro](https://img.shields.io/badge/TitlePilot-Pro%20v2.0-blueviolet?style=for-the-badge&logo=youtube)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)
![Flask](https://img.shields.io/badge/Flask-2.3+-green?style=flat-square)
![Gemini AI](https://img.shields.io/badge/Gemini-AI%20Powered-orange?style=flat-square&logo=google)
![License](https://img.shields.io/badge/License-Private-red?style=flat-square)

**A ferramenta definitiva de inteligência de títulos para criadores de conteúdo YouTube**

*Powered by Google Gemini AI + YouTube Data API*

</div>

---

## ✨ Funcionalidades

### 📊 Análise de Títulos
- **Analisador de Título** — Score viral 0-100, grade A-F, estruturas virais detectadas
- **Deep AI Analysis** — Análise profunda com Gemini AI + 3 versões melhoradas
- **Gerador de 15 Títulos** — 15 variantes virais com personas (Strategist, Showman, Detective...)
- **A/B Battle Simulator** — Simula comportamento da audiência entre 2 títulos
- **Hook Blueprint** — Script de 60s com blocos de retenção
- **Batch Analyzer** — Analisa 5-50 títulos de uma vez

### 🔥 Estratégia de Nicho (3 Níveis)
- **Sub-nicho Finder** — Blue Ocean Score + 1º Vídeo Ideal + RPM estimado
- **🔬 Micro-Nicho** — 6 micro-nichos com ângulo único, gap de conteúdo, 3 vídeos iniciais
- **🗺️ Mapa Estratégico Completo** — 1 nicho → 6 sub-nichos → 18 micro-nichos

### 🚀 Ferramentas Avançadas
- **Crossover Engine** — Funde 2 nichos em narrativa viral
- **Trend Hijacker** — Transforma trending news em vídeo evergreen
- **Outlier Finder** — Detecta vídeos que performaram 5x-100x acima da média
- **Competitor X-Ray** — Reverse-engineer de qualquer canal
- **Niche Scorer** — Score de lucratividade, saturação e tamanho de mercado
- **Shorts Engine** — Loop viral para TikTok/YouTube Shorts
- **VPH Radar** — Top vídeos por Views Per Hour em tempo real

### 📊 YouTube Data API (Dados Reais)
- **Channel Analyzer** — Análise completa de canal com VPH, outliers, DNA de conteúdo
- **Niche Deep Dive** — Top vídeos por VPH, outliers, temas virais
- **Trending Now** — 25 vídeos trending com métricas reais
- **Channel Compare** — Comparação de até 5 canais lado a lado

---

## ⚡ Instalação Rápida

### Pré-requisitos
- Python 3.10+ ([download](https://python.org/downloads))
- Chave de API Gemini ([obter aqui](https://aistudio.google.com/))
- Chave YouTube Data API v3 ([console](https://console.cloud.google.com/))

### Windows (recomendado)

```bash
# 1. Clone ou baixe o ZIP
git clone https://github.com/seu-usuario/titlepilot-pro.git
cd titlepilot-pro

# 2. Execute o instalador automático
INSTALL.bat

# 3. Inicie a ferramenta
START.bat
```

O navegador abrirá automaticamente em `http://localhost:5050`

### Manual (qualquer OS)

```bash
pip install -r requirements.txt
python server.py
```

---

## 🔑 Configuração de API Keys

Na primeira execução, configure suas chaves na aba **YouTube Scanner**:

1. **Gemini AI** — Configure via variável de ambiente `GEMINI_API_KEY` ou no arquivo `.api_keys.json`
2. **YouTube Data API** — Insira na aba "YouTube Scanner" → campo "YouTube API Key" → clique "Save"

> ⚠️ Nunca compartilhe seu `.api_keys.json`. Ele está no `.gitignore` por padrão.

---

## 🏗️ Arquitetura

```
titlepilot_pro/
├── server.py           # Backend Flask (2000+ linhas de inteligência)
├── requirements.txt    # Dependências Python
├── static/
│   ├── app.js          # Frontend JavaScript (1500+ linhas)
│   ├── style.css       # Dark theme premium
│   └── index.css       # Utilitários CSS
├── templates/
│   └── index.html      # UI Principal (18 páginas)
├── core/               # Módulos compartilhados
│   ├── api_keys.py     # Gerenciamento de chaves
│   └── youtube_api.py  # Wrapper YouTube Data API
├── INSTALL.bat         # Instalador Windows
├── START.bat           # Iniciador Windows
└── .gitignore
```

---

## 📡 API Reference

| Endpoint | Método | Descrição |
|---|---|---|
| `POST /api/analyze` | POST | Análise de título individual |
| `POST /api/analyze_batch` | POST | Análise em lote |
| `POST /api/deep_analyze` | POST | Análise profunda com Gemini |
| `POST /api/generate` | POST | Gerador de 15 títulos virais |
| `POST /api/ab_test` | POST | Simulador A/B |
| `POST /api/subniche` | POST | Finder de sub-nichos |
| `POST /api/micronicho` | POST | Finder de micro-nichos |
| `POST /api/niche_strategy_complete` | POST | Mapa estratégico 3 níveis |
| `POST /api/hook_blueprint` | POST | Script Hook 60s |
| `POST /api/crossover_engine` | POST | Engine de crossover de nichos |
| `POST /api/trend_hijacker` | POST | Hijacker de trending news |
| `POST /api/outlier_finder` | POST | Finder de outliers reais (YT) |
| `POST /api/competitor_xray` | POST | X-Ray de canal |
| `POST /api/niche_scorer` | POST | Score de nicho (YT) |
| `POST /api/shorts_engine` | POST | Engine viral para Shorts |
| `POST /api/vph_radar` | POST | Radar VPH em tempo real (YT) |
| `POST /api/youtube/channel` | POST | Análise de canal (YT) |
| `POST /api/youtube/niche` | POST | Análise de nicho (YT) |
| `POST /api/youtube/trending` | POST | Trending videos (YT) |

---

## 🧠 Powered By

- **Google Gemini 2.0 Flash** — Geração e análise de conteúdo
- **YouTube Data API v3** — Dados reais de vídeos e canais
- **Flask** — Backend leve e rápido
- **Vanilla JS** — Frontend sem dependências

---

## 📄 Licença

Uso privado. Todos os direitos reservados.
