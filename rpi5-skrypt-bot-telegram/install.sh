#!/bin/bash
# ============================================================
#  install.sh — Instalacja połączonego systemu
#  RAID Monitor + Telegram Bot + Kursy walut + Notatki
#  Debian 12 / OpenMediaVault 7
#  Uruchom jako root: sudo bash install.sh
# ============================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ "$EUID" -ne 0 ]] && die "Uruchom skrypt jako root (sudo bash install.sh)"

INSTALL_DIR="/opt/server-monitor"
ENV_FILE="/etc/server-monitor.env"
CURRENCY_DATA_DIR="/var/lib/currency-monitor"
NOTES_FILE="${CURRENCY_DATA_DIR}/notes.json"
BOT_SERVICE="telegram-bot.service"
MONITOR_SERVICE="server-monitor.timer"

echo ""
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   RAID5 + Telegram Bot + Kursy walut + Notatki   ║"
echo "  ║   Instalator — Debian / OpenMediaVault           ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo ""

# ── Krok 1: Dane konfiguracyjne ───────────────────────────────
echo -e "${CYAN}Konfiguracja:${NC}"
echo ""

if [[ -f "$ENV_FILE" ]]; then
    warn "Znaleziono istniejący plik konfiguracyjny: $ENV_FILE"
    read -rp "  Użyć istniejącej konfiguracji? [T/n]: " use_existing
    if [[ "${use_existing,,}" != "n" ]]; then
        source "$ENV_FILE"
        BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
        CHAT_ID="${TELEGRAM_CHAT_ID:-}"
        RAID_DEV="${RAID_DEVICE:-/dev/md0}"
        MONITOR_DISKS="${MONITOR_DISKS:-/dev/sda /dev/sdb /dev/sdc /dev/sdd}"
        DISK_THRESHOLD="${DISK_SPACE_THRESHOLD:-90}"
        CHECK_INTERVAL="${CHECK_INTERVAL:-5}"
        ok "Wczytano istniejącą konfigurację"
    fi
fi

if [[ -z "${BOT_TOKEN:-}" ]]; then
    read -rp "  Podaj TOKEN bota Telegram (od @BotFather): " BOT_TOKEN
    [[ -z "$BOT_TOKEN" ]] && die "Token nie może być pusty!"
fi
if [[ -z "${CHAT_ID:-}" ]]; then
    read -rp "  Podaj CHAT_ID grupy/kanału docelowego: " CHAT_ID
    [[ -z "$CHAT_ID" ]] && die "Chat ID nie może być pusty!"
fi

RAID_DEV="${RAID_DEV:-}"
[[ -z "$RAID_DEV" ]] && { read -rp "  Urządzenie RAID [/dev/md0]: " RAID_DEV; RAID_DEV="${RAID_DEV:-/dev/md0}"; }

MONITOR_DISKS="${MONITOR_DISKS:-}"
[[ -z "$MONITOR_DISKS" ]] && { read -rp "  Dyski [/dev/sda /dev/sdb /dev/sdc /dev/sdd]: " MONITOR_DISKS; MONITOR_DISKS="${MONITOR_DISKS:-/dev/sda /dev/sdb /dev/sdc /dev/sdd}"; }

DISK_THRESHOLD="${DISK_THRESHOLD:-}"
[[ -z "$DISK_THRESHOLD" ]] && { read -rp "  Próg zajętości dysku dla alertu [90]: " DISK_THRESHOLD; DISK_THRESHOLD="${DISK_THRESHOLD:-90}"; }

CHECK_INTERVAL="${CHECK_INTERVAL:-}"
[[ -z "$CHECK_INTERVAL" ]] && { read -rp "  Interwał monitorowania w minutach [5]: " CHECK_INTERVAL; CHECK_INTERVAL="${CHECK_INTERVAL:-5}"; }

SCRIPT_UPDATE_URL="${SCRIPT_UPDATE_URL:-}"
if [[ -z "$SCRIPT_UPDATE_URL" ]]; then
    echo ""
    echo -e "${CYAN}  URL do aktualizacji skryptu (opcjonalne):${NC}"
    echo "  Np. https://raw.githubusercontent.com/UZYTKOWNIK/REPO/main/rpi5-skrypt-bot-telegram/telegram_bot.py"
    read -rp "  URL [pomiń Enter]: " SCRIPT_UPDATE_URL
fi

# ── Krok 2: Zależności ────────────────────────────────────────
echo ""
info "Instaluję zależności systemowe…"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv mdadm smartmontools curl 2>/dev/null \
    || warn "Niektóre pakiety mogły się nie zainstalować"
ok "Zależności gotowe"

# ── Krok 3: Katalogi ──────────────────────────────────────────
info "Tworzę katalogi…"
mkdir -p "$INSTALL_DIR" "$CURRENCY_DATA_DIR" /var/lib/raid_monitor
touch /var/log/raid_monitor.log /var/log/telegram_bot.log
chmod 755 "$CURRENCY_DATA_DIR"

# Inicjalizacja pustego pliku notatek jeśli nie istnieje
if [[ ! -f "$NOTES_FILE" ]]; then
    echo "{}" > "$NOTES_FILE"
    chmod 644 "$NOTES_FILE"
    ok "Utworzono pusty plik notatek: $NOTES_FILE"
else
    info "Plik notatek już istnieje: $NOTES_FILE — zachowuję dane"
fi

# ── Krok 4: Python venv ───────────────────────────────────────
info "Tworzę środowisko Python…"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet "python-telegram-bot[job-queue]>=20.0"
ok "Środowisko Python gotowe (z job-queue)"

# ── Krok 5: Kopiuj skrypty ────────────────────────────────────
info "Kopiuję skrypty…"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for f in raid_monitor.sh telegram_bot.py; do
    [[ -f "$SCRIPT_DIR/$f" ]] || die "Brak pliku $f w $SCRIPT_DIR"
    cp "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f"
done

chmod +x "$INSTALL_DIR/raid_monitor.sh" "$INSTALL_DIR/telegram_bot.py"
ok "Skrypty skopiowane do $INSTALL_DIR"

# ── Krok 6: Plik ENV ──────────────────────────────────────────
info "Zapisuję konfigurację…"
cat > "$ENV_FILE" <<EOF
# Server Monitor + Currency Monitor + Notatki — konfiguracja
# Wygenerowano: $(date)

TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
TELEGRAM_CHAT_ID=${CHAT_ID}
RAID_DEVICE=${RAID_DEV}
MONITOR_DISKS=${MONITOR_DISKS}
DISK_SPACE_THRESHOLD=${DISK_THRESHOLD}
CHECK_INTERVAL=${CHECK_INTERVAL}
CURRENCY_DATA_DIR=${CURRENCY_DATA_DIR}
NOTES_FILE=${NOTES_FILE}
SCRIPT_UPDATE_URL=${SCRIPT_UPDATE_URL}
EOF
chmod 600 "$ENV_FILE"
ok "Konfiguracja zapisana w $ENV_FILE"

# ── Krok 7: Systemd — Telegram Bot ───────────────────────────
info "Tworzę usługę telegram-bot…"
cat > "/etc/systemd/system/${BOT_SERVICE}" <<EOF
[Unit]
Description=Telegram Bot — Server Monitor + Currency + Notatki
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/telegram_bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=telegram-bot

[Install]
WantedBy=multi-user.target
EOF

# ── Krok 8: Systemd — RAID Monitor ───────────────────────────
info "Tworzę usługę i timer raid-monitor…"
cat > "/etc/systemd/system/server-monitor.service" <<EOF
[Unit]
Description=RAID5 & Disk Monitor (jednorazowe)
After=network.target

[Service]
Type=oneshot
User=root
EnvironmentFile=${ENV_FILE}
ExecStart=${INSTALL_DIR}/raid_monitor.sh
StandardOutput=journal
StandardError=journal
SyslogIdentifier=raid-monitor
EOF

cat > "/etc/systemd/system/server-monitor.timer" <<EOF
[Unit]
Description=RAID5 & Disk Monitor — co ${CHECK_INTERVAL} minut
After=network.target

[Timer]
OnBootSec=2min
OnUnitActiveSec=${CHECK_INTERVAL}min
Unit=server-monitor.service
Persistent=true

[Install]
WantedBy=timers.target
EOF

# ── Krok 9: Uruchom ───────────────────────────────────────────
info "Ładuję i uruchamiam usługi…"
systemctl daemon-reload

systemctl enable --quiet "${BOT_SERVICE}"
systemctl restart "${BOT_SERVICE}"
sleep 2

if systemctl is-active --quiet "${BOT_SERVICE}"; then
    ok "Telegram bot działa ✓"
else
    warn "Bot nie uruchomił się — sprawdź: journalctl -u telegram-bot -n 50"
fi

systemctl enable --quiet "${MONITOR_SERVICE}"
systemctl start "${MONITOR_SERVICE}"
ok "Timer RAID monitoringu włączony (co ${CHECK_INTERVAL} min)"

# ── Krok 10: Test powiadomienia ───────────────────────────────
info "Wysyłam testowe powiadomienie…"
HOSTNAME_SHORT=$(hostname -s)
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d "chat_id=${CHAT_ID}" \
    -d "parse_mode=HTML" \
    -d "text=✅ <b>Instalacja zakończona!</b>%0ASerwer: <code>${HOSTNAME_SHORT}</code>%0A%0A<b>Dostępne komendy:</b>%0A/rpi5_help — pełna lista%0A/kurs — aktualny kurs USD i EUR%0A/rpi5_status — status serwera i RAID%0A/rpi5_notatki — zarządzanie notatkami" \
    --connect-timeout 10 --max-time 20 > /dev/null \
    && ok "Powiadomienie testowe wysłane!" \
    || warn "Nie udało się wysłać — sprawdź token i chat_id"

# ── Podsumowanie ──────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Instalacja zakończona pomyślnie!               ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Pliki:"
echo "    Skrypty:       $INSTALL_DIR/"
echo "    Konfiguracja:  $ENV_FILE"
echo "    Dane walutowe: $CURRENCY_DATA_DIR/"
echo "    Notatki:       $NOTES_FILE"
echo ""
echo "  Komendy Telegram:"
echo "    Serwer:  /rpi5_status /rpi5_disks /rpi5_raid /rpi5_df"
echo "             /rpi5_uptime /rpi5_logs /rpi5_reboot /rpi5_update /rpi5_help"
echo "    Waluty:  /kurs /kurs_historia /kurs_prognoza"
echo "    Notatki: /rpi5_notatki lista"
echo "             /rpi5_notatki dodaj <temat> <treść>"
echo "             /rpi5_notatki czytaj <temat>"
echo "             /rpi5_notatki edytuj <temat>"
echo "             /rpi5_notatki usun <temat>"
echo "             /rpi5_notatki szukaj <fraza>"
echo ""
echo "  Diagnostyka:"
echo "    journalctl -u telegram-bot -f"
echo "    journalctl -u server-monitor -n 30"
echo ""
