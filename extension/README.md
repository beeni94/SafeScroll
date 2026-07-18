# SafeScroll Chrome extension

This directory contains the Manifest V3 client delivered in Sprint 5.

## Load it locally

1. Start the Flask application at `http://127.0.0.1:5000`.
2. Open `chrome://extensions`, enable **Developer mode**, select **Load unpacked**, and choose this `extension` directory.
3. Copy the generated extension ID, set `API_CORS_ORIGINS=chrome-extension://<extension-id>` in the project `.env`, and restart Flask.
4. Sign in to SafeScroll, open `/extension`, and select **Connect extension**.

The access token is stored in extension-owned `chrome.storage.local`, persists
across browser restarts, and is bound to the installation identifier. The
server stores only its SHA-256 digest. Production API targets must use HTTPS.

For a production hostname other than `https://safescroll.app`, update the
manifest `host_permissions` and pairing-bridge `matches`, then add the exact
`chrome-extension://<extension-id>` origin to `API_CORS_ORIGINS`.
