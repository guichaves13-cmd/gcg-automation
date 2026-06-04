# 🚀 AvatarPilot Pro — Checklist Pré-Lançamento

> Use este documento ANTES de cada release para garantir produto pronto para usuários reais.

---

## 1. 🧪 Validação técnica

- [x] **226 testes verde** em 12 suites
- [x] **0 regressões** nas suites core (endpoints + advanced + extreme)
- [x] **Pipeline e2e** testado em clips 10s + 30s
- [x] **Stress queue** (5 jobs back-to-back) — validar rodando
- [x] **Adversarial inputs** (10 imagens difíceis) — 0 crashes
- [x] **Memory leak test** pós-warmup — dentro de tolerância
- [x] **Concurrent settings** (20 threads) — atomic write OK
- [ ] **Teste em VM clean** — instalar do zero (recomendado pra release)

## 2. 🔐 Segurança

- [x] `.api_keys.json` no `.gitignore`
- [x] `.license_keys.json` (chave privada vendor) no `.gitignore`
- [x] `license.dat` (licença ativada do cliente) no `.gitignore`
- [x] `.admin_token` no `.gitignore`
- [x] Rate limit ativo (10 jobs/IP/60s)
- [x] Path traversal bloqueado em static endpoints
- [x] AI keys mascaradas no GET /api/settings
- [x] Stripe webhook signature verification
- [ ] **Code signing certificate** (Sectigo/DigiCert) — evitar SmartScreen warning
- [ ] **HTTPS no webhook endpoint** (vendor server)

## 3. 📦 Distribuição

- [x] `AvatarPilotPro.iss` configurado (v1.1.0)
- [x] `Start AvatarPilot Pro.bat` com env vars corretas (50k cap)
- [x] `first_run_setup.bat` baixa venv + deps
- [x] `scripts/download_all_models.bat` baixa modelos
- [x] `models/haarcascade_frontalface_default.xml` bundled
- [x] `LICENSE.txt` presente
- [x] `README.md` atualizado com features novas
- [ ] **Compilar instalador**: `ISCC.exe AvatarPilotPro.iss`
- [ ] **Testar instalador em VM** Windows 10/11 limpa

## 4. 💰 Monetização

- [x] Ed25519 license system (`license_system.py`)
- [x] Hardware fingerprint binding
- [x] Stripe checkout endpoint
- [x] Stripe webhook handler
- [x] License email delivery via SMTP
- [x] Daily quota tracking
- [x] Plan enforcement opt-in via `AVP_LICENSE_ENFORCE=1`
- [ ] **Configurar stripe_config.json** com chaves live (vendor server)
- [ ] **Webhook URL pública** (via ngrok ou servidor com HTTPS)
- [ ] **SMTP credenciais** configuradas (Gmail App Password ou SendGrid)
- [ ] **Stripe Dashboard** — products + prices criados

## 5. 📣 Marketing

- [x] `DEMO_VIDEO_SCRIPT.md` (60s + 3min versions)
- [x] `README.md` público no GitHub
- [ ] **Gravar demo video** com features novas (karaoke, auto-duck, codeformer)
- [ ] **Landing page** ou `pricing.html` polida
- [ ] **FAQ** completa (preços, system requirements, refund policy)
- [ ] **Social proof** — testimonials, casos de uso
- [ ] **YouTube tutorial** "Como criar avatar falante em 5 min"

## 6. 🛟 Suporte

- [x] Email de contato no README
- [x] Issues GitHub público
- [x] Troubleshooting em RESUMO_AVATARPILOT.md
- [ ] **Knowledge base** com perguntas frequentes
- [ ] **Status page** (uptime do webhook server)
- [ ] **Refund policy** documentada

## 7. 🎬 Pre-launch Smoke Test (na VM)

Execute em ordem ANTES de divulgar:

1. [ ] Instala installer em VM Windows 10/11 limpa
2. [ ] First-run setup completa em <15 min
3. [ ] Servidor sobe em `http://localhost:5052`
4. [ ] UI carrega + Hardware ID visível
5. [ ] Submit job teste: foto + script curto + voz EN
6. [ ] Job completa em <5 min
7. [ ] Output 1920×1080 reproduzível
8. [ ] Trial → Ativar licença gerada pelo vendor
9. [ ] Job pós-licença funciona com quota correta
10. [ ] Desinstalador remove app + preserva uploads/outputs

## 8. 📊 Métricas pós-launch

- [ ] **Setup analytics** (opt-in, anônimo)
- [ ] **Crash reporting** (Sentry ou similar)
- [ ] **Daily active users** tracking
- [ ] **Average video duration** stats
- [ ] **Feature usage** (% que usa karaoke, gesture, etc.)

---

## ⚠️ NUNCA commitar (verificado)

- `.api_keys.json`
- `.license_keys.json`
- `license.dat`
- `.admin_token`
- `stripe_config.json` com chaves live
- `users.json` (se existir)
- `.env` files
- `data/*.db` (SQLite com jobs históricos)

---

## 🎯 Critério de "PRONTO"

Todos os checkboxes da seção 1 (Validação), 2 (Segurança), 3 (Distribuição) marcados +
pelo menos 1 checkbox de cada outra seção. Seções 5/6/8 podem evoluir pós-launch.
