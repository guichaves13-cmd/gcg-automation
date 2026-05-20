# Veo 3 Automation - Extensao Chrome

Extensao para automatizar a geracao de videos no Veo 3 com prompts em massa.

## Funcionalidades

- **Prompts em Massa**: Cole multiplos prompts separados por uma linha em branco
- **Processamento em Lotes**: Processa 5 prompts por vez (configuravel)
- **Tempo de Espera Automatico**: Aguarda 60 segundos entre lotes (configuravel)
- **Inclusao Automatica de Imagens**: Clica automaticamente em "Incluir no comando" para todas as imagens disponiveis
- **Pausar/Retomar**: Pause e retome a automacao a qualquer momento
- **Download em Massa**: Baixe todos os videos gerados de uma vez, em ordem dos prompts
- **Interface Intuitiva**: Visual moderno com log de progresso em tempo real

## Como Instalar

1. Abra o Chrome e navegue ate `chrome://extensions/`
2. Ative o **Modo do desenvolvedor** no canto superior direito
3. Clique em **Carregar sem compactacao**
4. Selecione a pasta `extension` deste projeto

## Como Usar

1. Navegue ate a pagina do Veo 3 (Flow/Criacao de cenas)
2. Clique no icone da extensao na barra do Chrome
3. Cole seus prompts na area de texto, separando cada um por uma **linha em branco**
4. Configure as opcoes:
   - **Prompts por lote**: Quantos prompts processar antes de esperar
   - **Tempo de espera**: Segundos para aguardar entre lotes
   - **Incluir imagens**: Se deve clicar em "Incluir no comando" nas imagens
5. Clique em **Iniciar Automacao**

## Controles de Automacao

- **Iniciar**: Comeca a processar os prompts
- **Pausar**: Pausa a automacao no proximo passo (amarelo)
- **Retomar**: Continua a automacao de onde parou
- **Parar**: Interrompe completamente a automacao

## Download de Videos

Apos gerar os videos, use a secao "Download de Videos" para:

1. Ver quantos videos foram detectados na pagina
2. Clicar em **Baixar Todos os Videos**
3. Escolher a pasta de destino (sera solicitado no primeiro arquivo)
4. Os videos serao baixados em ordem: veo3_video_001.mp4, veo3_video_002.mp4, etc.

Os videos sao baixados na mesma ordem em que os prompts foram processados.

## Fluxo de Automacao

Para cada prompt, a extensao:

1. Clica na aba **Imagens**
2. Clica em **Incluir no comando** em todas as imagens disponiveis
3. Volta para a aba **Videos**
4. Insere o prompt no campo de texto
5. Pressiona **Enter** para enviar
6. Repete ate processar todos os prompts

Apos processar o numero configurado de prompts (padrao: 5), aguarda o tempo configurado (padrao: 60 segundos) antes de continuar.

## Exemplo de Prompts

```
Cinematic wide shot of a car driving through mountains at sunset

A person walking through a futuristic city with neon lights

Underwater scene with colorful fish swimming around coral reef
```

## Notas

- A extensao funciona melhor quando a pagina do Veo 3 esta carregada completamente
- Se houver erros, verifique se voce esta na pagina correta
- O progresso e salvo automaticamente
- Videos sao rastreados durante a automacao para garantir ordem correta no download

## Estrutura de Arquivos

```
extension/
- manifest.json      # Configuracao da extensao
- popup.html         # Interface do popup
- popup.js           # Logica do popup
- content.js         # Script que roda na pagina
- background.js      # Service worker para downloads
- styles.css         # Estilos do popup
- icons/             # Icones da extensao
- README.md          # Este arquivo
```

## Solucao de Problemas

- **Botao nao encontrado**: A pagina pode ter sido atualizada. Verifique se esta na pagina correta do Veo 3.
- **Imagens nao incluidas**: Certifique-se de que ha imagens geradas antes de iniciar a automacao.
- **Automacao nao inicia**: Verifique se a extensao tem permissao para acessar a pagina.
- **Downloads nao funcionam**: Certifique-se de permitir downloads multiplos quando solicitado pelo navegador.

## Licenca

Este projeto e de uso pessoal e educacional.
