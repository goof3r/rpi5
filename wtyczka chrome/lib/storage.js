const SERVERS_KEY = 'servers';
const SEEN_KEY = 'seenCompletions';
const LAST_SERVER_KEY = 'lastServerId';

export async function getServers() {
  const { [SERVERS_KEY]: servers = [] } = await chrome.storage.local.get(SERVERS_KEY);
  return servers;
}

export async function saveServers(servers) {
  await chrome.storage.local.set({ [SERVERS_KEY]: servers });
}

export async function getServer(id) {
  const servers = await getServers();
  return servers.find((s) => s.id === id);
}

export async function upsertServer(server) {
  const servers = await getServers();
  const idx = servers.findIndex((s) => s.id === server.id);
  if (idx >= 0) servers[idx] = server;
  else servers.push(server);
  await saveServers(servers);
}

export async function removeServerById(id) {
  const servers = await getServers();
  await saveServers(servers.filter((s) => s.id !== id));
}

export async function getSeenCompletions() {
  const { [SEEN_KEY]: seen = {} } = await chrome.storage.local.get(SEEN_KEY);
  return seen;
}

export async function saveSeenCompletions(seen) {
  await chrome.storage.local.set({ [SEEN_KEY]: seen });
}

export async function getLastServerId() {
  const { [LAST_SERVER_KEY]: id = null } = await chrome.storage.local.get(LAST_SERVER_KEY);
  return id;
}

export async function setLastServerId(id) {
  await chrome.storage.local.set({ [LAST_SERVER_KEY]: id });
}

export function genId() {
  return 's_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

export function originFromUrl(url) {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}
