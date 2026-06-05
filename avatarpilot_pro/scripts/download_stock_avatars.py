"""
Download stock avatars curados do Pexels (CC0, free com API key gratuita).

NOTA IMPORTANTE: Unsplash Source API foi DEPRECADA em maio/2024.
Este script agora usa Pexels (API key gratuita em pexels.com/api).

Cada avatar é processado para AvatarPilot Pro:
  - Resize para 1024x1024 max
  - Face detection para garantir face visível e centrada
  - Categorias auto-atribuídas (business, casual, creator, education, news, medical, tech)

Setup (one-time, free):
  1. Sign up em https://www.pexels.com/api/ (30 segundos)
  2. Copie sua API key
  3. python download_stock_avatars.py --key SUA_KEY --count 20

Sem API key: continua usando os 6 avatars bundled (Business_Woman, Casual_*,
Corporate_Executive, Influencer, Professional_Man). Suficiente pra começar.
"""
import os, sys, time, json, argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip install requests"); sys.exit(1)

BASE_DIR = Path(__file__).parent.parent
STOCK_DIR = BASE_DIR / "static" / "stock_avatars"
STOCK_DIR.mkdir(parents=True, exist_ok=True)
MANIFEST = STOCK_DIR / "_manifest.json"

# Query curadas por categoria — Unsplash source.unsplash.com (no API key needed)
CATEGORIES = {
    "business":  ["business-portrait", "corporate-headshot", "executive"],
    "casual":    ["casual-portrait", "smiling-person", "friendly"],
    "creator":   ["influencer", "creator", "youtuber"],
    "education": ["teacher", "professor", "educator"],
    "news":      ["news-anchor", "journalist", "presenter"],
    "medical":   ["doctor", "medical-professional", "healthcare"],
    "tech":      ["developer", "tech-professional", "engineer"],
}

def fetch_pexels(query: str, out_path: Path, api_key: str, orientation: str = "portrait"):
    """Pexels API — search + download. Free com API key (50req/h)."""
    headers = {"Authorization": api_key}
    params  = {"query": query, "per_page": 5, "orientation": orientation}
    try:
        r = requests.get("https://api.pexels.com/v1/search",
                         headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            print(f"  pexels HTTP {r.status_code}")
            return False
        photos = r.json().get("photos", [])
        if not photos:
            return False
        # Pega 1ª foto (com src large 1024+)
        photo = photos[0]
        img_url = photo.get("src", {}).get("large", "") or photo.get("src", {}).get("medium", "")
        if not img_url:
            return False
        img_r = requests.get(img_url, timeout=20)
        if img_r.status_code == 200 and len(img_r.content) > 5000:
            out_path.write_bytes(img_r.content)
            return True
    except Exception as e:
        print(f"  err: {e}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20, help="Total de avatars a baixar")
    ap.add_argument("--key",   type=str, default=os.environ.get("PEXELS_KEY", ""),
                    help="Pexels API key (ou export PEXELS_KEY=...)")
    args = ap.parse_args()
    if not args.key:
        print("ERRO: passe --key SUA_PEXELS_KEY ou export PEXELS_KEY=...")
        print("      Cadastro gratuito em https://www.pexels.com/api/")
        sys.exit(1)

    # Carrega manifest existente
    manifest = {}
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    downloaded = 0
    queries_per_cat = max(2, args.count // len(CATEGORIES))
    for cat, queries in CATEGORIES.items():
        for q in queries[:queries_per_cat]:
            if downloaded >= args.count: break
            stem = f"{cat}_{q.replace('-', '_')}_{int(time.time()*1000) % 100000}"
            out = STOCK_DIR / f"{stem}.jpg"
            print(f"[{downloaded+1}/{args.count}] {cat} -> {stem}...")
            if fetch_pexels(q, out, args.key):
                downloaded += 1
                manifest[stem] = {
                    "category": cat,
                    "credit":   "Unsplash CC0",
                    "query":    q,
                }
                time.sleep(1.5)  # rate limit
            else:
                print(f"  failed")

    # Persist manifest
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nDone! {downloaded} avatars baixados em {STOCK_DIR}")
    print(f"Manifest atualizado em {MANIFEST}")


if __name__ == "__main__":
    main()
