import os
import sys
import shutil
import uuid
import subprocess
import requests
import logging
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    FFMPEG_PATH = shutil.which("ffmpeg")
except Exception:
    FFMPEG_PATH = shutil.which("ffmpeg")

import yt_dlp

logger = logging.getLogger("SocialDownloader")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Mode": "navigate",
}


class SocialDownloader:
    def __init__(self, output_dir: str = "downloads", browser_cookies: Optional[str] = None):
        """
        :param output_dir: Directory where downloaded files are saved
        :param browser_cookies: Optional browser name to extract cookies from
        """
        self.output_dir = Path(os.path.abspath(output_dir))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.browser_cookies = browser_cookies if browser_cookies and browser_cookies.lower() != "none" else None

    def _clean_url(self, url: str) -> str:
        """Resolve shortened URLs (like vt.tiktok.com, vm.tiktok.com, t.co, youtu.be, bit.ly)."""
        url = url.strip().strip('"').strip("'")
        if not url:
            return url

        # If it's a shortened redirect URL, resolve canonical destination
        if any(domain in url for domain in ["vt.tiktok.com", "vm.tiktok.com", "t.co", "bit.ly", "tinyurl.com", "fb.watch"]):
            try:
                res = requests.head(url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=8)
                if res.url:
                    return res.url
            except Exception:
                try:
                    res = requests.get(url, headers=DEFAULT_HEADERS, allow_redirects=True, stream=True, timeout=8)
                    if res.url:
                        return res.url
                except Exception:
                    pass
        return url

    def _get_base_opts(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "windowsfilenames": True,
            "socket_timeout": 30,
            "retries": 10,
            "file_access_retries": 5,
            "fragment_retries": 10,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "http_headers": DEFAULT_HEADERS,
        }
        if FFMPEG_PATH:
            opts["ffmpeg_location"] = FFMPEG_PATH

        if self.browser_cookies:
            opts["cookiesfrombrowser"] = (self.browser_cookies,)

        return opts

    def get_info(self, url: str) -> Dict[str, Any]:
        """Fetch metadata without downloading."""
        url = self._clean_url(url)
        opts = self._get_base_opts()
        opts["skip_download"] = True

        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    def download_media(
        self,
        url: str,
        req_dir: Optional[Path] = None,
        max_items: int = 30,
        progress_hook: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        Automatically download media in best quality video and/or photos.
        Can download multiple videos and pictures from a single link (carousels, playlists, albums).

        :param url: URL to post, video, carousel, or playlist
        :param req_dir: Optional destination directory for this request
        :param max_items: Maximum items to download from playlists/carousels
        :param progress_hook: Optional progress callback
        :return: Dict containing title, uploader, dir, and list of downloaded files
        """
        url = self._clean_url(url)
        if not req_dir:
            req_dir = self.output_dir / f"req_{uuid.uuid4().hex[:10]}"
        req_dir = Path(req_dir)
        req_dir.mkdir(parents=True, exist_ok=True)

        title = "Media"
        uploader = "Social Downloader"

        # Strategy 1: Check for TikTok photo slideshow (TikWM API)
        if "tiktok.com" in url or "douyin.com" in url:
            try:
                tw_res = requests.get(f"https://www.tikwm.com/api/?url={url}", headers=DEFAULT_HEADERS, timeout=10)
                if tw_res.status_code == 200:
                    tw_json = tw_res.json()
                    if tw_json.get("code") == 0:
                        data = tw_json.get("data", {})
                        images = data.get("images", [])
                        if images:
                            title = data.get("title") or title
                            uploader = data.get("author", {}).get("nickname") or uploader
                            for idx, img_url in enumerate(images[:max_items]):
                                try:
                                    img_r = requests.get(img_url, headers=DEFAULT_HEADERS, timeout=15)
                                    if img_r.status_code == 200:
                                        p_path = req_dir / f"slide_{idx+1:02d}.jpg"
                                        with open(p_path, "wb") as pf:
                                            pf.write(img_r.content)
                                except Exception as img_err:
                                    logger.warning(f"Error fetching TikTok slide {idx}: {img_err}")
            except Exception as tw_err:
                logger.debug(f"TikWM check notice: {tw_err}")

        # Strategy 2: Download with yt-dlp (Auto best quality video, audio, and entries)
        opts = self._get_base_opts()
        output_template = str(req_dir / "%(autonumber)02d_%(title).60s_%(id)s.%(ext)s")
        opts.update({
            "outtmpl": output_template,
            "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "noplaylist": False,
            "ignoreerrors": True,
            "max_downloads": max_items,
        })
        if progress_hook:
            opts["progress_hooks"] = [progress_hook]

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    title = info.get("title") or title
                    uploader = info.get("uploader") or info.get("channel") or uploader

                    # Check for photo entries in Instagram / carousels where yt-dlp extracted thumbnails
                    entries = info.get("entries") if "entries" in info else [info]
                    for idx, entry in enumerate(entries or []):
                        if not entry:
                            continue
                        formats = entry.get("formats") or []
                        thumbnails = entry.get("thumbnails") or []
                        # If entry has no video stream (e.g. Instagram photo entry)
                        has_video = any(f.get("vcodec") and f.get("vcodec") != "none" for f in formats)
                        if not has_video and thumbnails:
                            best_thumb = thumbnails[-1].get("url")
                            if best_thumb:
                                try:
                                    res = requests.get(best_thumb, headers=DEFAULT_HEADERS, timeout=15)
                                    if res.status_code == 200:
                                        photo_path = req_dir / f"photo_{idx+1:02d}.jpg"
                                        with open(photo_path, "wb") as pf:
                                            pf.write(res.content)
                                except Exception as ex:
                                    logger.debug(f"Failed to fetch photo candidate {idx}: {ex}")
        except Exception as ytdl_err:
            logger.info(f"yt-dlp download notice: {ytdl_err}")

        # Strategy 3: gallery-dl fallback / complementary fetch for Instagram, Twitter, Pinterest, Reddit galleries
        existing_files = [f for f in req_dir.glob("*") if f.is_file() and not f.name.endswith((".part", ".tmp", ".ytdl"))]
        is_gallery_platform = any(
            domain in url.lower() for domain in [
                "instagram.com", "twitter.com", "x.com", "pinterest.", "reddit.com", "threads.net", "flickr.com"
            ]
        )
        if len(existing_files) == 0 or is_gallery_platform:
            try:
                cmd = [
                    sys.executable, "-m", "gallery_dl",
                    "--directory", str(req_dir),
                    "--quiet",
                    "--no-mtime",
                    "--windows-filenames",
                ]
                if self.browser_cookies:
                    cmd.extend(["--cookies-from-browser", self.browser_cookies])
                cmd.append(url)
                subprocess.run(cmd, timeout=90, capture_output=True)
            except Exception as g_err:
                logger.debug(f"gallery-dl attempt notice: {g_err}")

        # Deduplicate files (if gallery-dl and yt-dlp both downloaded identical files)
        seen_sizes = set()
        for f in sorted(req_dir.rglob("*")):
            if not f.is_file():
                continue
            sz = f.stat().st_size
            if sz == 0:
                continue
            if sz in seen_sizes:
                try:
                    f.unlink()
                except Exception:
                    pass
            else:
                seen_sizes.add(sz)

        # Categorize all media files in req_dir
        media_files: List[Dict[str, Any]] = []
        for file_path in sorted(req_dir.rglob("*")):
            if not file_path.is_file():
                continue
            ext = file_path.suffix.lower()
            if ext in [".part", ".tmp", ".ytdl", ".aria2"]:
                continue
            size_bytes = file_path.stat().st_size
            if size_bytes == 0:
                continue

            media_type = "document"
            if ext in [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"]:
                media_type = "video"
            elif ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"]:
                media_type = "photo"
            elif ext in [".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus"]:
                media_type = "audio"

            media_files.append({
                "path": str(file_path),
                "filename": file_path.name,
                "type": media_type,
                "size_bytes": size_bytes,
                "size_mb": size_bytes / (1024 * 1024),
            })

        return {
            "title": title,
            "uploader": uploader,
            "dir": str(req_dir),
            "files": media_files,
        }

    def download(
        self,
        url: str,
        quality: str = "best",
        audio_only: bool = False,
        audio_format: str = "mp3",
        progress_hook: Optional[Callable] = None,
    ) -> str:
        """
        Download media from URL (maintained for GUI and CLI compatibility).
        """
        url = self._clean_url(url)
        output_template = str(self.output_dir / "%(extractor_key)s_%(title).80s_%(id)s.%(ext)s")

        opts = self._get_base_opts()
        opts.update({
            "outtmpl": output_template,
            "restrictfilenames": False,
        })

        if progress_hook:
            opts["progress_hooks"] = [progress_hook]

        if audio_only:
            opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": "320",
                }],
            })
        else:
            q = quality.lower().split()[0]
            if q == "4k":
                opts["format"] = "bestvideo*[height<=2160]+bestaudio/best[height<=2160]/bestvideo*+bestaudio/best"
            elif q == "1080p":
                opts["format"] = "bestvideo*[height<=1080]+bestaudio/best[height<=1080]/bestvideo*+bestaudio/best"
            elif q == "720p":
                opts["format"] = "bestvideo*[height<=720]+bestaudio/best[height<=720]/bestvideo*+bestaudio/best"
            elif q == "480p":
                opts["format"] = "bestvideo*[height<=480]+bestaudio/best[height<=480]/bestvideo*+bestaudio/best"
            else:
                opts["format"] = "bestvideo*+bestaudio/best"

            opts["merge_output_format"] = "mp4"

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise Exception("Could not extract media info from this link.")
            
            # If playlist/entries
            if "entries" in info and info["entries"]:
                info = info["entries"][0]

            # Check if yt-dlp recorded the final downloaded filepath
            if "requested_downloads" in info and info["requested_downloads"]:
                for item in reversed(info["requested_downloads"]):
                    filepath = item.get("filepath") or item.get("_filename")
                    if filepath and os.path.exists(filepath):
                        return filepath

            filename = ydl.prepare_filename(info)
            if not audio_only and opts.get("merge_output_format") == "mp4":
                base, _ = os.path.splitext(filename)
                if os.path.exists(base + ".mp4"):
                    return base + ".mp4"
            if os.path.exists(filename):
                return filename

            # Fallback search in output directory if file extension changed
            base_name = os.path.splitext(os.path.basename(filename))[0]
            for candidate in self.output_dir.glob(f"{base_name}*"):
                if candidate.is_file():
                    return str(candidate)

            return filename
