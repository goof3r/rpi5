import logging
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

import feedparser
import requests

logger = logging.getLogger(__name__)


# ─── RSS polling ────────────────────────────────────────────────────────────

def fetch_and_store(app) -> dict:
    """Główna funkcja pollingu RSS — wywołana przez scheduler."""
    from datetime import timedelta
    with app.app_context():
        from models import db, RssConfig

        feeds = RssConfig.query.filter_by(is_active=True).all()
        if not feeds:
            logger.warning('Brak aktywnych kanałów RSS — pomijam polling')
            return {'new': 0, 'skipped': 0, 'errors': 0}

        now = datetime.utcnow()
        total = {'new': 0, 'skipped': 0, 'errors': 0}

        for config in feeds:
            if not config.feed_url:
                continue
            if config.last_fetched:
                if now < config.last_fetched + timedelta(minutes=config.poll_interval):
                    continue

            label = config.name or config.feed_url
            counts = {'new': 0, 'skipped': 0, 'errors': 0}
            try:
                entries = _fetch_feed(config.feed_url)
            except Exception as e:
                logger.error('Błąd pobierania RSS [%s]: %s', label, e)
                counts['errors'] += 1
                for k in total:
                    total[k] += counts[k]
                continue

            for entry in entries:
                try:
                    result = _upsert_item(entry, source=label)
                    counts[result] += 1
                except Exception as e:
                    logger.error('Błąd zapisu wpisu RSS "%s": %s', entry.get('title', '?'), e)
                    counts['errors'] += 1
                    db.session.rollback()

            try:
                config.last_fetched = now
                db.session.commit()
            except Exception as e:
                logger.error('Błąd commit RSS [%s]: %s', label, e)
                db.session.rollback()

            logger.info('RSS poll [%s]: nowe=%d, pominięte=%d, błędy=%d',
                        label, counts['new'], counts['skipped'], counts['errors'])
            for k in total:
                total[k] += counts[k]

        return total


def _fetch_feed(url: str) -> list:
    resp = requests.get(url, timeout=30, headers={'User-Agent': 'TorrentRSSDownloader/1.0'})
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)

    entries = []
    for e in feed.entries:
        torrent_url = e.get('link', '')
        if not torrent_url:
            continue

        pub_date = None
        if e.get('published'):
            try:
                pub_date = parsedate_to_datetime(e.published).replace(tzinfo=None)
            except Exception:
                pass

        size_str = _get_extra(e, 'size')
        language = _get_extra(e, 'language')
        tags_raw = _get_extra(e, 'tags')

        category = e.get('category', '')
        if not category and hasattr(e, 'tags') and e.tags:
            category = e.tags[0].get('term', '')

        entries.append({
            'guid':        torrent_url,
            'title':       e.get('title', '').strip(),
            'category':    category.strip() if category else '',
            'pub_date':    pub_date,
            'description': e.get('summary', '').strip(),
            'size_str':    size_str,
            'language':    language,
            'tags':        tags_raw,
            'torrent_url': torrent_url,
        })

    return entries


def _get_extra(entry, field: str):
    val = entry.get(field)
    if val:
        return str(val).strip()
    for key in (field, f'rss_{field}', f'media_{field}'):
        val = getattr(entry, key, None)
        if val:
            return str(val).strip()
    return None


def _upsert_item(entry: dict, source: str = None) -> str:
    from models import db, RssItem

    existing = RssItem.query.filter_by(guid=entry['guid']).first()
    if existing:
        return 'skipped'

    item = RssItem(
        guid=entry['guid'],
        title=entry['title'],
        source=source or None,
        category=entry.get('category') or None,
        pub_date=entry.get('pub_date'),
        description=entry.get('description') or None,
        size_str=entry.get('size_str') or None,
        language=entry.get('language') or None,
        tags=entry.get('tags') or None,
        torrent_url=entry['torrent_url'],
    )
    db.session.add(item)
    db.session.flush()
    return 'new'


# ─── Auto-pobieranie wg wzorców ──────────────────────────────────────────────

def pattern_to_regex(pattern: str) -> str:
    """Zamienia wzorzec z % na wyrażenie regularne.

    % → dowolna liczba dowolnych znaków (jak SQL LIKE lub shell *).
    Dzięki temu np. Zuzel.2026.05.1% dopasuje każdy tytuł zaczynający
    się od tego prefiksu, niezależnie od długości reszty nazwy.
    """
    escaped = re.escape(pattern)
    escaped = escaped.replace('%', '.*')
    return f'^{escaped}$'


def auto_download_matching(app) -> int:
    """Sprawdza nowe wstawki RSS względem aktywnych wzorców i automatycznie pobiera pasujące.

    Każda wstawka sprawdzana jest przeciwko wzorcom po kolei; po pierwszym
    dopasowaniu pobieranie jest uruchamiane i pozostałe wzorce są pomijane
    (tak jak w skrypcie bash — break po pierwszym trafieniu).
    """
    with app.app_context():
        from models import db, RssConfig, RssItem, WatchPattern, Download

        config = RssConfig.query.filter_by(is_active=True).first()
        if not config:
            return 0

        patterns = WatchPattern.query.filter_by(is_active=True).order_by(WatchPattern.id).all()
        if not patterns:
            return 0

        # Wstawki, które mają już jakiekolwiek pobranie (manualne lub auto)
        already_ids = {
            row[0]
            for row in db.session.query(Download.rss_item_id).all()
            if row[0] is not None
        }

        if already_ids:
            items = RssItem.query.filter(~RssItem.id.in_(already_ids)).all()
        else:
            items = RssItem.query.all()

        downloaded = 0
        for item in items:
            for pat in patterns:
                regex = pattern_to_regex(pat.pattern)
                if re.match(regex, item.title, re.IGNORECASE):
                    dest = (pat.dest_dir
                            or config.default_download_dir
                            or os.path.expanduser('~/Downloads/torrents'))
                    logger.info('Auto-dopasowanie: "%s" → wzorzec "%s"', item.title, pat.pattern)
                    _do_auto_download(item, dest, config, pat.server_id)
                    downloaded += 1
                    break  # nie sprawdzaj kolejnych wzorców dla tej wstawki

        if downloaded:
            logger.info('Auto-pobieranie: zainicjowano %d pobrań', downloaded)
        return downloaded


def _do_auto_download(item, dest_dir: str, config, pattern_server_id=None) -> None:
    """Wysyła wstawkę do odpowiedniego klienta torrent.

    Jeśli wzorzec ma przypisany serwer (pattern_server_id), używa go
    w pierwszej kolejności; w razie braku lub wyłączenia serwera
    wraca do pierwszego aktywnego serwera.
    """
    from models import TransmissionServer

    client_type = (config.torrent_client or 'transmission').lower()

    if client_type in ('wget', 'file'):
        _auto_download_to_file(item, dest_dir)
        return

    # Wybór serwera: przypisany do wzorca → pierwszy aktywny
    server = None
    if pattern_server_id:
        server = TransmissionServer.query.get(pattern_server_id)
        if server and not server.is_active:
            logger.warning('Serwer przypisany do wzorca jest wyłączony — szukam aktywnego')
            server = None
    if server is None:
        server = TransmissionServer.query.filter_by(is_active=True).first()

    if client_type == 'auto':
        if server:
            _auto_download_to_transmission(item, server)
        else:
            logger.info('Auto (brak Transmission) → zapis pliku: %s', item.title)
            _auto_download_to_file(item, dest_dir)
    else:
        # transmission (domyślny)
        if not server:
            logger.warning('Auto-pobieranie: brak aktywnego serwera Transmission dla "%s"', item.title)
            return
        _auto_download_to_transmission(item, server)


def _auto_download_to_file(item, dest_dir: str) -> None:
    """Pobiera plik .torrent i zapisuje go na dysk."""
    from models import db, Download

    d = Download(
        rss_item_id=item.id,
        server_id=None,
        status=Download.STATUS_PENDING,
        auto_downloaded=True,
    )
    db.session.add(d)
    db.session.flush()

    try:
        os.makedirs(dest_dir, exist_ok=True)
        torrent_bytes = download_torrent_file(item.torrent_url)
        save_path = _save_torrent_file(torrent_bytes, dest_dir, item.title)
        d.status       = Download.STATUS_SAVED
        d.progress     = 100.0
        d.save_path    = save_path
        d.completed_at = datetime.utcnow()
        d.updated_at   = datetime.utcnow()
        db.session.commit()
        logger.info('Auto-zapisano plik: %s → %s', item.title, save_path)
    except Exception as e:
        logger.error('Błąd auto-pobierania (plik) "%s": %s', item.title, e)
        d.status        = Download.STATUS_ERROR
        d.error_message = str(e)
        d.updated_at    = datetime.utcnow()
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()


def _auto_download_to_transmission(item, server) -> None:
    """Wysyła torrent do serwera Transmission."""
    from models import db, Download
    import transmission_api

    d = Download(
        rss_item_id=item.id,
        server_id=server.id,
        status=Download.STATUS_PENDING,
        auto_downloaded=True,
    )
    db.session.add(d)
    db.session.flush()

    try:
        torrent_bytes       = download_torrent_file(item.torrent_url)
        client              = transmission_api.get_client(server)
        t_id, t_hash        = transmission_api.add_torrent_from_bytes(client, torrent_bytes)
        d.transmission_id   = t_id
        d.transmission_hash = t_hash
        d.status            = Download.STATUS_DOWNLOADING
        d.updated_at        = datetime.utcnow()
        db.session.commit()
        logger.info('Auto-dodano do Transmission (%s): %s', server.name, item.title)
    except Exception as e:
        logger.error('Błąd auto-pobierania (Transmission) "%s": %s', item.title, e)
        d.status        = Download.STATUS_ERROR
        d.error_message = str(e)
        d.updated_at    = datetime.utcnow()
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()


def _save_torrent_file(torrent_bytes: bytes, dest_dir: str, title: str) -> str:
    """Zapisuje bytes torrenta do pliku. Zwraca pełną ścieżkę."""
    fname = re.sub(r'[<>:"|?*\\/]', '_', title).replace(' ', '_') + '.torrent'
    path = os.path.join(dest_dir, fname)
    with open(path, 'wb') as f:
        f.write(torrent_bytes)
    return path


# ─── Pomocnicze ─────────────────────────────────────────────────────────────

def download_torrent_file(url: str, timeout: int = 30) -> bytes:
    """Pobiera plik .torrent jako bytes."""
    resp = requests.get(url, timeout=timeout, headers={'User-Agent': 'TorrentRSSDownloader/1.0'})
    resp.raise_for_status()
    return resp.content


def translate_wildcard(q: str) -> str:
    """Konwertuje wyszukiwanie użytkownika (z %%) na wzorzec SQL LIKE."""
    s = q.replace('_', r'\_')
    s = s.replace('%%', '\x00')
    s = s.replace('%', '\x00')
    return s.replace('\x00', '%')
