# GCG Automation Suite

Sistema completo de automação de conteúdo para YouTube.

## Ferramentas

| Porta | Ferramenta | Descrição |
|-------|-----------|-----------|
| 5050 | **TitlePilot Pro** | Inteligência de títulos virais + Subniche Finder |
| 5051 | **VideosMAX** | Pipeline autônomo de produção de vídeos |
| 5052 | **AvatarPilot Pro** | Gerador de avatares IA com lip sync |

## Como Rodar

```bash
# Iniciar todos os servidores
start_all.bat

# Ou individualmente
cd titlepilot_pro && python server.py      # porta 5050
cd studiopilot_web && python server.py     # porta 5051
cd avatarpilot_pro && python server.py     # porta 5052
```

## Requisitos
- Python 3.10+
- NVIDIA GPU (RTX 4060+ recomendado)
- FFmpeg
- Flask, edge-tts, google-generativeai

## Estrutura
```
├── avatarpilot_pro/    # Gerador de avatares IA
├── titlepilot_pro/     # Inteligência de títulos
├── studiopilot_web/    # Pipeline de vídeos
└── shared/             # Módulos compartilhados
```
