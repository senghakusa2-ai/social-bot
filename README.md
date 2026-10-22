# ⚡ Universal Social Media & Story Downloader (High Quality)

A fast, modern Python CLI tool to download videos, reels, posts, and stories from virtually any social media platform in the highest available quality (up to 4K 60fps) or extract high-bitrate audio.

## 🌟 Supported Platforms
- **Instagram**: Reels, Posts, IGTV, and **Stories** (using `--cookies-from-browser`)
- **TikTok**: Videos & photos without watermark
- **YouTube**: Videos, Shorts, Playlists (4K, 1080p, 60fps)
- **Facebook**: Public/Private HD Videos & Reels
- **X (Twitter)**: High-resolution MP4 clips
- **Reddit**: Merged HD video + audio
- **Pinterest, Threads, Twitch, Vimeo, SoundCloud, and 1000+ more**.

---

## 🚀 Quick Setup

1. Open your terminal in this folder:
```powershell
cd C:\Users\Lenovo\social_downloader
```

2. Install dependencies:
```powershell
pip install -r requirements.txt
```

---

## 🛠️ Usage Examples

### 1. Interactive Mode (Easiest)
Simply run the tool without arguments:
```powershell
python main.py
```
It will prompt you for the URL and settings interactively.

### 2. Direct Video Download (Highest Quality)
```powershell
python main.py "https://www.tiktok.com/@user/video/123456789"
python main.py "https://www.instagram.com/reel/Cxxxxxx/"
python main.py "https://www.youtube.com/watch?v=xxxxxx"
```

### 3. Downloading Instagram Stories or Private Media
Stories require an active login session. You can pass your existing browser session automatically without typing passwords:
```powershell
python main.py "https://www.instagram.com/stories/username/1234567890/" --cookies-from-browser chrome
```
*(Supports `chrome`, `edge`, `firefox`, `brave`, `opera`)*

### 4. Custom Video Quality
Choose resolution (`best`, `4k`, `1080p`, `720p`, `480p`):
```powershell
python main.py "https://www.youtube.com/watch?v=xxxxxx" -q 1080p
```

### 5. Extract Audio Only (MP3 320kbps)
```powershell
python main.py "https://www.youtube.com/watch?v=xxxxxx" --audio-only
```

### 6. Custom Output Directory
```powershell
python main.py "https://..." -o "C:\MyVideos"
```

---

## 🤖 Telegram Bot Mode

You can also run your downloader as a full-featured Telegram Bot!

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy your API Token.
2. Edit `.env` in this directory:
   ```env
   TELEGRAM_BOT_TOKEN=your_token_here
   ```
3. Run the bot by double clicking `START_BOT.bat` (or shortcut `START_TELEGRAM_BOT.bat` on your Desktop), or via terminal:
   ```powershell
   python bot.py
   ```
4. In Telegram, send any media link (TikTok, Instagram Reel, YouTube Shorts, X/Twitter, etc.) and pick **Best Video**, **720p**, or **Audio Only (MP3)**!
