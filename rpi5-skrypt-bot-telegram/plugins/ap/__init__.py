"""Wtyczka: access pointy na kontrolerach Omada (TP-Link) i UniFi (Ubiquiti).

Podgląd stanu AP, wiele kontrolerów dodawanych z Telegrama (dostęp tylko do odczytu),
opcjonalne alerty z przypomnieniami, godzinami powiadomień i pauzą.
Diagnostyka z konsoli (w /opt/server-monitor): python3 -m plugins.ap list
"""

NAME = "Access pointy (Omada, UniFi)"
HELP = (
    "<b>── Access pointy (Omada / UniFi) ──</b>\n"
    "  /ap                   — podsumowanie wszystkich kontrolerów\n"
    "  /ap &lt;nazwa&gt;          — lokalizacja lub kontroler\n"
    "  /omada · /unifi       — tylko kontrolery danego typu\n"
    "  /ap_wszystkie         — lista wszystkich AP\n"
    "  /kontrolery           — lista, test, alerty, usuwanie\n"
    "  /kontroler_dodaj      — dodaj kontroler (dane tylko do odczytu)\n"
    "  /powiadomienia        — godziny alertów, przypomnienia, pauza"
)


def register(app, core):
    from .handlers import register as _register
    _register(app, core)
