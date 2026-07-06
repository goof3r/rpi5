# aibot — minqlx + Claude API

Plugin dla serwera Quake Live pod **minqlx**, który integruje bota AI
(Anthropic Claude) w czacie serwera. Trzy funkcje w jednym pliku:

1. **`!ai <pytanie>`** — konwersacyjny czat z pamięcią per gracz.
2. **Q&A o serwerze** — bot zna Twój regulamin/tryby/adminów z pliku
   `aibot_context.txt` i odpowiada na pytania nowych graczy.
3. **Smart onjoin** — jednozdaniowe powitanie generowane pod gracza
   (uwzględnia „pierwszy raz" vs „stały bywalec", poziom uprawnień itd.).

Zero zewnętrznych zależności — tylko `stdlib` (`urllib`, `json`).

---

## Instalacja

### 1. Skopiuj plik pluginu na serwer

```bash
# na serwerze QL
cp aibot.py ~/.quakelive/minqlx-plugins/
cp aibot_context.example.txt ~/.quakelive/minqlx-plugins/aibot_context.txt
```

Ścieżkę dopasuj do swojej instalacji (u Ciebie może być np.
`/home/steam/steamcmd/steamapps/common/qlds/minqlx-plugins/`).

### 2. Uzupełnij kontekst serwera

Edytuj `aibot_context.txt` — wpisz nazwę serwera, zasady, adminów, tryby.
To trafia do system-promptu bota (z **prompt cachingiem** — płacisz raz,
potem 10% ceny za każdy hit), więc może być długi.

### 3. Klucz API

Preferowany sposób — zmienna środowiskowa dla procesu QL:

```bash
# np. w /etc/systemd/system/qlserver-ffa.service (Environment=)
Environment="ANTHROPIC_API_KEY=sk-ant-..."
```

Alternatywa (mniej bezpieczna — klucz w cfg):

```
set qlx_aiBotApiKey "sk-ant-..."
```

Klucze bierzesz z https://console.anthropic.com/

### 4. Włącz w server.cfg

Dopisz `aibot` do `qlx_plugins` — **przed** `permoverride`
(bo permoverride ma być ostatni), po `permission`:

```
set qlx_plugins "plugin_manager, essentials, motd, permission, aibot, ..., serverhelp, permoverride"
```

Restart serwera albo z konsoli / RCON:

```
!load aibot
```

### 5. Test

W grze:

```
!ai jakie mamy tryby?
!ai jak zostać adminem?
!ai_stats
```

W konsoli serwera powinieneś zobaczyć log per zapytanie:

```
[aibot] goof3r (76561198...) tokens in=1234 out=87 cr=1100 cw=0 usd=0.0021
```

---

## Konfiguracja (cvary)

| cvar | domyślnie | opis |
| --- | --- | --- |
| `qlx_aiBotEnabled` | `1` | `0` wyłącza cały plugin bez unload |
| `qlx_aiBotApiKey` | `""` | Klucz API (fallback jeśli brak ENV) |
| `qlx_aiBotModel` | `claude-haiku-4-5-20251001` | Model — patrz niżej |
| `qlx_aiBotMaxTokens` | `400` | Limit outputu (im mniej, tym taniej) |
| `qlx_aiBotCooldownSec` | `15` | Cooldown per SteamID |
| `qlx_aiBotGlobalCooldownSec` | `3` | Anti-spam globalny |
| `qlx_aiBotHistoryTurns` | `3` | Ile ostatnich wymian pamięta bot |
| `qlx_aiBotSmartOnjoin` | `1` | Powitanie AI on-connect |
| `qlx_aiBotDailyBudgetUSD` | `1.00` | Soft cap dzienny w USD (`0` = bez capa) |
| `qlx_aiBotContextFile` | `""` | Ścieżka do kontekstu (domyślnie obok pluginu) |
| `qlx_aiBotPricingIn/Out/CacheRead/CacheWrite` | z tabeli | Nadpisz cennik ręcznie (USD za 1M) |

### Wybór modelu

| model | wejście / 1M | wyjście / 1M | kiedy używać |
| --- | --- | --- | --- |
| `claude-haiku-4-5-20251001` | $1 | $5 | **Domyślny** — czat, powitania, Q&A |
| `claude-sonnet-4-6` | $3 | $15 | Jeśli chcesz mądrzejsze odpowiedzi |
| `claude-opus-4-7` | $15 | $75 | Analizy meczów, długie podsumowania |

---

## Komendy w grze

| komenda | perm | opis |
| --- | --- | --- |
| `!ai <pytanie>` | 0 | Zadaj pytanie botowi |
| `!ai_reset` | 0 | Wyczyść własną historię rozmowy |
| `!ai_stats` | 0 | Zużycie tokenów i koszt tej sesji |
| `!ai_reload` | 5 | Przeładuj `aibot_context.txt` bez restartu |
| `!ai_budget` | 5 | Ile już wydano dziś / łącznie |
| `!ai_toggle` | 5 | Włącz/wyłącz plugin bez `unload` |

---

## Bezpieczeństwo i koszty

- **Rate limit** per SteamID + globalny są włączone domyślnie. Bez tego
  jeden zapalony gracz wydałby cały budżet.
- **Soft cap** (`qlx_aiBotDailyBudgetUSD`) blokuje nowe requesty gdy
  dzień przekroczy limit. Nie zabija w środku odpowiedzi.
- **Prompt caching** — cały kontekst serwera + persona idzie z
  `cache_control: ephemeral`, hit-rate blisko 100% przy aktywnej rozmowie.
- **Klucz API w ENV, nie w cfg** — cfg trafia do backupów i logów.
- **Historia w Redisie wygasa po 24h** (klucze `minqlx:aibot:history:*`).

---

## Struktura danych w Redisie

```
minqlx:aibot:history:<sid>          LIST(JSON) — ostatnie wymiany (TTL 24h)
minqlx:aibot:cost:player:<sid>      HASH {in, out, cr, cw, usd}
minqlx:aibot:cost:day:<yyyy-mm-dd>  FLOAT — dzienne wydanie USD (TTL 48h)
minqlx:aibot:cost:total_usd         FLOAT — łączne wydanie
minqlx:aibot:cd:player:<sid>        int — flaga cooldown (TTL = cooldown)
minqlx:aibot:cd:global              int — flaga cooldown globalny
minqlx:aibot:lastjoin:<sid>         int — unix ts poprzedniego wejścia
```

Podgląd wydatków ręcznie:

```bash
# dziś
redis-cli get "minqlx:aibot:cost:day:$(date -u +%F)"

# łącznie
redis-cli get minqlx:aibot:cost:total_usd

# top wydający gracze
for k in $(redis-cli --scan --pattern 'minqlx:aibot:cost:player:*'); do
  usd=$(redis-cli hget "$k" usd)
  echo "$usd  ${k##*:}"
done | sort -rn | head
```

---

## Rozbudowa (co można łatwo dodać)

Plugin jest samowystarczalny, ale całą infrastrukturę (API call,
cooldowny, historia, koszt, cache) możesz reużyć do kolejnych ficzerów
z pierwszej rozmowy:

- **Podsumowanie meczu** — hook `game_end` → `_api_call` z kontekstem
  round-by-round → wypis do wszystkich.
- **`!tip`** — hook na komendę, do promptu doklej statystyki broni
  gracza z Redisa (klucze `minqlx:players:<sid>:...` z `serverBDM`).
- **`!t <tekst>`** — tłumacz. Osobna komenda, ten sam `_api_call`,
  system-prompt jednozdaniowy.
- **Trash-talk po fragach** — hook `death`, mocno rate-limitowany
  (np. max raz na 3 min per gracz), tylko na `EXCELLENT`/`HUMILIATION`.

---

## Licencja

GPL-3.0-or-later (jak reszta minqlx).
