"""
Pexels Stock Footage Integration
Search and download free stock videos/images from Pexels.
Free API key from: https://www.pexels.com/api/
"""

import os
import time
import requests
from pathlib import Path

MAX_RETRIES = 3


PEXELS_VIDEO_URL = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_URL = "https://api.pexels.com/v1/search"


def search_videos(
    api_key: str,
    query: str,
    count: int = 10,
    min_duration: int = 5,
    orientation: str = "landscape",
) -> list:
    """
    Search for stock videos on Pexels.
    Returns list of dicts: {id, url, duration, width, height, preview_url}
    """
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "per_page": min(count * 2, 80),  # Request extra to filter
        "orientation": orientation,
    }

    data = {}
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(PEXELS_VIDEO_URL, headers=headers, params=params, timeout=15)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  [Pexels] Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.HTTPError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise

    results = []
    for video in data.get("videos", []):
        if video.get("duration", 0) < min_duration:
            continue

        # Get best quality HD file
        best_file = None
        for vf in video.get("video_files", []):
            if vf.get("quality") == "hd" and vf.get("width", 0) >= 1280:
                best_file = vf
                break
        if not best_file and video.get("video_files"):
            best_file = video["video_files"][0]

        if best_file:
            results.append({
                "id": video["id"],
                "url": best_file["link"],
                "duration": video.get("duration", 0),
                "width": best_file.get("width", 0),
                "height": best_file.get("height", 0),
                "preview_url": video.get("image", ""),
            })

        if len(results) >= count:
            break

    return results


def search_photos(
    api_key: str,
    query: str,
    count: int = 10,
    orientation: str = "landscape",
) -> list:
    """
    Search for stock photos on Pexels.
    Returns list of dicts: {id, url, width, height}
    """
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "per_page": min(count, 80),
        "orientation": orientation,
    }

    resp = requests.get(PEXELS_PHOTO_URL, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for photo in data.get("photos", []):
        src = photo.get("src", {})
        results.append({
            "id": photo["id"],
            "url": src.get("large2x", src.get("original", "")),
            "preview_url": src.get("medium", src.get("small", "")),
            "width": photo.get("width", 0),
            "height": photo.get("height", 0),
        })

    return results


def download_file(url: str, output_path: str, timeout: int = 60) -> str:
    """Download a file from URL to local path with retry."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=timeout, stream=True)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  [Pexels] Download rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Validate file size
            if os.path.getsize(output_path) < 10_000:
                print(f"  [Pexels] File too small, retrying...")
                os.remove(output_path)
                continue

            return output_path
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  [Pexels] Download retry {attempt+1}: {e}")
                time.sleep(2 * (attempt + 1))
            else:
                raise
    return output_path


def download_stock_footage(
    api_key: str,
    query: str,
    output_folder: str,
    video_count: int = 5,
    photo_count: int = 5,
    on_progress=None,
) -> list:
    """
    Search and download stock videos and photos from Pexels.

    Args:
        api_key: Pexels API key
        query: Search term (e.g. "ocean waves", "ancient temple")
        output_folder: Folder to save downloads
        video_count: Number of videos to download
        photo_count: Number of photos to download
        on_progress: Callback(current, total, status)

    Returns:
        List of downloaded file paths
    """
    os.makedirs(output_folder, exist_ok=True)
    downloaded = []
    total = video_count + photo_count
    current = 0

    # Download videos
    if video_count > 0:
        if on_progress:
            on_progress(0, total, f"Searching videos: '{query}'...")

        try:
            videos = search_videos(api_key, query, video_count)
            for i, video in enumerate(videos):
                output_path = os.path.join(output_folder, f"stock_v{i+1:03d}.mp4")
                if on_progress:
                    on_progress(current, total, f"Downloading video {i+1}/{len(videos)}...")
                download_file(video["url"], output_path)
                downloaded.append(output_path)
                current += 1
        except Exception as e:
            if on_progress:
                on_progress(current, total, f"Video search error: {str(e)[:50]}")

    # Download photos
    if photo_count > 0:
        if on_progress:
            on_progress(current, total, f"Searching photos: '{query}'...")

        try:
            photos = search_photos(api_key, query, photo_count)
            for i, photo in enumerate(photos):
                ext = ".jpg"
                output_path = os.path.join(output_folder, f"stock_p{i+1:03d}{ext}")
                if on_progress:
                    on_progress(current, total, f"Downloading photo {i+1}/{len(photos)}...")
                download_file(photo["url"], output_path)
                downloaded.append(output_path)
                current += 1
        except Exception as e:
            if on_progress:
                on_progress(current, total, f"Photo search error: {str(e)[:50]}")

    if on_progress:
        on_progress(total, total, f"Done! {len(downloaded)} files downloaded.")

    return downloaded
