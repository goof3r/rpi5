#!/bin/bash
# ============================================================
#  install_omada.sh — Monitor access pointów Omada → Telegram
#  Dokłada do istniejącej instalacji bota (install.sh):
#    • /opt/server-monitor/omada_monitor.py
#    • ustawienia OMADA_* w /etc/server-monitor.env
#    • usługę systemd omada-monitor.service
#    • nowszy telegram_bot.py (komendy /omada…)
#  Uruchom jako root: sudo bash install_omada.sh
#  Można uruchamiać ponownie — zachowuje dotychczasowe ustawienia.
# ============================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ "$EUID" -ne 0 ]] && die "Uruchom skrypt jako root (sudo bash install_omada.sh)"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/server-monitor"
ENV_FILE="/etc/server-monitor.env"
DATA_DIR="/var/lib/omada-monitor"
SERVICE="omada-monitor.service"
BOT_SERVICE="telegram-bot.service"
PY="${INSTALL_DIR}/venv/bin/python3"

[[ -f "$ENV_FILE" && -x "$PY" ]] || die "Nie widzę instalacji bota ($ENV_FILE, $PY) — najpierw uruchom install.sh"
[[ -f "$SRC_DIR/omada_monitor.py" ]] || die "Brak omada_monitor.py w $SRC_DIR"

_get() { grep -m1 "^$1=" "$ENV_FILE" | cut -d'=' -f2- | tr -d '"' || true; }

echo ""
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   Monitor access pointów Omada → Telegram        ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo ""
echo -e "${CYAN}Przygotuj dostęp Open API w kontrolerze Omada:${NC}"
echo "  Ustawienia (Global) → Platform Integration → Open API → Add New App"
echo "    • Mode: Client         • Role: Viewer (tylko odczyt)"
echo "    • Site Privileges: wszystkie lokalizacje do pilnowania"
echo "  Po zapisaniu skopiuj Client ID i Client Secret."
echo ""

# ── Krok 1: Dane ─────────────────────────────────────────────
CUR_URL=$(_get OMADA_URL)
CUR_ID=$(_get OMADA_CLIENT_ID)
CUR_SECRET=$(_get OMADA_CLIENT_SECRET)
CUR_INTERVAL=$(_get OMADA_INTERVAL)
CUR_REMIND=$(_get OMADA_REMIND_MINUTES)
CUR_HOURS=$(_get OMADA_NOTIFY_HOURS)

while true; do
    read -rp "  Adres kontrolera, np. https://192.168.1.10:8043 [${CUR_URL}]: " OMADA_URL
    OMADA_URL="${OMADA_URL:-$CUR_URL}"
    OMADA_URL="${OMADA_URL%/}"
    [[ "$OMADA_URL" =~ ^https?:// ]] && break
    warn "Podaj adres z http:// lub https://"
done

read -rp "  Client ID [${CUR_ID:-brak}]: " OMADA_CLIENT_ID
OMADA_CLIENT_ID="${OMADA_CLIENT_ID:-$CUR_ID}"
[[ -z "$OMADA_CLIENT_ID" ]] && die "Client ID nie może być pusty"

if [[ -n "$CUR_SECRET" ]]; then
    read -rsp "  Client Secret [Enter = zostaw obecny]: " OMADA_CLIENT_SECRET; echo
else
    read -rsp "  Client Secret: " OMADA_CLIENT_SECRET; echo
fi
OMADA_CLIENT_SECRET="${OMADA_CLIENT_SECRET:-$CUR_SECRET}"
[[ -z "$OMADA_CLIENT_SECRET" ]] && die "Client Secret nie może być pusty"

read -rp "  Co ile sekund sprawdzać [${CUR_INTERVAL:-60}]: " OMADA_INTERVAL
OMADA_INTERVAL="${OMADA_INTERVAL:-${CUR_INTERVAL:-60}}"

read -rp "  Przypomnienia po (minuty) [${CUR_REMIND:-30,60,180}]: " OMADA_REMIND_MINUTES
OMADA_REMIND_MINUTES="${OMADA_REMIND_MINUTES:-${CUR_REMIND:-30,60,180}}"

read -rp "  Domyślne godziny powiadomień [${CUR_HOURS:-zawsze}]: " OMADA_NOTIFY_HOURS
OMADA_NOTIFY_HOURS="${OMADA_NOTIFY_HOURS:-${CUR_HOURS:-zawsze}}"

# ── Krok 2: Pliki ────────────────────────────────────────────
info "Kopiuję pliki…"
install -m 755 "$SRC_DIR/omada_monitor.py" "$INSTALL_DIR/omada_monitor.py"
if [[ -f "$SRC_DIR/telegram_bot.py" ]] && ! cmp -s "$SRC_DIR/telegram_bot.py" "$INSTALL_DIR/telegram_bot.py"; then
    cp "$INSTALL_DIR/telegram_bot.py" "$INSTALL_DIR/telegram_bot.py.bak"
    install -m 755 "$SRC_DIR/telegram_bot.py" "$INSTALL_DIR/telegram_bot.py"
    ok "Zaktualizowano telegram_bot.py (kopia: telegram_bot.py.bak)"
fi
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"
ok "Pliki w $INSTALL_DIR"

# ── Krok 3: Konfiguracja ─────────────────────────────────────
info "Zapisuję ustawienia w $ENV_FILE…"
TMP=$(mktemp)
grep -v -e '^OMADA_' -e '^# Omada AP Monitor' "$ENV_FILE" > "$TMP" || true
# usuń puste linie z końca pliku
sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$TMP"
cat >> "$TMP" <<EOF

# Omada AP Monitor
OMADA_URL=${OMADA_URL}
OMADA_CLIENT_ID=${OMADA_CLIENT_ID}
OMADA_CLIENT_SECRET=${OMADA_CLIENT_SECRET}
OMADA_VERIFY_SSL=0
OMADA_INTERVAL=${OMADA_INTERVAL}
OMADA_REMIND_MINUTES=${OMADA_REMIND_MINUTES}
OMADA_NOTIFY_HOURS=${OMADA_NOTIFY_HOURS}
OMADA_DEVICE_TYPES=ap
OMADA_DATA_DIR=${DATA_DIR}
EOF
cat "$TMP" > "$ENV_FILE"
rm -f "$TMP"
chmod 600 "$ENV_FILE"
ok "Ustawienia zapisane"

# ── Krok 4: Test połączenia ──────────────────────────────────
info "Sprawdzam połączenie z kontrolerem…"
echo ""
if "$PY" "$INSTALL_DIR/omada_monitor.py" list --env-file "$ENV_FILE"; then
    echo ""
    ok "Kontroler odpowiada"
else
    echo ""
    warn "Nie udało się pobrać listy AP (szczegóły wyżej)."
    read -rp "  Mimo to włączyć usługę? [t/N]: " ans
    [[ "${ans,,}" == "t" ]] || die "Przerwano. Popraw dane i uruchom instalator ponownie."
fi

# ── Krok 5: Usługa systemd ───────────────────────────────────
info "Tworzę usługę ${SERVICE}…"
cat > "/etc/systemd/system/${SERVICE}" <<EOF
[Unit]
Description=Omada AP Monitor — alerty Telegram o access pointach
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PY} ${INSTALL_DIR}/omada_monitor.py daemon
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=omada-monitor

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --quiet "$SERVICE"
systemctl restart "$SERVICE"
# bot musi wczytać nowe zmienne OMADA_* i komendy /omada
systemctl restart "$BOT_SERVICE" || warn "Nie udało się zrestartować ${BOT_SERVICE}"
sleep 3

if systemctl is-active --quiet "$SERVICE"; then
    ok "Monitor Omada działa ✓ (na Telegram przyjdzie wiadomość „Monitor Omada uruchomiony”)"
else
    warn "Usługa nie wystartowała — sprawdź: journalctl -u omada-monitor -n 50"
fi

echo ""
echo -e "${GREEN}Gotowe.${NC}"
echo ""
echo "  Komendy Telegram:"
echo "    /omada                 — podsumowanie lokalizacji (przyciski)"
echo "    /omada <lokalizacja>   — AP w danej lokalizacji"
echo "    /omada_wszystkie       — wszystkie AP"
echo "    /omada_powiadomienia   — godziny wysyłania, przypomnienia, pauza"
echo ""
echo "  Diagnostyka:"
echo "    journalctl -u omada-monitor -f"
echo "    sudo ${PY} ${INSTALL_DIR}/omada_monitor.py list"
echo ""
