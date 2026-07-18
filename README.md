# SafeScroll Web Application

SafeScroll is a Flask account and control panel for personalized YouTube Shorts viewing modes. It provides registration and login, password recovery, profiles, mode management, device and session controls, a user-specific dashboard, a versioned REST API backed by SQLite, and an installable Manifest V3 Chrome extension.

The included Chrome extension pairs with the website, persists its device-bound session, synchronizes configuration automatically, exposes popup controls, and applies blocked-keyword filtering to YouTube Shorts. OCR and frame-analysis remain future filtering enhancements.

## Requirements

- Windows 10 or 11
- Python 3.11 or newer recommended
- PowerShell

No Node.js or frontend build step is required. Flask serves the existing CSS and JavaScript assets directly.

## Windows setup

From the repository directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Generate a development secret:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the printed value into `SECRET_KEY` in `.env`. Never commit `.env` or use the example secret in a deployed environment.

If PowerShell blocks virtual-environment activation, either allow scripts for the current process or call the environment's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

## Run locally

The simplest development command is:

```powershell
python run.py
```

The equivalent Flask CLI command is:

```powershell
python -m flask --app run.py run --debug
```

Open <http://127.0.0.1:5000>. The default `DATABASE_URL` uses a local SQLite database. Runtime database files and secrets are ignored by Git.

In development, missing tables are created automatically on first startup. You can also initialize them explicitly:

```powershell
python -m flask --app run.py init-db
```

Set `AUTO_CREATE_DATABASE=false` outside local development and run `init-db` as a deployment step.

When upgrading from the first Flask build, startup or `init-db` safely copies legacy `viewing_modes` records into the normalized `modes`, `categories`, `keywords`, and `mode_schedules` tables. The compatibility copy is idempotent and retains the legacy table as a fallback.

## Mode management

Each mode belongs to one user and stores its name, description, icon, color, strictness, preferred and blocked categories, preferred and blocked keywords, optional schedule, and protection settings. Categories, keywords, and schedule days are normalized into their own tables.

Users can create, view, edit, duplicate, delete, and activate modes from `/modes`. Only one mode can be active per account. Activating another mode updates the recently used timestamp. A protected mode requires its hashed 4-8 digit PIN before it can be edited, duplicated, deleted, or activated; successful browser unlocks last five minutes. PIN verification is throttled per mode and authenticated browser session or API token, with five attempts and a 15-minute lockout by default.

The dashboard displays the active mode, total mode count, recently used mode, and a quick mode switch alongside the Create Mode action.

## Sprint 4: Extension management and REST API

Sprint 4 is complete. The website includes the authenticated extension
management page, device disconnection, revocable expiring API tokens,
user-scoped mode and activation endpoints, extension configuration sync,
configuration versioning, validation, ownership enforcement, rate limiting,
and secure JSON errors. One-click browser pairing was deferred from this
foundation and is now delivered by Sprint 5.

The extension management page at `/extension` shows connection status, connected Chrome installations, last synchronization time, configuration version, per-device disconnect controls, installation guidance, and live one-click pairing status.

The API uses revocable bearer tokens and never accepts a signed-in browser session as API authentication. Raw tokens are displayed once and only their SHA-256 digest is stored. Tokens expire, can be revoked, and become invalid when their bound extension device is disconnected.

Create a token for an active account:

```powershell
python -m flask --app run.py api issue-token --email user@example.com --label "Chrome extension" --days 90
```

Store the printed `ss_...` value securely. Revoke it later using its displayed prefix:

```powershell
python -m flask --app run.py api revoke-token --prefix ss_exampleprefix
```

Send it in every request:

```http
Authorization: Bearer ss_your_token_here
Content-Type: application/json
```

Available endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/modes/active` | Get the user's complete active-mode configuration |
| `GET` | `/api/modes` | Get all modes owned by the user |
| `GET` | `/api/modes/<id>` | Get one owned mode |
| `POST` | `/api/modes` | Create a mode |
| `PATCH` / `PUT` | `/api/modes/<id>` | Update a mode |
| `DELETE` | `/api/modes/<id>` | Delete a mode |
| `POST` | `/api/modes/<id>/activate` | Make one owned mode active |
| `GET` | `/api/extension/config` | Fetch the versioned configuration and mode snapshot |
| `POST` | `/api/extension/sync` | Identify/bind a device and record synchronization |
| `POST` | `/api/extension/pair` | Create a five-minute single-use pairing credential from the signed-in website |
| `POST` | `/api/extension/exchange` | Exchange a pairing credential once for a device-bound access token |
| `GET` | `/api/extension/status` | Read the authenticated extension, user, token, and synchronization status |
| `POST` | `/api/extension/disconnect` | Disconnect the calling extension and revoke its bound credentials |

The existing `/api/v1/...` routes remain available for compatibility. The shorter `/api/...` prefix uses the same authentication, ownership checks, CORS policy, validation, secure JSON errors, and per-token rate limit.

Example creation payload:

```json
{
  "name": "Study",
  "description": "Prioritize learning content",
  "preferred_categories": ["Education", "Programming"],
  "blocked_categories": ["Gaming"],
  "preferred_keywords": ["tutorial", "lecture"],
  "blocked_keywords": ["prank"],
  "strictness": 4,
  "color": "#14B8A6",
  "icon": "📚",
  "schedule": {
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "start": "08:00",
    "end": "14:00"
  }
}
```

For protected mode mutations, include the current PIN as `"pin"`. Set or replace protection with `"is_protected": true` and `"protection_pin": "2468"`. Configure `API_CORS_ORIGINS` as a comma-separated exact allowlist of extension origins such as `chrome-extension://<extension-id>`; wildcards are intentionally unsupported.

An extension sync request uses an installation identifier and the last configuration version it has applied:

```json
{
  "device_identifier": "chrome-install-550e8400",
  "device_name": "Study laptop",
  "browser": "Chrome 140",
  "platform": "Windows",
  "extension_version": "1.2.0",
  "config_version": 12
}
```

The first authenticated sync binds an unbound token to that user-owned device. Installation identifiers are globally unique, and later token mismatches or cross-user claims are rejected. The server returns `update_required: true` whenever the client version differs; an exact match records the configuration as synchronized. Mode creation, editing, deletion, activation, and nested category/keyword/schedule changes atomically increment the user's `config_version` once per transaction.

API traffic defaults to 120 requests per 60-second window per token. Configure this with `API_RATE_LIMIT_PER_MINUTE` and `API_RATE_LIMIT_WINDOW_SECONDS`. The included in-memory limiter is suitable for the current single-process deployment; use a shared limiter backend such as Redis before running multiple production workers.

The extension data layer uses `ExtensionDevice`, `ApiToken` (while retaining the established `APIToken` import), `SyncLog`, `ExtensionConfiguration`, `ExtensionPairingToken`, and `ExtensionEvent`. Every record is linked to its authenticated user. Existing extension-flavoured device rows and bearer tokens are upgraded idempotently on startup or through `init-db`.

## Sprint 5: One-click pairing and Chrome client

Sprint 5 is implemented in the `extension/` directory as a Chrome Manifest V3 extension. Pairing starts on the signed-in `/extension` page. The website creates a high-entropy credential that expires after five minutes; the extension consumes it once, receives a device-bound access token, and immediately synchronizes. The raw pairing and access tokens are never stored in the server database.

The background service synchronizes at extension startup, when Chrome restarts, when YouTube opens, after a website mode-change navigation, when the popup Sync button is selected, and every five minutes. `chrome.storage.local` retains the device identifier, user identity, device-bound token, configuration, and pause state across service-worker and browser restarts. The popup shows the connected account, active mode, connection state, last synchronization time, manual sync, pause/resume, dashboard, and disconnect controls.

Pairing, connection, disconnection, successful synchronization, rejected device checks, and authenticated synchronization errors are recorded without request secrets. Production API targets require HTTPS; plain HTTP is accepted only for `localhost` and `127.0.0.1` by the extension.

To load the extension locally:

1. Start SafeScroll at `http://127.0.0.1:5000`.
2. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select the repository's `extension` directory.
3. Copy the generated extension ID, set `API_CORS_ORIGINS=chrome-extension://<extension-id>` in `.env`, and restart Flask.
4. Sign in, open `/extension`, and select **Connect extension**.

For a production hostname other than `https://safescroll.app`, update the exact `host_permissions` and content-script match in `extension/manifest.json`, set `APP_BASE_URL`, and configure the exact Chrome extension origin. Wildcard API origins remain unsupported.

## Run tests

The tests create a fresh in-memory SQLite database and do not modify the development database:

```powershell
python -m pytest -q
```

To run one area while developing:

```powershell
python -m pytest tests/test_auth.py -q
python -m pytest tests/test_reset.py -q
python -m pytest tests/test_isolation.py -q
```

To verify the complete Sprint 4 contract:

```powershell
python -m pytest -q tests/test_api.py tests/test_extension_api.py tests/test_extension_models.py tests/test_extension_page.py
```

To verify the Sprint 5 pairing contract:

```powershell
python -m pytest -q tests/test_pairing_api.py tests/test_csrf_flow.py
```

Functional tests disable CSRF so they can focus on route behavior. A separate test starts the app with CSRF enabled and verifies that an untrusted POST is rejected.

## Password-reset email and SMTP

Password-reset requests intentionally return the same public response whether or not an email exists. This avoids revealing registered addresses.

For SMTP delivery, configure these values in `.env`:

```dotenv
APP_BASE_URL=https://your-domain.example
MAIL_SERVER=smtp.example.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=your-smtp-user
MAIL_PASSWORD=your-smtp-password
MAIL_DEFAULT_SENDER=noreply@your-domain.example
```

Keep `MAIL_PASSWORD` out of source control. If `MAIL_SERVER` is blank, development requests are recorded in the application's in-memory test outbox but no email is delivered. Configure SMTP when manually exercising the complete email flow. For production, use HTTPS, set `APP_ENV=production`, set `FLASK_DEBUG=0`, set `SESSION_COOKIE_SECURE=true`, provide a strong `SECRET_KEY`, and configure a working SMTP account.

To enable the Chrome Web Store button when a listing is available, set `EXTENSION_INSTALL_URL` in `.env`. OCR and frame analysis remain outside the current keyword-filtering client.

## Main routes

- `/register`, `/login`, `/logout`
- `/forgot-password`, `/reset-password/<token>`
- `/dashboard`, `/analytics`, `/extension`
- `/profile`, `/security`
- `/modes`, `/modes/new`, `/modes/<id>/edit`
- `/devices`
- `/api/modes`, `/api/modes/active`, `/api/extension/config`, `/api/extension/sync`
- `/api/v1/...` compatibility routes

Dashboard, account, mode, device, analytics, extension, and security routes require authentication. State-changing operations use POST requests and CSRF protection. Protected modes hash their PINs and require a short-lived PIN unlock before editing, activating, duplicating, or deleting them.

## Troubleshooting

- **`py` is not recognized:** install Python from python.org and enable the installer option that adds the launcher to PATH.
- **Database schema errors after model changes:** remove only the local development database under `instance` and restart. Do not do this to a production database.
- **Reset email is not delivered:** verify SMTP host, port, TLS setting, credentials, sender authorization, and spam filtering.
- **Tests import the wrong app:** run pytest from the repository root with the virtual environment active.
