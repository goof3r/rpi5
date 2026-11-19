"""Fabryka i pamięć podręczna klientów kontrolerów (wspólna dla monitora i komend bota)."""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from .common import ControllerError, Snapshot
from .omada import OmadaClient
from .store import defaults
from .unifi import UnifiClient

_cache: dict[str, tuple[str, object, threading.Lock]] = {}
_cache_lock = threading.Lock()
CLIENT_FIELDS = ("type", "url", "client_id", "client_secret", "omadac_id", "auth",
                 "api_key", "username", "password", "verify_ssl")


def _fingerprint(ctrl: dict, ignore_maintenance: bool) -> str:
    data = {k: ctrl.get(k) for k in CLIENT_FIELDS}
    data["_im"] = ignore_maintenance
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def make_client(ctrl: dict, ignore_maintenance: bool = False, timeout: int = 15):
    if ctrl.get("type") == "omada":
        return OmadaClient(ctrl, ignore_maintenance, timeout)
    if ctrl.get("type") == "unifi":
        return UnifiClient(ctrl, ignore_maintenance, timeout)
    raise ControllerError(f"nieznany typ kontrolera: {ctrl.get('type')}")


def snapshot(ctrl: dict, fresh: bool = False) -> Snapshot:
    """Stan kontrolera; klient (token/sesja) jest używany ponownie między wywołaniami."""
    d = defaults()
    fp = _fingerprint(ctrl, d.ignore_maintenance)
    with _cache_lock:
        entry = _cache.get(ctrl["id"])
        if fresh or not entry or entry[0] != fp:
            entry = (fp, make_client(ctrl, d.ignore_maintenance, d.timeout), threading.Lock())
            _cache[ctrl["id"]] = entry
    _, client, lock = entry
    with lock:
        snap = client.snapshot()
    snap.controller = ctrl.get("name", snap.controller)
    for dev in snap.devices:
        dev.controller = snap.controller
    return snap


def fetch_all(ctrls: list[dict]) -> list[tuple[dict, Snapshot | None, str]]:
    """Równoległe pobranie stanu kontrolerów: [(kontroler, snapshot|None, błąd)]."""
    def one(ctrl):
        try:
            return ctrl, snapshot(ctrl), ""
        except ControllerError as e:
            return ctrl, None, str(e)
        except Exception as e:  # nieoczekiwany błąd jednego kontrolera nie psuje reszty
            return ctrl, None, f"nieoczekiwany błąd: {e}"
    if not ctrls:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(ctrls))) as pool:
        return list(pool.map(one, ctrls))


def forget(cid: str) -> None:
    with _cache_lock:
        _cache.pop(cid, None)
