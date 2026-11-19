"""Wspólne elementy wtyczki AP: model urządzenia, HTTP, formatowanie, okna czasowe."""

import html
import json
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (HTTPCookieProcessor, HTTPRedirectHandler, HTTPSHandler,
                            Request, build_opener)

USER_AGENT = "rpi5-bot-ap/3"


class ControllerError(Exception):
    """Błąd komunikacji z kontrolerem — tekst nadaje się do pokazania użytkownikowi."""


@dataclass
class Device:
    controller_id: str
    controller: str
    site_id: str
    site: str
    mac: str
    name: str
    model: str
    ip: str
    ok: bool
    label: str
    code: str = ""   # surowy kod stanu z API (diagnostyka)

    @property
    def key(self) -> str:
        return f"{self.controller_id}|{self.mac or self.site_id + '/' + self.name}"


@dataclass
class Snapshot:
    controller_id: str
    controller: str
    type: str
    sites: list[dict]            # [{"id": …, "name": …}]
    devices: list[Device]
    skipped: dict[str, int] = field(default_factory=dict)
    raw: list = field(default_factory=list)
    version: str = ""


# ── HTTP ──────────────────────────────────────────────────────────────────────

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Http:
    """Minimalny klient HTTP (stdlib): JSON, ciasteczka, certyfikaty self-signed."""

    def __init__(self, verify_ssl: bool = False, timeout: int = 15, cookies: bool = False):
        if verify_ssl:
            ctx = ssl.create_default_context()
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        handlers = [HTTPSHandler(context=ctx)]
        self.jar = CookieJar() if cookies else None
        if self.jar is not None:
            handlers.append(HTTPCookieProcessor(self.jar))
        self._open = build_opener(*handlers).open
        self._open_noredirect = build_opener(*handlers, _NoRedirect()).open
        self.timeout = timeout

    def request(self, method: str, url: str, params: dict | None = None, json_body=None,
                headers: dict | None = None, follow: bool = True):
        """Zwraca (status, nagłówki, treść). Brak połączenia → ControllerError."""
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT}
        hdrs.update(headers or {})
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=hdrs, method=method)
        opener = self._open if follow else self._open_noredirect
        try:
            with opener(req, timeout=self.timeout) as resp:
                return resp.status, resp.headers, resp.read()
        except HTTPError as e:
            return e.code, e.headers, e.read()
        except (URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise ControllerError(f"brak połączenia z kontrolerem: {reason}") from None

    def json(self, method: str, url: str, **kw):
        """Jak request(), ale treść jako JSON (albo None, gdy to nie JSON)."""
        status, headers, raw = self.request(method, url, **kw)
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace")) if raw else None
        except ValueError:
            payload = None
        return status, headers, payload


# ── Formatowanie ──────────────────────────────────────────────────────────────

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


def parse_hm(h: str, m: str | None) -> int:
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
                start, end = parse_hm(m[1], m[2]), parse_hm(m[3], m[4])
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
