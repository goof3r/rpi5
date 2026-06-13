# maps.py — minqlx / minqlxtended plugin
# Komenda !maps — wyswietla dostepne mapy z pliku wskazanego przez
# cvar sv_mapPoolFile (np. mappool_ft.txt).
#
# Format pliku puli (jedna mapa na linie):
#   mapname|factory      np. almostlost|mg_ft_fullclassic
#   (linie zaczynajace sie od // lub # sa pomijane)
#
# Instalacja:
#   1. Skopiuj do ~/qlds/minqlx-plugins/maps.py
#   2. Dodaj "maps" do qlx_plugins w ft.cfg
#   3. Restart serwera lub w konsoli RCON: !load maps
#
# Uzycie w grze:
#   !maps          — lista wszystkich map z puli
#   !maps <fraza>  — tylko mapy zawierajace <fraze> (filtr)

import minqlx
import os

MAPS_PER_LINE = 6        # ile nazw map w jednej linii outputu
LINE_DELAY = 0.20        # odstep miedzy liniami (s) — chroni przed overflow

class maps(minqlx.Plugin):
    def __init__(self):
        self.add_command("maps", self.cmd_maps, priority=minqlx.PRI_LOW)

    def _find_mappool_path(self):
        """Znajdz fizyczna sciezke do pliku z cvara sv_mapPoolFile."""
        fname = self.get_cvar("sv_mapPoolFile")
        if not fname:
            return None, None
        homepath = self.get_cvar("fs_homepath") or ""
        basepath = self.get_cvar("fs_basepath") or ""
        gamedir = self.get_cvar("fs_game") or "baseq3"
        candidates = [
            os.path.join(homepath, gamedir, fname),
            os.path.join(homepath, "baseq3", fname),
            os.path.join(basepath, gamedir, fname),
            os.path.join(basepath, "baseq3", fname),
            os.path.join("baseq3", fname),
            fname,
        ]
        path = next((p for p in candidates if os.path.isfile(p)), None)
        return path, fname

    def _load_maps(self, path):
        """Wczytaj i sparsuj liste map. Zwraca liste (mapname, factory)."""
        out = []
        seen = set()
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("//") or line.startswith("#"):
                    continue
                parts = line.split("|")
                mapname = parts[0].strip().lower()
                factory = parts[1].strip() if len(parts) > 1 else ""
                if mapname and mapname not in seen:
                    seen.add(mapname)
                    out.append((mapname, factory))
        return out

    @minqlx.thread
    def _send_batched(self, player, header, mapnames):
        """Wysyla liste do gracza partiami, z opoznieniem (anty-overflow)."""
        import time
        player.tell(header)
        line = []
        for name in mapnames:
            line.append(name)
            if len(line) >= MAPS_PER_LINE:
                player.tell("^7" + "  ^2".join(line))
                line = []
                time.sleep(LINE_DELAY)
        if line:
            player.tell("^7" + "  ^2".join(line))

    def cmd_maps(self, player, msg, channel):
        path, fname = self._find_mappool_path()

        if not fname:
            player.tell("^1Brak ustawionego cvara ^7sv_mapPoolFile^1.")
            return minqlx.RET_STOP_ALL
        if not path:
            player.tell("^1Nie znaleziono pliku puli map: ^7{}".format(fname))
            return minqlx.RET_STOP_ALL

        all_maps = self._load_maps(path)
        if not all_maps:
            player.tell("^1Plik ^7{}^1 nie zawiera zadnych map.".format(fname))
            return minqlx.RET_STOP_ALL

        # opcjonalny filtr: !maps <fraza>
        flt = msg[1].lower() if len(msg) > 1 else None
        if flt:
            shown = [(m, fac) for (m, fac) in all_maps if flt in m]
        else:
            shown = all_maps

        if not shown:
            player.tell("^3Brak map pasujacych do: ^7{}".format(flt))
            return minqlx.RET_STOP_ALL

        current = (self.game.map.lower() if self.game else "")
        # aktualna mapa oznaczona gwiazdka
        names = []
        for (m, fac) in shown:
            names.append("^3*{}".format(m) if m == current else m)

        if flt:
            header = "^2Mapy ^7({}/{})^2 pasujace do ^7'{}'^2:".format(
                len(shown), len(all_maps), flt)
        else:
            header = "^2Dostepne mapy ^7({})^2 z ^7{}^2:".format(
                len(all_maps), fname)

        self._send_batched(player, header, names)
        return minqlx.RET_STOP_ALL
