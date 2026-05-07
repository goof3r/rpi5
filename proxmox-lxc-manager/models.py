from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    containers = db.relationship('LxcContainer', backref='creator', lazy=True)


class ProxmoxSettings(db.Model):
    __tablename__ = 'proxmox_settings'

    id = db.Column(db.Integer, primary_key=True)
    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, default=8006)
    node = db.Column(db.String(100), nullable=False)
    token_id = db.Column(db.String(255), nullable=False)
    token_secret = db.Column(db.String(255), nullable=False)
    verify_ssl = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LxcContainer(db.Model):
    __tablename__ = 'lxc_containers'

    id = db.Column(db.Integer, primary_key=True)
    vmid = db.Column(db.Integer, nullable=False)
    hostname = db.Column(db.String(255), nullable=False)
    ram_mb = db.Column(db.Integer, nullable=False)
    disk_gb = db.Column(db.Numeric(10, 2), nullable=False)
    cores = db.Column(db.Integer, nullable=False)
    network_bridge = db.Column(db.String(50), nullable=False)
    template = db.Column(db.String(255), nullable=False)
    ip_config = db.Column(db.String(100))
    status = db.Column(db.String(50), default='creating')
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
