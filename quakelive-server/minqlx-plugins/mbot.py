# mbot.py  -  minqlx / minqlxtended
# ---------------------------------------------------------------------------
# System veto map (dropowanie) dla zawodow / turniejow / ligi.
# Calym procesem zarzadza bot "mbot".
#
# Przeplyw:
#   !mbot start          -> start procesu; PIERWSZY gracz z druzyny (RED/BLUE),
#                           ktory wpisze te komende, zostaje osoba wybierajaca
#                           zestaw map
#   !mbot s1|s2|s3|s4    -> wybor zestawu (tylko gracz ktory rozpoczal / admin)
#   !drop <mapa>         -> druzyny NA PRZEMIAN usuwaja mapy; RED zaczyna
#   ... gdy zostanie 1 mapa -> bot robi:  callvote map <ostatnia_mapa>
#
# Komendy pomocnicze:
#   !mbot status         -> aktualny stan veto (dla wszystkich)
#   !mbot help           -> krotka pomoc
#   !mbot stop|reset     -> anulowanie procesu [admin]
#   !mbot maps           -> przeladowanie zestawow z pliku JSON [admin]
#
# Cvary:
#   qlx_mbotPerm       (5)  poziom admina: reset/stop/maps oraz mozliwosc
#                           wyboru zestawu za gracza (override sedziego)
#   qlx_mbotForceVote  (0)  1 = bot automatycznie przepuszcza glosowanie
#   qlx_mbotVoteTime   (30) czas glosowania w sekundach
#
# Zestawy map: plik  mbot_maps.json  obok pluginu (tworzony automatycznie,
# mozna edytowac dowolnie - liczba map na zestaw moze byc inna niz 7).
# ---------------------------------------------------------------------------

import minqlx
import os
import json

MBOT_VERSION = "1.0"
TAG = "^2[mbot]^7"

DEFAULT_MAPSETS = {
    "s1": ["bloodrun", "furiousheights", "campgrounds", "aerowalk",
           "toxicity", "hektik", "lostworld"],
    "s2": ["asylum", "spillway", "ironworks", "campgrounds",
           "troubledwaters", "houseofdecay", "overkill"],
    "s3": ["cure", "almostlost", "battleforged", "hiddenfortress",
           "dismemberment", "silence", "deepinside"],
    "s4": ["bloodrun", "aerowalk", "cure", "toxicity",
           "furiousheights", "hektik", "campgrounds"],
}


class mbot(minqlx.Plugin):
    def __init__(self):
        super().__init__()

        self.set_cvar_once("qlx_mbotPerm", "5")
        self.set_cvar_once("qlx_mbotForceVote", "0")
        self.set_cvar_once("qlx_mbotVoteTime", "30")

        self.add_command("mbot", self.cmd_mbot,
                         usage="start|s1|s2|s3|s4|status|stop|reset|maps|help")
        self.add_command("drop", self.cmd_drop, usage="<mapa>")

        # po wczytaniu nowej mapy czyscimy proces veto
        self.add_hook("map", self.handle_map)

        self._mapsets = self.load_mapsets()
        self.reset_state()

        minqlx.console_print("mbot v{}: zaladowano, zestawy: {}".format(
            MBOT_VERSION, ", ".join(sorted(self._mapsets))))

    # ------------------------------------------------------------------ stan
    def reset_state(self):
        self._state = "idle"     # idle | choose | drop | done
        self._set = None
        self._pool = []
        self._turn = "red"       # RED zaczyna
        self._history = []
        self._picker_id = None   # steam_id gracza ktory wybiera zestaw
        self._picker_name = None
        self._picker_team = None

    def handle_map(self, mapname, factory):
        if self._state != "idle":
            self.reset_state()

    # -------------------------------------------------------------- mapsety
    def maps_file(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "mbot_maps.json")

    def load_mapsets(self):
        path = self.maps_file()
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                clean = {}
                for k, v in data.items():
                    if isinstance(v, list) and len(v) >= 2:
                        clean[str(k).lower()] = [str(m).lower() for m in v]
                if clean:
                    return clean
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_MAPSETS, f, indent=4)
                minqlx.console_print("mbot: utworzono domyslny {}".format(path))
        except Exception as e:
            minqlx.console_print("mbot: blad pliku mapsetow: {}".format(e))
        return dict(DEFAULT_MAPSETS)

    # ------------------------------------------------------------ uprawnienia
    def perm_of(self, player):
        try:
            owner = self.get_cvar("qlx_owner")
            if owner and str(player.steam_id) == str(owner):
                return 5
        except Exception:
            pass
        try:
            key = "minqlx:players:{}:permission".format(player.steam_id)
            if self.db is not None and key in self.db:
                return int(self.db[key])
        except Exception:
            pass
        return 0

    def is_admin(self, player):
        return self.perm_of(player) >= self.get_cvar("qlx_mbotPerm", int)

    # ---------------------------------------------------------- komenda !mbot
    def cmd_mbot(self, player, msg, channel):
        if len(msg) < 2:
            return self.show_help(channel)

        action = msg[1].lower()

        if action in ("status", "stan"):
            return self.show_status(channel)
        if action in ("help", "pomoc", "?"):
            return self.show_help(channel)

        # start: moze wywolac KAZDY gracz z druzyny; pierwszy ktory to zrobi
        # zostaje osoba wybierajaca zestaw map
        if action == "start":
            return self.start_veto(player, channel)

        # wybor zestawu: tylko gracz ktory rozpoczal veto (lub admin)
        if action in self._mapsets:
            return self.choose_set(player, action, channel)

        # operacje administracyjne (przerwanie / przeladowanie map)
        if action in ("stop", "reset", "cancel", "maps"):
            if not self.is_admin(player):
                channel.reply("{} Ta komenda tylko dla admina (poziom {}+).".format(
                    TAG, self.get_cvar("qlx_mbotPerm")))
                return
            if action == "maps":
                self._mapsets = self.load_mapsets()
                channel.reply("{} Wczytano zestawy: {}".format(
                    TAG, ", ".join(sorted(self._mapsets))))
            else:
                self.reset_state()
                self.msg("{} Proces veto zostal anulowany.".format(TAG))
            return

        self.show_help(channel)

    # ----------------------------------------------------------------- start
    def start_veto(self, player, channel):
        # nie pozwalamy restartowac trwajacego procesu (ochrona przed grieferami)
        if self._state in ("choose", "drop"):
            if self._picker_name:
                channel.reply("{} Veto juz trwa (zaczal: {}). "
                              "Admin moze przerwac: ^2!mbot reset^7.".format(
                                  TAG, self._picker_name))
            else:
                channel.reply("{} Veto juz trwa. Admin moze przerwac: "
                              "^2!mbot reset^7.".format(TAG))
            return

        # start moze wywolac tylko gracz z druzyny (RED/BLUE); admin - zawsze
        if player.team not in ("red", "blue") and not self.is_admin(player):
            channel.reply("{} Musisz byc w druzynie (RED/BLUE), zeby "
                          "rozpoczac veto.".format(TAG))
            return

        self.reset_state()
        self._state = "choose"
        self._picker_id = player.steam_id
        self._picker_name = player.clean_name
        self._picker_team = player.team if player.team in ("red", "blue") else None

        self.msg("{} ^3Rozpoczynam veto map.^7".format(TAG))
        if self._picker_team:
            self.msg("{} ^7{} ({}) ^7wybiera zestaw map.".format(
                TAG, player.clean_name, self.team_col(self._picker_team)))
        else:
            self.msg("{} ^7{} wybiera zestaw map.".format(TAG, player.clean_name))
        sets = " ".join("^5{}^7({})".format(k, len(v))
                        for k, v in sorted(self._mapsets.items()))
        self.msg("{} Wpisz ^2!mbot <zestaw>^7   dostepne: {}".format(TAG, sets))

    # --------------------------------------------------------- wybor zestawu
    def choose_set(self, player, setname, channel):
        if self._state != "choose":
            channel.reply("{} Najpierw uzyj ^2!mbot start^7.".format(TAG))
            return
        # zestaw wybiera tylko ten, kto rozpoczal veto (lub admin)
        if player.steam_id != self._picker_id and not self.is_admin(player):
            who = self._picker_name or "gracz ktory rozpoczal"
            channel.reply("{} Zestaw wybiera {}.".format(TAG, who))
            return
        self._set = setname
        self._pool = list(self._mapsets[setname])
        self._turn = "red"
        self._history = []
        self._state = "drop"
        self.msg("{} Zestaw ^5{}^7 - pula map ({}):".format(
            TAG, setname, len(self._pool)))
        self.announce_pool()
        self.msg("{} Druzyny na przemian usuwaja mapy: ^2!drop <mapa>^7 "
                 "(zaczyna ^1RED^7).".format(TAG))
        self.announce_turn()

    # ----------------------------------------------------------- komenda !drop
    def cmd_drop(self, player, msg, channel):
        if self._state != "drop":
            channel.reply("{} Teraz nie trwa dropowanie map.".format(TAG))
            return
        if len(msg) < 2:
            channel.reply("{} Uzycie: ^2!drop <mapa>^7".format(TAG))
            return

        admin = self.is_admin(player)
        if player.team != self._turn and not admin:
            channel.reply("{} Teraz dropuje druzyna {}. Poczekaj na swoja "
                          "ture.".format(TAG, self.team_col(self._turn)))
            return

        target, err = self.match_map(msg[1])
        if target is None:
            channel.reply("{} {}".format(TAG, err))
            return

        self._pool.remove(target)
        self._history.append((self._turn, target))
        self.msg("{} {} ^7({}) dropuje ^3{}^7.".format(
            TAG, self.team_col(self._turn), player.clean_name, target))

        if len(self._pool) == 1:
            self.announce_pool()
            self.finish_veto()
            return

        self._turn = "blue" if self._turn == "red" else "red"
        self.announce_pool()
        self.announce_turn()

    # ------------------------------------------------------------- zakonczenie
    def finish_veto(self):
        final = self._pool[0]
        self._state = "done"
        self.msg("{} ^3VETO ZAKONCZONE.^7 Mapa meczu: ^2{}^7".format(TAG, final))
        self.msg("{} Wywoluje: ^5callvote map {}^7".format(TAG, final))
        minqlx.console_print("mbot: veto -> map {}".format(final))

        vote = "map {}".format(final)
        disp = "Zagraj mape {} ?".format(final)
        vote_time = self.get_cvar("qlx_mbotVoteTime", int)
        try:
            self.callvote(vote, disp, vote_time)
        except Exception:
            try:
                self.callvote(vote, disp)
            except Exception as e:
                minqlx.console_print("mbot: blad callvote: {}".format(e))
                self.msg("{} Nie udalo sie wywolac glosowania.".format(TAG))
                return

        if self.get_cvar("qlx_mbotForceVote", bool):
            @minqlx.delay(2)
            def _force():
                try:
                    minqlx.force_vote(True)
                    self.msg("{} Glosowanie przepuszczone automatycznie.".format(TAG))
                except Exception as e:
                    minqlx.console_print("mbot: force_vote: {}".format(e))
            _force()

    # ---------------------------------------------------------------- pomocne
    def match_map(self, token):
        token = token.lower()
        if token in self._pool:
            return token, None
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(self._pool):
                return self._pool[idx], None
            return None, "Nieprawidlowy numer mapy."
        matches = [m for m in self._pool if token in m]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, "Niejednoznaczne: " + ", ".join(matches)
        return None, "Nie ma takiej mapy w puli."

    def team_col(self, team):
        return "^1RED^7" if team == "red" else "^4BLUE^7"

    def announce_pool(self):
        line = "  ".join("^3{}.^5{}^7".format(i + 1, m)
                         for i, m in enumerate(self._pool))
        self.msg("{} {}".format(TAG, line))

    def announce_turn(self):
        if len(self._pool) <= 1:
            return
        self.msg("{} Tura: {} -> ^2!drop <mapa>^7".format(
            TAG, self.team_col(self._turn)))

    def show_status(self, channel):
        if self._state == "idle":
            channel.reply("{} Brak aktywnego veto. Start: ^2!mbot start^7".format(TAG))
        elif self._state == "choose":
            who = self._picker_name or "?"
            channel.reply("{} Zestaw wybiera {} (^2!mbot s1..s4^7).".format(TAG, who))
        elif self._state == "drop":
            channel.reply("{} Zestaw ^5{}^7, pozostalo {} map:".format(
                TAG, self._set, len(self._pool)))
            self.announce_pool()
            self.announce_turn()
        elif self._state == "done":
            m = self._pool[0] if self._pool else "?"
            channel.reply("{} Veto zakonczone. Mapa: ^2{}^7".format(TAG, m))

    def show_help(self, channel):
        channel.reply("{} mbot v{} - veto map dla turniejow/ligi.".format(
            TAG, MBOT_VERSION))
        channel.reply("{} ^2!mbot start^7 -> ^2!mbot s1..s4^7 -> "
                      "^2!drop <mapa>^7  | ^2!mbot status^7 | ^2!mbot reset^7".format(TAG))
