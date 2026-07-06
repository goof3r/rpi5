# Transmission Notifier

Wtyczka do Chrome (Manifest V3), ktora:

- obsluguje **wiele serwerow Transmission** rownoczesnie,
- pokazuje aktualnie pobierane / ukonczone torrenty kazdego serwera,
- pozwala dodawac **magnetlinki** i pliki **.torrent** na wybrany serwer,
- co minute pyta kazdy serwer w tle i pokazuje **systemowe powiadomienie** w rogu ekranu po zakonczeniu pobierania,
- dodaje **menu kontekstowe** "Wyslij do Transmission" po prawym kliknieciu na linku (magnet lub `.torrent`).

## Instalacja (tryb developera)

1. Otworz Chrome i wejdz na `chrome://extensions/`.
2. Wlacz **Tryb dewelopera** (przelacznik w prawym gornym rogu).
3. Kliknij **Wczytaj rozpakowane** i wskaz katalog `wtyczka chrome`.
4. Po instalacji kliknij ikone wtyczki, a potem **kolko zebate** (lub `Szczegoly -> Opcje rozszerzenia`) zeby dodac pierwszy serwer.

## Dodawanie serwera

W ustawieniach podajesz:

- **Nazwa** - dowolna etykieta (pojawi sie w popupie i w powiadomieniach).
- **URL** - np. `http://192.168.1.10:9091` (bez `/transmission/rpc` - wtyczka dokleja sama).
- **Login / Haslo** - jesli serwer ma wlaczone uwierzytelnianie.

Po `Zapisz` przegladarka zapyta o **zgode na dostep do tego origin'u** (`http://192.168.1.10:9091/*`). Bez tej zgody Chrome zablokuje zadania `fetch` do RPC.

Przycisk **Testuj polaczenie** wywoluje `session-get` na RPC i potwierdza, ze wszystko gra.

## Uzywanie

- **Popup** (ikona w pasku) - lista torrentow z aktywnego serwera, pasek postepu, predkosci, ETA, przyciski `Pauza/Wznow`, `Usun`, `Usun + pliki`.
- **Pole "magnet:?... lub URL pliku .torrent"** - wkleja link na biezacy serwer; obok przycisk **Plik...** wysyla `.torrent` z dysku.
- **Selektor serwera** u gory popupu przelacza widok i zapamietuje ostatni wybor.
- **Prawy klik** na linku (magnet lub `.torrent`) na dowolnej stronie -> `Wyslij do Transmission -> <nazwa serwera>`.
- **Badge na ikonie** pokazuje liczbe aktywnie pobieranych torrentow (laczne dla wszystkich serwerow).
- **Powiadomienie systemowe** ("chmurka" w rogu ekranu wg ustawien Windows/Mac) pojawia sie raz, gdy torrent skonczy pobieranie. Klikniecie otwiera webowy UI Transmission.
- **Dwuklik na torrencie** otwiera dane w udziale sieciowym - URL skladany jest z pol `Bazowy URL udzialu` + `(downloadDir - prefiks)` + `nazwa torrenta`. Obsluga formatow:
  - `smb://nas/torrents` (wymaga handlera SMB w systemie, np. Windows lub aplikacji)
  - `file://///nas/torrents` (Windows: cztery slashe oznaczaja UNC `\\nas\torrents`)
  - `http://nas/files/torrents` (webowy file manager - dziala najlepiej w Chrome)

## Jak to dziala technicznie

- Service worker (`background.js`) trzyma `chrome.alarms` z interwalem 60 s i odpytuje `torrent-get` na kazdym serwerze.
- Stan widzianych ukonczen (`seenCompletions`) jest w `chrome.storage.local`, zeby nie spamowac powiadomieniami za kazdym sprawdzeniem.
- Handshake CSRF Transmission (`X-Transmission-Session-Id`) jest obslugiwany automatycznie: po 409 zapamietujemy sessionId per serwer.
- Uprawnienia do hostow sa **opcjonalne** (`optional_host_permissions`) i przyznawane per origin przy dodaniu serwera, nie globalnie.

## Struktura plikow

```
wtyczka chrome/
  manifest.json
  background.js          # service worker - pollowanie + powiadomienia + menu
  popup.html/css/js      # podglad torrentow + szybki dodaj
  options.html/css/js    # zarzadzanie serwerami
  lib/
    transmission.js      # klient RPC
    storage.js           # serwery, seen, ostatnio wybrany
  icons/
    icon16.png icon48.png icon128.png
```

## Import / eksport konfiguracji

W ustawieniach na gorze sa przyciski **Eksportuj konfiguracje** i **Importuj konfiguracje**.

- **Eksport** pobiera plik `transmission-notifier-YYYY-MM-DD.json` ze wszystkimi serwerami (URL, login, haslo, samba, prefiks). **Uwaga: hasla zapisane sa jawnym tekstem** - przechowuj plik bezpiecznie (zaszyfrowany dysk, menedzer hasel itp.).
- **Import** przyjmuje plik JSON o tej samej strukturze. Jezeli masz juz jakies serwery, pojawi sie pytanie:
  - **OK** - zastapic wszystkie biezace,
  - **Anuluj** - scalic po `id` (istniejace nadpisane, nowe dodane).
- Po imporcie Chrome poprosi jednorazowo o zgody na hosty wszystkich importowanych serwerow.

Format pliku:

```json
{
  "app": "transmission-notifier",
  "version": 1,
  "exportedAt": "2026-06-27T12:00:00.000Z",
  "servers": [
    {
      "id": "s_abc123",
      "name": "Domowy NAS",
      "url": "http://192.168.1.10:9091",
      "username": "user",
      "password": "secret",
      "sambaBase": "smb://192.168.1.10/torrents",
      "dlPathPrefix": "/mnt/storage/torrents"
    }
  ]
}
```

## Wskazowki / czesto zadawane

- **HTTPS z self-signed cert** - musi byc zaakceptowany w przegladarce (jednorazowo otworz adres w karcie i przyjmij wyjatek), inaczej `fetch` z service workera zostanie zablokowany.
- **Inny port niz 9091** - po prostu wpisz pelny URL z portem: `http://nas.lan:51234`.
- **Custom `download-dir`** - aktualnie wtyczka uzywa katalogu domyslnego serwera. Mozna to latwo rozszerzyc w `popup.js` (`addTorrentUrl(server, v, { downloadDir: '...' })`).
- **Powiadomienia nie sa wyswietlane** - sprawdz `Ustawienia systemu -> Powiadomienia -> Google Chrome` (Windows: Action Center > Focus Assist).

## Licencja

Do uzytku wlasnego. Brak gwarancji.
