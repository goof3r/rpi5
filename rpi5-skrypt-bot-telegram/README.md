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

### Omada (access pointy)
| Komenda | Opis |
|---|---|
| `/omada` | Podsumowanie wszystkich lokalizacji + przyciski do każdej z nich |
| `/omada <lokalizacja>` | Stan AP w danej lokalizacji (wystarczy fragment nazwy) |
| `/omada_wszystkie` | Wszystkie AP ze wszystkich lokalizacji |
| `/omada_powiadomienia` | Godziny wysyłania alertów, przypomnienia, pauza |

---

## Monitor access pointów Omada

`omada_monitor.py` co minutę pyta kontroler TP-Link Omada (Open API) o stan wszystkich AP
we wszystkich lokalizacjach. Gdy AP ma stan inny niż **Połączony** (rozłączony, utracony
kontakt, oczekuje, izolowany, aktualizacja…), od razu wysyła alert na Telegram — tym samym
botem, na ten sam czat.

- **Alert** od razu po wykryciu; kilka AP naraz → jedna wiadomość z listą.
- **Przypomnienia**, gdy AP nie wraca: domyślnie po 30 min, 1 h i 3 h od awarii, potem cisza.
- **Powrót**: wiadomość „znów połączone” z czasem przerwy.
- **Kontroler niedostępny** (3 nieudane próby z rzędu) → osobny alert; po jego powrocie pierwsze
  sprawdzenie nie alarmuje (AP muszą się ponownie zgłosić).
- **Godziny powiadomień** (`/omada_powiadomienia godziny …`): poza oknem zdarzenia są zbierane
  i wysyłane jednym podsumowaniem, gdy okno się otworzy.
- **Pauza** (`/omada_powiadomienia pauza 2h`) — np. na czas aktualizacji firmware.

Stan języka panelu nie ma znaczenia — monitor czyta kody stanu z API, nie napisy.

### Instalacja (po `install.sh`)

1. W kontrolerze: **Ustawienia → Platform Integration → Open API → Add New App**
   — tryb **Client**, rola tylko do odczytu (Viewer), dostęp do wszystkich lokalizacji.
   Skopiuj **Client ID** i **Client Secret** (hasło administratora nie jest potrzebne).
2. Na serwerze (jednorazowo):
   ```bash
   cd /tmp && rm -rf rpi5 && git clone https://github.com/goof3r/rpi5.git
   sudo bash rpi5/rpi5-skrypt-bot-telegram/install_omada.sh
   ```
   Instalator zapyta o adres kontrolera i dane Open API (resztę pytań można zatwierdzić Enterem),
   sprawdzi połączenie (wypisze listę AP), utworzy usługę `omada-monitor`, zaktualizuje bota
   i go zrestartuje. Na Telegram przyjdzie „Monitor Omada uruchomiony”.

### Aktualizacje

Kolejne wersje wgrywa `/rpi5_update` (wysłane dwa razy) — pobiera z GitHuba `telegram_bot.py`
**i** `omada_monitor.py`, robi kopie `.bak` i restartuje bota oraz usługę `omada-monitor`.
Ustawienia w `/etc/server-monitor.env` i `/var/lib/omada-monitor/` zostają bez zmian.

### Ustawienia powiadomień z Telegrama

```
/omada_powiadomienia                                  — aktualne ustawienia + pomoc
/omada_powiadomienia godziny zawsze
/omada_powiadomienia godziny 07:00-22:00
/omada_powiadomienia godziny pn-pt 07:00-17:00; so,nd 09:00-13:00
/omada_powiadomienia godziny 22:00-06:00              — okno przez północ
/omada_powiadomienia przypomnienia 30m 1h 3h
/omada_powiadomienia przypomnienia brak
/omada_powiadomienia pauza 2h      (albo: pauza do 18:00)
/omada_powiadomienia wznow
/omada_powiadomienia domyslne
```

Zmiany działają od następnego sprawdzenia, bez restartu (zapis w
`/var/lib/omada-monitor/settings.json`).

### Konfiguracja (`/etc/server-monitor.env`)

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `OMADA_URL` | — | Adres kontrolera, np. `https://192.168.1.10:8043` |
| `OMADA_CLIENT_ID` / `OMADA_CLIENT_SECRET` | — | Dane aplikacji Open API (tryb Client) |
| `OMADA_ID` | z `/api/info` | omadacId kontrolera (zwykle niepotrzebne) |
| `OMADA_VERIFY_SSL` | `0` | `1` = sprawdzaj certyfikat (`OMADA_CA_FILE` dla własnego CA) |
| `OMADA_INTERVAL` | `60` | Co ile sekund sprawdzać |
| `OMADA_REMIND_MINUTES` | `30,60,180` | Przypomnienia (minuty od początku awarii) |
| `OMADA_NOTIFY_HOURS` | `zawsze` | Domyślne godziny powiadomień (bot może je nadpisać) |
| `OMADA_CONFIRM_CHECKS` | `1` | Ile kolejnych złych odczytów przed alertem (1 = od razu) |
| `OMADA_CONTROLLER_FAIL_CHECKS` | `3` | Ile nieudanych zapytań przed alertem o kontrolerze |
| `OMADA_IGNORE_MAINTENANCE` | `0` | `1` = nie alarmuj przy aktualizacji/restarcie/provisioningu |
| `OMADA_DEVICE_TYPES` | `ap` | Typy urządzeń, np. `ap,switch,gateway` |

### Diagnostyka

```bash
journalctl -u omada-monitor -f
sudo /opt/server-monitor/venv/bin/python3 /opt/server-monitor/omada_monitor.py list
sudo /opt/server-monitor/venv/bin/python3 /opt/server-monitor/omada_monitor.py check --dry-run
```

Zmiana adresu kontrolera lub danych Open API: uruchom ponownie `install_omada.sh`
(Enter zostawia dotychczasowe wartości).

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
sudo cp rpi5-skrypt-bot-telegram/telegram_bot.py rpi5-skrypt-bot-telegram/omada_monitor.py /opt/server-monitor/
sudo systemctl restart telegram-bot
sudo systemctl restart omada-monitor   # jeśli zainstalowany
```
