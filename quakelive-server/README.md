# Quake Live Dedicated Server + minqlx — automatyczny instalator

[🇬🇧 English version](README.en.md)

Skrypt `install_minqlx_server.sh` stawia od zera serwer Quake Live (QLDS) z
minqlx i kompletem pluginów (MinoMino + BarelyMiSSeD + tjone270 + kilka
zewnętrznych) na czystym Debianie/Ubuntu (x86_64).

**Instalator generuje tylko skrypty startowe** (`start.sh`, `start-tdm.sh`,
`start-ffa.sh`, `start-ft.sh`, `start-<nazwa>.sh`) — **nie tworzy usług
systemd i nie wrapuje serwera w screen**. Serwery uruchamiasz ręcznie:

```bash
bash ~/qlds/start-tdm.sh
bash ~/qlds/start-ffa.sh
bash ~/qlds/start-ft.sh
```

Jeśli chcesz mieć konsolę w tle, sam użyj np. `screen` / `tmux` / `nohup`:

```bash
screen -dmS start-tdm bash ~/qlds/start-tdm.sh
tmux new -d -s start-tdm "bash ~/qlds/start-tdm.sh"
```

## Wymagania

- Debian 10/11/12 lub Ubuntu 20.04/22.04/24.04 (apt-based)
- Architektura **x86_64**
- Użytkownik z `sudo` (NIE root)

## Szybka instalacja (one-liner, prosto z GitHuba)

```bash
QLX_OWNER=76561198799965164 \
RCON_PASSWORD=mojeRconHaslo \
STATS_PASSWORD=mojeStatsHaslo \
bash <(curl -fsSL https://raw.githubusercontent.com/goof3r/quakelive-server/master/install_minqlx_server.sh)
```

`bash <(curl ...)` zachowuje stdin do terminala — interaktywne pytanie
*„Dodać teraz kolejny serwer?"* zadziała. Alternatywa `curl | bash` też
zadziała, ale po prostu pominie ten prompt.

## Instalacja z klona repo (zalecana)

```bash
git clone https://github.com/goof3r/quakelive-server.git
cd quakelive-server
QLX_OWNER=76561198799965164 ./install_minqlx_server.sh
```

W tym wariancie instalator automatycznie użyje lokalnych plików z repo:

| Katalog / plik | Co robi podczas instalacji |
|---|---|
| `configs and mappool/ffa.cfg` `tdm.cfg` `ft.cfg` | Kopiowane bezpośrednio do `$QLDS_DIR/baseq3/` zamiast generowania z szablonu |
| `configs and mappool/mappool_*.txt` `access.txt` | Kopiowane do `$QLDS_DIR/baseq3/` |
| `configs and mappool/workshop.txt` | Kopiowany do `$QLDS_DIR/workshop.txt` (z komentarzami i grupowaniem) |
| `minqlx-plugins/*.py` | Kopiowane jako **ostatni krok** — nadpisują wersje z repo MinoMino/BarelyMiSSeD/tjone270 |
| `minqlx-plugins/Map_Names/` `extras/` `mbot_maps.json` | Kopiowane razem z pluginami |
| `commands.py` `serverhelp.py` `permoverride.py` | Kopiowane z katalogu obok skryptu zamiast pobierania z GitHuba |

## Konfiguracja przez zmienne środowiskowe

| Zmienna | Domyślnie | Co robi |
|---|---|---|
| `QLX_OWNER` | `76561198799965164` | **TWÓJ SteamID64 (17 cyfr)** |
| `SV_HOSTNAME` | `^2My minqlx Server` | nazwa serwera na liście |
| `NET_PORT` | `27960` | port UDP bazowego serwera |
| `RCON_PASSWORD` | `zmien_to_haslo_rcon` | hasło rcon (ZMIEŃ) |
| `STATS_PASSWORD` | `zmien_to_haslo_stats` | hasło zmq stats (ZMIEŃ) |
| `INSTALL_GAMETYPE_SERVERS` | `1` | `0` = nie instaluj serwerów FFA/TDM/FT |
| `QLDS_DIR` | `$HOME/qlds` | gdzie wyląduje serwer |

SteamID64 znajdziesz na <https://steamid.io>.

## Uruchamianie serwerów trybów

Instalator tworzy trzy skrypty startowe:

| Tryb | Port UDP | Skrypt startowy |
|---|---|---|
| TDM | 27960 | `~/qlds/start-tdm.sh` |
| FFA | 27961 | `~/qlds/start-ffa.sh` |
| FT  | 27962 | `~/qlds/start-ft.sh`  |

```bash
# Uruchom (foreground, w bieżącej sesji terminala):
bash ~/qlds/start-tdm.sh

# W tle w sesji screen (opcjonalnie):
screen -dmS start-tdm bash ~/qlds/start-tdm.sh
screen -r start-tdm            # podłącz konsolę (odłącz: Ctrl+A, D)
screen -ls                     # lista aktywnych sesji

# W tle w sesji tmux (opcjonalnie):
tmux new -d -s start-tdm "bash ~/qlds/start-tdm.sh"
tmux attach -t start-tdm       # podłącz konsolę (odłącz: Ctrl+B, D)

# Zatrzymaj: przełącz się do sesji i wpisz w konsoli minqlx  quit
```

**Po reboot** — serwery **nie** startują automatycznie. Uruchom je ręcznie
(lub dodaj sobie własny wpis w `crontab -e` z `@reboot`, jeśli chcesz).

## Definicje trybów gry (gametypes-factories)

Plik `gametypes-factories` zawiera 10 definicji trybów używanych przez serwer:

| ID | Tytuł | Bazowy tryb |
|---|---|---|
| `mg_ft_fullclassic` | Full Classic Freeze Tag | FT |
| `mg_ft_allweapons` | All Weapons Freeze Tag | FT |
| `mg_ft_promode` | Q3 Freeze Tag | FT |
| `mg_ft_uft` | Ultra Freeze Tag | FT |
| `mg_tdm_utdm` | Ultra TDM | TDM |
| `maido` | Maido | TDM |
| `sparing` | Sparing (RG & LG) | TDM |
| `mg_race_classic` | Classic Race | Race |
| `mg_ffa_aw` | All Weapons FFA | FFA |
| `mg_tdm_fullclassic` | Full Classic TDM | TDM |

Plik trafia do `$QLDS_DIR/baseq3/scripts/gametypes.factories` podczas instalacji.

## Dodawanie kolejnych serwerów

```bash
~/qlds/add_server.sh              # interaktywnie pyta o nazwę i port
~/qlds/add_server.sh duel 27970   # od razu z argumentami
```

Tworzy `baseq3/<nazwa>.cfg`, `start-<nazwa>.sh` oraz katalog
`instances/<nazwa>/`. Uruchamiasz serwer ręcznie:

```bash
bash ~/qlds/start-duel.sh
```

Otwórz w firewallu port UDP (oraz `port+1000` TCP dla rcon).

## Co instaluje skrypt

1. apt: python3-dev, redis-server, build-essential, lib32gcc, screen (pakiet do
   opcjonalnego użycia), ...
2. SteamCMD + QLDS (app 349090, login anonymous)
3. Kompilacja minqlx ze źródeł (MinoMino/minqlx)
4. Pluginy (w kolejności, ostatni wygrywa):
   - **MinoMino/minqlx-plugins** (oficjalne)
   - **BarelyMiSSeD/minqlx-plugins** (specqueue, serverBDM, kills, ...)
   - **tjone270/Quake-Live/minqlx-plugins** (q3resolver, branding, ...)
   - Pojedyncze: queue, autospec, iouonegirl, checkplayers
   - **Lokalny `minqlx-plugins/`** z repo (nadpisuje wszystko powyżej — nowsze wersje)
   - Załatany `commands.py`, `serverhelp.py`, `permoverride.py`
5. `commlink.py` (most IRC) i `changemap.py` (auto-reset mapy) są usuwane.
6. `server.cfg`, `start.sh`, `workshop.txt`
7. Konfiguracje trybów z `configs and mappool/` (ffa/tdm/ft.cfg + mapoole)
8. `gametypes.factories` (10 definicji trybów)
9. Skrypty startowe `start-tdm.sh` / `start-ffa.sh` / `start-ft.sh`
10. `add_server.sh` do późniejszych instancji

## Aktualizacja

Uruchom instalator ponownie — zaktualizuje QLDS, minqlx i wszystkie pluginy.
Twój `server.cfg` zostanie nietknięty (sync tylko linii `qlx_plugins`,
kopia jako `.bak.<timestamp>`).

## Komendy dodane przez ten installer

### Plugin `serverhelp`

| Komenda | Co robi |
|---|---|
| `!help` | Lista wszystkich dostępnych komend z poziomem uprawnień i pluginem |
| `!version` | Wersja minqlx + wersja plugin packa |
| `!perms` | Lista poziomów 0–5 + zaznaczenie Twojego aktualnego poziomu |

### Plugin `permoverride`

Pozwala zmienić poziom uprawnień dowolnej komendy bez patchowania jej pluginu.
Konfiguracja w `server.cfg`:

```
set qlx_permFor_kick    "1"   // !kick domyślnie perm 2, tu obniżamy do 1
set qlx_permFor_map     "3"   // !map tylko head-admin
```

| Komenda | Co robi |
|---|---|
| `!permset <komenda> <0-5>` | Zmiana poziomu na żywo (perm 5) |
| `!permshow <komenda>` | Aktualny poziom i plugin właściciel (perm 0) |
| `!permlist` | Lista aktywnych override'ów z `qlx_permFor_*` (perm 0) |
| `!permreload` | Ponowne wczytanie cvarów z server.cfg (perm 5) |

## Test po instalacji

```
!myperm          // pokaże poziom > 0 jeśli jesteś właścicielem
!help            // lista wszystkich komend
!version         // wersja minqlx
!perms           // poziomy uprawnień
```

## Licencja

Sam instalator i własne pluginy (`serverhelp.py`, `permoverride.py`) — bez
ograniczeń. `commands.py` to GPL-3.0 (pochodna pracy BarelyMiSSeD).
