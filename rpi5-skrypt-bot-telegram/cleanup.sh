#!/bin/bash
# ============================================================
#  cleanup.sh — Usuwa stare pliki currency_monitor.sh
#  i przygotowuje miejsce pod połączony system
# ============================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

echo ""
echo "  ╔════════════════════════════════════════════╗"
echo "  ║   Cleanup — usuwanie starych plików        ║"
echo "  ╚════════════════════════════════════════════╝"
echo ""

# ── 1. Usuń stare crontaby currency_monitor ──────────────────
info "Sprawdzam crontab pod kątem currency_monitor…"
if crontab -l 2>/dev/null | grep -q "currency_monitor"; then
    crontab -l 2>/dev/null | grep -v "currency_monitor" | crontab -
    ok "Usunięto wpisy currency_monitor z crontab"
else
    info "Brak wpisów currency_monitor w crontab — pomijam"
fi

# ── 2. Znajdź i usuń pliki currency_monitor ──────────────────
info "Szukam plików currency_monitor.sh w typowych lokalizacjach…"

SEARCH_PATHS=(
    "/opt/currency-monitor"
    "/opt/server-monitor"
    "$HOME/currency"
    "$HOME/currency_monitor"
    "/usr/local/bin"
    "/home/*/currency*"
)

FOUND=0
for path in "${SEARCH_PATHS[@]}"; do
    for match in $path/currency_monitor.sh $path 2>/dev/null; do
        if [[ -f "$match" ]] && [[ "$match" == *currency_monitor.sh ]]; then
            warn "Znaleziono: $match"
            read -rp "  Usunąć? [t/N]: " ans
            if [[ "${ans,,}" == "t" ]]; then
                rm -f "$match"
                ok "Usunięto: $match"
                # Usuń też powiązane pliki jeśli są w tym samym katalogu
                DIR=$(dirname "$match")
                for extra in "$DIR/currency_config.conf" "$DIR/install.sh"; do
                    if [[ -f "$extra" ]]; then
                        read -rp "  Usunąć też $extra? [t/N]: " ans2
                        [[ "${ans2,,}" == "t" ]] && rm -f "$extra" && ok "Usunięto: $extra"
                    fi
                done
                # Usuń katalog jeśli pusty
                rmdir "$DIR" 2>/dev/null && ok "Usunięto pusty katalog: $DIR" || true
            fi
            FOUND=$((FOUND + 1))
        fi
    done
done

# Dodatkowe wyszukiwanie
while IFS= read -r found_file; do
    warn "Znaleziono przez find: $found_file"
    read -rp "  Usunąć? [t/N]: " ans
    if [[ "${ans,,}" == "t" ]]; then
        rm -f "$found_file"
        ok "Usunięto: $found_file"
    fi
    FOUND=$((FOUND + 1))
done < <(find / -name "currency_monitor.sh" -not -path "*/proc/*" 2>/dev/null || true)

[[ $FOUND -eq 0 ]] && info "Nie znaleziono żadnych plików currency_monitor.sh"

# ── 3. Przenieś dane kursów do wspólnego katalogu ────────────
info "Szukam starych danych CSV kursów walut…"

OLD_DATA_DIRS=(
    "$HOME/currency/data"
    "$HOME/currency_monitor/data"
    "/opt/currency-monitor/data"
)

NEW_DATA_DIR="/var/lib/currency-monitor"
mkdir -p "$NEW_DATA_DIR"

for old_dir in "${OLD_DATA_DIRS[@]}"; do
    if [[ -d "$old_dir" ]] && ls "$old_dir"/*.csv 2>/dev/null | head -1 &>/dev/null; then
        info "Znaleziono dane w: $old_dir"
        read -rp "  Przenieść dane CSV do $NEW_DATA_DIR? [t/N]: " ans
        if [[ "${ans,,}" == "t" ]]; then
            cp "$old_dir"/*.csv "$NEW_DATA_DIR/" 2>/dev/null && ok "Dane przeniesione" || warn "Nie udało się przenieść danych"
        fi
    fi
done

# ── 4. Zaktualizuj /etc/server-monitor.env jeśli istnieje ────
ENV_FILE="/etc/server-monitor.env"
if [[ -f "$ENV_FILE" ]]; then
    info "Znaleziono istniejący plik ENV: $ENV_FILE"
    if ! grep -q "CURRENCY_DATA_DIR" "$ENV_FILE"; then
        echo "" >> "$ENV_FILE"
        echo "# Katalog danych kursów walut (dodano przez cleanup.sh)" >> "$ENV_FILE"
        echo "CURRENCY_DATA_DIR=${NEW_DATA_DIR}" >> "$ENV_FILE"
        ok "Dodano CURRENCY_DATA_DIR do $ENV_FILE"
    else
        info "CURRENCY_DATA_DIR już istnieje w $ENV_FILE"
    fi
fi

echo ""
echo "  ╔════════════════════════════════════════════╗"
echo "  ║   Cleanup zakończony!                      ║"
echo "  ╚════════════════════════════════════════════╝"
echo ""
echo "  Następny krok: uruchom install.sh"
echo "  aby zainstalować połączony system."
echo ""
