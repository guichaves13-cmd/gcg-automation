# 🚀 Scale Roadmap — De 1 user para 100s de users

**Data:** 2026-06-09
**Modelo de negócio:** Desktop SaaS (compra única, roda local)
**Status atual:** Código pronto v1.1.0, falta infraestrutura externa

---

## 🎯 TL;DR — Para vender pra 100 users hoje:

**Necessário (estimativa 8-12h de setup):**
1. Webhook server público HTTPS (~$5/mês)
2. SMTP pra licenças (Gmail App Password = $0)
3. Stripe live keys + produtos
4. Landing page hosted (Cloudflare Pages = $0)
5. CDN pro installer download (Cloudflare R2 = $0)
6. Crash reporting (Sentry free = 5k events/mês)

**Total custo mensal estimado: ~$5-15/mês** pra primeiros 100 users.

---

## ✅ JÁ TÁ PRONTO (não precisa fazer nada)

| Item | Status | Onde |
|---|---|---|
| Código v1.1.0 | ✅ 100% | GitHub `guichaves13-cmd/gcg-automation` |
| 280+ testes | ✅ verde | Suites no repo |
| Pipeline production-ready | ✅ | Servidor estável |
| Stripe checkout endpoint | ✅ | `/api/stripe/checkout` |
| Stripe webhook handler | ✅ | `/api/stripe/webhook` |
| License system Ed25519 | ✅ | `license_system.py` |
| Hardware binding | ✅ | Anti-piracy |
| Daily quota tracking | ✅ | `_DAILY_USAGE_FILE` |
| Inno Setup installer | ✅ | `AvatarPilotPro.iss` |
| 16 docs markdown | ✅ | README, FAQ, EULA, etc. |
| Watchdog + auto-cleanup | ✅ | Anti-bloat |

---

## ⏳ FALTA (manual setup do vendor)

### 1. 🌐 Webhook server público HTTPS *(crítico)*
**Por quê:** Stripe envia webhook quando user paga. Precisa estar acessível pela internet via HTTPS.

**Opções:**
- **Render.com** ($7/mês, fácil): deploy do `server.py` que recebe webhook só
- **Railway.app** ($5/mês): mesmo deal
- **Vercel** (free pra hobby): Serverless function pra webhook
- **Cloudflare Workers** (free 100k req/dia): serverless

**Setup minimo:** subir um endpoint Python Flask que:
1. Recebe POST `/api/stripe/webhook`
2. Valida assinatura Stripe
3. Gera license key via `license_system.py`
4. Envia email pro user via SMTP
5. (Opcional) Loga em SQLite/DB

**Tempo:** 2-4h setup inicial.

### 2. 📧 SMTP para envio de licenças *(crítico)*
**Por quê:** User paga → tem que receber a license key por email automaticamente.

**Opções (free):**
- **Gmail App Password**: 500 emails/dia free. Setup 5 min.
- **SendGrid free**: 100 emails/dia free.
- **Mailgun free**: 5k emails/mês free.

**Setup:**
```python
# data/stripe_config.json
{
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "vendor@gmail.com",
    "smtp_pass": "GMAIL_APP_PASSWORD"
}
```

**Tempo:** 30 min setup.

### 3. 💳 Stripe live setup *(crítico)*
**Etapas:**
1. Criar produtos no Stripe Dashboard (Basic/Pro/Enterprise tiers)
2. Pegar `price_id` de cada
3. Configurar webhook URL pro endpoint público
4. Pegar `STRIPE_SECRET_KEY` (live, sk_live_...)
5. Pegar `STRIPE_WEBHOOK_SECRET` (whsec_...)
6. Salvar em `data/stripe_config.json`

**Tempo:** 1-2h setup.

### 4. 🏠 Landing page hosted *(importante)*
**O que precisa:**
- Pricing page (já existe `templates/pricing.html`)
- Demo video embed
- Download button → CDN do installer
- Stripe checkout button

**Opções:**
- **Cloudflare Pages** (free): conecta GitHub, deploy automático
- **Vercel** (free hobby): deploy de Next.js/static
- **Netlify** (free)

**Tempo:** 4-6h.

### 5. 📦 CDN para download do installer *(importante)*
**Por quê:** `.exe` tem ~150 MB. Distribuir do GitHub Release é OK mas lento. CDN dá UX melhor.

**Opções:**
- **Cloudflare R2** (free 10 GB egress/mês): zero custo até ~70 downloads
- **AWS S3 + CloudFront**: ~$1-5/mês
- **GitHub Releases** (free): suficiente pra começar, slow

**Tempo:** 1-2h.

### 6. 🐛 Crash reporting *(recomendado)*
**Por quê:** User reportará bugs por email. Crash reporter pega automaticamente.

**Opções:**
- **Sentry** (free 5k events/mês): SDK Python
- **Rollbar** (free 5k events/mês)
- **GlitchTip** (self-hosted free)

**Setup:**
```python
import sentry_sdk
sentry_sdk.init(dsn="https://...")
```

**Tempo:** 1h.

### 7. 📊 Analytics opt-in *(opcional)*
**Por quê:** Saber quais features mais usadas, quanto convertem, etc.

**Opções (privacy-friendly):**
- **PostHog** (free 1M events/mês): self-hosted ou cloud
- **Plausible** ($9/mês): zero cookies, GDPR
- **Custom em SQLite**: zero custo, simples

**Tempo:** 2-3h.

### 8. 🎓 Onboarding tutorial *(recomendado pós-launch)*
**O que:** Tutorial in-app no primeiro launch que guia através de:
1. Hardware ID display
2. License activation
3. First avatar generation
4. Settings tour

**Pode ser:** modal HTML/JS no `index.html` que aparece se `firstrun.json` não existe.

**Tempo:** 4-8h.

### 9. ⚖️ Legal docs *(antes de vender)*
- [x] EULA (`LICENSE.txt` existe)
- [ ] **Privacy Policy** — escrever (~2h)
- [ ] **Terms of Service** — escrever (~2h)
- [x] Refund Policy (`REFUND_POLICY.md` existe)

**Tempo:** 4h.

### 10. 🔄 Auto-update *(v1.2 feature)*
**O que:** App checa periodicamente se tem nova versão e oferece download.

**Implementação:**
```python
GET /api/version_check → server retorna {latest_version, download_url, notes}
Se localVersion < latestVersion: notifica user.
```

**Tempo:** 6-8h (próxima versão, não bloqueia launch).

---

## 📊 Capacidade técnica por escala

### 1-10 users
- ✅ Tudo já funciona como está
- Cada user roda no PC dele
- Vendor server: webhook only (~10 req/dia)

### 10-100 users
- ✅ Setup acima cobre 100% do que precisa
- Vendor server: ~100 req/dia, ainda free tier
- Suporte: 1 pessoa cuida via email
- Estimativa receita: 100 × $97 = $9700 (compra única)

### 100-1000 users
- ⚠️ Precisa **DB real** (Postgres) no vendor server pra license tracking
- ⚠️ Precisa **help desk** dedicado (Crisp/Intercom)
- ⚠️ Considerar **affiliate program** pra crescimento
- Vendor server: scale Render/Railway pra paid tier ($25/mês)

### 1000+ users
- ⚠️ **Multi-tenant cloud** opcional (NextJS frontend + Render API)
- ⚠️ **CRM** (HubSpot free / Pipedrive)
- ⚠️ **Team de suporte**
- Considerar **API hosted** alternativa (charge per render)

---

## 🎯 Checklist mínimo pra LANÇAR

Em ordem de prioridade:

- [ ] **#1 Compilar installer**: `ISCC.exe AvatarPilotPro.iss` (5 min)
- [ ] **#2 Test em VM clean** (30 min)
- [ ] **#3 Webhook server público** (Render/Railway, 2-4h)
- [ ] **#4 SMTP setup** (Gmail App Password, 30 min)
- [ ] **#5 Stripe live keys** (1-2h)
- [ ] **#6 Privacy Policy + ToS** (4h)
- [ ] **#7 Landing page** com Stripe checkout (4-6h)
- [ ] **#8 CDN installer download** (Cloudflare R2, 1-2h)
- [ ] **#9 Sentry crash reporting** (1h)
- [ ] **#10 Demo video gravado** (2-4h)

**Total estimado:** 16-24 horas de trabalho focado.

**Custo recorrente mensal:** $5-15 USD (Render/Railway + opcionais).

**ROI estimado:** Break-even em 1 venda. Vendendo a $97 (R$~500), bastam 100
vendas pra fazer ~$10k. ROI infinito após ponto de break-even.

---

## 🚨 Bug fixes pendentes antes de releaese

| Bug | Severidade | Status |
|---|---|---|
| AutoCleanup pode deletar outputs em <5GB disk | 🟡 Medium | Workaround: aumentar threshold ou flag `protected_outputs` |
| Mediapipe Windows path não-ASCII | 🟢 Low | Fallback Haar funciona, código ready |
| MuseTalk subprocess uninterruptible | 🟢 Low | Cancel grace 90s acceptable |
| Voice/avatar gender mismatch (UX) | 🟡 Medium | **Adicionar warning na UI quando voz não match foto** |

**Recomendação:** Fix o #1 (protected_outputs) + #4 (gender warning) antes
de release pra primeiros users. Os outros são acceptable.
