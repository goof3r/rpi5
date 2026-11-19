"""Diagnostyka wtyczki AP z konsoli (bez Telegrama).

  cd /opt/server-monitor
  sudo venv/bin/python3 -m plugins.ap list            # stan AP na wszystkich kontrolerach
  sudo venv/bin/python3 -m plugins.ap list --raw      # surowe dane z API
  sudo venv/bin/python3 -m plugins.ap check           # jedno sprawdzenie monitora (wiadomości na ekran)
  sudo venv/bin/python3 -m plugins.ap kontrolery      # lista kontrolerów (bez sekretów)
Opcja --env-file PLIK wczytuje zmienne (domyślnie /etc/server-monitor.env).
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import clients, store
from .monitor import Monitor, render_outgoing


def _load_env(path: str) -> bool:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m plugins.ap", description="Diagnostyka wtyczki AP")
    ap.add_argument("command", choices=["list", "check", "kontrolery"])
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--env-file", default="/etc/server-monitor.env")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _load_env(args.env_file)

    ctrls = store.load_controllers()
    if args.command == "kontrolery":
        for c in ctrls:
            safe = {k: v for k, v in c.items() if k not in store.SECRET_FIELDS}
            print(json.dumps(safe, ensure_ascii=False))
        print(f"Plik: {store.controllers_file()}  (kontrolerów: {len(ctrls)})")
        return 0
    if not ctrls:
        print("Brak kontrolerów — dodaj je w Telegramie: /kontroler_dodaj", file=sys.stderr)
        return 2
    if args.command == "check":
        for item in Monitor().check_all():
            print("──────── [Telegram] ────────\n" + render_outgoing(item) + "\n────────────────────────────")
        return 0

    rc = 0
    for ctrl, snap, err in clients.fetch_all(ctrls):
        head = f"== {ctrl['name']} ({store.TYPE_LABEL[ctrl['type']]}) {ctrl['url']}"
        if not snap:
            print(f"{head}\nBŁĄD: {err}\n")
            rc = 1
            continue
        if args.raw:
            print(head)
            print(json.dumps(snap.raw, ensure_ascii=False, indent=2))
            continue
        print(f"{head}  wersja {snap.version or '?'}  alerty: {'tak' if ctrl.get('alerts') else 'nie'}")
        rows = [("LOKALIZACJA", "NAZWA", "MODEL", "IP", "MAC", "KOD", "STAN")]
        for d in snap.devices:
            rows.append((d.site, d.name, d.model, d.ip, d.mac, d.code, ("OK " if d.ok else "!! ") + d.label))
        widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
        for r in rows:
            print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip())
        n_ok = sum(1 for d in snap.devices if d.ok)
        extra = (" · pominięte: " + ", ".join(f"{k}: {v}" for k, v in sorted(snap.skipped.items()))) \
            if snap.skipped else ""
        print(f"AP: {len(snap.devices)}, połączonych: {n_ok}{extra}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
