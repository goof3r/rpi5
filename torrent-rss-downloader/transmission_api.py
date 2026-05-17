import base64
import logging
import os
import tempfile
from pathlib import Path

from transmission_rpc import Client
from transmission_rpc.error import TransmissionError, TransmissionConnectError, TransmissionAuthError

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    'stopped':       lambda pct: 'completed' if pct >= 99.9 else 'error',
    'check wait':    lambda _: 'pending',
    'check':         lambda _: 'pending',
    'download wait': lambda _: 'downloading',
    'download':      lambda _: 'downloading',
    'seed wait':     lambda _: 'seeding',
    'seed':          lambda _: 'seeding',
}


def get_client(server) -> Client:
    return Client(
        host=server.host,
        port=server.port,
        username=server.username or None,
        password=server.password or None,
        path=server.base_path,
        protocol='http',
    )


def add_torrent_from_bytes(client: Client, torrent_bytes: bytes) -> tuple:
    """Dodaje torrent z bytes. Zwraca (transmission_id, hash_string).

    transmission-rpc v7 wysyła string jako 'filename' zamiast 'metainfo',
    więc zapisujemy do pliku tymczasowego i przekazujemy Path — wtedy
    biblioteka poprawnie koduje do base64 i używa pola 'metainfo'.
    """
    tmp = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.torrent')
        os.write(fd, torrent_bytes)
        os.close(fd)
        tmp = Path(tmp_path)
        torrent = client.add_torrent(torrent=tmp)
        return torrent.id, torrent.hash_string
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def get_torrent_status(client: Client, torrent_id: int) -> dict:
    """Zwraca {status, progress, error} dla danego torrentu."""
    torrent = client.get_torrent(
        torrent_id,
        arguments=['id', 'hashString', 'status', 'percentDone', 'errorString'],
    )
    pct = (torrent.percent_done or 0.0) * 100
    status_str = str(torrent.status).lower().replace('_', ' ')
    mapper = _STATUS_MAP.get(status_str, lambda _: 'downloading')
    our_status = mapper(pct)

    error = None
    if torrent.error_string:
        error = torrent.error_string
    if our_status == 'error' and not error:
        error = 'Torrent zatrzymany przed ukończeniem'

    return {
        'status':   our_status,
        'progress': round(pct, 1),
        'error':    error,
    }


def test_connection(server) -> tuple:
    """Testuje połączenie z Transmission. Zwraca (ok: bool, message: str)."""
    try:
        client = get_client(server)
        session = client.get_session()
        version = getattr(session, 'version', '?')
        free = getattr(session, 'download_dir_free_space', None)
        free_str = f' — wolne: {_fmt_bytes(free)}' if free else ''
        return True, f'Transmission {version}{free_str}'
    except TransmissionAuthError:
        return False, 'Błąd uwierzytelnienia (zły login/hasło)'
    except TransmissionConnectError as e:
        return False, f'Nie można połączyć: {e}'
    except TransmissionError as e:
        return False, f'Błąd Transmission: {e}'
    except Exception as e:
        return False, f'Nieznany błąd: {e}'


def _fmt_bytes(b) -> str:
    if b is None:
        return '?'
    b = int(b)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if b < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} PB'
