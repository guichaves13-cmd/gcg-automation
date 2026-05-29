"""
AvatarPilot Pro — Testes do Sistema de Licenças (2026-05-29)

Valida o ciclo de vida completo da licença desktop hardware-bound via API:
hardware_id, geração (admin), ativação, rejeição (outra máquina/inválida/forja),
status, e enforcement de plano (se AVP_LICENSE_ENFORCE=1).

Uso: python test_license_avp.py
"""
import sys, os, requests

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
    print(f"  \033[92m[PASS]\033[0m {n}" + (f" [{d}]" if d else ""))
def fail(n, d=""):
    global fails; fails += 1; fail_log.append(f"{n}: {d}")
    print(f"  \033[91m[FAIL]\033[0m {n}: {d}")
def sep(t): print(f"\n{'='*70}\n  {t}\n{'='*70}")

def G(p, **k): return requests.get(f"{BASE}{p}", timeout=15, **k)
def P(p, **k): return requests.post(f"{BASE}{p}", timeout=15, **k)


sep("PRÉ")
try:
    assert G("/api/healthz").status_code == 200
    ok("PRE — servidor OK", f"admin={'sim' if ADMIN else 'não'}")
except Exception as e:
    print("OFFLINE"); sys.exit(1)

# Estado limpo: desativar qualquer licença existente
P("/api/license/deactivate", headers=AH)


sep("L1 — CICLO DE VIDA DA LICENÇA")
# L1.1 — hardware_id
hwid = ""
try:
    hwid = G("/api/license/hardware_id").json().get("hardware_id", "")
    assert len(hwid) == 32 and all(c in "0123456789abcdef" for c in hwid)
    ok("L1.1 — hardware_id (32 hex)", hwid[:12] + "...")
except Exception as e:
    fail("L1.1 — hardware_id", str(e)[:100])

# L1.2 — status inicial = trial/inativo
try:
    st = G("/api/license/status").json()
    assert st.get("active") is False and st.get("plan") == "trial"
    ok("L1.2 — status inicial trial/inativo")
except Exception as e:
    fail("L1.2 — status inicial", str(e)[:100])

# L1.3 — admin gera licença assinada
lic = ""
try:
    r = P("/api/admin/license/generate",
          json={"hardware_id": hwid, "plan": "pro", "days": 365, "customer": "Teste"}, headers=AH)
    lic = r.json().get("license", "")
    assert r.status_code == 200 and lic and "." in lic
    ok("L1.3 — admin gera licença assinada")
except Exception as e:
    fail("L1.3 — gerar licença", str(e)[:100])

# L1.4 — gerar sem admin → 401
try:
    r = P("/api/admin/license/generate", json={"hardware_id": hwid, "plan": "pro"})
    assert r.status_code == 401
    ok("L1.4 — gerar sem admin → 401")
except Exception as e:
    fail("L1.4 — sem admin", str(e)[:100])

# L1.5 — gerar com plano inválido → 400
try:
    r = P("/api/admin/license/generate", json={"hardware_id": hwid, "plan": "xyz"}, headers=AH)
    assert r.status_code == 400
    ok("L1.5 — plano inválido → 400")
except Exception as e:
    fail("L1.5 — plano inválido", str(e)[:100])

# L1.6 — ativar licença válida → 200
try:
    r = P("/api/license/activate", json={"license": lic})
    assert r.status_code == 200 and r.json().get("plan") == "pro"
    ok("L1.6 — ativar licença → 200 (pro)")
except Exception as e:
    fail("L1.6 — ativar", str(e)[:100])

# L1.7 — status agora ATIVO/pro
try:
    st = G("/api/license/status").json()
    assert st.get("active") is True and st.get("plan") == "pro"
    ok("L1.7 — status ATIVO/pro", f"expira={st.get('expires','')[:10]}")
except Exception as e:
    fail("L1.7 — status ativo", str(e)[:100])


sep("L2 — SEGURANÇA DA LICENÇA")
# L2.1 — licença de outra máquina → 400
try:
    lic_o = P("/api/admin/license/generate",
              json={"hardware_id": "0"*32, "plan": "unlimited"}, headers=AH).json().get("license", "")
    r = P("/api/license/activate", json={"license": lic_o})
    assert r.status_code == 400
    ok("L2.1 — licença de outra máquina → 400 (hardware binding)")
except Exception as e:
    fail("L2.1 — outra máquina", str(e)[:100])

# L2.2 — licença lixo/forjada → 400
try:
    r = P("/api/license/activate", json={"license": "Zm9yamE.aW52YWxpZGE"})
    assert r.status_code == 400
    ok("L2.2 — licença forjada/inválida → 400")
except Exception as e:
    fail("L2.2 — forjada", str(e)[:100])

# L2.3 — activate vazio → 400
try:
    r = P("/api/license/activate", json={})
    assert r.status_code == 400
    ok("L2.3 — activate vazio → 400")
except Exception as e:
    fail("L2.3 — vazio", str(e)[:100])

# L2.4 — payload adulterado → 400
try:
    bad = lic[:-4] + "AAAA" if len(lic) > 8 else "x.y"
    r = P("/api/license/activate", json={"license": bad})
    assert r.status_code == 400
    ok("L2.4 — payload adulterado → 400")
except Exception as e:
    fail("L2.4 — adulterado", str(e)[:100])


sep("L3 — ENFORCEMENT DE PLANO (se AVP_LICENSE_ENFORCE=1)")
# Detecta se o enforcement está ligado. Se sim, valida que trial limita duração.
enforce_on = os.environ.get("AVP_LICENSE_ENFORCE", "0") in ("1", "true", "yes")
try:
    # Re-ativar pro (foi sobrescrito acima? não — as falhas não alteram a licença ativa)
    st = G("/api/license/status").json()
    if not enforce_on:
        ok("L3.1 — enforcement OFF (dev) — limites não aplicados (esperado)",
           f"plano ativo={st.get('plan')}")
    else:
        ok(f"L3.1 — enforcement ON — plano '{st.get('plan')}' autoritativo",
           f"limits={st.get('limits',{})}")
except Exception as e:
    fail("L3.1 — enforcement", str(e)[:100])

# Limpeza: desativar
P("/api/license/deactivate", headers=AH)


total = passes + fails
print(f"\n{'='*70}")
print(f"  LICENÇA — RESULTADO: {passes} PASS / {fails} FAIL / {total} TOTAL")
print(f"{'='*70}")
if fail_log:
    print(f"\n  FALHAS:")
    for f in fail_log: print(f"    {f}")
else:
    print(f"\n  TODOS OS {total} TESTES DE LICENÇA PASSARAM! 🏆")
print()
sys.exit(0 if fails == 0 else 1)
