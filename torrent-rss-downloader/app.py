import logging
import os
from datetime import datetime

import bcrypt
from flask import (Flask, render_template, redirect, url_for,
                   request, flash, jsonify, abort)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from flask_wtf.csrf import CSRFProtect

from config import Config
from models import db, User, RssConfig, RssItem, TransmissionServer, Download, WatchPattern

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
csrf = CSRFProtect(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Zaloguj się aby kontynuować.'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('browse'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').encode('utf-8')
        user = User.query.filter_by(username=username).first()
        if user and bcrypt.checkpw(password, user.password_hash.encode('utf-8')):
            login_user(user)
            return redirect(request.args.get('next') or url_for('browse'))
        flash('Błędna nazwa użytkownika lub hasło.', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ── Browse ────────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    return redirect(url_for('browse'))


@app.route('/browse')
@login_required
def browse():
    q        = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()
    language = request.args.get('language', '').strip()
    tag      = request.args.get('tag', '').strip()
    page     = request.args.get('page', 1, type=int)

    query = _build_rss_query(q, category, language, tag)
    pagination = query.paginate(page=page, per_page=25, error_out=False)

    categories = [r[0] for r in db.session.query(RssItem.category)
                  .filter(RssItem.category.isnot(None))
                  .distinct().order_by(RssItem.category).all()]
    languages  = [r[0] for r in db.session.query(RssItem.language)
                  .filter(RssItem.language.isnot(None))
                  .distinct().order_by(RssItem.language).all()]
    servers    = TransmissionServer.query.filter_by(is_active=True).all()

    return render_template('browse.html',
                           pagination=pagination,
                           items=pagination.items,
                           q=q, category=category, language=language, tag=tag,
                           categories=categories, languages=languages,
                           servers=servers)


def _build_rss_query(q, category, language, tag):
    from rss_fetcher import translate_wildcard
    query = RssItem.query.order_by(RssItem.pub_date.desc().nullslast(), RssItem.fetched_at.desc())

    if q:
        pattern = translate_wildcard(q)
        query = query.filter(RssItem.title.ilike(pattern, escape='\\'))
    if category:
        query = query.filter(RssItem.category == category)
    if language:
        query = query.filter(RssItem.language == language)
    if tag:
        query = query.filter(RssItem.tags.ilike(f'%{tag}%'))

    return query


# ── Queue ─────────────────────────────────────────────────────────────────────

@app.route('/queue')
@login_required
def queue():
    downloads = (Download.query
                 .order_by(Download.added_at.desc())
                 .limit(200).all())
    return render_template('queue.html', downloads=downloads)


# ── Settings ──────────────────────────────────────────────────────────────────

@app.route('/settings')
@login_required
def settings():
    rss_config = RssConfig.query.first()
    servers    = TransmissionServer.query.order_by(TransmissionServer.name).all()
    patterns   = WatchPattern.query.order_by(WatchPattern.id).all()
    return render_template('settings.html', rss_config=rss_config,
                           servers=servers, patterns=patterns)


@app.route('/settings/rss', methods=['POST'])
@login_required
def settings_rss():
    feed_url             = request.form.get('feed_url', '').strip()
    poll_interval        = int(request.form.get('poll_interval', 15))
    poll_interval        = max(1, min(poll_interval, 1440))
    default_download_dir = request.form.get('default_download_dir', '').strip() or None
    torrent_client       = request.form.get('torrent_client', 'transmission')
    if torrent_client not in ('transmission', 'wget', 'auto'):
        torrent_client = 'transmission'

    config = RssConfig.query.first()
    if not config:
        config = RssConfig()
        db.session.add(config)
    config.feed_url             = feed_url
    config.poll_interval        = poll_interval
    config.default_download_dir = default_download_dir
    config.torrent_client       = torrent_client
    config.updated_at           = datetime.utcnow()
    db.session.commit()

    try:
        from scheduler import reschedule_rss
        reschedule_rss(poll_interval)
    except Exception:
        pass

    flash('Konfiguracja RSS zapisana.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/password', methods=['POST'])
@login_required
def settings_password():
    current_pw = request.form.get('current_password', '').encode('utf-8')
    new_pw     = request.form.get('new_password', '').encode('utf-8')
    confirm_pw = request.form.get('confirm_password', '').encode('utf-8')

    if not bcrypt.checkpw(current_pw, current_user.password_hash.encode('utf-8')):
        flash('Obecne hasło jest nieprawidłowe.', 'danger')
        return redirect(url_for('settings'))
    if new_pw != confirm_pw:
        flash('Nowe hasła nie są identyczne.', 'danger')
        return redirect(url_for('settings'))
    if len(new_pw) < 6:
        flash('Hasło musi mieć co najmniej 6 znaków.', 'danger')
        return redirect(url_for('settings'))

    current_user.password_hash = bcrypt.hashpw(new_pw, bcrypt.gensalt()).decode('utf-8')
    db.session.commit()
    flash('Hasło zostało zmienione.', 'success')
    return redirect(url_for('settings'))


# ── Wzorce auto-pobierania ────────────────────────────────────────────────────

@app.route('/settings/patterns/add', methods=['POST'])
@login_required
def pattern_add():
    pattern   = request.form.get('pattern', '').strip()
    dest_dir  = request.form.get('dest_dir', '').strip() or None
    server_id = request.form.get('server_id', type=int) or None
    if not pattern:
        flash('Wzorzec nie może być pusty.', 'danger')
        return redirect(url_for('settings'))
    if server_id and not TransmissionServer.query.get(server_id):
        server_id = None
    p = WatchPattern(pattern=pattern, dest_dir=dest_dir, server_id=server_id, is_active=True)
    db.session.add(p)
    db.session.commit()
    flash(f'Wzorzec „{pattern}" dodany.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/patterns/<int:pid>/edit', methods=['POST'])
@login_required
def pattern_edit(pid):
    p = WatchPattern.query.get_or_404(pid)
    pattern   = request.form.get('pattern', '').strip()
    dest_dir  = request.form.get('dest_dir', '').strip() or None
    server_id = request.form.get('server_id', type=int) or None
    if not pattern:
        flash('Wzorzec nie może być pusty.', 'danger')
        return redirect(url_for('settings'))
    if server_id and not TransmissionServer.query.get(server_id):
        server_id = None
    p.pattern   = pattern
    p.dest_dir  = dest_dir
    p.server_id = server_id
    db.session.commit()
    flash(f'Wzorzec „{pattern}" zaktualizowany.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/patterns/<int:pid>/delete', methods=['POST'])
@login_required
def pattern_delete(pid):
    p = WatchPattern.query.get_or_404(pid)
    name = p.pattern
    db.session.delete(p)
    db.session.commit()
    flash(f'Wzorzec „{name}" usunięty.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/patterns/<int:pid>/toggle', methods=['POST'])
@login_required
def pattern_toggle(pid):
    p = WatchPattern.query.get_or_404(pid)
    p.is_active = not p.is_active
    db.session.commit()
    state = 'włączony' if p.is_active else 'wyłączony'
    flash(f'Wzorzec „{p.pattern}" {state}.', 'success')
    return redirect(url_for('settings'))


# ── Transmission server CRUD ──────────────────────────────────────────────────

@app.route('/settings/servers/add', methods=['POST'])
@login_required
def server_add():
    server = TransmissionServer(
        name      = request.form.get('name', '').strip(),
        host      = request.form.get('host', '').strip(),
        port      = int(request.form.get('port', 9091)),
        username  = request.form.get('username', '').strip() or None,
        password  = request.form.get('password', '').strip() or None,
        base_path = request.form.get('base_path', '/transmission/rpc').strip(),
        is_active = True,
    )
    db.session.add(server)
    db.session.commit()
    flash(f'Serwer „{server.name}" dodany.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/servers/<int:sid>/edit', methods=['POST'])
@login_required
def server_edit(sid):
    server = TransmissionServer.query.get_or_404(sid)
    server.name      = request.form.get('name', '').strip()
    server.host      = request.form.get('host', '').strip()
    server.port      = int(request.form.get('port', 9091))
    server.username  = request.form.get('username', '').strip() or None
    server.password  = request.form.get('password', '').strip() or None
    server.base_path = request.form.get('base_path', '/transmission/rpc').strip()
    server.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f'Serwer „{server.name}" zaktualizowany.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/servers/<int:sid>/delete', methods=['POST'])
@login_required
def server_delete(sid):
    server = TransmissionServer.query.get_or_404(sid)
    name = server.name
    db.session.delete(server)
    db.session.commit()
    flash(f'Serwer „{name}" usunięty.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/servers/<int:sid>/toggle', methods=['POST'])
@login_required
def server_toggle(sid):
    server = TransmissionServer.query.get_or_404(sid)
    server.is_active = not server.is_active
    server.updated_at = datetime.utcnow()
    db.session.commit()
    state = 'włączony' if server.is_active else 'wyłączony'
    flash(f'Serwer „{server.name}" {state}.', 'success')
    return redirect(url_for('settings'))


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route('/api/download', methods=['POST'])
@login_required
def api_download():
    data      = request.get_json(force=True, silent=True) or {}
    item_id   = data.get('rss_item_id')
    server_id = data.get('server_id')

    item   = RssItem.query.get(item_id)
    server = TransmissionServer.query.get(server_id)
    if not item or not server or not server.is_active:
        return jsonify(ok=False, error='Nieprawidłowy element lub serwer'), 400

    download = Download(
        rss_item_id=item.id,
        server_id=server.id,
        status=Download.STATUS_PENDING,
        added_by=current_user.id,
    )
    db.session.add(download)
    db.session.commit()

    try:
        import rss_fetcher, transmission_api
        torrent_bytes = rss_fetcher.download_torrent_file(item.torrent_url)
        client        = transmission_api.get_client(server)
        t_id, t_hash  = transmission_api.add_torrent_from_bytes(client, torrent_bytes)

        download.transmission_id   = t_id
        download.transmission_hash = t_hash
        download.status            = Download.STATUS_DOWNLOADING
        download.updated_at        = datetime.utcnow()
        db.session.commit()
        return jsonify(ok=True, download_id=download.id)

    except Exception as e:
        logger.error('Błąd pobierania torrenta %d: %s', item.id, e)
        download.status        = Download.STATUS_ERROR
        download.error_message = str(e)
        download.updated_at    = datetime.utcnow()
        db.session.commit()
        return jsonify(ok=False, error=str(e)), 500


@app.route('/api/downloads/<int:did>/status')
@login_required
def api_download_status(did):
    d = Download.query.get_or_404(did)
    return jsonify(
        id=d.id,
        status=d.status,
        status_label=d.status_label(),
        badge_class=d.status_badge_class(),
        progress=d.progress,
        error=d.error_message,
    )


@app.route('/api/servers/<int:sid>/test', methods=['POST'])
@login_required
def api_server_test(sid):
    server = TransmissionServer.query.get_or_404(sid)
    import transmission_api
    ok, message = transmission_api.test_connection(server)
    return jsonify(ok=ok, message=message)


@app.route('/api/rss/poll-now', methods=['POST'])
@login_required
def api_rss_poll_now():
    try:
        from rss_fetcher import fetch_and_store, auto_download_matching
        counts = fetch_and_store(app)
        auto   = auto_download_matching(app)
        return jsonify(ok=True, auto_downloaded=auto, **counts)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route('/api/filter-options')
@login_required
def api_filter_options():
    categories = [r[0] for r in db.session.query(RssItem.category)
                  .filter(RssItem.category.isnot(None)).distinct().all()]
    languages  = [r[0] for r in db.session.query(RssItem.language)
                  .filter(RssItem.language.isnot(None)).distinct().all()]
    servers    = [{'id': s.id, 'name': s.name}
                  for s in TransmissionServer.query.filter_by(is_active=True).all()]
    return jsonify(categories=sorted(categories), languages=sorted(languages), servers=servers)


# ── Init i migracja DB ────────────────────────────────────────────────────────

def _create_default_admin():
    if not User.query.filter_by(username='admin').first():
        pw_hash = bcrypt.hashpw(b'admin', bcrypt.gensalt()).decode('utf-8')
        db.session.add(User(username='admin', password_hash=pw_hash))
        db.session.commit()
        logger.info('Utworzono domyślne konto: admin / admin')


def _seed_rss_config():
    if not RssConfig.query.first():
        feed_url = app.config.get('RSS_FEED_URL', '')
        interval = app.config.get('RSS_POLL_INTERVAL', 15)
        db.session.add(RssConfig(feed_url=feed_url, poll_interval=interval))
        db.session.commit()


def _migrate_db():
    """Migruje istniejącą bazę SQLite — dodaje brakujące kolumny i aktualizuje schemat."""
    from sqlalchemy import text

    def _get_cols(conn, table):
        rows = conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
        return {row[1]: row for row in rows}  # name → (cid, name, type, notnull, dflt, pk)

    with db.engine.begin() as conn:
        tables = {r[0] for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}

        # rss_config: nowe kolumny
        if 'rss_config' in tables:
            cols = _get_cols(conn, 'rss_config')
            if 'default_download_dir' not in cols:
                conn.execute(text(
                    'ALTER TABLE rss_config ADD COLUMN default_download_dir VARCHAR(500)'))
                logger.info('DB migr: rss_config.default_download_dir')
            if 'torrent_client' not in cols:
                conn.execute(text(
                    "ALTER TABLE rss_config ADD COLUMN torrent_client VARCHAR(50) DEFAULT 'transmission'"))
                logger.info('DB migr: rss_config.torrent_client')

        # watch_patterns: nowa kolumna server_id
        if 'watch_patterns' in tables:
            cols = _get_cols(conn, 'watch_patterns')
            if 'server_id' not in cols:
                conn.execute(text(
                    'ALTER TABLE watch_patterns ADD COLUMN server_id INTEGER REFERENCES transmission_servers(id)'))
                logger.info('DB migr: watch_patterns.server_id')

        # downloads: server_id musi być nullable + nowe kolumny
        if 'downloads' in tables:
            cols = _get_cols(conn, 'downloads')
            server_notnull = cols.get('server_id', (None, None, None, 0))[3]
            need_save_path = 'save_path' not in cols
            need_auto_dl   = 'auto_downloaded' not in cols

            if server_notnull or need_save_path or need_auto_dl:
                logger.info('DB migr: przebudowa tabeli downloads')
                conn.execute(text("""
                    CREATE TABLE downloads_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        rss_item_id INTEGER NOT NULL REFERENCES rss_items(id),
                        server_id INTEGER REFERENCES transmission_servers(id),
                        transmission_id INTEGER,
                        transmission_hash VARCHAR(64),
                        status VARCHAR(32) NOT NULL DEFAULT 'pending',
                        error_message TEXT,
                        progress FLOAT NOT NULL DEFAULT 0.0,
                        save_path VARCHAR(500),
                        auto_downloaded BOOLEAN NOT NULL DEFAULT 0,
                        added_at DATETIME,
                        updated_at DATETIME,
                        completed_at DATETIME,
                        added_by INTEGER REFERENCES users(id)
                    )
                """))
                known = {'id', 'rss_item_id', 'server_id', 'transmission_id',
                         'transmission_hash', 'status', 'error_message', 'progress',
                         'added_at', 'updated_at', 'completed_at', 'added_by'}
                copy_cols = ', '.join(sorted(set(cols.keys()) & known))
                conn.execute(text(
                    f'INSERT INTO downloads_new ({copy_cols}) SELECT {copy_cols} FROM downloads'))
                conn.execute(text('DROP TABLE downloads'))
                conn.execute(text('ALTER TABLE downloads_new RENAME TO downloads'))
                logger.info('DB migr: tabela downloads zaktualizowana')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _migrate_db()
        _create_default_admin()
        _seed_rss_config()

        if app.config.get('SCHEDULER_ENABLED', True):
            from scheduler import init_scheduler
            init_scheduler(app)

    port  = app.config.get('FLASK_PORT', 5000)
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug, use_reloader=False)
