# 📡 Radar Pro — Mapa de Canais do YouTube

Reconstrução do **Radar Club** (banco de canais dark/faceless em ascensão) — **calibrada pela engenharia
reversa da API real deles** (`/api/radar/feed`): mesmo modelo de dados (darkScore, viewsPerSub, monetized,
facelessEstimate, badge, crescimento, 14 categorias, tiers). Com **três melhorias que a original NÃO tem**:
1. **VPH visível** em todo lugar (por vídeo, VPH do hit, VPH médio) — nos cards e no detalhe.
2. **Canais similares** (motor de "vizinhos de lane" por co-ocorrência de busca).
3. **Raio-X do nicho** (`/api/niche/<cat>`): top canais, oportunidades e estruturas de título recorrentes.

Roda 100% local. Usa a sua YouTube Data API v3 com **rotação de chaves**
(reaproveita `youtube_analyzer.py` — quota "nunca acaba").

---

## Como abrir
Clique em **`ABRIR RADAR PRO.bat`** (ou `python app.py`) → abre em http://127.0.0.1:5051

## Como encher o banco (crawler)
```bash
python crawler.py seed                  # semeia dos seus canais salvos (barato)
python crawler.py discover 8000         # varre TODAS as categorias (gasta ~8000 unidades)
python crawler.py category "História" 3000   # só uma categoria
python crawler.py channel <url/@/id>    # 1 canal + descobre similares ao vivo
python crawler.py refresh 80            # reatualiza os 80 canais mais antigos
python crawler.py rescore               # recalcula TODOS os scores (quota ZERO)
```
> Rode `rescore` sempre que mexer no `scoring.py` — ele reprocessa o banco sem tocar na API.
Também dá pra rodar pela interface: botões **↻ Atualizar** e **🔍 Mapear YouTube**.

> **Quota:** cada `search` custa 100 unidades; `channels`/`videos` custam 1.
> `seed` quase não usa busca (URLs `/channel/` são resolvidas por regex).
> `discover` é o que gasta. Ponha mais chaves em
> `..\.env_youtube_keys.txt` (1 por linha) e a rotação cuida do resto.

---

## Arquitetura
| Arquivo | Papel |
|---|---|
| `db.py` | Banco SQLite (channels, videos, crawl_log) + schema |
| `scoring.py` | Viral score, multiplicador, categoria, idioma, **tiers (as 8 abas)** |
| `yt.py` | Ponte para a YouTube API (rotação de chaves, snippet completo, VPH) |
| `crawler.py` | Descoberta: busca por categoria → coleta → enriquece → grava |
| `similar.py` | **Canais similares** (do banco = instantâneo; ao vivo = co-ocorrência) |
| `app.py` | Servidor Flask (API + UI), porta 5051 |
| `templates/index.html` | Interface (abas, filtros, cards, detalhe com VPH + similares) |
| `radar.db` | O banco (criado automaticamente) |

## Scoring (viral_score 0-100)
Combina: multiplicador (best_views/subs) · views absolutas do hit · VPH ·
frescor do hit · consistência. Canal pequeno com hit recente pontua ALTO
(= demanda reprimida sendo capturada agora).

### As 8 abas (tiers)
- 🔥 **Extremamente Virais** — score ≥ 80
- 🚀 **Virais** — score ≥ 60
- 🌱 **Virais Recentes** — score ≥ 60 e postou há ≤ 10 dias
- 💎 **Oportunidades** — canal pequeno (<100K) batendo acima do peso (mult ≥ 5, hit ≥ 20K)
- ✅ **Bons Canais** — score 40–60
- 🆕 **Recém Adicionados** — entrou no radar há ≤ 7 dias
- 🎵 **Canais de Música** — detecção de nicho música
- 🌐 **Todos**

## Categorias
Música · Guerra · História · True Crime · Mistério & Terror · Religião & Bíblia ·
Finanças & Riqueza · Motivação & Estoicismo · Ciência & Espaço · Outros
(classificação automática por palavras-chave EN/ES/PT + topicDetails da API).

## Filtros (paridade total com o Radar Club + extras)
- **Ordenação:** mais views · vídeo recente · views/inscrito · score dark · menos inscritos · recém-descobertos · **VPH** · **potencial** (os 2 últimos são só nossos)
- **Formato 3-vias:** 🎬 Longos (horizontal) · 📱 Shorts (vertical) · 🌐 Todos
- **9 idiomas:** EN, PT, ES, FR, DE, IT, RU, HI, AR
- **Categoria** (14, com contagem) · **Score mínimo** · **Busca** por nome/ID
- **Filtros adicionais** (3 chips): 💎 Menos inscritos, muitas views · 🆕 Novos (≤3 meses) · ⚡ Postando agora

## Banco de Referências (coleções)
Crie coleções nomeadas ("uma pra cada tipo de canal") e salve canais nelas pelo 🔖 do card.
Persistido no banco (`collections` + `collection_items`). Endpoints `/api/collections*`.

## Extras que o Radar Club NÃO tem
⚡ **VPH** (por vídeo/hit/médio) · 🔗 **Canais similares** · 📊 **Raio-X do nicho**.

---

## 🔥 Em alta agora (alta extrema: 24 h · 2-3 d · 4-6 d)
Botão **🔥 Em alta agora** no topo (ou http://127.0.0.1:5051/em-alta).
- Varre os nichos de **`nichos_em_alta.json`** (guitarra, bateria, hi-fi UK, caravans AU, aposentados UK,
  snowbird/RV, Amazon/Costco, rural, moda 60+ e adjacentes: carros UK/EUA, custo de vida, marcas colapsando,
  luxo/relógios, imóveis/mudança). Edite o JSON para mudar nichos, queries e limiares.
- Ranqueia por **VPH** por janela; 🚨 = outlier (VPH ≥ 3× a mediana do canal, ou da busca do nicho).
- **Cota mínima:** yt-dlp primeiro (busca "esta semana", zero cota) → API só `videos.list` para completar
  (1 unidade/50 vídeos, nunca `search`) → cache em `cache_em_alta/` (6 h).
- **Canais monitorados** (tabela `monitored_channels`): auto-adiciona canal com ≤40 vídeos e VPH ≥2000;
  marca 🧊 "esfriou" após 7 dias sem pico.
- Exporta `ESTRATEGIAS_PAINEIS/radar/EM_ALTA_HOJE.md` (Top 24 h, Top 2-3 d, canais novos explodindo).

```bash
python em_alta.py scan            # diário (rotina na nuvem): scan + canais + MD
python em_alta.py scan --no-api   # cota zero
python em_alta.py export          # reexporta o MD do último scan
python -m unittest discover -s tests
```
