# Torrent RSS Downloader

Webowa aplikacja do monitorowania kanałów RSS i automatycznego pobierania torrentów. Działa na Raspberry Pi (i każdym systemie Linux z Python 3.8+).

## Funkcje

- Przeglądanie kanału RSS z filtrowaniem i wyszukiwaniem
- **Wzorce auto-pobierania** — definiujesz wzorzec tytułu (z `%` jako wildcard), a pasujące wstawki są pobierane automatycznie po każdym odświeżeniu RSS
- Obsługa wielu serwerów Transmission (RPC)
- Tryb `wget` — zapis pliku `.torrent` do wskazanego folderu (dla klientów z watch-folder)
- Kolejka pobrań z podglądem postępu na żywo
- Logowanie z hasłem (bcrypt)

---

## Wymagania

- Python 3.8+
- Git
- Raspberry Pi OS / Debian / Ubuntu (lub inna dystrybucja z systemd)

---

## Instalacja (jednorazowa)

### 1. Sklonuj repozytorium

```bash
git clone https://github.com/goof3r/claude-gof.git
cd claude-gof/torrent-rss-downloader
```

### 2. Utwórz plik konfiguracyjny

```bash
cp .env.example .env
nano .env
```

Uzupełnij wymagane wartości:

```env
# Wygeneruj losowy klucz: python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=wklej-tutaj-losowy-klucz

# Pełny URL Twojego kanału RSS
RSS_FEED_URL=https://rss.addres.your.tracker.com/TWOJ-TOKEN
```

### 3. Uruchom instalator

```bash
bash setup.sh
```

Skrypt automatycznie:
- tworzy wirtualne środowisko Python (`venv`)
- instaluje wszystkie zależności
- rejestruje i uruchamia usługę systemd `torrent-rss`
- włącza automatyczny start przy restarcie systemu

Po zakończeniu aplikacja jest dostępna pod adresem:

```
http://<IP-Raspberry-Pi>:5000
```

Domyślne dane logowania: **admin / admin** — zmień hasło po pierwszym logowaniu w Ustawieniach.

---

## Aktualizacja

```bash
cd ~/claude-gof/torrent-rss-downloader
bash update.sh
```

Skrypt pobiera najnowszą wersję z git, aktualizuje zależności i restartuje usługę.

---

## Zarządzanie usługą

```bash
sudo systemctl status  torrent-rss    # status
sudo systemctl restart torrent-rss    # restart
sudo systemctl stop    torrent-rss    # zatrzymaj
sudo systemctl start   torrent-rss    # uruchom

journalctl -u torrent-rss -f          # logi na żywo
journalctl -u torrent-rss --since today  # logi z dzisiaj
```

---

## Konfiguracja po instalacji

### Serwer Transmission

Przejdź do **Ustawienia → Serwery Transmission** i dodaj serwer:
- Host: IP lub hostname serwera z Transmission
- Port: domyślnie `9091`
- Ścieżka RPC: domyślnie `/transmission/rpc`
- Login/hasło: jeśli autoryzacja jest wyłączona, pozostaw puste

### Wzorce auto-pobierania

Przejdź do **Ustawienia → Wzorce auto-pobierania** i dodaj wzorzec tytułu RSS.

Znak `%` zastępuje **dowolną liczbę znaków**:

| Wzorzec | Co pobiera |
|---|---|
| `Zuzel.PGE.Ekstraliga.2026.05%` | wszystkie mecze żużla z maja 2026 |
| `Nemesis.2026%1080p%` | serial Nemesis 2026 w 1080p |
| `%Falubaz%` | każda wstawka zawierająca „Falubaz" |

Dla każdego wzorca możesz wybrać:
- **Serwer** — konkretny serwer Transmission lub automatyczny (pierwszy aktywny)
- **Folder docelowy** — gdzie trafia plik (przy trybie `wget`)

### Klient torrent

W **Ustawienia → Kanał RSS** wybierz klienta używanego przez auto-pobieranie:

| Opcja | Opis |
|---|---|
| `Transmission (RPC)` | wysyła torrent do wskazanego serwera Transmission |
| `Plik .torrent (wget)` | zapisuje plik `.torrent` do folderu docelowego |
| `Auto` | próbuje Transmission, przy braku serwera zapisuje plik |

---

## Struktura projektu

```
torrent-rss-downloader/
├── app.py              # główna aplikacja Flask, trasy HTTP
├── models.py           # modele bazy danych (SQLAlchemy)
├── rss_fetcher.py      # pobieranie RSS, dopasowanie wzorców, auto-pobieranie
├── scheduler.py        # scheduler APScheduler (polling RSS, sync statusów)
├── transmission_api.py # klient Transmission RPC
├── config.py           # konfiguracja z .env
├── requirements.txt    # zależności Python
├── setup.sh            # jednorazowa instalacja + usługa systemd
├── update.sh           # aktualizacja do nowej wersji
├── .env.example        # szablon konfiguracji
├── templates/          # szablony HTML (Jinja2 + Bootstrap 5)
└── static/             # CSS i JavaScript
```

---

## Zmienne środowiskowe (.env)

| Zmienna | Domyślna | Opis |
|---|---|---|
| `SECRET_KEY` | — | Klucz sesji Flask (wymagany) |
| `RSS_FEED_URL` | — | URL kanału RSS |
| `RSS_POLL_INTERVAL` | `15` | Interwał pollingu w minutach |
| `FLASK_PORT` | `5000` | Port HTTP |
| `FLASK_DEBUG` | `false` | Tryb debug (nie używaj na produkcji) |
| `SCHEDULER_ENABLED` | `true` | Wyłącz przy wielu workerach gunicorn |
