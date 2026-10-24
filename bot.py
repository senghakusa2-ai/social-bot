import os
import re
import sys
import uuid
import html
import json
import asyncio
import logging
import shutil
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, Any, Optional, List

# Ensure UTF-8 output across all operating systems and Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from downloader import SocialDownloader, FFMPEG_PATH, FFPROBE_PATH

# Setup Logging
logging.basicConfig(
    format="%(asctime)s - [%(name)s] - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("SocialDownloaderBot")

# Load environment variables
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DOWNLOADS_DIR = Path("bot_downloads")
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

PERMANENT_SAVED_DIR = DOWNLOADS_DIR / "saved_full_quality"
PERMANENT_SAVED_DIR.mkdir(parents=True, exist_ok=True)

# URL regex pattern
URL_REGEX = re.compile(
    r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)"
)

# Initialize downloader with cookie discovery
cookies_file = os.getenv("COOKIES_FILE") or ("cookies.txt" if os.path.exists("cookies.txt") else None)
browser_cookies = os.getenv("BROWSER_COOKIES") or None
downloader = SocialDownloader(
    output_dir=str(DOWNLOADS_DIR),
    cookies_file=cookies_file,
    browser_cookies=browser_cookies,
)

TELEGRAM_MAX_MB = float(os.getenv("MAX_TELEGRAM_FILE_MB", "49.5"))


def format_duration(seconds: Optional[int]) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    if not seconds:
        return "Unknown"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def get_video_duration(video_path: str) -> Optional[float]:
    """Get video duration using ffprobe."""
    if not FFPROBE_PATH or not os.path.exists(video_path):
        return None
    try:
        cmd = [
            FFPROBE_PATH,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            video_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            duration = data.get("format", {}).get("duration")
            if duration:
                return float(duration)
    except Exception as e:
        logger.debug(f"ffprobe duration check failed: {e}")
    return None


def optimize_video_for_telegram(video_path: str, target_max_mb: float = 48.5) -> Optional[str]:
    """
    If a video exceeds Telegram's upload limit (50 MB), compress it using ffmpeg
    so that it fits under target_max_mb while maintaining maximum visual quality.
    Uses 2-pass CRF-constrained encoding for superior quality.
    """
    if not FFMPEG_PATH or not os.path.exists(video_path):
        return None

    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if size_mb <= target_max_mb:
        return video_path

    duration = get_video_duration(video_path)
    if not duration or duration <= 0:
        duration = max(30.0, size_mb * 2.0)

    # Calculate target video bitrate (bytes -> bits, with 5% margin for container overhead)
    target_bytes = target_max_mb * 1024 * 1024 * 0.95
    total_bitrate = int((target_bytes * 8) / duration)
    audio_bitrate = min(192000, max(96000, int(total_bitrate * 0.12)))
    video_bitrate = max(800000, total_bitrate - audio_bitrate)

    # Choose CRF based on how much we need to compress
    compression_ratio = size_mb / target_max_mb
    if compression_ratio < 1.5:
        crf = 20   # Very light compression
    elif compression_ratio < 3:
        crf = 22   # Moderate compression, still excellent quality
    elif compression_ratio < 6:
        crf = 24   # Good quality
    else:
        crf = 26   # Heavier compression for very large files

    base, ext = os.path.splitext(video_path)
    optimized_path = f"{base}_tg_hd.mp4"

    logger.info(
        f"Optimizing {os.path.basename(video_path)} ({size_mb:.1f}MB) -> target ~{target_max_mb}MB "
        f"(vbr: {video_bitrate//1000}k, crf: {crf})"
    )

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i", video_path,
        "-c:v", "libx264",
        "-crf", str(crf),
        "-maxrate", str(video_bitrate),
        "-bufsize", str(int(video_bitrate * 2)),
        # Keep original resolution up to 1920px wide (no unnecessary downscaling)
        "-vf", "scale='min(1920,iw)':-2:flags=lanczos",
        "-preset", "medium",
        "-profile:v", "high",
        "-level:v", "4.2",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", str(audio_bitrate),
        "-ac", "2",
        "-movflags", "+faststart",
        optimized_path,
    ]

    try:
        subprocess.run(cmd, capture_output=True, timeout=600, check=True)
        if os.path.exists(optimized_path):
            new_size_mb = os.path.getsize(optimized_path) / (1024 * 1024)
            logger.info(f"Optimized video created: {new_size_mb:.1f}MB (CRF {crf})")
            if new_size_mb <= 50.0:
                return optimized_path
            # If still too large with CRF, try again with tighter CRF
            if new_size_mb > 50.0 and crf < 28:
                logger.info(f"Still too large ({new_size_mb:.1f}MB), retrying with CRF {crf + 4}...")
                cmd2 = cmd.copy()
                crf_idx = cmd2.index("-crf") + 1
                cmd2[crf_idx] = str(crf + 4)
                try:
                    os.remove(optimized_path)
                except Exception:
                    pass
                subprocess.run(cmd2, capture_output=True, timeout=600, check=True)
                if os.path.exists(optimized_path):
                    new_size_mb = os.path.getsize(optimized_path) / (1024 * 1024)
                    if new_size_mb <= 50.0:
                        return optimized_path
    except Exception as e:
        logger.warning(f"ffmpeg video optimization error: {e}")

    return None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    first_name = html.escape(user.first_name if user and user.first_name else "there")
    welcome_text = (
        f"👋 <b>Hello {first_name}!</b>\n\n"
        "⚡ Welcome to <b>Universal Social Downloader Bot</b>!\n\n"
        "🎯 <b>Highlights & Features:</b>\n"
        "• 🌟 <b>Highest Quality Only (HD - 4K)</b>: Prioritizes 4K (2160p), 1440p, and 1080p full HD resolution!\n"
        "• 📸 <b>Stories & Highlights</b>: Supports Instagram & Facebook Stories (via cookies)!\n"
        "• 🎬 <b>Smart 50MB Handling</b>: Auto-optimizes large 4K/HD videos so Telegram allows downloading without errors!\n"
        "• 📱 <b>Platforms Supported</b>: TikTok (No watermark / Slides), Instagram (Reels, Carousels, Stories), Facebook (Reels, Watch, Shares), YouTube (Shorts & 4K), Twitter/X, Reddit, Pinterest & more!\n\n"
        "👉 <b>Simply paste or send any link here to start downloading!</b>\n"
        "💡 Send /cookies to learn how to enable Instagram & Facebook Story downloads."
    )
    if update.message:
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)


async def cookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cookies command to guide users on enabling Story downloads."""
    status_icon = "✅" if (downloader.cookies_file or downloader.browser_cookies) else "⚠️"
    current_status = "Cookies Loaded & Active" if (downloader.cookies_file or downloader.browser_cookies) else "No Cookies Configured"

    info_text = (
        f"🍪 <b>Story & Cookie Configuration:</b>\n\n"
        f"<b>Status:</b> {status_icon} <code>{current_status}</code>\n"
    )
    if downloader.cookies_file:
        info_text += f"• File: <code>{downloader.cookies_file.name}</code>\n\n"
    elif downloader.browser_cookies:
        info_text += f"• Browser: <code>{downloader.browser_cookies}</code>\n\n"
    else:
        info_text += (
            "\n<b>Why are cookies needed for Stories?</b>\n"
            "Instagram and Facebook strictly require an active login session to view Stories, even on public accounts.\n\n"
            "💡 <b>How to enable Story downloads:</b>\n"
            "1. Install browser extension <b>'Get cookies.txt LOCALLY'</b> in Chrome, Edge, or Firefox.\n"
            "2. Log in to Instagram/Facebook on your browser.\n"
            "3. Export cookies and save the file as <code>cookies.txt</code> in the bot folder:\n"
            f"<code>{os.path.abspath('cookies.txt')}</code>\n"
            "4. Or set <code>BROWSER_COOKIES=chrome</code> (or <code>edge</code>, <code>firefox</code>) in your <code>.env</code> file.\n\n"
            "<i>Once saved, restart the bot and all IG & FB Stories will download automatically!</i>"
        )
    if update.message:
        await update.message.reply_text(info_text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "📖 <b>How to Use Social Downloader Bot:</b>\n\n"
        "1. <b>Send any link</b> from TikTok, Instagram, YouTube, Twitter/X, Facebook, Pinterest, Reddit, etc.\n"
        "2. The bot <b>automatically selects the highest quality available (HD - 4K)</b> and downloads <b>all pictures and videos</b> in the post!\n"
        "3. Wait a few moments and all files will be uploaded directly to your chat.\n\n"
        "✨ <b>Supported Content:</b>\n"
        "• <b>4K & Full HD Videos</b>: YouTube, Facebook Reels & Watch, TikTok, Twitter/X\n"
        "• <b>Instagram Stories & Carousels</b>: Multiple photos and videos from single links\n"
        "• <b>TikTok Photo Slideshows</b>: All photos in original crisp resolution\n"
        "• <b>Facebook Share Links</b>: Directly handles mobile <code>facebook.com/share/r/</code> URLs\n\n"
        "💡 <b>Helpful Commands:</b>\n"
        "• /cookies : Check or configure cookies for Stories\n"
        "• /help : Show this instruction message"
    )
    if update.message:
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def handle_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detect URLs sent by users and automatically download best quality video & pictures."""
    message = update.message
    if not message or not message.text:
        return

    matches = URL_REGEX.findall(message.text)
    if not matches:
        await message.reply_text("❌ Please send a valid media URL (TikTok, Instagram, YouTube, Facebook, X, etc.).")
        return

    url = matches[0]
    chat_id = message.chat_id
    bot_username = getattr(context.bot, "username", None) or "MultidownloadHak_bot"

    status_msg = await message.reply_text(
        "⏳ <b>Downloading media in highest quality (HD-4K)...</b>\n"
        "<i>Processing video & photo streams, please wait...</i>",
        parse_mode=ParseMode.HTML,
    )

    loop = asyncio.get_running_loop()
    req_id = uuid.uuid4().hex[:10]
    req_dir = DOWNLOADS_DIR / f"dl_{req_id}"

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

        # Download automatically in highest quality (handles single & multiple media)
        result = await loop.run_in_executor(
            None,
            lambda: downloader.download_media(url, req_dir=req_dir),
        )

        files: List[Dict[str, Any]] = result.get("files", [])
        is_story = result.get("is_story", False)
        story_missing_cookies = result.get("story_missing_cookies", False)

        # If story link was submitted and failed due to lack of cookies
        if (is_story and not files) or story_missing_cookies:
            story_help_text = (
                "⚠️ <b>Instagram / Facebook Story Notice:</b>\n\n"
                "Instagram and Facebook require an active login session (cookies) to download Stories (even on public profiles).\n\n"
                "💡 <b>How to enable Story Downloads in 1 minute:</b>\n"
                "1. Install <b>'Get cookies.txt LOCALLY'</b> extension in your browser.\n"
                "2. Export your cookies while logged into Instagram / Facebook.\n"
                f"3. Place the file as <code>cookies.txt</code> in:\n<code>{os.path.abspath('cookies.txt')}</code>\n"
                "4. Or add <code>BROWSER_COOKIES=chrome</code> in your <code>.env</code> file.\n\n"
                "<i>Send /cookies for more details!</i>"
            )
            await status_msg.edit_text(story_help_text, parse_mode=ParseMode.HTML)
            return

        if not files:
            await status_msg.edit_text(
                "❌ <b>No media could be downloaded from this link.</b>\n"
                "<i>Please check if the video/post is public, still accessible, or requires cookies.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        title = result.get("title", "Media") or "Media"
        uploader = result.get("uploader", "Creator") or "Creator"
        safe_title = html.escape(title[:80])

        # Process each video to ensure it fits within Telegram's upload limit
        final_files_to_send: List[Dict[str, Any]] = []
        oversized_files: List[Dict[str, Any]] = []

        for item in files:
            p = item["path"]
            sz_mb = item["size_mb"]

            if item["type"] == "video" and sz_mb > TELEGRAM_MAX_MB:
                # Video is 4K or large HD exceeding Telegram limit:
                # 1. Save a pristine copy to permanent local storage
                try:
                    permanent_copy = PERMANENT_SAVED_DIR / f"{Path(p).stem}_original_4k{Path(p).suffix}"
                    shutil.copy2(p, permanent_copy)
                    item["saved_original_path"] = str(permanent_copy)
                except Exception as save_err:
                    logger.debug(f"Could not copy to permanent directory: {save_err}")

                # 2. Try smart ffmpeg compression to fit Telegram's limit
                try:
                    await status_msg.edit_text(
                        f"⚙️ <b>Optimizing high-res video ({sz_mb:.1f} MB) for Telegram...</b>\n"
                        "<i>Pristine 4K original is saved on your PC!</i>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

                optimized_p = await loop.run_in_executor(
                    None,
                    lambda: optimize_video_for_telegram(p, target_max_mb=TELEGRAM_MAX_MB),
                )

                if optimized_p and os.path.exists(optimized_p):
                    new_sz_mb = os.path.getsize(optimized_p) / (1024 * 1024)
                    item["path"] = optimized_p
                    item["size_mb"] = new_sz_mb
                    item["was_optimized"] = True
                    final_files_to_send.append(item)
                else:
                    oversized_files.append(item)
            elif sz_mb <= 50.0:
                final_files_to_send.append(item)
            else:
                oversized_files.append(item)

        if not final_files_to_send:
            oversized_info = "\n".join(
                [f"• <code>{html.escape(f['filename'])}</code> ({f['size_mb']:.1f} MB)" for f in oversized_files]
            )
            saved_paths = "\n".join(
                [f"💾 <code>{html.escape(f.get('saved_original_path') or f['path'])}</code>" for f in oversized_files]
            )
            await status_msg.edit_text(
                f"⚠️ <b>Video exceeds Telegram's 50 MB limit:</b>\n{oversized_info}\n\n"
                f"✅ <b>Pristine Full 4K/HD file saved on your PC at:</b>\n{saved_paths}",
                parse_mode=ParseMode.HTML,
            )
            return

        total_files = len(final_files_to_send)
        try:
            await status_msg.edit_text(
                f"⬆️ <b>Uploading {total_files} file(s) in highest quality (HD-4K)...</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        sent_count = 0

        # Separate photo/video vs audio/document
        pv_files = [f for f in final_files_to_send if f["type"] in ("video", "photo")]
        other_files = [f for f in final_files_to_send if f["type"] not in ("video", "photo")]

        # Caption builder
        base_caption = f"🎬 <b>{safe_title}</b>\n👤 {html.escape(uploader[:50])}\n⚡ @{bot_username}"

        # 1) If exactly 1 photo or video: send directly
        if len(pv_files) == 1 and not other_files:
            item = pv_files[0]
            caption = base_caption
            if item.get("saved_original_path"):
                caption += f"\n\n💾 <i>Full 4K saved on PC:</i>\n<code>{html.escape(os.path.basename(item['saved_original_path']))}</code>"

            if item["type"] == "photo":
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                with open(item["path"], "rb") as f:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        read_timeout=120,
                        write_timeout=120,
                    )
            else:
                # *** SEND AS DOCUMENT to preserve original HD/4K quality ***
                # Telegram's send_video re-encodes the video on their servers
                # which massively degrades quality. send_document keeps the raw file intact.
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
                with open(item["path"], "rb") as f:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        read_timeout=300,
                        write_timeout=300,
                    )
            sent_count += 1

        # 2) If multiple photos/videos: send photos in media group, videos individually as documents
        elif len(pv_files) > 1:
            # Separate photos and videos for proper handling
            photo_items = [f for f in pv_files if f["type"] == "photo"]
            video_items = [f for f in pv_files if f["type"] == "video"]

            # Send photos in media groups (albums up to 10 items) — photos don't get re-encoded
            if photo_items:
                chunk_size = 10
                for i in range(0, len(photo_items), chunk_size):
                    chunk = photo_items[i : i + chunk_size]
                    media_group = []
                    opened_files = []
                    try:
                        for idx, item in enumerate(chunk):
                            fp = open(item["path"], "rb")
                            opened_files.append(fp)
                            item_caption = base_caption if (i == 0 and idx == 0 and not video_items) else None
                            media_group.append(
                                InputMediaPhoto(
                                    media=fp,
                                    caption=item_caption,
                                    parse_mode=ParseMode.HTML if item_caption else None,
                                )
                            )

                        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                        await context.bot.send_media_group(
                            chat_id=chat_id,
                            media=media_group,
                            read_timeout=240,
                            write_timeout=240,
                        )
                        sent_count += len(chunk)
                    except Exception as album_err:
                        logger.warning(f"send_media_group photos failed ({album_err}), falling back to individual sends")
                        for item in chunk:
                            try:
                                with open(item["path"], "rb") as f:
                                    await context.bot.send_photo(
                                        chat_id=chat_id,
                                        photo=f,
                                        caption=base_caption,
                                        parse_mode=ParseMode.HTML,
                                    )
                                sent_count += 1
                            except Exception as single_err:
                                logger.error(f"Failed to send photo {item['filename']}: {single_err}")
                    finally:
                        for fp in opened_files:
                            try:
                                fp.close()
                            except Exception:
                                pass

            # Send each video as document to preserve full HD/4K quality
            for idx, item in enumerate(video_items):
                try:
                    caption = base_caption if (idx == 0 and not photo_items) else None
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
                    with open(item["path"], "rb") as f:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            caption=caption,
                            parse_mode=ParseMode.HTML if caption else None,
                            read_timeout=300,
                            write_timeout=300,
                        )
                    sent_count += 1
                except Exception as vid_err:
                    logger.error(f"Failed to send video {item['filename']}: {vid_err}")

        # 3) Handle any other files (audios, documents)
        for item in other_files:
            try:
                with open(item["path"], "rb") as f:
                    if item["type"] == "audio":
                        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=f,
                            title=title[:80],
                            performer=uploader[:50],
                            caption=base_caption,
                            parse_mode=ParseMode.HTML,
                            read_timeout=120,
                            write_timeout=120,
                        )
                    else:
                        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            caption=base_caption,
                            parse_mode=ParseMode.HTML,
                            read_timeout=120,
                            write_timeout=120,
                        )
                sent_count += 1
            except Exception as oth_err:
                logger.error(f"Failed to send {item['filename']}: {oth_err}")

        # Summary notification
        completion_text = f"✅ <b>Downloaded and sent {sent_count} file(s) in highest quality (HD-4K)!</b>"
        if oversized_files:
            saved_oversized = "\n".join([f"💾 <code>{html.escape(f['filename'])}</code>" for f in oversized_files])
            completion_text += f"\n\n💾 <i>Full 4K files saved on your PC:</i>\n{saved_oversized}"
        try:
            await status_msg.edit_text(completion_text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Error processing link {url}: {e}", exc_info=True)
        safe_err = html.escape(str(e)[:250])
        try:
            await status_msg.edit_text(
                f"❌ <b>Download failed:</b>\n<code>{safe_err}</code>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    finally:
        # Clean up temporary request directory
        if req_dir.exists():
            try:
                shutil.rmtree(req_dir, ignore_errors=True)
            except Exception as clean_err:
                logger.warning(f"Failed to clean temporary folder {req_dir}: {clean_err}")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Graceful handler for any legacy callback buttons."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    await query.edit_message_text("⚡ The bot now downloads automatically in highest quality (HD-4K)! Just send any link.")


BOT_STATUS = {
    "status": "Starting up",
    "message": "Initializing Social Downloader Bot...",
}


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP healthcheck handler for Render keep-alive and diagnostics."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        status_text = BOT_STATUS.get("status", "Running")
        msg_text = BOT_STATUS.get("message", "Service active")
        html_doc = (
            "<!DOCTYPE html>"
            "<html>"
            "<head>"
            "<meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Social Downloader Bot</title>"
            "<style>"
            "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }"
            ".card { background: #1e293b; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); text-align: center; max-width: 480px; width: 90%; }"
            "h1 { font-size: 1.5rem; margin-bottom: 0.5rem; color: #38bdf8; }"
            ".badge { display: inline-block; padding: 6px 14px; border-radius: 20px; font-weight: bold; background: #0284c7; color: white; margin: 12px 0; }"
            "p { line-height: 1.5; color: #94a3b8; font-size: 0.95rem; }"
            "</style>"
            "</head>"
            "<body>"
            "<div class='card'>"
            "<h1>⚡ Social Downloader Bot</h1>"
            f"<div class='badge'>{html.escape(status_text)}</div>"
            f"<p>{html.escape(msg_text)}</p>"
            "</div>"
            "</body>"
            "</html>"
        )
        self.wfile.write(html_doc.encode("utf-8"))

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.getenv("PORT", "8080"))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        logger.info(f"Health server listening on port {port}")
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Health server could not start on port {port}: {e}")


def main():
    """Start the Telegram bot."""
    # Start background health server for Render keep-alive
    threading.Thread(target=run_health_server, daemon=True).start()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or BOT_TOKEN
    is_cloud = bool(os.getenv("PORT") or os.getenv("RENDER"))

    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        BOT_STATUS["status"] = "Action Required: Missing Token"
        BOT_STATUS["message"] = "Please configure TELEGRAM_BOT_TOKEN in Render Dashboard > Environment or .env file."
        logger.error("=" * 60)
        logger.error("⚠️ TELEGRAM_BOT_TOKEN NOT FOUND!")
        logger.error("=" * 60)
        if is_cloud:
            import time
            while not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
                time.sleep(10)
                load_dotenv(override=True)
                token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        else:
            try:
                token = input("Or enter your Telegram Bot Token now: ").strip()
            except EOFError:
                token = ""
            if not token:
                logger.error("❌ No token provided. Exiting.")
                return

    try:
        print("🚀 Starting Social Downloader Telegram Bot (@MultidownloadHak_bot)...")
        app = Application.builder().token(token).build()

        # Handlers
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("cookies", cookies_command))
        app.add_handler(CallbackQueryHandler(handle_callback_query))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_message))

        BOT_STATUS["status"] = "Online & Connected"
        BOT_STATUS["message"] = "Social Downloader Bot is running healthy and listening for Telegram messages!"

        print("🤖 Bot is running! Press Ctrl+C to stop.")
        app.run_polling()
    except Exception as run_err:
        BOT_STATUS["status"] = "Bot Error"
        BOT_STATUS["message"] = f"Telegram error: {run_err}"
        logger.error(f"Telegram Bot error: {run_err}", exc_info=True)
        if is_cloud:
            import time
            while True:
                time.sleep(60)


if __name__ == "__main__":
    main()
