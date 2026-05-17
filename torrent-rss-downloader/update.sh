#!/usr/bin/env bash
# Aktualizacja aplikacji do najnowszej wersji.
# Uruchom: bash update.sh
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="torrent-rss"

echo "=== Torrent RSS Downloader — aktualizacja ==="
cd "$APP_DIR"

# ── Pobierz zmiany z repozytorium ────────────────────────────
echo "[1/3] Pobieranie najnowszej wersji z git..."
git pull
echo "      ✓ Kod zaktualizowany"

# ── Zaktualizuj zależności ────────────────────────────────────
echo "[2/3] Aktualizacja zależności Python..."
./venv/bin/pip install -r requirements.txt -q
echo "      ✓ Zależności aktualne"

# ── Restart usługi ────────────────────────────────────────────
echo "[3/3] Restart usługi ${SERVICE_NAME}..."
sudo systemctl restart "$SERVICE_NAME"
sleep 2
sudo systemctl status "$SERVICE_NAME" --no-pager

echo ""
echo "✓ Aktualizacja zakończona"
