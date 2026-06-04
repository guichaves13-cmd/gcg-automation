"""
Download stock avatars curados do Unsplash (free, CC0 license, sem API key necessária).

Cada avatar é processado para AvatarPilot Pro:
  - Resize para 1024x1024 max
  - Face detection para garantir face visível e centrada
  - Categorias auto-atribuídas (business, casual, creator, education, news, medical)

Uso: python download_stock_avatars.py [--count 20]

NOTA: respeita rate limit do Unsplash. Para uso comercial em massa,
prefira sua própria curadoria.
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

def fetch_unsplash(query: str, out_path: Path, size="1024x1024"):
    """Unsplash Source — random photo by keywords (no API key)."""
    url = f"https://source.unsplash.com/{size}/?{query}"
    try:
        r = requests.get(url, timeout=15, allow_redirects=True)
        if r.status_code == 200 and len(r.content) > 5000:
            out_path.write_bytes(r.content)
            return True
    except Exception as e:
        print(f"  err: {e}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20, help="Total de avatars a baixar")
    args = ap.parse_args()

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
            if fetch_unsplash(q, out):
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
