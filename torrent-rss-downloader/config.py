import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    SQLALCHEMY_DATABASE_URI = 'sqlite:///torrents.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    RSS_FEED_URL = os.environ.get('RSS_FEED_URL', '')
    RSS_POLL_INTERVAL = int(os.environ.get('RSS_POLL_INTERVAL', '15'))
    FLASK_PORT = int(os.environ.get('FLASK_PORT', '5000'))
    SCHEDULER_ENABLED = os.environ.get('SCHEDULER_ENABLED', 'true').lower() == 'true'
