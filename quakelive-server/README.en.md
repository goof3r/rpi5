# Quake Live Dedicated Server + minqlx — automated installer

[🇵🇱 Polska wersja](README.md)

The `install_minqlx_server.sh` script sets up a Quake Live Dedicated Server
(QLDS) from scratch with minqlx and a full plugin pack (MinoMino +
BarelyMiSSeD + tjone270 + several external ones) on a clean Debian/Ubuntu
(x86_64) system.

**The installer only generates start scripts** (`start.sh`, `start-tdm.sh`,
`start-ffa.sh`, `start-ft.sh`, `start-<name>.sh`) — **it does NOT create
systemd services and does NOT wrap the server in a screen session**. You
start the servers manually:

```bash
bash ~/qlds/start-tdm.sh
bash ~/qlds/start-ffa.sh
bash ~/qlds/start-ft.sh
```

If you want the console detached in the background, use `screen` / `tmux` /
`nohup` yourself:

```bash
screen -dmS start-tdm bash ~/qlds/start-tdm.sh
tmux new -d -s start-tdm "bash ~/qlds/start-tdm.sh"
```

## Requirements

- Debian 10/11/12 or Ubuntu 20.04/22.04/24.04 (apt-based)
- **x86_64** architecture
- User with `sudo` rights (NOT root)

## Quick install (one-liner from GitHub)

```bash
QLX_OWNER=76561198799965164 \
RCON_PASSWORD=myRconPassword \
STATS_PASSWORD=myStatsPassword \
bash <(curl -fsSL https://raw.githubusercontent.com/goof3r/quakelive-server/master/install_minqlx_server.sh)
```

`bash <(curl ...)` keeps stdin attached to the terminal — the interactive
prompt *"Add another server now?"* will work. The `curl | bash` alternative
also works but simply skips that prompt.

## Install from a repo clone (recommended)

```bash
git clone https://github.com/goof3r/quakelive-server.git
cd quakelive-server
QLX_OWNER=76561198799965164 ./install_minqlx_server.sh
```

In this mode the installer automatically uses local files from the repo:

| Directory / file | What it does during install |
|---|---|
| `configs and mappool/ffa.cfg` `tdm.cfg` `ft.cfg` | Copied directly to `$QLDS_DIR/baseq3/` instead of generating from a template |
| `configs and mappool/mappool_*.txt` `access.txt` | Copied to `$QLDS_DIR/baseq3/` |
| `configs and mappool/workshop.txt` | Copied to `$QLDS_DIR/workshop.txt` (with comments and grouping) |
| `minqlx-plugins/*.py` | Copied as the **last step** — overwrites versions from MinoMino/BarelyMiSSeD/tjone270 |
| `minqlx-plugins/Map_Names/` `extras/` `mbot_maps.json` | Copied together with the plugins |
| `commands.py` `serverhelp.py` `permoverride.py` | Copied from the directory next to the script instead of pulling from GitHub |

## Configuration via environment variables

| Variable | Default | What it does |
|---|---|---|
| `QLX_OWNER` | `76561198799965164` | **YOUR SteamID64 (17 digits)** |
| `SV_HOSTNAME` | `^2My minqlx Server` | server name in the list |
| `NET_PORT` | `27960` | UDP port of the base server |
| `RCON_PASSWORD` | `zmien_to_haslo_rcon` | rcon password (CHANGE IT) |
| `STATS_PASSWORD` | `zmien_to_haslo_stats` | zmq stats password (CHANGE IT) |
| `INSTALL_GAMETYPE_SERVERS` | `1` | `0` = don't install the FFA/TDM/FT servers |
| `QLDS_DIR` | `$HOME/qlds` | where the server will be installed |

You can find your SteamID64 at <https://steamid.io>.

## Running the gametype servers

The installer creates three start scripts:

| Gametype | UDP port | Start script |
|---|---|---|
| TDM | 27960 | `~/qlds/start-tdm.sh` |
| FFA | 27961 | `~/qlds/start-ffa.sh` |
| FT  | 27962 | `~/qlds/start-ft.sh`  |

```bash
# Run (foreground, in the current terminal session):
bash ~/qlds/start-tdm.sh

# Detached, inside a screen session (optional):
screen -dmS start-tdm bash ~/qlds/start-tdm.sh
screen -r start-tdm            # attach console (detach: Ctrl+A, D)
screen -ls                     # list active sessions

# Detached, inside a tmux session (optional):
tmux new -d -s start-tdm "bash ~/qlds/start-tdm.sh"
tmux attach -t start-tdm       # attach console (detach: Ctrl+B, D)

# Stop: switch to the session and type in the minqlx console  quit
```

**After a reboot** — servers do **not** start automatically. Start them
manually (or add your own `@reboot` entry in `crontab -e` if you want).

## Gametype definitions (gametypes-factories)

The `gametypes-factories` file contains 10 gametype definitions used by the
server:

| ID | Title | Base gametype |
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

The file is installed to `$QLDS_DIR/baseq3/scripts/gametypes.factories`.

## Adding more servers

```bash
~/qlds/add_server.sh              # interactively asks for name and port
~/qlds/add_server.sh duel 27970   # inline arguments
```

Creates `baseq3/<name>.cfg`, `start-<name>.sh` and the `instances/<name>/`
directory. Start the server manually:

```bash
bash ~/qlds/start-duel.sh
```

Open the UDP port (and TCP `port+1000` for rcon) in your firewall.

## What the script installs

1. apt: python3-dev, redis-server, build-essential, lib32gcc, screen
   (package available for optional use), ...
2. SteamCMD + QLDS (app 349090, login anonymous)
3. Compiling minqlx from source (MinoMino/minqlx)
4. Plugins (in order — the last one wins):
   - **MinoMino/minqlx-plugins** (official)
   - **BarelyMiSSeD/minqlx-plugins** (specqueue, serverBDM, kills, ...)
   - **tjone270/Quake-Live/minqlx-plugins** (q3resolver, branding, ...)
   - Single files: queue, autospec, iouonegirl, checkplayers
   - **Local `minqlx-plugins/`** from this repo (overrides everything above — newer versions)
   - Patched `commands.py`, `serverhelp.py`, `permoverride.py`
5. `commlink.py` (IRC bridge) and `changemap.py` (auto map reset) are removed.
6. `server.cfg`, `start.sh`, `workshop.txt`
7. Gametype configs from `configs and mappool/` (ffa/tdm/ft.cfg + mappools)
8. `gametypes.factories` (10 gametype definitions)
9. Start scripts `start-tdm.sh` / `start-ffa.sh` / `start-ft.sh`
10. `add_server.sh` for future instances

## Updating

Just run the installer again — it will update QLDS, minqlx and all plugins.
Your `server.cfg` is left intact (only the `qlx_plugins` line is synced, and
a backup is saved as `.bak.<timestamp>`).

## Commands added by this installer

### `serverhelp` plugin

| Command | What it does |
|---|---|
| `!help` | Lists all available commands with permission level and owning plugin |
| `!version` | Prints minqlx version + plugin pack version |
| `!perms` | Lists permission levels 0–5 and highlights your current level |

### `permoverride` plugin

Lets you change the permission level of any command without patching its
plugin. Configuration in `server.cfg`:

```
set qlx_permFor_kick    "1"   // !kick defaults to perm 2, lowered here to 1
set qlx_permFor_map     "3"   // !map only for head-admins
```

| Command | What it does |
|---|---|
| `!permset <command> <0-5>` | Change level on the fly (perm 5) |
| `!permshow <command>` | Current level and owning plugin (perm 0) |
| `!permlist` | Lists active overrides from `qlx_permFor_*` (perm 0) |
| `!permreload` | Reloads cvars from server.cfg (perm 5) |

## Post-install test

```
!myperm          // shows a level > 0 if you are the owner
!help            // lists all commands
!version         // minqlx version
!perms           // permission levels
```

## License

The installer itself and the custom plugins (`serverhelp.py`,
`permoverride.py`) — no restrictions. `commands.py` is GPL-3.0 (derivative
of BarelyMiSSeD's work).
