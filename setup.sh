#!/usr/bin/env bash
# One-shot setup for the VOX watcher on your own Ubuntu/Debian box or Raspberry Pi.
# Usage:  bash setup.sh
set -euo pipefail

echo "==> Installing system packages (python venv + pip)..."
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip

echo "==> Creating virtual environment (.venv)..."
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Installing Playwright's Chromium + system libraries..."
python -m playwright install --with-deps chromium

echo ""
echo "✅ Setup complete."
echo "Next:"
echo "  1) export TELEGRAM_BOT_TOKEN=... ; export TELEGRAM_CHAT_ID=..."
echo "  2) source .venv/bin/activate"
echo "  3) python watcher.py --seats 2 --test-telegram          # verify Telegram"
echo "  4) python watcher.py --seats 2 --mode notify --once     # single test cycle"
