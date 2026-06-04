# 📚 AvatarPilot Pro — Knowledge Base por Use Case

Guia prático: para cada persona/objetivo, **configuração ideal** + **passo-a-passo**.

---

## 👨‍💼 1. Criador de conteúdo (Reels / TikTok / Shorts)

**Objetivo:** vídeos curtos (15-60s) com avatar falando, captions vibrantes, fundo musical.

### Configuração ideal
- **Voz:** `pt-BR-FranciscaNeural` (feminina) ou `pt-BR-AntonioNeural` (masculina)
- **Engine:** Edge-TTS (free, qualidade ok)
- **Enhancer:** `gfpgan` (rápido)
- **Format:** `portrait` (1080×1920)
- **Captions:** ON, estilo **Karaoke**, cor **amarelo** ou **ciano**
- **Música:** Sim, com **Auto-duck ON**, volume 15%
- **Watermark:** OFF (licença Pro)

### Tempo esperado
- 30s clip: ~7 min em RTX 4060

### Roteiro modelo
> "Você sabia que [hook]? A maioria das pessoas comete o erro de [problema].
> Mas tem uma forma MUITO melhor: [solução em 3 passos].
> Quer mais conteúdo assim? Segue aí!"

---

## 🎓 2. Educador / Curso online

**Objetivo:** aulas de 5-30 min, narração profissional, captions burned-in.

### Configuração ideal
- **Voz:** Voice preset `documentary_narrator` (en-US-GuyNeural -10% rate -10Hz pitch)
- **Engine:** Edge-TTS (estável p/ duração longa)
- **Enhancer:** `gfpgan` + **codeformer_refine=true** (qualidade premium)
- **Format:** `landscape` (1920×1080)
- **Captions:** ON, estilo **Standard**, cor **branco**, posição **bottom**
- **Música:** Opcional, volume 10%, com Auto-duck
- **Trim silence:** ON (encurta pausas longas)
- **Loudnorm:** ON (-16 LUFS broadcast)
- **Fade:** ON (0.5s in/out)

### Tempo esperado
- 5min aula: ~30-40 min
- 15min aula: ~90 min

### Dica
Quebre aulas longas em chapters de 5min cada. Use a função de batch
(`POST /api/batch`) pra processar em série overnight.

---

## 🏢 3. Agência de marketing (white-label)

**Objetivo:** entregar vídeos pra clientes, qualidade HeyGen-equivalent.

### Configuração ideal
- **Voz:** Voice cloning via F5-TTS (use a voz do cliente — 15s de áudio dele)
- **Engine:** F5-TTS local
- **Enhancer:** `codeformer` (max identidade-preservante)
- **Format:** Cliente escolhe — `landscape` ou `portrait`
- **Captions:** ON, customize estilo/cor pela brand do cliente
- **Background:** Custom (faça upload do logo do cliente)
- **Watermark:** OFF
- **Output extra:** `webm` (web upload) + `mov` (broadcast)

### Workflow recomendado
1. Cliente envia: foto, brand colors, script
2. Faça F5-TTS clone da voz do cliente (15s de áudio de reunião dele)
3. Configure preset com brand (color picker do karaoke = cor da brand)
4. Generate batch de 5-10 vídeos
5. Cliente recebe link pra preview/aprovar
6. Final delivery em 24-48h

### Pricing sugerido cliente
R$ 150-300 por vídeo curto, R$ 800-2000 por série de 10 vídeos.

---

## 🧠 4. Terapeuta / Coach (conteúdo educativo)

**Objetivo:** vídeos de 2-5min explicando conceitos, tom acolhedor.

### Configuração ideal
- **Voz:** `pt-BR-FranciscaNeural` (acolhedora) ou voice clone de sua própria voz
- **Engine:** Edge-TTS ou F5-TTS clone
- **Enhancer:** `gfpgan`
- **Format:** `landscape` ou `portrait`
- **Captions:** ON, **Standard**, fonte size 24
- **Música:** `calm_ambient.mp3` com Auto-duck, volume 10%
- **Silence trim:** ON
- **Fade:** ON

### Dica de roteiro
Use linguagem em 2ª pessoa ("você"). Comece com pergunta, valide a dor,
ofereça insight, termine com CTA leve.

---

## 📰 5. Notícias / Documentário

**Objetivo:** narração com autoridade, sem música, qualidade broadcast.

### Configuração ideal
- **Voz:** Voice preset `news_anchor` (en-US-JennyNeural +5% rate)
  ou `documentary_narrator` (en-US-GuyNeural -10% rate -10Hz)
- **Engine:** Edge-TTS
- **Enhancer:** `codeformer` (premium, identidade-firme)
- **Format:** `landscape`
- **Captions:** ON, **Standard**, posição bottom, fonte 22, cor branco
- **Música:** OFF (autoridade vem da voz, não da BGM)
- **Loudnorm:** ON (consistência broadcast)
- **Trim silence:** OFF (mantém pausas naturais da narração)

### Tempo esperado
- 60s notícia: ~12-15 min
- 5min documentário: ~30 min

---

## 🚀 6. Anúncio rápido (R$30-50 por gen)

**Objetivo:** anúncios em 60s pra Stories/Ads, urgente.

### Configuração ideal — modo SPEED
- **Voz:** `pt-BR-AntonioNeural` ou voice preset `sales_pitch`
- **Engine:** Edge-TTS
- **Enhancer:** `none` (skip GFPGAN — 3x mais rápido)
- **Format:** `portrait` (Stories)
- **Captions:** ON, **Karaoke**, cor verde ou laranja (chamar atenção)
- **Música:** `upbeat_energy.mp3` com Auto-duck, volume 20%
- **Trim silence:** ON

### Tempo esperado
- 30s anúncio: **~2 min** (com enhancer=none)

---

## 🔧 Tuning avançado por GPU

| GPU | VRAM | Recomendação |
|---|---|---|
| RTX 2060 / GTX 1660 | 4-6 GB | Enhancer=none, sem CodeFormer, max 60s |
| RTX 3060 / 4060 8GB | 6-8 GB | Default OK, codeformer_refine OFF |
| RTX 4060 Ti / 4070 12GB | 10-12 GB | codeformer_refine=true OK, batch 2 jobs |
| RTX 4080 / 4090 16-24GB | 16+ GB | Tudo ON, MAX_WORKERS=2-3 |

---

## ❓ Troubleshooting por sintoma

**"Lip sync está dessincronizado"**
- Verifique se framerate do input é estável (use ffmpeg `-r 25` no source)
- Tente engine alternativo (auto-troca SadTalker→Wav2Lip→MuseTalk)
- Aumente padding do mouth region (config interno, pode customizar)

**"Avatar tá feio / artefatos no rosto"**
- Use `enhancer=codeformer` em vez de gfpgan
- Ative `codeformer_refine=true` (custa 2x mais tempo)
- Foto-source com boa iluminação + face frontal centralizada

**"Voz tá robotizada"**
- Mude pra preset com `rate` natural (-5% a +0%)
- Use F5-TTS voice clone com áudio de qualidade
- Para premium: ElevenLabs API key (opcional, custo R$/min)

**"Geração tá lenta demais"**
- enhancer=none (poupa 50% do tempo)
- captions=false (Whisper transcribe gasta 30s+)
- Use Wav2Lip ao invés de MuseTalk (3x mais rápido, qualidade ~80%)

---

## 📞 Dúvidas? Suporte

guilhermechaveshistory@gmail.com — resposta em <24h dias úteis.
