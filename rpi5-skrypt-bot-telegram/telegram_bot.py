#!/usr/bin/env python3
"""
Telegram Bot — zarządzanie serwerem + monitorowanie kursów walut
Komendy RAID/serwer: /status /reboot /help /disks /raid /df /uptime /logs
Komendy walutowe:    /kurs /kurs_historia /kurs_prognoza
Wtyczki (katalog plugins/) ładują się same — np. access pointy Omada/UniFi.
Aktualizacja całości (bot + wtyczki wg manifest.json): /rpi5_update
"""

import ast
import asyncio
import importlib
import json
import logging
import os
import pkgutil
import re
import shlex
import shutil
import subprocess
import sys
import csv
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen, Request
from urllib.error import URLError

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        CallbackQueryHandler, ConversationHandler, ContextTypes, filters,
    )
except ImportError:
    print("Brak biblioteki python-telegram-bot. Zainstaluj: pip3 install python-telegram-bot")
    sys.exit(1)

VERSION = "3.0.0"

# ── Konfiguracja ──────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
RAID_DEV    = os.environ.get("RAID_DEVICE", "/dev/md0")
DISKS       = os.environ.get("MONITOR_DISKS", "/dev/sda /dev/sdb /dev/sdc /dev/sdd").split()
ALLOWED_IDS = [CHAT_ID] if CHAT_ID else []

# Katalog danych kursów walut
CURRENCY_DATA_DIR = Path(os.environ.get("CURRENCY_DATA_DIR", "/var/lib/currency-monitor"))
CURRENCY_DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE    = "/var/log/raid_monitor.log"
DEFAULT_UPDATE_URL = ("https://raw.githubusercontent.com/goof3r/rpi5/master/"
                      "rpi5-skrypt-bot-telegram/telegram_bot.py")
UPDATE_URL  = os.environ.get("SCRIPT_UPDATE_URL", "").strip() or DEFAULT_UPDATE_URL
SCRIPT_PATH = Path(__file__).resolve()
BASE_DIR    = SCRIPT_PATH.parent
LOCAL_MANIFEST = BASE_DIR / "manifest.json"
UPDATE_MARKER  = BASE_DIR / ".update_done"
BACKUP_DIR     = BASE_DIR / ".backup"

# Plik notatek
NOTES_FILE = Path(os.environ.get("NOTES_FILE", "/var/lib/currency-monitor/notes.json"))

# Stany konwersacji notatek
_NOTE_ADD_STATE  = 1
_NOTE_EDIT_STATE = 2

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/telegram_bot.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)

# ── Helpers ogólne ────────────────────────────────────────────────────────────

def run(cmd: str, timeout: int = 10) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace"
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", f"Przekroczono czas ({timeout}s)"
    except Exception as e:
        return 1, "", str(e)


def authorized(update: Update) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not ALLOWED_IDS:
        return True
    return user_id in ALLOWED_IDS or chat_id in ALLOWED_IDS


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _send_pages(message, text: str, parse_mode: str = "HTML") -> None:
    MAX = 3800
    while len(text) > MAX:
        split = text.rfind("\n", 0, MAX)
        if split == -1:
            split = MAX
        await message.reply_text(text[:split], parse_mode=parse_mode)
        text = text[split:].lstrip("\n")
    await message.reply_text(text, parse_mode=parse_mode)

# ── Notatki — CRUD na pliku JSON ─────────────────────────────────────────────

def _notes_load() -> dict:
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _notes_save(data: dict) -> None:
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(NOTES_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

def notes_add(temat: str, tresc: str) -> tuple[bool, str]:
    temat, tresc = temat.strip(), tresc.strip()
    if not temat:
        return False, "Temat nie może być pusty."
    if not tresc:
        return False, "Treść nie może być pusta."
    data = _notes_load()
    if temat in data:
        return False, f"Notatka <b>{esc(temat)}</b> już istnieje — użyj <code>/rpi5_notatki edytuj {esc(temat)}</code>"
    data[temat] = {"tresc": tresc, "data_dodania": _now_iso(), "data_modyfikacji": _now_iso()}
    _notes_save(data)
    return True, f"✅ Notatka <b>{esc(temat)}</b> zapisana."

def notes_get(temat: str) -> dict | None:
    return _notes_load().get(temat.strip())

def notes_all() -> dict:
    return _notes_load()

def notes_update(temat: str, tresc: str) -> tuple[bool, str]:
    temat, tresc = temat.strip(), tresc.strip()
    if not tresc:
        return False, "Nowa treść nie może być pusta."
    data = _notes_load()
    if temat not in data:
        return False, f"Notatka <b>{esc(temat)}</b> nie istnieje."
    data[temat]["tresc"] = tresc
    data[temat]["data_modyfikacji"] = _now_iso()
    _notes_save(data)
    return True, f"✅ Notatka <b>{esc(temat)}</b> zaktualizowana."

def notes_delete(temat: str) -> tuple[bool, str]:
    temat = temat.strip()
    data = _notes_load()
    if temat not in data:
        return False, f"Notatka <b>{esc(temat)}</b> nie istnieje."
    del data[temat]
    _notes_save(data)
    return True, f"🗑️ Notatka <b>{esc(temat)}</b> usunięta."

def notes_search(fraza: str) -> dict:
    fraza = fraza.lower().strip()
    return {t: d for t, d in _notes_load().items()
            if fraza in t.lower() or fraza in d["tresc"].lower()}

def _parse_note_args(args: list) -> tuple[str, str]:
    """Zwraca (temat, tresc). Temat może być w cudzysłowie."""
    joined = " ".join(args)
    if joined.startswith('"'):
        end = joined.find('"', 1)
        if end != -1:
            return joined[1:end], joined[end + 1:].strip()
    parts = joined.split(" ", 1)
    return parts[0], parts[1].strip() if len(parts) > 1 else ""

def _parse_note_temat(args: list) -> str:
    joined = " ".join(args)
    if joined.startswith('"') and joined.endswith('"'):
        return joined[1:-1]
    return joined

# ── Komendy notatek ───────────────────────────────────────────────────────────

async def cmd_notatki(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/rpi5_notatki <akcja> [argumenty]"""
    args = ctx.args or []

    if not args:
        n = len(notes_all())
        keyboard = [[
            InlineKeyboardButton("📋 Lista", callback_data="nts:lista"),
            InlineKeyboardButton("🔍 Szukaj", callback_data="nts:szukaj_info"),
        ]]
        await update.message.reply_text(
            f"📋 <b>Notatki</b> (łącznie: {n})\n\n"
            "<code>/rpi5_notatki lista</code>\n"
            "<code>/rpi5_notatki dodaj &lt;temat&gt; &lt;treść&gt;</code>\n"
            "<code>/rpi5_notatki czytaj &lt;temat&gt;</code>\n"
            "<code>/rpi5_notatki edytuj &lt;temat&gt;</code>\n"
            "<code>/rpi5_notatki usun &lt;temat&gt;</code>\n"
            "<code>/rpi5_notatki szukaj &lt;fraza&gt;</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    akcja = args[0].lower()

    # ── lista ──
    if akcja == "lista":
        wszystkie = notes_all()
        if not wszystkie:
            await update.message.reply_text(
                "📭 Brak notatek.\n\n"
                "Dodaj pierwszą:\n<code>/rpi5_notatki dodaj &lt;temat&gt; &lt;treść&gt;</code>",
                parse_mode="HTML"
            )
            return
        lines = [f"📋 <b>Notatki ({len(wszystkie)}):</b>\n"]
        for i, (t, d) in enumerate(sorted(wszystkie.items()), 1):
            preview = d["tresc"][:50] + ("…" if len(d["tresc"]) > 50 else "")
            data_m = d.get("data_modyfikacji", "")[:10]
            lines.append(f"{i}. <b>{esc(t)}</b>\n   <i>{esc(preview)}</i>\n   📅 {data_m}")
        await _send_pages(update.message, "\n".join(lines))

    # ── dodaj ──
    elif akcja == "dodaj":
        if len(args) < 2:
            await update.message.reply_text(
                "❌ Użycie: <code>/rpi5_notatki dodaj &lt;temat&gt;</code>",
                parse_mode="HTML"
            )
            return
        temat = _parse_note_temat(args[1:])
        if not temat:
            await update.message.reply_text(
                "❌ Podaj temat: <code>/rpi5_notatki dodaj &lt;temat&gt;</code>",
                parse_mode="HTML"
            )
            return
        if notes_get(temat):
            await update.message.reply_text(
                f"⚠️ Notatka <b>{esc(temat)}</b> już istnieje — użyj <code>/rpi5_notatki edytuj {esc(temat)}</code>",
                parse_mode="HTML"
            )
            return
        ctx.user_data["nts_dodaj"] = temat
        await update.message.reply_text(
            f"📝 <b>Nowa notatka: {esc(temat)}</b>\n\n"
            "Wyślij treść notatki (lub /anuluj aby przerwać):",
            parse_mode="HTML",
        )
        return _NOTE_ADD_STATE

    # ── czytaj ──
    elif akcja in ("czytaj", "pokaz", "pokaż"):
        if len(args) < 2:
            await update.message.reply_text(
                "❌ Użycie: <code>/rpi5_notatki czytaj &lt;temat&gt;</code>", parse_mode="HTML"
            )
            return
        temat = _parse_note_temat(args[1:])
        notatka = notes_get(temat)
        if not notatka:
            await update.message.reply_text(
                f"❌ Nie znaleziono notatki: <b>{esc(temat)}</b>", parse_mode="HTML"
            )
            return
        data_d = notatka.get("data_dodania", "")[:16].replace("T", " ")
        data_m = notatka.get("data_modyfikacji", "")[:16].replace("T", " ")
        keyboard = [[
            InlineKeyboardButton("✏️ Edytuj", callback_data=f"nts:edytuj:{temat}"),
            InlineKeyboardButton("🗑️ Usuń",  callback_data=f"nts:usun_confirm:{temat}"),
        ]]
        await update.message.reply_text(
            f"📝 <b>{esc(temat)}</b>\n\n"
            f"{esc(notatka['tresc'])}\n\n"
            f"📅 Dodano: {data_d}\n✏️ Zmieniono: {data_m}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # ── usun ──
    elif akcja in ("usun", "usuń", "del"):
        if len(args) < 2:
            await update.message.reply_text(
                "❌ Użycie: <code>/rpi5_notatki usun &lt;temat&gt;</code>", parse_mode="HTML"
            )
            return
        temat = _parse_note_temat(args[1:])
        if not notes_get(temat):
            await update.message.reply_text(
                f"❌ Nie znaleziono notatki: <b>{esc(temat)}</b>", parse_mode="HTML"
            )
            return
        keyboard = [[
            InlineKeyboardButton("✅ Tak, usuń", callback_data=f"nts:usun_tak:{temat}"),
            InlineKeyboardButton("❌ Anuluj",    callback_data="nts:anuluj"),
        ]]
        await update.message.reply_text(
            f"⚠️ Usunąć notatkę <b>{esc(temat)}</b>?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # ── edytuj ──
    elif akcja in ("edytuj", "edit"):
        if len(args) < 2:
            await update.message.reply_text(
                "❌ Użycie: <code>/rpi5_notatki edytuj &lt;temat&gt;</code>", parse_mode="HTML"
            )
            return
        temat = _parse_note_temat(args[1:])
        notatka = notes_get(temat)
        if not notatka:
            await update.message.reply_text(
                f"❌ Nie znaleziono notatki: <b>{esc(temat)}</b>", parse_mode="HTML"
            )
            return
        ctx.user_data["nts_edytuj"] = temat
        await update.message.reply_text(
            f"✏️ <b>Edytujesz: {esc(temat)}</b>\n\n"
            f"Aktualna treść:\n<i>{esc(notatka['tresc'])}</i>\n\n"
            "Wyślij nową treść (lub /anuluj aby przerwać):",
            parse_mode="HTML",
        )
        return _NOTE_EDIT_STATE

    # ── szukaj ──
    elif akcja in ("szukaj", "search", "znajdz", "znajdź"):
        if len(args) < 2:
            await update.message.reply_text(
                "❌ Użycie: <code>/rpi5_notatki szukaj &lt;fraza&gt;</code>", parse_mode="HTML"
            )
            return
        fraza = " ".join(args[1:])
        wyniki = notes_search(fraza)
        if not wyniki:
            await update.message.reply_text(
                f"🔍 Brak wyników dla: <b>{esc(fraza)}</b>", parse_mode="HTML"
            )
            return
        lines = [f"🔍 <b>Wyniki dla: {esc(fraza)}</b>\n"]
        for t, d in wyniki.items():
            lines.append(f"• <b>{esc(t)}</b>\n  <i>{esc(d['tresc'][:60])}…</i>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    else:
        await update.message.reply_text(
            f"❓ Nieznana akcja: <code>{esc(akcja)}</code>\n"
            "Dostępne: lista, dodaj, czytaj, edytuj, usun, szukaj",
            parse_mode="HTML"
        )


async def cmd_notatki_add_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Odbiera treść nowej notatki po komendzie dodaj."""
    temat = ctx.user_data.get("nts_dodaj")
    if not temat:
        return ConversationHandler.END
    tresc = update.message.text.strip()
    ok_flag, msg = notes_add(temat, tresc)
    await update.message.reply_text(("" if ok_flag else "⚠️ ") + msg, parse_mode="HTML")
    ctx.user_data.pop("nts_dodaj", None)
    return ConversationHandler.END


async def cmd_notatki_edit_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Odbiera nową treść notatki po komendzie edytuj."""
    temat = ctx.user_data.get("nts_edytuj")
    if not temat:
        return ConversationHandler.END
    nowa = update.message.text.strip()
    ok_flag, msg = notes_update(temat, nowa)
    await update.message.reply_text(("" if ok_flag else "⚠️ ") + msg, parse_mode="HTML")
    ctx.user_data.pop("nts_edytuj", None)
    return ConversationHandler.END


async def cmd_anuluj(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("nts_edytuj", None)
    await update.message.reply_text("❌ Anulowano.")
    return ConversationHandler.END


async def notes_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Obsługuje przyciski inline dla notatek."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "nts:lista":
        wszystkie = notes_all()
        if not wszystkie:
            await query.edit_message_text("📭 Brak notatek.", parse_mode="HTML")
            return
        lines = [f"📋 <b>Notatki ({len(wszystkie)}):</b>\n"]
        for i, (t, d) in enumerate(sorted(wszystkie.items()), 1):
            preview = d["tresc"][:50] + ("…" if len(d["tresc"]) > 50 else "")
            lines.append(f"{i}. <b>{esc(t)}</b> — <i>{esc(preview)}</i>")
        full_text = "\n".join(lines)
        if len(full_text) <= 3800:
            await query.edit_message_text(full_text, parse_mode="HTML")
        else:
            await query.edit_message_text("📋 Lista notatek:", parse_mode="HTML")
            await _send_pages(query.message, full_text)

    elif data == "nts:szukaj_info":
        await query.edit_message_text(
            "Użyj: <code>/rpi5_notatki szukaj &lt;fraza&gt;</code>", parse_mode="HTML"
        )

    elif data == "nts:anuluj":
        await query.edit_message_text("❌ Anulowano.")

    elif data.startswith("nts:usun_confirm:"):
        temat = data.split(":", 2)[2]
        kb = [[
            InlineKeyboardButton("✅ Tak, usuń", callback_data=f"nts:usun_tak:{temat}"),
            InlineKeyboardButton("❌ Anuluj",    callback_data="nts:anuluj"),
        ]]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("nts:usun_tak:"):
        temat = data.split(":", 2)[2]
        ok_flag, msg = notes_delete(temat)
        await query.edit_message_text(("" if ok_flag else "⚠️ ") + msg, parse_mode="HTML")

    elif data.startswith("nts:edytuj:"):
        temat = data.split(":", 2)[2]
        notatka = notes_get(temat)
        if notatka:
            ctx.user_data["nts_edytuj"] = temat
            await query.edit_message_text(
                f"✏️ <b>Edytujesz: {esc(temat)}</b>\n\n"
                f"Aktualna treść:\n<i>{esc(notatka['tresc'])}</i>\n\n"
                "Wyślij nową treść jako kolejną wiadomość:",
                parse_mode="HTML",
            )

# ── Wtyczki (katalog plugins/) ───────────────────────────────────────────────

PLUGINS_HELP: list[str] = []
PLUGIN_ERRORS: list[str] = []
STARTUP_NOTES: list[str] = []


def load_plugins(app) -> None:
    """Ładuje każdy moduł/pakiet z plugins/, który ma register(app, core)."""
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    try:
        import plugins
    except ImportError:
        logger.info("Brak katalogu plugins/ — bot działa bez wtyczek")
        return
    core = SimpleNamespace(authorized=authorized, esc=esc, send_pages=_send_pages, chat_id=CHAT_ID,
                           logger=logger, run=run, version=VERSION, base_dir=BASE_DIR,
                           startup_notes=STARTUP_NOTES)
    for mod in sorted(pkgutil.iter_modules(plugins.__path__), key=lambda m: m.name):
        try:
            module = importlib.import_module(f"plugins.{mod.name}")
            if hasattr(module, "register"):
                module.register(app, core)
            if getattr(module, "HELP", ""):
                PLUGINS_HELP.append(module.HELP)
            logger.info("Wtyczka %s załadowana", mod.name)
        except Exception as e:
            logger.exception("Wtyczka %s: błąd ładowania", mod.name)
            PLUGIN_ERRORS.append(f"{mod.name}: {e}")

# ── Helpers kursów walut ──────────────────────────────────────────────────────

def nbp_fetch_rate(currency: str) -> float | None:
    """Pobierz kurs sprzedaży (ask) z NBP Tabela C."""
    for url in [
        f"https://api.nbp.pl/api/exchangerates/rates/c/{currency}/today/?format=json",
        f"https://api.nbp.pl/api/exchangerates/rates/c/{currency}/last/1/?format=json",
    ]:
        try:
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return float(data["rates"][0]["ask"])
        except Exception:
            continue
    return None


def revolut_fetch_rate(pair: str) -> float | None:
    """Pobierz kurs mid-market z Revolut (np. 'USDPLN')."""
    url = f"https://www.revolut.com/api/quote/public/{pair}"
    try:
        req = Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return float(data["rate"])
    except Exception:
        return None


def walutomat_fetch_rate(pair: str) -> dict | None:
    """Pobierz kursy kupno/sprzedaż z InternetowyKantor.pl (Walutomat API, np. 'USD_PLN')."""
    url = f"https://api.walutomat.pl/api/v1/public/market/orderbook/{pair}"
    try:
        req = Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {
                "bid": float(data["bids"][0]["price"]),
                "ask": float(data["asks"][0]["price"]),
            }
    except Exception:
        return None


def currency_file(symbol: str, day: date | None = None) -> Path:
    d = day or date.today()
    return CURRENCY_DATA_DIR / f"{symbol}_{d.strftime('%Y-%m-%d')}.csv"


def save_rate(symbol: str, rate: float) -> None:
    f = currency_file(symbol)
    write_header = not f.exists()
    with open(f, "a", newline="") as fh:
        w = csv.writer(fh)
        if write_header:
            w.writerow(["timestamp", "rate"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"{rate:.4f}"])


def load_rates_today(symbol: str) -> list[float]:
    f = currency_file(symbol)
    if not f.exists():
        return []
    with open(f, newline="") as fh:
        rows = list(csv.reader(fh))
    return [float(r[1]) for r in rows[1:] if len(r) >= 2]


def load_yesterday_close(symbol: str) -> float | None:
    f = currency_file(symbol, date.today() - timedelta(days=1))
    if not f.exists():
        return None
    with open(f, newline="") as fh:
        rows = list(csv.reader(fh))
    data = [r for r in rows[1:] if len(r) >= 2]
    return float(data[-1][1]) if data else None


def linear_regression_slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den else 0.0


def trend_label(slope: float) -> str:
    if slope < -0.0002:
        return "TENDENCJA SPADKOWA 🔴"
    elif slope > 0.0002:
        return "TENDENCJA WZROSTOWA 🟢"
    return "TENDENCJA NEUTRALNA 🟡"


def change_str(current: float, reference: float, label: str) -> str:
    diff = current - reference
    pct  = diff / reference * 100
    arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "→")
    return f"{arrow} {diff:+.4f} PLN ({pct:+.2f}% {label})"

# ── Komendy walutowe ──────────────────────────────────────────────────────────

async def cmd_kurs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Aktualny kurs USD i EUR z wielu źródeł."""
    await update.message.reply_text("⏳ Pobieram kursy…")

    usd_nbp = nbp_fetch_rate("USD")
    eur_nbp = nbp_fetch_rate("EUR")
    usd_rev = revolut_fetch_rate("USDPLN")
    eur_rev = revolut_fetch_rate("EURPLN")
    usd_wlt = walutomat_fetch_rate("USD_PLN")
    eur_wlt = walutomat_fetch_rate("EUR_PLN")

    if all(v is None for v in [usd_nbp, usd_rev, usd_wlt]):
        await update.message.reply_text("⚠️ Nie udało się pobrać żadnych kursów. Spróbuj za chwilę.")
        return

    if usd_nbp:
        save_rate("USD", usd_nbp)
    if eur_nbp:
        save_rate("EUR", eur_nbp)

    usd_rates = load_rates_today("USD")
    eur_rates  = load_rates_today("EUR")
    usd_prev   = load_yesterday_close("USD")
    eur_prev   = load_yesterday_close("EUR")

    def section(symbol: str, flag: str,
                nbp: float | None, rev: float | None, wlt: dict | None,
                rates: list, prev: float | None) -> list[str]:
        sep = "━━━━━━━━━━━━━━━━━━━━━━━━━"
        out = [f"{sep}", f"{flag} <b>{symbol} / PLN</b>", ""]

        # NBP
        if nbp is not None:
            out.append(f"🏦 <b>NBP</b>  (oficjalny kurs sprzedaży)")
            out.append(f"   <b>{nbp:.4f} PLN</b>")
            if len(rates) > 1:
                out.append(f"   {change_str(nbp, rates[0], 'od rana')}")
            if prev:
                out.append(f"   {change_str(nbp, prev, 'vs wczoraj')}")
        else:
            out.append("🏦 <b>NBP</b>  ❌ niedostępny")

        out.append("")

        # Revolut
        if rev is not None:
            out.append(f"🔄 <b>Revolut</b>  (kurs mid-market)")
            out.append(f"   <b>{rev:.4f} PLN</b>")
        else:
            out.append("🔄 <b>Revolut</b>  ❌ niedostępny")

        out.append("")

        # InternetowyKantor.pl
        if wlt is not None:
            out.append(f"🌐 <b>InternetowyKantor.pl</b>  (rynek P2P)")
            out.append(f"   Kupno:     <b>{wlt['bid']:.4f} PLN</b>")
            out.append(f"   Sprzedaż:  <b>{wlt['ask']:.4f} PLN</b>")
        else:
            out.append("🌐 <b>InternetowyKantor.pl</b>  ❌ niedostępny")

        return out

    now = datetime.now().strftime("%d.%m.%Y  %H:%M:%S")
    lines = [f"💱 <b>Kursy walut</b>  —  {now}", ""]
    lines += section("USD", "💵", usd_nbp, usd_rev, usd_wlt, usd_rates, usd_prev)
    lines.append("")
    lines += section("EUR", "💶", eur_nbp, eur_rev, eur_wlt, eur_rates, eur_prev)
    lines.append("")
    lines.append("📡 <i>NBP Tabela C · Revolut · Walutomat API</i>")

    await _send_pages(update.message, "\n".join(lines))


async def cmd_kurs_historia(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Podsumowanie kursów z dzisiejszego dnia."""
    usd_rates = load_rates_today("USD")
    eur_rates  = load_rates_today("EUR")

    if not usd_rates or not eur_rates:
        await update.message.reply_text(
            "📭 Brak danych z dziś. Wywołaj /kurs aby zapisać pierwszy pomiar."
        )
        return

    def summary(rates: list[float], symbol: str, flag: str) -> str:
        lo, hi = min(rates), max(rates)
        diff = rates[-1] - rates[0]
        pct  = diff / rates[0] * 100
        arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "→")
        return (
            f"{flag} <b>{symbol}/PLN</b> — {len(rates)} pomiarów\n"
            f"   Otwarcie: {rates[0]:.4f} | Ostatni: {rates[-1]:.4f}\n"
            f"   Min: {lo:.4f} | Max: {hi:.4f}\n"
            f"   Zmiana dnia: {arrow} {diff:+.4f} PLN ({pct:+.2f}%)"
        )

    today = date.today().strftime("%d.%m.%Y")
    msg = (
        f"📅 <b>Historia dnia — {today}</b>\n\n"
        + summary(usd_rates, "USD", "💵") + "\n\n"
        + summary(eur_rates,  "EUR",  "💶")
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_kurs_prognoza(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Prognoza tendencji na podstawie regresji liniowej dnia."""
    usd_rates = load_rates_today("USD")
    eur_rates  = load_rates_today("EUR")

    if len(usd_rates) < 3 or len(eur_rates) < 3:
        await update.message.reply_text(
            "📊 Za mało danych do prognozy (potrzeba min. 3 pomiarów).\n"
            "Wywołaj /kurs kilka razy w ciągu dnia."
        )
        return

    usd_slope = linear_regression_slope(usd_rates)
    eur_slope  = linear_regression_slope(eur_rates)

    today = date.today().strftime("%d.%m.%Y")
    msg = (
        f"🔮 <b>Prognoza tendencji — {today}</b>\n\n"
        f"💵 <b>USD/PLN</b>\n"
        f"   Nachylenie trendu: {usd_slope:+.6f}\n"
        f"   {trend_label(usd_slope)}\n\n"
        f"💶 <b>EUR/PLN</b>\n"
        f"   Nachylenie trendu: {eur_slope:+.6f}\n"
        f"   {trend_label(eur_slope)}\n\n"
        f"📊 <i>Regresja liniowa z {len(usd_rates)} pomiarów dnia.\n"
        f"Nie stanowi porady inwestycyjnej.</i>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

# ── Komendy serwera (bez zmian) ───────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    extra = "".join(f"\n\n{h}" for h in PLUGINS_HELP)
    if PLUGIN_ERRORS:
        extra += "\n\n⚠️ <b>Wtyczki z błędem:</b>\n" + "\n".join(f"  {esc(e)}" for e in PLUGIN_ERRORS)
    await _send_pages(update.message,
        f"🖥️ <b>Bot monitorowania serwera i kursów walut</b> <i>v{VERSION}</i>\n\n"
        "<b>── Serwer (RPi5) ──</b>\n"
        "  /rpi5_status   — pełny przegląd systemu\n"
        "  /rpi5_info     — informacje o systemie (uname)\n"
        "  /rpi5_disks    — stan dysków\n"
        "  /rpi5_raid     — status macierzy RAID\n"
        "  /rpi5_df       — wolne miejsce\n"
        "  /rpi5_uptime   — czas działania serwera\n"
        "  /rpi5_logs     — ostatnie logi monitoringu\n"
        "  /rpi5_reboot   — REBOOT serwera\n"
        "  /rpi5_update   — aktualizacja bota i wtyczek z GitHuba\n"
        "  /rpi5_help     — ta wiadomość\n\n"
        "<b>── Kursy walut (NBP) ──</b>\n"
        "  /kurs           — aktualny kurs USD i EUR\n"
        "  /kurs_historia  — podsumowanie dnia\n"
        "  /kurs_prognoza  — tendencja (regresja liniowa)\n\n"
        "<b>── Notatki ──</b>\n"
        "  /rpi5_notatki lista            — wszystkie notatki\n"
        "  /rpi5_notatki dodaj &lt;t&gt; &lt;treść&gt; — nowa notatka\n"
        "  /rpi5_notatki czytaj &lt;temat&gt;    — odczytaj\n"
        "  /rpi5_notatki edytuj &lt;temat&gt;    — edytuj\n"
        "  /rpi5_notatki usun &lt;temat&gt;      — usuń\n"
        "  /rpi5_notatki szukaj &lt;fraza&gt;    — szukaj"
        + extra
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("🚫 Brak uprawnień.")
        return

    lines = [f"📊 <b>Status serwera</b> — <code>{esc(run('hostname -s')[1])}</code>"]
    lines.append(f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>\n")

    rc, out, _ = run("uptime -p")
    lines.append(f"⬆️ <b>Uptime:</b> {esc(out or '?')}")

    rc, out, _ = run("cat /proc/loadavg")
    if out:
        la = out.split()[:3]
        lines.append(f"📝 <b>Load:</b> {' | '.join(la)}")

    rc, out, _ = run("free -h | awk 'NR==2{printf \"%s / %s\", $3, $2}'")
    lines.append(f"🧠 <b>RAM:</b> {esc(out or '?')}")

    lines.append("\n💾 <b>Dyski:</b>")
    for d in DISKS:
        rc, _, _ = run(f"lsblk {d} -o NAME,SIZE,TYPE 2>/dev/null | head -2")
        icon = "✅" if rc == 0 else "❌"
        lines.append(f"  {icon} <code>{d}</code>")

    lines.append("\n🔧 <b>RAID:</b>")
    rc, out, err = run(f"mdadm --detail {RAID_DEV} 2>&1")
    if rc == 0:
        state  = re.search(r"State\s*:\s*(.+)", out)
        active = re.search(r"Active Devices\s*:\s*(\d+)", out)
        failed = re.search(r"Failed Devices\s*:\s*(\d+)", out)
        s     = state.group(1).strip() if state else "?"
        a     = active.group(1) if active else "?"
        f_val = failed.group(1) if failed else "?"
        icon  = "✅" if "clean" in s.lower() and f_val == "0" else "⚠️"
        lines.append(f"  {icon} <code>{RAID_DEV}</code>: {esc(s)}")
        lines.append(f"  Aktywne: {a} | Uszkodzone: {f_val}")
    else:
        lines.append(f"  ❌ Błąd: <code>{esc(err or out)}</code>")

    lines.append("\n📦 <b>Miejsce:</b>")
    rc, out, _ = run("df -h --output=target,size,avail,pcent | tail -n +2")
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            mp, size, avail, pct = parts[0], parts[1], parts[2], parts[3]
            pct_val = int(pct.replace("%", "")) if pct.replace("%", "").isdigit() else 0
            icon = "🔴" if pct_val >= 90 else ("🟠" if pct_val >= 75 else "🟢")
            if mp.startswith("/") and "loop" not in mp:
                lines.append(f"  {icon} <code>{esc(mp)}</code>: {avail} wolne z {size} ({pct})")

    lines.append("\n💱 <b>Kursy walut (ostatni pomiar):</b>")
    usd_rates = load_rates_today("USD")
    eur_rates  = load_rates_today("EUR")
    if usd_rates and eur_rates:
        lines.append(f"  💵 USD/PLN: <b>{usd_rates[-1]:.4f}</b> | 💶 EUR/PLN: <b>{eur_rates[-1]:.4f}</b>")
        lines.append(f"  <i>Użyj /kurs_prognoza po więcej danych</i>")
    else:
        lines.append("  <i>Brak danych — wywołaj /kurs</i>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_disks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("🚫 Brak uprawnień.")
        return

    lines = ["💾 <b>Szczegóły dysków</b>\n"]
    for d in DISKS:
        rc, out, _ = run(f"lsblk {d} --output=NAME,SIZE,TYPE,MODEL 2>/dev/null || lsblk {d} 2>&1")
        icon = "✅" if rc == 0 else "❌"
        lines.append(f"{icon} <code>{d}</code>")
        if rc == 0 and out:
            lines.append(f"<pre>{esc(out[:400])}</pre>")
        else:
            lines.append("  ⚠️ Dysk niewidoczny w systemie!\n")
        rc2, smart, _ = run(f"smartctl -H {d} 2>/dev/null")
        if rc2 in (0, 1):
            health = re.search(r"(PASSED|FAILED|OK|BAD)", smart, re.I)
            if health:
                sh = health.group(1).upper()
                s_icon = "✅" if sh in ("PASSED", "OK") else "🔴"
                lines.append(f"  {s_icon} SMART: <b>{sh}</b>")
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_raid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("🚫 Brak uprawnień.")
        return

    lines = ["🔧 <b>Status RAID</b>\n"]
    rc, mdstat, _ = run("cat /proc/mdstat")
    if rc == 0:
        lines.append("<b>/proc/mdstat:</b>")
        lines.append(f"<pre>{esc(mdstat[:800])}</pre>")

    rc, detail, err = run(f"mdadm --detail {RAID_DEV} 2>&1", timeout=15)
    if rc == 0:
        lines.append(f"\n<b>mdadm --detail {esc(RAID_DEV)}:</b>")
        lines.append(f"<pre>{esc(detail[:1200])}</pre>")
    else:
        lines.append(f"\n❌ <code>mdadm --detail</code> błąd:\n<code>{esc(err or detail)}</code>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_df(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("🚫 Brak uprawnień.")
        return

    rc, out, _ = run("df -h | grep -v 'loop\\|tmpfs\\|udev' || df -h")
    await update.message.reply_text(
        f"📦 <b>Wolne miejsce:</b>\n<pre>{esc(out[:1500])}</pre>",
        parse_mode="HTML"
    )


async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("🚫 Brak uprawnień.")
        return

    rc, uname, _ = run("uname -a")
    rc, hostname, _ = run("hostname -f")
    rc, os_info, _ = run("cat /etc/os-release | grep -E '^PRETTY_NAME' | cut -d'=' -f2 | tr -d '\"'")
    rc, arch, _ = run("dpkg --print-architecture 2>/dev/null || uname -m")

    msg = (
        f"🖥️ <b>Informacje o systemie</b>\n\n"
        f"<b>Host:</b> <code>{esc(hostname or '?')}</code>\n"
        f"<b>System:</b> <code>{esc(os_info or '?')}</code>\n"
        f"<b>Arch:</b> <code>{esc(arch or '?')}</code>\n\n"
        f"<b>uname -a:</b>\n<code>{esc(uname or '?')}</code>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_uptime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("🚫 Brak uprawnień.")
        return

    rc, uptime,  _ = run("uptime")
    rc, loadavg, _ = run("cat /proc/loadavg")
    rc, meminfo, _ = run("free -h | head -3")
    rc, temp,    _ = run("sensors 2>/dev/null | grep -E 'Core|Temp|temp' | head -5 || echo 'brak danych'")

    msg = (
        f"⬆️ <b>Uptime i obciążenie</b>\n\n"
        f"<pre>{esc(uptime)}</pre>\n"
        f"<b>Load avg:</b> <code>{esc(loadavg)}</code>\n\n"
        f"<b>Pamięć:</b>\n<pre>{esc(meminfo)}</pre>\n"
        f"<b>Temperatura:</b>\n<pre>{esc(temp)}</pre>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("🚫 Brak uprawnień.")
        return

    rc, out, _ = run(f"tail -50 {LOG_FILE} 2>/dev/null || echo 'Plik logów niedostępny'")
    lines = (out or "brak logów").splitlines()[-30:]
    await update.message.reply_text(
        f"📋 <b>Ostatnie logi monitoringu:</b>\n<pre>{esc(chr(10).join(lines))}</pre>",
        parse_mode="HTML"
    )


_reboot_pending: dict[int, datetime] = {}

async def cmd_reboot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("🚫 Brak uprawnień do restartowania serwera.")
        return

    uid = update.effective_user.id
    now = datetime.now()

    if uid in _reboot_pending:
        delta = (now - _reboot_pending[uid]).total_seconds()
        if delta < 30:
            del _reboot_pending[uid]
            await update.message.reply_text(
                "⚠️ <b>Potwierdzono — restartuję serwer!</b>",
                parse_mode="HTML"
            )
            logger.warning("REBOOT polecony przez użytkownika %d", uid)
            run("sleep 2 && /sbin/shutdown -r now 'Reboot przez Telegram bot'")
            return
        else:
            del _reboot_pending[uid]

    _reboot_pending[uid] = now
    await update.message.reply_text(
        "🔴 <b>UWAGA: Zamierzasz zrestartować serwer!</b>\n\n"
        "Wyślij <code>/rpi5_reboot</code> jeszcze raz w ciągu <b>30 sekund</b> aby potwierdzić.",
        parse_mode="HTML"
    )

# ── Aktualizacja z GitHuba: bot + wtyczki + skrypty wg manifest.json ─────────

_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
_update_plans: dict[int, tuple[datetime, dict]] = {}


def _http_get(url: str, timeout: int = 30) -> bytes:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _update_base() -> str:
    return UPDATE_URL.rsplit("/", 1)[0]


def _safe_rel(rel: str) -> bool:
    return bool(_SAFE_PATH.match(rel)) and ".." not in rel.split("/")


def _check_file(rel: str, content: bytes) -> None:
    """Nie instaluj uszkodzonych plików (np. obcięty download)."""
    if rel.endswith(".py"):
        ast.parse(content, filename=rel)
    elif rel.endswith(".json"):
        json.loads(content.decode("utf-8"))
    elif rel.endswith(".sh") and not content.startswith(b"#!"):
        raise ValueError(f"{rel}: to nie wygląda na skrypt")


def _fetch_manifest() -> dict:
    raw = _http_get(_update_base() + "/manifest.json")
    m = json.loads(raw.decode("utf-8"))
    files = m.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and _safe_rel(f) for f in files):
        raise ValueError("manifest.json: niepoprawna lista plików")
    m["remove"] = [r for r in m.get("remove", []) if isinstance(r, str) and _safe_rel(r)]
    m["_raw"] = raw
    return m


def _plan_update(manifest: dict, only_missing: bool = False, skip_self: bool = False) -> dict:
    """Pobiera pliki z manifestu i zwraca te, które różnią się od zainstalowanych."""
    base = _update_base()
    changed: dict[str, bytes] = {}
    for rel in manifest["files"]:
        target = BASE_DIR / rel
        if (skip_self and target == SCRIPT_PATH) or (only_missing and target.exists()):
            continue
        content = _http_get(f"{base}/{rel}")
        _check_file(rel, content)
        if not target.exists() or target.read_bytes() != content:
            changed[rel] = content
    removed = [r for r in manifest["remove"] if (BASE_DIR / r).exists()]
    return {"version": str(manifest.get("version") or "?"), "changed": changed, "removed": removed,
            "pip": [p for p in manifest.get("pip", []) if isinstance(p, str)], "manifest": manifest["_raw"]}


def _apply_update(plan: dict) -> Path:
    """Zapisuje pliki (z kopią zapasową w .backup/), usuwa zbędne, doinstalowuje pakiety pip."""
    backup = BACKUP_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    for rel in list(plan["changed"]) + plan["removed"]:
        src = BASE_DIR / rel
        if src.exists():
            dst = backup / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for rel, content in plan["changed"].items():
        target = BASE_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".new")
        tmp.write_bytes(content)
        os.chmod(tmp, 0o755 if rel.endswith(".sh") or target == SCRIPT_PATH else 0o644)
        os.replace(tmp, target)
    for rel in plan["removed"]:
        try:
            (BASE_DIR / rel).unlink()
        except OSError:
            pass
    LOCAL_MANIFEST.write_bytes(plan["manifest"])
    if plan["pip"] and plan["changed"]:
        pip = Path(sys.executable).with_name("pip")
        rc, _, err = run(f"{shlex.quote(str(pip))} install --quiet "
                         + " ".join(shlex.quote(p) for p in plan["pip"]), timeout=900)
        if rc != 0:
            logger.warning("pip install: %s", err)
    try:
        for old in sorted(BACKUP_DIR.iterdir())[:-5]:   # zostaw 5 ostatnich kopii
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass
    return backup


def sync_components() -> list[str]:
    """Przy starcie bota: dociąga z GitHuba pliki, których brakuje (np. wtyczki po aktualizacji
    starszą wersją bota, która pobierała tylko telegram_bot.py). Samego bota nie podmienia."""
    notes: list[str] = []
    try:
        if UPDATE_MARKER.exists():
            notes.append(UPDATE_MARKER.read_text(encoding="utf-8").strip())
            UPDATE_MARKER.unlink()
    except OSError:
        pass
    try:
        local = json.loads(LOCAL_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        local = None
    up_to_date = bool(local) and local.get("version") == VERSION
    if up_to_date and all((BASE_DIR / f).exists() for f in local.get("files", [])):
        return notes
    try:
        plan = _plan_update(_fetch_manifest(), only_missing=up_to_date, skip_self=True)
        if plan["changed"] or plan["removed"] or not local:
            _apply_update(plan)
    except Exception as e:
        logger.warning("Dociąganie plików z GitHuba nieudane: %s", e)
        notes.append(f"⚠️ Nie udało się pobrać plików z GitHuba ({esc(str(e))}) — "
                     "spróbuję przy następnym starcie albo przez /rpi5_update.")
        return notes
    if plan["changed"] or plan["removed"]:
        notes.append("📦 Dociągnięto z GitHuba: " + ", ".join(sorted(plan["changed"]) or ["—"])
                     + (" · usunięto: " + ", ".join(plan["removed"]) if plan["removed"] else ""))
    return notes


async def cmd_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/rpi5_update — sprawdza GitHub; drugie wysłanie w ciągu 30 s instaluje całość i restartuje bota."""
    if not authorized(update):
        await update.message.reply_text("🚫 Brak uprawnień.")
        return
    uid = update.effective_user.id
    now = datetime.now()

    pending = _update_plans.pop(uid, None)
    if pending and (now - pending[0]).total_seconds() < 30:
        plan = pending[1]
        await update.message.reply_text("⏳ Instaluję aktualizację…")
        try:
            backup = await asyncio.to_thread(_apply_update, plan)
        except Exception as e:
            await update.message.reply_text(
                f"❌ <b>Błąd instalacji:</b>\n<code>{esc(str(e))}</code>\n"
                f"Kopia poprzednich plików: <code>{esc(str(BACKUP_DIR))}</code>", parse_mode="HTML")
            return
        try:
            UPDATE_MARKER.write_text(f"✅ Zaktualizowano z {VERSION} do {plan['version']} "
                                     f"(plików: {len(plan['changed'])}).", encoding="utf-8")
        except OSError:
            pass
        await update.message.reply_text(
            f"✅ <b>Zainstalowano wersję {esc(plan['version'])}</b>\n"
            f"Kopia poprzednich plików: <code>{esc(str(backup))}</code>\n"
            "Restartuję bota — za chwilę potwierdzi start…", parse_mode="HTML")
        run("sleep 3 && systemctl restart telegram-bot.service")
        return

    msg = await update.message.reply_text("🔎 Sprawdzam aktualizacje na GitHubie…")
    try:
        plan = await asyncio.to_thread(lambda: _plan_update(_fetch_manifest()))
    except Exception as e:
        await msg.edit_text(f"❌ <b>Nie udało się sprawdzić aktualizacji:</b>\n<code>{esc(str(e))}</code>\n"
                            f"Źródło: <code>{esc(_update_base())}</code>", parse_mode="HTML")
        return
    if not plan["changed"] and not plan["removed"]:
        await msg.edit_text(f"✅ Masz najnowszą wersję (<code>{VERSION}</code>) — nic do zrobienia.",
                            parse_mode="HTML")
        return
    _update_plans[uid] = (now, plan)
    files = sorted(plan["changed"])
    listing = "\n".join(f"• <code>{esc(f)}</code>" for f in files[:20])
    if len(files) > 20:
        listing += f"\n• … i {len(files) - 20} więcej"
    if plan["removed"]:
        listing += "\n" + "\n".join(f"• usunięcie <code>{esc(f)}</code>" for f in plan["removed"])
    await msg.edit_text(
        f"🔄 <b>Dostępna aktualizacja</b>\n"
        f"Wersja: <code>{VERSION}</code> → <code>{esc(plan['version'])}</code>\n"
        f"Zmiany ({len(files) + len(plan['removed'])}):\n{listing}\n\n"
        "Wyślij <code>/rpi5_update</code> ponownie w ciągu <b>30 sekund</b>, aby zainstalować.\n"
        "Konfiguracja, kontrolery i notatki zostaną bez zmian; bot zrestartuje się na chwilę.",
        parse_mode="HTML")


# ── Harmonogram automatycznych raportów walutowych ───────────────────────────

async def scheduled_currency_report(context: ContextTypes.DEFAULT_TYPE):
    """Wysyłany przez JobQueue — pobiera i zapisuje kursy."""
    usd = nbp_fetch_rate("USD")
    eur = nbp_fetch_rate("EUR")
    if usd is None or eur is None:
        logger.warning("Nie udało się pobrać kursów w harmonogramie")
        return
    save_rate("USD", usd)
    save_rate("EUR", eur)

    now_hour = datetime.now().hour

    # Raport poranny 08:00
    if now_hour == 8:
        usd_prev = load_yesterday_close("USD")
        eur_prev  = load_yesterday_close("EUR")
        usd_chg = change_str(usd, usd_prev, "vs wczoraj") if usd_prev else "brak danych"
        eur_chg  = change_str(eur,  eur_prev,  "vs wczoraj") if eur_prev  else "brak danych"
        msg = (
            f"🌅 <b>Raport poranny — {date.today().strftime('%d.%m.%Y')}</b>\n\n"
            f"💵 <b>USD/PLN</b>: <b>{usd:.4f} PLN</b>\n   {usd_chg}\n\n"
            f"💶 <b>EUR/PLN</b>: <b>{eur:.4f} PLN</b>\n   {eur_chg}\n\n"
            f"📡 NBP Tabela C"
        )
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")

    # Raport wieczorny 20:00
    elif now_hour == 20:
        usd_rates = load_rates_today("USD")
        eur_rates  = load_rates_today("EUR")
        usd_forecast = trend_label(linear_regression_slope(usd_rates)) if len(usd_rates) >= 3 else "za mało danych"
        eur_forecast  = trend_label(linear_regression_slope(eur_rates))  if len(eur_rates)  >= 3 else "za mało danych"
        usd_diff = usd_rates[-1] - usd_rates[0] if len(usd_rates) >= 2 else 0
        eur_diff  = eur_rates[-1]  - eur_rates[0]  if len(eur_rates)  >= 2 else 0

        msg = (
            f"🌙 <b>Podsumowanie dnia — {date.today().strftime('%d.%m.%Y')}</b>\n\n"
            f"💵 <b>USD/PLN</b>\n"
            f"   Otwarcie: {usd_rates[0]:.4f} | Zamknięcie: <b>{usd:.4f}</b>\n"
            f"   Zmiana: {usd_diff:+.4f} PLN\n"
            f"   🔮 Prognoza: <b>{usd_forecast}</b>\n\n"
            f"💶 <b>EUR/PLN</b>\n"
            f"   Otwarcie: {eur_rates[0]:.4f} | Zamknięcie: <b>{eur:.4f}</b>\n"
            f"   Zmiana: {eur_diff:+.4f} PLN\n"
            f"   🔮 Prognoza: <b>{eur_forecast}</b>\n\n"
            f"📊 <i>Prognoza oparta na regresji liniowej. Nie stanowi porady inwestycyjnej.</i>"
        )
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")


# ── Odbiór edycji notatki uruchomionej z przycisku inline ────────────────────

async def inline_note_edit_receiver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Przechwytuje tekst gdy edycja notatki uruchomiona przez przycisk inline."""
    if "nts_edytuj" not in ctx.user_data:
        return
    temat = ctx.user_data.pop("nts_edytuj")
    nowa = update.message.text.strip()
    ok_flag, msg = notes_update(temat, nowa)
    await update.message.reply_text(("" if ok_flag else "⚠️ ") + msg, parse_mode="HTML")

async def startup_notice(context: ContextTypes.DEFAULT_TYPE):
    """Po aktualizacji / dociągnięciu wtyczek / migracji — jedna wiadomość na czat."""
    lines = [f"🤖 <b>Bot v{VERSION} uruchomiony</b>"] + STARTUP_NOTES
    if PLUGIN_ERRORS:
        lines.append("⚠️ <b>Wtyczki z błędem:</b>\n" + "\n".join(esc(e) for e in PLUGIN_ERRORS))
    try:
        await context.bot.send_message(chat_id=CHAT_ID, text="\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.warning("Wiadomość startowa: %s", e)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        logger.error("Brak TELEGRAM_BOT_TOKEN!")
        sys.exit(1)

    logger.info("Uruchamiam bota v%s…", VERSION)
    STARTUP_NOTES.extend(sync_components())   # brakujące wtyczki/pliki z GitHuba
    app = Application.builder().token(BOT_TOKEN).build()

    # Komendy serwera — prefix rpi5_
    app.add_handler(CommandHandler("rpi5_help",   cmd_help))
    app.add_handler(CommandHandler("rpi5_status", cmd_status))
    app.add_handler(CommandHandler("rpi5_disks",  cmd_disks))
    app.add_handler(CommandHandler("rpi5_raid",   cmd_raid))
    app.add_handler(CommandHandler("rpi5_df",     cmd_df))
    app.add_handler(CommandHandler("rpi5_uptime", cmd_uptime))
    app.add_handler(CommandHandler("rpi5_logs",   cmd_logs))
    app.add_handler(CommandHandler("rpi5_info",    cmd_info))
    app.add_handler(CommandHandler("rpi5_reboot",  cmd_reboot))
    app.add_handler(CommandHandler("rpi5_update",  cmd_update))
    app.add_handler(CommandHandler("start",        cmd_start))

    # Komendy walutowe
    app.add_handler(CommandHandler("kurs",           cmd_kurs))
    app.add_handler(CommandHandler("kurs_historia",  cmd_kurs_historia))
    app.add_handler(CommandHandler("kurs_prognoza",  cmd_kurs_prognoza))

    # ── Notatki — ConversationHandler (obsługuje tryb edycji) ──
    notes_conv = ConversationHandler(
        entry_points=[CommandHandler("rpi5_notatki", cmd_notatki)],
        states={
            _NOTE_ADD_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_notatki_add_save)
            ],
            _NOTE_EDIT_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_notatki_edit_save)
            ],
        },
        fallbacks=[CommandHandler("anuluj", cmd_anuluj)],
        allow_reentry=True,
    )
    app.add_handler(notes_conv)
    app.add_handler(CallbackQueryHandler(notes_callback, pattern="^nts:"))

    # Wtyczki z katalogu plugins/ (np. access pointy Omada/UniFi)
    load_plugins(app)
    # Obsługuje edycję notatki gdy uruchomiona z przycisku inline (poza ConversationHandler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, inline_note_edit_receiver), group=1)

    # Harmonogram kursów: co godzinę (08:00 = raport poranny, 20:00 = wieczorny)
    if CHAT_ID:
        app.job_queue.run_repeating(
            scheduled_currency_report,
            interval=3600,
            first=60,
            name="currency_hourly"
        )
        logger.info("Harmonogram kursów walut aktywny (co 1h, raporty o 08:00 i 20:00)")
        if STARTUP_NOTES or PLUGIN_ERRORS:
            app.job_queue.run_once(startup_notice, when=5, name="startup_notice")

    logger.info("Bot gotowy. Czekam na komendy…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
