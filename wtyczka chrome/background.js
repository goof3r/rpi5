import {
  getServers,
  getSeenCompletions,
  saveSeenCompletions,
} from './lib/storage.js';
import {
  getTorrents,
  addTorrentUrl,
  webUiUrl,
} from './lib/transmission.js';

const ALARM_NAME = 'tm-poll';
const POLL_MINUTES = 1;
const CTX_MENU_PREFIX = 'tm-send-';
const NOTIF_PREFIX = 'tm-done:';

chrome.runtime.onInstalled.addListener(async () => {
  ensureAlarm();
  await rebuildContextMenu();
});

chrome.runtime.onStartup.addListener(() => {
  ensureAlarm();
  rebuildContextMenu();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.servers) {
    rebuildContextMenu();
  }
});

function ensureAlarm() {
  chrome.alarms.get(ALARM_NAME, (a) => {
    if (!a) chrome.alarms.create(ALARM_NAME, { periodInMinutes: POLL_MINUTES });
  });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) pollAllServers().catch(console.warn);
});

async function pollAllServers() {
  const servers = await getServers();
  const seen = await getSeenCompletions();
  let totalActive = 0;

  for (const server of servers) {
    try {
      const torrents = await getTorrents(server);
      const serverSeen = seen[server.id] || {};

      for (const t of torrents) {
        const key = String(t.id);
        const isDone = t.percentDone >= 1 || t.isFinished;

        if (isDone) {
          if (!serverSeen[key]) {
            chrome.notifications.create(
              `${NOTIF_PREFIX}${server.id}:${t.id}:${Date.now()}`,
              {
                type: 'basic',
                iconUrl: 'icons/icon128.png',
                title: `Pobieranie zakonczone - ${server.name}`,
                message: t.name || '(bez nazwy)',
                priority: 2,
                requireInteraction: false,
              },
            );
            serverSeen[key] = t.doneDate || Math.floor(Date.now() / 1000);
          }
        } else {
          if (serverSeen[key]) delete serverSeen[key];
          if (t.status === 4) totalActive++;
        }
      }

      const activeIds = new Set(torrents.map((t) => String(t.id)));
      for (const k of Object.keys(serverSeen)) {
        if (!activeIds.has(k)) delete serverSeen[k];
      }
      seen[server.id] = serverSeen;
    } catch (err) {
      console.warn(`[transmission] ${server.name}:`, err.message);
    }
  }

  await saveSeenCompletions(seen);
  setBadge(totalActive);
}

function setBadge(count) {
  chrome.action.setBadgeText({ text: count > 0 ? String(count) : '' });
  chrome.action.setBadgeBackgroundColor({ color: '#2563eb' });
}

async function rebuildContextMenu() {
  await chrome.contextMenus.removeAll();
  const servers = await getServers();
  if (!servers.length) return;

  const parentId = 'tm-parent';
  chrome.contextMenus.create({
    id: parentId,
    title: 'Wyslij do Transmission',
    contexts: ['link', 'selection'],
  });
  for (const s of servers) {
    chrome.contextMenus.create({
      id: CTX_MENU_PREFIX + s.id,
      parentId,
      title: s.name,
      contexts: ['link', 'selection'],
    });
  }
}

chrome.contextMenus.onClicked.addListener(async (info) => {
  if (!info.menuItemId.startsWith(CTX_MENU_PREFIX)) return;
  const serverId = info.menuItemId.slice(CTX_MENU_PREFIX.length);
  const target = info.linkUrl || (info.selectionText || '').trim();
  if (!target) return;

  const servers = await getServers();
  const server = servers.find((s) => s.id === serverId);
  if (!server) return;

  try {
    await addTorrentUrl(server, target);
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon128.png',
      title: `Dodano do ${server.name}`,
      message: target.length > 120 ? target.slice(0, 117) + '...' : target,
    });
  } catch (err) {
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon128.png',
      title: `Blad: ${server.name}`,
      message: err.message,
    });
  }
});

chrome.notifications.onClicked.addListener(async (id) => {
  if (!id.startsWith(NOTIF_PREFIX)) return;
  const [, rest] = id.split(NOTIF_PREFIX);
  const serverId = rest.split(':')[0];
  const servers = await getServers();
  const server = servers.find((s) => s.id === serverId);
  if (server) {
    chrome.tabs.create({ url: webUiUrl(server) });
  }
  chrome.notifications.clear(id);
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === 'poll-now') {
    pollAllServers()
      .then(() => sendResponse({ ok: true }))
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }
});
