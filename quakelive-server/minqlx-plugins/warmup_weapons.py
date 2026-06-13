import minqlx
import struct
import os

WEAPON_CLASSNAMES = {
    "weapon_shotgun":         "shotgun",
    "weapon_grenadelauncher": "grenade",
    "weapon_rocketlauncher":  "rocket",
    "weapon_lightning":       "lightning",
    "weapon_railgun":         "railgun",
    "weapon_plasmagun":       "plasma",
    "weapon_chaingun":        "chaingun",
    "weapon_hmg":             "hmg",
    "weapon_nailgun":         "nail",
    "weapon_prox_launcher":   "proximity",
}

# Typowe lokalizacje plików pk3/bsp w QL
BSP_SEARCH_PATHS = [
    "/home/qlive/qlds/baseq3",
    "/home/steam/ql/baseq3",
    "/home/qlive/qlds/home/baseq3",
]

def find_bsp(mapname):
    """Szuka pliku .bsp w pk3 lub bezpośrednio."""
    import zipfile
    bsp_filename = f"maps/{mapname}.bsp"

    for base in BSP_SEARCH_PATHS:
        if not os.path.isdir(base):
            continue
        # Szukaj bezpośrednio jako plik
        direct = os.path.join(base, bsp_filename)
        if os.path.isfile(direct):
            with open(direct, "rb") as f:
                return f.read()
        # Szukaj w plikach pk3 (to są zip-y)
        for fname in sorted(os.listdir(base), reverse=True):
            if fname.endswith(".pk3"):
                pk3path = os.path.join(base, fname)
                try:
                    with zipfile.ZipFile(pk3path, "r") as zf:
                        if bsp_filename in zf.namelist():
                            return zf.read(bsp_filename)
                except Exception:
                    continue
    return None

def parse_entities_from_bsp(data):
    """Wyciąga string entity z BSP (lump 0)."""
    # BSP header: 4 bajty magic + 4 bajty version + lumpy
    # Lump 0: offset 8, każdy lump to 2x int32 (offset, length)
    magic = data[:4]
    if magic not in (b"IBSP", b"RBSP", b"VBSP"):
        return []
    lump0_offset = struct.unpack_from("<i", data, 8)[0]
    lump0_length = struct.unpack_from("<i", data, 12)[0]
    entity_str = data[lump0_offset:lump0_offset + lump0_length].decode("latin-1", errors="replace")
    # Parsuj { key value } bloki
    entities = []
    for block in entity_str.split("{"):
        block = block.split("}")[0].strip()
        if not block:
            continue
        ent = {}
        for line in block.splitlines():
            line = line.strip().strip("\x00")
            if line.count('"') >= 4:
                parts = line.split('"')
                # parts: ['', key, ' ', value, '']
                if len(parts) >= 5:
                    ent[parts[1]] = parts[3]
        if ent:
            entities.append(ent)
    return entities


class warmup_weapons(minqlx.Plugin):
    def __init__(self):
        self.add_hook("new_game",     self.on_new_game)
        self.add_hook("player_spawn", self.on_player_spawn)
        self._map_weapons = set()

    def on_new_game(self):
        self._map_weapons = set()
        try:
            mapname = self.game.map.lower()
            data = find_bsp(mapname)
            if data is None:
                self.logger.warning(f"warmup_weapons: nie znaleziono BSP dla mapy '{mapname}'")
                return
            entities = parse_entities_from_bsp(data)
            for ent in entities:
                cn = ent.get("classname", "")
                if cn in WEAPON_CLASSNAMES:
                    self._map_weapons.add(WEAPON_CLASSNAMES[cn])
            self.logger.info(f"warmup_weapons: bronie na mapie {mapname}: {self._map_weapons}")
        except Exception as e:
            self.logger.error(f"warmup_weapons: błąd: {e}")

    def on_player_spawn(self, player):
        if self.game.state != "warmup":
            return
        if not self._map_weapons:
            return

        player.weapons(
            gauntlet=True,
            machinegun=True,
            shotgun=   "shotgun"   in self._map_weapons,
            grenade=   "grenade"   in self._map_weapons,
            rocket=    "rocket"    in self._map_weapons,
            lightning= "lightning" in self._map_weapons,
            railgun=   "railgun"   in self._map_weapons,
            plasma=    "plasma"    in self._map_weapons,
            chaingun=  "chaingun"  in self._map_weapons,
            hmg=       "hmg"       in self._map_weapons,
            nail=      "nail"      in self._map_weapons,
            proximity= "proximity" in self._map_weapons,
            bfg=False,
        )