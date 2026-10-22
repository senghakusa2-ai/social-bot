import os
import sys
import shutil
import requests
from pathlib import Path
from typing import Optional, Callable, Dict, Any

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    FFMPEG_PATH = shutil.which("ffmpeg")
except Exception:
    FFMPEG_PATH = shutil.which("ffmpeg")

import yt_dlp

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
            "ignoreerrors": False,
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

    def download(
        self,
        url: str,
        quality: str = "best",
        audio_only: bool = False,
        audio_format: str = "mp3",
        progress_hook: Optional[Callable] = None,
    ) -> str:
        """
        Download media from URL with highest quality or chosen settings.
        
        :param url: URL to video, reel, story, post, or audio
        :param quality: 'best', '4k', '1080p', '720p', '480p'
        :param audio_only: Download audio only
        :param audio_format: Audio format if audio_only is True ('mp3', 'm4a', 'wav')
        :param progress_hook: Custom yt-dlp progress callback
        :return: Path to downloaded file
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
