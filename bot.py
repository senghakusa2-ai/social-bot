import os
import re
import uuid
import html
import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, Any, Optional

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
        "I can download videos, reels, posts, and audios from:\n"
        "• 📱 <b>TikTok</b> (No Watermark)\n"
        "• 📸 <b>Instagram</b> (Reels, Posts, Stories)\n"
        "• 🔴 <b>YouTube & Shorts</b>\n"
        "• 🐦 <b>X / Twitter</b>\n"
        "• 📘 <b>Facebook</b>\n"
        "• 👽 <b>Reddit, Pinterest, Threads & 1000+ more!</b>\n\n"
        "👉 <b>Simply send or paste any video/audio link here!</b>"
    )
    if update.message:
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "📖 <b>How to Use Social Downloader Bot:</b>\n\n"
        "1. <b>Send any link</b> from YouTube, TikTok, Instagram, X/Twitter, Facebook, etc.\n"
        "2. Choose your preferred format from the interactive buttons:\n"
        "   - 🎬 <b>Best Quality</b> (Highest resolution up to 1080p/4K)\n"
        "   - 📱 <b>720p (Fast & Compact)</b> (Optimized file size)\n"
        "   - 🎵 <b>Audio Only (MP3 320kbps)</b>\n"
        "3. Wait a few seconds, and the bot will upload the file directly to your chat!\n\n"
        "⚠️ <b>Note on File Limits:</b>\n"
        "Telegram bots have a standard upload limit of <b>50 MB</b> per file. "
        "For very long videos, please choose <b>720p</b> or <b>Audio Only</b>."
    )
    if update.message:
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def handle_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detect and process URLs sent by users."""
    message = update.message
    if not message or not message.text:
        return

    match = URL_REGEX.search(message.text)
    if not match:
        await message.reply_text("❌ Please send a valid media URL (TikTok, Instagram, YouTube, etc.).")
        return

    url = match.group(0)
    status_msg = await message.reply_text("🔍 <b>Analyzing link... Please wait...</b>", parse_mode=ParseMode.HTML)

    # Fetch info in background thread
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, downloader.get_info, url)
        if not info:
            raise Exception("Unable to extract info from this link.")

        # Handle playlist or multiple entries
        if "entries" in info and info["entries"]:
            info = info["entries"][0]

        title = info.get("title", "Unknown Title") or "Unknown Title"
        uploader = info.get("uploader") or info.get("channel") or "Unknown Creator"
        duration = format_duration(info.get("duration"))

        # Create unique session token for this request
        req_id = uuid.uuid4().hex[:10]
        PENDING_DOWNLOADS[req_id] = {
            "url": url,
            "title": title,
            "uploader": uploader,
        }

        # Keep pending map bounded
        if len(PENDING_DOWNLOADS) > 200:
            first_key = next(iter(PENDING_DOWNLOADS))
            PENDING_DOWNLOADS.pop(first_key, None)

        keyboard = [
            [
                InlineKeyboardButton("🎬 Best Video", callback_data=f"dl:{req_id}:best"),
                InlineKeyboardButton("📱 720p Video", callback_data=f"dl:{req_id}:720p"),
            ],
            [
                InlineKeyboardButton("🎵 Audio MP3 (320k)", callback_data=f"dl:{req_id}:audio"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"dl:{req_id}:cancel"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        safe_title = html.escape(title[:80])
        safe_uploader = html.escape(uploader[:50])

        caption = (
            f"📹 <b>Title:</b> <code>{safe_title}</code>\n"
            f"👤 <b>Author:</b> {safe_uploader}\n"
            f"⏱ <b>Duration:</b> {duration}\n\n"
            "Choose a download format:"
        )

        await status_msg.edit_text(caption, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error extracting metadata for {url}: {e}")
        safe_err = html.escape(str(e)[:250])
        await status_msg.edit_text(
            f"⚠️ <b>Could not fetch media info:</b>\n<code>{safe_err}</code>\n\n"
            "Please check if the link is public and valid.",
            parse_mode=ParseMode.HTML,
        )


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle download button clicks."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data
    if not data or not data.startswith("dl:"):
        return

    parts = data.split(":")
    if len(parts) < 3:
        return

    req_id = parts[1]
    choice = parts[2]

    if choice == "cancel":
        PENDING_DOWNLOADS.pop(req_id, None)
        await query.edit_message_text("❌ Download cancelled.")
        return

    item = PENDING_DOWNLOADS.get(req_id)
    if not item:
        await query.edit_message_text("⚠️ This request has expired. Please send the link again.")
        return

    url = item["url"]
    title = item["title"]
    uploader = item.get("uploader", "Social Downloader")

    safe_title = html.escape(title[:60])
    await query.edit_message_text(f"⏳ <b>Downloading:</b> <code>{safe_title}</code>\nPlease wait...", parse_mode=ParseMode.HTML)

    loop = asyncio.get_running_loop()
    downloaded_file = None
    chat_id = query.message.chat_id if query.message else None
    if not chat_id:
        return

    try:
        # Send Typing/Recording action
        await context.bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.RECORD_VOICE if choice == "audio" else ChatAction.RECORD_VIDEO,
        )

        if choice == "audio":
            downloaded_file = await loop.run_in_executor(
                None, lambda: downloader.download(url, audio_only=True, audio_format="mp3")
            )
        else:
            quality = "best" if choice == "best" else "720p"
            downloaded_file = await loop.run_in_executor(
                None, lambda: downloader.download(url, quality=quality, audio_only=False)
            )

        if not downloaded_file or not os.path.exists(downloaded_file):
            raise Exception("File was not found after download.")

        file_size_bytes = os.path.getsize(downloaded_file)
        file_size_mb = file_size_bytes / (1024 * 1024)

        # Telegram Bot API upload limit is 50MB
        if file_size_mb > 50.0:
            await query.edit_message_text(
                f"⚠️ <b>File is too large for Telegram ({file_size_mb:.1f} MB)</b>\n\n"
                "Telegram bots are restricted to a maximum 50 MB upload limit.\n"
                "💡 <i>Tip:</i> Try selecting <b>720p</b> or <b>Audio Only (MP3)</b> instead.",
                parse_mode=ParseMode.HTML,
            )
            return

        await query.edit_message_text("⬆️ <b>Uploading to Telegram...</b>", parse_mode=ParseMode.HTML)

        # Build clean caption
        bot_username = context.bot.username if getattr(context.bot, "username", None) else "MultidownloadHak_bot"
        caption = f"🎬 {title[:90]}\n⚡ Downloaded with @{bot_username}"

        ext = os.path.splitext(downloaded_file)[1].lower()

        if choice == "audio":
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
            with open(downloaded_file, "rb") as f:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    title=title[:80],
                    performer=uploader[:50],
                    caption=caption,
                    read_timeout=120,
                    write_timeout=120,
                )
        elif ext in [".jpg", ".jpeg", ".png", ".webp"]:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
            with open(downloaded_file, "rb") as f:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=caption,
                    read_timeout=120,
                    write_timeout=120,
                )
        else:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
            try:
                with open(downloaded_file, "rb") as f:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=caption,
                        supports_streaming=True,
                        read_timeout=120,
                        write_timeout=120,
                    )
            except Exception as video_err:
                logger.warning(f"send_video failed ({video_err}), falling back to send_document")
                with open(downloaded_file, "rb") as f:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        caption=caption,
                        read_timeout=120,
                        write_timeout=120,
                    )

        await query.edit_message_text("✅ <b>Download and upload complete!</b>", parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Download/Upload error for {url}: {e}")
        safe_err = html.escape(str(e)[:250])
        await query.edit_message_text(
            f"❌ <b>Failed to process download:</b>\n<code>{safe_err}</code>",
            parse_mode=ParseMode.HTML,
        )

    finally:
        # Clean up temporary file to conserve disk space
        if downloaded_file and os.path.exists(downloaded_file):
            try:
                os.remove(downloaded_file)
            except Exception as clean_err:
                logger.warning(f"Failed to remove temp file {downloaded_file}: {clean_err}")
        PENDING_DOWNLOADS.pop(req_id, None)


class HealthHandler(BaseHTTPRequestHandler):
    """Simple HTTP healthcheck handler for Render keep-alive."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Social Downloader Bot is running healthy!")

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
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("=" * 60)
        print("⚠️  TELEGRAM_BOT_TOKEN NOT FOUND!")
        print("Please configure your Telegram Bot Token in the .env file:")
        print("TELEGRAM_BOT_TOKEN=8883643806:AAF2AUkT1UWo2DwuF-HnNcQj-POlIhtA7yI")
        print("=" * 60)
        try:
            token = input("Or enter your Telegram Bot Token now: ").strip()
        except EOFError:
            token = ""
        if not token:
            print("❌ No token provided. Exiting.")
            return

    print("🚀 Starting Social Downloader Telegram Bot (@MultidownloadHak_bot)...")
    app = Application.builder().token(token).build()

    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_message))

    print("🤖 Bot is running! Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
