"""
Configuration for the VOX showtimes watcher.
Prefer environment variables for secrets; edit the rest here as needed.
"""
import os

# ----- Telegram -----
# 1) Create a bot with @BotFather -> get the token
# 2) Message your bot once, then open
#    https://api.telegram.org/bot<TOKEN>/getUpdates  to read your numeric chat id
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "PUT-YOUR-BOT-TOKEN-HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "PUT-YOUR-CHAT-ID-HERE")

# ----- What to watch -----
CINEMA = "city-centre-almaza"   # slug from the URL: ?c=<cinema>
MOVIE = "the-odyssey"           # slug from the URL: ?m=<movie>

# "Next week": leave TARGET_DATES empty to auto-check the date DAYS_AHEAD_IF_AUTO
# ahead each cycle; or hard-code specific dates like ["20260818", "20260819"].
# You can also override via env VOX_DATES (comma-separated) — handy on CI.
TARGET_DATES = [d for d in os.getenv("VOX_DATES", "").split(",") if d.strip()]
DAYS_AHEAD_IF_AUTO = 7

EXPERIENCE = os.getenv("VOX_EXPERIENCE", "IMAX")   # "IMAX" | "GOLD" | "Standard"

# ----- Booking preference (seat auto-selection) -----
# From the real VOX IMAX map: the screen is at the TOP, and the blue "Regular"
# block is the FRONT section — only the rows up to the 5th row from the screen.
# "Most backward seats in the regular section" therefore means: the LAST regular
# row (the 5th from the screen), centered horizontally.
SEATS_TO_BOOK = None            # set at startup via --seats

REGULAR_HINTS = ["regular", "seat--regular", "available", "seat-available"]
REGULAR_COLORS = ["#4aa3df", "#3fa9e0", "rgb(74, 163, 223)"]
REGULAR_MAX_ROWS_FROM_SCREEN = 5

# ----- Timing -----
POLL_SECONDS = 300              # 5 minutes (used only in the always-on loop)
JITTER_SECONDS = 45             # random +/- so requests don't look robotic
HEADLESS = True

# ----- Behaviour -----
# "notify" -> only send Telegram alert when the date/showtime appears (safest)
# "hold"   -> also open seat map, auto-pick best seats, stop BEFORE payment,
#             and Telegram you the checkout link to finish paying yourself
MODE = os.getenv("VOX_MODE", "notify")   # "notify" | "hold"

# Runtime flags (set by CLI; leave as-is)
RUN_ONCE = False
# Path to a small JSON file remembering which dates were already alerted, so
# scheduled / --once runs (e.g. GitHub Actions) don't re-notify every time.
STATE_FILE = os.getenv("VOX_STATE_FILE") or None
