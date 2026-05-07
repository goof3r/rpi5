# RPi5 Telegram Bot — Monitor serwera

Bot Telegram do zarządzania serwerem (Raspberry Pi 5 / Debian / OpenMediaVault) przez chat.  
Obsługuje monitoring RAID, dysków, systemu, kursy walut z NBP/Revolut/InternetowyKantor.pl oraz notatki.

---

## Dostępne komendy

### Serwer
| Komenda | Opis |
|---|---|
| `/rpi5_status` | Pełny przegląd systemu (RAM, load, dyski, RAID, miejsce) |
| `/rpi5_info` | Informacje o systemie (`uname -a`, OS, architektura) |
| `/rpi5_disks` | Stan dysków + wynik SMART |
| `/rpi5_raid` | Status macierzy RAID (`/proc/mdstat` + `mdadm --detail`) |
| `/rpi5_df` | Wolne miejsce na partycjach |
| `/rpi5_uptime` | Czas działania, load avg, RAM, temperatura |
| `/rpi5_logs` | Ostatnie logi monitoringu RAID |
| `/rpi5_reboot` | Restart serwera (wymaga potwierdzenia) |
| `/rpi5_update` | Aktualizacja skryptu bota z GitHub |
| `/rpi5_help` | Lista wszystkich komend |

### Kursy walut
| Komenda | Opis |
|---|---|
| `/kurs` | Aktualny kurs USD i EUR (NBP · Revolut · InternetowyKantor.pl) |
| `/kurs_historia` | Podsumowanie kursów z bieżącego dnia |
| `/kurs_prognoza` | Tendencja na podstawie regresji liniowej |

### Notatki
| Komenda | Opis |
|---|---|
| `/rpi5_notatki lista` | Wszystkie notatki |
| `/rpi5_notatki dodaj <temat>` | Nowa notatka (treść w kolejnej wiadomości) |
| `/rpi5_notatki czytaj <temat>` | Odczytaj notatkę |
| `/rpi5_notatki edytuj <temat>` | Edytuj notatkę |
| `/rpi5_notatki usun <temat>` | Usuń notatkę |
| `/rpi5_notatki szukaj <fraza>` | Szukaj w notatkach |

---

## Wymagania

- Debian 12 / OpenMediaVault 7 (lub kompatybilny)
- Python 3.11+
- `mdadm`, `smartmontools`
- Token bota Telegram (od [@BotFather](https://t.me/BotFather))
- Chat ID grupy lub kanału docelowego

---

## Instalacja

### 1. Pobierz pliki na serwer

```bash
cd /tmp
git clone https://github.com/goof3r/rpi5.git
cd rpi5/rpi5-skrypt-bot-telegram
```

### 2. Usuń stare pliki (jeśli istnieją)

```bash
sudo bash cleanup.sh
```

### 3. Uruchom instalator

```bash
sudo bash install.sh
```

Instalator przeprowadzi przez konfigurację krok po kroku:
- Token bota Telegram
- Chat ID
- Urządzenie RAID (domyślnie `/dev/md0`)
- Lista dysków do monitorowania
- Próg zajętości dysku dla alertu
- Interwał monitorowania (minuty)
- URL do aktualizacji skryptu *(opcjonalne — patrz sekcja Aktualizacja)*

Po zakończeniu instalator:
- Tworzy środowisko Python w `/opt/server-monitor/venv/`
- Zapisuje konfigurację w `/etc/server-monitor.env`
- Rejestruje i uruchamia usługi systemd (`telegram-bot.service`, `server-monitor.timer`)
- Wysyła testową wiadomość na Telegram

### Lokalizacja plików po instalacji

| Ścieżka | Zawartość |
|---|---|
| `/opt/server-monitor/` | Skrypty bota i RAID monitora |
| `/etc/server-monitor.env` | Konfiguracja (tokeny, chat ID) |
| `/var/lib/currency-monitor/` | Historia kursów walut (CSV) |
| `/var/lib/currency-monitor/notes.json` | Notatki |
| `/var/log/telegram_bot.log` | Logi bota |
| `/var/log/raid_monitor.log` | Logi monitoringu RAID |

### Diagnostyka

```bash
# Logi bota na żywo
journalctl -u telegram-bot -f

# Status usługi
systemctl status telegram-bot

# Logi RAID monitora
journalctl -u server-monitor -n 30
```

---

## Aktualizacja skryptu

### Krok 1 — jednorazowa konfiguracja URL aktualizacji

Dodaj do `/etc/server-monitor.env` na serwerze:

```bash
echo 'SCRIPT_UPDATE_URL=https://raw.githubusercontent.com/goof3r/rpi5/master/rpi5-skrypt-bot-telegram/telegram_bot.py' \
  | sudo tee -a /etc/server-monitor.env

sudo systemctl restart telegram-bot
```

### Krok 2 — aktualizacja przez Telegram

Sprawdza i ściąga najnowszą wersję skryptu :

```
/rpi5_update
```

Bot wyświetli bieżącą wersję i źródło. Wyślij `/rpi5_update` **drugi raz w ciągu 30 sekund** aby potwierdzić.

Bot automatycznie:
1. Pobiera nowy skrypt z GitHub
2. Sprawdza poprawność składni Python
3. Tworzy backup (`/opt/server-monitor/telegram_bot.py.bak`)
4. Zastępuje stary skrypt nowym
5. Restartuje usługę — konfiguracja i dane są nienaruszone

### Aktualizacja ręczna (bez Telegrama)

```bash
cd /tmp/rpi5 && git pull
sudo cp rpi5-skrypt-bot-telegram/telegram_bot.py /opt/server-monitor/
sudo systemctl restart telegram-bot
```
