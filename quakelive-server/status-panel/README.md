# qlstatus — publiczny panel statusu serwerów QL

Lekka, read-only strona statusu serwerów Quake Live, dostępna pod `http://<ip>/`.

Co pokazuje (auto-odświeżanie co 10 s):
- nazwa serwera (`sv_hostname`)
- port UDP
- status ONLINE/OFFLINE
- aktualna mapa + tryb gry
- liczba graczy/botów + lista (nick, fragi, czas połączenia)
- screenshot mapy (`levelshots/<mapname>.jpg` z pk3 — z baseq3 lub workshop)

**Stack:** czysty Python 3 stdlib (`http.server` + `socket`), brak zależności. Steam A2S_INFO/A2S_PLAYER zamiast Q3 `getstatus` (QL nie odpowiada na vanilla Q3 query).

## Architektura

- `app.py` — serwer HTTP, odpytuje każdy serwer z `servers.json` przez UDP A2S
- `servers.json` — lista (id/host/port/name)
- `cache/` — wyciągnięte levelshoty z pk3 (lazy, raz na mapę)

## Instalacja na serwerze QL

```bash
# Z hosta z lokalną kopią repo:
tar czf - -C status-panel . | ssh qlsrv "mkdir -p ~/qlstatus && tar xzf - -C ~/qlstatus"

# Na serwerze QL:
ssh qlsrv "cd ~/qlstatus && bash install.sh"
```

Instalator pisze unit `qlstatus.service` (`User=qlive`, bindowanie portu 80 via
`AmbientCapabilities=CAP_NET_BIND_SERVICE` — bez root, bez Flask, bez venv,
bez apt). Włącza i uruchamia usługę.

## Dostęp z internetu

Panel słucha na 0.0.0.0:80 wewnątrz LAN. Publiczne URL-e (`tsk.qlive.one.pl`)
wymagają przekierowania TCP 80 z routera na `192.168.1.32:80`.

## Konfiguracja

Edycja `servers.json` → `sudo systemctl restart qlstatus`.

## Diagnostyka

```bash
sudo journalctl -u qlstatus -f
curl -s http://127.0.0.1/api/status | jq
```
