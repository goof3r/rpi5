#!/usr/bin/env bash
###############################################################################
#  install_minqlx_server.sh
#  Automatyczna instalacja serwera Quake Live (QLDS) + minqlx + pluginy.
#
#  Co robi skrypt:
#    1. Instaluje zależności systemowe (Python3, redis, git, build-essential...)
#    2. Pobiera SteamCMD i instaluje QLDS (Steam app 349090, login anonymous)
#    3. Klonuje i KOMPILUJE minqlx ze źródeł (gotowych binarek już nie ma)
#    4. Kopiuje binarki minqlx do katalogu serwera
#    5. Klonuje pluginy MinoMino (oficjalne) oraz BarelyMiSSeD (dodatkowe)
#    6. Instaluje zależności pip pluginów
#    7. Generuje server.cfg, skrypt startowy oraz usługę systemd
#
#  Wymagania: Debian 10/11/12 lub Ubuntu 20.04/22.04/24.04 (system apt),
#             architektura x86_64, użytkownik z prawami sudo (NIE root).
#
#  Użycie:
#    1) Edytuj sekcję KONFIGURACJA poniżej (przede wszystkim QLX_OWNER!)
#    2) chmod +x install_minqlx_server.sh
#    3) ./install_minqlx_server.sh
#
#  Wszystkie zmienne można też nadpisać przez środowisko, np.:
#    QLX_OWNER=76561198799965164 NET_PORT=27960 ./install_minqlx_server.sh
###############################################################################

set -euo pipefail

# ───────────────────────────── KONFIGURACJA ─────────────────────────────────
# TWÓJ SteamID64 (17 cyfr) — WŁAŚCICIEL serwera. Bez tego nie zadziałają
# komendy admina. Konwerter: https://steamid.io  (pole steamID64)
: "${QLX_OWNER:=76561198799965164}"

# Nazwa serwera widoczna na liście
: "${SV_HOSTNAME:=^2My minqlx Server}"

# Port UDP serwera
: "${NET_PORT:=27960}"

# Hasła dla zdalnej konsoli (rcon) i statystyk (zmq) — ZMIEŃ na własne!
: "${RCON_PASSWORD:=zmien_to_haslo_rcon}"
: "${STATS_PASSWORD:=zmien_to_haslo_stats}"

# Katalogi instalacji (domyślnie w katalogu domowym użytkownika)
: "${STEAMCMD_DIR:=$HOME/steamcmd}"
: "${QLDS_DIR:=$HOME/qlds}"          # tu wyląduje serwer QL + minqlx
: "${BUILD_DIR:=$HOME/minqlx-build}" # tu klonujemy i kompilujemy źródła

# Czy zainstalować usługę systemd (autostart). 1 = tak, 0 = nie
: "${INSTALL_SYSTEMD:=1}"

# Czy zainstalować gotowe serwery trybów FFA/TDM/FT (z dołączonymi factory
# crobartie). 1 = tak. Gdy włączone, instalator NIE uruchamia generycznego
# qlserver (server.cfg) automatycznie — zamiast tego włącza qlserver-ffa/tdm/ft.
: "${INSTALL_GAMETYPE_SERVERS:=1}"

# Repozytoria (zwykle nie trzeba zmieniać)
MINQLX_REPO="https://github.com/MinoMino/minqlx.git"
PLUGINS_REPO="https://github.com/MinoMino/minqlx-plugins.git"
BARELY_REPO="https://github.com/BarelyMiSSeD/minqlx-plugins.git"
TJONE_REPO="https://github.com/tjone270/Quake-Live.git"   # pluginy w podkatalogu minqlx-plugins/
# Pojedyncze pluginy zewnętrzne używane przez przykładowe cfg trybów FFA/TDM/FT
# (nie ma ich w MinoMino/BarelyMiSSeD/tjone270 — pobierane bezpośrednio z repo autorów):
QUEUE_RAW="https://raw.githubusercontent.com/Melodeiro/minqlx-plugins_mattiZed/master/queue.py"
AUTOSPEC_RAW="https://raw.githubusercontent.com/dsverdlo/minqlx-plugins/master/autospec.py"
IOUONE_RAW="https://raw.githubusercontent.com/dsverdlo/minqlx-plugins/master/iouonegirl.py"  # klasa bazowa dla autospec
CHECKPLAYERS_RAW="https://raw.githubusercontent.com/x0rnn/minqlx-plugins/master/checkplayers.py"
# Repo TEGO instalatora (źródło załatanego commands.py, gdy instalator uruchamiany
# przez 'curl | bash' bez lokalnej kopii). Nadpiszesz np. forkując i ustawiając
# COMMANDS_PY_URL=...  w środowisku przed uruchomieniem.
: "${COMMANDS_PY_URL:=https://raw.githubusercontent.com/goof3r/quakelive-server/main/commands.py}"
# Plugin serverhelp (własny: !help / !version / !perms — patrz serverhelp.py w repo).
: "${SERVERHELP_PY_URL:=https://raw.githubusercontent.com/goof3r/quakelive-server/main/serverhelp.py}"
# Plugin permoverride (własny: cvar qlx_permFor_<komenda> + !permset/!permshow/!permlist/!permreload).
: "${PERMOVERRIDE_PY_URL:=https://raw.githubusercontent.com/goof3r/quakelive-server/main/permoverride.py}"
QLDS_APPID="349090"
STEAMCMD_URL="https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"

# Lista Steam Workshop ID-ków ładowanych na starcie serwera. Generuje:
#   1) $QLDS_DIR/workshop.txt (jeden ID na linię — łatwa edycja ręczna),
#   2) cvar 'qlx_workshopReferences' (comma-lista) w server.cfg i tdm/ffa/ft.cfg
#      — plugin 'workshop' (MinoMino) faktycznie czyta TĘ wartość, plik .txt to
#      tylko ludzka kopia listy.
WORKSHOP_IDS=(
  623144451 539421982 539421606 546664071 547252823 573808557 583820600
  573807159 584964611 564894881 575312620 586817666 584984610 565025333
  638618725 638531198 637351306 617896584 564946744 641499246 641587915
  637350852 641575854 643615147 675534589 679928531 679928822 568582691
  582665687 584815070 673213646 726131097 726132863 726133798 726134197
  663160788 774095795 803438741 824405313 827249184 827250713 827252336
  824405003 850146040 852034378
)
WORKSHOP_IDS_CSV="$( IFS=, ; echo "${WORKSHOP_IDS[*]}" )"
WORKSHOP_IDS_TXT="$(printf '%s\n' "${WORKSHOP_IDS[@]}")"
# ─────────────────────────────────────────────────────────────────────────────

# ── Pomocnicze ───────────────────────────────────────────────────────────────
c_ok="\033[1;32m"; c_warn="\033[1;33m"; c_err="\033[1;31m"; c_info="\033[1;36m"; c_end="\033[0m"
log()  { echo -e "${c_info}[*]${c_end} $*"; }
ok()   { echo -e "${c_ok}[OK]${c_end} $*"; }
warn() { echo -e "${c_warn}[!]${c_end} $*"; }
err()  { echo -e "${c_err}[X]${c_end} $*" >&2; }
die()  { err "$*"; exit 1; }

# Klonuje LUB aktualizuje (git pull) repo z pluginami i kopiuje pliki .py do
# katalogu pluginów serwera. Dzięki temu ta sama funkcja obsługuje pierwszą
# instalację oraz każdą późniejszą aktualizację (ponowne uruchomienie skryptu).
#   $1 = etykieta (do logów)
#   $2 = URL repo git
#   $3 = lokalny katalog klona (w BUILD_DIR)
#   $4 = podkatalog w repo z pluginami ("." = korzeń repo)
sync_plugin_repo() {
  local label="$1" url="$2" dir="$3" subdir="$4"
  log "Pobieram/aktualizuję pluginy: ${label}..."
  if [ -d "$dir/.git" ]; then
    git -C "$dir" pull --ff-only || warn "git pull ${label} nieudany — używam lokalnej kopii."
  else
    git clone "$url" "$dir" || { warn "Nie udało się sklonować ${label} — pomijam."; return 0; }
  fi
  local src="$dir/$subdir"
  if compgen -G "$src/*.py" >/dev/null 2>&1; then
    # Uwaga: jeśli dwa repozytoria mają plik o tej samej nazwie, wygrywa to
    # kopiowane później (kolejność wywołań w sekcji 5).
    cp -v "$src"/*.py "$QLDS_DIR/minqlx-plugins/" || warn "Kopiowanie .py z ${label} częściowo nieudane."
    ok "Pluginy ${label} skopiowane."
  else
    warn "Brak plików .py w ${src} (${label}) — nic nie skopiowano."
  fi
}

# ── Kontrole wstępne ─────────────────────────────────────────────────────────
[ "$(id -u)" -ne 0 ] || die "Nie uruchamiaj jako root. Użyj zwykłego użytkownika z sudo (SteamCMD i serwer NIE mogą działać jako root)."
command -v sudo  >/dev/null 2>&1 || die "Brak 'sudo'. Zainstaluj sudo i dodaj użytkownika do grupy sudo."
command -v apt-get >/dev/null 2>&1 || die "Skrypt obsługuje tylko systemy z apt (Debian/Ubuntu). Zobacz README dla innych systemów."
[ "$(uname -m)" = "x86_64" ] || warn "Wykryto architekturę $(uname -m). Serwer QL wymaga x86_64 — może nie zadziałać."

if ! [[ "$QLX_OWNER" =~ ^[0-9]{17}$ ]]; then
  warn "QLX_OWNER nie jest poprawnym SteamID64 (17 cyfr). Zainstaluję serwer, ale"
  warn "PAMIĘTAJ ustawić qlx_owner później (w start.sh), inaczej nie będziesz adminem."
fi

log "Katalog QLDS:  $QLDS_DIR"
log "Katalog build: $BUILD_DIR"
echo

# ── 1. Zależności systemowe ──────────────────────────────────────────────────
log "Instaluję zależności systemowe (apt)..."
sudo dpkg --add-architecture i386 || true
sudo apt-get update -y
sudo apt-get install -y \
  python3 python3-dev python3-pip \
  redis-server git build-essential make \
  wget curl ca-certificates tar locales \
  || die "Nie udało się zainstalować pakietów bazowych."

# Biblioteki 32-bit potrzebne SteamCMD (nazwy różnią się między wersjami)
sudo apt-get install -y lib32gcc-s1     || sudo apt-get install -y lib32gcc1 || warn "Nie zainstalowano lib32gcc — SteamCMD może protestować."
sudo apt-get install -y lib32stdc++6    || warn "Nie zainstalowano lib32stdc++6."
ok "Zależności gotowe."

PYVER="$(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:2])))')"
log "Wersja Pythona: $PYVER"

# ── 2. Redis ─────────────────────────────────────────────────────────────────
log "Uruchamiam i włączam usługę redis-server..."
sudo systemctl enable --now redis-server 2>/dev/null \
  || sudo systemctl enable --now redis 2>/dev/null \
  || warn "Nie udało się włączyć redis przez systemd — sprawdź ręcznie."
ok "Redis skonfigurowany (domyślnie 127.0.0.1:6379)."

# ── 3. SteamCMD + QLDS ───────────────────────────────────────────────────────
log "Instaluję SteamCMD..."
mkdir -p "$STEAMCMD_DIR"
if [ ! -f "$STEAMCMD_DIR/steamcmd.sh" ]; then
  ( cd "$STEAMCMD_DIR" && curl -sqL "$STEAMCMD_URL" | tar zxvf - ) \
    || die "Nie udało się pobrać/rozpakować SteamCMD."
fi
ok "SteamCMD gotowy."

log "Pobieram / aktualizuję Quake Live Dedicated Server (app $QLDS_APPID)..."
mkdir -p "$QLDS_DIR"
"$STEAMCMD_DIR/steamcmd.sh" \
  +force_install_dir "$QLDS_DIR" \
  +login anonymous \
  +app_update "$QLDS_APPID" validate \
  +quit \
  || die "SteamCMD nie zainstalował QLDS. Uruchom ponownie skrypt (czasem trzeba 2x)."
[ -f "$QLDS_DIR/run_server_x64.sh" ] || die "Brak run_server_x64.sh — instalacja QLDS niepełna."
ok "QLDS zainstalowany w $QLDS_DIR."

# ── 4. Kompilacja minqlx ─────────────────────────────────────────────────────
log "Klonuję i kompiluję minqlx ze źródeł..."
mkdir -p "$BUILD_DIR"
if [ -d "$BUILD_DIR/minqlx/.git" ]; then
  git -C "$BUILD_DIR/minqlx" pull --ff-only || warn "git pull minqlx nieudany, kompiluję istniejącą wersję."
else
  git clone "$MINQLX_REPO" "$BUILD_DIR/minqlx"
fi
( cd "$BUILD_DIR/minqlx" && make clean >/dev/null 2>&1 || true; make ) \
  || die "Kompilacja minqlx nie powiodła się. Najczęstsza przyczyna: brak python3-dev lub bardzo nowy Python. Zobacz README (sekcja Docker / starszy Python)."
[ -f "$BUILD_DIR/minqlx/bin/minqlx.x64.so" ] || die "Nie powstał plik bin/minqlx.x64.so — kompilacja niepełna."
ok "minqlx skompilowany."

log "Kopiuję binarki minqlx do katalogu serwera..."
cp -rv "$BUILD_DIR/minqlx/bin/." "$QLDS_DIR/"
chmod +x "$QLDS_DIR"/run_server_x64_minqlx.sh 2>/dev/null || true
[ -f "$QLDS_DIR/run_server_x64_minqlx.sh" ] || die "Brak run_server_x64_minqlx.sh po kopiowaniu."
ok "Binarki minqlx na miejscu."

# ── 5. Pluginy ───────────────────────────────────────────────────────────────
log "Klonuję oficjalne pluginy MinoMino..."
if [ -d "$QLDS_DIR/minqlx-plugins/.git" ]; then
  git -C "$QLDS_DIR/minqlx-plugins" pull --ff-only || warn "git pull pluginów nieudany."
else
  git clone "$PLUGINS_REPO" "$QLDS_DIR/minqlx-plugins"
fi
ok "Pluginy MinoMino gotowe."

log "Dodaję pluginy BarelyMiSSeD (specqueue, serverBDM, protect, kills itd.)..."
sync_plugin_repo "BarelyMiSSeD" "$BARELY_REPO" "$BUILD_DIR/barely" "."
# Dodatkowy folder z nazwami map (używany przez listmaps):
[ -d "$BUILD_DIR/barely/Map_Names" ] && cp -rv "$BUILD_DIR/barely/Map_Names" "$QLDS_DIR/minqlx-plugins/" || true

log "Dodaję pluginy tjone270 (q3resolver, branding, botmanager, quiet itd.)..."
# W tym repo pluginy leżą w PODKATALOGU minqlx-plugins/, dlatego 4. argument:
sync_plugin_repo "tjone270" "$TJONE_REPO" "$BUILD_DIR/tjone270" "minqlx-plugins"

# commlink (most IRC) jest zbędny i niezgodny z Pythonem 3.11+ — USUWAMY plik
# całkowicie z instalacji (także przy aktualizacji, po ponownym skopiowaniu).
rm -f "$QLDS_DIR/minqlx-plugins/commlink.py"
ok "Plugin commlink usunięty z instalacji (zbędny most IRC)."

# changemap (tjone270) automatycznie zmienia mapę na DOMYŚLNĄ, gdy serwer się
# opróżnia (hook player_disconnect przy <=1 graczu). Efekt uboczny: po reconnect
# mapa/gametyp wracają do qlx_defaultMapToChangeTo/...Factory (domyślnie
# campgrounds + ffa). USUWAMY go całkowicie (także przy aktualizacji).
rm -f "$QLDS_DIR/minqlx-plugins/changemap.py"
ok "Plugin changemap usunięty z instalacji (auto-reset mapy na pustym serwerze)."

# Wgrywamy ZAŁATANĄ wersję pluginu 'commands' (komendy !lc / !plugins).
# Oryginał z BarelyMiSSeD wysyłał osobny komunikat player.tell() na KAŻDY plugin —
# przy pełnej liście pluginów potrafiło to wypchnąć kilkadziesiąt komend 'reliable'
# w jednej klatce, przepełnić bufor silnika QL i wywalić CAŁY proces serwera
# (systemd podnosił go z Restart=on-failure -> wyglądało jak 'reset'). Ta wersja
# buduje całą listę, pakuje ją w kilka komunikatów <=900 znaków i rozkłada na
# kolejne klatki.
#
# Źródło pliku:
#   1) jeśli instalator uruchomiony z klona repo (commands.py LEŻY OBOK skryptu) —
#      kopiujemy lokalny (szybciej, działa offline po sklonowaniu),
#   2) inaczej (np. instalacja przez 'curl | bash') — pobieramy z GitHub raw URL
#      (COMMANDS_PY_URL) jednym żądaniem.
SCRIPT_DIR="$( (cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || true )"
CONFIGS_DIR=""
[ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/configs and mappool" ] && CONFIGS_DIR="$SCRIPT_DIR/configs and mappool"
SRC_COMMANDS=""
[ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/commands.py" ] && SRC_COMMANDS="$SCRIPT_DIR/commands.py"
DST_COMMANDS="$QLDS_DIR/minqlx-plugins/commands.py"
if [ -n "$SRC_COMMANDS" ]; then
  log "Wgrywam załataną wersję pluginu commands (!lc) z lokalnego $SRC_COMMANDS..."
  cp -f "$SRC_COMMANDS" "$DST_COMMANDS"
  ok "Plugin commands (załatany) wgrany z $SRC_COMMANDS."
else
  log "Pobieram załataną wersję pluginu commands (!lc) z $COMMANDS_PY_URL..."
  if curl -fsSL "$COMMANDS_PY_URL" -o "$DST_COMMANDS" && [ -s "$DST_COMMANDS" ]; then
    # Lekka walidacja: ma być plik Pythona z klasą 'commands'.
    if grep -qE '^class[[:space:]]+commands' "$DST_COMMANDS"; then
      ok "Plugin commands (załatany) pobrany z GitHub."
    else
      warn "Pobrany commands.py wygląda na uszkodzony (brak 'class commands') — pozostawiam wersję z repo BarelyMiSSeD."
      cp -f "$BUILD_DIR/barely/commands.py" "$DST_COMMANDS" 2>/dev/null || true
    fi
  else
    warn "Nie udało się pobrać commands.py z $COMMANDS_PY_URL — pozostaje wersja z repo BarelyMiSSeD"
    warn "(może wywalać serwer przy !lc z pełną listą pluginów)."
  fi
fi

# ── 5a-bis. Plugin serverhelp (własny: !help / !version / !perms) ─────────────
# Przejmuje !help (lista WSZYSTKICH komend, jedna pod drugą) oraz !version
# (zwraca wersję minqlx) — robi to przez priority=PRI_HIGH + RET_STOP_ALL,
# więc handler cmd_help z essentials nie zostanie wywołany dla tych aliasów.
# Dodaje też !perms (poziomy 0..5 + bieżący poziom gracza).
#
# Źródło pliku: tak samo jak commands.py — lokalne obok skryptu albo curl.
SRC_SERVERHELP=""
[ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/serverhelp.py" ] && SRC_SERVERHELP="$SCRIPT_DIR/serverhelp.py"
DST_SERVERHELP="$QLDS_DIR/minqlx-plugins/serverhelp.py"
if [ -n "$SRC_SERVERHELP" ]; then
  log "Wgrywam plugin serverhelp (!help/!version/!perms) z lokalnego $SRC_SERVERHELP..."
  cp -f "$SRC_SERVERHELP" "$DST_SERVERHELP"
  ok "Plugin serverhelp wgrany z $SRC_SERVERHELP."
else
  log "Pobieram plugin serverhelp z $SERVERHELP_PY_URL..."
  if curl -fsSL "$SERVERHELP_PY_URL" -o "$DST_SERVERHELP" && [ -s "$DST_SERVERHELP" ]; then
    if grep -qE '^class[[:space:]]+serverhelp' "$DST_SERVERHELP"; then
      ok "Plugin serverhelp pobrany z GitHub."
    else
      warn "Pobrany serverhelp.py wygląda na uszkodzony (brak 'class serverhelp') — usuwam."
      rm -f "$DST_SERVERHELP"
    fi
  else
    warn "Nie udało się pobrać serverhelp.py z $SERVERHELP_PY_URL — !help pozostanie domyślne."
  fi
fi

# ── 5a-ter. Plugin permoverride (nadpisywanie qlx perm dla komend cvarami) ────
# Czyta `qlx_permFor_<komenda>` z server.cfg i podmienia .permission na
# obiektach minqlx.Command po starcie. Dodaje !permset/!permshow/!permlist/
# !permreload. Powinien być ZA innymi pluginami w qlx_plugins — installer
# ustawia go na samym końcu listy automatycznie.
SRC_PERMOVERRIDE=""
[ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/permoverride.py" ] && SRC_PERMOVERRIDE="$SCRIPT_DIR/permoverride.py"
DST_PERMOVERRIDE="$QLDS_DIR/minqlx-plugins/permoverride.py"
if [ -n "$SRC_PERMOVERRIDE" ]; then
  log "Wgrywam plugin permoverride z lokalnego $SRC_PERMOVERRIDE..."
  cp -f "$SRC_PERMOVERRIDE" "$DST_PERMOVERRIDE"
  ok "Plugin permoverride wgrany z $SRC_PERMOVERRIDE."
else
  log "Pobieram plugin permoverride z $PERMOVERRIDE_PY_URL..."
  if curl -fsSL "$PERMOVERRIDE_PY_URL" -o "$DST_PERMOVERRIDE" && [ -s "$DST_PERMOVERRIDE" ]; then
    if grep -qE '^class[[:space:]]+permoverride' "$DST_PERMOVERRIDE"; then
      ok "Plugin permoverride pobrany z GitHub."
    else
      warn "Pobrany permoverride.py wygląda na uszkodzony (brak 'class permoverride') — usuwam."
      rm -f "$DST_PERMOVERRIDE"
    fi
  else
    warn "Nie udało się pobrać permoverride.py z $PERMOVERRIDE_PY_URL — override'y cvarami nie zadziałają."
  fi
fi

ok "Pluginy dodatkowe skopiowane (włączysz wybrane w server.cfg → qlx_plugins)."

# ── 5b. Pluginy zewnętrzne używane przez cfg trybów FFA/TDM/FT ────────────────
# Te pluginy NIE występują w repo MinoMino/BarelyMiSSeD/tjone270 — pobieramy je
# pojedynczo z repozytoriów ich autorów (świeża wersja przy każdym uruchomieniu):
#   queue        (mattiZed/Melodeiro) — kolejka graczy do gry
#   autospec     (dsverdlo)           — auto-spec przy nierównych drużynach;
#                                        wymaga klasy bazowej iouonegirl.py + pip 'requests'
#   checkplayers (x0rnn)              — !checkplayers: lista perm/ban/silence/leaver
# UWAGA: są tylko POBIERANE (dostępne), ale NIE włączone w domyślnym server.cfg.
# Włączają je dopiero konfiguracje trybów (qlx_plugins w ffa/tdm/ft.cfg).
# Pluginów 'patch' i 'specvote' z tamtych cfg NIE pobieramy — to bespoke pluginy
# konkretnego serwera (twarde, obce ustawienia), bezużyteczne na innym serwerze.
log "Pobieram zewnętrzne pluginy (queue, autospec, checkplayers)..."
fetch_plugin() {  # $1=URL  $2=docelowa_nazwa_pliku
  if curl -sfqL "$1" -o "$QLDS_DIR/minqlx-plugins/$2" && [ -s "$QLDS_DIR/minqlx-plugins/$2" ]; then
    ok "  pobrano: $2"
  else
    warn "  nie udało się pobrać $2 (z $1) — plugin pominięty."
  fi
}
fetch_plugin "$QUEUE_RAW"        "queue.py"
fetch_plugin "$AUTOSPEC_RAW"     "autospec.py"
fetch_plugin "$IOUONE_RAW"       "iouonegirl.py"   # klasa bazowa wymagana przez autospec
fetch_plugin "$CHECKPLAYERS_RAW" "checkplayers.py"

# ── 5c. Lokalne pluginy z katalogu minqlx-plugins/ (nadpisują wersje z repo) ─
# Jeśli obok skryptu instalatora istnieje katalog minqlx-plugins/ (lokalny klon
# repo z nowszymi wersjami), jego zawartość jest kopiowana jako ostatnia —
# nadpisuje wszystko pobrane wcześniej z MinoMino/BarelyMiSSeD/tjone270 i przez
# fetch_plugin. Lokalne wersje zawsze wygrywają.
PLUGINS_LOCAL_DIR=""
[ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/minqlx-plugins" ] && PLUGINS_LOCAL_DIR="$SCRIPT_DIR/minqlx-plugins"
if [ -n "$PLUGINS_LOCAL_DIR" ]; then
  log "Kopiuję lokalne pluginy z $PLUGINS_LOCAL_DIR (nadpisują wersje z repo)..."
  if compgen -G "$PLUGINS_LOCAL_DIR/*.py" >/dev/null 2>&1; then
    cp -v "$PLUGINS_LOCAL_DIR"/*.py "$QLDS_DIR/minqlx-plugins/" \
      || warn "Kopiowanie lokalnych pluginów częściowo nieudane."
  fi
  [ -f "$PLUGINS_LOCAL_DIR/requirements.txt" ] && \
    cp -f "$PLUGINS_LOCAL_DIR/requirements.txt" "$QLDS_DIR/minqlx-plugins/requirements.txt"
  [ -f "$PLUGINS_LOCAL_DIR/mbot_maps.json" ] && \
    cp -f "$PLUGINS_LOCAL_DIR/mbot_maps.json" "$QLDS_DIR/minqlx-plugins/mbot_maps.json"
  [ -d "$PLUGINS_LOCAL_DIR/Map_Names" ] && \
    cp -rv "$PLUGINS_LOCAL_DIR/Map_Names" "$QLDS_DIR/minqlx-plugins/"
  [ -d "$PLUGINS_LOCAL_DIR/extras" ] && \
    cp -rv "$PLUGINS_LOCAL_DIR/extras" "$QLDS_DIR/minqlx-plugins/"
  ok "Lokalne pluginy skopiowane z $PLUGINS_LOCAL_DIR."
else
  log "Brak lokalnego katalogu minqlx-plugins/ obok skryptu — używam wersji z repo."
fi

# ── 6. Zależności pip pluginów ───────────────────────────────────────────────
log "Instaluję zależności Pythona dla pluginów (pip)..."
if [ -f "$QLDS_DIR/minqlx-plugins/requirements.txt" ]; then
  sudo -H env PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install \
       -r "$QLDS_DIR/minqlx-plugins/requirements.txt" \
    || warn "pip zgłosił błędy — sprawdź wyżej. Część ostrzeżeń jest nieszkodliwa."
else
  warn "Brak requirements.txt w pluginach — pomijam pip."
fi

# Dodatkowe zależności wymagane przez pluginy spoza MinoMino.
# UWAGA: przy starcie minqlx jeden plugin z brakującą zależnością przerywa
# ładowanie WSZYSTKICH kolejnych pluginów z listy — dlatego instalujemy je z góry.
#   schedule -> wymagane przez autorestart (tjone270)
#   requests -> wymagane przez autospec (dsverdlo) — import na sztywno na górze pliku
EXTRA_PIP="schedule requests"
log "Instaluję dodatkowe zależności pluginów: ${EXTRA_PIP}..."
sudo -H env PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install $EXTRA_PIP \
  || warn "Nie udało się zainstalować: ${EXTRA_PIP} — pluginy ich wymagające nie wczytają się."
ok "Zależności pip zainstalowane."

# ── 7. server.cfg ────────────────────────────────────────────────────────────
CFG="$QLDS_DIR/baseq3/server.cfg"
mkdir -p "$QLDS_DIR/baseq3"

# Pełna lista pluginów. Wstrzykiwana do server.cfg, a przy aktualizacji
# synchronizowana również w już istniejącym server.cfg (patrz niżej).
QLX_PLUGINS_LIST="plugin_manager, essentials, motd, permission, ban, silence, clan, names, log, workshop, aliases, autorestart, botmanager, branding, custom_votes, dictionary, disabled_commands, ips, onjoin, permaban, permissionlist, q3resolver, quiet, ratinglimiter, sv_fps, thirtysecwarn, votemanager, votestats, commands, serverhelp, permoverride"

# Lista pluginów dla serwerów trybów (FFA/TDM/FT). Wzięta z dołączonych cfg-ów,
# ale OCZYSZCZONA: usunięte 'irc' (na życzenie) oraz 'patch' i 'specvote' (bespoke
# pluginy obcego serwera — nie istnieją w żadnym repo, blokowałyby ładowanie).
GT_PLUGINS_LIST="plugin_manager, essentials, motd, permission, ban, clan, names, silence, log, balance, branding, workshop, queue, autospec, checkplayers, votestats, ips, aliases, botmanager, onjoin, serverhelp, permoverride"

if [ -f "$CFG" ]; then
  warn "server.cfg już istnieje — nie nadpisuję go w całości. Wzór: server.cfg.example."
  CFG_OUT="$QLDS_DIR/baseq3/server.cfg.example"
else
  CFG_OUT="$CFG"
fi
cat > "$CFG_OUT" <<'CFGEOF'
// ===========================================================================
//  server.cfg — konfiguracja serwera Quake Live + minqlx
//  Cvary minqlx mają prefiks qlx_. Pełna lista komend:
//  https://github.com/MinoMino/minqlx/wiki/Command-List
// ===========================================================================

// --- Podstawy QL ---
set sv_hostname            "^2My minqlx Server"
set g_motd                 "Powered by minqlx"
set sv_maxclients          "16"
set teamsize               "4"
set g_inactivity           "0"
set sv_allowDownload       "1"
set g_allowVote            "1"

// --- minqlx: rdzeń ---
// qlx_owner ustawiany jest w start.sh (z wartości QLX_OWNER). Możesz też tu:
// set qlx_owner            "76561198799965164"

set qlx_commandPrefix      "!"

// Lista wczytywanych pluginów (kolejność ma znaczenie).
// Zawiera domyślne pluginy MinoMino + WSZYSTKIE pluginy tjone270 (włączone).
// Jeśli kiedykolwiek nazwa pliku tjone270 pokryje się z pluginem MinoMino,
// instalator kopiuje wersję tjone270 jako ostatnią (ona wygrywa = podmiana).
set qlx_plugins            "__QLX_PLUGINS__"

// Pluginy tjone270 wczytywane powyżej (kolejność na liście = kolejność ładowania):
//   aliases          - aliasy komend
//   autorestart      - automatyczny restart serwera
//   botmanager       - automatyczne dodawanie/usuwanie botów (bot_autoManage)
//   branding         - personalizacja serwera (qlx_serverBrandName itd.)
//   custom_votes     - własne typy callvote
//   dictionary       - słownik/tłumaczenia komend
//   disabled_commands- wyłącza wskazane komendy (sprawdź ustawienia w pliku!)
//   ips              - !ip <id> — historia adresów IP gracza (z Redis)
//   onjoin           - !onjoin <wiadomość> — wiadomość powitalna gracza
//   permaban         - bany trwałe (pokrywa się funkcją z 'ban')
//   permissionlist   - !permissionlist — lista graczy z uprawnieniami > 0
//   q3resolver       - głosowanie nazwami map z Quake 3 (np. /cv map q3dm12)
//   quiet            - blokada czatu w trakcie meczu (qlx_permitChatDuringWarmup)
//   ratinglimiter    - limit dołączania wg ratingu/ELO
//   sv_fps           - !svfps <int> — zmiana sv_fps na żywo (qlx_svfps, dom. 40)
//   thirtysecwarn    - dźwięk VO przy zbliżającym się limicie czasu rundy
//   votemanager      - zarządzanie głosowaniami / force-vote (permlevel 3)
//   votestats        - !votes — odanonimizowanie i statystyki głosowań
//
// (plugin 'commlink' / most IRC został celowo USUNIĘTY z instalacji —
//  zbędny i niezgodny z Pythonem 3.11+.)
// (plugin 'changemap' został celowo USUNIĘTY — automatycznie resetował mapę na
//  pustym serwerze, m.in. po reconnect. Jeśli go potrzebujesz, sklonuj z repo
//  tjone270 i ustaw cvary qlx_defaultMapToChangeTo / qlx_defaultMapFactoryToChangeTo.)
//
// --- Plugin 'commands' (BarelyMiSSeD) — WŁĄCZONY domyślnie, wersja ZAŁATANA ---
//   commands    - !plugins / !lc — lista załadowanych pluginów i komend.
//                 Instalator wgrywa poprawioną wersję (oryginał wywalał serwer
//                 przy !lc z powodu przepełnienia bufora komend). Nie chcesz tej
//                 komendy? Usuń 'commands' z qlx_plugins powyżej.
//
// --- Pluginy DODATKOWE (BarelyMiSSeD) — opcjonalne, NIE włączone ---
// Aby włączyć, DOPISZ nazwę pliku (bez .py) do qlx_plugins powyżej.
// Dostępne (po skopiowaniu przez instalator):
//   specqueue   - kolejka graczy / wyrównywanie drużyn
//   serverBDM   - rating BDM + auto-balans (UWAGA: nadpisuje !balance/!teams)
//   protect     - ochrona graczy, !forcets, vote mute/afk
//   kills       - statystyki specjalnych fragów (gauntlet, air rocket itd.)
//   listmaps    - !listmaps — lista map na serwerze
//   maplimiter  - ograniczanie map do głosowania
//   votelimiter - limit i whitelist głosowań
//   voteban     - ban gracza od głosowania
//   handicap    - auto-handicap wg ELO
//   inviteonly  - serwer tylko dla zaproszonych
//   clanmembers - zarządzanie tagami klanowymi
//   specall     - !specall — wszyscy na spec
//   voicechat   - przełączanie global/team voice
//   bots        - utrzymuje boty (wymaga specqueue)
//   battleroyale- tryb last-man-standing (NIEzgodny z innymi kolejkami)
//   wipeout     - tryb Wipeout na bazie Clan Arena

// --- Pluginy ZEWNĘTRZNE (pobierane przez instalator z repo innych autorów) ---
// Dostępne, ale NIE włączone tutaj — używają ich konfiguracje trybów FFA/TDM/FT:
//   queue        - kolejka graczy do gry (mattiZed/Melodeiro)
//   autospec     - auto-spec przy nierównych drużynach (dsverdlo; wymaga requests)
//   checkplayers - !checkplayers: gracze z perm/ban/silence/leaver (x0rnn)
// (UWAGA: 'balance' (MinoMino) + 'autospec' + 'queue' to trzy nakładające się
//  systemy zarządzania drużynami/kolejką — włączaj świadomie, mogą sobie wchodzić
//  w drogę. 'patch' i 'specvote' z obcych cfg celowo POMINIĘTE — bespoke, obce.)

// --- minqlx: baza danych (Redis) ---
set qlx_database           "Redis"
set qlx_redisAddress       "127.0.0.1"
set qlx_redisDatabase      "0"
set qlx_redisUnixSocket    "0"
// set qlx_redisPassword   ""

// --- Steam Workshop (plugin 'workshop' z MinoMino) ---
// Lista ID-ków przedmiotów Workshop ładowanych do serwera (mapy, modele itd.).
// Ludzką wersję tej listy trzymasz w pliku $QLDS_DIR/workshop.txt — instalator
// generuje OBA, ale ŹRÓDŁEM PRAWDY DLA SERWERA jest CVAR poniżej (plugin czyta
// cvar, nie plik). Po edycji workshop.txt zsynchronizuj ten cvar ręcznie albo
// uruchom instalator ponownie.
set qlx_workshopReferences "__QLX_WORKSHOP__"

// --- minqlx: logi ---
set qlx_logs               "5"
set qlx_logsSize           "5000000"

// --- Pula map (przykład) ---
// set sv_mapPoolFile      "mappool.txt"

// Pierwsza mapa po starcie. WAŻNE: podaj też factory (gametyp)!
// Samo "map campgrounds" bez factory powoduje, że QLDS wypisuje tylko składnię
// "map (map) (factory)" i serwer NIE wstaje. Dostępne factory:
//   ffa duel ca ctf tdm ft dom ad oneflag har race rr infected quadhog actf ictf iffa ift vca
map campgrounds ffa
CFGEOF
# Wstrzykujemy aktualną listę pluginów oraz listę workshop w wygenerowany plik:
sed -i "s|__QLX_PLUGINS__|${QLX_PLUGINS_LIST}|" "$CFG_OUT"
sed -i "s|__QLX_WORKSHOP__|${WORKSHOP_IDS_CSV}|" "$CFG_OUT"
ok "Zapisano konfigurację: $CFG_OUT"

# Jeśli aktywny server.cfg już istniał (zapisaliśmy tylko .example), to mimo to
# ZSYNCHRONIZUJ w nim linie qlx_plugins i qlx_workshopReferences — inaczej nowe
# pluginy/workshop się nie załadują. Reszta Twoich ustawień (mapa, hostname itd.)
# pozostaje nietknięta. Backup obok.
if [ "$CFG_OUT" != "$CFG" ] && [ -f "$CFG" ]; then
  cp -a "$CFG" "${CFG}.bak.$(date +%Y%m%d%H%M%S)"
  if grep -qE '^[[:space:]]*set[[:space:]]+qlx_plugins' "$CFG"; then
    sed -i -E "s|^[[:space:]]*set[[:space:]]+qlx_plugins.*|set qlx_plugins \"${QLX_PLUGINS_LIST}\"|" "$CFG"
    ok "Zsynchronizowano qlx_plugins w istniejącym server.cfg (kopia: ${CFG}.bak.*)."
  else
    printf '\nset qlx_plugins "%s"\n' "$QLX_PLUGINS_LIST" >> "$CFG"
    ok "Dodano qlx_plugins do istniejącego server.cfg (kopia: ${CFG}.bak.*)."
  fi
  if grep -qE '^[[:space:]]*set[[:space:]]+qlx_workshopReferences' "$CFG"; then
    sed -i -E "s|^[[:space:]]*set[[:space:]]+qlx_workshopReferences.*|set qlx_workshopReferences \"${WORKSHOP_IDS_CSV}\"|" "$CFG"
    ok "Zsynchronizowano qlx_workshopReferences w istniejącym server.cfg."
  else
    printf 'set qlx_workshopReferences "%s"\n' "$WORKSHOP_IDS_CSV" >> "$CFG"
    ok "Dodano qlx_workshopReferences do istniejącego server.cfg."
  fi
fi

# ── 7a. workshop.txt (Steam Workshop ID-ki) ──────────────────────────────────
# Plik z listą ID-ków Workshop, jeden na linię. ŹRÓDŁEM PRAWDY dla pluginu jest
# CVAR qlx_workshopReferences (powyżej, w cfgach) — plik to ludzka, łatwa do
# edycji wersja listy. Przy ponownym uruchomieniu instalatora cvar i plik są
# regenerowane z tablicy WORKSHOP_IDS na początku tego skryptu.
WORKSHOP_FILE="$QLDS_DIR/workshop.txt"
if [ -f "$WORKSHOP_FILE" ]; then
  warn "workshop.txt już istnieje — nie nadpisuję. Wzór zapisuję jako workshop.txt.example."
  if [ -n "$CONFIGS_DIR" ] && [ -f "$CONFIGS_DIR/workshop.txt" ]; then
    cp -f "$CONFIGS_DIR/workshop.txt" "${WORKSHOP_FILE}.example"
  else
    printf '%s\n' "$WORKSHOP_IDS_TXT" > "${WORKSHOP_FILE}.example"
  fi
else
  if [ -n "$CONFIGS_DIR" ] && [ -f "$CONFIGS_DIR/workshop.txt" ]; then
    cp -f "$CONFIGS_DIR/workshop.txt" "$WORKSHOP_FILE"
    ok "Lista workshop skopiowana z lokalnego configs and mappool/: $WORKSHOP_FILE"
  else
    printf '%s\n' "$WORKSHOP_IDS_TXT" > "$WORKSHOP_FILE"
  fi
fi
ok "Lista workshop: $WORKSHOP_FILE (${#WORKSHOP_IDS[@]} ID-ków)"

# ── 8. Skrypt startowy ───────────────────────────────────────────────────────
START="$QLDS_DIR/start.sh"
cat > "$START" <<EOF
#!/usr/bin/env bash
# Skrypt startowy serwera QL + minqlx (wygenerowany przez instalator)
cd "${QLDS_DIR}" || exit 1
exec ./run_server_x64_minqlx.sh \\
  +set net_strict 1 \\
  +set net_port "${NET_PORT}" \\
  +set fs_homepath "${QLDS_DIR}" \\
  +set zmq_stats_enable 1 \\
  +set zmq_stats_password "${STATS_PASSWORD}" \\
  +set zmq_rcon_enable 1 \\
  +set zmq_rcon_password "${RCON_PASSWORD}" \\
  +set sv_hostname "${SV_HOSTNAME}" \\
  +set qlx_owner "${QLX_OWNER}" \\
  +exec server.cfg
EOF
chmod +x "$START"
ok "Skrypt startowy: $START"

# ── 9. systemd ───────────────────────────────────────────────────────────────
if [ "$INSTALL_SYSTEMD" = "1" ]; then
  log "Instaluję usługę systemd (autostart)..."
  UNIT="/etc/systemd/system/qlserver.service"
  sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=Quake Live Dedicated Server (minqlx)
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${QLDS_DIR}
ExecStart=${QLDS_DIR}/start.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  if [ "$INSTALL_GAMETYPE_SERVERS" = "1" ]; then
    ok "Usługa qlserver.service zainstalowana, ale NIE włączona (używasz serwerów trybów FFA/TDM/FT; generyczny server.cfg dzieliłby port 27960 z tdm)."
  else
    sudo systemctl enable qlserver.service
    ok "Usługa qlserver.service zainstalowana (jeszcze nieuruchomiona)."
  fi
fi

# ── 10. Narzędzie do dodawania kolejnych serwerów QL ─────────────────────────
# Kolejne serwery używają TEJ SAMEJ instalacji QLDS/minqlx, ale mają:
#   • własny port UDP,
#   • własny plik konfiguracji baseq3/<nazwa>.cfg (pierwszy ma server.cfg),
#   • własny skrypt start-<nazwa>.sh.
# Owner (qlx_owner) oraz hasła rcon/stats dziedziczone są z pierwszego start.sh.
#
# UWAGA: instancje dodawane przez add_server.sh NIE są rejestrowane w systemd
# i NIE uruchamiają się automatycznie. Uruchamiasz je ręcznie skryptem
# start-<nazwa>.sh (najlepiej w screen/tmux lub nohup). Jeśli chcesz autostart,
# napisz własny unit albo zrób to ręcznie na bazie szablonu z sekcji 9.
ADD_SCRIPT="$QLDS_DIR/add_server.sh"
log "Tworzę narzędzie dodawania serwerów: $ADD_SCRIPT"
{
  echo '#!/usr/bin/env bash'
  echo '# add_server.sh — dodaje kolejny serwer QL (instancję).'
  echo '# Użycie:  ./add_server.sh [nazwa] [port]   (bez argumentów pyta interaktywnie)'
  echo '# Instancja NIE jest rejestrowana w systemd — uruchamiasz ręcznie ./start-<nazwa>.sh'
  echo 'set -euo pipefail'
  echo "QLDS_DIR=\"$QLDS_DIR\""
  cat <<'ADDBODY'
c_ok="\033[1;32m"; c_warn="\033[1;33m"; c_err="\033[1;31m"; c_i="\033[1;36m"; c_e="\033[0m"
log(){ echo -e "${c_i}[*]${c_e} $*"; }; ok(){ echo -e "${c_ok}[OK]${c_e} $*"; }
warn(){ echo -e "${c_warn}[!]${c_e} $*"; }; die(){ echo -e "${c_err}[X]${c_e} $*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Nie uruchamiaj jako root."

# 1) Nazwa instancji (plik konfiguracji i nazwa skryptu startowego)
NAME="${1:-}"
if [ -z "$NAME" ]; then read -rp "Nazwa nowego serwera (np. duel, ffa2): " NAME; fi
SAFE="$(echo "$NAME" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9_-')"
[ -n "$SAFE" ] || die "Nieprawidłowa nazwa (dozwolone: a-z 0-9 _ -)."
[ "$SAFE" != "server" ] || die "Nazwa 'server' jest zarezerwowana dla pierwszego serwera."

CFG="$QLDS_DIR/baseq3/${SAFE}.cfg"
START="$QLDS_DIR/start-${SAFE}.sh"
HOMEPATH="$QLDS_DIR/instances/${SAFE}"
[ ! -f "$CFG" ] || die "Konfiguracja ${SAFE}.cfg już istnieje — wybierz inną nazwę."

# 2) Port UDP (rcon = port+1000, stats = port — muszą być wolne)
PORT="${2:-}"
if [ -z "$PORT" ]; then read -rp "Port UDP nowego serwera (np. 27970): " PORT; fi
[[ "$PORT" =~ ^[0-9]{3,5}$ ]] || die "Port musi byc liczba (3-5 cyfr)."
for f in "$QLDS_DIR"/start.sh "$QLDS_DIR"/start-*.sh; do
  [ -f "$f" ] || continue
  e="$(grep -oE 'net_port "[0-9]+"' "$f" | head -1 | tr -cd '0-9')"
  [ -n "$e" ] || continue
  if [ "$PORT" = "$e" ] || [ "$PORT" = "$((e+1000))" ] || [ "$((PORT+1000))" = "$e" ]; then
    die "Port $PORT koliduje z instancja na porcie $e (rcon = port+1000). Wybierz inny, odlegly o >=10."
  fi
done

# 3) Owner i hasła — dziedziczone z pierwszego start.sh
extract(){ grep -oE "\+set $1 \"[^\"]*\"" "$QLDS_DIR/start.sh" 2>/dev/null | head -1 | sed -E 's/.*"([^"]*)"/\1/'; }
OWNER="$(extract qlx_owner)"; OWNER="${OWNER:-76561198799965164}"
STATS_PW="$(extract zmq_stats_password)"; STATS_PW="${STATS_PW:-zmien_to_haslo_stats}"
RCON_PW="$(extract zmq_rcon_password)"; RCON_PW="${RCON_PW:-zmien_to_haslo_rcon}"

# 4) Konfiguracja — kopiujemy server.cfg jako bazę i zmieniamy sv_hostname
mkdir -p "$HOMEPATH"
if [ -f "$QLDS_DIR/baseq3/server.cfg" ]; then
  cp "$QLDS_DIR/baseq3/server.cfg" "$CFG"
  sed -i -E "s|^([[:space:]]*set[[:space:]]+sv_hostname[[:space:]]+).*|\1\"^3${SAFE}\"|I" "$CFG"
else
  printf 'set sv_hostname "^3%s"\nmap campgrounds ffa\n' "$SAFE" > "$CFG"
fi

# 5) Skrypt startowy instancji
cat > "$START" <<START_EOF
#!/usr/bin/env bash
# Serwer QL '${SAFE}' (minqlx) — wygenerowany przez add_server.sh
cd "$QLDS_DIR" || exit 1
exec ./run_server_x64_minqlx.sh \\
  +set net_strict 1 \\
  +set net_port "$PORT" \\
  +set fs_homepath "$HOMEPATH" \\
  +set zmq_stats_enable 1 \\
  +set zmq_stats_password "$STATS_PW" \\
  +set zmq_rcon_enable 1 \\
  +set zmq_rcon_password "$RCON_PW" \\
  +set sv_hostname "^3$SAFE" \\
  +set qlx_owner "$OWNER" \\
  +exec ${SAFE}.cfg
START_EOF
chmod +x "$START"

# 6) BEZ systemd — instancja uruchamiana ręcznie.
#    Jeśli chcesz autostart przy boocie, utwórz własny unit na wzór qlserver.service
#    (sekcja 9 instalatora) i wskaż w nim ExecStart=${START}.

ok "Dodano serwer '${SAFE}' (BEZ rejestracji w systemd)."
echo "  config:    $CFG"
echo "  start:     $START"
echo "  port:      UDP $PORT (rcon TCP $((PORT+1000)))"
echo "  uruchom:   $START"
echo "  w tle:     nohup $START > $QLDS_DIR/${SAFE}.log 2>&1 &"
echo "  w screen:  screen -dmS qlserver-${SAFE} $START   (potem: screen -r qlserver-${SAFE})"
echo "  firewall:  otworz port UDP $PORT"
echo "  panel:     aby zarzadzac nim w panelu, dodaj wpis do qlpanel/servers.json"
ADDBODY
} > "$ADD_SCRIPT"
chmod +x "$ADD_SCRIPT"
ok "Gotowe: $ADD_SCRIPT — uruchom kiedykolwiek, by dodać kolejny serwer (bez systemd)."

# Opcjonalnie: dodaj kolejne serwery już teraz (tylko w trybie interaktywnym).
if [ -t 0 ]; then
  while true; do
    read -rp $'\nDodać teraz kolejny serwer QL? [t/N]: ' _yn || break
    case "${_yn:-}" in
      [tTyY]*) bash "$ADD_SCRIPT" || warn "Nie udało się dodać serwera (patrz wyżej)." ;;
      *) break ;;
    esac
  done
fi

# ── 11. Serwery trybów FFA / TDM / FT (+ dołączone factory) ──────────────────
# Wgrywa plik z własnymi factory (gametypes.factories) oraz trzy gotowe,
# OCZYSZCZONE konfiguracje trybów i usługi systemd dla każdej z nich.
# Porty: tdm=27960, ffa=27961, ft=27962.
if [ "$INSTALL_GAMETYPE_SERVERS" = "1" ]; then
  log "Instaluję serwery trybów FFA/TDM/FT + factory..."

  # 11a. Plik z definicjami factory -> baseq3/scripts/ (czyta go każda instancja).
  mkdir -p "$QLDS_DIR/baseq3/scripts"
  cat > "$QLDS_DIR/baseq3/scripts/gametypes.factories" <<'FACTORIES_EOF'
[
  {
    "basegt": "ft",
    "id":"mg_ft_fullclassic",
    "title":"Full Classic Freeze Tag",
    "author":"crobartie",
    "description":"Full Classic FT settings.",
    "cvars":{
      "sv_tags": "crobartie,ft,SAM,FriendlyFire,5sRespawnWeapon,NoRoundDelay,de,minqlx",
      "fraglimit": "0",
      "sv_mappoolfile": "mappool_mg_ft_fullclassic.txt",
      "g_forceNextMap": "0",
      "g_voteflags": "13416",
	  "g_allowVoteMidGame": "1",
	  "g_allowSpecVote": "1",
      "g_allowKill":"1000",
      "g_battleSuitDampen": ".25",
      "g_complaintLimit": "0",
      "g_damage_mg": "5",
      "g_damage_hmg": "7",
      "g_dropCmds": "6",
      "g_dropPowerups": "0",
      "g_friendlyFire": "1",
      "g_itemHeight":"15",
      "g_itemTimers":"0",
      "g_overtime": "0",
      "g_spawnArmor": "0",
      "g_teamForceBalance": "1",
      "g_timeoutCount": "3",
      "g_freezeResetHealthOnRound":"0",
      "g_freezeResetWeaponsOnRound":"0",
      "g_freezeResetArmorOnRound":"0",
      "g_startingweapons":"135",
      "g_loadout":"0",
      "g_ammoPack":"0",
      "pmove_BunnyHop":"0",
      "pmove_CrouchStepJump":"0",
      "pmove_JumpTimeDeltaMin":"50",
      "pmove_WaterSwimScale":"0.5f",
      "pmove_WaterWadeScale":"0.75f",
      "sv_warmupReadyPercentage": "0.74",
      "timelimit": "20",
      "roundlimit":"10",
      "mercylimit":"7",
	  "roundtimelimit": "0",
      "teamsize": "6",
      "sv_maxClients": "22",
      "disable_weapon_bfg":"1",
      "disable_ammo_bfg":"1",
      "sv_fps": "40",
	  "g_freezeThawWinningTeam":"0",
	  "g_freezeRoundDelay":"0",
      "g_inactivity": "240"
    }
  },
 
  {
    "basegt": "ft",
    "id":"mg_ft_allweapons",
    "title":"All Weapons Freeze Tag",
    "author":"crobartie",
    "description":"FT All Weapons settings.",
    "cvars":{
      "sv_tags": "crobartie,ft,SAM,FriendlyFire,allweapons,5sRespawnWeapon,NoRoundDelay",
      "fraglimit": "0",
      "sv_mappoolfile": "mappool_mg_ft_allweapons.txt",
      "g_forceNextMap": "0",
      "g_voteflags": "13416",
	  "g_allowVoteMidGame": "1",
	  "g_allowSpecVote": "1",
      "g_allowKill":"1000",
      "g_battleSuitDampen": ".25",
      "g_complaintLimit": "0",
      "g_damage_mg": "5",
      "g_damage_hmg": "7",
      "g_dropCmds": "6",
      "g_dropPowerups": "0",
      "g_friendlyFire": "1",
      "g_itemHeight":"15",
      "g_itemTimers":"0",
      "g_overtime": "0",
      "g_spawnArmor": "0",
      "g_teamForceBalance": "1",
      "g_timeoutCount": "3",
      "g_freezeResetHealthOnRound":"0",
      "g_freezeResetWeaponsOnRound":"0",
      "g_freezeResetArmorOnRound":"25",
      "g_startingweapons":"16387",
      "g_loadout":"0",
      "g_ammoPack":"0",
      "pmove_BunnyHop":"0",
      "pmove_CrouchStepJump":"0",
      "pmove_JumpTimeDeltaMin":"50",
      "pmove_WaterSwimScale":"0.5f",
      "pmove_WaterWadeScale":"0.75f",
      "sv_warmupReadyPercentage": "0.74",
      "timelimit": "20",
      "roundlimit":"10",
      "mercylimit":"7",
	  "roundtimelimit": "0",
      "teamsize": "6",
      "sv_maxClients": "30",
      "disable_weapon_bfg":"1",
      "disable_ammo_bfg":"1",
      "sv_fps": "40",
	  "g_startingArmor":"25",
	  "g_freezeThawWinningTeam":"1",
	  "g_freezeRoundDelay":"0",
      "g_inactivity": "240"
    }
  },
   
  {
    "basegt": "ft",
    "id":"mg_ft_promode",
    "title":"Q3 Freeze Tag",
    "author":"crobartie",
    "description":"Pro Q3 FT settings.",
    "cvars":{
      "sv_hostname": "crobartie's serv, Q3 FT _______ IRC: #croFTpro, #SAMQL",
      "sv_tags": "crobartie,ft,q3,fastweapons,SAM",	
      "fraglimit": "0",
      "sv_mappoolfile": "mappool_mg_ft_promode.txt",
      "g_forceNextMap": "0",
      "g_voteflags": "13416",
	  "g_allowVoteMidGame": "1",
	  "g_allowSpecVote": "1",
      "g_allowKill":"1000",
      "g_battleSuitDampen": ".25",
      "g_complaintLimit": "0",
      "g_damage_mg": "5",
      "g_damage_hmg": "6",
	  "g_damage_lg": "7",
	  "g_damage_rg": "100",		  
      "g_dropCmds": "6",
      "g_dropPowerups": "0",
      "g_friendlyFire": "1",
      "g_itemHeight":"15",
      "g_itemTimers":"0",
      "g_overtime": "0",
      "g_spawnArmor": "0",
      "g_teamForceBalance": "1",
      "g_timeoutCount": "3",
      "g_weaponRespawn": "30",
      "g_freezeResetHealthOnRound":"0",
      "g_freezeResetWeaponsOnRound":"0",
      "g_freezeResetArmorOnRound":"0",
      "g_startingweapons":"135",
      "g_loadout":"0",
      "g_ammoPack":"0",
      "pmove_BunnyHop":"0",
      "pmove_CrouchStepJump":"0",
      "pmove_JumpTimeDeltaMin":"50",
      "pmove_WaterSwimScale":"0.5f",
      "pmove_WaterWadeScale":"0.75f",
	  "pmove_WeaponRaiseTime":"0",
	  "pmove_WeaponDropTime":"0",
	  "pmove_RampJump":"0",
	  "pmove_AirControl":"0",
      "sv_warmupReadyPercentage": "0.8",
      "timelimit": "20",
      "roundlimit":"10",
      "teamsize": "6",
      "sv_maxClients": "30",
      "disable_weapon_bfg":"1",
      "disable_ammo_bfg":"1",
      "sv_fps": "40",
	  "g_freezeThawWinningTeam":"0",
	  "g_freezeRoundDelay":"0",
      "g_inactivity": "240"
    }
 },
 
   {
    "basegt": "ft",
    "id":"mg_ft_uft",
    "title":"Ultra Freeze Tag",
    "author":"crobartie",
    "description":"Ultra FT settings.",
    "cvars":{
      "sv_hostname": "crobartie's serv, Ultra FT _______ IRC: #croUFT, #SAMQL",
      "sv_tags": "crobartie,ft,uft,ultra,SAM",
      "fraglimit": "0",
      "sv_mappoolfile": "mappool_mg_ft_uft.txt",
      "g_forceNextMap": "0",
      "g_voteflags": "13416",
	  "g_allowVoteMidGame": "1",
	  "g_allowSpecVote": "1",
      "g_allowKill":"1000",
      "g_battleSuitDampen": ".25",
      "g_complaintLimit": "0",
      "g_damage_mg": "4",
      "g_damage_hmg": "6",
      "g_dropCmds": "6",
      "g_dropPowerups": "0",
      "g_friendlyFire": "1",
      "g_itemHeight":"15",
      "g_itemTimers":"0",
      "g_overtime": "0",
      "g_spawnArmor": "0",
      "g_teamForceBalance": "1",
      "g_timeoutCount": "3",
      "g_weaponRespawn": "30",
      "g_freezeResetHealthOnRound":"0",
      "g_freezeResetWeaponsOnRound":"0",
      "g_freezeResetArmorOnRound":"0",
      "g_startingweapons":"16387",
      "g_loadout":"0",
      "g_ammoPack":"0",
      "pmove_BunnyHop":"0",
      "pmove_CrouchStepJump":"0",
      "pmove_JumpTimeDeltaMin":"50",
      "pmove_WaterSwimScale":"0.5f",
      "pmove_WaterWadeScale":"0.75f",
      "sv_warmupReadyPercentage": "0.8",
      "timelimit": "20",
      "roundlimit":"15",
	  "roundtimelimit": "0",
      "teamsize": "6",
      "sv_maxClients": "30",
      "disable_weapon_bfg":"1",
      "disable_ammo_bfg":"1",
      "sv_fps": "40",
      "g_inactivity": "240",
	  "g_freezeRemovePowerupsOnRound":"0",
	  "g_freezeThawWinningTeam":"0",
	  "g_freezeEnvironmentalRespawnDelay":"3000",
	  "g_freezeRoundDelay":"0",
	  "g_freezeThawTick":"2",
	  "g_freezeAutoThawTime":"90000",
	  "g_startingArmor":"25",
	  "g_forceDmgThroughSurface":"1",
	  "g_startingAmmo_pg":"75",
	  "g_freezeThawTime":"2500",
	  "g_overtime": "0"
    }
  },
  
  {
    "basegt": "tdm",
    "id":"mg_tdm_utdm",
    "title":"Ultra TDM",
    "author":"crobartie",
    "description":"Ultra TDM settings.",
    "cvars":{
      "sv_hostname": "crobartie's serv, Ultra TDM _______ IRC: #croUTDM, #SAMQL",
      "sv_tags": "crobartie,tdm,utdm,ultra,SAM",
      "fraglimit": "0",
      "sv_mappoolfile": "mappool_mg_tdm_utdm.txt",
      "g_forceNextMap": "0",
      "g_voteflags": "13416",
	  "g_allowVoteMidGame": "1",
	  "g_allowSpecVote": "1",
      "g_allowKill":"1000",
      "g_battleSuitDampen": ".25",
      "g_complaintLimit": "0",
      "g_damage_mg": "4",
      "g_damage_hmg": "6",
      "g_dropCmds": "4",
      "g_dropPowerups": "0",
      "g_friendlyFire": "1",
      "g_itemHeight":"15",
      "g_itemTimers":"0",
      "g_mercytime": "0",
      "mercylimit": "50",
      "g_overtime": "120",
      "g_spawnArmor": "0",
      "g_teamForceBalance": "1",
      "g_timeoutCount": "3",
      "g_weaponRespawn": "30",
	  "g_startingweapons":"16387",
      "g_loadout":"0",
      "g_ammoPack":"0",
      "pmove_BunnyHop":"0",
      "pmove_CrouchStepJump":"0",
      "pmove_JumpTimeDeltaMin":"50",
      "pmove_WaterSwimScale":"0.5f",
      "pmove_WaterWadeScale":"0.75f",
      "sv_warmupReadyPercentage": "0.8",
      "timelimit": "20",
      "teamsize": "5",
      "sv_maxClients": "30",
      "disable_weapon_bfg":"1",
      "disable_ammo_bfg":"1",
      "sv_fps": "40",
      "g_inactivity": "240",
	  "g_startingArmor":"25",
	  "g_forceDmgThroughSurface":"1",
	  "g_startingAmmo_pg":"75",
	  "g_overtime": "0",
	  "g_grantItemOnSpawn":"",
	  "g_maxFlightFuel":"",
	  "g_kamiMinRatio":"0.0",
	  "g_kamiAttenuate":"0"
    }
  },
  
  {
	"id": "maido",
	"title": "Maido",
	"author": "Bsan",
	"description": "Mid-air like mode & map. 1.console open /callvote map maido maido. 2.add nightmare bot",
	"basegt": "tdm",
	"cvars": {
	    "sv_hostname": "crobartie's serv, Maido _______ IRC: #SAMQL, #croMaido",
		"sv_tags": "crobartie,maido,SAM",
		"sv_mapPoolFile": "maido.txt",
		"g_startingHealth": "1000",
		"g_startingHealthBonus": "0",
		"g_regenHealth": "1",
		"g_regenHealthRate": "1",
		"g_damage_rl": "999",
		"g_splashdamage_rl": "1",
		"g_knockback_rl": "10",
		"g_knockback_z": "320",
		"g_respawn_delay_min": "500",
		"g_respawn_delay_max": "500",
		"g_infiniteAmmo": "1",
		"g_startingWeapons": "17",
		"g_overTime": "30",
		"g_voteFlags": "2048",
		"bot_startingSkill": "1",
		"timelimit": "2",
		"pmove_bunnyhop": "0",
		"teamsize": "1",
		"g_spawnItemPowerup": "0",
		"bot_enable":"1",
		"g_inactivity": "240"
	}
  },
  
    {
    "basegt": "tdm",
    "id":"sparing",
    "title":"Sparing",
    "author":"crobartie",
    "description":"Sparing:rg & lg.",
    "cvars":{
      "sv_hostname": "crobartie's serv, Sparing _______ IRC: #croSparing, #SAMQL",
      "sv_tags": "crobartie,SAM,sparing,rg,lg",
	  "practiceflags": "1",
      "fraglimit": "0",
      "sv_mappoolfile": "sparing.txt",
      "g_forceNextMap": "0",
      "g_voteflags": "13416",
	  "g_allowVoteMidGame": "1",
	  "g_allowSpecVote": "1",
      "g_allowKill":"1000",
      "g_battleSuitDampen": ".25",
      "g_complaintLimit": "0",
      "g_damage_mg": "4",
      "g_damage_hmg": "6",
      "g_dropCmds": "4",
      "g_dropPowerups": "0",
      "g_friendlyFire": "1",
      "g_itemHeight":"15",
      "g_itemTimers":"0",
      "g_mercytime": "0",
      "mercylimit": "50",
      "g_overtime": "120",
      "g_spawnArmor": "0",
      "g_teamForceBalance": "1",
      "g_timeoutCount": "3",
      "g_weaponRespawn": "30",
      "g_loadout":"0",
      "g_ammoPack":"0",
      "pmove_BunnyHop":"0",
      "pmove_CrouchStepJump":"0",
      "pmove_JumpTimeDeltaMin":"50",
      "pmove_WaterSwimScale":"0.5f",
      "pmove_WaterWadeScale":"0.75f",
      "sv_warmupReadyPercentage": "0.8",
      "timelimit": "2",
      "teamsize": "5",
      "sv_maxClients": "16",
      "disable_weapon_bfg":"1",
      "disable_ammo_bfg":"1",
      "sv_fps": "40",
	  "g_regenhealth": "1",
	  "g_regenHealthRate": "1",
	  "g_infiniteammo": "1",
	  "g_startinghealth": "999",
	  "g_startingarmor": "999",
	  "g_startingweapons": "97",
      "g_password": "accur4cy",
	  "g_spawnItemPowerup": "0",
	  "bot_enable":"1",
	  "g_inactivity": "240"
    }
  },
  
    {
    "basegt": "race",
    "id":"mg_race_classic",
    "title":"Classic Race",
    "author":"crobartie",
    "description":"Race with classic (Vanilla) physics.",
    "cvars":{
      "sv_hostname": "crobartie's serv, Race _______ IRC: #SAMQL",
      "sv_tags": "crobartie,SAM,race",
	  "practiceflags": "1",
      "fraglimit": "0",
      "sv_mappoolfile": "sparing.txt",
      "g_forceNextMap": "0",
      "g_voteflags": "13416",
	  "g_allowVoteMidGame": "1",
	  "g_allowSpecVote": "1",
      "g_battleSuitDampen": ".25",
      "g_complaintLimit": "0",
      "g_damage_mg": "4",
      "g_damage_hmg": "6",
      "g_dropCmds": "4",
      "g_dropPowerups": "0",
      "g_friendlyFire": "1",
      "g_itemHeight":"15",
      "g_itemTimers":"0",
      "g_mercytime": "0",
      "mercylimit": "50",
      "g_spawnArmor": "0",
      "g_teamForceBalance": "1",
      "g_timeoutCount": "3",
      "g_weaponRespawn": "30",
      "g_loadout":"0",
      "g_ammoPack":"0",
      "pmove_noPlayerClip": "1",
      "dmflags": "28",
      "g_startingWeapons": "145",
      "g_allowKill": "1",
      "g_overtime": "0",
      "g_respawn_delay_max": "1",
      "g_respawn_delay_min": "1",
      "g_startingHealthBonus": "0",
      "pmove_BunnyHop":"0",
      "pmove_CrouchStepJump":"0",
      "pmove_JumpTimeDeltaMin":"50",
      "pmove_WaterSwimScale":"0.5f",
      "pmove_WaterWadeScale":"0.75f",
      "sv_warmupReadyPercentage": "0.8",
      "timelimit": "8",
      "teamsize": "5",
      "sv_maxClients": "16",
      "disable_weapon_bfg":"1",
      "disable_ammo_bfg":"1",
      "sv_fps": "40",
      "g_password": "accur4cy",
	  "bot_enable":"1",
	  "g_inactivity": "240"
    }
  },
  
    {
	"basegt": "ffa",
	"id":"mg_ffa_aw",
	"title":"All Weapons FFA",
	"author":"crobartie",
	"description":"FFA with a hint of CA. Spawn with all weapons, self damage.",
	"cvars":{
      "sv_tags": "crobartie,ffa,SAM,allweapons,de,classic,aw,minqlx",
      "sv_mappoolfile": "mappool_mg_ffa_aw.txt",
      "g_startingWeapons":"16387",
      "g_forceNextMap": "0",
      "g_voteflags": "13416",
	  "g_allowVoteMidGame": "1",
	  "g_allowSpecVote": "1",
      "g_allowKill":"1000",
      "g_battleSuitDampen": ".25",
      "g_complaintLimit": "0",
      "g_damage_mg": "5",
      "g_damage_hmg": "7",
      "sv_warmupReadyPercentage": "0.51",
      "sv_maxClients": "22",
      "disable_weapon_bfg":"1",
      "disable_ammo_bfg":"1",
      "sv_fps": "40",
      "dmflags":"0",
      "timelimit":"10",
      "fraglimit":"100",
      "teamsize": "8",
      "g_itemTimers":"0",
      "g_overtime":"0",
      "g_startingAmmo_sg":"10",
      "g_startingAmmo_sg":"10",
      "g_startingAmmo_rl":"10",
      "g_startingAmmo_lg":"100",
      "g_startingAmmo_rg":"10",
      "g_startingAmmo_pg":"100",
      "g_startingAmmo_hmg":"100",
      "g_ammoPack":"0",
      "g_ammoRespawn":"40",
      "pmove_BunnyHop": "0",
      "pmove_CrouchStepJump": "0",
      "pmove_JumpTimeDeltaMin": "50",
      "pmove_WaterSwimScale": "0.5f",
      "pmove_WaterWadeScale": "0.75f",
      "g_inactivity": "120"	  
	}
  },
  
  {
    "basegt": "tdm",
    "id":"mg_tdm_fullclassic",
    "title":"Full Classic TDM",
    "author":"crobartie",
    "description":"Full Classic TDM settings.",
    "cvars":{
      "sv_tags": "crobartie,classic,SAM",
      "fraglimit": "0",
      "sv_mappoolfile": "ctdm.txt",
      "g_forceNextMap": "0",
      "g_voteflags": "13416",
	  "g_allowVoteMidGame": "1",
	  "g_allowSpecVote": "1",
      "g_allowKill":"1000",
      "g_battleSuitDampen": ".25",
      "g_complaintLimit": "0",
      "g_damage_mg": "4",
      "g_damage_hmg": "6",
      "g_dropCmds": "4",
      "g_dropPowerups": "0",
      "g_friendlyFire": "1",
      "g_itemHeight":"15",
      "g_itemTimers":"0",
      "g_mercytime": "0",
      "mercylimit": "50",
      "g_overtime": "120",
      "g_spawnArmor": "0",
      "g_teamForceBalance": "1",
      "g_timeoutCount": "3",
      "g_weaponRespawn": "30",
      "g_loadout":"0",
      "g_ammoPack":"0",
      "pmove_BunnyHop":"0",
      "pmove_CrouchStepJump":"0",
      "pmove_JumpTimeDeltaMin":"50",
      "pmove_WaterSwimScale":"0.5f",
      "pmove_WaterWadeScale":"0.75f",
      "sv_warmupReadyPercentage": "0.8",
      "timelimit": "20",
      "teamsize": "5",
      "sv_maxClients": "22",
      "disable_weapon_bfg":"1",
      "disable_ammo_bfg":"1",
      "sv_fps": "40",
      "g_password": "",
	  "g_grantItemOnSpawn":"",
      "g_inactivity": "240"
    }
  }
]
FACTORIES_EOF
  ok "Factory wgrane: $QLDS_DIR/baseq3/scripts/gametypes.factories"

  # 11b. Generator pojedynczej konfiguracji trybu.
  #   $1 nazwa  $2 sufiks_hostname  $3 sv_tags  $4 mappool  $5 serverstartup
  #   $6 brand  $7 etykieta_motd
  write_gt_cfg() {
    local gt="$1" suf="$2" tags="$3" pool="$4" startup="$5" brand="$6" motd="$7"
    cat > "$QLDS_DIR/baseq3/${gt}.cfg" <<GTCFG
// ===========================================================================
//  ${gt}.cfg — serwer trybu ${suf} (minqlx). Wygenerowane przez instalator.
//  Lista pluginów oczyszczona: bez irc / patch / specvote.
// ===========================================================================
set sv_hostname            "[TSK] THE SHADOWS KILLERS #${suf}"
set sv_tags                "${tags}"
set g_accessFile           "access.txt"
set sv_maxClients          "22"
set com_hunkMegs           "90"
set sv_floodprotect        "10"
set g_floodprot_maxcount   "10"
set g_floodprot_decay      "1000"
set g_voteFlags            "14190"
set g_allowVote            "1"
set g_voteDelay            "5000"
set g_allowVoteMidGame     "0"
set g_allowSpecVote        "0"
set g_inactivity           "120"
set g_alltalk              "0"
set sv_serverType          "2"
set sv_master              "1"
set sv_fps                 "40"
set sv_idleExit            "600"

// minqlx
set qlx_owner              "${QLX_OWNER}"
set qlx_plugins            "${GT_PLUGINS_LIST}"
set qlx_workshopReferences "${WORKSHOP_IDS_CSV}"
set qlx_database           "Redis"
set qlx_redisAddress       "127.0.0.1"
set qlx_redisDatabase      "0"
set qlx_redisUnixSocket    "0"
set qlx_logs               "5"
set qlx_logsSize           "5000000"

// balance / leaver
set qlx_balanceAuto        "1"
set qlx_balanceUseLocal    "0"
set qlx_balanceMinimumSuggestionDiff "30"
set qlx_leaverBan          "1"
set qlx_leaverBanThreshold "0.75"

// branding
set qlx_motdHeader         "^7==^2 ^1[TSK] ^7==^2 ${motd} ^7==^3 THE SHADOWS KILLERS ^7==^4 GL HF ^7==^2"
set qlx_serverBrandName    "^1[TSK]^7 ${brand}"
set qlx_serverBrandTopField    "^7Running ^2minqlx^7 and ^2qlstats.net^7"
set qlx_serverBrandBottomField "^7Have ^1fun^7, play well, don't ^3whine^7"
set qlx_votepass           "1"

// pula map. UWAGA: qlx_enforceMappool=0 — pula nie jest wymuszana, więc brak
// niestandardowego pliku puli nie blokuje głosowań ani startu. Chcesz wymuszać?
// Ustaw na 1 i utwórz odpowiedni plik puli w baseq3/.
set sv_mapPoolFile         "${pool}"
set qlx_enforceMappool     "0"

set fraglimit              "0"
set timelimit              "0"
set teamsize               "8"
set roundlimit             "10"

// Pierwsza mapa + factory (factory MUSI istnieć, inaczej serwer nie wstanie).
set serverstartup          "${startup}"
GTCFG
    ok "  konfiguracja: baseq3/${gt}.cfg  (startup: ${startup})"
  }

  # 11c. Generator skryptu startowego + usługi systemd dla instancji trybu.
  #   $1 nazwa(gt)  $2 port_udp
  write_gt_service() {
    local gt="$1" port="$2"
    local start="$QLDS_DIR/start-${gt}.sh"
    local home="$QLDS_DIR/instances/${gt}"
    mkdir -p "$home"
    cat > "$start" <<STARTEOF
#!/usr/bin/env bash
# Serwer QL trybu '${gt}' (minqlx) — wygenerowany przez instalator.
cd "${QLDS_DIR}" || exit 1
exec ./run_server_x64_minqlx.sh \\
  +set net_strict 1 \\
  +set net_port "${port}" \\
  +set fs_homepath "${home}" \\
  +set zmq_stats_enable 1 \\
  +set zmq_stats_password "${STATS_PASSWORD}" \\
  +set zmq_rcon_enable 1 \\
  +set zmq_rcon_password "${RCON_PASSWORD}" \\
  +set qlx_owner "${QLX_OWNER}" \\
  +exec ${gt}.cfg
STARTEOF
    chmod +x "$start"
    if [ "$INSTALL_SYSTEMD" = "1" ]; then
      sudo tee "/etc/systemd/system/qlserver-${gt}.service" >/dev/null <<UNITEOF
[Unit]
Description=Quake Live Dedicated Server (minqlx) - ${gt}
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${QLDS_DIR}
ExecStart=${start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNITEOF
      sudo systemctl daemon-reload
      sudo systemctl enable "qlserver-${gt}.service"
    fi
    ok "  usługa: qlserver-${gt}  (port UDP ${port}, rcon TCP $((port+1000)))"
  }

  # 11d. Konfiguracje trybów — z lokalnego katalogu configs and mappool/ lub generowane.
  install_gt_cfg() {
    local gt="$1"
    if [ -n "$CONFIGS_DIR" ] && [ -f "$CONFIGS_DIR/${gt}.cfg" ]; then
      cp -f "$CONFIGS_DIR/${gt}.cfg" "$QLDS_DIR/baseq3/${gt}.cfg"
      # Zsynchronizuj qlx_workshopReferences z aktualną listą WORKSHOP_IDS.
      if grep -qE '^[[:space:]]*set[[:space:]]+qlx_workshopReferences' "$QLDS_DIR/baseq3/${gt}.cfg"; then
        sed -i -E "s|^[[:space:]]*set[[:space:]]+qlx_workshopReferences.*|set qlx_workshopReferences \"${WORKSHOP_IDS_CSV}\"|" \
          "$QLDS_DIR/baseq3/${gt}.cfg"
      else
        printf '\nset qlx_workshopReferences "%s"\n' "$WORKSHOP_IDS_CSV" >> "$QLDS_DIR/baseq3/${gt}.cfg"
      fi
      ok "  konfiguracja: baseq3/${gt}.cfg (skopiowana z lokalnego configs and mappool/)"
    else
      case "$gt" in
        tdm) write_gt_cfg "tdm" "TDM" "TDM, minqlx, qlstats.net, ELO, TSK, [TSK]," \
               "mappool_tdm.txt" "map campgrounds mg_tdm_fullclassic" "TEAM DEATHMATCH" "TDM" ;;
        ffa) write_gt_cfg "ffa" "FFA" "FFA, minqlx, qlstats.net, ELO, tsk, [TSK]," \
               "mappool_ffa.txt" "map longestyard ffa" "FREE FOR ALL" "FFA" ;;
        ft)  write_gt_cfg "ft"  "FT"  "FT, FREEZE TAG, minqlx, qlstats.net, ELO, tsk, [TSK]," \
               "mappool_tdm.txt" "map almostlost mg_ft_fullclassic" "FREEZE TAG" "FT" ;;
      esac
    fi
  }

  install_gt_cfg "tdm"
  install_gt_cfg "ffa"
  install_gt_cfg "ft"

  # 11e. Pliki puli map i access.txt z lokalnego katalogu configs and mappool/.
  if [ -n "$CONFIGS_DIR" ]; then
    log "Kopiuję pliki puli map z lokalnego configs and mappool/..."
    _map_count=0
    for _f in "$CONFIGS_DIR"/*.txt; do
      [ -f "$_f" ] || continue
      _fname="$(basename "$_f")"
      [ "$_fname" = "workshop.txt" ] && continue  # workshop.txt trafia do $QLDS_DIR/, nie baseq3/
      cp -f "$_f" "$QLDS_DIR/baseq3/$_fname"
      _map_count=$((_map_count+1))
    done
    if [ "$_map_count" -gt 0 ]; then
      ok "Skopiowano $_map_count plików puli map do baseq3/."
    else
      warn "Brak plików *.txt w katalogu configs and mappool/ — nie skopiowano żadnego."
    fi
  fi

  write_gt_service "tdm" "27960"
  write_gt_service "ffa" "27961"
  write_gt_service "ft"  "27962"

  ok "Serwery trybów gotowe. Start: sudo systemctl start qlserver-{tdm,ffa,ft}"
  warn "Otwórz w firewallu porty UDP: 27960 (tdm), 27961 (ffa), 27962 (ft)."
fi

# ── Podsumowanie ─────────────────────────────────────────────────────────────
echo
echo -e "${c_ok}=============================================================${c_end}"
echo -e "${c_ok} INSTALACJA ZAKOŃCZONA${c_end}"
echo -e "${c_ok}=============================================================${c_end}"
cat <<EOF

Serwer:    ${QLDS_DIR}
Config:    ${QLDS_DIR}/baseq3/server.cfg
Start:     ${QLDS_DIR}/start.sh
Pluginy:   ${QLDS_DIR}/minqlx-plugins
Workshop:  ${QLDS_DIR}/workshop.txt (${#WORKSHOP_IDS[@]} ID-ków, cvar qlx_workshopReferences w cfgach)

ZANIM WYSTARTUJESZ — sprawdź:
  • qlx_owner (SteamID64) w start.sh        -> obecnie: ${QLX_OWNER}
  • hasła rcon/stats w start.sh             -> ZMIEŃ na własne
  • lista pluginów (qlx_plugins) w server.cfg
  • otwórz w firewallu port UDP ${NET_PORT}

URUCHOMIENIE:
  Ręcznie:        ${QLDS_DIR}/start.sh
EOF
if [ "$INSTALL_SYSTEMD" = "1" ]; then
cat <<EOF
  Przez systemd:  sudo systemctl start qlserver
  Logi na żywo:   sudo journalctl -u qlserver -f
EOF
fi
cat <<EOF

TEST: wejdź na serwer i wpisz na czacie:  !myperm
  -> jeśli pokaże poziom uprawnień > 0, jesteś rozpoznany jako właściciel.

KOLEJNE SERWERY: uruchom  ${QLDS_DIR}/add_server.sh  (lub  add_server.sh <nazwa> <port>).
  Każdy kolejny serwer ma własny port i własny plik baseq3/<nazwa>.cfg.
  WAŻNE: instancje dodawane przez add_server.sh NIE są rejestrowane w systemd
  i NIE startują automatycznie. Uruchamiasz je ręcznie:
      ${QLDS_DIR}/start-<nazwa>.sh
      nohup ${QLDS_DIR}/start-<nazwa>.sh > ${QLDS_DIR}/<nazwa>.log 2>&1 &
      screen -dmS qlserver-<nazwa> ${QLDS_DIR}/start-<nazwa>.sh
  Pamiętaj otworzyć w firewallu port UDP każdego z nich.

AKTUALIZACJA: uruchom ten skrypt ponownie (zaktualizuje QLDS, minqlx i pluginy).
EOF
