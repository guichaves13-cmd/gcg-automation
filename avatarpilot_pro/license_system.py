"""
AvatarPilot Pro — Sistema de Licenças Desktop (2026-05-29)

Licenciamento offline amarrado ao hardware, anti-pirataria via Ed25519:
  - Hardware fingerprint estável (MachineGuid + MAC + CPU)
  - Licença = token assinado: <payload_b64>.<assinatura_b64>
    payload = {hardware_id, plan, expires, customer, issued, v}
  - O APP valida offline com a chave PÚBLICA embutida (não pode forjar)
  - O VENDOR assina com a chave PRIVADA (mantida só no license authority)

Fluxo:
  1. App mostra o hardware_id (get_hardware_id)
  2. Cliente paga (Stripe) → vendor assina licença p/ aquele hardware_id (sign_license)
  3. Cliente cola a licença no app → activate() valida e salva license.dat
  4. No boot, load_active_license() valida assinatura + hardware + expiração

A chave privada NUNCA vai pro cliente — fica em .license_keys.json (gitignored),
usada só pelo endpoint admin de geração / webhook do Stripe (license authority).
"""
import os, json, base64, hashlib, platform, uuid
from datetime import datetime, timezone, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.exceptions import InvalidSignature

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE    = os.path.join(BASE_DIR, ".license_keys.json")   # vendor (privada) — NÃO distribuir
LICENSE_FILE = os.path.join(BASE_DIR, "license.dat")          # licença ativada do cliente

PLAN_LIMITS = {
    "trial":     {"max_seconds": 30,   "watermark": True,  "daily_jobs": 3},
    "starter":   {"max_seconds": 120,  "watermark": False, "daily_jobs": 30},
    "pro":       {"max_seconds": 600,  "watermark": False, "daily_jobs": 200},
    "unlimited": {"max_seconds": 0,    "watermark": False, "daily_jobs": 0},  # 0 = sem limite
}


# ─── Hardware fingerprint ─────────────────────────────────────────────────────
def get_hardware_id() -> str:
    """Fingerprint estável da máquina: SHA-256 de MachineGuid + MAC + CPU + node."""
    parts = []
    # Windows MachineGuid (muito estável — sobrevive a reinstalação de apps)
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\Cryptography")
        guid, _ = winreg.QueryValueEx(k, "MachineGuid")
        winreg.CloseKey(k)
        parts.append(str(guid))
    except Exception:
        pass
    # MAC address (uuid.getnode)
    try:
        parts.append(format(uuid.getnode(), "x"))
    except Exception:
        pass
    parts.append(platform.processor() or "")
    parts.append(platform.node() or "")
    raw = "|".join(p for p in parts if p)
    if not raw:
        raw = "unknown-machine"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ─── Geração de chaves (vendor — rodar UMA vez) ───────────────────────────────
def license_keygen() -> tuple[str, str]:
    """Gera par Ed25519. Retorna (priv_hex, pub_hex)."""
    priv = Ed25519PrivateKey.generate()
    return (priv.private_bytes_raw().hex(),
            priv.public_key().public_bytes_raw().hex())

def ensure_vendor_keys() -> dict:
    """Garante que o par de chaves do vendor existe (cria se faltar). Retorna o dict."""
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("private_hex") and d.get("public_hex"):
                return d
        except Exception:
            pass
    priv_hex, pub_hex = license_keygen()
    d = {"private_hex": priv_hex, "public_hex": pub_hex,
         "created": datetime.now(timezone.utc).isoformat()}
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    try:
        if os.name == "nt":
            import subprocess
            subprocess.run(["icacls", KEYS_FILE, "/inheritance:r", "/grant:r",
                            f"{os.environ.get('USERNAME','')}:F"],
                           capture_output=True, timeout=10)
    except Exception:
        pass
    return d

def get_public_key_hex() -> str:
    """Chave pública embutida (env AVP_LICENSE_PUBKEY) ou do keystore local do vendor."""
    env = os.environ.get("AVP_LICENSE_PUBKEY", "").strip()
    if env:
        return env
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("public_hex", "")
    except Exception:
        return ""


# ─── Assinatura (vendor) ──────────────────────────────────────────────────────
def sign_license(hardware_id: str, plan: str = "pro", days: int = 365,
                 customer: str = "", priv_hex: str = "") -> str:
    """Vendor: assina uma licença p/ um hardware_id específico. Retorna a string."""
    if not priv_hex:
        priv_hex = ensure_vendor_keys()["private_hex"]
    if plan not in PLAN_LIMITS:
        plan = "pro"
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    expires = "never" if days <= 0 else \
        (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    payload = {"hardware_id": hardware_id, "plan": plan, "expires": expires,
               "customer": customer,
               "issued": datetime.now(timezone.utc).isoformat(), "v": 1}
    pj = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = priv.sign(pj)
    return (_b64e(pj) + "." + _b64e(sig))


# ─── Validação (cliente, offline) ─────────────────────────────────────────────
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")

def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def verify_license(license_str: str, hardware_id: str = None,
                   pub_hex: str = "") -> tuple[bool, object]:
    """Valida assinatura + hardware + expiração. Retorna (ok, payload|msg_erro)."""
    if not pub_hex:
        pub_hex = get_public_key_hex()
    if not pub_hex:
        return False, "Chave pública do vendor ausente (instalação inválida)"
    try:
        pj_b64, sig_b64 = license_str.strip().split(".")
        pj = _b64d(pj_b64)
        sig = _b64d(sig_b64)
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex)).verify(sig, pj)
        payload = json.loads(pj)
    except InvalidSignature:
        return False, "Assinatura inválida — licença forjada ou corrompida"
    except Exception as e:
        return False, f"Licença malformada: {e}"
    if hardware_id and payload.get("hardware_id") != hardware_id:
        return False, "Esta licença pertence a outra máquina (hardware ID não confere)"
    exp = payload.get("expires", "never")
    if exp != "never":
        try:
            if datetime.fromisoformat(exp) < datetime.now(timezone.utc):
                return False, f"Licença expirada em {exp[:10]}"
        except Exception:
            return False, "Data de expiração inválida na licença"
    return True, payload


# ─── Ativação + estado (cliente) ──────────────────────────────────────────────
def activate(license_str: str) -> tuple[bool, object]:
    """Valida a licença p/ ESTA máquina e salva em license.dat. Retorna (ok, payload|erro)."""
    hwid = get_hardware_id()
    ok, res = verify_license(license_str, hardware_id=hwid)
    if not ok:
        return False, res
    try:
        with open(LICENSE_FILE, "w", encoding="utf-8") as f:
            f.write(license_str.strip())
    except Exception as e:
        return False, f"Falha ao salvar licença: {e}"
    return True, res

def load_active_license() -> dict:
    """Carrega+valida a licença ativada. Retorna {active, plan, expires, ...} sempre."""
    hwid = get_hardware_id()
    if not os.path.exists(LICENSE_FILE):
        return {"active": False, "plan": "trial", "hardware_id": hwid,
                "reason": "Nenhuma licença ativada — modo trial",
                "limits": PLAN_LIMITS["trial"]}
    try:
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            lic = f.read().strip()
    except Exception as e:
        return {"active": False, "plan": "trial", "hardware_id": hwid,
                "reason": f"Erro ao ler licença: {e}", "limits": PLAN_LIMITS["trial"]}
    ok, res = verify_license(lic, hardware_id=hwid)
    if not ok:
        return {"active": False, "plan": "trial", "hardware_id": hwid,
                "reason": res, "limits": PLAN_LIMITS["trial"]}
    plan = res.get("plan", "pro")
    return {"active": True, "plan": plan, "hardware_id": hwid,
            "expires": res.get("expires", "never"),
            "customer": res.get("customer", ""),
            "issued": res.get("issued", ""),
            "limits": PLAN_LIMITS.get(plan, PLAN_LIMITS["pro"])}

def deactivate() -> bool:
    try:
        if os.path.exists(LICENSE_FILE):
            os.remove(LICENSE_FILE)
        return True
    except Exception:
        return False


# ─── CLI / self-test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print("=== SELF-TEST license_system ===")
        ok_count = fail_count = 0
        def chk(name, cond):
            global ok_count, fail_count
            if cond: ok_count += 1; print(f"  [PASS] {name}")
            else:    fail_count += 1; print(f"  [FAIL] {name}")

        priv, pub = license_keygen()
        hwid = get_hardware_id()
        chk("hardware_id 32 hex", len(hwid) == 32 and all(c in "0123456789abcdef" for c in hwid))
        chk("hardware_id estável (2 chamadas iguais)", get_hardware_id() == hwid)

        lic = sign_license(hwid, plan="pro", days=365, customer="Teste", priv_hex=priv)
        ok, res = verify_license(lic, hardware_id=hwid, pub_hex=pub)
        chk("licença válida verifica OK", ok and res.get("plan") == "pro")

        # hardware errado
        ok2, _ = verify_license(lic, hardware_id="0"*32, pub_hex=pub)
        chk("licença de outra máquina rejeitada", not ok2)

        # chave pública errada (forja)
        _, pub2 = license_keygen()
        ok3, _ = verify_license(lic, hardware_id=hwid, pub_hex=pub2)
        chk("assinatura com chave errada rejeitada", not ok3)

        # tamper no payload
        tampered = lic.split(".")[0][:-2] + "XX." + lic.split(".")[1]
        ok4, _ = verify_license(tampered, hardware_id=hwid, pub_hex=pub)
        chk("payload adulterado rejeitado", not ok4)

        # expirada
        lic_exp = sign_license(hwid, plan="pro", days=-1, customer="X", priv_hex=priv)
        # days<=0 vira "never", então forçar expiração real:
        priv_k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv))
        payload = {"hardware_id": hwid, "plan": "pro",
                   "expires": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                   "customer": "X", "issued": "x", "v": 1}
        pj = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        lic_exp = _b64e(pj) + "." + _b64e(priv_k.sign(pj))
        ok5, msg5 = verify_license(lic_exp, hardware_id=hwid, pub_hex=pub)
        chk("licença expirada rejeitada", not ok5)

        # unlimited never-expira
        lic_unl = sign_license(hwid, plan="unlimited", days=0, priv_hex=priv)
        ok6, res6 = verify_license(lic_unl, hardware_id=hwid, pub_hex=pub)
        chk("licença unlimited never-expira", ok6 and res6.get("expires") == "never")

        print(f"\n  RESULTADO: {ok_count} PASS / {fail_count} FAIL")
        sys.exit(0 if fail_count == 0 else 1)
    elif len(sys.argv) > 1 and sys.argv[1] == "hwid":
        print(get_hardware_id())
    else:
        print("uso: python license_system.py [test|hwid]")
