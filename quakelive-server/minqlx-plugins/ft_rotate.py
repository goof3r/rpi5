# ft_rotate.py — minqlx plugin
# Po zakonczeniu meczu losuje nastepna mape z mappool_ft.txt
# i ZAWSZE laduje ja z factory mg_ft_fullclassic (gametype = ft).
#
# Rozwiazuje problem: QLDS po wylaczonym endgame vote (g_voteFlags bit 2048)
# ma wlasna losowa rotacje ktora ignoruje factory i moze przelaczyc na FFA/TDM.
# Ten plugin przejmuje kontrole nad nastepna mapa.
#
# Instalacja:
#   1. Skopiuj do ~/qlds/minqlx-plugins/ft_rotate.py
#   2. Dodaj "ft_rotate" do qlx_plugins w ft.cfg
#   3. Restart serwera (lub "!load ft_rotate" w konsoli)

import minqlx
import random
import os

FACTORY = "mg_ft_fullclassic"
MAPPOOL_FILE = "mappool_ft.txt"

class ft_rotate(minqlx.Plugin):
    def __init__(self):
        self.add_hook("game_end", self.handle_game_end)
        self.set_cvar_once("qlx_ftRotateFactory", FACTORY)
        self.set_cvar_once("qlx_ftRotateMappool", MAPPOOL_FILE)

    def _load_mappool(self):
        """Czyta mappool_ft.txt i zwraca liste nazw map (tylko z factory ft)."""
        factory = self.get_cvar("qlx_ftRotateFactory") or FACTORY
        fname = self.get_cvar("qlx_ftRotateMappool") or MAPPOOL_FILE
        # baseq3 path — minqlx udostepnia fs_homepath / fs_basepath
        homepath = self.get_cvar("fs_homepath") or ""
        candidates = [
            os.path.join(homepath, "baseq3", fname),
            os.path.join("baseq3", fname),
            fname,
        ]
        path = next((p for p in candidates if os.path.isfile(p)), None)
        if not path:
            return []
        maps = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("//") or line.startswith("#"):
                    continue
                # format: mapname|factory
                parts = line.split("|")
                if len(parts) < 2:
                    continue
                mapname = parts[0].strip().lower()
                fac = parts[1].strip()
                # Bierzemy tylko mapy z naszym FT factory
                if fac == factory and mapname:
                    maps.append(mapname)
        return maps

    @minqlx.delay(8)  # poczekaj az skoncza sie statystyki endgame
    def _rotate(self):
        maps = self._load_mappool()
        factory = self.get_cvar("qlx_ftRotateFactory") or FACTORY
        if not maps:
            self.logger.warning("ft_rotate: mappool pusty lub nie znaleziony — pomijam rotacje.")
            return
        current = (self.game.map if self.game else "").lower()
        # unikaj powtorzenia tej samej mapy jesli sa inne dostepne
        choices = [m for m in maps if m != current] or maps
        nextmap = random.choice(choices)
        self.logger.info("ft_rotate: laduje {} z factory {}".format(nextmap, factory))
        minqlx.console_command("map {} {}".format(nextmap, factory))

    def handle_game_end(self, data):
        # tylko gdy mecz faktycznie sie zakonczyl (nie abort/warmup)
        try:
            if not data or data.get("ABORTED"):
                return
        except Exception:
            pass
        self._rotate()
