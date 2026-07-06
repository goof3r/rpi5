#!/usr/bin/env bash
# install.sh — instaluje qlstatus (publiczny panel statusu QL na porcie 80).
# Wymaga: Python 3 stdlib (już jest w Ubuntu), sudo (do zapisu unit file i daemon-reload).
set -euo pipefail

PANEL_DIR="${PANEL_DIR:-$HOME/qlstatus}"
QLDS_DIR_DEFAULT="${QLDS_DIR:-$HOME/qlds}"
WHO="$(whoami)"
GRP="$(id -gn)"

c_i="\033[1;36m"; c_ok="\033[1;32m"; c_e="\033[0m"
log(){ echo -e "${c_i}[*]${c_e} $*"; }
ok(){ echo -e "${c_ok}[OK]${c_e} $*"; }

[ -d "$PANEL_DIR" ] || { echo "Brak $PANEL_DIR — wgraj zawartość status-panel/ przed instalacją"; exit 1; }
[ -f "$PANEL_DIR/app.py" ] || { echo "Brak $PANEL_DIR/app.py"; exit 1; }

log "Sprawdzam Pythona (wymagany 3.10+, używamy tylko stdlib)..."
PY="$(command -v python3)"
"$PY" --version
"$PY" -c "import http.server, socket, struct, json, zipfile" || { echo "Brak modułów stdlib"; exit 1; }

UNIT="/etc/systemd/system/qlstatus.service"
log "Rejestruję usługę systemd: $UNIT"
sudo tee "$UNIT" >/dev/null <<UEOF
[Unit]
Description=Quake Live public status panel (port 80, stdlib)
After=network.target

[Service]
Type=simple
User=${WHO}
Group=${GRP}
WorkingDirectory=${PANEL_DIR}
Environment=PORT=80
Environment=QLDS_DIR=${QLDS_DIR_DEFAULT}
ExecStart=${PY} ${PANEL_DIR}/app.py
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=${PANEL_DIR}/cache
PrivateTmp=true
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UEOF

sudo systemctl daemon-reload
sudo systemctl enable --now qlstatus.service
sleep 1
sudo systemctl --no-pager status qlstatus.service | head -12

IP="$(hostname -I | awk '{print $1}')"
ok "Gotowe: http://${IP}/  (oraz pod publicznym IP po przekierowaniu portu 80 na routerze)."
