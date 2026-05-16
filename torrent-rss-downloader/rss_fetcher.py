import logging
from datetime import datetime
from email.utils import parsedate_to_datetime

import feedparser
import requests

logger = logging.getLogger(__name__)


def fetch_and_store(app) -> dict:
    """Główna funkcja pollingu RSS — wywołana przez scheduler."""
    with app.app_context():
        from models import db, RssConfig, RssItem

        config = RssConfig.query.first()
        if not config or not config.feed_url:
            logger.warning('Brak URL RSS w konfiguracji — pomijam polling')
            return {'new': 0, 'skipped': 0, 'errors': 0}

        counts = {'new': 0, 'skipped': 0, 'errors': 0}
        try:
            entries = _fetch_feed(config.feed_url)
        except Exception as e:
            logger.error('Błąd pobierania RSS: %s', e)
            counts['errors'] += 1
            return counts

        for entry in entries:
            try:
                result = _upsert_item(entry)
                counts[result] += 1
            except Exception as e:
                logger.error('Błąd zapisu wpisu RSS "%s": %s', entry.get('title', '?'), e)
                counts['errors'] += 1
                db.session.rollback()

        try:
            config.last_fetched = datetime.utcnow()
            db.session.commit()
        except Exception as e:
            logger.error('Błąd commit RSS: %s', e)
            db.session.rollback()

        logger.info('RSS poll: nowe=%d, pominięte=%d, błędy=%d', counts['new'], counts['skipped'], counts['errors'])
        return counts


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

        # feedparser udostępnia niestandardowe pola RSS przez .get() lub jako atrybuty
        size_str  = _get_extra(e, 'size')
        language  = _get_extra(e, 'language')
        tags_raw  = _get_extra(e, 'tags')

        # kategoria może być w e.tags (lista obiektów) lub w e.category
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
    """Pobiera niestandardowe pole RSS z obiektu feedparser entry."""
    val = entry.get(field)
    if val:
        return str(val).strip()
    # feedparser czasem przechowuje pod prefiksowaną nazwą
    for key in (field, f'rss_{field}', f'media_{field}'):
        val = getattr(entry, key, None)
        if val:
            return str(val).strip()
    return None


def _upsert_item(entry: dict) -> str:
    from models import db, RssItem

    existing = RssItem.query.filter_by(guid=entry['guid']).first()
    if existing:
        return 'skipped'

    item = RssItem(
        guid=entry['guid'],
        title=entry['title'],
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


def download_torrent_file(url: str, timeout: int = 30) -> bytes:
    """Pobiera plik .torrent jako bytes."""
    resp = requests.get(url, timeout=timeout, headers={'User-Agent': 'TorrentRSSDownloader/1.0'})
    resp.raise_for_status()
    return resp.content


def translate_wildcard(q: str) -> str:
    """Konwertuje wyszukiwanie użytkownika (z %%) na wzorzec SQL LIKE."""
    s = q.replace('_', r'\_')   # zabezpiecz podkreślnik SQL
    s = s.replace('%%', '\x00')
    s = s.replace('%', '\x00')
    return s.replace('\x00', '%')
