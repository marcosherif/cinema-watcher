#!/usr/bin/env python3
"""
VOX Cinemas showtimes watcher + Telegram notifier (+ optional seat "hold").

MODE="notify" (default, safest): check target date; the moment it publishes IMAX
showtimes, you get a Telegram ping with the direct booking link.

MODE="hold": additionally opens the seat map, auto-selects the center / most-back
row of the REGULAR (blue) block, and stops right before payment, sending you the
checkout URL so YOU tap "Pay". It never enters card details.

Runs two ways:
  * Always-on loop (VPS / your PC):   python watcher.py --seats 2
  * One-shot for schedulers (CI/cron): python watcher.py --seats 2 --once \
                                          --state-file state.json

Test/utility flags:
    --test-telegram   send a test Telegram message and exit
    --once            run a single check cycle then exit (no loop)
    --state-file P    remember alerted dates in JSON P (avoids re-notifying)
    --calibrate       open seat page, dump seat colours/classes, screenshot
"""
import argparse
import datetime as dt
import json
import os
import random
import sys
import time
import traceback
from urllib.parse import urlencode

import requests
import config

BASE = "https://egy.voxcinemas.com/showtimes"


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def tg_send(text: str, disable_preview: bool = False) -> None:
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if "PUT-YOUR" in token or "PUT-YOUR" in str(chat_id):
        print("[telegram] token/chat_id not set — printing instead:\n", text)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": disable_preview},
            timeout=20,
        )
        if r.status_code != 200:
            print("[telegram] error:", r.status_code, r.text)
        else:
            print("[telegram] sent.")
    except Exception as e:
        print("[telegram] exception:", e)


# --------------------------------------------------------------------------- #
# State persistence (so schedulers don't re-alert)
# --------------------------------------------------------------------------- #
def load_state() -> set:
    p = config.STATE_FILE
    if p and os.path.exists(p):
        try:
            with open(p) as f:
                return set(json.load(f).get("alerted", []))
        except Exception:
            pass
    return set()


def save_state(alerted: set) -> None:
    p = config.STATE_FILE
    if not p:
        return
    try:
        with open(p, "w") as f:
            json.dump({"alerted": sorted(alerted)}, f)
    except Exception as e:
        print("[state] could not save:", e)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def target_dates() -> list:
    if config.TARGET_DATES:
        return config.TARGET_DATES
    d = dt.date.today() + dt.timedelta(days=config.DAYS_AHEAD_IF_AUTO)
    return [d.strftime("%Y%m%d")]


def showtimes_url(date_yyyymmdd: str) -> str:
    q = urlencode({"c": config.CINEMA, "m": config.MOVIE, "d": date_yyyymmdd})
    return f"{BASE}?{q}"


def human_date(yyyymmdd: str) -> str:
    return dt.datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%a %d %b %Y")


# --------------------------------------------------------------------------- #
# Page checking (Playwright)
# --------------------------------------------------------------------------- #
def check_date(page, date_yyyymmdd: str):
    """Return dict with found showtimes for the wanted EXPERIENCE, or None."""
    url = showtimes_url(date_yyyymmdd)
    page.goto(url, wait_until="networkidle", timeout=60_000)
    page.wait_for_timeout(2500)

    body = page.inner_text("body")
    lowered = body.lower()
    no_signals = ["no showtimes", "not available", "no sessions", "coming soon"]
    if any(s in lowered for s in no_signals) and config.EXPERIENCE.lower() not in lowered:
        return None
    if config.EXPERIENCE.lower() not in lowered:
        return None

    times = page.eval_on_selector_all(
        "a, button",
        """els => els
            .map(e => (e.innerText || '').trim())
            .filter(t => /^\\d{1,2}:\\d{2}\\s?(am|pm)$/i.test(t))
        """,
    )
    times = sorted(set(times))
    if not times:
        return None
    return {"date": date_yyyymmdd, "url": url,
            "experience": config.EXPERIENCE, "times": times}


# --------------------------------------------------------------------------- #
# Optional seat "hold" (stops before payment)
# --------------------------------------------------------------------------- #
def _read_seats(page):
    return page.eval_on_selector_all(
        "[data-seat], [data-seat-number], .seat, [class*='seat']",
        """(els, cfg) => {
            const hints  = cfg.hints.map(s => s.toLowerCase());
            const colors = cfg.colors.map(s => s.toLowerCase());
            return els.map(e => {
                const r   = e.getBoundingClientRect();
                const cls = (e.className || '').toString().toLowerCase();
                const lab = (e.getAttribute('aria-label') || e.innerText || '').trim();
                const cs  = getComputedStyle(e);
                const fill = (e.getAttribute('fill') || cs.backgroundColor || cs.fill || '').toLowerCase();
                const blob = cls + ' ' + lab.toLowerCase() + ' ' + fill;
                const unavailable = /(sold|reserved|occupied|unavailable|disabled|--x|cross)/.test(blob)
                                    || e.getAttribute('aria-disabled') === 'true';
                const isRegular = hints.some(h => blob.includes(h))
                                  || colors.some(c => fill.includes(c));
                return { x: r.left + r.width/2, y: r.top + r.height/2, w: r.width,
                         label: lab, cls, fill, available: !unavailable, regular: isRegular };
            }).filter(s => s.w > 4);
        }""",
        {"hints": config.REGULAR_HINTS, "colors": config.REGULAR_COLORS},
    )


def try_hold_seats(page, hit: dict, seats: int):
    """Auto-pick the most-backward REGULAR row (screen at top), centered, then
    return the checkout URL WITHOUT paying. Returns URL or None."""
    try:
        page.goto(hit["url"], wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2000)
        page.click(f"text={hit['times'][0]}", timeout=15_000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2500)

        seats_info = _read_seats(page)
        reg = [s for s in seats_info if s["available"] and s["regular"]]
        if not reg:
            return None

        reg.sort(key=lambda s: s["y"])
        rows, cur, last_y = [], [], None
        for s in reg:
            if last_y is None or abs(s["y"] - last_y) <= max(6, s["w"] * 0.6):
                cur.append(s)
            else:
                rows.append(cur); cur = [s]
            last_y = s["y"]
        if cur:
            rows.append(cur)

        rows = rows[: config.REGULAR_MAX_ROWS_FROM_SCREEN]
        target_row = rows[-1]
        if len(target_row) < seats:
            fits = [r for r in rows if len(r) >= seats]
            if not fits:
                return None
            target_row = fits[-1]

        target_row.sort(key=lambda s: s["x"])
        mid_x = (target_row[0]["x"] + target_row[-1]["x"]) / 2
        best_start, best_cost = 0, float("inf")
        for i in range(0, len(target_row) - seats + 1):
            window = target_row[i:i + seats]
            block_mid = (window[0]["x"] + window[-1]["x"]) / 2
            cost = abs(block_mid - mid_x)
            if cost < best_cost:
                best_cost, best_start = cost, i
        chosen = target_row[best_start:best_start + seats]

        for s in chosen:
            if s["label"]:
                page.click(f"[aria-label='{s['label']}']", timeout=8000)
            else:
                page.mouse.click(s["x"], s["y"])
            page.wait_for_timeout(400)

        for label in ["Continue", "Proceed", "Checkout", "Confirm seats", "Next"]:
            try:
                page.click(f"text={label}", timeout=4000)
                page.wait_for_load_state("networkidle")
                break
            except Exception:
                continue
        return page.url
    except Exception:
        traceback.print_exc()
        return None


def calibrate(seats: int):
    """Open the first live target date's seat page, dump seat info + screenshot."""
    from playwright.sync_api import sync_playwright
    config.HEADLESS = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context(locale="en-US").new_page()
        for d in target_dates():
            hit = check_date(page, d)
            if not hit:
                print(f"{d}: not live yet, skipping")
                continue
            page.goto(hit["url"], wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(2000)
            page.click(f"text={hit['times'][0]}", timeout=15_000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2500)
            seats_info = _read_seats(page)
            print(f"\nFound {len(seats_info)} seat nodes. Sample:")
            for s in seats_info[:25]:
                print(f"  fill={s['fill']!r:24} regular={s['regular']} "
                      f"avail={s['available']} cls={s['cls'][:40]!r} label={s['label']!r}")
            page.screenshot(path="seatmap_calibrate.png", full_page=True)
            print("\nSaved screenshot -> seatmap_calibrate.png")
            input("Press Enter to close browser...")
            break
        browser.close()


# --------------------------------------------------------------------------- #
# Core check (one pass over all target dates)
# --------------------------------------------------------------------------- #
def one_pass(page, seats, mode, already_alerted):
    for d in target_dates():
        if d in already_alerted:
            continue
        try:
            hit = check_date(page, d)
        except Exception:
            traceback.print_exc()
            hit = None

        stamp = dt.datetime.now().strftime("%H:%M:%S")
        if hit:
            print(f"[{stamp}] HIT {d}: {hit['times']}")
            tg_send(
                f"🎬 <b>The Odyssey — showtimes are LIVE!</b>\n"
                f"📅 {human_date(d)} · {config.EXPERIENCE} @ Almaza\n"
                f"🕒 {', '.join(hit['times'])}\n"
                f'🔗 <a href="{hit["url"]}">Open booking page</a>'
            )
            if mode == "hold":
                checkout = try_hold_seats(page, hit, seats)
                if checkout:
                    tg_send(
                        f"🪑 <b>{seats} seat(s) held</b> (center / most-back regular "
                        f"row).\n💳 Finish payment here:\n{checkout}\n"
                        f"⚠️ Seats hold only a few minutes — pay now."
                    )
                else:
                    tg_send("⚠️ Couldn't auto-pick seats (map layout). "
                            "Open the booking link above and grab them manually.")
            already_alerted.add(d)
            save_state(already_alerted)
        else:
            print(f"[{stamp}] {d}: not available yet")


# --------------------------------------------------------------------------- #
# Runners
# --------------------------------------------------------------------------- #
def run(seats: int, mode: str):
    from playwright.sync_api import sync_playwright

    already_alerted = load_state()
    # Only announce startup for the always-on loop (not every CI tick).
    if not config.RUN_ONCE:
        tg_send(
            f"👀 <b>VOX watcher started</b>\n"
            f"Movie: {config.MOVIE} @ {config.CINEMA}\n"
            f"Experience: {config.EXPERIENCE} | Seats: {seats} | Mode: {mode}\n"
            f"Polling every ~{config.POLL_SECONDS//60} min."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        ctx = browser.new_context(
            locale="en-US",
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        )
        page = ctx.new_page()

        while True:
            one_pass(page, seats, mode, already_alerted)

            if all(d in already_alerted for d in target_dates()):
                if not config.RUN_ONCE:
                    tg_send("✅ All target dates handled. Watcher exiting.")
                break
            if config.RUN_ONCE:
                print("[--once] single cycle done, exiting.")
                break

            sleep = config.POLL_SECONDS + random.randint(-config.JITTER_SECONDS,
                                                          config.JITTER_SECONDS)
            time.sleep(max(30, sleep))

        browser.close()


def parse_args():
    ap = argparse.ArgumentParser(description="VOX showtimes watcher + Telegram")
    ap.add_argument("--seats", type=int, help="number of seats to target")
    ap.add_argument("--mode", choices=["notify", "hold"], default=config.MODE)
    ap.add_argument("--dates", type=str, default="",
                    help="comma-separated YYYYMMDD; overrides auto next-week")
    ap.add_argument("--test-telegram", action="store_true",
                    help="send a test Telegram message and exit")
    ap.add_argument("--once", action="store_true",
                    help="run a single check cycle then exit")
    ap.add_argument("--state-file", type=str, default=config.STATE_FILE,
                    help="JSON file to remember alerted dates (avoids re-notify)")
    ap.add_argument("--calibrate", action="store_true",
                    help="open seat page, dump colours/classes + screenshot")
    return ap.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.test_telegram:
        tg_send("✅ VOX watcher: Telegram is wired up correctly.")
        sys.exit(0)

    seats = args.seats
    if seats is None:
        env_seats = os.getenv("VOX_SEATS")
        if env_seats and env_seats.isdigit():
            seats = int(env_seats)
        else:
            try:
                seats = int(input("How many seats to reserve? ").strip())
            except (ValueError, EOFError):
                print("Need a seat count. Example: python watcher.py --seats 2")
                sys.exit(1)

    if args.dates.strip():
        config.TARGET_DATES = [x.strip() for x in args.dates.split(",") if x.strip()]
    config.SEATS_TO_BOOK = seats
    config.RUN_ONCE = args.once
    config.STATE_FILE = args.state_file

    if args.calibrate:
        calibrate(seats)
        sys.exit(0)

    run(seats=seats, mode=args.mode)
