#!/usr/bin/env python3
"""
VOX Cinemas showtimes watcher + Telegram notifier (+ optional seat "hold").

MODE="notify" (default): the moment the target date publishes IMAX showtimes,
send a Telegram ping with the direct booking link.

MODE="hold": additionally open the seat map, auto-pick the center / most-back
row of the REGULAR (blue) block, and stop before payment (never pays).

Flags:
  --test-telegram  send a test Telegram message and exit
  --dry-run        send a FAKE "showtimes live" alert (no real date) and exit
  --once           single cycle then exit
  --state-file P   remember alerted dates in JSON P (avoids re-notify)
  --debug          save page screenshot + text for each date checked
  --calibrate      open seat page, dump seat colours/classes, screenshot
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

# Chromium flags: force HTTP/1.1 (fixes net::ERR_HTTP2_PROTOCOL_ERROR some CDNs
# throw at headless Chromium) + standard CI-friendly hardening.
CHROMIUM_ARGS = [
    "--disable-http2",
    "--disable-quic",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]


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
        print("[telegram] sent." if r.status_code == 200
              else f"[telegram] error: {r.status_code} {r.text}")
    except Exception as e:
        print("[telegram] exception:", e)


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
def _goto_with_retry(page, url, tag="page"):
    """Navigate with retries. Uses lenient 'commit' wait (resolves as soon as
    the server responds). On final failure, saves a screenshot + HTML so we can
    SEE whether the CDN served a bot-challenge page. Returns True or raises."""
    last = None
    for attempt in range(1, config.NAV_RETRIES + 1):
        try:
            page.goto(url, wait_until="commit", timeout=config.NAV_TIMEOUT_MS)
            # Let the SPA (or any challenge) settle, then continue regardless.
            page.wait_for_timeout(6000)
            return True
        except Exception as e:
            last = e
            print(f"  [nav] attempt {attempt}/{config.NAV_RETRIES} failed: "
                  f"{type(e).__name__}: {str(e).splitlines()[0]}")
            # Always try to capture what the browser is actually seeing.
            try:
                page.screenshot(path=f"debug_{tag}_attempt{attempt}.png",
                                full_page=True)
                html = page.content()
                with open(f"debug_{tag}_attempt{attempt}.html", "w",
                          encoding="utf-8") as f:
                    f.write(html)
                snippet = " ".join(html.split())[:300]
                print(f"  [nav] page snapshot title/snippet: {snippet!r}")
            except Exception as ce:
                print("  [nav] could not capture page:", ce)
            page.wait_for_timeout(2000 * attempt)
    raise last


def check_date(page, date_yyyymmdd: str):
    """
    Return one of:
      * dict  -> showtimes found for the wanted EXPERIENCE
      * None  -> page loaded fine but no matching showtimes yet
      * raises -> genuine load/network error (caller distinguishes this)
    """
    url = showtimes_url(date_yyyymmdd)
    _goto_with_retry(page, url, tag=date_yyyymmdd)   # may raise -> real error
    body = page.inner_text("body")

    if config.DEBUG:
        try:
            page.screenshot(path=f"debug_{date_yyyymmdd}.png", full_page=True)
            with open(f"debug_{date_yyyymmdd}.txt", "w", encoding="utf-8") as f:
                f.write(body)
            print(f"  [debug] saved debug_{date_yyyymmdd}.png / .txt "
                  f"(page text length={len(body)})")
        except Exception as e:
            print("  [debug] could not save artifacts:", e)

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
    try:
        _goto_with_retry(page, hit["url"])
        page.click(f"text={hit['times'][0]}", timeout=15_000)
        page.wait_for_load_state("domcontentloaded")
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
                page.wait_for_load_state("domcontentloaded")
                break
            except Exception:
                continue
        return page.url
    except Exception:
        traceback.print_exc()
        return None


def calibrate(seats: int):
    from playwright.sync_api import sync_playwright
    config.HEADLESS = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=CHROMIUM_ARGS)
        page = browser.new_context(locale="en-US").new_page()
        for d in target_dates():
            try:
                hit = check_date(page, d)
            except Exception as e:
                print(f"{d}: load error {e}")
                continue
            if not hit:
                print(f"{d}: not live yet, skipping")
                continue
            page.click(f"text={hit['times'][0]}", timeout=15_000)
            page.wait_for_load_state("domcontentloaded")
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
def one_pass(page, seats, mode, already_alerted):
    errors = []
    for d in target_dates():
        if d in already_alerted:
            continue
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        try:
            hit = check_date(page, d)
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e).splitlines()[0]}"
            print(f"[{stamp}] {d}: LOAD ERROR -> {msg}")
            errors.append((d, msg))
            continue   # do NOT treat a load error as "not available"

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

    # Surface persistent load failures instead of hiding them.
    if errors and config.NOTIFY_ON_ERROR:
        lines = "\n".join(f"• {d}: {m}" for d, m in errors)
        tg_send(f"⚠️ <b>VOX watcher: page load failed</b>\n{lines}\n"
                f"(Will retry next cycle.)")


def run(seats: int, mode: str):
    from playwright.sync_api import sync_playwright

    already_alerted = load_state()
    if not config.RUN_ONCE:
        tg_send(
            f"👀 <b>VOX watcher started</b>\n"
            f"Movie: {config.MOVIE} @ {config.CINEMA}\n"
            f"Experience: {config.EXPERIENCE} | Seats: {seats} | Mode: {mode}\n"
            f"Polling every ~{config.POLL_SECONDS//60} min."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS, args=CHROMIUM_ARGS)
        ctx = browser.new_context(
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
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


def dry_run():
    d = target_dates()[0]
    tg_send(
        f"🧪 <b>[TEST] The Odyssey — showtimes are LIVE!</b>\n"
        f"📅 {human_date(d)} · {config.EXPERIENCE} @ Almaza\n"
        f"🕒 6:45pm, 10:30pm  (sample)\n"
        f'🔗 <a href="{showtimes_url(d)}">Open booking page</a>\n'
        f"<i>This is a dry-run test — not a real availability alert.</i>"
    )


def parse_args():
    ap = argparse.ArgumentParser(description="VOX showtimes watcher + Telegram")
    ap.add_argument("--seats", type=int)
    ap.add_argument("--mode", choices=["notify", "hold"], default=config.MODE)
    ap.add_argument("--dates", type=str, default="")
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--state-file", type=str, default=config.STATE_FILE)
    ap.add_argument("--debug", action="store_true",
                    help="save page screenshot + text for each date")
    ap.add_argument("--calibrate", action="store_true")
    return ap.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.dates.strip():
        config.TARGET_DATES = [x.strip() for x in args.dates.split(",") if x.strip()]
    config.DEBUG = args.debug

    if args.test_telegram:
        tg_send("✅ VOX watcher: Telegram is wired up correctly.")
        sys.exit(0)
    if args.dry_run:
        dry_run()
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

    config.SEATS_TO_BOOK = seats
    config.RUN_ONCE = args.once
    config.STATE_FILE = args.state_file

    if args.calibrate:
        calibrate(seats)
        sys.exit(0)

    run(seats=seats, mode=args.mode)
