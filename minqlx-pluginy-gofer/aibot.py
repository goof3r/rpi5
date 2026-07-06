# aibot.py — minqlx plugin z integracją Claude API (Anthropic)
# Copyright (C) 2026 goof3r
# Licencja: GPL-3.0-or-later
#
# Trzy funkcje w jednym pluginie:
#   1) !ai <pytanie>         — konwersacyjny czat (per-gracz historia w Redisie)
#   2) Q&A o serwerze        — kontekst z pliku aibot_context.txt wstrzykiwany
#                              do system-promptu (z prompt cachingiem)
#   3) smart onjoin          — jedno-zdaniowe powitanie generowane pod gracza
#                              (na podstawie SteamID + historii wizyt)
#
# Zależności: tylko stdlib (urllib, json). Bez SDK Anthropic, żeby nie
# instalować pipem niczego na serwerze QL.
#
# Konfiguracja — cvary w server.cfg:
#   set qlx_aiBotEnabled           "1"       // 0 wyłącza cały plugin
#   set qlx_aiBotApiKey            ""        // preferuj ENV ANTHROPIC_API_KEY
#   set qlx_aiBotModel             "claude-haiku-4-5-20251001"
#   set qlx_aiBotMaxTokens         "400"
#   set qlx_aiBotCooldownSec       "15"      // per SteamID
#   set qlx_aiBotGlobalCooldownSec "3"       // globalny anty-spam
#   set qlx_aiBotHistoryTurns      "3"       // ile ostatnich wymian pamięta
#   set qlx_aiBotSmartOnjoin       "1"       // powitanie AI on-connect
#   set qlx_aiBotDailyBudgetUSD    "1.00"    // miękki cap dzienny (soft)
#   set qlx_aiBotContextFile       ""        // ścieżka do aibot_context.txt
#                                            // domyślnie obok pluginu
#
# Komendy w grze:
#   !ai <pytanie>              perm 0   — zapytaj bota
#   !ai_reset                  perm 0   — wyczyść własną historię
#   !ai_stats                  perm 0   — twoje zużycie tokenów
#   !ai_reload                 perm 5   — przeładuj plik kontekstu
#   !ai_budget                 perm 5   — dzienny licznik $
#   !ai_toggle                 perm 5   — on/off bez restartu

import json
import os
import time
import urllib.request
import urllib.error

import minqlx


VERSION = "1.0"

API_URL = "https://api.anthropic.com/v1/messages"
API_TIMEOUT = 20  # sek

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 400
DEFAULT_COOLDOWN = 15
DEFAULT_GLOBAL_COOLDOWN = 3
DEFAULT_HISTORY_TURNS = 3
DEFAULT_DAILY_BUDGET_USD = 1.00

# Ceny per 1M tokenów (Haiku 4.5). Jeśli zmienisz model, dostosuj lub
# ustaw qlx_aiBotPricingIn / qlx_aiBotPricingOut / qlx_aiBotPricingCacheRead
# / qlx_aiBotPricingCacheWrite w server.cfg (USD za 1M).
PRICING = {
    "claude-haiku-4-5-20251001": {
        "in": 1.00, "out": 5.00, "cache_read": 0.10, "cache_write": 1.25,
    },
    "claude-sonnet-4-6": {
        "in": 3.00, "out": 15.00, "cache_read": 0.30, "cache_write": 3.75,
    },
    "claude-opus-4-7": {
        "in": 15.00, "out": 75.00, "cache_read": 1.50, "cache_write": 18.75,
    },
}

# --- Redis keys -------------------------------------------------------------
RK_HISTORY = "minqlx:aibot:history:{sid}"          # LIST(JSON)
RK_COST_PLAYER = "minqlx:aibot:cost:player:{sid}"  # HASH {in,out,cr,cw,usd}
RK_COST_TOTAL_USD = "minqlx:aibot:cost:total_usd"  # float w stringu
RK_COST_DAY = "minqlx:aibot:cost:day:{ymd}"        # float, TTL 48h
RK_CD_PLAYER = "minqlx:aibot:cd:player:{sid}"      # cooldown per gracz
RK_CD_GLOBAL = "minqlx:aibot:cd:global"            # cooldown globalny
RK_LAST_JOIN = "minqlx:aibot:lastjoin:{sid}"       # unix ts poprzedniego joina

# Osobowość i zasady — trafia do system-promptu razem z kontekstem serwera.
# Trzymamy tu, nie w pliku, żeby zawsze była spójna z kodem.
PERSONA_PROMPT = """Jesteś asystentem AI na serwerze Quake Live. Odpowiadasz \
w czacie in-game, więc:

- Bądź ZWIĘZŁY. 1-3 zdania. Czat QL to nie chatbot www.
- Odpowiadaj w języku pytania (domyślnie polski).
- NIE używaj markdownu, emoji, list. Zwykły tekst.
- Możesz używać kodów kolorów Quake (^1..^7) sporadycznie dla akcentu.
- Jeśli nie znasz odpowiedzi z podanego kontekstu serwera — powiedz to \
wprost i zasugeruj napisać do admina. Nie zmyślaj faktów o tym serwerze.
- Możesz odpowiadać na ogólne pytania (gry, kultura, technika, ciekawostki) \
w oparciu o swoją wiedzę — ale zawsze zwięźle.
- Nie moderuj graczy, nie decyduj o banach, nie udawaj admina.
- Jeśli ktoś prosi o coś nielegalnego / cheaty / exploit — odmów krótko.
"""


# ============================================================================
#  Pomocnicze
# ============================================================================

def _now():
    return int(time.time())


def _today_ymd():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _split_for_chat(text, hard_limit=140):
    """Dzieli długą odpowiedź na kawałki mieszczące się w linii czatu QL.
    Preferuje granicę zdania (kropka/wykrzyknik/pytajnik), potem spację.
    """
    text = (text or "").strip().replace("\n", " ")
    while "  " in text:
        text = text.replace("  ", " ")
    if len(text) <= hard_limit:
        return [text] if text else []

    parts = []
    remaining = text
    while len(remaining) > hard_limit:
        cut = -1
        # spróbuj granicy zdania w rozsądnym oknie
        for i in range(hard_limit, max(hard_limit - 60, 20), -1):
            if remaining[i-1] in ".!?":
                cut = i
                break
        if cut == -1:
            cut = remaining.rfind(" ", 0, hard_limit)
            if cut == -1:
                cut = hard_limit
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


# ============================================================================
#  Plugin
# ============================================================================

class aibot(minqlx.Plugin):
    def __init__(self):
        # Cvary — z domyślnymi. set_cvar_once nie nadpisuje jeśli już jest.
        self.set_cvar_once("qlx_aiBotEnabled", "1")
        self.set_cvar_once("qlx_aiBotApiKey", "")
        self.set_cvar_once("qlx_aiBotModel", DEFAULT_MODEL)
        self.set_cvar_once("qlx_aiBotMaxTokens", str(DEFAULT_MAX_TOKENS))
        self.set_cvar_once("qlx_aiBotCooldownSec", str(DEFAULT_COOLDOWN))
        self.set_cvar_once("qlx_aiBotGlobalCooldownSec", str(DEFAULT_GLOBAL_COOLDOWN))
        self.set_cvar_once("qlx_aiBotHistoryTurns", str(DEFAULT_HISTORY_TURNS))
        self.set_cvar_once("qlx_aiBotSmartOnjoin", "1")
        self.set_cvar_once("qlx_aiBotDailyBudgetUSD", str(DEFAULT_DAILY_BUDGET_USD))
        self.set_cvar_once("qlx_aiBotContextFile", "")

        # Komendy
        self.add_command("ai", self.cmd_ai, 0, usage="<pytanie>")
        self.add_command("ai_reset", self.cmd_ai_reset, 0)
        self.add_command("ai_stats", self.cmd_ai_stats, 0)
        self.add_command("ai_reload", self.cmd_ai_reload, 5)
        self.add_command("ai_budget", self.cmd_ai_budget, 5)
        self.add_command("ai_toggle", self.cmd_ai_toggle, 5)

        # Hook na wejście gracza — smart onjoin.
        self.add_hook("player_connect", self.handle_player_connect)

        # Kontekst serwera — plik ładowany przy starcie.
        self._server_context = ""
        self._server_context_path = self._resolve_context_path()
        self._load_server_context()

        # API key: preferuj ENV, fallback do cvara.
        self._api_key = os.environ.get("ANTHROPIC_API_KEY") or self.get_cvar("qlx_aiBotApiKey") or ""
        if not self._api_key:
            minqlx.console_print(
                "[aibot] UWAGA: brak klucza API. Ustaw ENV ANTHROPIC_API_KEY "
                "albo qlx_aiBotApiKey w server.cfg."
            )

    # ------------------------------------------------------------------ #
    #  Kontekst serwera (plik)
    # ------------------------------------------------------------------ #
    def _resolve_context_path(self):
        override = (self.get_cvar("qlx_aiBotContextFile") or "").strip()
        if override:
            return override
        # domyślnie obok pluginu
        try:
            return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "aibot_context.txt")
        except NameError:
            return "aibot_context.txt"

    def _load_server_context(self):
        path = self._server_context_path
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._server_context = f.read().strip()
            minqlx.console_print(
                "[aibot] Kontekst serwera wczytany z {} ({} znaków).".format(
                    path, len(self._server_context)
                )
            )
        except FileNotFoundError:
            self._server_context = ""
            minqlx.console_print(
                "[aibot] Brak pliku kontekstu {} — bot będzie odpowiadał "
                "bez wiedzy o Twoim serwerze.".format(path)
            )
        except Exception as e:
            self._server_context = ""
            minqlx.console_print(
                "[aibot] Błąd czytania {}: {}".format(path, e)
            )

    def _build_system_blocks(self):
        """Zwraca listę bloków system-prompt z cache_control na dużym bloku
        (kontekst + persona). Cache 'ephemeral' jest 5-min, ale przy ruchu
        na czacie odświeża się w kółko — realny hit-rate blisko 100%.
        """
        big = PERSONA_PROMPT
        if self._server_context:
            big += "\n\n--- KONTEKST TEGO SERWERA ---\n" + self._server_context

        blocks = [{
            "type": "text",
            "text": big,
            "cache_control": {"type": "ephemeral"},
        }]
        return blocks

    # ------------------------------------------------------------------ #
    #  Historia rozmowy (Redis)
    # ------------------------------------------------------------------ #
    def _history_key(self, sid):
        return RK_HISTORY.format(sid=sid)

    def _get_history(self, sid):
        key = self._history_key(sid)
        try:
            raw = self.db.lrange(key, 0, -1) or []
        except Exception:
            return []
        out = []
        for r in raw:
            try:
                if isinstance(r, bytes):
                    r = r.decode("utf-8")
                out.append(json.loads(r))
            except Exception:
                continue
        return out

    def _push_history(self, sid, role, content):
        key = self._history_key(sid)
        entry = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        try:
            self.db.rpush(key, entry)
            turns = int(self.get_cvar("qlx_aiBotHistoryTurns") or DEFAULT_HISTORY_TURNS)
            # 2 wpisy na turę (user + assistant)
            self.db.ltrim(key, -2 * turns, -1)
            # historia wygasa po dobie, żeby stare rozmowy nie ciągnęły się w nieskończoność
            self.db.expire(key, 86400)
        except Exception as e:
            minqlx.console_print("[aibot] push history dla {}: {}".format(sid, e))

    def _reset_history(self, sid):
        try:
            self.db.delete(self._history_key(sid))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Cooldowny i budżet
    # ------------------------------------------------------------------ #
    def _cd_active(self, key, ttl):
        try:
            if self.db.exists(key):
                return True
            self.db.setex(key, ttl, 1)
            return False
        except Exception:
            return False

    def _budget_ok(self):
        cap = float(self.get_cvar("qlx_aiBotDailyBudgetUSD") or DEFAULT_DAILY_BUDGET_USD)
        if cap <= 0:
            return True  # 0 = brak capa
        key = RK_COST_DAY.format(ymd=_today_ymd())
        try:
            spent = float(self.db.get(key) or 0)
        except Exception:
            spent = 0.0
        return spent < cap

    def _pricing(self):
        model = self.get_cvar("qlx_aiBotModel") or DEFAULT_MODEL
        p = dict(PRICING.get(model, PRICING[DEFAULT_MODEL]))
        # override z cvarów jeśli ktoś chce ręcznie ustawić cennik
        for k, cvar in [("in", "qlx_aiBotPricingIn"),
                        ("out", "qlx_aiBotPricingOut"),
                        ("cache_read", "qlx_aiBotPricingCacheRead"),
                        ("cache_write", "qlx_aiBotPricingCacheWrite")]:
            v = self.get_cvar(cvar)
            if v:
                try:
                    p[k] = float(v)
                except ValueError:
                    pass
        return p

    def _record_cost(self, sid, usage):
        p = self._pricing()
        it = int(usage.get("input_tokens", 0) or 0)
        ot = int(usage.get("output_tokens", 0) or 0)
        cr = int(usage.get("cache_read_input_tokens", 0) or 0)
        cw = int(usage.get("cache_creation_input_tokens", 0) or 0)

        usd = (it * p["in"] + ot * p["out"] +
               cr * p["cache_read"] + cw * p["cache_write"]) / 1_000_000.0

        try:
            key_p = RK_COST_PLAYER.format(sid=sid)
            self.db.hincrby(key_p, "in", it)
            self.db.hincrby(key_p, "out", ot)
            self.db.hincrby(key_p, "cr", cr)
            self.db.hincrby(key_p, "cw", cw)
            # USD trzymamy jako float w osobnym field (hincrbyfloat)
            try:
                self.db.hincrbyfloat(key_p, "usd", usd)
            except Exception:
                # niektóre wrappery nie mają hincrbyfloat — trzymamy w mikrocentach
                self.db.hincrby(key_p, "usd_micro", int(round(usd * 1_000_000)))

            key_d = RK_COST_DAY.format(ymd=_today_ymd())
            try:
                self.db.incrbyfloat(key_d, usd)
            except Exception:
                # fallback ręczny
                cur = float(self.db.get(key_d) or 0)
                self.db.set(key_d, str(cur + usd))
            self.db.expire(key_d, 60 * 60 * 48)

            try:
                self.db.incrbyfloat(RK_COST_TOTAL_USD, usd)
            except Exception:
                cur = float(self.db.get(RK_COST_TOTAL_USD) or 0)
                self.db.set(RK_COST_TOTAL_USD, str(cur + usd))
        except Exception as e:
            minqlx.console_print("[aibot] zapis kosztu: {}".format(e))

        return usd

    # ------------------------------------------------------------------ #
    #  Wywołanie API
    # ------------------------------------------------------------------ #
    def _api_call(self, messages, system_blocks=None):
        if not self._api_key:
            return None, "brak klucza API"

        body = {
            "model": self.get_cvar("qlx_aiBotModel") or DEFAULT_MODEL,
            "max_tokens": int(self.get_cvar("qlx_aiBotMaxTokens") or DEFAULT_MAX_TOKENS),
            "messages": messages,
        }
        if system_blocks:
            body["system"] = system_blocks

        req = urllib.request.Request(
            API_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data, None
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            return None, "HTTP {} — {}".format(e.code, err_body[:200])
        except urllib.error.URLError as e:
            return None, "sieć: {}".format(e.reason)
        except Exception as e:
            return None, "{}: {}".format(type(e).__name__, e)

    @staticmethod
    def _extract_text(resp):
        if not resp:
            return ""
        parts = resp.get("content") or []
        out = []
        for p in parts:
            if p.get("type") == "text":
                out.append(p.get("text", ""))
        return "\n".join(out).strip()

    # ------------------------------------------------------------------ #
    #  Główna komenda !ai
    # ------------------------------------------------------------------ #
    def cmd_ai(self, player, msg, channel):
        if not self._enabled():
            channel.reply("^3[ai]^7 aktualnie wyłączony.")
            return
        if len(msg) < 2:
            return minqlx.RET_USAGE

        question = " ".join(msg[1:]).strip()
        if not question:
            return minqlx.RET_USAGE

        sid = player.steam_id
        # cooldowny
        cd_player = int(self.get_cvar("qlx_aiBotCooldownSec") or DEFAULT_COOLDOWN)
        cd_global = int(self.get_cvar("qlx_aiBotGlobalCooldownSec") or DEFAULT_GLOBAL_COOLDOWN)
        if cd_player > 0 and self._cd_active(RK_CD_PLAYER.format(sid=sid), cd_player):
            channel.reply("^3[ai]^7 poczekaj {}s przed kolejnym pytaniem.".format(cd_player))
            return
        if cd_global > 0 and self._cd_active(RK_CD_GLOBAL, cd_global):
            channel.reply("^3[ai]^7 zaraz, ktoś już pyta — spróbuj za chwilę.")
            return

        if not self._budget_ok():
            channel.reply("^3[ai]^7 dzienny budżet wyczerpany, wróć jutro.")
            return

        self._ask_async(sid, player.clean_name, question, channel)

    @minqlx.thread
    def _ask_async(self, sid, name, question, channel):
        history = self._get_history(sid)
        history.append({"role": "user", "content": question})

        resp, err = self._api_call(history, system_blocks=self._build_system_blocks())
        if err:
            channel.reply("^3[ai]^7 błąd: ^1{}".format(err))
            minqlx.console_print("[aibot] API err dla {}: {}".format(sid, err))
            return

        text = self._extract_text(resp)
        if not text:
            channel.reply("^3[ai]^7 pusta odpowiedź (stop_reason={}).".format(
                resp.get("stop_reason") if resp else "?"))
            return

        # zapisz do historii
        self._push_history(sid, "user", question)
        self._push_history(sid, "assistant", text)

        # policz koszt
        usage = (resp or {}).get("usage", {}) or {}
        usd = self._record_cost(sid, usage)

        # wypisz do czatu
        header = "^3[ai -> {}]^7".format(name)
        chunks = _split_for_chat(text)
        if not chunks:
            channel.reply("^3[ai]^7 (brak treści)")
            return
        channel.reply("{} {}".format(header, chunks[0]))
        for c in chunks[1:]:
            channel.reply("^3[ai]^7 " + c)

        minqlx.console_print(
            "[aibot] {} ({}) tokens in={} out={} cr={} cw={} usd={:.4f}".format(
                name, sid,
                usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                usage.get("cache_read_input_tokens", 0),
                usage.get("cache_creation_input_tokens", 0),
                usd,
            )
        )

    # ------------------------------------------------------------------ #
    #  Pozostałe komendy
    # ------------------------------------------------------------------ #
    def cmd_ai_reset(self, player, msg, channel):
        self._reset_history(player.steam_id)
        channel.reply("^3[ai]^7 historia rozmowy wyczyszczona.")

    def cmd_ai_stats(self, player, msg, channel):
        sid = player.steam_id
        key = RK_COST_PLAYER.format(sid=sid)
        try:
            data = self.db.hgetall(key) or {}
        except Exception:
            data = {}
        # normalizacja (bytes -> str)
        norm = {}
        for k, v in data.items():
            if isinstance(k, bytes):
                k = k.decode()
            if isinstance(v, bytes):
                v = v.decode()
            norm[k] = v
        if not norm:
            channel.reply("^3[ai]^7 nie zadałeś jeszcze żadnego pytania.")
            return
        usd = float(norm.get("usd") or 0)
        if not usd and norm.get("usd_micro"):
            usd = float(norm["usd_micro"]) / 1_000_000
        channel.reply(
            "^3[ai]^7 twoje: in={} out={} cache={} — koszt: ${:.4f}".format(
                norm.get("in", 0), norm.get("out", 0),
                int(norm.get("cr", 0) or 0) + int(norm.get("cw", 0) or 0),
                usd,
            )
        )

    def cmd_ai_reload(self, player, msg, channel):
        self._load_server_context()
        # odśwież też klucz API, bo mógł się zmienić w cvarze
        self._api_key = os.environ.get("ANTHROPIC_API_KEY") or self.get_cvar("qlx_aiBotApiKey") or ""
        channel.reply("^3[ai]^7 kontekst przeładowany ({} znaków).".format(
            len(self._server_context)))

    def cmd_ai_budget(self, player, msg, channel):
        day_key = RK_COST_DAY.format(ymd=_today_ymd())
        try:
            spent_day = float(self.db.get(day_key) or 0)
            spent_total = float(self.db.get(RK_COST_TOTAL_USD) or 0)
        except Exception:
            spent_day = 0.0
            spent_total = 0.0
        cap = float(self.get_cvar("qlx_aiBotDailyBudgetUSD") or DEFAULT_DAILY_BUDGET_USD)
        channel.reply(
            "^3[ai]^7 dzisiaj ${:.4f} / ${:.2f} — łącznie ${:.4f}".format(
                spent_day, cap, spent_total,
            )
        )

    def cmd_ai_toggle(self, player, msg, channel):
        cur = self._enabled()
        self.set_cvar("qlx_aiBotEnabled", "0" if cur else "1")
        channel.reply("^3[ai]^7 " + ("wyłączony" if cur else "włączony") + ".")

    def _enabled(self):
        return (self.get_cvar("qlx_aiBotEnabled") or "1") not in ("0", "false", "off", "")

    # ------------------------------------------------------------------ #
    #  Smart onjoin
    # ------------------------------------------------------------------ #
    def handle_player_connect(self, player):
        if not self._enabled():
            return
        if (self.get_cvar("qlx_aiBotSmartOnjoin") or "1") in ("0", "false", "off"):
            return
        if not self._api_key:
            return
        # nie wchodź na wszystkich rzędem — globalny cd + budżet
        if not self._budget_ok():
            return
        cd_g = int(self.get_cvar("qlx_aiBotGlobalCooldownSec") or DEFAULT_GLOBAL_COOLDOWN)
        if cd_g > 0 and self._cd_active(RK_CD_GLOBAL, cd_g):
            return

        self._smart_greet_async(player.steam_id, player.clean_name)

    @minqlx.thread
    def _smart_greet_async(self, sid, name):
        # przygotuj mini-kontekst o graczu
        last_join_key = RK_LAST_JOIN.format(sid=sid)
        try:
            last = self.db.get(last_join_key)
            last = int(last) if last else None
        except Exception:
            last = None
        try:
            self.db.set(last_join_key, str(_now()))
        except Exception:
            pass

        # spróbuj wyciągnąć perm i BDM z Redisa (jeśli są)
        perm = None
        try:
            v = self.db.get("minqlx:players:{}:permission".format(sid))
            if v is not None:
                perm = int(v)
        except Exception:
            pass

        info_lines = ["Gracz: {}".format(name), "SteamID: {}".format(sid)]
        if last:
            hours = max(0, (_now() - last) // 3600)
            if hours < 1:
                info_lines.append("Ostatni raz był mniej niż godzinę temu.")
            elif hours < 48:
                info_lines.append("Ostatni raz był {}h temu.".format(hours))
            else:
                info_lines.append("Ostatni raz był {} dni temu.".format(hours // 24))
        else:
            info_lines.append("Pierwszy raz na tym serwerze.")
        if perm is not None and perm > 0:
            info_lines.append("Poziom uprawnień: {}.".format(perm))

        user_msg = (
            "Powitaj gracza w chacie serwera QL jednym zdaniem, luźno, po polsku. "
            "Nie mów 'witam' ani 'siema'. Nie wymieniaj SteamID. "
            "Maks 100 znaków.\n\nDane:\n" + "\n".join(info_lines)
        )

        resp, err = self._api_call(
            [{"role": "user", "content": user_msg}],
            system_blocks=self._build_system_blocks(),
        )
        if err:
            minqlx.console_print("[aibot] onjoin api err: {}".format(err))
            return
        text = self._extract_text(resp)
        if not text:
            return
        # przytnij defensywnie
        text = text.replace("\n", " ").strip().strip('"').strip("'")
        if len(text) > 140:
            text = text[:140].rstrip() + "..."

        self._record_cost(sid, (resp or {}).get("usage", {}) or {})

        # wypisz do wszystkich
        try:
            minqlx.CHAT_CHANNEL.reply("^3[ai]^7 " + text)
        except Exception as e:
            minqlx.console_print("[aibot] onjoin reply: {}".format(e))
