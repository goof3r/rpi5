import atexit
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(
    daemon=True,
    job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 60},
)


def init_scheduler(app):
    from models import RssConfig
    from config import Config

    with app.app_context():
        config = RssConfig.query.first()
        interval = config.poll_interval if config else Config.RSS_POLL_INTERVAL

    scheduler.add_job(
        func=_rss_poll_job,
        trigger=IntervalTrigger(minutes=interval),
        id='rss_poll',
        args=[app],
        replace_existing=True,
    )
    scheduler.add_job(
        func=_sync_downloads_job,
        trigger=IntervalTrigger(minutes=2),
        id='download_sync',
        args=[app],
        replace_existing=True,
    )

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))
    logger.info('Scheduler uruchomiony (RSS co %d min, sync pobrań co 2 min)', interval)


def reschedule_rss(minutes: int):
    scheduler.reschedule_job('rss_poll', trigger=IntervalTrigger(minutes=minutes))
    logger.info('RSS poll zmieniony na co %d min', minutes)


def _rss_poll_job(app):
    try:
        from rss_fetcher import fetch_and_store, auto_download_matching
        fetch_and_store(app)
        auto_download_matching(app)
    except Exception as e:
        logger.error('Błąd w zadaniu RSS poll: %s', e)


def _sync_downloads_job(app):
    try:
        sync_all_downloads(app)
    except Exception as e:
        logger.error('Błąd w zadaniu sync downloads: %s', e)


def sync_all_downloads(app):
    """Odpytuje Transmission o status aktywnych pobrań i aktualizuje DB."""
    with app.app_context():
        from datetime import datetime
        from models import db, Download, TransmissionServer
        import transmission_api

        active = Download.query.filter(
            Download.status.in_([Download.STATUS_PENDING, Download.STATUS_DOWNLOADING, Download.STATUS_SEEDING])
        ).filter(Download.transmission_id.isnot(None)).all()

        if not active:
            return

        # Grupuj po server_id
        by_server: dict[int, list] = {}
        for d in active:
            by_server.setdefault(d.server_id, []).append(d)

        for server_id, downloads in by_server.items():
            server = TransmissionServer.query.get(server_id)
            if not server or not server.is_active:
                continue
            try:
                client = transmission_api.get_client(server)
            except Exception as e:
                logger.warning('Nie można połączyć z serwerem %s: %s', server.name, e)
                continue

            for d in downloads:
                try:
                    info = transmission_api.get_torrent_status(client, d.transmission_id)
                    d.status   = info['status']
                    d.progress = info['progress']
                    if info['error']:
                        d.error_message = info['error']
                    if d.status == Download.STATUS_COMPLETED and not d.completed_at:
                        d.completed_at = datetime.utcnow()
                    d.updated_at = datetime.utcnow()
                except Exception as e:
                    logger.warning('Błąd statusu pobrania %d: %s', d.id, e)
                    d.status = Download.STATUS_ERROR
                    d.error_message = str(e)
                    d.updated_at = datetime.utcnow()

            try:
                db.session.commit()
            except Exception as e:
                logger.error('Błąd commit sync dla serwera %s: %s', server.name, e)
                db.session.rollback()
