"""
=============================================================================
TEST ALL ENDPOINTS — smoke test em TODOS os 167 endpoints do server.py
=============================================================================
Estrategia:
  - GET sem args -> espera 200/302/400/401/404 (qualquer um aceitavel; sem 500)
  - POST sem body -> espera 400/401/415 (sem crash 500)
  - Endpoints com <param> -> usa fake_id como placeholder
  - Endpoints de auth/login podem retornar 200 ou 401 (ok)
  - O CRITERIO: nenhum endpoint pode retornar 500 ou crash
=============================================================================
"""
import sys, os, json

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

GREEN = "\033[92m"; RED = "\033[91m"; CYAN = "\033[96m"; YELLOW = "\033[93m"; RESET = "\033[0m"
passes = 0; fails = 0; errors_list = []
unsafe_500 = 0

def ok(label, detail=""):
    global passes; passes += 1
    sfx = f" [{detail}]" if detail else ""
    try: print(f"  {GREEN}[PASS]{RESET} {label}{sfx}")
    except UnicodeEncodeError: print(f"  [PASS] {label}{sfx}".encode("ascii","replace").decode())

def fail(label, reason=""):
    global fails; fails += 1
    errors_list.append(f"{label}: {reason}")
    try: print(f"  {RED}[FAIL]{RESET} {label} -- {reason}")
    except UnicodeEncodeError: print(f"  [FAIL] {label} -- {reason}".encode("ascii","replace").decode())

print("=== Importing server ===")
from studiopilot_web.server import app
client = app.test_client()

# Get all endpoints
endpoints = []
for rule in app.url_map.iter_rules():
    if "static" in rule.endpoint: continue
    methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
    endpoints.append((str(rule), methods))
endpoints.sort()

print(f"\nTotal: {len(endpoints)} endpoints")
print(f"Strategy: smoke test cada endpoint, ASSERTAR no 500 nem crash")
print()

# Endpoints which expect specific args - we'll provide fake data
fake_payloads = {
    "/api/auth/login": {"username": "test", "password": "test"},
    "/api/auth/register": {"username": "test_smoke", "password": "test123"},
}

# Endpoints that should be skipped (destructive or long-running)
SKIP = {
    "/api/clean",  # destrutivo
    "/api/uploads/cleanup_all",  # destrutivo
    "/api/queue/clear",  # destrutivo
}

# Endpoints that may legitimately return 500 (need special config)
ACCEPT_500 = {
    # nenhum por padrao - queremos pegar todos
}

def _smoke_get(url):
    try:
        r = client.get(url)
        return r.status_code, None
    except Exception as e:
        return None, str(e)[:80]

def _smoke_post(url, payload=None):
    try:
        if payload is None:
            payload = {}
        r = client.post(url, data=json.dumps(payload),
                         content_type="application/json")
        return r.status_code, None
    except Exception as e:
        return None, str(e)[:80]

def _resolve_param(url):
    """Replace <param> placeholders with fake values."""
    import re
    return re.sub(r"<[^>]+>", "fake_id_xyz", url)


# Bulk test
for ep, methods in endpoints:
    test_url = _resolve_param(ep)
    if any(ep.startswith(s) for s in SKIP):
        ok(f"{methods[0]} {ep}", "SKIP (destrutivo)")
        continue

    for method in methods:
        # Pick smoke approach
        if method == "GET":
            sc, err = _smoke_get(test_url)
        elif method == "POST":
            payload = fake_payloads.get(ep, {})
            sc, err = _smoke_post(test_url, payload)
        else:
            try:
                r = client.open(test_url, method=method)
                sc = r.status_code
                err = None
            except Exception as e:
                sc, err = None, str(e)[:80]

        if err is not None:
            fail(f"{method} {ep}", f"crash: {err}")
            unsafe_500 += 1
        elif sc == 500:
            # 500 e' o problema critico
            # Tenta ver se foi handled pelo global_handler (com 'suggestion' no body)
            fail(f"{method} {ep}", f"HTTP 500 (crash interno)")
            unsafe_500 += 1
        elif sc in (200, 201, 202, 204, 301, 302, 304,
                     400, 401, 403, 404, 405, 409, 413, 415, 422):
            # Codigos esperados - endpoint respondeu OK ou rejeitou input invalido
            ok(f"{method} {ep}", f"{sc}")
        else:
            # Outros codigos (418 teapot, etc) - aceita mas reporta
            ok(f"{method} {ep}", f"{sc} (unusual)")


# =============================================================================
# RESULTADO
# =============================================================================
total = passes + fails
print(f"\n{'='*70}")
print(f"  RESULTADO: {passes}/{total} endpoints smoke OK")
if fails:
    print(f"  {fails} ENDPOINTS COM CRASH/500:")
    for e in errors_list[:30]:
        try: print(f"    - {e}")
        except UnicodeEncodeError: print(f"    - {e}".encode("ascii","replace").decode())
print(f"  Total crashes/500: {unsafe_500}")
print(f"{'='*70}\n")
sys.exit(0 if unsafe_500 == 0 else 1)
