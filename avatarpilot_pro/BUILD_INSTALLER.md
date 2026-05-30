# 📦 Como Gerar o Instalador do AvatarPilot Pro

Este documento descreve como gerar o instalador Windows (`AvatarPilotPro-Setup-X.Y.Z.exe`) que será distribuído aos clientes.

## ⚙️ Pré-requisitos (apenas você, vendor)

1. **Inno Setup 6** — https://jrsoftware.org/isinfo.php (instalar com defaults)
2. **Python 3.11+** disponível no PATH (para o `pip freeze`)
3. O venv311 já configurado (você já tem)

## 🏗️ Como compilar

### Opção A — Pela GUI do Inno Setup
1. Abra `Inno Setup Compiler`
2. File → Open → `avatarpilot_pro/AvatarPilotPro.iss`
3. Build → Compile (Ctrl+F9)
4. O instalador sairá em `avatarpilot_pro/dist/AvatarPilotPro-Setup-1.0.0.exe`

### Opção B — Linha de comando
```cmd
cd avatarpilot_pro
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" AvatarPilotPro.iss
```

## 📐 Arquitetura do instalador

| Tamanho | Conteúdo |
|---------|----------|
| **~150 MB** | server.py + license_system.py + frontend + scripts + launchers + EULA |
| **~7 GB** | `venv311/` baixado pelo `first_run_setup.bat` na primeira execução |
| **~38 GB** | Modelos de IA baixados na primeira execução |

**Por que não embutir tudo?** Um instalador de 45 GB é inviável para download. O modelo "instalador slim + setup na primeira execução" é o padrão da indústria para apps de ML (Stable Diffusion WebUI, ComfyUI, etc.).

## 🔑 Antes de distribuir — IMPORTANTE

### 1. Gerar o par de chaves de licença (UMA VEZ na sua máquina)
A primeira execução do servidor cria `.license_keys.json` com o par Ed25519 do vendor.

⚠️ **NUNCA distribua `.license_keys.json`** — ele contém a chave privada que assina licenças. Já está no `.gitignore`.

A chave PÚBLICA é embutida no app distribuído (lida do `.license_keys.json` automaticamente). Para builds em outras máquinas, defina `AVP_LICENSE_PUBKEY=<sua_chave_publica_hex>` como variável de ambiente do .exe shipado.

### 2. Configurar Stripe (vendor server)
No `data/stripe_config.json` (ou via Admin > Stripe):
- `stripe_secret_key` (sk_live_...)
- `stripe_webhook_secret` (whsec_...)
- `stripe_price_*` IDs dos preços por plano
- `smtp_*` para enviar a licença por email

O webhook (`/api/stripe/webhook`) deve estar acessível pela internet (via tunnel/server).
Configure no Dashboard Stripe → Webhooks → endpoint URL.

### 3. Customizar metadados
No `AvatarPilotPro.iss`:
- `AppVersion` — bump a cada release
- `AppId` GUID — mantenha fixo (define identidade do app no Windows)
- `LicenseFile` — ajuste a EULA em `LICENSE.txt` conforme necessário

## 🧪 Testar o instalador

1. Compile o instalador
2. Rode num **VM/máquina limpa** (não a sua de dev, que já tem tudo)
3. Verifique:
   - ✅ Instala sem permissão de admin (PrivilegesRequired=lowest)
   - ✅ Atalho no Menu Iniciar funciona
   - ✅ first_run_setup baixa Python deps com sucesso
   - ✅ Servidor sobe e abre o navegador
   - ✅ UI mostra Hardware ID + Status "trial"
   - ✅ Ativação de licença gerada por você funciona
   - ✅ Desinstalador remove tudo (exceto uploads/outputs do cliente)

## 🚀 Distribuir

O `.exe` resultante pode ser:
- Hospedado no seu site (com link no checkout pós-pagamento)
- Enviado por email após o pagamento
- Compartilhado via Google Drive / cloud storage

**Assinatura digital** (recomendado para evitar warning de SmartScreen): use um certificado de Code Signing (Sectigo, DigiCert) e assine com `signtool sign`. Sem assinatura, o Windows mostra "Editor desconhecido" no primeiro install.

## 📋 Checklist de release

- [ ] `AppVersion` atualizado no `.iss`
- [ ] `requirements.txt` regenerado (`pip freeze > requirements.txt`)
- [ ] Testes 100% verdes (rodar regressão: endpoints + licença + hardcore + extremos)
- [ ] CHANGELOG atualizado
- [ ] Instalador compilado em `dist/`
- [ ] Testado em VM limpa
- [ ] (Opcional) Assinatura digital aplicada
- [ ] Upload do `.exe` para distribuição
