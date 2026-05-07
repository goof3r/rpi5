#!/bin/bash
# =============================================================
#  RAID5 + Disk Monitor â€” Debian / OpenMediaVault
#  Uruchamiany przez cron lub systemd timer
#  WysyĹ‚a alert przez Telegram gdy coĹ› jest nie tak
# =============================================================

set -euo pipefail

# â”€â”€ Konfiguracja â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
CHAT_ID="${TELEGRAM_CHAT_ID:-}"
RAID_DEVICE="${RAID_DEVICE:-/dev/md0}"
LOG_FILE="/var/log/raid_monitor.log"
STATE_FILE="/var/lib/raid_monitor/last_state"
HOSTNAME_SHORT=$(hostname -s)

# â”€â”€ Kolory dla logu â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [$1] $2" | tee -a "$LOG_FILE"; }

send_telegram() {
    local msg="$1"
    if [[ -z "$BOT_TOKEN" || -z "$CHAT_ID" ]]; then
        log "WARN" "Brak TELEGRAM_BOT_TOKEN lub TELEGRAM_CHAT_ID â€” pomijam wysyĹ‚kÄ™"
        return 1
    fi
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${CHAT_ID}" \
        --data-urlencode "parse_mode=HTML" \
        --data-urlencode "text=${msg}" \
        --connect-timeout 10 \
        --max-time 20 > /dev/null 2>&1 && return 0 || return 1
}

alert() {
    local level="$1" msg="$2"
    local icon
    case "$level" in
        CRITICAL) icon="đź”´" ;;
        WARNING)  icon="đźźˇ" ;;
        OK)       icon="đźź˘" ;;
        *)        icon="â„ąď¸Ź"  ;;
    esac

    log "$level" "$msg"

    # SprawdĹş czy ten sam alert juĹĽ byĹ‚ wysĹ‚any (unikaj spamu)
    local state_key
    state_key=$(echo "${level}_${msg}" | md5sum | cut -d' ' -f1)
    mkdir -p "$(dirname "$STATE_FILE")"
    touch "$STATE_FILE"

    if grep -qF "$state_key" "$STATE_FILE" 2>/dev/null && [[ "$level" != "OK" ]]; then
        log "INFO" "Alert juĹĽ wysĹ‚any â€” pomijam duplikat"
        return 0
    fi

    # Zapisz stan â€” OK kasuje wpisy, error dodaje
    if [[ "$level" == "OK" ]]; then
        > "$STATE_FILE"
    else
        echo "$state_key" >> "$STATE_FILE"
    fi

    send_telegram "${icon} <b>[${HOSTNAME_SHORT}] ${level}</b>
${msg}
<i>$(date '+%Y-%m-%d %H:%M:%S')</i>"
}

# â”€â”€ Sprawdzanie dyskĂłw â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
check_disks() {
    local issues=()
    local ok=()

    # Parsuj MONITOR_DISKS jako tablicÄ™ (spacje jako separator)
    read -ra DISK_ARRAY <<< "${MONITOR_DISKS:-/dev/sda /dev/sdb /dev/sdc /dev/sdd}"

    for disk in "${DISK_ARRAY[@]}"; do
        if [[ ! -b "$disk" ]]; then
            issues+=("âťŚ Dysk <code>$disk</code> NIE JEST widoczny w systemie!")
            log "ERROR" "Dysk $disk nie istnieje jako urzÄ…dzenie blokowe"
            continue
        fi

        # SprawdĹş czy dysk odpowiada
        if ! lsblk "$disk" &>/dev/null; then
            issues+=("âš ď¸Ź Dysk <code>$disk</code> nie odpowiada na lsblk")
            continue
        fi

        ok+=("$disk")
        log "INFO" "Dysk $disk: OK"

        # SprawdĹş S.M.A.R.T. jeĹ›li smartctl dostÄ™pny
        if command -v smartctl &>/dev/null; then
            local smart_status
            smart_status=$(smartctl -H "$disk" 2>/dev/null | grep -i "overall-health\|result" || true)
            if echo "$smart_status" | grep -qi "FAILED\|BAD"; then
                issues+=("đź”´ SMART <code>$disk</code>: <b>FAILED</b> â€” dysk prawdopodobnie uszkodzony!")
            fi
        fi
    done

    if [[ ${#issues[@]} -gt 0 ]]; then
        local msg="<b>Problemy z dyskami:</b>\n"
        for i in "${issues[@]}"; do
            msg+="â€˘ ${i}\n"
        done
        [[ ${#ok[@]} -gt 0 ]] && msg+="\n<b>Sprawne:</b> $(IFS=', '; echo "${ok[*]}")"
        alert "CRITICAL" "$msg"
        return 1
    fi

    log "INFO" "Wszystkie dyski widoczne: $(IFS=' '; echo "${ok[*]}")"
    return 0
}

# â”€â”€ Sprawdzanie RAID â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
check_raid() {
    # SprawdĹş czy mdadm jest dostÄ™pny
    if ! command -v mdadm &>/dev/null; then
        alert "WARNING" "Polecenie <code>mdadm</code> nie znalezione â€” zainstaluj pakiet mdadm"
        return 1
    fi

    # ZnajdĹş urzÄ…dzenie RAID (md0 lub inne)
    local md_dev="$RAID_DEVICE"
    if [[ ! -b "$md_dev" ]]; then
        # SprĂłbuj odnaleĹşÄ‡ automatycznie
        md_dev=$(ls /dev/md[0-9]* 2>/dev/null | head -1 || true)
        if [[ -z "$md_dev" ]]; then
            alert "CRITICAL" "Nie znaleziono ĹĽadnego urzÄ…dzenia RAID (<code>/dev/md*</code>)!"
            return 1
        fi
        log "INFO" "UĹĽywam $md_dev zamiast $RAID_DEVICE"
    fi

    # Pobierz szczegĂłĹ‚y RAID
    local detail
    detail=$(mdadm --detail "$md_dev" 2>&1)
    local exit_code=$?

    if [[ $exit_code -ne 0 ]]; then
        alert "CRITICAL" "Nie moĹĽna pobraÄ‡ statusu RAID <code>$md_dev</code>:\n<code>${detail}</code>"
        return 1
    fi

    # Parsuj stan
    local state
    state=$(echo "$detail" | grep -i "State :" | awk -F': ' '{print $2}' | xargs || true)
    local active
    active=$(echo "$detail" | grep -i "Active Devices" | awk -F': ' '{print $2}' | xargs || true)
    local failed
    failed=$(echo "$detail" | grep -i "Failed Devices" | awk -F': ' '{print $2}' | xargs || true)
    local degraded
    degraded=$(echo "$detail" | grep -i "Degraded" | awk -F': ' '{print $2}' | xargs || true)
    local rebuild
    rebuild=$(echo "$detail" | grep -i "Rebuild Status\|Resync Status" | head -1 || true)

    log "INFO" "RAID $md_dev â€” stan: $state, aktywne: $active, uszkodzone: $failed"

    # Ocena stanu
    if echo "$state" | grep -qi "clean\|active$" && ! echo "$state" | grep -qi "degraded"; then
        if [[ "$failed" == "0" || -z "$failed" ]]; then
            log "INFO" "RAID $md_dev: SPRAWNY"
            return 0
        fi
    fi

    # Buduj wiadomoĹ›Ä‡ alertu
    local msg="<b>Problem z macierzÄ… RAID</b>\n"
    msg+="UrzÄ…dzenie: <code>$md_dev</code>\n"
    msg+="Stan: <code>$state</code>\n"
    [[ -n "$active"   ]] && msg+="Aktywne dyski: <code>$active</code>\n"
    [[ -n "$failed"   ]] && msg+="Uszkodzone: <code>$failed</code>\n"
    [[ -n "$rebuild"  ]] && msg+="Odbudowa: <code>$rebuild</code>\n"

    # PokaĹĽ listÄ™ dyskĂłw w macierzy
    local members
    members=$(echo "$detail" | grep -E "^\s+[0-9]+" | awk '{print $7, $6}' || true)
    [[ -n "$members" ]] && msg+="\n<b>Dyski:</b>\n<code>$members</code>"

    if echo "$state" | grep -qi "degraded\|failed\|inactive\|recovering"; then
        alert "CRITICAL" "$msg"
    else
        alert "WARNING" "$msg"
    fi

    return 1
}

# â”€â”€ Sprawdzanie /proc/mdstat â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
check_mdstat() {
    if [[ ! -f /proc/mdstat ]]; then
        log "INFO" "/proc/mdstat niedostÄ™pny â€” pomijam"
        return 0
    fi

    local mdstat
    mdstat=$(cat /proc/mdstat)

    # Szukaj oznak problemĂłw
    if echo "$mdstat" | grep -qi "\[.*_.*\]"; then
        local degraded_lines
        degraded_lines=$(echo "$mdstat" | grep "\[.*_.*\]" || true)
        alert "CRITICAL" "đź”´ <b>Wykryto zdegradowanÄ… macierz RAID</b> w /proc/mdstat:\n<code>$degraded_lines</code>"
        return 1
    fi

    log "INFO" "/proc/mdstat: brak oznak problemĂłw"
    return 0
}

# â”€â”€ Sprawdzanie przestrzeni dyskowej â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
check_disk_space() {
    local threshold="${DISK_SPACE_THRESHOLD:-90}"
    local issues=()

    while IFS= read -r line; do
        local use_pct mp
        use_pct=$(echo "$line" | awk '{print $5}' | tr -d '%')
        mp=$(echo "$line" | awk '{print $6}')
        [[ -z "$use_pct" || "$use_pct" == "Use%" ]] && continue

        if [[ "$use_pct" -ge "$threshold" ]]; then
            issues+=("đź’ľ <code>$mp</code>: ${use_pct}% zapeĹ‚nione")
        fi
    done < <(df -h | tail -n +2)

    if [[ ${#issues[@]} -gt 0 ]]; then
        local msg="<b>Niski poziom wolnego miejsca:</b>\n"
        for i in "${issues[@]}"; do msg+="â€˘ ${i}\n"; done
        alert "WARNING" "$msg"
        return 1
    fi

    return 0
}

# â”€â”€ GĹ‚Ăłwna pÄ™tla â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
main() {
    log "INFO" "=== Uruchomienie monitoringu ==="

    local overall_status=0

    check_disks      || overall_status=1
    check_raid       || overall_status=1
    check_mdstat     || overall_status=1
    check_disk_space || overall_status=1

    if [[ $overall_status -eq 0 ]]; then
        log "INFO" "=== Wszystko sprawne ==="
        # WyczyĹ›Ä‡ stary stan alertĂłw, wyĹ›lij 'OK' tylko gdy poprzednio byĹ‚ bĹ‚Ä…d
        if [[ -s "$STATE_FILE" ]]; then
            alert "OK" "âś… <b>Wszystkie systemy sprawne</b> â€” dyski i RAID dziaĹ‚ajÄ… poprawnie."
        fi
    else
        log "WARN" "=== Wykryto problemy! ==="
    fi
}

main "$@"
