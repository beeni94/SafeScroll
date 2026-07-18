"""Sprint 5 one-click extension pairing and connection management APIs."""

from threading import Lock

from flask import current_app, g, request
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy.exc import IntegrityError, OperationalError
from wtforms.validators import ValidationError

from safescroll.api import bp
from safescroll.api.extension import DEVICE_IDENTIFIER, _configuration, _extension_device
from safescroll.api.models import APIToken
from safescroll.api.rate_limit import fixed_window_rate_limit
from safescroll.api.responses import error, success
from safescroll.api.validation import read_json
from safescroll.extension_sync import get_or_create_extension_configuration
from safescroll.extensions import db
from safescroll.models import (
    ExtensionConfiguration,
    ExtensionDevice,
    ExtensionEvent,
    ExtensionPairingToken,
    SyncLog,
    User,
)
from safescroll.utils import utcnow


PAIRING_FIELDS = {
    "pairing_token",
    "device_identifier",
    "device_name",
    "browser",
    "platform",
    "extension_version",
}
_PAIRING_EXCHANGE_LOCK = Lock()


@bp.post("/extension/pair")
def begin_extension_pairing():
    """Create a short-lived credential from an authenticated website session."""

    csrf_error = _validate_website_csrf()
    if csrf_error:
        return csrf_error

    retry_after = fixed_window_rate_limit(
        "pairing-user",
        str(current_user.id),
        limit=current_app.config["PAIRING_RATE_LIMIT_PER_MINUTE"],
        window=60,
    )
    if retry_after is not None:
        return _rate_limited(retry_after)

    payload, response = read_json(optional=True)
    if response:
        return response
    if payload:
        return error(
            "validation_error",
            "The pairing request does not accept additional fields.",
            status=422,
            details={
                "unknown_fields": [
                    f"Unsupported field: {field}" for field in sorted(payload)
                ]
            },
        )

    # Keep only one pending browser-initiated pairing request per account.
    pending = ExtensionPairingToken.query.filter_by(
        user_id=current_user.id, used_at=None, revoked_at=None
    ).all()
    for previous in pending:
        previous.revoke()

    record, raw_token = ExtensionPairingToken.issue(
        current_user,
        lifetime=current_app.config["PAIRING_TOKEN_LIFETIME"],
        created_ip=request.remote_addr,
    )
    db.session.add(record)
    db.session.add(
        ExtensionEvent(
            user_id=current_user.id,
            event_type="pairing",
            status="created",
            message="A one-click extension pairing request was created.",
        )
    )
    db.session.commit()

    api_base_url = current_app.config.get("APP_BASE_URL") or request.host_url.rstrip("/")
    return success(
        {
            "pairing_token": raw_token,
            "expires_at": record.expires_at.isoformat(),
            "api_base_url": api_base_url.rstrip("/"),
        },
        status=201,
    )


@bp.post("/extension/exchange")
def exchange_extension_pairing():
    """Consume a pairing credential once and issue a device-bound API token."""

    retry_after = fixed_window_rate_limit(
        "pairing-exchange",
        request.remote_addr or "unknown",
        limit=current_app.config["PAIRING_EXCHANGE_RATE_LIMIT_PER_MINUTE"],
        window=60,
    )
    if retry_after is not None:
        return _rate_limited(retry_after)

    payload, response = read_json()
    if response:
        return response
    cleaned, errors = _validate_exchange_payload(payload)
    if errors:
        return error(
            "validation_error",
            "The extension pairing payload is invalid.",
            status=422,
            details=errors,
        )

    with _PAIRING_EXCHANGE_LOCK:
        pairing = ExtensionPairingToken.from_raw_token(cleaned["pairing_token"])
        if pairing is None:
            return _invalid_pairing_token()
        pairing = (
            ExtensionPairingToken.query.filter_by(id=pairing.id)
            .with_for_update()
            .one()
        )
        if not pairing.is_valid:
            _pairing_event(
                pairing.user_id,
                status="rejected",
                message="An expired, revoked, or consumed pairing token was rejected.",
                extension_version=cleaned.get("extension_version"),
            )
            db.session.commit()
            return _invalid_pairing_token()

        user = db.session.get(User, pairing.user_id)
        if user is None or not user.is_active:
            pairing.revoke()
            db.session.commit()
            return _invalid_pairing_token()

        try:
            device, device_error = _claim_device(user, cleaned)
            if device_error:
                return device_error

            token, raw_access_token = APIToken.issue(
                user,
                extension_device=device,
                lifetime=current_app.config["API_TOKEN_LIFETIME"],
                label=f"SafeScroll extension on {cleaned.get('device_name', 'Chrome')}",
            )
            db.session.add(token)
            pairing.consume(device)

            configuration = get_or_create_extension_configuration(user)
            configuration.sync_status = "pending"
            _pairing_event(
                user.id,
                device=device,
                status="connected",
                message="The extension exchanged a pairing token successfully.",
                extension_version=device.extension_version,
            )
            db.session.flush()
            db.session.commit()
        except (IntegrityError, OperationalError):
            db.session.rollback()
            return error(
                "pairing_conflict",
                "The extension could not be paired. Generate a new pairing request and retry.",
                status=409,
            )

    return success(
        {
            "access_token": raw_access_token,
            "token_type": "Bearer",
            "expires_at": token.expires_at.isoformat(),
            "user": {
                "id": user.id,
                "name": user.full_name,
                "email": user.email,
            },
            "device": _extension_device(device),
            "configuration": configuration.export_dict(),
        },
        status=201,
    )


@bp.get("/extension/status")
def extension_status():
    configuration = _configuration()
    device = _authenticated_device()
    return success(
        {
            "connected": bool(device and device.is_connected),
            "user": {
                "id": g.api_user.id,
                "name": g.api_user.full_name,
                "email": g.api_user.email,
            },
            "device": _extension_device(device),
            "configuration": configuration.export_dict(),
            "token": {
                "expires_at": g.api_token.expires_at.isoformat(),
                "last_used_at": (
                    g.api_token.last_used_at.isoformat()
                    if g.api_token.last_used_at
                    else None
                ),
            },
        }
    )


@bp.post("/extension/disconnect")
def disconnect_extension():
    device = _authenticated_device()
    if device is None:
        return error(
            "device_not_bound",
            "This access token is not bound to an extension device.",
            status=409,
        )

    configuration = ExtensionConfiguration.query.filter_by(
        user_id=g.api_user.id
    ).first()
    device.revoke()
    remaining = ExtensionDevice.query.filter(
        ExtensionDevice.user_id == g.api_user.id,
        ExtensionDevice.id != device.id,
        ExtensionDevice.revoked_at.is_(None),
    ).first()
    if configuration is not None and remaining is None:
        configuration.sync_status = "disconnected"
    if configuration is not None:
        db.session.add(
            SyncLog(
                user_id=g.api_user.id,
                extension_device_id=device.id,
                config_version=configuration.config_version,
                status="disconnected",
                message="The extension disconnected itself.",
            )
        )
    db.session.add(
        ExtensionEvent(
            user_id=g.api_user.id,
            extension_device_id=device.id,
            event_type="connection",
            status="disconnected",
            message="The extension disconnected itself and revoked its access token.",
            extension_version=device.extension_version,
        )
    )
    db.session.commit()
    return success({"connected": False, "device_id": device.id})


def _claim_device(user, cleaned: dict):
    identifier = cleaned["device_identifier"]
    device = ExtensionDevice.query.filter_by(device_identifier=identifier).first()
    if device is not None and device.user_id != user.id:
        _pairing_event(
            user.id,
            status="rejected",
            message="A device identifier owned by another account was rejected.",
            extension_version=cleaned.get("extension_version"),
        )
        db.session.commit()
        return None, error(
            "device_not_available",
            "This extension device cannot be paired with the current account.",
            status=403,
        )

    if device is None:
        device = ExtensionDevice(
            user_id=user.id,
            device_identifier=identifier,
            name=cleaned.get("device_name", "Chrome extension"),
            browser=cleaned.get("browser", "Chrome"),
            platform=cleaned.get("platform", "Unknown platform"),
        )
        db.session.add(device)
        db.session.flush()
    else:
        # Re-pairing is an explicit user-authorized reconnect. Previously bound
        # credentials remain revoked; a fresh access token is issued below.
        for old_token in device.api_tokens:
            old_token.revoke()
        device.revoked_at = None

    device.name = cleaned.get("device_name", device.name)
    device.browser = cleaned.get("browser", device.browser)
    device.platform = cleaned.get("platform", device.platform)
    device.extension_version = cleaned.get(
        "extension_version", device.extension_version
    )
    device.last_seen_at = utcnow()
    return device, None


def _authenticated_device():
    device_id = getattr(g.api_token, "extension_device_id", None)
    if not device_id:
        return None
    return ExtensionDevice.query.filter_by(
        id=device_id, user_id=g.api_user.id, revoked_at=None
    ).first()


def _validate_exchange_payload(payload: dict):
    errors: dict[str, list[str]] = {}
    cleaned = {}
    unknown = sorted(set(payload) - PAIRING_FIELDS)
    if unknown:
        errors["unknown_fields"] = [
            f"Unsupported field: {field}" for field in unknown
        ]

    pairing_token = payload.get("pairing_token")
    if (
        not isinstance(pairing_token, str)
        or not pairing_token.startswith("sp_")
        or len(pairing_token) > 128
    ):
        errors.setdefault("pairing_token", []).append(
            "Provide the single-use pairing token generated by the website."
        )
    else:
        cleaned["pairing_token"] = pairing_token

    identifier = payload.get("device_identifier")
    if not isinstance(identifier, str) or not DEVICE_IDENTIFIER.fullmatch(
        identifier.strip()
    ):
        errors.setdefault("device_identifier", []).append(
            "Provide an 8 to 128 character extension device identifier."
        )
    else:
        cleaned["device_identifier"] = identifier.strip()

    for field, maximum in (
        ("device_name", 120),
        ("browser", 80),
        ("platform", 80),
        ("extension_version", 32),
    ):
        if field not in payload:
            continue
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            errors.setdefault(field, []).append("Must be a nonempty string.")
        elif len(value.strip()) > maximum:
            errors.setdefault(field, []).append(
                f"Must be at most {maximum} characters."
            )
        else:
            cleaned[field] = value.strip()
    return cleaned, errors


def _validate_website_csrf():
    if not current_app.config.get("WTF_CSRF_ENABLED", True):
        return None
    try:
        validate_csrf(request.headers.get("X-CSRFToken"))
    except ValidationError:
        return error(
            "invalid_csrf_token",
            "Refresh the page and try pairing the extension again.",
            status=400,
        )
    return None


def _pairing_event(
    user_id: int,
    *,
    status: str,
    message: str,
    device=None,
    extension_version: str | None = None,
):
    db.session.add(
        ExtensionEvent(
            user_id=user_id,
            extension_device_id=device.id if device else None,
            event_type="pairing",
            status=status,
            message=message,
            extension_version=extension_version,
        )
    )


def _invalid_pairing_token():
    return error(
        "invalid_pairing_token",
        "The pairing token is invalid, expired, or has already been used.",
        status=401,
    )


def _rate_limited(retry_after: int):
    return error(
        "rate_limited",
        "Too many pairing requests. Try again shortly.",
        status=429,
        details={"retry_after_seconds": retry_after},
        headers={"Retry-After": str(retry_after)},
    )
