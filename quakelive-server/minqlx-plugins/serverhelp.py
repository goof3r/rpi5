# serverhelp.py — minqlx plugin
# Copyright (C) 2026 goof3r
#
# Licencja: GPL-3.0-or-later (zgodnie z minqlx i ekosystemem pluginów).
#
# Przejmuje komendy !help, !version, !perms tak, żeby:
#   !help     -> lista WSZYSTKICH dostępnych komend, jedna pod drugą
#                (domyślnie tylko te, na które gracz ma uprawnienia;
#                cvar qlx_serverhelpShowAll = 1 pokazuje absolutnie wszystkie).
#   !version  -> wersja minqlx + wersja MinoMino/minqlx-plugins
#                (czyli to, co domyślnie pokazywał !help w essentials).
#   !perms    -> lista poziomów uprawnień 0..5 z polskimi etykietami
#                + wskazanie BIEŻĄCEGO poziomu gracza.
#
# Dlaczego nadpisujemy, a nie patchujemy essentials.py:
#   W MinoMino/essentials.py jeden handler cmd_help obsługuje aliasy
#   help/about/version i pokazuje TYLKO wersję minqlx. Zamiast utrzymywać
#   własny fork essentials, rejestrujemy własne handlery z priorytetem
#   PRI_HIGH i zwracamy minqlx.RET_STOP_ALL — wtedy minqlx nie woła już
#   handlera essentials dla tych komend. Alias !about zostaje w essentials
#   (jako bezpiecznik — można nadal sprawdzić wersję, jeśli ktoś zna stary
#   alias).
#
# Crash-safe wysyłka (jak w commands.py v1.1):
#   QL engine ma ograniczony bufor "reliable" command'ów na klatkę. Wysłanie
#   ~60 player.tell() jednym strzałem przepełnia ten bufor i pada CAŁY QLDS
#   (systemd to potem podnosi -> użytkownik widzi "reset"). Dlatego pełną
#   listę pakujemy w kilka kawałków po MAX_TELL_LEN znaków i wysyłamy
#   MSGS_PER_FRAME na klatkę, rozkładając resztę przez @minqlx.delay.

"""
//Konfiguracja w server.cfg:
//Pokazuj WSZYSTKIE komendy, łącznie z tymi do których gracz nie ma uprawnień
//(0 = pokazuj tylko dostępne dla gracza, 1 = pokazuj wszystkie).
set qlx_serverhelpShowAll "0"
"""

import minqlx

VERSION = "1.0"

# Limity wysyłki — takie same jak w załatanym commands.py v1.1.
MAX_TELL_LEN = 900
MSGS_PER_FRAME = 3
BATCH_DELAY = 0.15

# minqlx nie definiuje nazw poziomów — to konwencja przyjęta przez społeczność
# (MinoMino permission plugin: setperm <id> <0..5>). Trzymamy nazwy tutaj, żeby
# ujednolicić output !perms; admini mogą podmienić wartości stałej PERM_LEVEL_NAMES.
PERM_LEVEL_NAMES = {
    0: "gracz",
    1: "moderator (mod)",
    2: "admin",
    3: "senior admin",
    4: "head admin",
    5: "owner (root)",
}


class serverhelp(minqlx.Plugin):
    def __init__(self):
        self.set_cvar_once("qlx_serverhelpShowAll", "0")

        # PRI_HIGH + RET_STOP_ALL przejmuje !help/!version sprzed essentials,
        # niezależnie od kolejności qlx_plugins. !perms nie koliduje z niczym
        # z domyślnego stacka (jest tylko !myperm w permission.py).
        self.add_command("help", self.cmd_help, priority=minqlx.PRI_HIGH)
        self.add_command("version", self.cmd_version, priority=minqlx.PRI_HIGH)
        self.add_command("perms", self.cmd_perms)

    # ------------------------------------------------------------------ #
    #  Throttled tell — kopia idiomu z commands.py v1.1.
    # ------------------------------------------------------------------ #
    def _send_lines(self, player, lines):
        chunks = []
        buf = ""
        for line in lines:
            piece = line + "\n"
            if buf and len(buf) + len(piece) > MAX_TELL_LEN:
                chunks.append(buf.rstrip("\n"))
                buf = ""
            buf += piece
        if buf:
            chunks.append(buf.rstrip("\n"))
        self._send_chunks(player, chunks, 0)

    def _send_chunks(self, player, chunks, index):
        if index >= len(chunks):
            return
        end = min(index + MSGS_PER_FRAME, len(chunks))
        for i in range(index, end):
            try:
                player.tell(chunks[i])
            except Exception:
                return
        if end < len(chunks):
            @minqlx.delay(BATCH_DELAY)
            def _continue():
                self._send_chunks(player, chunks, end)
            _continue()

    # ------------------------------------------------------------------ #
    #  !help — lista wszystkich komend, jedna pod drugą.
    # ------------------------------------------------------------------ #
    def cmd_help(self, player, msg, channel):
        try:
            caller_perm = self._get_perm(player)
        except Exception:
            caller_perm = 0

        show_all = self.get_cvar("qlx_serverhelpShowAll", bool)
        prefix = self.get_cvar("qlx_commandPrefix") or "!"

        # Deduplikacja po kanonicznej nazwie (cmd.name[0]); aliasy zachowujemy
        # do pokazania w linii ("help/about/version" itd.).
        seen = {}
        for cmd in minqlx.COMMANDS.commands:
            names = getattr(cmd, "name", None)
            if not names:
                continue
            canonical = names[0]
            if canonical in seen:
                continue
            perm = getattr(cmd, "permission", 0) or 0
            if (not show_all) and perm > caller_perm:
                continue
            plugin_obj = getattr(cmd, "plugin", None)
            plugin_name = type(plugin_obj).__name__ if plugin_obj else "core"
            usage = getattr(cmd, "usage", "") or ""
            seen[canonical] = (perm, list(names), plugin_name, usage)

        if not seen:
            player.tell("^3Brak dostępnych komend do wyświetlenia.")
            return minqlx.RET_STOP_ALL

        lines = [
            "^3Komendy dostępne na serwerze ^7({}^3, twój poziom: ^6{}^3)^3:".format(
                len(seen), caller_perm
            ),
        ]
        for canonical in sorted(seen):
            perm, names, plugin_name, usage = seen[canonical]
            aliases = "/".join(names) if len(names) > 1 else canonical
            line = "^7{}{} ^8perm:{} ^4[{}]".format(prefix, aliases, perm, plugin_name)
            if usage:
                line += " ^7{}".format(usage)
            lines.append(line)
        lines.append(
            "^3Wersja serwera: ^6{0}version^3   Twoje uprawnienia: ^6{0}perms".format(
                prefix
            )
        )

        self._send_lines(player, lines)
        return minqlx.RET_STOP_ALL

    # ------------------------------------------------------------------ #
    #  !version — wersja minqlx + pluginów MinoMino.
    # ------------------------------------------------------------------ #
    def cmd_version(self, player, msg, channel):
        mv = getattr(minqlx, "__version__", "?")
        pv = getattr(minqlx, "__plugins_version__", "?")
        player.tell("^7minqlx: ^6{}^7   minqlx-plugins: ^6{}".format(mv, pv))
        player.tell("^7serverhelp: ^6{}^7   ^8github.com/MinoMino/minqlx".format(VERSION))
        return minqlx.RET_STOP_ALL

    # ------------------------------------------------------------------ #
    #  !perms — poziomy 0..5 + bieżący poziom gracza.
    # ------------------------------------------------------------------ #
    def cmd_perms(self, player, msg, channel):
        my_perm = self._get_perm(player)
        is_owner = False
        try:
            is_owner = (minqlx.owner() == player.steam_id)
        except Exception:
            pass

        prefix = self.get_cvar("qlx_commandPrefix") or "!"
        lines = ["^3Poziomy uprawnień minqlx (^60^3..^65^3):"]
        for lvl in range(6):
            label = PERM_LEVEL_NAMES.get(lvl, "?")
            marker = "  ^2<- Ty" if lvl == my_perm else ""
            lines.append("^6{}^7  {}{}".format(lvl, label, marker))
        if is_owner:
            lines.append("^3Jesteś też ^6właścicielem ^3serwera (qlx_owner = Twój SteamID).")
        lines.append(
            "^3Poziom innego gracza nadaje owner: ^6{}setperm <id> <0-5>".format(prefix)
        )
        self._send_lines(player, lines)
        return minqlx.RET_STOP_ALL

    # ------------------------------------------------------------------ #
    #  Odczyt poziomu uprawnień (defensywny — działa też bez redisa).
    # ------------------------------------------------------------------ #
    def _get_perm(self, player):
        # Owner zawsze 5, niezależnie od bazy.
        try:
            if minqlx.owner() == player.steam_id:
                return 5
        except Exception:
            pass
        # Standardowe źródło: permission plugin (redis).
        try:
            db = getattr(self, "db", None)
            if db is not None:
                level = db.get_permission(player)
                if level is not None:
                    return int(level)
        except Exception:
            pass
        # Fallback: privileges z silnika (None / "mod" / "admin" / "root").
        priv = getattr(player, "privileges", None) or ""
        return {"root": 5, "admin": 3, "mod": 1}.get(priv, 0)
