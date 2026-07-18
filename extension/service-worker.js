'use strict';

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:5000';
const SYNC_ALARM = 'safescroll-periodic-sync';
const SYNC_PERIOD_MINUTES = 5;
let activeSync = null;

const storageGet = (keys) => chrome.storage.local.get(keys);
const storageSet = (values) => chrome.storage.local.set(values);
const storageRemove = (keys) => chrome.storage.local.remove(keys);

const normalizeBaseUrl = (value) => {
  const url = new URL(value || DEFAULT_API_BASE_URL);
  const localHost = ['localhost', '127.0.0.1'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && localHost)) {
    throw new Error('SafeScroll requires HTTPS except for local development.');
  }
  return url.origin;
};

const ensureDeviceIdentifier = async () => {
  const state = await storageGet('deviceIdentifier');
  if (state.deviceIdentifier) return state.deviceIdentifier;
  const identifier = `chrome-${crypto.randomUUID()}`;
  await storageSet({ deviceIdentifier: identifier });
  return identifier;
};

const deviceMetadata = async () => {
  const platform = await chrome.runtime.getPlatformInfo();
  return {
    device_identifier: await ensureDeviceIdentifier(),
    device_name: `Chrome on ${platform.os || 'computer'}`,
    browser: `Chrome ${navigator.userAgent.match(/Chrome\/([0-9.]+)/)?.[1] || ''}`.trim(),
    platform: platform.os || 'unknown',
    extension_version: chrome.runtime.getManifest().version,
  };
};

const parseResponse = async (response) => {
  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    throw new Error('SafeScroll returned an unreadable response.');
  }
  if (!response.ok || !payload?.ok) {
    const problem = new Error(payload?.error?.message || `SafeScroll request failed (${response.status}).`);
    problem.status = response.status;
    problem.code = payload?.error?.code;
    throw problem;
  }
  return payload.data;
};

const request = async (path, { method = 'GET', body, authenticated = true } = {}) => {
  const state = await storageGet(['apiBaseUrl', 'accessToken', 'deviceIdentifier']);
  const baseUrl = normalizeBaseUrl(state.apiBaseUrl || DEFAULT_API_BASE_URL);
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (authenticated) {
    if (!state.accessToken) throw new Error('Connect the extension from the SafeScroll dashboard first.');
    headers.Authorization = `Bearer ${state.accessToken}`;
    if (state.deviceIdentifier) headers['X-Device-ID'] = state.deviceIdentifier;
  }
  const response = await fetch(`${baseUrl}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  });
  return parseResponse(response);
};

const clearAuthentication = async (status = 'not_connected') => {
  await storageRemove([
    'accessToken',
    'tokenExpiresAt',
    'user',
    'device',
    'configuration',
    'lastSyncAt',
  ]);
  await storageSet({ connectionStatus: status, lastError: null });
  await updateBadge();
};

const handleApiFailure = async (error) => {
  if (error?.status === 401 || error?.code === 'device_disconnected') {
    await clearAuthentication('disconnected');
  } else {
    await storageSet({
      connectionStatus: 'error',
      lastError: error instanceof Error ? error.message : 'Synchronization failed.',
    });
    await updateBadge();
  }
};

const updateBadge = async () => {
  const state = await storageGet(['accessToken', 'paused', 'connectionStatus']);
  if (!state.accessToken || state.connectionStatus === 'disconnected') {
    await chrome.action.setBadgeText({ text: '!' });
    await chrome.action.setBadgeBackgroundColor({ color: '#64748b' });
  } else if (state.paused) {
    await chrome.action.setBadgeText({ text: 'Ⅱ' });
    await chrome.action.setBadgeBackgroundColor({ color: '#f59e0b' });
  } else {
    await chrome.action.setBadgeText({ text: '' });
  }
};

const notifyYouTubeTabs = async (message) => {
  const tabs = await chrome.tabs.query({ url: 'https://www.youtube.com/*' });
  await Promise.all(tabs.map(async (tab) => {
    if (!tab.id) return;
    try {
      await chrome.tabs.sendMessage(tab.id, message);
    } catch (_error) {
      // A tab can navigate between query and delivery; the next page load
      // retrieves the current state again.
    }
  }));
};

const performSync = async (trigger = 'manual') => {
  const current = await storageGet(['accessToken', 'configuration']);
  if (!current.accessToken) throw new Error('Connect the extension from the SafeScroll dashboard first.');
  const metadata = await deviceMetadata();
  const configVersion = current.configuration?.configuration?.config_version;
  const data = await request('/api/extension/sync', {
    method: 'POST',
    body: {
      ...metadata,
      ...(Number.isInteger(configVersion) ? { config_version: configVersion } : {}),
    },
  });
  const now = new Date().toISOString();
  await storageSet({
    configuration: data,
    device: data.device,
    lastSyncAt: now,
    connectionStatus: 'connected',
    lastError: null,
    lastSyncTrigger: trigger,
  });
  await updateBadge();
  await notifyYouTubeTabs({ type: 'CONFIGURATION_UPDATED', configuration: data });
  return data;
};

const syncConfiguration = async (trigger) => {
  if (activeSync) return activeSync;
  activeSync = performSync(trigger)
    .catch(async (error) => {
      await handleApiFailure(error);
      throw error;
    })
    .finally(() => { activeSync = null; });
  return activeSync;
};

const pairExtension = async ({ pairingToken, apiBaseUrl }) => {
  const normalizedBaseUrl = normalizeBaseUrl(apiBaseUrl);
  await storageSet({ apiBaseUrl: normalizedBaseUrl });
  const metadata = await deviceMetadata();
  const data = await request('/api/extension/exchange', {
    method: 'POST',
    authenticated: false,
    body: { pairing_token: pairingToken, ...metadata },
  });
  await storageSet({
    accessToken: data.access_token,
    tokenExpiresAt: data.expires_at,
    user: data.user,
    device: data.device,
    connectionStatus: 'connected',
    lastError: null,
    paused: false,
  });
  await syncConfiguration('pairing');
  return { ok: true };
};

const publicState = async () => {
  const state = await storageGet([
    'accessToken', 'apiBaseUrl', 'user', 'device', 'configuration',
    'lastSyncAt', 'connectionStatus', 'lastError', 'paused', 'tokenExpiresAt',
  ]);
  return {
    connected: Boolean(state.accessToken && state.connectionStatus !== 'disconnected'),
    apiBaseUrl: state.apiBaseUrl || DEFAULT_API_BASE_URL,
    user: state.user || null,
    device: state.device || null,
    configuration: state.configuration || null,
    lastSyncAt: state.lastSyncAt || null,
    connectionStatus: state.connectionStatus || 'not_connected',
    lastError: state.lastError || null,
    paused: Boolean(state.paused),
    tokenExpiresAt: state.tokenExpiresAt || null,
  };
};

const initialize = async (trigger) => {
  await ensureDeviceIdentifier();
  await chrome.alarms.create(SYNC_ALARM, { periodInMinutes: SYNC_PERIOD_MINUTES });
  await updateBadge();
  const { accessToken } = await storageGet('accessToken');
  if (accessToken) {
    try {
      await syncConfiguration(trigger);
    } catch (_error) {
      // State and badge already reflect the failure.
    }
  }
};

chrome.runtime.onInstalled.addListener(() => { initialize('installed'); });
chrome.runtime.onStartup.addListener(() => { initialize('startup'); });

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === SYNC_ALARM) syncConfiguration('periodic').catch(() => {});
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const respond = async () => {
    switch (message?.type) {
      case 'PAIR_EXTENSION':
        try {
          return await pairExtension(message.payload || {});
        } catch (error) {
          await handleApiFailure(error);
          return { ok: false, message: error instanceof Error ? error.message : 'Pairing failed.' };
        }
      case 'GET_STATE':
        return { ok: true, state: await publicState() };
      case 'SYNC_NOW':
        try {
          await syncConfiguration('manual');
          return { ok: true, state: await publicState() };
        } catch (error) {
          return { ok: false, message: error instanceof Error ? error.message : 'Sync failed.' };
        }
      case 'SET_PAUSED': {
        const paused = Boolean(message.paused);
        await storageSet({ paused });
        await updateBadge();
        await notifyYouTubeTabs({ type: 'PAUSE_CHANGED', paused });
        return { ok: true, state: await publicState() };
      }
      case 'DISCONNECT':
        try {
          await request('/api/extension/disconnect', { method: 'POST', body: {} });
        } catch (error) {
          if (error?.status !== 401) return { ok: false, message: error.message };
        }
        await clearAuthentication('not_connected');
        await notifyYouTubeTabs({ type: 'PAUSE_CHANGED', paused: true });
        return { ok: true, state: await publicState() };
      case 'YOUTUBE_OPENED':
        syncConfiguration('youtube_opened').catch(() => {});
        return { ok: true, state: await publicState() };
      case 'CONFIGURATION_CHANGED':
        syncConfiguration('mode_changed').catch(() => {});
        return { ok: true };
      default:
        return { ok: false, message: 'Unknown extension message.' };
    }
  };

  respond().then(sendResponse).catch((error) => {
    sendResponse({ ok: false, message: error instanceof Error ? error.message : 'Extension error.' });
  });
  return true;
});

initialize('service_worker').catch(() => {});
