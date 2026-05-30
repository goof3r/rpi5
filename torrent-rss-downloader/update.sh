#!/usr/bin/env bash
# Aktualizacja aplikacji do najnowszej wersji.
# Działa zarówno po git clone jak i po ręcznym skopiowaniu plików.
# Uruchom: bash update.sh
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="torrent-rss"
REPO_URL="https://github.com/goof3r/rpi5.git"
REPO_SUBDIR="torrent-rss-downloader"
DATA_DIR="${DATA_DIR:-/var/lib/torrent-rss}"

echo "=== Torrent RSS Downloader — aktualizacja ==="
cd "$APP_DIR"

# ── Funkcja: szukaj katalogu głównego git w górę drzewa ──────
find_git_root() {
    local dir="$1"
    while [ "$dir" != "/" ]; do
        [ -d "$dir/.git" ] && echo "$dir" && return 0
        dir="$(dirname "$dir")"
    done
    return 1
}

# ── Krok 1: pobierz nowe pliki ────────────────────────────────
GIT_ROOT=$(find_git_root "$APP_DIR" || true)

if [ -n "$GIT_ROOT" ]; then
    echo "[1/3] Repozytorium git znalezione w: $GIT_ROOT"
    echo "      Pobieranie zmian..."
    cd "$GIT_ROOT"
    git pull
    cd "$APP_DIR"
else
    echo "[1/3] Brak lokalnego repozytorium git."
    echo "      Pobieranie najnowszej wersji z GitHub..."

    TMP_DIR=$(mktemp -d)
    trap "rm -rf '$TMP_DIR'" EXIT

    git clone --depth 1 --quiet "$REPO_URL" "$TMP_DIR"

    # Zachowaj dane użytkownika przed nadpisaniem
    [ -f "$APP_DIR/.env" ] && cp "$APP_DIR/.env" "$TMP_DIR/.env.bak"
    # Baza danych jest w DATA_DIR — nie wymaga backup/restore podczas aktualizacji kodu

    # Skopiuj nowe pliki (rsync zachowuje strukturę katalogów)
    if command -v rsync &>/dev/null; then
        rsync -a --exclude='.env' --exclude='torrents.db' --exclude='venv/' \
            "$TMP_DIR/$REPO_SUBDIR/" "$APP_DIR/"
    else
        # Fallback bez rsync
        cp -r "$TMP_DIR/$REPO_SUBDIR/." "$APP_DIR/"
    fi

    # Przywróć .env
    [ -f "$TMP_DIR/.env.bak" ] && cp "$TMP_DIR/.env.bak" "$APP_DIR/.env"

    echo "      ✓ Pliki zaktualizowane"
fi

# ── Krok 2: zaktualizuj zależności Python ─────────────────────
echo "[2/3] Aktualizacja zależności Python..."
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
echo "      ✓ Zależności aktualne"

# ── Krok 3: restart usługi ────────────────────────────────────
echo "[3/3] Restart usługi ${SERVICE_NAME}..."
sudo systemctl restart "$SERVICE_NAME"
sleep 2
sudo systemctl status "$SERVICE_NAME" --no-pager

echo ""
echo "✓ Aktualizacja zakończona"
