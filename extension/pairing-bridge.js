(() => {
  'use strict';

  const sendToPage = (type, details = {}) => {
    window.postMessage(
      { source: 'safescroll-extension', type, ...details },
      window.location.origin,
    );
  };

  window.addEventListener('message', (event) => {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const data = event.data;
    if (!data || data.source !== 'safescroll-web' || data.type !== 'SAFESCROLL_PAIR') return;
    if (typeof data.pairingToken !== 'string' || !data.pairingToken.startsWith('sp_')) {
      sendToPage('SAFESCROLL_PAIR_RESULT', {
        ok: false,
        message: 'The website supplied an invalid pairing request.',
      });
      return;
    }

    chrome.runtime.sendMessage(
      {
        type: 'PAIR_EXTENSION',
        payload: {
          pairingToken: data.pairingToken,
          // Bind the API target to the trusted page origin instead of accepting
          // an arbitrary address from window messaging.
          apiBaseUrl: event.origin,
        },
      },
      (response) => {
        if (chrome.runtime.lastError) {
          sendToPage('SAFESCROLL_PAIR_RESULT', {
            ok: false,
            message: 'SafeScroll could not contact its background service.',
          });
          return;
        }
        sendToPage('SAFESCROLL_PAIR_RESULT', response || {
          ok: false,
          message: 'The pairing request did not complete.',
        });
      },
    );
  });

  const announceReady = () => sendToPage('SAFESCROLL_EXTENSION_READY');
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', announceReady, { once: true });
  } else {
    announceReady();
  }

  // A website mode mutation ends on a fresh modes/dashboard page. This signal
  // makes the extension synchronize immediately after that navigation.
  if (/^\/(modes|dashboard)(\/|$)/.test(window.location.pathname)) {
    window.setTimeout(() => {
      chrome.runtime.sendMessage({ type: 'CONFIGURATION_CHANGED' });
    }, 400);
  }
})();
