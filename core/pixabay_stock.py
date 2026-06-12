"""
Pixabay Stock Footage Integration
Search and download free stock videos/images from Pixabay.
Free API key from: https://pixabay.com/api/docs/

v2: Added rate-limit retry, file validation, exponential backoff.
"""

import os
import time
import requests

PIXABAY_URL = "https://pixabay.com/api/"
PIXABAY_VIDEO_URL = "https://pixabay.com/api/videos/"
MAX_RETRIES = 3


def _request_with_retry(url, params, timeout=15):
    """HTTP GET with rate-limit retry and exponential backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  [Pixabay] Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            print(f"  [Pixabay] Timeout attempt {attempt + 1}/{MAX_RETRIES}")
            time.sleep(2 * (attempt + 1))
        except requests.exceptions.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 * (attempt + 1))
            else:
                print(f"  [Pixabay] HTTP error: {e}")
                return {}
        except Exception as e:
            print(f"  [Pixabay] Error: {e}")
            return {}
    return {}


def search_videos(api_key, query, count=10, min_duration=5):
    params = {"key": api_key, "q": query, "per_page": max(3, min(count * 2, 50)),
              "video_type": "film", "safesearch": "true"}
    data = _request_with_retry(PIXABAY_VIDEO_URL, params)
    
    results = []
    for v in data.get("hits", []):
        if v.get("duration", 0) < min_duration:
            continue
        vid = v.get("videos", {})
        hd = vid.get("large", vid.get("medium", {}))
        if hd.get("url"):
            # Pixabay thumbnails come from picture_id, NOT video id.
            # Without picture_id we'd build a broken URL → Vision sees Vimeo's
            # color-stripe placeholder and gives score 0 for every clip.
            picture_id = v.get("picture_id", "")
            if picture_id:
                thumb = f"https://i.vimeocdn.com/video/{picture_id}_640x360.jpg"
            else:
                # Fallback: use Pixabay's own preview image (always works)
                thumb = v.get("userImageURL") or v.get("pageURL", "") or ""
            results.append({
                "id": v["id"],
                "url": hd["url"],
                "duration": v.get("duration", 0),
                "width": hd.get("width", 1280),
                "height": hd.get("height", 720),
                "thumbnail_url": thumb,
                "tags": v.get("tags", ""),
                "page_url": v.get("pageURL", ""),
            })
        if len(results) >= count:
            break
    return results


def search_photos(api_key, query, count=10):
    params = {"key": api_key, "q": query, "per_page": max(3, min(count, 50)),
              "image_type": "photo", "orientation": "horizontal", "safesearch": "true"}
    data = _request_with_retry(PIXABAY_URL, params)
    
    return [{"id": p["id"], "url": p.get("largeImageURL", p.get("webformatURL", ""))}
            for p in data.get("hits", [])]


def download_file(url, output_path, timeout=60):
    """Download with retry and file validation."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=timeout, stream=True)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  [Pixabay] Download rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            
            # Validate file
            if os.path.getsize(output_path) < 10_000:
                print(f"  [Pixabay] File too small, retrying...")
                os.remove(output_path)
                continue
            
            return output_path
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  [Pixabay] Download retry {attempt+1}: {e}")
                time.sleep(2 * (attempt + 1))
            else:
                raise
    return output_path
