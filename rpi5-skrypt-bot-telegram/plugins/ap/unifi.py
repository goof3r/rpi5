"""Klient Ubiquiti UniFi Network — tylko odczyt.

Dwa sposoby dostępu:
  • auth="key"     — klucz API (UniFi Network 9+: Ustawienia → Control Plane → Integrations),
                     oficjalne Integration API: /proxy/network/integration/v1/… (nagłówek X-API-KEY).
  • auth="account" — lokalne konto z rolą „View Only”: klasyczne API
                     (UniFi OS: /api/auth/login + /proxy/network/api/…; samodzielny
                     Network Application: /api/login + /api/…), wykrywane automatycznie.
"""

from .common import ControllerError, Device, Http, Snapshot

# Integration API: pole „state”
INTEG_STATE = {
    "ONLINE": "Połączony",
    "OFFLINE": "Rozłączony",
    "PENDING_ADOPTION": "Oczekuje na adopcję",
    "UPDATING": "Aktualizacja firmware",
    "GETTING_READY": "Przygotowanie",
    "ADOPTING": "Adoptowanie",
    "DELETING": "Usuwanie",
    "CONNECTION_INTERRUPTED": "Przerwane połączenie",
    "ISOLATED": "Izolowany",
}
INTEG_OK = {"ONLINE"}
INTEG_MAINT = {"UPDATING", "GETTING_READY"}

# Klasyczne API: pole „state” (liczba)
CLASSIC_STATE = {
    0: "Rozłączony",
    1: "Połączony",
    2: "Oczekuje na adopcję",
    3: "Niezgodny firmware",
    4: "Aktualizacja firmware",
    5: "Provisioning",
    6: "Utracony kontakt (heartbeat missed)",
    7: "Adoptowanie",
    8: "Usuwanie",
    9: "Błąd komunikacji (inform)",
    10: "Adopcja nieudana",
    11: "Izolowany",
}
CLASSIC_OK = {1}
CLASSIC_MAINT = {4, 5}

INTEG_PREFIXES = ("/proxy/network/integration/v1", "/integration/v1")
PAGE_LIMIT = 200


def _is_ap(d: dict) -> bool:
    feats = d.get("features")
    if isinstance(feats, dict):
        feats = list(feats.keys())
    feats = [str(f).lower() for f in (feats or [])]
    ifaces = d.get("interfaces")
    if isinstance(ifaces, dict):
        ifaces = list(ifaces.keys())
    ifaces = [str(i).lower() for i in (ifaces or [])]
    return "accesspoint" in feats or "radios" in ifaces


def integ_state(state, ignore_maintenance=False) -> tuple[bool, str]:
    s = str(state or "").upper()
    ok = s in INTEG_OK or (ignore_maintenance and s in INTEG_MAINT)
    return ok, INTEG_STATE.get(s, f"Nieznany stan ({s or '?'})")


def classic_state(d: dict, ignore_maintenance=False) -> tuple[bool, str]:
    try:
        st = int(d.get("state"))
    except (TypeError, ValueError):
        st = None
    if d.get("adopted") is False and st != 1:
        return False, "Oczekuje na adopcję"
    ok = st in CLASSIC_OK or (ignore_maintenance and st in CLASSIC_MAINT)
    return ok, CLASSIC_STATE.get(st, f"Nieznany stan (kod {st})")


class UnifiClient:
    type = "unifi"

    def __init__(self, ctrl: dict, ignore_maintenance: bool = False, timeout: int = 15):
        self.ctrl = ctrl
        self.url = ctrl["url"].rstrip("/")
        self.auth = ctrl.get("auth") or ("key" if ctrl.get("api_key") else "account")
        self.ignore_maintenance = ignore_maintenance
        self.http = Http(verify_ssl=bool(ctrl.get("verify_ssl")), timeout=timeout,
                         cookies=self.auth == "account")
        self._integ_prefix: str | None = None
        self._unifi_os: bool | None = None
        self._prefix = ""
        self._csrf = ""
        self._logged_in = False
        self.version = ""

    # ── klucz API (Integration API) ──
    def _integ_get(self, path: str, params: dict | None = None):
        prefixes = (self._integ_prefix,) if self._integ_prefix else INTEG_PREFIXES
        statuses = []
        for pre in prefixes:
            status, _, p = self.http.json("GET", self.url + pre + path, params=params,
                                          headers={"X-API-KEY": self.ctrl.get("api_key", "")})
            if status == 200 and isinstance(p, (dict, list)):
                self._integ_prefix = pre
                return p
            if status in (401, 403):
                raise ControllerError(f"klucz API odrzucony (HTTP {status}) — sprawdź klucz")
            statuses.append(status)
        if all(s == 404 for s in statuses):
            raise ControllerError("Integration API niedostępne (HTTP 404) — wymaga UniFi Network 9+; "
                                  "dla starszych użyj konta „View Only”")
        raise ControllerError(f"kontroler odpowiada błędem (HTTP {statuses[-1]})")

    def _integ_paged(self, path: str) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while offset <= 50000:
            p = self._integ_get(path, {"offset": offset, "limit": PAGE_LIMIT})
            if isinstance(p, list):
                return items + p
            data = p.get("data") or []
            items.extend(data)
            total = p.get("totalCount")
            if not data or total is None or len(items) >= int(total):
                break
            offset += len(data)
        return items

    def _snapshot_key(self):
        try:
            info = self._integ_get("/info")
            if isinstance(info, dict):
                self.version = str(info.get("applicationVersion") or "")
        except ControllerError as e:
            if "odrzucony" in str(e):
                raise
        sites = [{"id": str(s.get("id")), "name": str(s.get("name") or s.get("internalReference") or s.get("id"))}
                 for s in self._integ_paged("/sites") if s.get("id")]
        rows = []
        for site in sites:
            for d in self._integ_paged(f"/sites/{site['id']}/devices"):
                rows.append((site, d))
        return sites, rows

    # ── konto (klasyczne API) ──
    def _detect(self) -> None:
        status, _, _ = self.http.request("GET", self.url + "/", follow=False,
                                         headers={"Accept": "text/html"})
        self._unifi_os = status == 200

    def _login(self) -> None:
        if self._unifi_os is None:
            self._detect()
        creds = {"username": self.ctrl.get("username", ""), "password": self.ctrl.get("password", "")}
        if self._unifi_os:
            status, headers, _ = self.http.json("POST", self.url + "/api/auth/login",
                                                json_body={**creds, "remember": True})
            if status == 200:
                self._csrf = headers.get("X-CSRF-Token") or headers.get("X-Updated-CSRF-Token") or ""
                self._prefix = "/proxy/network"
                self._logged_in = True
                return
        else:
            status, _, p = self.http.json("POST", self.url + "/api/login", json_body=creds)
            if status == 200 and ((p or {}).get("meta") or {}).get("rc") == "ok":
                self._prefix = ""
                self._logged_in = True
                return
        if status == 429:
            raise ControllerError("za dużo prób logowania (HTTP 429) — odczekaj kilka minut")
        raise ControllerError(f"logowanie nieudane (HTTP {status}) — sprawdź login i hasło; "
                              "konto musi być lokalne (bez 2FA)")

    def _classic_get(self, path: str) -> list:
        for attempt in (1, 2):
            if not self._logged_in:
                self._login()
            headers = {"X-CSRF-Token": self._csrf} if self._csrf else {}
            status, _, p = self.http.json("GET", self.url + self._prefix + path, headers=headers)
            if status == 401 and attempt == 1:
                self._logged_in = False
                continue
            meta = (p or {}).get("meta") or {} if isinstance(p, dict) else {}
            if status == 200 and isinstance(p, dict) and meta.get("rc", "ok") == "ok":
                return p.get("data") or []
            raise ControllerError(f"{path}: HTTP {status} {meta.get('msg', '')}".strip())
        raise ControllerError(f"{path}: autoryzacja nieudana")

    def _snapshot_account(self):
        sites = [{"id": str(s.get("name")), "name": str(s.get("desc") or s.get("name"))}
                 for s in self._classic_get("/api/self/sites") if s.get("name")]
        rows = []
        for site in sites:
            for d in self._classic_get(f"/api/s/{site['id']}/stat/device"):
                rows.append((site, d))
        return sites, rows

    # ── wspólne ──
    def snapshot(self) -> Snapshot:
        cid, cname = self.ctrl.get("id", ""), self.ctrl.get("name", "UniFi")
        sites, rows = self._snapshot_key() if self.auth == "key" else self._snapshot_account()
        sites.sort(key=lambda s: s["name"].lower())
        devices, skipped, raw = [], {}, []
        for site, d in rows:
            raw.append({"_site": site["name"], **d})
            if self.auth == "key":
                if not _is_ap(d):
                    skipped["inne"] = skipped.get("inne", 0) + 1
                    continue
                ok, label = integ_state(d.get("state"), self.ignore_maintenance)
                code = str(d.get("state"))
                mac, ip = d.get("macAddress"), d.get("ipAddress")
            else:
                dtype = str(d.get("type") or "").lower()
                if dtype != "uap":
                    skipped[dtype or "?"] = skipped.get(dtype or "?", 0) + 1
                    continue
                ok, label = classic_state(d, self.ignore_maintenance)
                code = str(d.get("state"))
                mac, ip = d.get("mac"), d.get("ip")
            devices.append(Device(
                controller_id=cid, controller=cname, site_id=site["id"], site=site["name"],
                mac=str(mac or "").upper().replace(":", "-"), name=str(d.get("name") or mac or "?"),
                model=str(d.get("model") or ""), ip=str(ip or ""), ok=ok, label=label, code=code))
        devices.sort(key=lambda x: (x.site.lower(), x.name.lower()))
        return Snapshot(cid, cname, self.type, sites, devices, skipped, raw, version=self.version)
