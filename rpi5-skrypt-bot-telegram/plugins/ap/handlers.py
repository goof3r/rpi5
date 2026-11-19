"""Komendy, przyciski, dodawanie kontrolerów i zadanie monitorujące — warstwa Telegrama."""

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
                          MessageHandler, filters)

from . import store, views
from .common import esc, split_message
from .monitor import Monitor, render_outgoing

log = logging.getLogger("ap-monitor")
CORE = None
MON: Monitor | None = None

(A_TYPE, A_NAME, A_URL, A_CID, A_SECRET, A_UAUTH, A_KEY, A_USER, A_PASS, A_ALERTS) = range(700, 710)


def _markup(buttons):
    if not buttons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in buttons])


async def _reply(message, text: str, buttons=None) -> None:
    parts = split_message(text) or ["—"]
    for part in parts[:-1]:
        await message.reply_text(part, parse_mode="HTML")
    await message.reply_text(parts[-1], parse_mode="HTML", reply_markup=_markup(buttons))


async def _show(update: Update, text: str, buttons=None) -> None:
    """Odpowiedź na komendę albo edycja wiadomości z przyciskiem."""
    q = update.callback_query
    if q and len(text) <= 3800:
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=_markup(buttons))
        except Exception as e:
            if "not modified" not in str(e).lower():
                log.warning("Edycja wiadomości: %s", e)
        return
    await _reply(q.message if q else update.message, text, buttons)


async def _busy(update: Update):
    if update.callback_query:
        await update.callback_query.answer("Sprawdzam…")
        return None
    return await update.message.reply_text("⏳ Pytam kontrolery…")


async def _done(wait) -> None:
    if wait is not None:
        try:
            await wait.delete()
        except Exception:
            pass


# ── podgląd ───────────────────────────────────────────────────────────────────

def _view_cmd(type_filter):
    async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not CORE.authorized(update):
            return
        wait = await _busy(update)
        query = " ".join(ctx.args or []).strip()
        if query:
            text, buttons = await asyncio.to_thread(views.search, query, type_filter)
        else:
            text, buttons = await asyncio.to_thread(views.summary, type_filter)
        await _done(wait)
        await _show(update, text, buttons)
    return handler


def _all_cmd(type_filter):
    async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not CORE.authorized(update):
            return
        wait = await _busy(update)
        text, buttons = await asyncio.to_thread(views.all_view, type_filter)
        await _done(wait)
        await _show(update, text, buttons)
    return handler


async def view_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ap:sum:<t> · ap:all:<t> · ap:s:<kontroler>:<lokalizacja>"""
    q = update.callback_query
    if not CORE.authorized(update):
        await q.answer("Brak dostępu")
        return
    await q.answer("Sprawdzam…")
    parts = (q.data or "").split(":", 3)
    kind = parts[1] if len(parts) > 1 else "sum"
    if kind == "s" and len(parts) == 4:
        text, buttons = await asyncio.to_thread(views.site_view, parts[2], parts[3])
    else:
        tf = views.CODE_TYPES.get(parts[2] if len(parts) > 2 else "")
        fn = views.all_view if kind == "all" else views.summary
        text, buttons = await asyncio.to_thread(fn, tf)
    await _show(update, text, buttons)


# ── kontrolery ────────────────────────────────────────────────────────────────

async def cmd_controllers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not CORE.authorized(update):
        return
    text, buttons = await asyncio.to_thread(views.controllers_view)
    await _show(update, text, buttons)


async def controllers_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not CORE.authorized(update):
        await q.answer("Brak dostępu")
        return
    parts = (q.data or "").split(":", 2)
    action, cid = parts[1], (parts[2] if len(parts) > 2 else "")
    await q.answer("Sprawdzam…" if action == "test" else None)
    fn = {"list": lambda: views.controllers_view(), "test": lambda: views.test_controller(cid),
          "al": lambda: views.toggle_alerts(cid), "del": lambda: views.confirm_delete(cid),
          "delok": lambda: views.delete(cid)}.get(action, views.controllers_view)
    text, buttons = await asyncio.to_thread(fn)
    await _show(update, text, buttons)


# ── dodawanie kontrolera (rozmowa) ────────────────────────────────────────────

def _rec(ctx) -> dict:
    return ctx.user_data.setdefault("ap_add", {})


async def _ask(update: Update, text: str, buttons=None) -> None:
    chat = update.effective_chat
    await chat.send_message(text, parse_mode="HTML", reply_markup=_markup(buttons))


async def _forget_secret(update: Update) -> None:
    try:
        await update.message.delete()
    except Exception:
        await _ask(update, "⚠️ Nie mogę usunąć wiadomości z danymi dostępowymi — usuń ją ręcznie "
                           "(albo daj botu prawo usuwania wiadomości w tej grupie).")


async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not CORE.authorized(update):
        if update.callback_query:
            await update.callback_query.answer("Brak dostępu")
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    ctx.user_data["ap_add"] = {}
    await _ask(update, "➕ <b>Nowy kontroler access pointów</b>\nPotrzebny jest dostęp <b>tylko do odczytu</b>. "
                       "Wiadomości z kluczami i hasłami od razu usunę.\n\nWybierz typ:",
               [[("Omada (TP-Link)", "apadd:omada"), ("UniFi (Ubiquiti)", "apadd:unifi")],
                [("✖️ Anuluj", "apadd:cancel")]])
    return A_TYPE


async def add_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "apadd:cancel":
        return await add_cancel(update, ctx)
    t = q.data.split(":", 1)[1]
    _rec(ctx)["type"] = t
    await q.edit_message_text(f"Typ: <b>{store.TYPE_LABEL[t]}</b>\n\nPodaj <b>nazwę</b> kontrolera "
                              "(np. klient albo miejsce):", parse_mode="HTML")
    return A_NAME


async def add_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()[:40]
    if not name:
        await _ask(update, "Nazwa nie może być pusta — podaj nazwę:")
        return A_NAME
    if any(c["name"].lower() == name.lower() for c in store.load_controllers()):
        await _ask(update, f"Kontroler „{esc(name)}” już istnieje — podaj inną nazwę:")
        return A_NAME
    rec = _rec(ctx)
    rec["name"] = name
    example = "https://192.168.1.10:8043" if rec["type"] == "omada" else \
        "https://192.168.1.1 (UniFi OS: UDM, Cloud Key, UCG) albo https://serwer:8443"
    await _ask(update, f"Podaj <b>adres</b> kontrolera, np. <code>{esc(example)}</code>")
    return A_URL


async def add_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = (update.message.text or "").strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
        await _ask(update, "To nie wygląda na adres — podaj np. <code>https://192.168.1.10:8043</code>")
        return A_URL
    rec = _rec(ctx)
    rec["url"] = url
    if rec["type"] == "omada":
        await _ask(update, "W kontrolerze Omada: <b>Ustawienia → Platform Integration → Open API → Add New App</b>\n"
                           "• Mode: <b>Client</b> · Role: <b>Viewer</b> · dostęp do potrzebnych lokalizacji\n\n"
                           "Wyślij <b>Client ID</b>:")
        return A_CID
    await _ask(update, "Jak bot ma się logować do UniFi?",
               [[("🔑 Klucz API (zalecane)", "apadd:key")], [("👤 Konto „View Only”", "apadd:acct")],
                [("✖️ Anuluj", "apadd:cancel")]])
    return A_UAUTH


async def add_client_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _rec(ctx)["client_id"] = (update.message.text or "").strip()
    await _forget_secret(update)
    await _ask(update, "Wyślij <b>Client Secret</b> (wiadomość usunę):")
    return A_SECRET


async def add_secret(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _rec(ctx)["client_secret"] = (update.message.text or "").strip()
    await _forget_secret(update)
    return await _test_and_ask(update, ctx)


async def add_uauth(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "apadd:cancel":
        return await add_cancel(update, ctx)
    if q.data == "apadd:key":
        _rec(ctx)["auth"] = "key"
        await q.edit_message_text(
            "W UniFi Network (9.0 lub nowszy): <b>Ustawienia → Control Plane → Integrations → Create API Key</b>.\n"
            "Klucz daje dostęp tylko do odczytu.\n\nWyślij <b>klucz API</b> (wiadomość usunę):", parse_mode="HTML")
        return A_KEY
    _rec(ctx)["auth"] = "account"
    await q.edit_message_text(
        "Załóż lokalne konto administratora z rolą <b>View Only</b> (bez 2FA, nie konto Ubiquiti w chmurze).\n\n"
        "Wyślij <b>login</b>:", parse_mode="HTML")
    return A_USER


async def add_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _rec(ctx)["api_key"] = (update.message.text or "").strip()
    await _forget_secret(update)
    return await _test_and_ask(update, ctx)


async def add_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _rec(ctx)["username"] = (update.message.text or "").strip()
    await _ask(update, "Wyślij <b>hasło</b> (wiadomość usunę):")
    return A_PASS


async def add_pass(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _rec(ctx)["password"] = (update.message.text or "").strip()
    await _forget_secret(update)
    return await _test_and_ask(update, ctx)


async def _test_and_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rec = _rec(ctx)
    msg = await update.effective_chat.send_message("⏳ Sprawdzam połączenie z kontrolerem…")
    ok, info = await asyncio.to_thread(views.test_record, rec)
    tail = ("\n\nCzy wysyłać <b>alerty</b>, gdy AP przestanie działać?\n"
            "„Tylko podgląd” — stan sprawdzasz komendą /ap.") if ok else \
        "\n\nZapisać mimo to? (np. kontroler chwilowo wyłączony)"
    buttons = [[("🔔 Alerty", "apadd:al1"), ("👁 Tylko podgląd", "apadd:al0")], [("✖️ Anuluj", "apadd:cancel")]]
    try:
        await msg.edit_text(info + tail, parse_mode="HTML", reply_markup=_markup(buttons))
    except Exception:
        await _ask(update, info + tail, buttons)
    return A_ALERTS


async def add_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "apadd:cancel":
        return await add_cancel(update, ctx)
    rec = dict(_rec(ctx))
    rec["alerts"] = q.data == "apadd:al1"
    await asyncio.to_thread(store.add_controller, rec)
    ctx.user_data.pop("ap_add", None)
    await q.edit_message_text(
        f"✅ Dodano kontroler <b>{esc(rec['name'])}</b> ({store.TYPE_LABEL[rec['type']]}) — "
        f"{'alerty włączone' if rec['alerts'] else 'tylko podgląd'}.\n"
        "Podgląd: /ap · zarządzanie: /kontrolery", parse_mode="HTML")
    return ConversationHandler.END


async def add_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("ap_add", None)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text("❌ Anulowano dodawanie kontrolera.")
        except Exception:
            pass
    else:
        await _ask(update, "❌ Anulowano dodawanie kontrolera.")
    return ConversationHandler.END


# ── powiadomienia ─────────────────────────────────────────────────────────────

async def cmd_notify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not CORE.authorized(update):
        return
    text = await asyncio.to_thread(views.notify_command, list(ctx.args or []))
    await _reply(update.message, text)


# ── zadanie cykliczne ─────────────────────────────────────────────────────────

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        items = await asyncio.to_thread(MON.check_all)
    except Exception:
        log.exception("Monitor AP: nieoczekiwany błąd")
        return
    for item in items:
        try:
            for part in split_message(render_outgoing(item)):
                await context.bot.send_message(chat_id=CORE.chat_id, text=part, parse_mode="HTML")
        except Exception as e:
            log.warning("Monitor AP: nie wysłano wiadomości (%s) — ponowię", e)
            await asyncio.to_thread(MON.requeue, item)


# ── rejestracja ───────────────────────────────────────────────────────────────

def register(app, core) -> None:
    global CORE, MON
    CORE = core
    notes = store.migrate_legacy(core.run)
    if notes:
        core.startup_notes.extend(notes)
    MON = Monitor()

    text_in = filters.TEXT & ~filters.COMMAND
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("kontroler_dodaj", add_start),
                      CallbackQueryHandler(add_start, pattern=r"^apc:add$")],
        states={
            A_TYPE: [CallbackQueryHandler(add_type, pattern=r"^apadd:(omada|unifi|cancel)$")],
            A_NAME: [MessageHandler(text_in, add_name)],
            A_URL: [MessageHandler(text_in, add_url)],
            A_CID: [MessageHandler(text_in, add_client_id)],
            A_SECRET: [MessageHandler(text_in, add_secret)],
            A_UAUTH: [CallbackQueryHandler(add_uauth, pattern=r"^apadd:(key|acct|cancel)$")],
            A_KEY: [MessageHandler(text_in, add_key)],
            A_USER: [MessageHandler(text_in, add_user)],
            A_PASS: [MessageHandler(text_in, add_pass)],
            A_ALERTS: [CallbackQueryHandler(add_alerts, pattern=r"^apadd:(al1|al0|cancel)$")],
        },
        fallbacks=[CommandHandler("anuluj", add_cancel),
                   CallbackQueryHandler(add_cancel, pattern=r"^apadd:cancel$")],
        conversation_timeout=900,
        allow_reentry=True,
    ))

    for cmd, tf in (("ap", None), ("omada", "omada"), ("unifi", "unifi")):
        app.add_handler(CommandHandler(cmd, _view_cmd(tf)))
    for cmd, tf in (("ap_wszystkie", None), ("omada_wszystkie", "omada"), ("unifi_wszystkie", "unifi")):
        app.add_handler(CommandHandler(cmd, _all_cmd(tf)))
    app.add_handler(CommandHandler("kontrolery", cmd_controllers))
    for cmd in ("powiadomienia", "omada_powiadomienia"):
        app.add_handler(CommandHandler(cmd, cmd_notify))
    app.add_handler(CallbackQueryHandler(view_callback, pattern=r"^ap:"))
    app.add_handler(CallbackQueryHandler(controllers_callback, pattern=r"^apc:(list|test|al|del|delok)"))

    if core.chat_id:
        interval = store.defaults().interval
        app.job_queue.run_repeating(monitor_job, interval=interval, first=30, name="ap_monitor")
        log.info("Monitor AP: sprawdzanie co %ss", interval)
