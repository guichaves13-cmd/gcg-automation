"""
YouTube B-roll Downloader v3
- Busca videos por keyword (CC ou geral)
- Baixa apenas 5-7s do meio do video (evita intro/logo/outro)
- Remove audio completamente (avatar fornece o audio)
- Crop leve para eliminar watermarks nos cantos
- Suporta busca dentro de canais especificos
- Retry com exponential backoff (3 tentativas)
- Timeout 30s por download FFmpeg
- Validacao: arquivo deve ter > 100KB
"""
import os
import re
import time
import shutil
import subprocess


# ─── Configuração de segmento ───────────────────────────────────────────
CLIP_DURATION  = 6        # segundos de cada clip extraido
SKIP_START_PCT = 0.20     # pula os primeiros 20% do video (intro/logo)
SKIP_END_PCT   = 0.15     # pula os ultimos 15% (outro/CTA)
MAX_VIDEO_DL   = 90       # maximo de duracao do video fonte a baixar (s)
CROP_MARGIN    = 8        # pixels a cortar em cada borda (watermarks de canto)
MIN_FILE_BYTES = 100_000  # 100KB minimo para considerar download valido
FFMPEG_TIMEOUT = 30       # segundos de timeout para o FFmpeg de extracao


def _ensure_ytdlp() -> bool:
    """Verifica se yt-dlp esta instalado. Instala automaticamente se nao estiver."""
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        print("  [YouTube] yt-dlp nao encontrado. Instalando...")
        try:
            result = subprocess.run(
                ["pip", "install", "yt-dlp", "-q"],
                capture_output=True, timeout=120
            )
            if result.returncode == 0:
                print("  [YouTube] yt-dlp instalado com sucesso.")
                return True
            print(f"  [YouTube] Falha ao instalar yt-dlp: {result.stderr.decode()[:200]}")
            return False
        except Exception as e:
            print(f"  [YouTube] Erro ao instalar yt-dlp: {e}")
            return False


def search_and_download(keyword: str, output_path: str,
                        youtube_api_key: str = "",
                        max_duration: int = 30,
                        channel_ids: list = None,
                        channel_names: list = None) -> bool:
    """
    Busca um video relevante no YouTube e extrai um segmento de 5-7s.

    Prioridade:
    1. Canais especificos via API (channel_ids) -- maxima relevancia de nicho
    2. YouTube Data API com filtro Creative Commons (seguro para monetizacao)
    3. yt-dlp search com channel_names como boost nas queries
    4. yt-dlp search generico
    5. Multi-query fallback (simplifica keyword, adiciona 'footage'/'b-roll')

    Todas as tentativas usam retry com exponential backoff (3x).
    """
    if not _ensure_ytdlp():
        print(f"    [YouTube] FAIL '{keyword}': yt-dlp unavailable")
        return False

    attempted = []  # Track each attempt for clear failure reporting

    # 1. Canais especificos via API
    if youtube_api_key and channel_ids:
        for ch_id in (channel_ids or []):
            attempted.append(f"channel:{ch_id[:12]}")
            videos = _search_api(keyword, youtube_api_key, channel_id=ch_id, cc_only=False)
            if videos:
                ok = _download_segment_with_retry(videos[0]["url"], output_path)
                if ok:
                    print(f"    [YouTube-Canal] OK '{keyword}' -> {videos[0]['title'][:50]}")
                    return True
                else:
                    print(f"    [YouTube-Canal] FAIL '{keyword}' on {ch_id[:12]}: download failed")

    # 2. API geral com CC
    if youtube_api_key:
        attempted.append("api+CC")
        videos = _search_api(keyword, youtube_api_key, cc_only=True)
        if videos:
            ok = _download_segment_with_retry(videos[0]["url"], output_path)
            if ok:
                print(f"    [YouTube-CC] OK '{keyword}' -> {videos[0]['title'][:50]}")
                return True
            else:
                print(f"    [YouTube-CC] FAIL '{keyword}': download failed after retries")
        else:
            print(f"    [YouTube-CC] no CC results for '{keyword}'")

        # API w/o CC filter as a softer fallback (still real videos from YT)
        attempted.append("api")
        videos = _search_api(keyword, youtube_api_key, cc_only=False)
        if videos:
            ok = _download_segment_with_retry(videos[0]["url"], output_path)
            if ok:
                print(f"    [YouTube-API] OK '{keyword}' -> {videos[0]['title'][:50]}")
                return True
            else:
                print(f"    [YouTube-API] FAIL '{keyword}': download failed")

    # 3. yt-dlp com channel_names como boost (sem API key)
    if channel_names:
        attempted.append("ytdlp+channels")
        if _ytdlp_search(keyword, output_path, channel_name_hints=channel_names):
            return True

    # 4. yt-dlp search generico
    attempted.append("ytdlp")
    if _ytdlp_search(keyword, output_path):
        return True

    # 5. Multi-query fallback — try variations
    fallback_queries = _generate_fallback_queries(keyword)
    for fq in fallback_queries:
        attempted.append(f"fallback:{fq[:30]}")
        print(f"    [YouTube] Fallback query: '{fq}'")
        if _ytdlp_search(fq, output_path):
            return True

    # All sources exhausted — log clearly so this doesn't fail silently
    print(f"    [YouTube] EXHAUSTED '{keyword}' — tried: {', '.join(attempted)}")
    return False


def _generate_fallback_queries(keyword: str) -> list:
    """Generate simplified fallback search queries from original keyword."""
    queries = []
    words = keyword.strip().split()
    
    # Add 'footage' suffix
    queries.append(f"{keyword} footage")
    
    # Add 'b-roll' suffix  
    queries.append(f"{keyword} b-roll")
    
    # Simplify to 2 words if longer
    if len(words) > 2:
        queries.append(" ".join(words[:2]))
        queries.append(" ".join(words[-2:]))
    
    # Single most important word (skip common adjectives)
    skip = {"the", "a", "an", "of", "in", "on", "for", "with", "close", "up", "wide", "shot"}
    important = [w for w in words if w.lower() not in skip and len(w) > 3]
    if important:
        queries.append(f"{important[0]} video")
    
    return queries[:4]  # Max 4 fallback attempts


def download_from_url(url: str, output_path: str) -> bool:
    """Baixa de uma URL YouTube especifica e extrai segmento de 5-7s."""
    if not _ensure_ytdlp():
        return False
    return _download_segment_with_retry(url, output_path)


# ─── Busca via YouTube Data API ─────────────────────────────────────────

def _search_api(keyword: str, api_key: str,
                cc_only: bool = True,
                channel_id: str = None,
                max_results: int = 5) -> list:
    try:
        import requests
        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "videoDuration": "short",
            "maxResults": max_results,
            "key": api_key,
            "order": "relevance",
            "safeSearch": "moderate",
        }
        if cc_only:
            params["videoLicense"] = "creativeCommon"
        if channel_id:
            params["channelId"] = channel_id

        r = requests.get("https://www.googleapis.com/youtube/v3/search",
                         params=params, timeout=10)
        r.raise_for_status()
        items = r.json().get("items", [])
        video_ids = [i["id"]["videoId"] for i in items if i["id"].get("videoId")]
        if not video_ids:
            return []

        stats_r = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "contentDetails,snippet", "id": ",".join(video_ids), "key": api_key},
            timeout=10
        )
        stats_r.raise_for_status()

        results = []
        for item in stats_r.json().get("items", []):
            dur = _parse_iso(item["contentDetails"]["duration"])
            if 8 <= dur <= MAX_VIDEO_DL:
                results.append({
                    "id": item["id"],
                    "title": item["snippet"]["title"],
                    "duration": dur,
                    "url": f"https://www.youtube.com/watch?v={item['id']}",
                })
        return results
    except Exception as e:
        print(f"    [YouTube API] Erro: {e}")
        return []


# ─── Download + extração de segmento ────────────────────────────────────

def _download_segment_with_retry(url: str, output_path: str, max_retries: int = 3) -> bool:
    """
    Wrapper com retry e exponential backoff para _download_segment.
    3 tentativas: esperas 2s, 4s, 8s entre falhas.
    Retorna False (nao levanta excecao) se todas as tentativas falharem.
    """
    for attempt in range(max_retries):
        try:
            ok = _download_segment(url, output_path)
            if ok:
                return True
        except Exception as e:
            print(f"    [YouTube] Tentativa {attempt+1}/{max_retries} falhou: {e}")

        if attempt < max_retries - 1:
            wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
            print(f"    [YouTube] Aguardando {wait}s antes da proxima tentativa...")
            time.sleep(wait)

        # Limpa arquivo parcial entre tentativas
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass

    return False


def _download_segment(url: str, output_path: str) -> bool:
    """
    Baixa o video fonte e extrai um segmento de CLIP_DURATION segundos
    do meio do video (evitando intro e outro), sem audio, com crop leve.

    Raises Exception em caso de erro (use _download_segment_with_retry para retry automatico).
    """
    import yt_dlp

    ffmpeg = _find_ffmpeg()
    tmp_raw = output_path.replace(".mp4", "_ytraw.mp4")

    # Passo 1: baixar video completo (sem audio, 720p max)
    ydl_opts = {
        "format": "bestvideo[ext=mp4][height<=720]/bestvideo[height<=720]/best[height<=720]",
        "outtmpl": tmp_raw.replace(".mp4", ".%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": 200 * 1024 * 1024,
        "socket_timeout": 30,
        "postprocessors": [],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            total_dur = info.get("duration", 0) if info else 0
    except yt_dlp.utils.DownloadError as dle:
        msg = str(dle)[:200]
        print(f"    [YouTube] yt-dlp DownloadError: {msg}")
        raise Exception(f"yt-dlp download error: {msg}")
    except yt_dlp.utils.ExtractorError as exe:
        msg = str(exe)[:200]
        print(f"    [YouTube] yt-dlp ExtractorError: {msg}")
        raise Exception(f"yt-dlp extractor error: {msg}")

    # Resolve o arquivo baixado (pode ter extensao diferente)
    raw_file = _resolve_file(tmp_raw)
    if not raw_file:
        print(f"    [YouTube] No file found after download (tmp_raw={tmp_raw})")
        return False

    try:
        # Passo 2: determinar ponto de corte no meio do video
        if total_dur <= 0:
            total_dur = _probe_duration(raw_file, ffmpeg)

        if total_dur < CLIP_DURATION + 2:
            ss = 0.5
        else:
            usable_start = total_dur * SKIP_START_PCT
            usable_end   = total_dur * (1.0 - SKIP_END_PCT)
            mid = (usable_start + usable_end) / 2.0
            ss = max(usable_start, mid - CLIP_DURATION / 2.0)
            ss = min(ss, usable_end - CLIP_DURATION)

        # Passo 3: extrair segmento + crop + sem audio
        cmd = [
            ffmpeg, "-y",
            "-ss", f"{ss:.2f}",
            "-i", raw_file,
            "-t", str(CLIP_DURATION),
            "-vf", (
                f"crop=iw-{CROP_MARGIN*2}:ih-{CROP_MARGIN*2}:{CROP_MARGIN}:{CROP_MARGIN},"
                f"scale=1280:720:force_original_aspect_ratio=decrease,"
                f"pad=1280:720:(ow-iw)/2:(oh-ih)/2"
            ),
            "-an",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            output_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"    [YouTube] ffmpeg extract TIMED OUT after {FFMPEG_TIMEOUT}s")
            return False

        if result.returncode != 0:
            err = (result.stderr or b"").decode("utf-8", errors="replace")[-300:]
            print(f"    [YouTube] ffmpeg extract rc={result.returncode}: {err}")

        ok = (
            result.returncode == 0
            and os.path.exists(output_path)
            and os.path.getsize(output_path) >= MIN_FILE_BYTES
        )

        if ok:
            size_kb = os.path.getsize(output_path) // 1024
            print(f"    [YouTube] Segmento extraido: {ss:.0f}s-{ss+CLIP_DURATION:.0f}s ({size_kb}KB, sem audio)")
        elif os.path.exists(output_path):
            too_small = os.path.getsize(output_path)
            print(f"    [YouTube] Arquivo muito pequeno ({too_small} bytes < {MIN_FILE_BYTES}) -- descartando")
            os.remove(output_path)
            ok = False

        return ok

    finally:
        try:
            os.remove(raw_file)
        except Exception:
            pass


# ─── yt-dlp search sem API key ──────────────────────────────────────────

def _ytdlp_search(keyword: str, output_path: str, channel_name_hints: list = None) -> bool:
    """Busca no YouTube via yt-dlp + extrai segmento.

    Se channel_name_hints fornecido, tenta primeiro buscar em cada canal por nome
    antes de fazer busca genérica.
    """
    try:
        import yt_dlp
    except ImportError:
        return False

    queries_to_try = []

    # Monta queries priorizando canais sugeridos pelo Gemini
    if channel_name_hints:
        for ch_name in channel_name_hints[:3]:
            queries_to_try.append(f"ytsearch3:{keyword} {ch_name}")

    # Query genérica como fallback
    queries_to_try.append(f"ytsearch5:{keyword} footage documentary")
    queries_to_try.append(f"ytsearch3:{keyword} b-roll stock footage")

    ydl_info_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "match_filter": _dur_filter(MAX_VIDEO_DL),
    }

    for query in queries_to_try:
        try:
            with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
                results = ydl.extract_info(query, download=False)
                entries = results.get("entries", []) if results else []
                usable = [e for e in entries if e and 10 <= (e.get("duration") or 0) <= MAX_VIDEO_DL]
                if not usable:
                    continue
                best = min(usable, key=lambda e: e.get("duration", 999))
                url = best.get("webpage_url") or best.get("url", "")
                if not url:
                    continue
            ok = _download_segment_with_retry(url, output_path)
            if ok:
                return True
        except Exception as e:
            print(f"    [YouTube yt-dlp] Erro na query '{query[:60]}': {e}")
            continue

    return False


# ─── Utilidades ─────────────────────────────────────────────────────────

def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(project_dir, "ffmpeg", "ffmpeg.exe")
    return bundled if os.path.isfile(bundled) else "ffmpeg"


def _resolve_file(base_path: str) -> str:
    """Encontra o arquivo baixado independente da extensão."""
    stem = os.path.splitext(base_path)[0]
    for ext in (".mp4", ".webm", ".mkv", ".mov", ".avi"):
        cand = stem + ext
        if os.path.exists(cand) and os.path.getsize(cand) > 10_000:
            return cand
    return ""


def _probe_duration(path: str, ffmpeg: str) -> float:
    """Usa ffprobe para obter duração do arquivo. Replaces only the basename
    so paths containing 'ffmpeg' in directory (e.g. .../ffmpeg/ffmpeg.exe)
    don't get corrupted."""
    try:
        ff_dir = os.path.dirname(ffmpeg)
        ff_base = os.path.basename(ffmpeg)
        probe_base = ff_base.replace("ffmpeg", "ffprobe")
        ffprobe = os.path.join(ff_dir, probe_base) if ff_dir else "ffprobe"
        if not os.path.isfile(ffprobe):
            ffprobe = shutil.which("ffprobe") or "ffprobe"
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10
        )
        return float(r.stdout.strip())
    except Exception as e:
        print(f"    [YouTube] _probe_duration error: {e}")
        return 30.0


def _parse_iso(iso_dur: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_dur or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def _dur_filter(max_seconds: int):
    def f(info, *, incomplete):
        dur = info.get("duration")
        if dur and dur > max_seconds:
            return f"Too long ({dur}s)"
        return None
    return f
