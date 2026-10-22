import os
import re
import uuid
import asyncio
import logging
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
    welcome_text = (
        f"👋 **Hello {user.first_name if user else 'there'}!**\n\n"
        "⚡ Welcome to **Universal Social Downloader Bot**!\n\n"
        "I can download videos, reels, posts, and audios from:\n"
        "• 📱 **TikTok** (No Watermark)\n"
        "• 📸 **Instagram** (Reels, Posts, Stories)\n"
        "• 🔴 **YouTube & Shorts**\n"
        "• 🐦 **X / Twitter**\n"
        "• 📘 **Facebook**\n"
        "• 👽 **Reddit, Pinterest, Threads & 1000+ more!**\n\n"
        "👉 **Simply send or paste any video/audio link here!**"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "📖 **How to Use Social Downloader Bot:**\n\n"
        "1. **Send any link** from YouTube, TikTok, Instagram, X/Twitter, Facebook, etc.\n"
        "2. Choose your preferred format from the interactive buttons:\n"
        "   - 🎬 **Best Quality** (Highest resolution up to 1080p/4K)\n"
        "   - 📱 **720p (Fast & Compact)** (Optimized file size)\n"
        "   - 🎵 **Audio Only (MP3 320kbps)**\n"
        "3. Wait a few seconds, and the bot will upload the file directly to your chat!\n\n"
        "⚠️ **Note on File Limits:**\n"
        "Telegram bots have a standard upload limit of **50 MB** per file. "
        "For very long videos, please choose **720p** or **Audio Only**."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


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
    status_msg = await message.reply_text("🔍 **Analyzing link... Please wait...**", parse_mode=ParseMode.MARKDOWN)

    # Fetch info in thread
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, downloader.get_info, url)
        if not info:
            raise Exception("Unable to extract info from this link.")

        # Handle playlist or entries
        if "entries" in info and info["entries"]:
            info = info["entries"][0]

        title = info.get("title", "Unknown Title")
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

        caption = (
            f"📹 **Title:** `{title[:80]}`\n"
            f"👤 **Author:** {uploader}\n"
            f"⏱ **Duration:** {duration}\n\n"
            "Choose a download format:"
        )

        await status_msg.edit_text(caption, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Error extracting metadata for {url}: {e}")
        await status_msg.edit_text(
            f"⚠️ **Could not fetch media info:**\n`{str(e)}`\n\n"
            "Please check if the link is public and valid.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle download button clicks."""
    query = update.callback_query
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

    await query.edit_message_text(f"⏳ **Downloading:** `{title[:60]}`\nPlease wait...", parse_mode=ParseMode.MARKDOWN)

    loop = asyncio.get_running_loop()
    downloaded_file = None

    try:
        # Send Typing/Recording action
        await context.bot.send_chat_action(
            chat_id=query.message.chat_id,
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
                f"⚠️ **File is too large for Telegram ({file_size_mb:.1f} MB)**\n\n"
                "Telegram bots are restricted to a maximum 50 MB upload limit.\n"
                "💡 *Tip:* Try selecting **720p** or **Audio Only (MP3)** instead.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await query.edit_message_text("⬆️ **Uploading to Telegram...**", parse_mode=ParseMode.MARKDOWN)

        # Send File
        caption = f"🎬 {title[:100]}\n⚡ Downloaded with @{context.bot.username or 'SocialDownloaderBot'}"

        if choice == "audio":
            await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.UPLOAD_VOICE)
            with open(downloaded_file, "rb") as f:
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=f,
                    title=title[:80],
                    performer=item.get("uploader", "Social Downloader"),
                    caption=caption,
                )
        else:
            await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.UPLOAD_VIDEO)
            with open(downloaded_file, "rb") as f:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                )

        await query.edit_message_text("✅ **Download and upload complete!**", parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Download/Upload error for {url}: {e}")
        await query.edit_message_text(
            f"❌ **Failed to process download:**\n`{str(e)}`",
            parse_mode=ParseMode.MARKDOWN,
        )

    finally:
        # Clean up temporary file to conserve disk space
        if downloaded_file and os.path.exists(downloaded_file):
            try:
                os.remove(downloaded_file)
            except Exception as clean_err:
                logger.warning(f"Failed to remove temp file {downloaded_file}: {clean_err}")
        PENDING_DOWNLOADS.pop(req_id, None)


def main():
    """Start the Telegram bot."""
    token = BOT_TOKEN
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("=" * 60)
        print("⚠️  TELEGRAM_BOT_TOKEN NOT FOUND!")
        print("Please configure your Telegram Bot Token in the .env file:")
        print("TELEGRAM_BOT_TOKEN=123456789:ABCDefgh-your_token_here")
        print("=" * 60)
        token = input("Or enter your Telegram Bot Token now: ").strip()
        if not token:
            print("❌ No token provided. Exiting.")
            return

    print("🚀 Starting Social Downloader Telegram Bot...")
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
