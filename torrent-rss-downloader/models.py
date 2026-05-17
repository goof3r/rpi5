from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    downloads = db.relationship('Download', backref='added_by_user', lazy='dynamic',
                                foreign_keys='Download.added_by')


class RssConfig(db.Model):
    __tablename__ = 'rss_config'

    id                   = db.Column(db.Integer, primary_key=True)
    feed_url             = db.Column(db.String(1024), nullable=False, default='')
    poll_interval        = db.Column(db.Integer, nullable=False, default=15)
    last_fetched         = db.Column(db.DateTime, nullable=True)
    updated_at           = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    default_download_dir = db.Column(db.String(500), nullable=True)
    torrent_client       = db.Column(db.String(50), nullable=False, default='transmission')


class RssItem(db.Model):
    __tablename__ = 'rss_items'

    id          = db.Column(db.Integer, primary_key=True)
    guid        = db.Column(db.String(512), unique=True, nullable=False, index=True)
    title       = db.Column(db.String(512), nullable=False, index=True)
    category    = db.Column(db.String(128), nullable=True, index=True)
    pub_date    = db.Column(db.DateTime, nullable=True)
    description = db.Column(db.Text, nullable=True)
    size_str    = db.Column(db.String(64), nullable=True)
    language    = db.Column(db.String(32), nullable=True, index=True)
    tags        = db.Column(db.String(512), nullable=True)
    torrent_url = db.Column(db.String(1024), nullable=False)
    fetched_at  = db.Column(db.DateTime, default=datetime.utcnow)

    downloads = db.relationship('Download', backref='rss_item', lazy='dynamic')

    def tags_list(self):
        if not self.tags:
            return []
        return [t.strip() for t in self.tags.split(',') if t.strip()]

    def latest_download(self):
        return self.downloads.order_by(Download.added_at.desc()).first()

    def download_status(self):
        d = self.latest_download()
        return d.status if d else None


class WatchPattern(db.Model):
    """Wzorzec tytułu RSS do automatycznego pobierania."""
    __tablename__ = 'watch_patterns'

    id         = db.Column(db.Integer, primary_key=True)
    pattern    = db.Column(db.String(500), nullable=False)
    dest_dir   = db.Column(db.String(500), nullable=True)
    server_id  = db.Column(db.Integer, db.ForeignKey('transmission_servers.id'), nullable=True)
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    server = db.relationship('TransmissionServer', foreign_keys=[server_id])

    def __repr__(self):
        return f'<WatchPattern {self.pattern}>'


class TransmissionServer(db.Model):
    __tablename__ = 'transmission_servers'

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(128), nullable=False)
    host       = db.Column(db.String(255), nullable=False)
    port       = db.Column(db.Integer, nullable=False, default=9091)
    username   = db.Column(db.String(128), nullable=True)
    password   = db.Column(db.String(255), nullable=True)
    base_path  = db.Column(db.String(256), nullable=False, default='/transmission/rpc')
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    downloads = db.relationship('Download', backref='server', lazy='dynamic')


class Download(db.Model):
    __tablename__ = 'downloads'

    STATUS_PENDING     = 'pending'
    STATUS_DOWNLOADING = 'downloading'
    STATUS_SEEDING     = 'seeding'
    STATUS_COMPLETED   = 'completed'
    STATUS_SAVED       = 'saved'
    STATUS_ERROR       = 'error'

    id                = db.Column(db.Integer, primary_key=True)
    rss_item_id       = db.Column(db.Integer, db.ForeignKey('rss_items.id'), nullable=False, index=True)
    server_id         = db.Column(db.Integer, db.ForeignKey('transmission_servers.id'), nullable=True)
    transmission_id   = db.Column(db.Integer, nullable=True)
    transmission_hash = db.Column(db.String(64), nullable=True)
    status            = db.Column(db.String(32), nullable=False, default='pending', index=True)
    error_message     = db.Column(db.Text, nullable=True)
    progress          = db.Column(db.Float, nullable=False, default=0.0)
    save_path         = db.Column(db.String(500), nullable=True)
    auto_downloaded   = db.Column(db.Boolean, nullable=False, default=False)
    added_at          = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at        = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at      = db.Column(db.DateTime, nullable=True)
    added_by          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    def status_badge_class(self):
        return {
            'pending':     'bg-secondary',
            'downloading': 'bg-primary',
            'seeding':     'bg-info text-dark',
            'completed':   'bg-success',
            'saved':       'bg-success',
            'error':       'bg-danger',
        }.get(self.status, 'bg-secondary')

    def status_label(self):
        return {
            'pending':     'Oczekuje',
            'downloading': 'Pobieranie',
            'seeding':     'Seedowanie',
            'completed':   'Ukończono',
            'saved':       'Zapisano',
            'error':       'Błąd',
        }.get(self.status, self.status)

    @property
    def is_active(self):
        return self.status in (self.STATUS_PENDING, self.STATUS_DOWNLOADING, self.STATUS_SEEDING)
