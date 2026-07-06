const sessionIds = new Map();

function rpcUrl(server) {
  let base = String(server.url || '').trim().replace(/\/+$/, '');
  if (!base) throw new Error('Brak URL serwera');
  if (!/^https?:\/\//i.test(base)) base = 'http://' + base;
  if (!/\/transmission\/rpc$/i.test(base)) base += '/transmission/rpc';
  return base;
}

async function rpc(server, payload) {
  const url = rpcUrl(server);
  const headers = { 'Content-Type': 'application/json' };

  if (server.username) {
    headers['Authorization'] =
      'Basic ' + btoa(`${server.username}:${server.password || ''}`);
  }

  const sid = sessionIds.get(server.id);
  if (sid) headers['X-Transmission-Session-Id'] = sid;

  const body = JSON.stringify(payload);
  let res = await fetch(url, { method: 'POST', headers, body });

  if (res.status === 409) {
    const newSid = res.headers.get('X-Transmission-Session-Id');
    if (!newSid) throw new Error('Brak naglowka X-Transmission-Session-Id w 409');
    sessionIds.set(server.id, newSid);
    headers['X-Transmission-Session-Id'] = newSid;
    res = await fetch(url, { method: 'POST', headers, body });
  }

  if (res.status === 401) throw new Error('Nieautoryzowany (sprawdz login/haslo)');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);

  const json = await res.json();
  if (json.result !== 'success') {
    throw new Error(`RPC: ${json.result || 'unknown error'}`);
  }
  return json.arguments || {};
}

export async function getTorrents(server) {
  const args = await rpc(server, {
    method: 'torrent-get',
    arguments: {
      fields: [
        'id', 'name', 'status', 'percentDone', 'rateDownload', 'rateUpload',
        'eta', 'totalSize', 'leftUntilDone', 'isFinished', 'downloadDir',
        'addedDate', 'doneDate', 'errorString', 'error', 'hashString',
        'sizeWhenDone', 'peersGettingFromUs', 'peersSendingToUs',
      ],
    },
  });
  return args.torrents || [];
}

export async function addTorrentFile(server, base64Metainfo, opts = {}) {
  const args = { metainfo: base64Metainfo, paused: !!opts.paused };
  if (opts.downloadDir) args['download-dir'] = opts.downloadDir;
  return rpc(server, { method: 'torrent-add', arguments: args });
}

export async function addTorrentUrl(server, url, opts = {}) {
  const args = { filename: url, paused: !!opts.paused };
  if (opts.downloadDir) args['download-dir'] = opts.downloadDir;
  return rpc(server, { method: 'torrent-add', arguments: args });
}

export async function removeTorrent(server, ids, deleteLocal = false) {
  return rpc(server, {
    method: 'torrent-remove',
    arguments: { ids, 'delete-local-data': deleteLocal },
  });
}

export async function startTorrent(server, ids) {
  return rpc(server, { method: 'torrent-start', arguments: { ids } });
}

export async function stopTorrent(server, ids) {
  return rpc(server, { method: 'torrent-stop', arguments: { ids } });
}

export async function testConnection(server) {
  return rpc(server, { method: 'session-get' });
}

export function webUiUrl(server) {
  let base = String(server.url || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(base)) base = 'http://' + base;
  base = base.replace(/\/transmission\/rpc$/i, '');
  return base + '/transmission/web/';
}

export const TORRENT_STATUS = {
  0: 'zatrzymany',
  1: 'w kolejce (sprawdzanie)',
  2: 'sprawdzanie',
  3: 'w kolejce (pobieranie)',
  4: 'pobieranie',
  5: 'w kolejce (seed)',
  6: 'seedowanie',
};
