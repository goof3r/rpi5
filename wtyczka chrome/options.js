import {
  getServers,
  saveServers,
  upsertServer,
  removeServerById,
  genId,
  originFromUrl,
} from './lib/storage.js';
import { testConnection } from './lib/transmission.js';

const $ = (id) => document.getElementById(id);
const list = $('servers');
const form = $('form');
const fId = $('f-id');
const fName = $('f-name');
const fUrl = $('f-url');
const fUser = $('f-user');
const fPass = $('f-pass');
const fSamba = $('f-samba');
const fDlpath = $('f-dlpath');
const formMsg = $('form-msg');
const formTitle = $('form-title');
const cancelBtn = $('cancel-btn');
const testBtn = $('test-btn');
const exportBtn = $('export-btn');
const importBtn = $('import-btn');
const importFile = $('import-file');

render();

form.addEventListener('submit', onSave);
cancelBtn.addEventListener('click', resetForm);
testBtn.addEventListener('click', onTest);
exportBtn.addEventListener('click', onExport);
importBtn.addEventListener('click', () => importFile.click());
importFile.addEventListener('change', onImport);

async function render() {
  const servers = await getServers();
  list.innerHTML = '';
  if (!servers.length) {
    const p = document.createElement('p');
    p.className = 'empty';
    p.textContent = 'Brak serwerow. Dodaj pierwszy ponizej.';
    list.appendChild(p);
    return;
  }
  for (const s of servers) list.appendChild(renderRow(s));
}

function renderRow(s) {
  const div = document.createElement('div');
  div.className = 'server';
  div.innerHTML = `
    <div class="info">
      <div class="name"></div>
      <div class="url"></div>
    </div>
    <div class="btns">
      <button data-act="edit">Edytuj</button>
      <button data-act="del" class="danger">Usun</button>
    </div>
  `;
  div.querySelector('.name').textContent = s.name;
  div.querySelector('.url').textContent =
    s.url + (s.username ? `  (login: ${s.username})` : '');

  div.querySelector('[data-act=edit]').addEventListener('click', () => loadForm(s));
  div.querySelector('[data-act=del]').addEventListener('click', () => onDelete(s));
  return div;
}

function loadForm(s) {
  fId.value = s.id;
  fName.value = s.name;
  fUrl.value = s.url;
  fUser.value = s.username || '';
  fPass.value = s.password || '';
  fSamba.value = s.sambaBase || '';
  fDlpath.value = s.dlPathPrefix || '';
  formTitle.textContent = `Edycja: ${s.name}`;
  cancelBtn.hidden = false;
  window.scrollTo({ top: form.offsetTop - 20, behavior: 'smooth' });
}

function resetForm() {
  form.reset();
  fId.value = '';
  formTitle.textContent = 'Dodaj serwer';
  cancelBtn.hidden = true;
  hideMsg();
}

function showMsg(text, kind = 'ok') {
  formMsg.textContent = text;
  formMsg.className = 'msg ' + kind;
  formMsg.hidden = false;
}

function hideMsg() {
  formMsg.hidden = true;
}

function readForm() {
  return {
    id: fId.value || genId(),
    name: fName.value.trim(),
    url: fUrl.value.trim(),
    username: fUser.value.trim(),
    password: fPass.value,
    sambaBase: fSamba.value.trim(),
    dlPathPrefix: fDlpath.value.trim(),
  };
}

async function ensureHostPermission(url) {
  const origin = originFromUrl(url);
  if (!origin) throw new Error('Niepoprawny URL');
  const pattern = origin + '/*';
  const has = await chrome.permissions.contains({ origins: [pattern] });
  if (has) return true;
  const granted = await chrome.permissions.request({ origins: [pattern] });
  if (!granted) throw new Error('Wymagana zgoda na dostep do ' + origin);
  return true;
}

async function onTest() {
  hideMsg();
  const s = readForm();
  if (!s.name || !s.url) {
    showMsg('Podaj nazwe i URL', 'err');
    return;
  }
  testBtn.disabled = true;
  try {
    await ensureHostPermission(s.url);
    await testConnection(s);
    showMsg('Polaczenie OK', 'ok');
  } catch (err) {
    showMsg('Blad: ' + err.message, 'err');
  } finally {
    testBtn.disabled = false;
  }
}

async function onSave(e) {
  e.preventDefault();
  hideMsg();
  const s = readForm();
  if (!s.name || !s.url) {
    showMsg('Podaj nazwe i URL', 'err');
    return;
  }
  try {
    await ensureHostPermission(s.url);
    await upsertServer(s);
    showMsg('Zapisano', 'ok');
    await render();
    resetForm();
  } catch (err) {
    showMsg('Blad: ' + err.message, 'err');
  }
}

async function onExport() {
  const servers = await getServers();
  const payload = {
    app: 'transmission-notifier',
    version: 1,
    exportedAt: new Date().toISOString(),
    servers,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `transmission-notifier-${new Date()
    .toISOString()
    .slice(0, 10)}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  showMsg(`Wyeksportowano ${servers.length} serwer(ow)`, 'ok');
}

async function onImport(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  hideMsg();
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    const imported = Array.isArray(data) ? data : data.servers;
    if (!Array.isArray(imported) || !imported.length) {
      throw new Error('Plik nie zawiera serwerow');
    }
    const cleaned = imported
      .map((s) => ({
        id: s.id || genId(),
        name: String(s.name || '').trim(),
        url: String(s.url || '').trim(),
        username: String(s.username || ''),
        password: String(s.password || ''),
        sambaBase: String(s.sambaBase || ''),
        dlPathPrefix: String(s.dlPathPrefix || ''),
      }))
      .filter((s) => s.name && s.url);
    if (!cleaned.length) throw new Error('Zaden wpis nie ma nazwy i URL');

    const existing = await getServers();
    let mode = 'replace';
    if (existing.length) {
      mode = confirm(
        `Plik zawiera ${cleaned.length} serwer(ow). Aktualnie masz ${existing.length}.\n\n` +
          'OK = zastapic wszystkie\n' +
          'Anuluj = scalic (po ID; istniejace nadpisane, nowe dodane)',
      )
        ? 'replace'
        : 'merge';
    }

    let result;
    if (mode === 'replace') {
      result = cleaned;
    } else {
      result = [...existing];
      for (const s of cleaned) {
        const idx = result.findIndex((x) => x.id === s.id);
        if (idx >= 0) result[idx] = s;
        else result.push(s);
      }
    }

    const origins = [
      ...new Set(
        result
          .map((s) => originFromUrl(s.url))
          .filter(Boolean)
          .map((o) => o + '/*'),
      ),
    ];
    if (origins.length) {
      try {
        await chrome.permissions.request({ origins });
      } catch {
        /* odmowa nie blokuje zapisu - polling zwroci blad pozniej */
      }
    }

    await saveServers(result);
    await render();
    showMsg(
      `Zaimportowano ${cleaned.length} serwer(ow) [${
        mode === 'replace' ? 'zastapiono' : 'scalono'
      }]`,
      'ok',
    );
  } catch (err) {
    showMsg('Blad importu: ' + err.message, 'err');
  } finally {
    e.target.value = '';
  }
}

async function onDelete(s) {
  if (!confirm(`Usunac serwer "${s.name}"?`)) return;
  await removeServerById(s.id);
  const origin = originFromUrl(s.url);
  if (origin) {
    try {
      await chrome.permissions.remove({ origins: [origin + '/*'] });
    } catch { /* nie wszystkie pozwolenia da sie cofnac */ }
  }
  await render();
  if (fId.value === s.id) resetForm();
}
