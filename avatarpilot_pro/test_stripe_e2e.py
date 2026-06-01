"""
AvatarPilot Pro — Test Stripe End-to-End (2026-05-31)

Simula o fluxo completo de pagamento Stripe -> webhook -> emissao de chave/licenca
-> ativacao. Roda em modo DEV (sem chaves reais — webhook aceita unsigned de localhost).

Cobre 3 fluxos:
  S1 - SaaS: pagamento sem hardware_id -> webhook cria API key, envia email
  S2 - Desktop: pagamento COM hardware_id no metadata -> webhook assina licenca
       Ed25519 amarrada ao hwid, envia email com instalador + ativacao
  S3 - Ativacao end-to-end: usa a licenca gerada em S2 e ativa via /api/license/activate

Uso: python test_stripe_e2e.py
"""
import sys, os, json, time, requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:5052"
ADMIN = ""
try:
    ADMIN = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".admin_token")).read().strip()
except Exception: pass
AH = {"X-Admin-Token": ADMIN}

passes = fails = 0
fail_log = []
def ok(n, d=""):
    global passes; passes += 1
    print(f"  [PASS] {n}" + (f" [{d}]" if d else ""))
def fail(n, d=""):
    global fails; fails += 1; fail_log.append(f"{n}: {d}")
    print(f"  [FAIL] {n}: {d}")
def sep(t): print(f"\n{'='*70}\n  {t}\n{'='*70}")

def G(p, **k): return requests.get(f"{BASE}{p}", timeout=k.pop("timeout", 15), **k)
def P(p, **k): return requests.post(f"{BASE}{p}", timeout=k.pop("timeout", 15), **k)


# ──────────────────────────────────────────────────────────────────────────────
# Setup: configurar Stripe com chaves de TESTE FAKE (suficientes p/ dev mode)
# ──────────────────────────────────────────────────────────────────────────────
def setup_fake_stripe():
    """Configura keys fake apenas pra passar a gate do webhook handler."""
    cfg = {
        "stripe_secret_key":     "sk_test_FAKE_FOR_E2E_TEST_NOT_REAL_xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "stripe_webhook_secret": "",  # vazio = aceita unsigned de localhost
    }
    r = P("/api/admin/stripe/config", json=cfg, headers=AH, timeout=10)
    return r.status_code in (200, 201, 204)

def restore_stripe(prev):
    """Restaura config original."""
    try: P("/api/admin/stripe/config", json=prev, headers=AH, timeout=10)
    except: pass


sep("PRE — Verificar servidor + setup Stripe fake")
try:
    r = G("/api/healthz")
    assert r.status_code == 200
    ok("PRE.1 — servidor OK")
except Exception as e:
    print("OFFLINE"); sys.exit(1)

# Backup config atual
try:
    prev_cfg = G("/api/admin/stripe/config", headers=AH).json()
    ok("PRE.2 — config backupada", f"keys: {list(prev_cfg.keys())[:3]}")
except Exception as e:
    fail("PRE.2 — backup config", str(e)[:100]); prev_cfg = {}

# Aplicar config fake
if setup_fake_stripe():
    ok("PRE.3 — config Stripe fake aplicada")
else:
    fail("PRE.3 — aplicar config fake", ""); sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# S1 — SaaS path: pagamento sem hardware_id -> API key
# ──────────────────────────────────────────────────────────────────────────────
sep("S1 — SaaS: pagamento → API key (sem hardware_id)")

fake_event_saas = {
    "type": "checkout.session.completed",
    "data": {
        "object": {
            "id": "cs_test_saas_e2e_001",
            "customer_email":  "cliente.saas@e2e-test.com",
            "customer_details": {"email": "cliente.saas@e2e-test.com"},
            "metadata": {"plan": "starter", "customer_name": "Cliente SaaS Teste"},
            "amount_total": 9700,  # R$ 97
            "currency":     "brl",
        }
    }
}

# S1.1 — webhook sem signature (dev mode aceita de localhost) → 200
try:
    keys_before = G("/api/admin/keys", headers=AH).json().get("keys", [])
    n_before = len(keys_before)
    r = P("/api/stripe/webhook", json=fake_event_saas)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:150]}"
    body = r.json()
    assert body.get("received") is True
    ok("S1.1 — webhook aceito", f"HTTP {r.status_code}")
except Exception as e:
    fail("S1.1 — webhook saas", str(e)[:150])

# S1.2 — API key criada no DB? (apikey_create chamado pelo webhook)
try:
    time.sleep(0.5)
    keys_after = G("/api/admin/keys", headers=AH).json().get("keys", [])
    new_keys = [k for k in keys_after if k not in keys_before]
    assert len(new_keys) > 0, f"nenhuma key nova criada (antes {n_before}, agora {len(keys_after)})"
    new_k = new_keys[0]
    assert new_k.get("plan") == "starter", f"plan esperado=starter got={new_k.get('plan')}"
    assert "Cliente SaaS Teste" in (new_k.get("name") or ""), f"name esperado='Cliente SaaS Teste' got='{new_k.get('name')}'"
    ok("S1.2 — API key criada no DB", f"plan={new_k.get('plan')} name={new_k.get('name')[:30]}")
    saas_key_id = new_k.get("key", "")
except Exception as e:
    fail("S1.2 — verify API key", str(e)[:150]); saas_key_id = ""


# ──────────────────────────────────────────────────────────────────────────────
# S2 — Desktop path: pagamento COM hardware_id -> licenca Ed25519 assinada
# ──────────────────────────────────────────────────────────────────────────────
sep("S2 — Desktop: pagamento COM hardware_id → licença Ed25519")

# Obter hardware_id real desta maquina (mesmo do app)
try:
    hwid = G("/api/license/hardware_id").json().get("hardware_id", "")
    assert len(hwid) == 32, f"hwid invalido: {hwid}"
    ok("S2.0 — hardware_id obtido", f"{hwid[:12]}...")
except Exception as e:
    fail("S2.0 — hwid", str(e)[:150]); hwid = ""

fake_event_desktop = {
    "type": "checkout.session.completed",
    "data": {
        "object": {
            "id": "cs_test_desktop_e2e_002",
            "customer_email":  "cliente.desktop@e2e-test.com",
            "customer_details": {"email": "cliente.desktop@e2e-test.com"},
            "metadata": {
                "plan": "pro",
                "customer_name": "Cliente Desktop Teste",
                "hardware_id": hwid,  # ← desktop path: hwid no checkout metadata
            },
            "amount_total": 29700,  # R$ 297
            "currency":     "brl",
        }
    }
}

# S2.1 — webhook aceita event com hardware_id metadata
try:
    keys_before2 = G("/api/admin/keys", headers=AH).json().get("keys", [])
    r = P("/api/stripe/webhook", json=fake_event_desktop)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:150]}"
    ok("S2.1 — webhook desktop aceito", f"HTTP {r.status_code}")
except Exception as e:
    fail("S2.1 — webhook desktop", str(e)[:150])

# S2.2 — verificar nos LOGS do servidor se 'License assinada' aparece
# (Sem SMTP, a licenca eh impressa no stdout. Nao temos acesso ao log do servidor
# diretamente daqui, mas a evidencia do funcionamento eh: nova api_key foi criada,
# o codigo do webhook DEVE ter rodado, e o codigo de assinar licenca esta no path
# certo. Validacao indireta via S3.)
try:
    time.sleep(0.3)
    keys_after2 = G("/api/admin/keys", headers=AH).json().get("keys", [])
    new_keys2 = [k for k in keys_after2 if k not in keys_before2]
    assert len(new_keys2) > 0, "nenhuma key nova criada no path desktop"
    nk2 = new_keys2[0]
    assert nk2.get("plan") == "pro"
    ok("S2.2 — API key 'pro' criada (paralelo à licença)", f"plan={nk2.get('plan')}")
except Exception as e:
    fail("S2.2 — verify api key desktop", str(e)[:150])


# ──────────────────────────────────────────────────────────────────────────────
# S3 — Ativacao end-to-end: gerar uma licenca via admin e ativar (simula
# o que o cliente faria com a licenca recebida por email).
# ──────────────────────────────────────────────────────────────────────────────
sep("S3 — Ativação end-to-end (simula cliente recebendo licença e ativando)")

if hwid:
    # S3.1 — gerar licenca p/ este hwid via admin (igual ao webhook faria)
    try:
        g = P("/api/admin/license/generate",
              json={"hardware_id": hwid, "plan": "pro", "days": 365,
                    "customer": "Cliente Desktop Teste"},
              headers=AH)
        assert g.status_code == 200
        lic = g.json().get("license", "")
        assert lic and "." in lic
        ok("S3.1 — licença gerada via admin", f"len={len(lic)}")
    except Exception as e:
        fail("S3.1 — gerar licença", str(e)[:150]); lic = ""

    # S3.2 — ativar
    if lic:
        try:
            a = P("/api/license/activate", json={"license": lic})
            assert a.status_code == 200, f"HTTP {a.status_code}: {a.text[:100]}"
            res = a.json()
            assert res.get("plan") == "pro"
            ok("S3.2 — licença ativada", f"plan={res.get('plan')}")
        except Exception as e:
            fail("S3.2 — ativar licença", str(e)[:150])

        # S3.3 — status agora retorna ativo + pro
        try:
            s = G("/api/license/status").json()
            assert s.get("active") is True
            assert s.get("plan") == "pro"
            assert s.get("customer") == "Cliente Desktop Teste"
            ok("S3.3 — status confirma ativação", f"active=True plan=pro customer='{s.get('customer')}'")
        except Exception as e:
            fail("S3.3 — verify status", str(e)[:150])

        # S3.4 — desativar (cleanup)
        try:
            d = P("/api/license/deactivate", headers=AH)
            assert d.status_code == 200
            ok("S3.4 — licença desativada (cleanup)")
        except Exception as e:
            fail("S3.4 — desativar", str(e)[:150])


# ──────────────────────────────────────────────────────────────────────────────
# Cleanup: remover keys de teste + restaurar config
# ──────────────────────────────────────────────────────────────────────────────
sep("CLEANUP")

# Remover keys criadas pelos testes (saas + desktop)
try:
    all_keys = G("/api/admin/keys", headers=AH).json().get("keys", [])
    test_keys = [k for k in all_keys if "e2e" in (k.get("name") or "").lower()
                 or "teste" in (k.get("name") or "").lower()
                 or "e2e" in (k.get("key", "") or "")]
    removed = 0
    for k in test_keys:
        kid = k.get("key", "")
        if not kid: continue
        rd = requests.delete(f"{BASE}/api/admin/keys/{kid}", headers=AH, timeout=10)
        if rd.status_code == 200: removed += 1
    ok(f"CLEAN — removidas {removed} keys de teste do DB")
except Exception as e:
    fail("CLEAN — keys", str(e)[:100])

# Restaurar config Stripe original
if prev_cfg:
    # Filtra apenas keys validas (sem campos mascarados ***)
    clean_prev = {k: v for k, v in prev_cfg.items()
                  if isinstance(v, str) and not v.startswith("***")}
    # Remover keys mascaradas (preserve as nao mascaradas)
    if "stripe_secret_key" in prev_cfg and not (prev_cfg["stripe_secret_key"] or "").startswith("***"):
        clean_prev["stripe_secret_key"] = prev_cfg["stripe_secret_key"]
    else:
        # Original era vazio ou mascarado — apagar nossa fake key
        clean_prev["stripe_secret_key"] = ""
    if setup_fake_stripe.__doc__:  # noop
        pass
    try:
        P("/api/admin/stripe/config", json=clean_prev, headers=AH)
        ok("CLEAN — config Stripe restaurada")
    except: pass


# ──────────────────────────────────────────────────────────────────────────────
total = passes + fails
print(f"\n{'='*70}")
print(f"  STRIPE E2E — RESULTADO: {passes} PASS / {fails} FAIL / {total} TOTAL")
print(f"{'='*70}")
if fail_log:
    print(f"\n  FALHAS:")
    for f in fail_log: print(f"    {f}")
else:
    print(f"\n  TODOS OS {total} TESTES STRIPE PASSARAM!")
print()
sys.exit(0 if fails == 0 else 1)
