import {
  getServers,
  getLastServerId,
  setLastServerId,
} from './lib/storage.js';
import {
  getTorrents,
  addTorrentUrl,
  addTorrentFile,
  startTorrent,
  stopTorrent,
  removeTorrent,
  TORRENT_STATUS,
} from './lib/transmission.js';

const $ = (id) => document.getElementById(id);
const select = $('server-select');
const list = $('list');
const totals = $('totals');
const msg = $('msg');
const addInput = $('add-input');
const addBtn = $('add-btn');
const addFile = $('add-file');
const refreshBtn = $('refresh');
const optionsBtn = $('options');

let currentServer = null;
let refreshTimer = null;

init();

async function init() {
  const servers = await getServers();
  if (!servers.length) {
    list.innerHTML =
      '<p class="empty">Brak skonfigurowanych serwerow.<br>Otworz <a href="#" id="open-opts">ustawienia</a>, aby dodac serwer.</p>';
    $('open-opts').addEventListener('click', openOptions);
    totals.hidden = true;
    return;
  }

  select.innerHTML = '';
  for (const s of servers) {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = s.name;
    select.appendChild(opt);
  }

  const lastId = await getLastServerId();
  if (lastId && servers.find((s) => s.id === lastId)) {
    select.value = lastId;
  }
  currentServer = servers.find((s) => s.id === select.value) || servers[0];

  select.addEventListener('change', async () => {
    currentServer = servers.find((s) => s.id === select.value);
    await setLastServerId(currentServer.id);
    await refresh();
  });

  refreshBtn.addEventListener('click', () => refresh(true));
  optionsBtn.addEventListener('click', openOptions);

  addBtn.addEventListener('click', addFromInput);
  addInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addFromInput();
  });
  addFile.addEventListener('change', addFromFile);

  await refresh();
  refreshTimer = setInterval(() => refresh().catch(() => {}), 3000);
}

function openOptions(e) {
  if (e) e.preventDefault();
  chrome.runtime.openOptionsPage();
}

function showMsg(text, kind = 'ok', timeout = 3500) {
  msg.textContent = text;
  msg.className = 'msg ' + kind;
  msg.hidden = false;
  if (timeout) setTimeout(() => (msg.hidden = true), timeout);
}

async function refresh(loud = false) {
  if (!currentServer) return;
  try {
    const torrents = await getTorrents(currentServer);
    renderTotals(torrents);
    renderList(torrents);
    if (loud) showMsg('Odswiezono', 'ok', 1500);
  } catch (err) {
    list.innerHTML = `<p class="empty">Blad: ${escape(err.message)}</p>`;
    totals.innerHTML = '';
  }
}

function renderTotals(torrents) {
  const active = torrents.filter((t) => t.status === 4).length;
  const done = torrents.filter((t) => t.percentDone >= 1).length;
  const seeding = torrents.filter((t) => t.status === 6).length;
  const dl = sumRate(torrents, 'rateDownload');
  const ul = sumRate(torrents, 'rateUpload');
  totals.innerHTML =
    `<span>${torrents.length} torr.</span>` +
    `<span>${active} pobiera</span>` +
    `<span>${done} gotowe</span>` +
    `<span>${seeding} seed</span>` +
    `<span style="margin-left:auto;color:#4ade80">&#9660; ${fmtRate(dl)}</span>` +
    `<span style="color:#60a5fa">&#9650; ${fmtRate(ul)}</span>`;
}

function sumRate(torrents, key) {
  return torrents.reduce((a, t) => a + (t[key] || 0), 0);
}

function renderList(torrents) {
  if (!torrents.length) {
    list.innerHTML = '<p class="empty">Brak torrentow.</p>';
    return;
  }
  const sorted = [...torrents].sort((a, b) => {
    if (a.percentDone < 1 && b.percentDone >= 1) return -1;
    if (a.percentDone >= 1 && b.percentDone < 1) return 1;
    return (b.rateDownload || 0) - (a.rateDownload || 0);
  });
  list.innerHTML = '';
  for (const t of sorted) list.appendChild(renderTorrent(t));
}

function renderTorrent(t) {
  const div = document.createElement('div');
  div.className = 't';
  if (t.error) div.classList.add('error');
  if (currentServer && currentServer.sambaBase) {
    div.title = 'Dwuklik: otworz w udziale sieciowym';
    div.style.cursor = 'pointer';
  }
  div.addEventListener('dblclick', (e) => {
    if (e.target.closest('.t-actions')) return;
    openInShare(t);
  });

  const pct = Math.round((t.percentDone || 0) * 100);
  const statusLabel = TORRENT_STATUS[t.status] || `status ${t.status}`;
  const remaining = t.leftUntilDone || 0;
  const eta = t.eta > 0 ? fmtEta(t.eta) : (t.percentDone >= 1 ? 'gotowe' : '-');

  const fgClass = t.error ? 'err' : (t.percentDone >= 1 ? 'done' : '');

  div.innerHTML = `
    <div class="t-row1">
      <div class="t-name">${escape(t.name || '(bez nazwy)')}</div>
      <div class="t-status">${escape(statusLabel)}</div>
    </div>
    <div class="bar-bg"><div class="bar-fg ${fgClass}" style="width:${pct}%"></div></div>
    <div class="t-meta">
      <span>${pct}% &middot; ${fmtBytes(t.sizeWhenDone - remaining)} / ${fmtBytes(t.sizeWhenDone || t.totalSize)}</span>
      <span>&#9660; ${fmtRate(t.rateDownload)} &nbsp; &#9650; ${fmtRate(t.rateUpload)} &nbsp; ETA ${escape(eta)}</span>
    </div>
    ${t.errorString ? `<div class="t-meta" style="color:#f87171">${escape(t.errorString)}</div>` : ''}
  `;

  const actions = document.createElement('div');
  actions.className = 't-actions';

  const isStopped = t.status === 0;
  const btnTog = document.createElement('button');
  btnTog.textContent = isStopped ? 'Wznow' : 'Pauza';
  btnTog.addEventListener('click', async () => {
    try {
      if (isStopped) await startTorrent(currentServer, [t.id]);
      else await stopTorrent(currentServer, [t.id]);
      await refresh();
    } catch (err) {
      showMsg(err.message, 'err');
    }
  });

  const btnDel = document.createElement('button');
  btnDel.textContent = 'Usun';
  btnDel.className = 'danger';
  btnDel.addEventListener('click', async () => {
    if (!confirm(`Usunac "${t.name}"?`)) return;
    try {
      await removeTorrent(currentServer, [t.id], false);
      await refresh();
    } catch (err) {
      showMsg(err.message, 'err');
    }
  });

  const btnDelData = document.createElement('button');
  btnDelData.textContent = 'Usun + pliki';
  btnDelData.className = 'danger';
  btnDelData.addEventListener('click', async () => {
    if (!confirm(`Usunac "${t.name}" RAZEM z plikami z dysku?`)) return;
    try {
      await removeTorrent(currentServer, [t.id], true);
      await refresh();
    } catch (err) {
      showMsg(err.message, 'err');
    }
  });

  actions.append(btnTog, btnDel, btnDelData);
  div.appendChild(actions);
  return div;
}

async function addFromInput() {
  const v = addInput.value.trim();
  if (!v || !currentServer) return;
  addBtn.disabled = true;
  try {
    await addTorrentUrl(currentServer, v);
    addInput.value = '';
    showMsg('Dodano', 'ok');
    await refresh();
  } catch (err) {
    showMsg('Blad: ' + err.message, 'err', 6000);
  } finally {
    addBtn.disabled = false;
  }
}

async function addFromFile(e) {
  const file = e.target.files && e.target.files[0];
  if (!file || !currentServer) return;
  try {
    const b64 = await fileToBase64(file);
    await addTorrentFile(currentServer, b64);
    showMsg(`Dodano: ${file.name}`, 'ok');
    await refresh();
  } catch (err) {
    showMsg('Blad: ' + err.message, 'err', 6000);
  } finally {
    e.target.value = '';
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const s = r.result;
      const comma = s.indexOf(',');
      resolve(comma >= 0 ? s.slice(comma + 1) : s);
    };
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

function buildShareUrl(server, torrent) {
  if (!server || !server.sambaBase) return null;
  const base = server.sambaBase.trim().replace(/[\\/]+$/, '');
  const prefix = (server.dlPathPrefix || '').trim().replace(/\\/g, '/').replace(/\/+$/, '');
  const dl = (torrent.downloadDir || '').replace(/\\/g, '/').replace(/\/+$/, '');
  let rel = '';
  if (prefix && dl && dl.startsWith(prefix)) {
    rel = dl.slice(prefix.length);
  }
  rel = rel.replace(/^\/+/, '');
  const name = encodeURIComponent(torrent.name || '');
  const parts = [base];
  if (rel) parts.push(rel.split('/').map(encodeURIComponent).join('/'));
  if (name) parts.push(name);
  return parts.join('/');
}

function openInShare(t) {
  const url = buildShareUrl(currentServer, t);
  if (!url) {
    showMsg('Skonfiguruj "Bazowy URL udzialu" w ustawieniach serwera.', 'err', 5000);
    return;
  }
  chrome.tabs.create({ url });
}

function fmtBytes(n) {
  if (!n || n < 0) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(n >= 10 || i === 0 ? 0 : 1) + ' ' + u[i];
}

function fmtRate(n) {
  return fmtBytes(n) + '/s';
}

function fmtEta(s) {
  if (s <= 0) return '-';
  if (s < 60) return s + 's';
  if (s < 3600) return Math.round(s / 60) + 'm';
  if (s < 86400) return Math.round(s / 3600) + 'h';
  return Math.round(s / 86400) + 'd';
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

window.addEventListener('unload', () => {
  if (refreshTimer) clearInterval(refreshTimer);
});
