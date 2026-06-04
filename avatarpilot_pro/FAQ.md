# ❓ AvatarPilot Pro — FAQ

## 💰 Preço e compra

**Quanto custa?**
Compra única (sem mensalidade). Veja `/pricing` no app pra opções de plano.

**Tem reembolso?**
Sim — 7 dias sem perguntas. Email pra suporte com seu número de licença.

**Posso usar comercialmente?**
Sim. A licença Pro permite gerar vídeos pra clientes pagantes. Sem royalties.

**Quantos vídeos posso gerar?**
Ilimitado por dia no Pro. O Trial tem limite de duração + watermark.

**A licença expira?**
Não — uma vez comprada, é vitalícia. Updates de versão maior podem custar separado.

## 🖥️ Requisitos técnicos

**Qual GPU preciso?**
NVIDIA com 4 GB+ VRAM (RTX 2060 ou superior). AMD/Intel não suportadas no momento.

**Roda no Mac?**
Não — só Windows 10/11 64-bit no momento. Linux experimental via WSL2 (não oficial).

**Quanto espaço em disco?**
50 GB livres recomendados (modelos + cache + outputs).

**Quanto RAM?**
8 GB mínimo, 16 GB recomendado.

**Roda offline?**
Sim, depois do setup inicial. Só Edge-TTS envia texto pra Microsoft (TTS público).
ElevenLabs/Replicate são opcionais e desligados por padrão.

## 🎬 Recursos

**Quantas vozes?**
322 vozes Edge-TTS (60+ idiomas), ElevenLabs opcional, F5-TTS voice cloning local.

**Posso clonar minha voz?**
Sim — F5-TTS local. Faz upload de 15-30s de áudio sua, pronto.

**Suporta video como input (não só foto)?**
Sim — Wav2Lip preserva o corpo e gestos do vídeo original, sincroniza só os lábios.

**Tem gestos com mão (não só cabeça)?**
Sim — Gesture Pack com 20 vídeos Pexels CC0 + face swap automático.

**Captions automáticas?**
Sim — Whisper transcribe + burn-in. Estilos: padrão ou karaoke (palavra-por-palavra
estilo Reels/TikTok) com cor de destaque customizável.

**Música de fundo?**
Sim — 3 tracks royalty-free incluídos + upload custom. Auto-duck automático
(música abaixa quando avatar fala, estilo HeyGen).

**Resolução máxima?**
1920×1080 nativo. Real-ESRGAN x2 upscale opcional pra ainda mais nitidez.

**Quanto tempo demora uma geração?**
Empírico em RTX 4060 8GB:
- 10s clip (sem enhancer): ~1 min
- 30s clip (gfpgan): ~7 min
- 30s clip (codeformer): ~20 min
- 2min clip (gfpgan): ~15-20 min

## 🛠️ Problemas comuns

**"GPU não detectada"**
Verifique se tem driver NVIDIA atualizado + CUDA 11.8+. Abra `nvidia-smi` no
PowerShell — se não funcionar, reinstale driver.

**"Out of memory" durante geração**
Sua VRAM é menor que o necessário. Tente: enhancer=none, vídeo mais curto,
desligar Real-ESRGAN. O sistema tem retry automático com batch reduzido.

**"Edge-TTS timeout"**
Conexão lenta com Microsoft. O sistema retry 3x automaticamente. Se persistir,
use upload de áudio próprio.

**"Job travado / progress não atualiza"**
Watchdog de 4h vai matar automaticamente. Pra cancelar manual: botão Cancel
na UI ou DELETE /api/job/<id>.

**Vídeo fica com voz dessincronizada do lábio**
Verifique se framerate do input é estável. Geralmente acontece com vídeos
de telefone com fps variável. Re-encode com `ffmpeg -i input.mp4 -r 25 fixed.mp4`.

## 🔐 Privacidade e segurança

**Meus vídeos vão pra nuvem?**
Não. Processamento 100% local na sua máquina.

**Vocês veem minhas fotos?**
Não. Nada sai do seu PC exceto: texto pro Edge-TTS (público da Microsoft) e,
se você ativar, prompts pra ElevenLabs/Replicate (opcional).

**Há telemetria?**
Não. O license system não envia analytics nem usage data.

**Posso usar fotos de rostos famosos?**
Tecnicamente sim, mas legalmente cuidado — direito de imagem aplica.
Use só com consentimento.

## 🤝 Suporte

**Email:** guilhermechaveshistory@gmail.com
**Issues:** https://github.com/guichaves13-cmd/gcg-automation/issues
**Update:** Quando lançarmos versão nova, você recebe email com instruções.
