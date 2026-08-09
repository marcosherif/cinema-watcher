# 🎬 VOX Showtimes Watcher + Telegram Notifier

Checks the VOX Cinemas showtimes page and, the instant your target date
(default: **next week**) publishes **IMAX** showtimes, pings your Telegram bot
with a direct booking link. Optional `hold` mode auto-selects the **center /
most-back row of the Regular (blue) block** and stops **before payment**.

**Runs 100% free with NO credit card** — two ways (see below).

> ⚠️ Read "Legal / ethical notes" at the bottom before running.

---

## Files
| File | Purpose |
|---|---|
| `watcher.py` | main script (poller + Telegram + optional seat-hold + test flags) |
| `config.py` | settings (also read from env vars for CI) |
| `requirements.txt` | Python deps |
| `.github/workflows/watch.yml` | **card-free 24/7** via GitHub Actions cron |
| `setup.sh` | one-shot installer for your own Linux box / Pi |
| `vox-watcher.service` | systemd unit for a self-hosted box |

---

## 🥇 Option A — GitHub Actions (free, NO card, recommended for notify)

GitHub runs your check on a schedule — no server, no card, ~2,000 free
min/month (unlimited on public repos). Perfect for "ping me when next week
opens". *(It's a scheduled job, not a live session, so use it for `notify`, not
`hold`.)*

1. **Create a GitHub account** (free) and a **new repository** (private is fine).
2. **Upload these files** to the repo (drag-and-drop in the web UI works).
3. **Add your secrets**: repo → **Settings → Secrets and variables → Actions →
   New repository secret**. Add:
   * `TELEGRAM_BOT_TOKEN`
   * `TELEGRAM_CHAT_ID`
4. (Optional) tweak `.github/workflows/watch.yml` env: `VOX_SEATS`, or pin
   `VOX_DATES: "20260818,20260819"` instead of auto next-week.
5. **Enable Actions**: the **Actions** tab → enable workflows. It now runs every
   ~10 min automatically; you can also click **Run workflow** to test instantly.
6. You'll get a Telegram alert the moment the date goes live. The `state.json`
   cache stops it re-alerting every run.

> ℹ️ GitHub disables scheduled workflows after ~60 days of **no repo activity**.
> Just push any commit occasionally, or click **Run workflow**, to keep it live.

---

## 🥈 Option B — Your own device (free, NO card, needed for `hold`)

Any always-on machine you own — a **Raspberry Pi**, an **old laptop**, or your
**PC** — runs the full always-on loop, including `hold` mode.

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
export TELEGRAM_BOT_TOKEN="..."; export TELEGRAM_CHAT_ID="..."   # Windows: setx
python watcher.py --seats 2 --test-telegram           # verify Telegram
python watcher.py --seats 2 --mode notify             # run the live loop
```

On Linux/Pi, keep it running across reboots with the included `vox-watcher.service`:
```bash
bash setup.sh                                   # installs venv + Playwright + Chromium
nano vox-watcher.service                        # set User, path, creds, seats
sudo cp vox-watcher.service /etc/systemd/system/vox-watcher.service
sudo systemctl daemon-reload
sudo systemctl enable --now vox-watcher
journalctl -u vox-watcher -f                    # live logs
```
Trade-off: it only runs while the device is powered on.

---

## Test flags
| Flag | Does |
|---|---|
| `--test-telegram` | send a test message and exit |
| `--once` | run one check cycle then exit (used by CI) |
| `--state-file P` | remember alerted dates in JSON `P` (avoids re-notify) |
| `--dates 20260811` | force specific date(s), YYYYMMDD (great for a live test) |
| `--calibrate` | open the seat page, print seat colours/classes, save `seatmap_calibrate.png` |

Env-var equivalents (handy on CI): `VOX_SEATS`, `VOX_MODE`, `VOX_DATES`,
`VOX_EXPERIENCE`, `VOX_STATE_FILE`.

---

## Telegram bot setup (2 min)
1. Telegram → **@BotFather** → `/newbot` → copy the **token**.
2. Message your new bot once (bots can't message you first).
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy the numeric
   `"chat":{"id":...}` value = your **chat id**.

---

## Why not a cloud VPS?
Oracle / Google / AWS "always-free" VMs are great but **all require a credit
card** for identity verification. Card-free "free VPS" hosts are mostly 30-day
trials or unreliable weekly-renew boxes — not worth trusting for weeks. GitHub
Actions (Option A) and your own device (Option B) are the genuinely card-free,
reliable $0 routes.

Avoid Render/Railway/Fly free tiers here too: they **sleep when idle**, which
breaks a long-running poller.

---

## Tuning to the real seat map (`hold` mode)
Seat selection reads each seat's on-screen **geometry** + category:
* Screen is at the **TOP** → smaller y = closer to screen.
* Regular block = first `REGULAR_MAX_ROWS_FROM_SCREEN` (=5) rows; we pick the
  **last** = most-backward regular row, then the **center** seats by x.
* Regular seats matched via `REGULAR_HINTS` / `REGULAR_COLORS` (blue).

When a date goes live, run `python watcher.py --seats 2 --calibrate`, inspect the
printed colours/classes + `seatmap_calibrate.png`, then update `REGULAR_COLORS`,
`REGULAR_HINTS`, or the seat selector in `try_hold_seats()`. Notify mode needs
none of this.

---

## ⚖️ Legal / ethical notes
* **Terms of Service:** automated access/booking is commonly restricted by
  ticketing sites. Review VOX's Terms; use responsibly and at your own risk.
* **No auto-payment by design:** `hold` mode stops at checkout and hands the
  payment step to you. Don't modify it to auto-pay.
* **Be polite:** keep the ~5–10-min interval + jitter. Don't hammer the site.
* **Personal use only:** for grabbing your own seats, not bulk/resale.
