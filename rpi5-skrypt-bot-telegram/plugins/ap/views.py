"""Teksty i przyciski dla Telegrama (bez zależności od python-telegram-bot).

Każda funkcja zwraca tekst HTML, a widoki z przyciskami — (tekst, przyciski),
gdzie przyciski to [[(etykieta, callback_data), …], …].
"""

import re
import time
from datetime import datetime, timedelta

from . import clients, store
from .common import (esc, fmt_duration, fmt_minutes, fmt_time, format_windows, parse_hm,
                     parse_minutes, parse_windows)

Buttons = list[list[tuple[str, str]]]
TYPE_CODES = {"omada": "o", "unifi": "u"}
CODE_TYPES = {v: k for k, v in TYPE_CODES.items()}


def _icon(ok: int, total: int) -> str:
    return "⚪" if not total else ("🟢" if ok == total else "🔴")


def _ctrl_label(c: dict) -> str:
    return f"{store.TYPE_LABEL.get(c['type'], c['type'])}{'' if c.get('alerts') else ', podgląd'}"


def _filtered(type_filter: str | None) -> list[dict]:
    return [c for c in store.load_controllers() if not type_filter or c["type"] == type_filter]


def _no_controllers(type_filter: str | None) -> tuple[str, Buttons]:
    what = store.TYPE_LABEL.get(type_filter, "") if type_filter else ""
    return (f"📡 Brak skonfigurowanych kontrolerów{(' ' + what) if what else ''}.\n"
            "Dodaj kontroler: /kontroler_dodaj"), [[("➕ Dodaj kontroler", "apc:add")]]


def _since(known: dict, dev, now: float) -> str:
    since = (known.get(dev.key) or {}).get("bad_since")
    return f"(od {fmt_time(since)}, {fmt_duration(now - since)})" if since else ""


def policy_line(now: float | None = None) -> str:
    policy = store.notify_policy()
    now = time.time() if now is None else now
    muted = policy.muted_reason(now)
    state = {"pause": f"⏸ pauza do {fmt_time(policy.pause_until or now)}",
             "quiet": "🌙 teraz poza godzinami"}.get(muted, "teraz aktywne")
    return f"🔔 Alerty: <b>{esc(policy.windows_text)}</b> · {state}"


# ── /ap, /omada, /unifi ───────────────────────────────────────────────────────

def summary(type_filter: str | None = None) -> tuple[str, Buttons]:
    ctrls = _filtered(type_filter)
    if not ctrls:
        return _no_controllers(type_filter)
    results = clients.fetch_all(ctrls)
    known = store.load_state().get("devices", {})
    now = time.time()
    total = sum(len(s.devices) for _, s, _ in results if s)
    bad = [d for _, s, _ in results if s for d in s.devices if not d.ok]
    errors = [c for c, s, _ in results if not s]
    icon = "🔴" if bad or errors else "🟢"
    lines = ["📡 <b>Access pointy</b>",
             f"{icon} Połączone: <b>{total - len(bad)}/{total}</b> · kontrolery: {len(ctrls)}", ""]
    buttons: Buttons = []
    row: list[tuple[str, str]] = []
    for ctrl, snap, err in results:
        if not snap:
            lines.append(f"❌ <b>{esc(ctrl['name'])}</b> ({_ctrl_label(ctrl)}) — {esc(err)}")
            continue
        n_ok = sum(1 for d in snap.devices if d.ok)
        lines.append(f"{_icon(n_ok, len(snap.devices))} <b>{esc(ctrl['name'])}</b> "
                     f"({_ctrl_label(ctrl)}) — {n_ok}/{len(snap.devices)}")
        for site in snap.sites:
            devs = [d for d in snap.devices if d.site_id == site["id"]]
            s_ok = sum(1 for d in devs if d.ok)
            if len(snap.sites) > 1:
                lines.append(f"   {_icon(s_ok, len(devs))} {esc(site['name'])} — {s_ok}/{len(devs)}")
            label = site["name"] if len(ctrls) == 1 else f"{ctrl['name']}: {site['name']}"
            row.append((f"{_icon(s_ok, len(devs))} {label[:28]} ({s_ok}/{len(devs)})",
                        f"ap:s:{ctrl['id']}:{site['id']}"[:64]))
            if len(row) == 2:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)
    if bad:
        lines += ["", "<b>Problemy:</b>"]
        for d in bad:
            meta = " · ".join(x for x in ((d.controller if len(ctrls) > 1 else None), d.site, d.model, d.ip) if x)
            lines.append(f"• <b>{esc(d.name)}</b> — {esc(d.label)} {_since(known, d, now)}".rstrip()
                         + (f"\n   <i>{esc(meta)}</i>" if meta else ""))
    lines += ["", policy_line(now)]
    t = TYPE_CODES.get(type_filter, "")
    buttons.append([("📋 Wszystkie AP", f"ap:all:{t}"), ("🔄 Odśwież", f"ap:sum:{t}")])
    buttons.append([("⚙️ Kontrolery", "apc:list")])
    return "\n".join(lines), buttons


def _site_block(ctrl: dict, snap, site: dict, known: dict, now: float, with_ctrl: bool) -> str:
    devs = [d for d in snap.devices if d.site_id == site["id"]]
    bad = [d for d in devs if not d.ok]
    good = [d for d in devs if d.ok]
    title = f"{esc(ctrl['name'])} · {esc(site['name'])}" if with_ctrl else esc(site["name"])
    lines = [f"📍 <b>{title}</b> {_icon(len(good), len(devs))} połączone {len(good)}/{len(devs)}"]
    for d in bad:
        meta = " · ".join(x for x in (d.model, d.ip, d.mac) if x)
        lines.append(f"🔴 <b>{esc(d.name)}</b> — {esc(d.label)} {_since(known, d, now)}".rstrip()
                     + (f"\n     <i>{esc(meta)}</i>" if meta else ""))
    for d in good:
        meta = " · ".join(x for x in (d.model, d.ip) if x)
        lines.append(f"🟢 {esc(d.name)}" + (f" <i>{esc(meta)}</i>" if meta else ""))
    if not devs:
        lines.append("<i>brak AP w tej lokalizacji</i>")
    return "\n".join(lines)


def site_view(cid: str, site_id: str) -> tuple[str, Buttons]:
    ctrl = store.get_controller(cid)
    back = [("⬅️ Podsumowanie", "ap:sum:")]
    if not ctrl:
        return "Ten kontroler został usunięty.", [back]
    (_, snap, err), = clients.fetch_all([ctrl])
    if not snap:
        return f"❌ <b>{esc(ctrl['name'])}:</b> {esc(err)}", [[("🔄 Spróbuj ponownie", f"ap:s:{cid}:{site_id}"[:64])] + back]
    site = next((s for s in snap.sites if s["id"] == site_id), None)
    if not site:
        return "Nie ma już takiej lokalizacji.", [back]
    known = store.load_state().get("devices", {})
    multi = len(store.load_controllers()) > 1
    return (_site_block(ctrl, snap, site, known, time.time(), multi),
            [[("🔄 Odśwież", f"ap:s:{cid}:{site_id}"[:64])] + back])


def search(query: str, type_filter: str | None = None) -> tuple[str, Buttons]:
    """/ap <tekst> — lokalizacja albo kontroler po fragmencie nazwy."""
    ctrls = _filtered(type_filter)
    if not ctrls:
        return _no_controllers(type_filter)
    q = query.strip().lower()
    results = clients.fetch_all(ctrls)
    matches = []
    for ctrl, snap, _ in results:
        if not snap:
            continue
        for site in snap.sites:
            if q in site["name"].lower() or q in ctrl["name"].lower():
                matches.append((ctrl, snap, site))
    exact = [m for m in matches if m[2]["name"].lower() == q]
    matches = exact or matches
    back = [("⬅️ Podsumowanie", f"ap:sum:{TYPE_CODES.get(type_filter, '')}")]
    if len(matches) == 1:
        ctrl, snap, site = matches[0]
        known = store.load_state().get("devices", {})
        return (_site_block(ctrl, snap, site, known, time.time(), len(store.load_controllers()) > 1),
                [[("🔄 Odśwież", f"ap:s:{ctrl['id']}:{site['id']}"[:64])] + back])
    if matches and len(matches) <= 20 and all(m[0]["id"] == matches[0][0]["id"] for m in matches) \
            and q in matches[0][0]["name"].lower():
        # pasuje nazwa kontrolera → wszystkie jego lokalizacje
        ctrl, snap, _ = matches[0]
        known = store.load_state().get("devices", {})
        blocks = [_site_block(ctrl, snap, s, known, time.time(), True) for s in snap.sites]
        return "\n\n".join(blocks), [back]
    rows: Buttons = []
    for ctrl, snap, site in matches[:20]:
        devs = [d for d in snap.devices if d.site_id == site["id"]]
        s_ok = sum(1 for d in devs if d.ok)
        rows.append([(f"{_icon(s_ok, len(devs))} {ctrl['name']}: {site['name']}"[:40],
                      f"ap:s:{ctrl['id']}:{site['id']}"[:64])])
    if matches:
        return f"🔎 Do „{esc(query)}” pasuje kilka lokalizacji — wybierz:", rows + [back]
    return f"🔎 Nie znalazłem lokalizacji ani kontrolera „{esc(query)}”.", [back]


def all_view(type_filter: str | None = None) -> tuple[str, Buttons]:
    ctrls = _filtered(type_filter)
    if not ctrls:
        return _no_controllers(type_filter)
    known = store.load_state().get("devices", {})
    now = time.time()
    blocks = []
    for ctrl, snap, err in clients.fetch_all(ctrls):
        if not snap:
            blocks.append(f"❌ <b>{esc(ctrl['name'])}:</b> {esc(err)}")
            continue
        blocks += [_site_block(ctrl, snap, s, known, now, len(ctrls) > 1) for s in snap.sites] or \
                  [f"📍 <b>{esc(ctrl['name'])}</b> — brak lokalizacji"]
    t = TYPE_CODES.get(type_filter, "")
    return ("📋 <b>Wszystkie access pointy</b>\n\n" + "\n\n".join(blocks),
            [[("🔄 Odśwież", f"ap:all:{t}"), ("⬅️ Podsumowanie", f"ap:sum:{t}")]])


# ── /kontrolery ───────────────────────────────────────────────────────────────

def _mask_url(url: str) -> str:
    return re.sub(r"//[^@/]*@", "//", url)


def controllers_view(note: str = "") -> tuple[str, Buttons]:
    ctrls = store.load_controllers()
    lines = []
    if note:
        lines += [note, ""]
    lines.append("⚙️ <b>Kontrolery access pointów</b>")
    if not ctrls:
        lines.append("Brak — dodaj pierwszy przyciskiem poniżej albo /kontroler_dodaj")
    buttons: Buttons = []
    for i, c in enumerate(ctrls, 1):
        auth = {"key": "klucz API", "account": f"konto {c.get('username', '')}"}.get(c.get("auth"), "Open API") \
            if c["type"] == "unifi" else "Open API"
        lines.append(f"{i}. <b>{esc(c['name'])}</b> — {store.TYPE_LABEL[c['type']]} · {esc(auth)}\n"
                     f"   <code>{esc(_mask_url(c['url']))}</code> · "
                     f"{'🔔 alerty' if c.get('alerts') else '👁 tylko podgląd'}")
        buttons.append([(f"🔌 Test {c['name'][:14]}", f"apc:test:{c['id']}"),
                        ("🔕 Wyłącz alerty" if c.get("alerts") else "🔔 Włącz alerty", f"apc:al:{c['id']}"),
                        ("🗑", f"apc:del:{c['id']}")])
    buttons.append([("➕ Dodaj kontroler", "apc:add"), ("📡 Podgląd AP", "ap:sum:")])
    return "\n".join(lines), buttons


def test_record(rec: dict) -> tuple[bool, str]:
    """Sprawdza dane nowego kontrolera (przed zapisem)."""
    d = store.defaults()
    try:
        client = clients.make_client({**rec, "id": "test"}, d.ignore_maintenance, d.timeout)
        snap = client.snapshot()
    except clients.ControllerError as e:
        return False, f"❌ Połączenie nieudane: {esc(e)}"
    except Exception as e:
        return False, f"❌ Nieoczekiwany błąd: {esc(e)}"
    n_ok = sum(1 for x in snap.devices if x.ok)
    sites = ", ".join(s["name"] for s in snap.sites[:6]) + ("…" if len(snap.sites) > 6 else "")
    ver = f" {snap.version}" if snap.version else ""
    return True, (f"✅ Połączono ({store.TYPE_LABEL[rec['type']]}{esc(ver)}).\n"
                  f"Lokalizacje: {esc(sites) or '—'}\nAP: {len(snap.devices)}, połączonych {n_ok}")


def test_controller(cid: str) -> tuple[str, Buttons]:
    ctrl = store.get_controller(cid)
    if not ctrl:
        return controllers_view("Ten kontroler już nie istnieje.")
    clients.forget(cid)
    ok, info = test_record(ctrl)
    return f"<b>{esc(ctrl['name'])}</b>\n{info}", [[("⬅️ Kontrolery", "apc:list")]]


def toggle_alerts(cid: str) -> tuple[str, Buttons]:
    ctrl = store.get_controller(cid)
    if not ctrl:
        return controllers_view("Ten kontroler już nie istnieje.")
    new = not ctrl.get("alerts")
    store.update_controller(cid, alerts=new)
    return controllers_view(f"{'🔔 Włączono' if new else '🔕 Wyłączono'} alerty dla <b>{esc(ctrl['name'])}</b>.")


def confirm_delete(cid: str) -> tuple[str, Buttons]:
    ctrl = store.get_controller(cid)
    if not ctrl:
        return controllers_view("Ten kontroler już nie istnieje.")
    return (f"🗑 Usunąć kontroler <b>{esc(ctrl['name'])}</b> ({store.TYPE_LABEL[ctrl['type']]})?\n"
            "Zapisane dane dostępowe zostaną skasowane.",
            [[("✅ Tak, usuń", f"apc:delok:{cid}"), ("✖️ Nie", "apc:list")]])


def delete(cid: str) -> tuple[str, Buttons]:
    gone = store.remove_controller(cid)
    clients.forget(cid)
    return controllers_view(f"🗑 Usunięto <b>{esc(gone['name'])}</b>." if gone else "Ten kontroler już nie istnieje.")


# ── /powiadomienia ────────────────────────────────────────────────────────────

NOTIFY_HELP = (
    "<b>Zmiana ustawień:</b>\n"
    "<code>/powiadomienia godziny zawsze</code>\n"
    "<code>/powiadomienia godziny 07:00-22:00</code>\n"
    "<code>/powiadomienia godziny pn-pt 07:00-17:00; so,nd 09:00-13:00</code>\n"
    "<code>/powiadomienia przypomnienia 30m 1h 3h</code>\n"
    "<code>/powiadomienia przypomnienia brak</code>\n"
    "<code>/powiadomienia pauza 2h</code>  (albo: <code>pauza do 18:00</code>)\n"
    "<code>/powiadomienia wznow</code>\n"
    "<code>/powiadomienia domyslne</code>\n"
    "<i>Dotyczy kontrolerów z włączonymi alertami. Okno może przechodzić przez północ "
    "(np. 22:00-06:00). Zdarzenia spoza godzin przychodzą jednym podsumowaniem, gdy okno się "
    "otworzy. Podczas pauzy wiadomości są pomijane — po pauzie przychodzi stan aktualny.</i>"
)


def notify_status(settings: dict | None = None, now: float | None = None) -> str:
    settings = store.load_settings() if settings is None else settings
    policy = store.notify_policy(settings)
    now = time.time() if now is None else now
    hours = "zawsze (24/7)" if not policy.windows else policy.windows_text
    remind = ("po " + ", ".join(fmt_minutes(m) for m in policy.remind_minutes) +
              " od awarii, potem cisza do powrotu AP") if policy.remind_minutes else \
        "wyłączone (tylko alert i powrót)"
    muted = policy.muted_reason(now)
    now_txt = {"pause": "⏸ pauza — wiadomości są pomijane",
               "quiet": "🌙 poza godzinami — zdarzenia trafią do podsumowania"}.get(muted, "✅ wysyłam na bieżąco")
    with_alerts = [c["name"] for c in store.load_controllers() if c.get("alerts")]
    lines = ["🔔 <b>Powiadomienia o access pointach</b>",
             f"Godziny wysyłania: <b>{esc(hours)}</b>",
             f"Przypomnienia: {esc(remind)}"]
    if policy.pause_until and now < policy.pause_until:
        lines.append(f"Pauza do: <b>{fmt_time(policy.pause_until)}</b>")
    lines.append(f"Teraz: {now_txt}")
    lines.append("Alerty dla: " + (esc(", ".join(with_alerts)) if with_alerts else "żadnego kontrolera (/kontrolery)"))
    return "\n".join(lines)


def _parse_pause(text: str, now: float) -> float:
    text = text.strip().lower()
    if not text:
        raise ValueError("podaj czas pauzy, np. „pauza 2h” albo „pauza do 18:00”")
    m = re.fullmatch(r"do\s+(\d{1,2})(?::(\d{2}))?", text)
    if m:
        target = parse_hm(m[1], m[2]) % 1440
        dt = datetime.fromtimestamp(now)
        until = dt.replace(hour=target // 60, minute=target % 60, second=0, microsecond=0)
        if until <= dt:
            until += timedelta(days=1)
        return until.timestamp()
    minutes = parse_minutes(text)
    if minutes <= 0:
        raise ValueError("czas pauzy musi być większy od zera")
    if minutes > 7 * 1440:
        raise ValueError("pauza może trwać najwyżej 7 dni")
    return now + minutes * 60


def notify_command(args: list[str], now: float | None = None) -> str:
    now = time.time() if now is None else now
    settings = store.load_settings()
    text = " ".join(args).strip()
    if not text:
        return notify_status(settings, now) + "\n\n" + NOTIFY_HELP
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
    store.save_settings(settings)
    return msg + "\n\n" + notify_status(settings, now)
