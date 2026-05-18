# Gesture Videos — Ativando qualidade HeyGen com mãos gesticulando

## Por que isso é necessário

O pipeline padrão (SadTalker + MuseTalk + GFPGAN) gera um **talking head profissional** com:
- Sincronização labial perfeita
- Movimento natural de cabeça/pescoço
- Restauração facial HD/Full HD

**Mas não gera gestos com mãos** porque SadTalker só anima a região da cabeça/peito.

Para qualidade HeyGen completa com **mãos gesticulando e corpo se movendo** como pessoa real, é preciso fornecer um vídeo-template de uma pessoa gesticulando. O sistema então faz:

1. **Face swap** do seu avatar nesse vídeo (InsightFace + inswapper_128)
2. **Lip sync** com MuseTalk no vídeo face-swapped
3. **GFPGAN** para qualidade facial final
4. **HD encode 1080p**

Resultado: vídeo com o **rosto do seu avatar gesticulando como a pessoa do template**.

## Como adicionar um gesture video

1. Grave (ou baixe) um vídeo de **30-60 segundos** com:
   - Uma pessoa de frente para a câmera
   - Visível do peito para cima (mais corpo = melhor)
   - **Gesticulando naturalmente com as mãos**
   - Falando (qualquer áudio, será substituído)
   - Iluminação boa, fundo neutro
   - Resolução pelo menos 720p

2. Salve o arquivo `.mp4` aqui: `static/gesture_videos/`

3. Reinicie o servidor (Ctrl+C e rode `python server.py` de novo)

4. Na geração, selecione o gesture template no dropdown (ou passe `gesture_video=<path>` na API)

## Fontes de gesture videos royalty-free

- **Pexels Videos**: https://www.pexels.com/search/videos/business%20presenter/
- **Pixabay Videos**: https://pixabay.com/videos/search/talking/
- **Mixkit**: https://mixkit.co/free-stock-video/business/

Busque por termos como: "business presenter", "talking head", "explaining", "vlog speaking".

## Especificações ideais

- **Duração**: 30-60s (loops automaticamente para áudios mais longos)
- **Resolução**: 1280×720 ou 1920×1080
- **Frame rate**: 25 ou 30 fps
- **Codec**: H.264
- **Áudio**: pode ter ou não (será removido)

## Sem gesture video?

O pipeline ainda funciona perfeitamente sem gesture video — produz um talking head de alta qualidade com:
- Movimento de cabeça natural (SadTalker)
- Body sway sutil (respiração/balanço corporal)
- Sincronização labial perfeita
- 1080p Full HD output

Apenas não terá gestos com as mãos.
