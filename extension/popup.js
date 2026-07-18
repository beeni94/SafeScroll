(() => {
  'use strict';

  const elements = {
    pill: document.querySelector('[data-connection-pill]'),
    accountName: document.querySelector('[data-account-name]'),
    accountEmail: document.querySelector('[data-account-email]'),
    modeIcon: document.querySelector('[data-mode-icon]'),
    modeName: document.querySelector('[data-mode-name]'),
    strictness: document.querySelector('[data-mode-strictness]'),
    lastSync: document.querySelector('[data-last-sync]'),
    notice: document.querySelector('[data-notice]'),
    sync: document.querySelector('[data-sync-button]'),
    pause: document.querySelector('[data-pause-button]'),
    dashboard: document.querySelector('[data-dashboard-button]'),
    disconnect: document.querySelector('[data-disconnect-button]'),
  };
  let currentState = null;

  const relativeTime = (value) => {
    if (!value) return 'Never';
    const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
    return new Date(value).toLocaleDateString();
  };

  const send = (message) => new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, message: 'SafeScroll background service is unavailable.' });
      } else {
        resolve(response || { ok: false, message: 'No response from SafeScroll.' });
      }
    });
  });

  const render = (state) => {
    currentState = state;
    const connected = Boolean(state?.connected);
    const mode = state?.configuration?.active_mode;
    elements.pill.textContent = connected ? 'Connected' : 'Offline';
    elements.pill.classList.toggle('connected', connected);
    elements.accountName.textContent = state?.user?.name || 'Not connected';
    elements.accountEmail.textContent = state?.user?.email || 'Connect from your SafeScroll dashboard.';
    elements.modeIcon.textContent = mode?.icon || '—';
    elements.modeName.textContent = mode?.name || 'No mode synchronized';
    elements.strictness.textContent = mode?.strictness ? `${mode.strictness} / 5` : '—';
    elements.lastSync.textContent = relativeTime(state?.lastSyncAt);
    elements.sync.disabled = !connected;
    elements.pause.disabled = !connected;
    elements.pause.textContent = state?.paused ? 'Resume filtering' : 'Pause filtering';
    elements.disconnect.hidden = !connected;
    elements.notice.classList.toggle('error', state?.connectionStatus === 'error');
    elements.notice.textContent = state?.lastError
      || (connected
        ? (state?.paused ? 'Filtering is paused on YouTube.' : 'Configuration is synchronized and filtering is active.')
        : 'Open your dashboard to connect SafeScroll.');
  };

  const setWorking = (button, working, label) => {
    button.disabled = working || !currentState?.connected;
    if (working) {
      button.dataset.previousLabel = button.textContent;
      button.textContent = label;
    } else if (button.dataset.previousLabel) {
      button.textContent = button.dataset.previousLabel;
      delete button.dataset.previousLabel;
    }
  };

  elements.sync.addEventListener('click', async () => {
    setWorking(elements.sync, true, 'Syncing…');
    const response = await send({ type: 'SYNC_NOW' });
    if (response.ok) render(response.state);
    else render({ ...currentState, lastError: response.message, connectionStatus: 'error' });
    setWorking(elements.sync, false, '');
  });

  elements.pause.addEventListener('click', async () => {
    const response = await send({ type: 'SET_PAUSED', paused: !currentState?.paused });
    if (response.ok) render(response.state);
  });

  elements.dashboard.addEventListener('click', () => {
    const baseUrl = currentState?.apiBaseUrl || 'http://127.0.0.1:5000';
    chrome.tabs.create({ url: `${baseUrl}/extension` });
  });

  elements.disconnect.addEventListener('click', async () => {
    if (!window.confirm('Disconnect this browser from SafeScroll?')) return;
    const response = await send({ type: 'DISCONNECT' });
    if (response.ok) render(response.state);
  });

  send({ type: 'GET_STATE' }).then((response) => {
    if (response.ok) render(response.state);
    else render({ connected: false, lastError: response.message, connectionStatus: 'error' });
  });
})();
