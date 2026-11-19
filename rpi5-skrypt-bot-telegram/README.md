# RPi5 Telegram Bot — Monitor serwera

Bot Telegram do zarządzania serwerem (Raspberry Pi 5 / Debian / OpenMediaVault) przez chat.  
Obsługuje monitoring RAID, dysków, systemu, kursy walut z NBP/Revolut/InternetowyKantor.pl, notatki
oraz — jako wtyczka — stan access pointów na kontrolerach **Omada** (TP-Link) i **UniFi** (Ubiquiti).

Całość (bot + wtyczki) aktualizuje się z Telegrama komendą `/rpi5_update` — bez logowania na serwer.

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
| `/rpi5_update` | Aktualizacja bota **i wszystkich wtyczek** z GitHuba |
| `/rpi5_help` | Lista wszystkich komend (z komendami wtyczek) |

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

### Access pointy (wtyczka `plugins/ap`)
| Komenda | Opis |
|---|---|
| `/ap` | Podsumowanie wszystkich kontrolerów + przyciski lokalizacji |
| `/ap <nazwa>` | Lokalizacja albo kontroler (wystarczy fragment nazwy) |
| `/omada` · `/unifi` | To samo, tylko dla kontrolerów danego typu |
| `/ap_wszystkie` | Wszystkie AP ze wszystkich kontrolerów |
| `/kontrolery` | Lista kontrolerów: test połączenia, alerty wł./wył., usuwanie |
| `/kontroler_dodaj` | Dodanie kontrolera (prowadzi krok po kroku) |
| `/powiadomienia` | Godziny alertów, przypomnienia, pauza |

---

## Access pointy — Omada i UniFi

Kontrolery dodaje się z Telegrama (`/kontroler_dodaj`) — może ich być dowolnie wiele, różnych
typów (np. kilku klientów). Bot potrzebuje dostępu **tylko do odczytu**:

| Kontroler | Dostęp | Gdzie go utworzyć |
|---|---|---|
| **Omada** (5.9+, w tym 6.x) | Open API — Client ID + Client Secret | Ustawienia → Platform Integration → Open API → Add New App: tryb **Client**, rola **Viewer** |
| **UniFi** Network 9+ | Klucz API (zalecane) | Ustawienia → Control Plane → Integrations → Create API Key |
| **UniFi** starsze / bez klucza | Lokalne konto z rolą **View Only** (bez 2FA) | Admins & Users → nowy administrator lokalny |

UniFi działa z konsolami UniFi OS (UDM, Cloud Key, UCG — adres `https://IP`) i z samodzielną
aplikacją Network (`https://serwer:8443`) — rodzaj jest wykrywany automatycznie.

Wiadomości z kluczami i hasłami bot od razu usuwa z czatu (w grupie potrzebuje do tego prawa
usuwania wiadomości). Dane zapisuje w `/var/lib/ap-monitor/controllers.json` (tylko root, 0600).

Przy dodawaniu wybierasz, czy kontroler ma **wysyłać alerty**, czy służy **tylko do podglądu**
(zmienisz to w `/kontrolery`). Dla kontrolerów z alertami bot co minutę sprawdza wszystkie AP:

- **Alert** od razu, gdy AP ma stan inny niż „Połączony”; kilka AP naraz → jedna wiadomość.
- **Przypomnienia**, gdy AP nie wraca: domyślnie po 30 min, 1 h i 3 h od awarii, potem cisza.
- **Powrót**: wiadomość „znów połączone” z czasem przerwy.
- **Kontroler niedostępny** (3 nieudane próby z rzędu) → osobny alert; po jego powrocie pierwsze
  sprawdzenie nie alarmuje (AP muszą się ponownie zgłosić).
- **Godziny powiadomień**: poza oknem zdarzenia są zbierane i przychodzą jednym podsumowaniem.
- **Pauza** — np. na czas aktualizacji firmware.

Język panelu nie ma znaczenia — bot czyta kody stanu z API, nie napisy.

### Ustawienia powiadomień

```
/powiadomienia                                  — aktualne ustawienia + pomoc
/powiadomienia godziny zawsze
/powiadomienia godziny 07:00-22:00
/powiadomienia godziny pn-pt 07:00-17:00; so,nd 09:00-13:00
/powiadomienia godziny 22:00-06:00              — okno przez północ
/powiadomienia przypomnienia 30m 1h 3h
/powiadomienia przypomnienia brak
/powiadomienia pauza 2h      (albo: pauza do 18:00)
/powiadomienia wznow
/powiadomienia domyslne
```

Zmiany działają od następnego sprawdzenia, bez restartu.

### Opcjonalne zmienne (`/etc/server-monitor.env`)

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `AP_INTERVAL` | `60` | Co ile sekund sprawdzać (wymaga restartu bota) |
| `AP_REMIND_MINUTES` | `30,60,180` | Domyślne przypomnienia (bot może je nadpisać) |
| `AP_NOTIFY_HOURS` | `zawsze` | Domyślne godziny powiadomień (bot może je nadpisać) |
| `AP_CONFIRM_CHECKS` | `1` | Ile kolejnych złych odczytów przed alertem (1 = od razu) |
| `AP_CONTROLLER_FAIL_CHECKS` | `3` | Ile nieudanych zapytań przed alertem o kontrolerze |
| `AP_IGNORE_MAINTENANCE` | `0` | `1` = nie alarmuj przy aktualizacji firmware / provisioningu |

Ustawienia `OMADA_*` z wersji 2.3 są przy pierwszym starcie 3.0 przenoszone do wtyczki
(kontroler „Omada” z alertami), a stara usługa `omada-monitor` jest wyłączana.

### Diagnostyka z konsoli

```bash
cd /opt/server-monitor
sudo venv/bin/python3 -m plugins.ap list         # stan AP na wszystkich kontrolerach (kolumna KOD = surowy stan)
sudo venv/bin/python3 -m plugins.ap list --raw   # surowe dane z API
sudo venv/bin/python3 -m plugins.ap kontrolery   # lista kontrolerów (bez sekretów)
```

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
- URL do aktualizacji *(opcjonalne — domyślnie to repozytorium)*

Po zakończeniu instalator:
- Tworzy środowisko Python w `/opt/server-monitor/venv/`
- Kopiuje bota i wtyczki według `manifest.json`
- Zapisuje konfigurację w `/etc/server-monitor.env`
- Rejestruje i uruchamia usługi systemd (`telegram-bot.service`, `server-monitor.timer`)
- Wysyła testową wiadomość na Telegram

### Lokalizacja plików po instalacji

| Ścieżka | Zawartość |
|---|---|
| `/opt/server-monitor/` | Bot, wtyczki (`plugins/`), RAID monitor, `manifest.json` |
| `/opt/server-monitor/.backup/` | Kopie plików sprzed 5 ostatnich aktualizacji |
| `/etc/server-monitor.env` | Konfiguracja (tokeny, chat ID) |
| `/var/lib/currency-monitor/` | Historia kursów walut (CSV), notatki |
| `/var/lib/ap-monitor/` | Kontrolery AP, ustawienia powiadomień, stan monitora |
| `/var/log/telegram_bot.log` | Logi bota |
| `/var/log/raid_monitor.log` | Logi monitoringu RAID |

### Diagnostyka

```bash
journalctl -u telegram-bot -f        # logi bota na żywo
systemctl status telegram-bot        # status usługi
journalctl -u server-monitor -n 30   # logi RAID monitora
```

---

## Aktualizacja

Wyślij w Telegramie `/rpi5_update`. Bot sprawdzi GitHub i pokaże, które pliki się zmieniły.
Wyślij `/rpi5_update` **drugi raz w ciągu 30 sekund**, aby zainstalować. Bot:

1. pobiera `manifest.json` i wszystkie wymienione w nim pliki (bot, wtyczki, skrypty),
2. sprawdza ich poprawność (składnia Pythona, JSON) — przy błędzie niczego nie zmienia,
3. zapisuje kopie poprzednich plików w `/opt/server-monitor/.backup/<data>/`,
4. instaluje nowe pliki, usuwa zbędne, w razie potrzeby doinstalowuje pakiety (`pip`),
5. restartuje się i potwierdza start nową wersją.

Konfiguracja, kontrolery, notatki i historia kursów zostają bez zmian.

Domyślnie aktualizacje pochodzą z tego repozytorium (`goof3r/rpi5`, gałąź `master`); inne źródło
ustawia `SCRIPT_UPDATE_URL` w `/etc/server-monitor.env` (adres `telegram_bot.py` w katalogu z `manifest.json`).

**Przejście ze starszej wersji (2.x):** stary bot pobiera przy `/rpi5_update` tylko `telegram_bot.py`
— nowa wersja przy pierwszym starcie sama dociąga brakujące wtyczki i potwierdza to wiadomością.
Starsza wersja bota wymaga ustawionego `SCRIPT_UPDATE_URL` (patrz wyżej).

### Nowa wtyczka

Wtyczka to moduł lub pakiet w `plugins/` z funkcją `register(app, core)` (opcjonalnie `HELP`
z opisem komend). Bot ładuje ją sam przy starcie. Żeby `/rpi5_update` ją pobierał, dopisz jej
pliki do `manifest.json` (i podnieś `version`).

### Aktualizacja ręczna (bez Telegrama)

```bash
cd /tmp && rm -rf rpi5 && git clone https://github.com/goof3r/rpi5.git
cd rpi5/rpi5-skrypt-bot-telegram
python3 -c 'import json; print("\n".join(json.load(open("manifest.json"))["files"]))' \
  | while read -r f; do sudo install -D -m 644 "$f" "/opt/server-monitor/$f"; done
sudo cp manifest.json /opt/server-monitor/
sudo chmod +x /opt/server-monitor/telegram_bot.py /opt/server-monitor/raid_monitor.sh
sudo systemctl restart telegram-bot
```
