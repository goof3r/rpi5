"""Pliki danych wtyczki AP: kontrolery, ustawienia powiadomień, stan monitora.

Wszystko w AP_DATA_DIR (domyślnie /var/lib/ap-monitor), pliki 0600 — zawierają
dane dostępowe kontrolerów. Zmieniane z Telegrama, bez restartu bota.
"""

import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .common import format_windows, in_windows, parse_minutes, parse_windows, Window

log = logging.getLogger("ap-monitor")
_lock = threading.RLock()

LEGACY_DIR = Path("/var/lib/omada-monitor")
LEGACY_UNIT = Path("/etc/systemd/system/omada-monitor.service")
SECRET_FIELDS = ("client_secret", "api_key", "password")


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n, "").strip().strip('"').strip("'")
        if v:
            return v
    return default


def data_dir() -> Path:
    return Path(_env("AP_DATA_DIR", default="/var/lib/ap-monitor"))


def controllers_file() -> Path:
    return data_dir() / "controllers.json"


def settings_file() -> Path:
    return data_dir() / "settings.json"


def state_file() -> Path:
    return data_dir() / "state.json"


def _read_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        log.warning("Nie mogę odczytać %s (%s)", path, e)
        return default


def write_json(path: Path, data) -> None:
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        tmp = path.with_name(path.name + f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)


# ── Ustawienia domyślne (zmienne środowiskowe) ────────────────────────────────

@dataclass
class Defaults:
    interval: int
    remind_minutes: list[float]
    notify_hours: str
    confirm_checks: int
    controller_fail_checks: int
    ignore_maintenance: bool
    timeout: int


def _int_env(names, default, minimum):
    try:
        return max(minimum, int(_env(*names, default=str(default))))
    except ValueError:
        return default


def defaults() -> Defaults:
    remind = []
    for part in re.split(r"[,;\s]+", _env("AP_REMIND_MINUTES", "OMADA_REMIND_MINUTES", default="30,60,180")):
        if part:
            try:
                remind.append(parse_minutes(part))
            except ValueError:
                pass
    return Defaults(
        interval=_int_env(("AP_INTERVAL", "OMADA_INTERVAL"), 60, 10),
        remind_minutes=sorted({m for m in remind if m > 0}),
        notify_hours=_env("AP_NOTIFY_HOURS", "OMADA_NOTIFY_HOURS", default="zawsze"),
        confirm_checks=_int_env(("AP_CONFIRM_CHECKS", "OMADA_CONFIRM_CHECKS"), 1, 1),
        controller_fail_checks=_int_env(("AP_CONTROLLER_FAIL_CHECKS", "OMADA_CONTROLLER_FAIL_CHECKS"), 3, 1),
        ignore_maintenance=_env("AP_IGNORE_MAINTENANCE", "OMADA_IGNORE_MAINTENANCE", default="0").lower()
        in ("1", "true", "yes", "tak", "on"),
        timeout=_int_env(("AP_TIMEOUT", "OMADA_TIMEOUT"), 15, 3),
    )


# ── Kontrolery ────────────────────────────────────────────────────────────────

TYPE_LABEL = {"omada": "Omada", "unifi": "UniFi"}


def load_controllers() -> list[dict]:
    with _lock:
        data = _read_json(controllers_file(), {})
        ctrls = data.get("controllers") if isinstance(data, dict) else None
        if ctrls is None:
            ctrls = _migrate_env_controller()
        return [c for c in ctrls if isinstance(c, dict) and c.get("id") and c.get("type") in TYPE_LABEL]


def save_controllers(ctrls: list[dict]) -> None:
    write_json(controllers_file(), {"controllers": ctrls, "updated": time.time()})


def get_controller(cid: str) -> dict | None:
    return next((c for c in load_controllers() if c["id"] == cid), None)


def add_controller(rec: dict) -> str:
    with _lock:
        ctrls = load_controllers()
        used = {c["id"] for c in ctrls}
        n = 1
        while f"c{n}" in used:
            n += 1
        rec = {k: v for k, v in rec.items() if v not in (None, "")}
        rec["id"] = f"c{n}"
        rec.setdefault("verify_ssl", False)
        rec.setdefault("alerts", False)
        rec["added"] = time.time()
        ctrls.append(rec)
        save_controllers(ctrls)
        return rec["id"]


def update_controller(cid: str, **changes) -> dict | None:
    with _lock:
        ctrls = load_controllers()
        for c in ctrls:
            if c["id"] == cid:
                c.update(changes)
                save_controllers(ctrls)
                return c
    return None


def remove_controller(cid: str) -> dict | None:
    with _lock:
        ctrls = load_controllers()
        gone = next((c for c in ctrls if c["id"] == cid), None)
        if gone:
            save_controllers([c for c in ctrls if c["id"] != cid])
        return gone


def _migrate_env_controller() -> list[dict]:
    """Jednorazowo: kontroler Omada z /etc/server-monitor.env (OMADA_*) z wersji 2.3."""
    url = _env("OMADA_URL")
    if not (url and _env("OMADA_CLIENT_ID") and _env("OMADA_CLIENT_SECRET")):
        return []
    ctrl = {"id": "c1", "name": "Omada", "type": "omada", "url": url.rstrip("/"),
            "client_id": _env("OMADA_CLIENT_ID"), "client_secret": _env("OMADA_CLIENT_SECRET"),
            "omadac_id": _env("OMADA_ID"), "verify_ssl": _env("OMADA_VERIFY_SSL", default="0") == "1",
            "alerts": True, "added": time.time(), "migrated": True}
    ctrl = {k: v for k, v in ctrl.items() if v != ""}
    save_controllers([ctrl])
    log.info("Przeniesiono kontroler Omada z OMADA_* do %s", controllers_file())
    return [ctrl]


# ── Ustawienia powiadomień ────────────────────────────────────────────────────

def load_settings() -> dict:
    return _read_json(settings_file(), {})


def save_settings(settings: dict) -> None:
    if settings:
        settings["updated"] = time.time()
        write_json(settings_file(), settings)
    else:
        try:
            settings_file().unlink()
        except FileNotFoundError:
            pass


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


def notify_policy(settings: dict | None = None) -> NotifyPolicy:
    d = defaults()
    settings = load_settings() if settings is None else settings
    text = settings.get("windows", d.notify_hours)
    try:
        windows = parse_windows(str(text))
    except ValueError as e:
        log.warning("Niepoprawne godziny powiadomień %r (%s) — wysyłam zawsze", text, e)
        windows = []
    remind = settings.get("remind_minutes")
    try:
        remind = sorted({float(m) for m in remind if float(m) > 0}) if isinstance(remind, list) else d.remind_minutes
    except (TypeError, ValueError):
        remind = d.remind_minutes
    try:
        pause = float(settings.get("pause_until") or 0) or None
    except (TypeError, ValueError):
        pause = None
    return NotifyPolicy(windows, remind, pause)


# ── Stan monitora ─────────────────────────────────────────────────────────────

def load_state() -> dict:
    state = _read_json(state_file(), {})
    for key, default in (("controllers", {}), ("devices", {}), ("outbox", []), ("quiet_events", [])):
        if not isinstance(state.get(key), type(default)):
            state[key] = default
    return state


def save_state(state: dict) -> None:
    write_json(state_file(), state)


# ── Migracja z wersji 2.3 (osobna usługa omada-monitor) ───────────────────────

def migrate_legacy(run=None) -> list[str]:
    """Przenosi ustawienia z /var/lib/omada-monitor i wyłącza starą usługę. Zwraca opis zmian."""
    notes = []
    if not controllers_file().exists():
        migrated = load_controllers()      # tworzy plik z OMADA_*, jeśli były
        if migrated:
            notes.append("Kontroler Omada z /etc/server-monitor.env przeniesiony do wtyczki AP.")
    old_settings = LEGACY_DIR / "settings.json"
    if old_settings.exists() and not settings_file().exists():
        try:
            shutil.copy2(old_settings, settings_file())
            notes.append("Przeniesiono ustawienia powiadomień (godziny, przypomnienia).")
        except OSError as e:
            log.warning("Migracja ustawień: %s", e)
    if run and LEGACY_UNIT.exists():
        run("systemctl disable --now omada-monitor.service", timeout=30)
        try:
            LEGACY_UNIT.unlink()
        except OSError:
            pass
        run("systemctl daemon-reload", timeout=30)
        notes.append("Wyłączono starą usługę omada-monitor — monitor działa teraz w bocie.")
    return notes
