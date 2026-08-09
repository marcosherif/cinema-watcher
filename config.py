"""
Configuration for the VOX showtimes watcher.
Prefer environment variables for secrets; edit the rest here as needed.
"""
import os

# ----- Telegram -----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "PUT-YOUR-BOT-TOKEN-HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "PUT-YOUR-CHAT-ID-HERE")

# ----- What to watch -----
CINEMA = "city-centre-almaza"   # slug from the URL: ?c=<cinema>
MOVIE = "the-odyssey"           # slug from the URL: ?m=<movie>

# "Next week": leave TARGET_DATES empty to auto-check the date DAYS_AHEAD_IF_AUTO
# ahead each cycle; or override via env VOX_DATES (comma-separated), or --dates.
TARGET_DATES = [d for d in os.getenv("VOX_DATES", "").split(",") if d.strip()]
DAYS_AHEAD_IF_AUTO = 7

EXPERIENCE = os.getenv("VOX_EXPERIENCE", "IMAX")   # "IMAX" | "GOLD" | "Standard"

# ----- Booking preference (seat auto-selection) -----
SEATS_TO_BOOK = None
REGULAR_HINTS = ["regular", "seat--regular", "available", "seat-available"]
REGULAR_COLORS = ["#4aa3df", "#3fa9e0", "rgb(74, 163, 223)"]
REGULAR_MAX_ROWS_FROM_SCREEN = 5

# ----- Timing -----
POLL_SECONDS = 300
JITTER_SECONDS = 45
HEADLESS = True

# Network robustness
NAV_RETRIES = 3                 # how many times to retry a failed page load
NAV_TIMEOUT_MS = 45_000
# Alert me on Telegram if the site keeps failing to load (so a broken site or
# IP-block doesn't fail silently). Set False to keep errors log-only.
NOTIFY_ON_ERROR = True

# ----- Behaviour -----
MODE = os.getenv("VOX_MODE", "notify")   # "notify" | "hold"

# Runtime flags
RUN_ONCE = False
DEBUG = False
STATE_FILE = os.getenv("VOX_STATE_FILE") or None
