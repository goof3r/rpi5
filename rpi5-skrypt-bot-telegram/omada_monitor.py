#!/usr/bin/env python3
"""
Omada AP Monitor — pilnuje stanu punktów dostępowych (AP) w kontrolerze
TP-Link Omada i wysyła alert na Telegram, gdy któryś AP nie jest „Połączony”.

Dostęp do kontrolera: Omada Open API w trybie Client Credentials
(Ustawienia → Platform Integration → Open API) — bez hasła administratora.
Konfiguracja: zmienne środowiskowe, domyślnie z /etc/server-monitor.env
(ten sam plik, z którego korzysta bot Telegram). Godziny powiadomień,
przypomnienia i pauzę ustawia się z bota (/omada_powiadomienia) — trafiają
do pliku ustawień (OMADA_SETTINGS_FILE) i działają bez restartu usługi.

Użycie:
  omada_monitor.py daemon          pętla monitorowania (usługa omada-monitor)
  omada_monitor.py check           jedno sprawdzenie z alertami
  omada_monitor.py list            lista AP i ich stan (nic nie wysyła)
  omada_monitor.py test-telegram   wiadomość testowa na Telegram
Opcje:
  --dry-run        wiadomości na ekran zamiast na Telegram
  --raw            (list) surowe dane urządzeń z API w JSON
  --env-file PLIK  wczytaj konfigurację z innego pliku
"""

import argparse
import html
import json
import logging
import os
import re
import signal
import ssl
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VERSION = "1.0.0"
DEFAULT_ENV_FILE = "/etc/server-monitor.env"

log = logging.getLogger("omada-monitor")

# ── Stany urządzeń Omada ──────────────────────────────────────────────────────
# detailStatus — szczegółowy stan, taki jak w kolumnie „Status” panelu
DETAIL_STATUS = {
    0: "Rozłączony",
    1: "Rozłączony (migracja)",
    10: "Provisioning",
    11: "Konfigurowanie",
    12: "Aktualizacja firmware",
    13: "Restart",
    14: "Połączony",
    15: "Połączony (bezprzewodowo)",
    16: "Połączony (migracja)",
    17: "Połączony (bezprzewodowo, migracja)",
    20: "Oczekuje na adopcję",
    21: "Oczekuje na adopcję (bezprzewodowo)",
    22: "Adoptowanie",
    23: "Adoptowanie (bezprzewodowo)",
    24: "Adopcja nieudana",
    25: "Adopcja nieudana (bezprzewodowo)",
    26: "Zarządzany przez inny kontroler",
    27: "Zarządzany przez inny kontroler (bezprzewodowo)",
    30: "Utracony kontakt (heartbeat missed)",
    31: "Utracony kontakt (bezprzewodowo)",
    32: "Utracony kontakt (migracja)",
    33: "Utracony kontakt (bezprzewodowo, migracja)",
    40: "Izolowany",
    41: "Izolowany (migracja)",
    50: "Konfigurowanie (slice)",
}
# status — ogólna kategoria stanu
STATUS = {
    0: "Rozłączony",
    1: "Połączony",
    2: "Oczekuje",
    3: "Utracony kontakt (heartbeat missed)",
    4: "Izolowany",
}
CONNECTED_DETAIL = {14, 15, 16, 17}
MAINTENANCE_DETAIL = {10, 11, 12, 13, 50}  # prace serwisowe: OMADA_IGNORE_MAINTENANCE=1

# Kody Open API oznaczające nieważny/wygasły token — wtedy logujemy się ponownie
TOKEN_ERRORS = {-44112, -44113, -44116}


# ── Konfiguracja ──────────────────────────────────────────────────────────────

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip().strip('"').strip("'")


def _env_bool(name: str, default: bool) -> bool:
    val = _env(name)
    if not val:
        return default
    return val.lower() in ("1", "true", "yes", "tak", "on")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(_env(name) or default))
    except ValueError:
        return default


def _env_minutes(name: str, default: str) -> list[float]:
    out = []
    for part in re.split(r"[,;\s]+", _env(name, default)):
        if not part:
            continue
        try:
            out.append(parse_minutes(part))
        except ValueError:
            log.warning("%s: pomijam niepoprawną wartość %r", name, part)
    return sorted({m for m in out if m > 0})


@dataclass
class Config:
    url: str
    client_id: str
    client_secret: str
    omadac_id: str
    verify_ssl: bool
    ca_file: str
    timeout: int
    interval: int
    remind_minutes: list[float]
    notify_hours: str
    confirm_checks: int
    controller_fail_checks: int
    device_types: set[str]
    ignore_maintenance: bool
    state_file: Path
    settings_file: Path
    bot_token: str
    chat_id: str
    telegram_api: str = "https://api.telegram.org"

    def missing(self) -> list[str]:
        need = {
            "OMADA_URL": self.url,
            "OMADA_CLIENT_ID": self.client_id,
            "OMADA_CLIENT_SECRET": self.client_secret,
        }
        return [k for k, v in need.items() if not v]


def load_env_file(path: str, override: bool = False) -> bool:
    """Wczytuje plik KEY=VALUE (format EnvironmentFile systemd)."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        val = val.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = val
    return True


def load_config() -> Config:
    types = {t.strip().lower() for t in _env("OMADA_DEVICE_TYPES", "ap").split(",") if t.strip()}
    state_dir = Path(_env("OMADA_DATA_DIR", "/var/lib/omada-monitor"))
    return Config(
        url=_env("OMADA_URL").rstrip("/"),
        client_id=_env("OMADA_CLIENT_ID"),
        client_secret=_env("OMADA_CLIENT_SECRET"),
        omadac_id=_env("OMADA_ID"),
        verify_ssl=_env_bool("OMADA_VERIFY_SSL", False),
        ca_file=_env("OMADA_CA_FILE"),
        timeout=_env_int("OMADA_TIMEOUT", 15, 3),
        interval=_env_int("OMADA_INTERVAL", 60, 10),
        remind_minutes=_env_minutes("OMADA_REMIND_MINUTES", "30,60,180"),
        notify_hours=_env("OMADA_NOTIFY_HOURS", "zawsze"),
        confirm_checks=_env_int("OMADA_CONFIRM_CHECKS", 1, 1),
        controller_fail_checks=_env_int("OMADA_CONTROLLER_FAIL_CHECKS", 3, 1),
        device_types=types,
        ignore_maintenance=_env_bool("OMADA_IGNORE_MAINTENANCE", False),
        state_file=Path(_env("OMADA_STATE_FILE") or state_dir / "state.json"),
        settings_file=Path(_env("OMADA_SETTINGS_FILE") or state_dir / "settings.json"),
        bot_token=_env("TELEGRAM_BOT_TOKEN"),
        chat_id=_env("TELEGRAM_CHAT_ID"),
        telegram_api=_env("TELEGRAM_API_URL", "https://api.telegram.org").rstrip("/"),
    )


# ── Czas: parsowanie i formatowanie ───────────────────────────────────────────

def parse_minutes(text: str) -> float:
    """'30' → 30, '30m' → 30, '1h' → 60, '1h30m' → 90, '2d' → 2880."""
    s = text.strip().lower().replace(" ", "")
    for a, b in (("godziny", "h"), ("godzin", "h"), ("godz", "h"), ("dni", "d"),
                 ("dzień", "d"), ("dzien", "d"), ("minut", "m"), ("min", "m")):
        s = s.replace(a, b)
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    m = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?", s)
    if not s or not m or not any(m.groups()):
        raise ValueError(f"niezrozumiały czas „{text}” (przykłady: 30m, 1h, 1h30m, 2d)")
    d, h, mi = (int(x or 0) for x in m.groups())
    return float(d * 1440 + h * 60 + mi)


def fmt_minutes(m: float) -> str:
    m = int(round(m))
    d, rest = divmod(m, 1440)
    h, mi = divmod(rest, 60)
    parts = []
    if d:
        parts.append(f"{d} d")
    if h:
        parts.append(f"{h} h")
    if mi or not parts:
        parts.append(f"{mi} min")
    return " ".join(parts)


def fmt_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1 min"
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes:02d} min"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h"


def fmt_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    if dt.date() == datetime.now().date():
        return dt.strftime("%H:%M")
    return dt.strftime("%d.%m %H:%M")


# ── Okna czasowe powiadomień ──────────────────────────────────────────────────

DAY_ALIASES = {
    "pn": 0, "pon": 0, "poniedzialek": 0, "poniedziałek": 0,
    "wt": 1, "wto": 1, "wtorek": 1,
    "sr": 2, "śr": 2, "sro": 2, "śro": 2, "sroda": 2, "środa": 2,
    "cz": 3, "czw": 3, "czwartek": 3,
    "pt": 4, "pia": 4, "pią": 4, "piatek": 4, "piątek": 4,
    "so": 5, "sb": 5, "sob": 5, "sobota": 5,
    "nd": 6, "nie": 6, "ndz": 6, "niedziela": 6,
}
DAY_LABELS = ["pn", "wt", "śr", "cz", "pt", "so", "nd"]
ALWAYS_WORDS = {"zawsze", "24/7", "calodobowo", "całodobowo", "always"}
EVERYDAY_WORDS = {"codziennie", "wszystkie", "*"}
TIME_RANGE_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?-(\d{1,2})(?::(\d{2}))?$")


@dataclass(frozen=True)
class Window:
    days: frozenset
    start: int  # minuty od północy
    end: int    # minuty od północy (1440 = 24:00); end < start → przez północ

    def contains(self, dt: datetime) -> bool:
        t = dt.hour * 60 + dt.minute
        wd = dt.weekday()
        if self.start == self.end:
            return wd in self.days
        if self.start < self.end:
            return wd in self.days and self.start <= t < self.end
        # przez północ — część po północy należy do dnia, w którym okno się zaczęło
        return (wd in self.days and t >= self.start) or ((wd - 1) % 7 in self.days and t < self.end)


def _day(name: str) -> int:
    key = name.strip().lower().rstrip(".")
    if key not in DAY_ALIASES:
        raise ValueError(f"nieznany dzień „{name}” (użyj: pn, wt, śr, cz, pt, so, nd)")
    return DAY_ALIASES[key]


def _parse_days(token: str) -> set[int]:
    token = token.strip().strip(",").lower()
    if not token or token in EVERYDAY_WORDS:
        return set(range(7))
    days: set[int] = set()
    for part in token.split(","):
        if not part.strip():
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            d, last = _day(a), _day(b)
            while True:
                days.add(d)
                if d == last:
                    break
                d = (d + 1) % 7
        else:
            days.add(_day(part))
    return days


def _parse_hm(h: str, m: str | None) -> int:
    hh, mm = int(h), int(m or 0)
    if not (0 <= hh <= 24 and 0 <= mm < 60) or (hh == 24 and mm):
        raise ValueError(f"niepoprawna godzina {h}:{m or '00'}")
    return hh * 60 + mm


def parse_windows(text: str) -> list[Window]:
    """'zawsze' → [] (bez ograniczeń); 'pn-pt 07:00-17:00; so-nd 09-13' → lista okien."""
    text = (text or "").strip().lower()
    if not text or text in ALWAYS_WORDS:
        return []
    windows = []
    for rule in re.split(r"[;\n]+", text):
        rule = rule.replace("–", "-").replace("—", "-")
        rule = re.sub(r"\s*-\s*", "-", rule)
        rule = re.sub(r"\s*,\s*", ",", rule).strip()
        if not rule:
            continue
        times, day_tokens = [], []
        for tok in rule.split():
            m = TIME_RANGE_RE.match(tok)
            if m:
                start, end = _parse_hm(m[1], m[2]), _parse_hm(m[3], m[4])
                if start >= 1440:
                    raise ValueError("okno nie może zaczynać się o 24:00")
                times.append((start, end))
            else:
                day_tokens.append(tok)
        days = _parse_days(",".join(day_tokens))
        for start, end in times or [(0, 0)]:
            windows.append(Window(frozenset(days), start, end))
    if not windows:
        raise ValueError("nie podano żadnego okna czasowego")
    return windows


def _fmt_days(days) -> str:
    ds = sorted(days)
    if len(ds) == 7:
        return "codziennie"
    runs, start, prev = [], ds[0], ds[0]
    for d in ds[1:]:
        if d == prev + 1:
            prev = d
            continue
        runs.append((start, prev))
        start = prev = d
    runs.append((start, prev))
    parts = []
    for a, b in runs:
        if a == b:
            parts.append(DAY_LABELS[a])
        elif b == a + 1:
            parts.append(f"{DAY_LABELS[a]},{DAY_LABELS[b]}")
        else:
            parts.append(f"{DAY_LABELS[a]}-{DAY_LABELS[b]}")
    return ",".join(parts)


def format_windows(windows: list[Window]) -> str:
    if not windows:
        return "zawsze"
    out = []
    for w in windows:
        if w.start == w.end:
            span = "00:00-24:00"
        else:
            span = f"{w.start // 60:02d}:{w.start % 60:02d}-{w.end // 60:02d}:{w.end % 60:02d}"
        out.append(f"{_fmt_days(w.days)} {span}")
    return "; ".join(out)


def in_windows(dt: datetime, windows: list[Window]) -> bool:
    return not windows or any(w.contains(dt) for w in windows)


# ── Ustawienia zmieniane z bota ───────────────────────────────────────────────

def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def load_settings(cfg: Config) -> dict:
    try:
        data = json.loads(cfg.settings_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("Nie mogę odczytać ustawień %s (%s) — używam domyślnych", cfg.settings_file, e)
        return {}


def save_settings(cfg: Config, settings: dict) -> None:
    settings["updated"] = time.time()
    _write_json(cfg.settings_file, settings)


@dataclass
class NotifyPolicy:
    windows: list[Window]
    remind_minutes: list[float]
    pause_until: float | None

    @property
    def windows_text(self) -> str:
        return format_windows(self.windows)

    def muted_reason(self, now: float) -> str | None:
        if self.pause_until and now < self.pause_until:
            return "pause"
        if not in_windows(datetime.fromtimestamp(now), self.windows):
            return "quiet"
        return None


def notify_policy(cfg: Config, settings: dict | None = None) -> NotifyPolicy:
    settings = load_settings(cfg) if settings is None else settings
    text = settings.get("windows", cfg.notify_hours)
    try:
        windows = parse_windows(str(text))
    except ValueError as e:
        log.warning("Niepoprawne godziny powiadomień %r (%s) — wysyłam zawsze", text, e)
        windows = []
    remind = settings.get("remind_minutes")
    if isinstance(remind, list):
        try:
            remind = sorted({float(m) for m in remind if float(m) > 0})
        except (TypeError, ValueError):
            remind = cfg.remind_minutes
    else:
        remind = cfg.remind_minutes
    pause = settings.get("pause_until")
    try:
        pause = float(pause) if pause else None
    except (TypeError, ValueError):
        pause = None
    return NotifyPolicy(windows, remind, pause)


# ── Klient Omada Open API ─────────────────────────────────────────────────────

class OmadaError(Exception):
    pass


def _to_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


@dataclass
class Device:
    mac: str
    name: str
    model: str
    ip: str
    site: str
    site_id: str
    type: str
    status: int | None
    detail_status: int | None
    status_text: str = ""

    @classmethod
    def from_api(cls, d: dict, site: dict) -> "Device":
        status = d.get("status")
        return cls(
            mac=str(d.get("mac") or "").upper(),
            name=str(d.get("name") or d.get("mac") or "?"),
            model=str(d.get("model") or d.get("showModel") or ""),
            ip=str(d.get("ip") or ""),
            site=site["name"],
            site_id=site["id"],
            type=str(d.get("type") or "").lower(),
            status=_to_int(status),
            detail_status=_to_int(d.get("detailStatus")),
            status_text="" if _to_int(status) is not None else str(status or ""),
        )

    def is_ok(self, ignore_maintenance: bool = False) -> bool:
        if self.detail_status is not None:
            if self.detail_status in CONNECTED_DETAIL:
                return True
            return ignore_maintenance and self.detail_status in MAINTENANCE_DETAIL
        if self.status is not None:
            return self.status == 1
        return self.status_text.lower() in ("connected", "połączony")

    @property
    def label(self) -> str:
        if self.detail_status is not None:
            return DETAIL_STATUS.get(self.detail_status, f"Nieznany stan (kod {self.detail_status})")
        if self.status is not None:
            return STATUS.get(self.status, f"Nieznany stan (kod {self.status})")
        return self.status_text or "Nieznany stan"

    @property
    def key(self) -> str:
        return self.mac or f"{self.site_id}/{self.name}"


@dataclass
class Snapshot:
    sites: list[dict]
    devices: list[Device]
    skipped: dict[str, int]
    raw: list[dict]


class OmadaClient:
    PAGE_SIZE = 100

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._token: str | None = None
        self._token_exp = 0.0
        self._omadac_id = cfg.omadac_id
        self._info: dict | None = None
        if cfg.verify_ssl:
            self._ctx = ssl.create_default_context(cafile=cfg.ca_file or None)
        else:
            # kontroler w sieci LAN ma zwykle certyfikat self-signed
            self._ctx = ssl.create_default_context()
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def _request(self, method: str, path: str, params: dict | None = None,
                 body: dict | None = None, auth: bool = True) -> tuple[int, dict]:
        url = self.cfg.url + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Accept": "application/json", "User-Agent": f"omada-monitor/{VERSION}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"AccessToken={self._token}"
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.cfg.timeout, context=self._ctx) as resp:
                status, raw = resp.status, resp.read()
        except HTTPError as e:
            status, raw = e.code, e.read()
        except (URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise OmadaError(f"brak połączenia z kontrolerem: {reason}") from None
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            raise OmadaError(f"{path}: HTTP {status}, odpowiedź nie jest JSON-em") from None
        if not isinstance(payload, dict):
            raise OmadaError(f"{path}: HTTP {status}, nieoczekiwany format odpowiedzi")
        return status, payload

    def info(self) -> dict:
        if self._info is None:
            _, p = self._request("GET", "/api/info", auth=False)
            if p.get("errorCode") != 0:
                raise OmadaError(f"/api/info: {p.get('msg')} (kod {p.get('errorCode')})")
            self._info = p.get("result") or {}
        return self._info

    def omadac_id(self) -> str:
        if not self._omadac_id:
            self._omadac_id = self.info().get("omadacId", "")
            if not self._omadac_id:
                raise OmadaError("kontroler nie zwrócił omadacId — ustaw OMADA_ID ręcznie")
        return self._omadac_id

    def _authenticate(self) -> None:
        status, p = self._request(
            "POST", "/openapi/authorize/token",
            params={"grant_type": "client_credentials"},
            body={"omadacId": self.omadac_id(),
                  "client_id": self.cfg.client_id,
                  "client_secret": self.cfg.client_secret},
            auth=False,
        )
        res = p.get("result") or {}
        if p.get("errorCode") != 0 or not res.get("accessToken"):
            raise OmadaError(
                f"logowanie do Open API nieudane: {p.get('msg')} "
                f"(kod {p.get('errorCode')}, HTTP {status}) — sprawdź Client ID/Secret"
            )
        self._token = res["accessToken"]
        self._token_exp = time.time() + max(60, int(res.get("expiresIn") or 7200) - 120)
        log.debug("Nowy token Open API (ważny %ss)", res.get("expiresIn"))

    def api_get(self, path: str, params: dict | None = None):
        for attempt in (1, 2):
            if not self._token or time.time() >= self._token_exp:
                self._authenticate()
            status, p = self._request("GET", f"/openapi/v1/{self.omadac_id()}{path}", params=params)
            code = p.get("errorCode")
            if code == 0:
                return p.get("result")
            if attempt == 1 and (status == 401 or code in TOKEN_ERRORS):
                self._token = None
                continue
            raise OmadaError(f"{path}: {p.get('msg')} (kod {code}, HTTP {status})")
        raise OmadaError(f"{path}: autoryzacja nieudana")

    def _paged(self, path: str) -> list[dict]:
        items: list[dict] = []
        page = 1
        while page <= 200:
            res = self.api_get(path, {"page": page, "pageSize": self.PAGE_SIZE})
            if isinstance(res, list):
                return items + res
            data = (res or {}).get("data") or []
            items.extend(data)
            total = (res or {}).get("totalRows")
            if not data or total is None or len(items) >= int(total):
                break
            page += 1
        return items

    def sites(self) -> list[dict]:
        out = []
        for s in self._paged("/sites"):
            sid = s.get("siteId") or s.get("id")
            if sid:
                out.append({"id": str(sid), "name": str(s.get("name") or sid)})
        out.sort(key=lambda s: s["name"].lower())
        return out

    def snapshot(self) -> Snapshot:
        """Wszystkie lokalizacje i urządzenia wybranych typów (OMADA_DEVICE_TYPES)."""
        wanted = self.cfg.device_types
        sites = self.sites()
        devices: list[Device] = []
        skipped: dict[str, int] = {}
        raw: list[dict] = []
        for site in sites:
            for d in self._paged(f"/sites/{site['id']}/devices"):
                raw.append({"_site": site["name"], **d})
                dev = Device.from_api(d, site)
                if wanted and dev.type not in wanted:
                    skipped[dev.type or "?"] = skipped.get(dev.type or "?", 0) + 1
                    continue
                devices.append(dev)
        devices.sort(key=lambda x: (x.site.lower(), x.name.lower()))
        return Snapshot(sites, devices, skipped, raw)


# ── Telegram ──────────────────────────────────────────────────────────────────

def esc(text) -> str:
    return html.escape(str(text), quote=False)


def split_message(text: str, limit: int = 3800) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


class Telegram:
    def __init__(self, cfg: Config, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run

    def send(self, text: str) -> bool:
        if self.dry_run:
            print("──────── [Telegram] ────────")
            print(text)
            print("────────────────────────────", flush=True)
            return True
        if not self.cfg.bot_token or not self.cfg.chat_id:
            log.error("Brak TELEGRAM_BOT_TOKEN lub TELEGRAM_CHAT_ID — nie wysyłam")
            return False
        url = f"{self.cfg.telegram_api}/bot{self.cfg.bot_token}/sendMessage"
        for chunk in split_message(text):
            if not self._post(url, chunk):
                return False
        return True

    def _post(self, url: str, text: str) -> bool:
        body = urlencode({
            "chat_id": self.cfg.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        for attempt in range(3):
            try:
                with urlopen(Request(url, data=body, method="POST"), timeout=20) as resp:
                    if json.loads(resp.read().decode("utf-8")).get("ok"):
                        return True
            except HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:300]
                log.warning("Telegram HTTP %s: %s", e.code, detail)
                if e.code == 400:  # błąd treści — ponawianie nic nie da
                    return False
            except (URLError, TimeoutError, OSError, ValueError) as e:
                log.warning("Telegram: %s", e)
            time.sleep(2 * (attempt + 1))
        return False


# ── Stan i logika alertów ─────────────────────────────────────────────────────

def _dev_line(d: dict, extra: str = "") -> str:
    meta = " · ".join(x for x in (d.get("site"), d.get("model"), d.get("ip")) if x)
    line = f"• <b>{esc(d.get('name'))}</b> — {esc(d.get('label'))}"
    if extra:
        line += extra if extra.startswith("\n") else f" {extra}"
    if meta:
        line += f"\n   <i>{esc(meta)}</i>"
    return line


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("devices", {})
            data.setdefault("controller", {})
            data.setdefault("outbox", [])
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        log.warning("Nie mogę odczytać stanu %s (%s) — zaczynam od zera", path, e)
    return {"devices": {}, "controller": {}, "outbox": [], "initialized": False}


def save_state(path: Path, state: dict) -> None:
    _write_json(path, state)


@dataclass
class Report:
    intro: list[str] = field(default_factory=list)
    down: list[str] = field(default_factory=list)
    reminders: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    last_reminder: bool = False
    new_down_keys: set = field(default_factory=set)

    def render(self, include_reminders: bool = True) -> str:
        parts = []
        if self.intro:
            parts.append("\n".join(self.intro))
        if self.down:
            head = "🔴 <b>Omada: AP niepołączony</b>" if len(self.down) == 1 else \
                   f"🔴 <b>Omada: niepołączone AP ({len(self.down)})</b>"
            parts.append(head + "\n" + "\n".join(self.down))
        if self.reminders and include_reminders:
            body = "⏰ <b>Omada: AP nadal niepołączony</b>\n" + "\n".join(self.reminders)
            if self.last_reminder:
                body += "\n<i>To ostatnie przypomnienie — następna wiadomość, gdy AP wróci.</i>"
            parts.append(body)
        if self.recovered:
            parts.append("✅ <b>Omada: znów połączone</b>\n" + "\n".join(self.recovered))
        if self.info:
            parts.append("ℹ️ <b>Omada</b>\n" + "\n".join(self.info))
        return "\n\n".join(parts)


class Monitor:
    QUIET_EVENTS_MAX = 30

    def __init__(self, cfg: Config, telegram: Telegram, client: OmadaClient | None = None):
        self.cfg = cfg
        self.tg = telegram
        self.client = client or OmadaClient(cfg)
        self.state = load_state(cfg.state_file)
        self.remind: list[float] = cfg.remind_minutes

    def _due_reminders(self, since: float, now: float) -> int:
        """Ile progów przypomnień już minęło od początku awarii."""
        elapsed_min = (now - since) / 60
        return sum(1 for m in self.remind if elapsed_min >= m)

    # ── wysyłka ──
    def _send(self, text: str, now: float) -> None:
        if not text:
            return
        if not self.tg.send(text):
            log.error("Nie wysłano wiadomości — zostaje w kolejce do ponowienia")
            outbox = self.state.setdefault("outbox", [])
            outbox.append({"ts": now, "text": text})
            del outbox[:-20]

    def _flush_outbox(self) -> None:
        outbox = self.state.get("outbox") or []
        while outbox:
            item = outbox[0]
            text = f"⌛ <i>Wiadomość opóźniona (z {fmt_time(item['ts'])})</i>\n{item['text']}"
            if self.tg.send(text):
                outbox.pop(0)
                continue
            item["tries"] = int(item.get("tries") or 0) + 1
            if item["tries"] >= 10:
                log.error("Porzucam wiadomość po %d próbach: %.200s", item["tries"], item["text"])
                outbox.pop(0)
                continue
            return

    # ── główne sprawdzenie ──
    def check(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        policy = notify_policy(self.cfg)
        self.remind = policy.remind_minutes
        report = Report()
        try:
            snap = self.client.snapshot()
        except OmadaError as e:
            self._controller_failed(str(e), now, report)
        else:
            self._evaluate(snap, now, report)
        self._dispatch(report, policy.muted_reason(now), now)
        save_state(self.cfg.state_file, self.state)

    def _evaluate(self, snap: Snapshot, now: float, report: Report) -> None:
        ctl = self.state.get("controller") or {}
        if ctl.get("alerted"):
            report.recovered.append(
                f"• <b>Kontroler Omada</b> znów odpowiada "
                f"(przerwa {fmt_duration(now - ctl.get('fail_since', now))})")
        # Po przerwie w łączności (np. restart kontrolera) AP przez chwilę wiszą jako
        # rozłączone, zanim się zgłoszą — w tym jednym sprawdzeniu nie alarmujemy o nowych.
        warmup = int(ctl.get("fail_count") or 0) > 0
        self.state["controller"] = {"fail_count": 0, "last_ok": now}

        first_run = not self.state.get("initialized")
        known: dict = self.state.setdefault("devices", {})
        seen = set()
        total_reminders = len(self.remind)

        for dev in snap.devices:
            seen.add(dev.key)
            ok = dev.is_ok(self.cfg.ignore_maintenance)
            prev = known.get(dev.key)
            entry = prev or {"bad_checks": 0, "alerted": False, "reminders": 0, "bad_since": None}
            entry.update({"name": dev.name, "site": dev.site, "site_id": dev.site_id,
                          "model": dev.model, "ip": dev.ip, "label": dev.label, "ok": ok})
            known[dev.key] = entry

            if ok:
                if entry.get("alerted"):
                    report.recovered.append(_dev_line(
                        entry, f"(przerwa {fmt_duration(now - (entry.get('bad_since') or now))})"))
                entry.update({"bad_checks": 0, "alerted": False, "reminders": 0, "bad_since": None})
                if prev is None and not first_run:
                    report.info.append(f"• Nowy AP w kontrolerze: <b>{esc(dev.name)}</b> "
                                       f"({esc(dev.site)}) — {esc(dev.label)}")
                continue

            # AP niepołączony
            if not warmup:
                entry["bad_checks"] = int(entry.get("bad_checks") or 0) + 1
            if not entry.get("bad_since"):
                entry["bad_since"] = now
            if not entry.get("alerted"):
                if entry["bad_checks"] >= self.cfg.confirm_checks:
                    entry["alerted"] = True
                    entry["reminders"] = self._due_reminders(entry["bad_since"], now)
                    report.new_down_keys.add(dev.key)
                    report.down.append(_dev_line(
                        entry, f"\n   od {fmt_time(entry['bad_since'])} · MAC {esc(dev.mac)}"))
                continue
            due = self._due_reminders(entry["bad_since"], now)
            if due > int(entry.get("reminders") or 0):
                entry["reminders"] = due
                report.reminders.append(_dev_line(
                    entry, f"\n   od {fmt_time(entry['bad_since'])} "
                           f"({fmt_duration(now - entry['bad_since'])}) · "
                           f"przypomnienie {due}/{total_reminders}"))
                if due >= total_reminders:
                    report.last_reminder = True

        for key in [k for k in known if k not in seen]:
            gone = known.pop(key)
            report.info.append(f"• AP <b>{esc(gone.get('name'))}</b> ({esc(gone.get('site'))}) "
                               f"zniknął z listy kontrolera — przestaję go pilnować")

        if first_run:
            n_ok = sum(1 for d in snap.devices if d.is_ok(self.cfg.ignore_maintenance))
            report.intro.append(f"🟢 <b>Monitor Omada uruchomiony</b> (v{VERSION})")
            report.intro.append(f"Pilnuję AP: {len(snap.devices)} w lokalizacjach: {len(snap.sites)} — "
                                f"połączonych {n_ok}/{len(snap.devices)}. Sprawdzam co {self.cfg.interval} s.")
            report.intro.append("Podgląd: /omada · ustawienia: /omada_powiadomienia")
            if not snap.devices:
                report.intro.append("⚠️ Nie znalazłem żadnego AP — sprawdź uprawnienia aplikacji Open API "
                                    "(dostęp do lokalizacji) i OMADA_DEVICE_TYPES.")
            if snap.skipped:
                report.intro.append("Pominięte inne urządzenia: " +
                                    ", ".join(f"{k}: {v}" for k, v in sorted(snap.skipped.items())))
            self.state["initialized"] = True

    def _controller_failed(self, error: str, now: float, report: Report) -> None:
        ctl = self.state.setdefault("controller", {})
        ctl["fail_count"] = int(ctl.get("fail_count") or 0) + 1
        ctl.setdefault("fail_since", now)
        ctl["last_error"] = error
        log.warning("Kontroler: %s (błąd %d z rzędu)", error, ctl["fail_count"])
        if not ctl.get("alerted"):
            if ctl["fail_count"] >= self.cfg.controller_fail_checks:
                ctl["alerted"] = True
                ctl["reminders"] = self._due_reminders(ctl["fail_since"], now)
                report.down.append(
                    f"• <b>Brak odpowiedzi z kontrolera Omada</b> od {fmt_time(ctl['fail_since'])}\n"
                    f"   <i>{esc(error)}</i>\n"
                    f"   Stanu AP nie da się teraz sprawdzić.")
            return
        due = self._due_reminders(ctl["fail_since"], now)
        if due > int(ctl.get("reminders") or 0):
            ctl["reminders"] = due
            report.reminders.append(
                f"• <b>Kontroler Omada</b> nadal nie odpowiada "
                f"({fmt_duration(now - ctl['fail_since'])}) · przypomnienie {due}/{len(self.remind)}\n"
                f"   <i>{esc(error)}</i>")
            if due >= len(self.remind):
                report.last_reminder = True

    # ── godziny ciszy i pauza ──
    def _dispatch(self, report: Report, muted: str | None, now: float) -> None:
        prev_muted = self.state.get("muted")
        if muted is None:
            self._flush_outbox()
            text = report.render()
            if prev_muted:
                digest = self._digest(prev_muted, report.new_down_keys, now)
                if digest:
                    # podsumowanie i tak wymienia wszystkie niepołączone AP z czasem awarii
                    text = "\n\n".join(x for x in (digest, report.render(include_reminders=False)) if x)
            self._send(text, now)
        elif muted == "quiet":
            text = report.render(include_reminders=False)
            if text:
                events = self.state.setdefault("quiet_events", [])
                events.append({"ts": now, "text": text})
                if len(events) > self.QUIET_EVENTS_MAX:
                    self.state["quiet_dropped"] = int(self.state.get("quiet_dropped") or 0) + 1
                    events.pop(0)
                log.info("Poza godzinami powiadomień — zdarzenie trafi do podsumowania")
        else:
            if report.render():
                log.info("Pauza powiadomień — pomijam wiadomość")
        self.state["muted"] = muted

    def _digest(self, reason: str, exclude: set, now: float) -> str:
        events = self.state.pop("quiet_events", None) or []
        dropped = int(self.state.pop("quiet_dropped", 0) or 0)
        devices = self.state.get("devices", {})
        down_now = [e for k, e in devices.items()
                    if not e.get("ok") and e.get("alerted") and k not in exclude]
        ctl_down = bool((self.state.get("controller") or {}).get("alerted"))
        if reason == "pause":
            head = "▶️ <b>Omada: koniec pauzy powiadomień</b>"
        elif not events and not down_now and not ctl_down:
            return ""
        else:
            head = "🌅 <b>Omada: podsumowanie spoza godzin powiadomień</b>"
        parts = [head]
        if dropped:
            parts.append(f"<i>(pominięto starszych zdarzeń: {dropped})</i>")
        for ev in events:
            parts.append(f"🕐 <b>{fmt_time(ev['ts'])}</b>\n{ev['text']}")
        if ctl_down:
            parts.append("<b>Kontroler Omada nadal nie odpowiada.</b>")
        elif down_now:
            parts.append("<b>Teraz niepołączone:</b>\n" + "\n".join(
                _dev_line(e, f"(od {fmt_time(e['bad_since'])}, {fmt_duration(now - e['bad_since'])})")
                for e in down_now if e.get("bad_since")))
        else:
            parts.append("Teraz wszystkie AP są połączone ✅")
        return "\n\n".join(parts)


# ── Funkcje dla bota Telegram (/omada, /omada_powiadomienia) ──────────────────
# Zwracają gotowy tekst HTML; przyciski jako [[(etykieta, callback_data), …], …].

Buttons = list[list[tuple[str, str]]]


def _snapshot_or_error(cfg: Config) -> tuple[Snapshot | None, str]:
    missing = cfg.missing()
    if missing:
        return None, ("⚠️ <b>Monitor Omada nie jest skonfigurowany</b>\n"
                      f"Brakuje: <code>{esc(', '.join(missing))}</code> w /etc/server-monitor.env")
    try:
        return OmadaClient(cfg).snapshot(), ""
    except OmadaError as e:
        return None, f"❌ <b>Omada:</b> {esc(e)}"


def _since_text(known: dict, dev: Device, now: float) -> str:
    since = (known.get(dev.key) or {}).get("bad_since")
    return f"(od {fmt_time(since)}, {fmt_duration(now - since)})" if since else ""


def _site_buttons(snap: Snapshot, cfg: Config, sites: list[dict] | None = None) -> Buttons:
    rows: Buttons = []
    row: list[tuple[str, str]] = []
    for site in sites if sites is not None else snap.sites:
        devs = [d for d in snap.devices if d.site_id == site["id"]]
        n_ok = sum(1 for d in devs if d.is_ok(cfg.ignore_maintenance))
        icon = "⚪" if not devs else ("🟢" if n_ok == len(devs) else "🔴")
        row.append((f"{icon} {site['name'][:24]} ({n_ok}/{len(devs)})", f"omd:s:{site['id']}"[:64]))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def policy_line(cfg: Config, policy: NotifyPolicy | None = None, now: float | None = None) -> str:
    policy = policy or notify_policy(cfg)
    now = time.time() if now is None else now
    muted = policy.muted_reason(now)
    state = {"pause": f"⏸ pauza do {fmt_time(policy.pause_until or now)}",
             "quiet": "🌙 teraz poza godzinami"}.get(muted, "teraz aktywne")
    hours = "zawsze" if not policy.windows else policy.windows_text
    return f"🔔 Powiadomienia: <b>{esc(hours)}</b> · {state}"


def bot_summary(cfg: Config | None = None) -> tuple[str, Buttons]:
    cfg = cfg or load_config()
    snap, err = _snapshot_or_error(cfg)
    if not snap:
        return err, [[("🔄 Spróbuj ponownie", "omd:sum")]]
    known = load_state(cfg.state_file).get("devices", {})
    now = time.time()
    bad = [d for d in snap.devices if not d.is_ok(cfg.ignore_maintenance)]
    total = len(snap.devices)
    lines = ["📡 <b>Omada — punkty dostępowe</b>",
             f"{'🟢' if not bad else '🔴'} Połączone: <b>{total - len(bad)}/{total}</b> · "
             f"lokalizacje: {len(snap.sites)}", ""]
    for site in snap.sites:
        devs = [d for d in snap.devices if d.site_id == site["id"]]
        n_ok = sum(1 for d in devs if d.is_ok(cfg.ignore_maintenance))
        icon = "⚪" if not devs else ("🟢" if n_ok == len(devs) else "🔴")
        lines.append(f"{icon} <b>{esc(site['name'])}</b> — {n_ok}/{len(devs)}"
                     + ("" if devs else " (brak AP)"))
    if bad:
        lines += ["", "<b>Problemy:</b>"]
        for d in bad:
            lines.append(_dev_line({"name": d.name, "label": d.label, "site": d.site,
                                    "model": d.model, "ip": d.ip}, _since_text(known, d, now)))
    lines += ["", policy_line(cfg, now=now), "", "Wybierz lokalizację:"]
    buttons = _site_buttons(snap, cfg)
    buttons.append([("📋 Wszystkie AP", "omd:all"), ("🔄 Odśwież", "omd:sum")])
    return "\n".join(lines), buttons


def _site_block(snap: Snapshot, site: dict, cfg: Config, known: dict, now: float) -> str:
    devs = [d for d in snap.devices if d.site_id == site["id"]]
    bad = [d for d in devs if not d.is_ok(cfg.ignore_maintenance)]
    good = [d for d in devs if d.is_ok(cfg.ignore_maintenance)]
    icon = "⚪" if not devs else ("🟢" if not bad else "🔴")
    lines = [f"📍 <b>{esc(site['name'])}</b> {icon} połączone {len(good)}/{len(devs)}"]
    for d in bad:
        meta = " · ".join(x for x in (d.model, d.ip, d.mac) if x)
        lines.append(f"🔴 <b>{esc(d.name)}</b> — {esc(d.label)} {_since_text(known, d, now)}".rstrip()
                     + (f"\n     <i>{esc(meta)}</i>" if meta else ""))
    for d in good:
        meta = " · ".join(x for x in (d.model, d.ip) if x)
        lines.append(f"🟢 {esc(d.name)}" + (f" <i>{esc(meta)}</i>" if meta else ""))
    if not devs:
        lines.append("<i>brak AP w tej lokalizacji</i>")
    return "\n".join(lines)


def bot_site(query: str, cfg: Config | None = None, by_id: bool = False) -> tuple[str, Buttons]:
    cfg = cfg or load_config()
    snap, err = _snapshot_or_error(cfg)
    if not snap:
        return err, [[("🔄 Spróbuj ponownie", "omd:sum")]]
    if by_id:
        matches = [s for s in snap.sites if s["id"] == query]
    else:
        q = query.strip().lower()
        matches = [s for s in snap.sites if s["name"].lower() == q] or \
                  [s for s in snap.sites if q in s["name"].lower()]
    back = [("⬅️ Podsumowanie", "omd:sum")]
    if len(matches) != 1:
        if matches:
            text = f"🔎 Do „{esc(query)}” pasuje kilka lokalizacji — wybierz:"
        else:
            text = f"🔎 Nie znalazłem lokalizacji „{esc(query)}”. Dostępne:"
        return text, _site_buttons(snap, cfg, matches or snap.sites) + [back]
    site = matches[0]
    known = load_state(cfg.state_file).get("devices", {})
    text = _site_block(snap, site, cfg, known, time.time())
    return text, [[("🔄 Odśwież", f"omd:s:{site['id']}"[:64])] + back]


def bot_all(cfg: Config | None = None) -> tuple[str, Buttons]:
    cfg = cfg or load_config()
    snap, err = _snapshot_or_error(cfg)
    if not snap:
        return err, [[("🔄 Spróbuj ponownie", "omd:all")]]
    known = load_state(cfg.state_file).get("devices", {})
    now = time.time()
    blocks = [_site_block(snap, s, cfg, known, now) for s in snap.sites]
    text = "📋 <b>Omada — wszystkie AP</b>\n\n" + "\n\n".join(blocks or ["Brak lokalizacji."])
    return text, [[("🔄 Odśwież", "omd:all"), ("⬅️ Podsumowanie", "omd:sum")]]


NOTIFY_HELP = (
    "<b>Zmiana ustawień:</b>\n"
    "<code>/omada_powiadomienia godziny zawsze</code>\n"
    "<code>/omada_powiadomienia godziny 07:00-22:00</code>\n"
    "<code>/omada_powiadomienia godziny pn-pt 07:00-17:00; so,nd 09:00-13:00</code>\n"
    "<code>/omada_powiadomienia przypomnienia 30m 1h 3h</code>\n"
    "<code>/omada_powiadomienia przypomnienia brak</code>\n"
    "<code>/omada_powiadomienia pauza 2h</code>  (albo: <code>pauza do 18:00</code>)\n"
    "<code>/omada_powiadomienia wznow</code>\n"
    "<code>/omada_powiadomienia domyslne</code>\n"
    "<i>Okno może przechodzić przez północ (np. 22:00-06:00). Zdarzenia spoza godzin "
    "trafiają do jednego podsumowania wysyłanego, gdy okno się otworzy. Podczas pauzy "
    "wiadomości są pomijane — po pauzie przychodzi stan aktualny.</i>"
)


def notify_status(cfg: Config, settings: dict | None = None, now: float | None = None) -> str:
    settings = load_settings(cfg) if settings is None else settings
    policy = notify_policy(cfg, settings)
    now = time.time() if now is None else now
    hours = "zawsze (24/7)" if not policy.windows else policy.windows_text
    if policy.remind_minutes:
        remind = "po " + ", ".join(fmt_minutes(m) for m in policy.remind_minutes) + \
                 " od awarii, potem cisza do powrotu AP"
    else:
        remind = "wyłączone (tylko alert i powrót)"
    muted = policy.muted_reason(now)
    now_txt = {"pause": "⏸ pauza — wiadomości są pomijane",
               "quiet": "🌙 poza godzinami — zdarzenia trafią do podsumowania"}.get(muted, "✅ wysyłam na bieżąco")
    lines = ["🔔 <b>Powiadomienia Omada</b>",
             f"Godziny wysyłania: <b>{esc(hours)}</b>",
             f"Przypomnienia: {esc(remind)}"]
    if policy.pause_until and now < policy.pause_until:
        lines.append(f"Pauza do: <b>{fmt_time(policy.pause_until)}</b>")
    lines.append(f"Teraz: {now_txt}")
    if not settings:
        lines.append("<i>(ustawienia domyślne z /etc/server-monitor.env)</i>")
    return "\n".join(lines)


def _parse_pause(text: str, now: float) -> float:
    text = text.strip().lower()
    if not text:
        raise ValueError("podaj czas pauzy, np. „pauza 2h” albo „pauza do 18:00”")
    m = re.fullmatch(r"do\s+(\d{1,2})(?::(\d{2}))?", text)
    if m:
        target_min = _parse_hm(m[1], m[2]) % 1440
        dt = datetime.fromtimestamp(now)
        until = dt.replace(hour=target_min // 60, minute=target_min % 60, second=0, microsecond=0)
        if until <= dt:
            until += timedelta(days=1)
        return until.timestamp()
    minutes = parse_minutes(text)
    if minutes <= 0:
        raise ValueError("czas pauzy musi być większy od zera")
    if minutes > 7 * 1440:
        raise ValueError("pauza może trwać najwyżej 7 dni")
    return now + minutes * 60


def bot_notify(args: list[str], cfg: Config | None = None, now: float | None = None) -> str:
    """Obsługa /omada_powiadomienia [godziny|przypomnienia|pauza|wznow|domyslne …]."""
    cfg = cfg or load_config()
    now = time.time() if now is None else now
    settings = load_settings(cfg)
    text = " ".join(args).strip()
    if not text:
        return notify_status(cfg, settings, now) + "\n\n" + NOTIFY_HELP
    sub, _, rest = text.partition(" ")
    sub, rest = sub.lower(), rest.strip()
    try:
        if sub in ("godziny", "okna", "czas"):
            if not rest:
                raise ValueError("podaj godziny, np. „godziny pn-pt 07:00-17:00” albo „godziny zawsze”")
            settings["windows"] = format_windows(parse_windows(rest))
            msg = "✅ Zapisano godziny powiadomień."
        elif sub in ("przypomnienia", "przypomnienie", "przyp"):
            if rest.lower() in ("brak", "wyłącz", "wylacz", "off", "0", "nie"):
                mins: list[float] = []
            else:
                tokens = [t for t in re.split(r"[,;\s]+", rest) if t]
                if not tokens:
                    raise ValueError("podaj czasy, np. „przypomnienia 30m 1h 3h” albo „przypomnienia brak”")
                mins = sorted({parse_minutes(t) for t in tokens})
                if len(mins) > 10:
                    raise ValueError("najwyżej 10 przypomnień")
                if any(m <= 0 for m in mins):
                    raise ValueError("czasy przypomnień muszą być większe od zera")
            settings["remind_minutes"] = mins
            msg = "✅ Zapisano przypomnienia."
        elif sub in ("pauza", "wycisz"):
            settings["pause_until"] = _parse_pause(rest, now)
            msg = f"⏸ Powiadomienia wstrzymane do {fmt_time(settings['pause_until'])}."
        elif sub in ("wznow", "wznów", "odcisz"):
            settings.pop("pause_until", None)
            msg = "▶️ Pauza wyłączona."
        elif sub in ("domyslne", "domyślne", "reset"):
            settings = {}
            msg = "↩️ Przywrócono ustawienia domyślne."
        else:
            raise ValueError(f"nieznana opcja „{sub}”")
    except ValueError as e:
        return f"❌ {esc(e)}\n\n{NOTIFY_HELP}"
    if settings:
        save_settings(cfg, settings)
    else:
        try:
            cfg.settings_file.unlink()
        except FileNotFoundError:
            pass
    return msg + "\n\n" + notify_status(cfg, settings, now)


# ── CLI ───────────────────────────────────────────────────────────────────────

def cmd_list(cfg: Config, raw: bool) -> int:
    client = OmadaClient(cfg)
    try:
        info = client.info()
        snap = client.snapshot()
    except OmadaError as e:
        print(f"BŁĄD: {e}", file=sys.stderr)
        return 1
    if raw:
        print(json.dumps(snap.raw, ensure_ascii=False, indent=2))
        return 0
    print(f"Kontroler: {cfg.url}  (Omada {info.get('controllerVer', '?')}, "
          f"omadacId {client.omadac_id()})")
    print("Lokalizacje: " + ", ".join(s["name"] for s in snap.sites))
    rows = [("LOKALIZACJA", "NAZWA", "MODEL", "IP", "MAC", "KOD", "STAN")]
    for d in snap.devices:
        mark = "OK " if d.is_ok(cfg.ignore_maintenance) else "!! "
        rows.append((d.site, d.name, d.model, d.ip, d.mac,
                     f"{d.status}/{d.detail_status}", mark + d.label))
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip())
    n_ok = sum(1 for d in snap.devices if d.is_ok(cfg.ignore_maintenance))
    print(f"\nAP: {len(snap.devices)}, połączonych: {n_ok}  (KOD = status/detailStatus)")
    if snap.skipped:
        print("Pominięte inne typy: " + ", ".join(f"{k}: {v}" for k, v in sorted(snap.skipped.items())))
    return 0


def run_daemon(cfg: Config, monitor: Monitor) -> int:
    stop = threading.Event()

    def _stop(signum, _frame):
        log.info("Sygnał %s — kończę", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    policy = notify_policy(cfg)
    log.info("Monitor Omada v%s: %s, co %ss, godziny: %s, przypomnienia: %s",
             VERSION, cfg.url, cfg.interval, policy.windows_text,
             ", ".join(fmt_minutes(m) for m in policy.remind_minutes) or "brak")
    while not stop.is_set():
        try:
            monitor.check()
        except Exception:
            log.exception("Nieoczekiwany błąd sprawdzania")
        stop.wait(cfg.interval)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Monitor stanu AP w kontrolerze Omada → Telegram")
    ap.add_argument("command", choices=["daemon", "check", "list", "test-telegram"])
    ap.add_argument("--dry-run", action="store_true", help="wiadomości na ekran zamiast na Telegram")
    ap.add_argument("--raw", action="store_true", help="(list) surowe dane z API")
    ap.add_argument("--env-file", help=f"plik z konfiguracją (domyślnie {DEFAULT_ENV_FILE})")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if args.env_file:
        if not load_env_file(args.env_file):
            print(f"Nie mogę odczytać {args.env_file}", file=sys.stderr)
            return 2
    elif not os.environ.get("OMADA_URL"):
        load_env_file(DEFAULT_ENV_FILE)

    cfg = load_config()
    tg = Telegram(cfg, dry_run=args.dry_run)

    if args.command == "test-telegram":
        ok = tg.send(f"🧪 <b>Monitor Omada</b> — wiadomość testowa (v{VERSION})\n"
                     f"Kontroler: <code>{esc(cfg.url or '—')}</code>")
        print("Wysłano." if ok else "Nie udało się wysłać — sprawdź token i chat_id.")
        return 0 if ok else 1

    missing = cfg.missing()
    if missing:
        print("Brak konfiguracji: " + ", ".join(missing), file=sys.stderr)
        return 2

    if args.command == "list":
        return cmd_list(cfg, args.raw)

    monitor = Monitor(cfg, tg)
    if args.command == "check":
        monitor.check()
        return 0
    return run_daemon(cfg, monitor)


if __name__ == "__main__":
    sys.exit(main())
