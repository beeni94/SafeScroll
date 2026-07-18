(() => {
  'use strict';

  const context = document.querySelector('[data-extension-pairing]');
  if (!context) return;

  const buttons = [...context.querySelectorAll('[data-extension-pair-button]')];
  const feedback = context.querySelector('[data-pairing-feedback]');
  const message = context.querySelector('[data-pairing-message]');
  let extensionDetected = false;
  let responseTimer = null;

  const setState = (state, text) => {
    feedback?.setAttribute('data-state', state);
    if (message) message.textContent = text;
    buttons.forEach((button) => {
      button.disabled = state === 'working';
      button.setAttribute('aria-busy', String(state === 'working'));
    });
  };

  const pairingMessage = (data) => {
    window.postMessage(
      {
        source: 'safescroll-web',
        type: 'SAFESCROLL_PAIR',
        pairingToken: data.pairing_token,
        apiBaseUrl: data.api_base_url,
        expiresAt: data.expires_at,
      },
      window.location.origin,
    );
  };

  const beginPairing = async () => {
    setState('working', extensionDetected
      ? 'Creating a secure pairing request…'
      : 'Looking for the SafeScroll extension…');
    try {
      const response = await fetch(context.dataset.pairUrl || '/api/extension/pair', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': context.dataset.csrfToken || '',
        },
        body: '{}',
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload?.error?.message || 'SafeScroll could not start pairing.');
      }

      pairingMessage(payload.data);
      setState('working', 'Pairing request sent. Approving this browser in Chrome…');
      window.clearTimeout(responseTimer);
      responseTimer = window.setTimeout(() => {
        setState(
          'error',
          'The extension did not respond. Install or enable it, refresh this page, and try again.',
        );
      }, 12000);
    } catch (error) {
      setState('error', error instanceof Error ? error.message : 'Pairing failed. Try again.');
    }
  };

  buttons.forEach((button) => button.addEventListener('click', beginPairing));

  window.addEventListener('message', (event) => {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const data = event.data;
    if (!data || data.source !== 'safescroll-extension') return;

    if (data.type === 'SAFESCROLL_EXTENSION_READY') {
      extensionDetected = true;
      setState('ready', 'SafeScroll extension detected. Ready for secure one-click pairing.');
      return;
    }

    if (data.type !== 'SAFESCROLL_PAIR_RESULT') return;
    window.clearTimeout(responseTimer);
    if (data.ok) {
      setState('success', 'Connected successfully. Loading the synchronized browser details…');
      window.setTimeout(() => window.location.reload(), 900);
    } else {
      setState('error', data.message || 'The extension could not complete pairing.');
    }
  });
})();
