# permoverride.py — minqlx plugin
# Copyright (C) 2026 goof3r
#
# Licencja: GPL-3.0-or-later.
#
# Pozwala nadpisać poziom uprawnień (qlx perm) dowolnej komendy bez patchowania
# pluginu właściciela. Działa przez bezpośrednią mutację pola `.permission` na
# obiektach `minqlx.Command` po wystartowaniu serwera.
#
# Konfiguracja w server.cfg — cvar per komenda:
#
#     set qlx_permFor_kick     "3"
#     set qlx_permFor_map      "1"
#     set qlx_permFor_shuffle  "2"
#
# Plugin przy starcie iteruje wszystkie komendy z minqlx.COMMANDS i dla każdej
# sprawdza, czy istnieje cvar qlx_permFor_<alias>. Jeśli tak — przepisuje
# `.permission`. Walidacja: 0..5 (zakres minqlx permission plugin).
#
# Komendy w grze:
#     !permset <komenda> <0-5>   — zmiana na żywo (perm 5)
#     !permshow <komenda>        — pokazuje aktualny poziom (perm 0)
#     !permlist                  — wszystkie aktywne override'y z cvarów (perm 0)
#     !permreload                — ponowne wczytanie cvarów (perm 5)
#                                  (przydatne po qlx_loadPlugin <nowy>)
#
# Plugin powinien być ŁADOWANY JAKO OSTATNI w qlx_plugins, żeby override leciał
# już po tym jak inne pluginy zarejestrowały swoje komendy. Installer dba o to
# automatycznie (serverhelp + permoverride są na końcu listy).

"""
//Konfiguracja w server.cfg — przykłady (zakomentowane):
//set qlx_permFor_kick    "3"   // !kick z 2 (default essentials) na 3
//set qlx_permFor_map     "1"   // !map ze standardowego 2 na 1
//set qlx_permFor_shuffle "2"   // !shuffle z 1 na 2
"""

import minqlx

VERSION = "1.0"


class permoverride(minqlx.Plugin):
    def __init__(self):
        self.add_command("permset", self.cmd_permset, 5, usage="<komenda> <0-5>")
        self.add_command("permshow", self.cmd_permshow, 0, usage="<komenda>")
        self.add_command("permlist", self.cmd_permlist, 0)
        self.add_command("permreload", self.cmd_permreload, 5)

        # Re-apply, gdy ktoś załaduje plugin po starcie serwera
        # (np. przez `qlx_loadPlugin`). Hook 'loaded' jest standardowy w minqlx.
        try:
            self.add_hook("loaded", self.handle_plugin_loaded)
        except Exception:
            # Defensywnie — jeśli wersja minqlx nie ma tego eventu,
            # zostaje ręczny !permreload.
            pass

        self._apply_overrides(verbose=True)

    # ------------------------------------------------------------------ #
    #  Wewnętrzne — szukanie i aplikacja override'ów.
    # ------------------------------------------------------------------ #
    def _find_command(self, name):
        """Zwraca pierwszą komendę, której którykolwiek alias == name (lower)."""
        name = (name or "").strip().lower()
        if not name:
            return None
        for cmd in minqlx.COMMANDS.commands:
            if name in (getattr(cmd, "name", None) or []):
                return cmd
        return None

    def _validate_level(self, raw):
        """Zwraca int 0..5 albo None gdy walidacja nie przejdzie."""
        try:
            lvl = int(raw)
        except (TypeError, ValueError):
            return None
        if lvl < 0 or lvl > 5:
            return None
        return lvl

    def _apply_overrides(self, verbose=False):
        """Iteruje po wszystkich Command'ach i sprawdza qlx_permFor_<alias>.
        Pierwszy znaleziony alias z ustawionym cvarem wygrywa — kolejne alisy
        tej samej komendy są pomijane (żeby unikać konfliktu)."""
        changes = 0
        for cmd in minqlx.COMMANDS.commands:
            aliases = getattr(cmd, "name", None) or []
            applied_alias = None
            for alias in aliases:
                cvar_name = "qlx_permFor_{}".format(alias)
                raw = self.get_cvar(cvar_name)
                if raw is None or raw.strip() == "":
                    continue
                lvl = self._validate_level(raw)
                if lvl is None:
                    if verbose:
                        minqlx.console_print(
                            "[permoverride] {} = {!r} — niewłaściwe (oczekuję 0-5), pomijam.".format(
                                cvar_name, raw
                            )
                        )
                    applied_alias = alias  # zaznaczamy, żeby nie sprawdzać dalej
                    break
                if cmd.permission != lvl:
                    old = cmd.permission
                    cmd.permission = lvl
                    changes += 1
                    if verbose:
                        minqlx.console_print(
                            "[permoverride] {} ({}) perm {} -> {}".format(
                                "/".join(aliases),
                                type(cmd.plugin).__name__ if cmd.plugin else "core",
                                old,
                                lvl,
                            )
                        )
                applied_alias = alias
                break
            # jeśli kilka aliasów ma ustawiony cvar, ostrzegamy
            if applied_alias is not None:
                for other in aliases:
                    if other == applied_alias:
                        continue
                    other_raw = self.get_cvar("qlx_permFor_{}".format(other))
                    if other_raw is not None and other_raw.strip() != "":
                        if verbose:
                            minqlx.console_print(
                                "[permoverride] uwaga: qlx_permFor_{} ustawione, "
                                "ale qlx_permFor_{} już zadziałało dla tej samej "
                                "komendy — ignoruję.".format(other, applied_alias)
                            )
        if verbose:
            minqlx.console_print(
                "[permoverride] zastosowano override'ów: {}".format(changes)
            )
        return changes

    # ------------------------------------------------------------------ #
    #  Hook: re-apply przy załadowaniu nowego pluginu.
    # ------------------------------------------------------------------ #
    def handle_plugin_loaded(self, plugin):
        # Nowy plugin mógł dorzucić komendy — sprawdzamy jeszcze raz.
        # verbose=False, żeby nie zaśmiecać konsoli przy każdym loadzie.
        try:
            self._apply_overrides(verbose=False)
        except Exception as e:
            minqlx.console_print("[permoverride] re-apply po loaded({}) wywalił: {}".format(plugin, e))

    # ------------------------------------------------------------------ #
    #  !permset <komenda> <0-5>
    # ------------------------------------------------------------------ #
    def cmd_permset(self, player, msg, channel):
        if len(msg) < 3:
            return minqlx.RET_USAGE
        name = msg[1].lower()
        lvl = self._validate_level(msg[2])
        if lvl is None:
            channel.reply("^1Niewłaściwy poziom: ^7{} ^1(oczekuję 0-5).".format(msg[2]))
            return
        cmd = self._find_command(name)
        if cmd is None:
            channel.reply("^1Nie znaleziono komendy: ^7{}".format(name))
            return
        old = cmd.permission
        cmd.permission = lvl
        aliases = "/".join(getattr(cmd, "name", None) or [name])
        plugin_name = type(cmd.plugin).__name__ if cmd.plugin else "core"
        channel.reply(
            "^7{} ^3[{}] ^3perm: ^6{} -> {}^3 (do najbliższego restartu lub override z cvar).".format(
                aliases, plugin_name, old, lvl
            )
        )

    # ------------------------------------------------------------------ #
    #  !permshow <komenda>
    # ------------------------------------------------------------------ #
    def cmd_permshow(self, player, msg, channel):
        if len(msg) < 2:
            return minqlx.RET_USAGE
        name = msg[1].lower()
        cmd = self._find_command(name)
        if cmd is None:
            channel.reply("^1Nie znaleziono komendy: ^7{}".format(name))
            return
        aliases = "/".join(getattr(cmd, "name", None) or [name])
        plugin_name = type(cmd.plugin).__name__ if cmd.plugin else "core"
        channel.reply(
            "^7{} ^3[{}] ^3perm: ^6{}".format(aliases, plugin_name, cmd.permission)
        )

    # ------------------------------------------------------------------ #
    #  !permlist — pokaż wszystkie aktywne override'y z cvarów.
    # ------------------------------------------------------------------ #
    def cmd_permlist(self, player, msg, channel):
        rows = []
        for cmd in minqlx.COMMANDS.commands:
            for alias in (getattr(cmd, "name", None) or []):
                raw = self.get_cvar("qlx_permFor_{}".format(alias))
                if raw is None or raw.strip() == "":
                    continue
                lvl = self._validate_level(raw)
                state = "perm {}".format(lvl) if lvl is not None else "INVALID ({})".format(raw)
                rows.append("^7{} ^3-> ^6{}".format(alias, state))
                break  # pierwszy alias z cvarem reprezentuje komendę
        if not rows:
            channel.reply("^3Brak override'ów (cvary qlx_permFor_* nie ustawione).")
            return
        channel.reply("^3Aktywne override'y uprawnień ^7({}^3):".format(len(rows)))
        for r in rows:
            channel.reply(r)

    # ------------------------------------------------------------------ #
    #  !permreload — ponowne wczytanie cvarów (np. po edycji server.cfg).
    # ------------------------------------------------------------------ #
    def cmd_permreload(self, player, msg, channel):
        n = self._apply_overrides(verbose=True)
        channel.reply("^3permoverride: zastosowano override'ów: ^6{}^3 (sprawdź konsolę po szczegóły).".format(n))
