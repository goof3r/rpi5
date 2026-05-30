#!/usr/bin/env bash
# Jednorazowa instalacja — tworzy venv, instaluje zależności
# i rejestruje aplikację jako usługę systemd.
# Uruchom: bash setup.sh
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="torrent-rss"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="$(whoami)"
DATA_DIR="/var/lib/torrent-rss"

echo "=== Torrent RSS Downloader — instalacja ==="
echo "Katalog: $APP_DIR"
echo "Użytkownik: $RUN_USER"
echo "Dane: $DATA_DIR"
echo ""

cd "$APP_DIR"

# ── Katalog danych (baza SQLite) ──────────────────────────────
echo "[0/4] Tworzenie katalogu danych: $DATA_DIR..."
sudo mkdir -p "$DATA_DIR"
sudo chown "${RUN_USER}:${RUN_USER}" "$DATA_DIR"
sudo chmod 750 "$DATA_DIR"
# Migracja istniejącej bazy z katalogu aplikacji
if [ -f "$APP_DIR/torrents.db" ] && [ ! -f "$DATA_DIR/torrents.db" ]; then
    cp "$APP_DIR/torrents.db" "$DATA_DIR/torrents.db"
    echo "      ✓ Przeniesiono istniejącą bazę danych do $DATA_DIR"
fi
echo "      ✓ Katalog danych gotowy"

# ── Środowisko wirtualne ──────────────────────────────────────
echo "[1/4] Tworzenie środowiska wirtualnego..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q
echo "      ✓ Zależności zainstalowane"

# ── Plik konfiguracyjny ───────────────────────────────────────
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[2/4] ✓ Stworzono plik .env"
    echo "      ➜  Otwórz go i uzupełnij RSS_FEED_URL oraz SECRET_KEY:"
    echo "         nano ${APP_DIR}/.env"
else
    echo "[2/4] ✓ Plik .env już istnieje — pomijam"
fi

# ── Usługa systemd ────────────────────────────────────────────
echo "[3/4] Tworzenie usługi systemd: ${SERVICE_NAME}..."

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Torrent RSS Downloader
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
Environment=DATA_DIR=${DATA_DIR}
ExecStart=${APP_DIR}/venv/bin/python3 app.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
echo "      ✓ Usługa zarejestrowana i włączona przy starcie"

# ── Uruchomienie ──────────────────────────────────────────────
echo "[4/4] Uruchamianie usługi..."
sudo systemctl start "$SERVICE_NAME"
sleep 2
sudo systemctl status "$SERVICE_NAME" --no-pager

echo ""
echo "════════════════════════════════════════════"
echo " Aplikacja działa pod adresem: http://$(hostname -I | awk '{print $1}'):5000"
echo "════════════════════════════════════════════"
echo ""
echo "Przydatne polecenia:"
echo "  sudo systemctl status  ${SERVICE_NAME}   — status"
echo "  sudo systemctl restart ${SERVICE_NAME}   — restart"
echo "  sudo systemctl stop    ${SERVICE_NAME}   — zatrzymaj"
echo "  journalctl -u ${SERVICE_NAME} -f         — logi na żywo"
