"""Monitor AP: wykrywa zmiany stanu na kontrolerach z włączonymi alertami i składa wiadomości.

Monitor nie wysyła sam — check_all() zwraca listę wiadomości, a bot wysyła je
(i oddaje przez requeue() te, których nie udało się dostarczyć).
"""

import logging
import threading
import time
from dataclasses import dataclass, field

from . import clients, store
from .common import esc, fmt_duration, fmt_time, Snapshot

log = logging.getLogger("ap-monitor")
QUIET_EVENTS_MAX = 30
OUTBOX_MAX = 20


def dev_line(e: dict, extra: str = "", with_controller: bool = False) -> str:
    meta = " · ".join(x for x in ((e.get("controller") if with_controller else None),
                                  e.get("site"), e.get("model"), e.get("ip")) if x)
    line = f"• <b>{esc(e.get('name'))}</b> — {esc(e.get('label'))}"
    if extra:
        line += extra if extra.startswith("\n") else f" {extra}"
    if meta:
        line += f"\n   <i>{esc(meta)}</i>"
    return line


@dataclass
class Report:
    multi: bool = False
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
            head = "🔴 <b>AP niepołączony</b>" if len(self.down) == 1 else \
                   f"🔴 <b>Niepołączone AP ({len(self.down)})</b>"
            parts.append(head + "\n" + "\n".join(self.down))
        if self.reminders and include_reminders:
            body = "⏰ <b>AP nadal niepołączony</b>\n" + "\n".join(self.reminders)
            if self.last_reminder:
                body += "\n<i>To ostatnie przypomnienie — następna wiadomość, gdy AP wróci.</i>"
            parts.append(body)
        if self.recovered:
            parts.append("✅ <b>Znów połączone</b>\n" + "\n".join(self.recovered))
        if self.info:
            parts.append("ℹ️ <b>Access pointy</b>\n" + "\n".join(self.info))
        return "\n\n".join(parts)


def render_outgoing(item: dict) -> str:
    if item.get("delayed"):
        return f"⌛ <i>Wiadomość opóźniona (z {fmt_time(item['ts'])})</i>\n{item['text']}"
    return item["text"]


class Monitor:
    def __init__(self):
        self.state = store.load_state()
        self.remind: list[float] = []
        self._lock = threading.Lock()

    def _due(self, since: float, now: float) -> int:
        elapsed = (now - since) / 60
        return sum(1 for m in self.remind if elapsed >= m)

    # ── wejście ──
    def check_all(self, now: float | None = None) -> list[dict]:
        with self._lock:
            now = time.time() if now is None else now
            d = store.defaults()
            policy = store.notify_policy()
            self.remind = policy.remind_minutes
            ctrls = [c for c in store.load_controllers() if c.get("alerts")]
            report = Report(multi=len(ctrls) > 1)
            self._prune({c["id"] for c in ctrls})
            for ctrl, snap, err in clients.fetch_all(ctrls):
                if snap is None:
                    self._controller_failed(ctrl, err, now, report, d.controller_fail_checks)
                else:
                    self._evaluate(ctrl, snap, now, report, d.confirm_checks)
            out = self._dispatch(report, policy.muted_reason(now), now)
            store.save_state(self.state)
            return out

    def requeue(self, item: dict) -> None:
        with self._lock:
            item = dict(item)
            item["tries"] = int(item.get("tries") or 0) + 1
            if item["tries"] >= 10:
                log.error("Porzucam wiadomość po %d próbach: %.200s", item["tries"], item.get("text"))
                return
            outbox = self.state.setdefault("outbox", [])
            outbox.append(item)
            del outbox[:-OUTBOX_MAX]
            store.save_state(self.state)

    # ── logika ──
    def _prune(self, active: set) -> None:
        """Kontrolery usunięte lub z wyłączonymi alertami — zapomnij ich stan."""
        for cid in [c for c in self.state["controllers"] if c not in active]:
            self.state["controllers"].pop(cid, None)
        for key in [k for k in self.state["devices"] if k.split("|", 1)[0] not in active]:
            self.state["devices"].pop(key, None)

    def _evaluate(self, ctrl: dict, snap: Snapshot, now: float, report: Report, confirm: int) -> None:
        cid = ctrl["id"]
        cst = self.state["controllers"].get(cid) or {}
        if cst.get("alerted"):
            report.recovered.append(f"• Kontroler <b>{esc(snap.controller)}</b> znów odpowiada "
                                    f"(przerwa {fmt_duration(now - cst.get('fail_since', now))})")
        # Po przerwie w łączności AP przez chwilę wiszą jako rozłączone, zanim się
        # zgłoszą — w tym jednym sprawdzeniu nie alarmujemy o nowych awariach.
        warmup = int(cst.get("fail_count") or 0) > 0
        first_run = not cst.get("initialized")
        self.state["controllers"][cid] = {"initialized": True, "fail_count": 0, "last_ok": now}

        known = self.state["devices"]
        seen = set()
        total = len(self.remind)
        for dev in snap.devices:
            seen.add(dev.key)
            prev = known.get(dev.key)
            e = prev or {"bad_checks": 0, "alerted": False, "reminders": 0, "bad_since": None}
            e.update({"name": dev.name, "controller": snap.controller, "site": dev.site,
                      "model": dev.model, "ip": dev.ip, "mac": dev.mac, "label": dev.label, "ok": dev.ok})
            known[dev.key] = e
            if dev.ok:
                if e.get("alerted"):
                    report.recovered.append(dev_line(
                        e, f"(przerwa {fmt_duration(now - (e.get('bad_since') or now))})", report.multi))
                e.update({"bad_checks": 0, "alerted": False, "reminders": 0, "bad_since": None})
                if prev is None and not first_run:
                    report.info.append(f"• Nowy AP: <b>{esc(dev.name)}</b> ({esc(dev.site)}) — {esc(dev.label)}")
                continue
            if not warmup:
                e["bad_checks"] = int(e.get("bad_checks") or 0) + 1
            if not e.get("bad_since"):
                e["bad_since"] = now
            if not e.get("alerted"):
                if e["bad_checks"] >= confirm:
                    e["alerted"] = True
                    e["reminders"] = self._due(e["bad_since"], now)
                    report.new_down_keys.add(dev.key)
                    report.down.append(dev_line(
                        e, f"\n   od {fmt_time(e['bad_since'])} · MAC {esc(dev.mac)}", report.multi))
                continue
            due = self._due(e["bad_since"], now)
            if due > int(e.get("reminders") or 0):
                e["reminders"] = due
                report.reminders.append(dev_line(
                    e, f"\n   od {fmt_time(e['bad_since'])} ({fmt_duration(now - e['bad_since'])}) · "
                       f"przypomnienie {due}/{total}", report.multi))
                if due >= total:
                    report.last_reminder = True

        prefix = cid + "|"
        for key in [k for k in known if k.startswith(prefix) and k not in seen]:
            gone = known.pop(key)
            report.info.append(f"• AP <b>{esc(gone.get('name'))}</b> ({esc(gone.get('site'))}) "
                               f"zniknął z kontrolera — przestaję go pilnować")

        if first_run:
            n_ok = sum(1 for x in snap.devices if x.ok)
            if not report.intro:
                report.intro.append("🟢 <b>Monitor AP — alerty włączone</b>")
            line = (f"• <b>{esc(snap.controller)}</b> ({store.TYPE_LABEL.get(snap.type, snap.type)}): "
                    f"AP {len(snap.devices)}, połączonych {n_ok}/{len(snap.devices)}")
            if not snap.devices:
                line += " — ⚠️ nie widzę żadnego AP (uprawnienia do lokalizacji?)"
            report.intro.append(line)

    def _controller_failed(self, ctrl: dict, error: str, now: float, report: Report, fail_checks: int) -> None:
        cst = self.state["controllers"].setdefault(ctrl["id"], {})
        cst["fail_count"] = int(cst.get("fail_count") or 0) + 1
        cst.setdefault("fail_since", now)
        cst["last_error"] = error
        name = esc(ctrl.get("name"))
        log.warning("Kontroler %s: %s (błąd %d z rzędu)", ctrl.get("name"), error, cst["fail_count"])
        if not cst.get("alerted"):
            if cst["fail_count"] >= fail_checks:
                cst["alerted"] = True
                cst["reminders"] = self._due(cst["fail_since"], now)
                report.down.append(f"• <b>Brak odpowiedzi z kontrolera {name}</b> od {fmt_time(cst['fail_since'])}\n"
                                   f"   <i>{esc(error)}</i>\n   Stanu jego AP nie da się teraz sprawdzić.")
            return
        due = self._due(cst["fail_since"], now)
        if due > int(cst.get("reminders") or 0):
            cst["reminders"] = due
            report.reminders.append(f"• Kontroler <b>{name}</b> nadal nie odpowiada "
                                    f"({fmt_duration(now - cst['fail_since'])}) · przypomnienie {due}/{len(self.remind)}\n"
                                    f"   <i>{esc(error)}</i>")
            if due >= len(self.remind):
                report.last_reminder = True

    # ── godziny ciszy, pauza, kolejka ──
    def _dispatch(self, report: Report, muted: str | None, now: float) -> list[dict]:
        prev = self.state.get("muted")
        out: list[dict] = []
        if muted is None:
            for item in self.state.get("outbox") or []:
                out.append({**item, "delayed": True})
            self.state["outbox"] = []
            text = report.render()
            if prev:
                digest = self._digest(prev, report.new_down_keys, now, report.multi)
                if digest:
                    text = "\n\n".join(x for x in (digest, report.render(include_reminders=False)) if x)
            if text:
                out.append({"ts": now, "text": text})
        elif muted == "quiet":
            text = report.render(include_reminders=False)
            if text:
                events = self.state.setdefault("quiet_events", [])
                events.append({"ts": now, "text": text})
                if len(events) > QUIET_EVENTS_MAX:
                    self.state["quiet_dropped"] = int(self.state.get("quiet_dropped") or 0) + 1
                    events.pop(0)
        self.state["muted"] = muted
        return out

    def _digest(self, reason: str, exclude: set, now: float, multi: bool) -> str:
        events = self.state.pop("quiet_events", None) or []
        self.state["quiet_events"] = []
        dropped = int(self.state.pop("quiet_dropped", 0) or 0)
        down_now = [e for k, e in self.state["devices"].items()
                    if not e.get("ok") and e.get("alerted") and k not in exclude and e.get("bad_since")]
        ctl_down = [c for c in self.state["controllers"].values() if c.get("alerted")]
        if reason == "pause":
            head = "▶️ <b>Koniec pauzy powiadomień</b>"
        elif not events and not down_now and not ctl_down:
            return ""
        else:
            head = "🌅 <b>Podsumowanie spoza godzin powiadomień</b>"
        parts = [head]
        if dropped:
            parts.append(f"<i>(pominięto starszych zdarzeń: {dropped})</i>")
        for ev in events:
            parts.append(f"🕐 <b>{fmt_time(ev['ts'])}</b>\n{ev['text']}")
        if ctl_down:
            parts.append(f"<b>Kontrolery bez odpowiedzi: {len(ctl_down)}</b>")
        if down_now:
            parts.append("<b>Teraz niepołączone:</b>\n" + "\n".join(
                dev_line(e, f"(od {fmt_time(e['bad_since'])}, {fmt_duration(now - e['bad_since'])})", multi)
                for e in down_now))
        elif not ctl_down:
            parts.append("Teraz wszystkie AP są połączone ✅")
        return "\n\n".join(parts)
