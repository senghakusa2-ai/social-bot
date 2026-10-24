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

You can run your downloader as a full-featured Telegram Bot!

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy your API Token.
2. Edit `.env` in this directory:
   ```env
   TELEGRAM_BOT_TOKEN=your_token_here
   COOKIES_FILE=cookies.txt
   ```
3. Run the bot by double clicking `START_BOT.bat`, or via desktop shortcut `START_TELEGRAM_BOT.bat`.
4. In Telegram, send any media link (TikTok, Instagram Reels & Carousels, YouTube, X/Twitter, Facebook, etc.). The bot automatically downloads in highest quality (HD - 4K)!

### 📸 Downloading Instagram & Facebook Stories in Bot
Instagram and Facebook require an active login session to view Stories.
1. In your browser (Chrome/Edge/Firefox), install the extension **"Get cookies.txt LOCALLY"**.
2. Log into Instagram and Facebook.
3. Export cookies and save the file as `cookies.txt` directly inside `C:\Users\Lenovo\social_downloader\cookies.txt`.
4. Restart the bot! The bot will automatically detect `cookies.txt` and can now download any Story link seamlessly.
