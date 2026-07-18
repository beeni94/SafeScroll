(() => {
  'use strict';

  let state = null;
  let scanTimer = null;
  const FILTERED_ATTRIBUTE = 'data-safescroll-filtered';

  const activeMode = () => state?.configuration?.active_mode || null;
  const filteringEnabled = () => Boolean(state?.connected && !state?.paused && activeMode());

  const blockedKeywords = () => (activeMode()?.blocked_keywords || [])
    .map((keyword) => String(keyword).trim().toLocaleLowerCase())
    .filter(Boolean);

  const restoreItems = () => {
    document.querySelectorAll(`[${FILTERED_ATTRIBUTE}]`).forEach((item) => {
      item.style.removeProperty('display');
      item.removeAttribute(FILTERED_ATTRIBUTE);
    });
  };

  const scanShorts = () => {
    scanTimer = null;
    document.documentElement.dataset.safescrollFiltering = filteringEnabled() ? 'active' : 'paused';
    if (!filteringEnabled()) {
      restoreItems();
      return;
    }

    const keywords = blockedKeywords();
    if (!keywords.length) {
      restoreItems();
      return;
    }

    document.querySelectorAll('ytd-reel-video-renderer, ytd-rich-item-renderer').forEach((item) => {
      const isShort = item.matches('ytd-reel-video-renderer') || item.querySelector('a[href*="/shorts/"]');
      if (!isShort) return;
      const text = (item.textContent || '').toLocaleLowerCase();
      const blocked = keywords.some((keyword) => text.includes(keyword));
      if (blocked) {
        item.style.setProperty('display', 'none', 'important');
        item.setAttribute(FILTERED_ATTRIBUTE, 'keyword');
      } else if (item.hasAttribute(FILTERED_ATTRIBUTE)) {
        item.style.removeProperty('display');
        item.removeAttribute(FILTERED_ATTRIBUTE);
      }
    });
  };

  const scheduleScan = () => {
    if (scanTimer !== null) return;
    scanTimer = window.setTimeout(scanShorts, 180);
  };

  const refreshState = () => {
    chrome.runtime.sendMessage({ type: 'GET_STATE' }, (response) => {
      if (chrome.runtime.lastError || !response?.ok) return;
      state = response.state;
      scheduleScan();
    });
  };

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === 'CONFIGURATION_UPDATED') {
      if (!state) state = {};
      state.configuration = message.configuration;
      state.connected = true;
      scheduleScan();
    }
    if (message?.type === 'PAUSE_CHANGED') {
      if (!state) state = {};
      state.paused = Boolean(message.paused);
      scheduleScan();
    }
  });

  new MutationObserver(scheduleScan).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
  chrome.runtime.sendMessage({ type: 'YOUTUBE_OPENED' }, (response) => {
    if (response?.ok) state = response.state;
    scheduleScan();
  });
  refreshState();
})();
