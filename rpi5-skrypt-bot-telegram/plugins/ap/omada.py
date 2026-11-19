"""Klient TP-Link Omada — Open API (tryb Client Credentials, dostęp tylko do odczytu).

Zweryfikowane na kontrolerze 6.3.0.45 (dokumentacja Swagger z kontrolera, grupa „03 Device”).
"""

import time

from .common import ControllerError, Device, Http, Snapshot

# detailStatus — szczegółowy stan, jak w kolumnie „Status” panelu
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
STATUS = {0: "Rozłączony", 1: "Połączony", 2: "Oczekuje",
          3: "Utracony kontakt (heartbeat missed)", 4: "Izolowany"}
CONNECTED_DETAIL = {14, 15, 16, 17}
MAINTENANCE_DETAIL = {10, 11, 12, 13, 50}
TOKEN_ERRORS = {-44112, -44113, -44116}   # wygasły/nieważny token → zaloguj ponownie
PAGE_SIZE = 100


def _int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def device_state(d: dict, ignore_maintenance: bool = False) -> tuple[bool, str, str]:
    """(połączony?, opis PL, surowy kod) dla rekordu urządzenia z Open API."""
    status, detail = _int(d.get("status")), _int(d.get("detailStatus"))
    code = f"{status}/{detail}"
    if detail is not None:
        ok = detail in CONNECTED_DETAIL or (ignore_maintenance and detail in MAINTENANCE_DETAIL)
        return ok, DETAIL_STATUS.get(detail, f"Nieznany stan (kod {detail})"), code
    if status is not None:
        return status == 1, STATUS.get(status, f"Nieznany stan (kod {status})"), code
    return False, "Nieznany stan", code


class OmadaClient:
    type = "omada"

    def __init__(self, ctrl: dict, ignore_maintenance: bool = False, timeout: int = 15):
        self.ctrl = ctrl
        self.url = ctrl["url"].rstrip("/")
        self.ignore_maintenance = ignore_maintenance
        self.http = Http(verify_ssl=bool(ctrl.get("verify_ssl")), timeout=timeout)
        self._token: str | None = None
        self._token_exp = 0.0
        self._omadac_id = ctrl.get("omadac_id") or ""
        self._info: dict | None = None

    def info(self) -> dict:
        if self._info is None:
            status, _, p = self.http.json("GET", self.url + "/api/info")
            if not isinstance(p, dict) or p.get("errorCode") != 0:
                raise ControllerError(f"to nie wygląda na kontroler Omada (/api/info: HTTP {status})")
            self._info = p.get("result") or {}
        return self._info

    def omadac_id(self) -> str:
        if not self._omadac_id:
            self._omadac_id = self.info().get("omadacId", "")
            if not self._omadac_id:
                raise ControllerError("kontroler nie zwrócił omadacId")
        return self._omadac_id

    def _authenticate(self) -> None:
        status, _, p = self.http.json(
            "POST", self.url + "/openapi/authorize/token",
            params={"grant_type": "client_credentials"},
            json_body={"omadacId": self.omadac_id(),
                       "client_id": self.ctrl.get("client_id", ""),
                       "client_secret": self.ctrl.get("client_secret", "")},
        )
        res = (p or {}).get("result") or {}
        if not isinstance(p, dict) or p.get("errorCode") != 0 or not res.get("accessToken"):
            msg = p.get("msg") if isinstance(p, dict) else f"HTTP {status}"
            code = p.get("errorCode") if isinstance(p, dict) else "?"
            raise ControllerError(f"logowanie do Open API nieudane: {msg} (kod {code}) — sprawdź Client ID/Secret")
        self._token = res["accessToken"]
        self._token_exp = time.time() + max(60, int(res.get("expiresIn") or 7200) - 120)

    def _get(self, path: str, params: dict | None = None):
        for attempt in (1, 2):
            if not self._token or time.time() >= self._token_exp:
                self._authenticate()
            status, _, p = self.http.json(
                "GET", f"{self.url}/openapi/v1/{self.omadac_id()}{path}", params=params,
                headers={"Authorization": f"AccessToken={self._token}"})
            if not isinstance(p, dict):
                raise ControllerError(f"{path}: HTTP {status}, odpowiedź nie jest JSON-em")
            code = p.get("errorCode")
            if code == 0:
                return p.get("result")
            if attempt == 1 and (status == 401 or code in TOKEN_ERRORS):
                self._token = None
                continue
            raise ControllerError(f"{path}: {p.get('msg')} (kod {code}, HTTP {status})")
        raise ControllerError(f"{path}: autoryzacja nieudana")

    def _paged(self, path: str) -> list[dict]:
        items: list[dict] = []
        page = 1
        while page <= 200:
            res = self._get(path, {"page": page, "pageSize": PAGE_SIZE})
            if isinstance(res, list):
                return items + res
            data = (res or {}).get("data") or []
            items.extend(data)
            total = (res or {}).get("totalRows")
            if not data or total is None or len(items) >= int(total):
                break
            page += 1
        return items

    def snapshot(self) -> Snapshot:
        cid, cname = self.ctrl.get("id", ""), self.ctrl.get("name", "Omada")
        sites = []
        for s in self._paged("/sites"):
            sid = s.get("siteId") or s.get("id")
            if sid:
                sites.append({"id": str(sid), "name": str(s.get("name") or sid)})
        sites.sort(key=lambda s: s["name"].lower())
        devices, skipped, raw = [], {}, []
        for site in sites:
            for d in self._paged(f"/sites/{site['id']}/devices"):
                raw.append({"_site": site["name"], **d})
                dtype = str(d.get("type") or "").lower()
                if dtype != "ap":
                    skipped[dtype or "?"] = skipped.get(dtype or "?", 0) + 1
                    continue
                ok, label, code = device_state(d, self.ignore_maintenance)
                devices.append(Device(
                    controller_id=cid, controller=cname, site_id=site["id"], site=site["name"],
                    mac=str(d.get("mac") or "").upper(), name=str(d.get("name") or d.get("mac") or "?"),
                    model=str(d.get("model") or d.get("showModel") or ""), ip=str(d.get("ip") or ""),
                    ok=ok, label=label, code=code))
        devices.sort(key=lambda x: (x.site.lower(), x.name.lower()))
        return Snapshot(cid, cname, self.type, sites, devices, skipped, raw,
                        version=str((self._info or {}).get("controllerVer", "")))
