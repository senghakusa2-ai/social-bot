import os
import re
import sys
import uuid
import html
import asyncio
import logging
import shutil
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

from downloader import SocialDownloader

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

# URL regex pattern
URL_REGEX = re.compile(
    r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)"
)

# In-memory storage for active download requests: token -> {url, title, uploader}
PENDING_DOWNLOADS: Dict[str, Dict[str, Any]] = {}

downloader = SocialDownloader(output_dir=str(DOWNLOADS_DIR))


def format_duration(seconds: Optional[int]) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    if not seconds:
        return "Unknown"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    first_name = html.escape(user.first_name if user and user.first_name else "there")
    welcome_text = (
        f"👋 <b>Hello {first_name}!</b>\n\n"
        "⚡ Welcome to <b>Universal Social Downloader Bot</b>!\n\n"
        "🎯 <b>Features:</b>\n"
        "• 🚀 <b>Auto Best Quality</b>: Automatically downloads highest resolution video!\n"
        "• 📸 <b>Multi-Media Support</b>: Download multiple videos & pictures from 1 link!\n"
        "• 📱 Supports TikTok (Videos & Photo Slides), Instagram (Reels & Carousels), YouTube, Twitter/X, Facebook, Reddit, Pinterest & more!\n\n"
        "👉 <b>Simply paste or send any link here to start downloading!</b>"
    )
    if update.message:
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "📖 <b>How to Use Social Downloader Bot:</b>\n\n"
        "1. <b>Send any link</b> from TikTok, Instagram, YouTube, Twitter/X, Facebook, Pinterest, Reddit, etc.\n"
        "2. The bot will <b>automatically pick the best quality video</b> and download <b>all pictures and videos</b> in the post!\n"
        "3. Wait a few moments and all files will be uploaded directly to your chat.\n\n"
        "✨ <b>Supported Multi-Media:</b>\n"
        "• Instagram Carousel Posts (multiple photos & videos)\n"
        "• TikTok Photo Slideshows & Videos\n"
        "• Twitter/X multiple media posts\n"
        "• YouTube Playlists & Shorts\n"
        "• Reddit galleries & Pinterest pins\n\n"
        "⚠️ <b>Note on Telegram Limits:</b>\n"
        "Telegram bots have a maximum upload limit of <b>50 MB</b> per file."
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
        await message.reply_text("❌ Please send a valid media URL (TikTok, Instagram, YouTube, X/Twitter, etc.).")
        return

    url = matches[0]
    chat_id = message.chat_id
    bot_username = getattr(context.bot, "username", None) or "MultidownloadHak_bot"

    status_msg = await message.reply_text(
        "⏳ <b>Downloading best quality media...</b>\n"
        "<i>Fetching videos and pictures, please wait...</i>",
        parse_mode=ParseMode.HTML,
    )

    loop = asyncio.get_running_loop()
    req_id = uuid.uuid4().hex[:10]
    req_dir = DOWNLOADS_DIR / f"dl_{req_id}"

    try:
        # Show recording/uploading action in chat
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

        # Download automatically in best quality (handles single & multiple media)
        result = await loop.run_in_executor(
            None,
            lambda: downloader.download_media(url, req_dir=req_dir),
        )

        files: List[Dict[str, Any]] = result.get("files", [])
        if not files:
            await status_msg.edit_text(
                "❌ <b>No media could be downloaded from this link.</b>\n"
                "<i>Please check if the link is public and still accessible.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        title = result.get("title", "Media") or "Media"
        uploader = result.get("uploader", "Creator") or "Creator"
        safe_title = html.escape(title[:80])
        caption = f"🎬 <b>{safe_title}</b>\n👤 {html.escape(uploader[:50])}\n⚡ @{bot_username}"

        # Filter by Telegram Bot API 50MB limit
        valid_files = [f for f in files if f["size_mb"] <= 50.0]
        oversized_files = [f for f in files if f["size_mb"] > 50.0]

        if not valid_files:
            oversized_info = "\n".join(
                [f"• <code>{html.escape(f['filename'])}</code> ({f['size_mb']:.1f} MB)" for f in oversized_files]
            )
            await status_msg.edit_text(
                f"⚠️ <b>File(s) exceed Telegram's 50 MB limit:</b>\n{oversized_info}\n\n"
                "Telegram bots cannot upload files larger than 50 MB.",
                parse_mode=ParseMode.HTML,
            )
            return

        total_files = len(valid_files)
        try:
            await status_msg.edit_text(
                f"⬆️ <b>Uploading {total_files} file(s) in best quality...</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        sent_count = 0

        # Separate photo/video vs audio/document
        pv_files = [f for f in valid_files if f["type"] in ("video", "photo")]
        other_files = [f for f in valid_files if f["type"] not in ("video", "photo")]

        # 1) If exactly 1 photo or video: send directly
        if len(pv_files) == 1 and not other_files:
            item = pv_files[0]
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
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                try:
                    with open(item["path"], "rb") as f:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=f,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            supports_streaming=True,
                            read_timeout=180,
                            write_timeout=180,
                        )
                except Exception as vid_err:
                    logger.warning(f"send_video error ({vid_err}), fallback to send_document")
                    with open(item["path"], "rb") as f:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            read_timeout=180,
                            write_timeout=180,
                        )
            sent_count += 1

        # 2) If multiple photos/videos: send in media groups (albums up to 10 items)
        elif len(pv_files) > 1:
            chunk_size = 10
            for i in range(0, len(pv_files), chunk_size):
                chunk = pv_files[i : i + chunk_size]
                media_group = []
                opened_files = []
                try:
                    for idx, item in enumerate(chunk):
                        fp = open(item["path"], "rb")
                        opened_files.append(fp)
                        item_caption = caption if (i == 0 and idx == 0) else None
                        if item["type"] == "photo":
                            media_group.append(
                                InputMediaPhoto(
                                    media=fp,
                                    caption=item_caption,
                                    parse_mode=ParseMode.HTML if item_caption else None,
                                )
                            )
                        else:
                            media_group.append(
                                InputMediaVideo(
                                    media=fp,
                                    caption=item_caption,
                                    parse_mode=ParseMode.HTML if item_caption else None,
                                    supports_streaming=True,
                                )
                            )

                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                    await context.bot.send_media_group(
                        chat_id=chat_id,
                        media=media_group,
                        read_timeout=180,
                        write_timeout=180,
                    )
                    sent_count += len(chunk)
                except Exception as album_err:
                    logger.warning(f"send_media_group failed ({album_err}), falling back to individual sends")
                    # Fallback: send each file in this chunk individually
                    for item in chunk:
                        try:
                            with open(item["path"], "rb") as f:
                                if item["type"] == "photo":
                                    await context.bot.send_photo(
                                        chat_id=chat_id,
                                        photo=f,
                                        caption=caption,
                                        parse_mode=ParseMode.HTML,
                                    )
                                else:
                                    await context.bot.send_video(
                                        chat_id=chat_id,
                                        video=f,
                                        caption=caption,
                                        parse_mode=ParseMode.HTML,
                                        supports_streaming=True,
                                    )
                            sent_count += 1
                        except Exception as single_err:
                            logger.error(f"Failed to send individual file {item['filename']}: {single_err}")
                finally:
                    for fp in opened_files:
                        try:
                            fp.close()
                        except Exception:
                            pass

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
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            read_timeout=120,
                            write_timeout=120,
                        )
                    else:
                        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            read_timeout=120,
                            write_timeout=120,
                        )
                sent_count += 1
            except Exception as oth_err:
                logger.error(f"Failed to send {item['filename']}: {oth_err}")

        # Summary notification
        completion_text = f"✅ <b>Downloaded and sent {sent_count} file(s) in best quality!</b>"
        if oversized_files:
            completion_text += f"\n⚠️ <i>({len(oversized_files)} file(s) skipped due to Telegram's 50MB limit)</i>"
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
        # Clean up temporary request directory to free disk space
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
    await query.edit_message_text("⚡ The bot now downloads automatically in best quality! Just send the link.")


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
        BOT_STATUS["message"] = "Please configure TELEGRAM_BOT_TOKEN in Render Dashboard > Environment."
        logger.error("=" * 60)
        logger.error("⚠️ TELEGRAM_BOT_TOKEN NOT FOUND!")
        logger.error("Please configure your Telegram Bot Token in Render Environment or .env file.")
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
