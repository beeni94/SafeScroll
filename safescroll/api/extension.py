"""Extension configuration and synchronization endpoints."""

import re
from contextlib import contextmanager
from threading import Lock

from flask import current_app, g, request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError

from safescroll.api import bp
from safescroll.api.responses import error, success
from safescroll.api.validation import read_json
from safescroll.extensions import db
from safescroll.models import ViewingMode
from safescroll.utils import utcnow


DEVICE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
SYNC_FIELDS = {
    "device_identifier",
    "device_name",
    "browser",
    "platform",
    "extension_version",
    "config_version",
}
_FIRST_SYNC_LOCK = Lock()


@bp.get("/extension/config")
def extension_config():
    configuration = _configuration()
    bound_device, device_error = _bound_device()
    if device_error:
        return device_error
    return success(_configuration_payload(configuration, bound_device))


@bp.post("/extension/sync")
def sync_extension():
    payload, response = read_json()
    if response:
        _record_request_error("The extension sent an invalid sync request.")
        return response
    cleaned, errors = _validate_sync_payload(payload)
    if errors:
        _record_request_error("The extension sync payload failed validation.")
        return _validation_error(errors)

    # Serialize the one-time token-to-device binding in this process, and take
    # a row lock on databases that support SELECT FOR UPDATE. SQLite ignores
    # the row-lock hint but is still protected within the application process.
    with _first_sync_guard():
        return _perform_sync(cleaned)


def _perform_sync(cleaned: dict):
    configuration = _configuration()
    try:
        device, device_error = _resolve_sync_device(cleaned, configuration)
    except IntegrityError:
        db.session.rollback()
        return error(
            "device_binding_conflict",
            "The extension device could not be bound. Retry the request.",
            status=409,
        )
    if device_error:
        return device_error

    server_version = configuration.config_version
    client_version = cleaned.get("config_version")
    update_required = (
        client_version is None or client_version != server_version
    )
    sync_status = "update_required" if update_required else "synced"
    now = utcnow()
    device.name = cleaned.get("device_name", device.name)
    device.browser = cleaned.get("browser", device.browser)
    device.platform = cleaned.get("platform", device.platform)
    device.extension_version = cleaned.get(
        "extension_version", device.extension_version
    )
    device.last_seen_at = now
    device.last_sync_at = now

    active_mode = ViewingMode.query.filter_by(
        user_id=g.api_user.id, is_active=True
    ).first()
    active_mode_id = active_mode.id if active_mode else None

    from safescroll.models import ExtensionEvent, SyncLog

    db.session.add(
        SyncLog(
            user_id=g.api_user.id,
            extension_device_id=device.id,
            config_version=server_version,
            status=sync_status,
        )
    )
    db.session.add(
        ExtensionEvent(
            user_id=g.api_user.id,
            extension_device_id=device.id,
            event_type="sync",
            status=sync_status,
            message="Extension configuration synchronization completed.",
            extension_version=device.extension_version,
        )
    )
    try:
        db.session.flush()
        if not _conditionally_store_sync_state(
            configuration,
            expected_version=server_version,
            active_mode_id=active_mode_id,
            synced_at=now,
            sync_status=sync_status,
        ):
            db.session.rollback()
            return error(
                "configuration_changed",
                "The extension configuration changed during synchronization. Retry the request.",
                status=409,
            )
        db.session.commit()
    except (IntegrityError, OperationalError):
        db.session.rollback()
        return error(
            "sync_conflict",
            "The extension configuration changed during synchronization. Retry the request.",
            status=409,
        )

    data = _configuration_payload(configuration, device)
    # A new mode change may land immediately after our successful sync commit.
    # Recompute against the version serialized in the response so the boolean
    # and payload cannot contradict one another.
    response_version = data["configuration"]["config_version"]
    data["update_required"] = (
        client_version is None or client_version != response_version
    )
    return success(data)


def _conditionally_store_sync_state(
    configuration,
    *,
    expected_version: int,
    active_mode_id: int | None,
    synced_at,
    sync_status: str,
) -> bool:
    """Persist sync state only if the compared configuration is still current."""

    from safescroll.models import ExtensionConfiguration

    result = db.session.execute(
        update(ExtensionConfiguration)
        .where(
            ExtensionConfiguration.id == configuration.id,
            ExtensionConfiguration.config_version == expected_version,
        )
        .values(
            active_mode_id=active_mode_id,
            last_sync_at=synced_at,
            sync_status=sync_status,
            updated_at=synced_at,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


@contextmanager
def _first_sync_guard():
    should_lock = getattr(g.api_token, "extension_device_id", None) is None
    if not should_lock:
        yield
        return

    _FIRST_SYNC_LOCK.acquire()
    try:
        # Another request may have completed the binding while this one waited.
        # The lock hint prevents the equivalent race across workers on database
        # engines that implement SELECT FOR UPDATE.
        db.session.refresh(g.api_token, with_for_update=True)
        yield
    finally:
        _FIRST_SYNC_LOCK.release()


def _configuration():
    from safescroll.extension_sync import get_or_create_extension_configuration
    from safescroll.models import ExtensionConfiguration

    configuration = get_or_create_extension_configuration(g.api_user)
    if configuration.id is None:
        # Normally created with the account; this covers pre-migration rows and
        # ensures a read-only config request does not lose the new row at
        # request teardown.
        db.session.commit()

    # The server's active mode is authoritative. Normally the model event hooks
    # keep this value current; atomically repair data created before versioning
    # and advance the version so a client cannot mistake that repair for an
    # already-applied configuration.
    active_mode = ViewingMode.query.filter_by(
        user_id=g.api_user.id, is_active=True
    ).first()
    active_id = active_mode.id if active_mode else None
    if configuration.active_mode_id != active_id:
        expected_version = configuration.config_version
        repaired_at = utcnow()
        result = db.session.execute(
            update(ExtensionConfiguration)
            .where(
                ExtensionConfiguration.id == configuration.id,
                ExtensionConfiguration.config_version == expected_version,
            )
            .values(
                config_version=expected_version + 1,
                active_mode_id=active_id,
                sync_status="pending",
                updated_at=repaired_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            db.session.commit()
        else:
            # A concurrent mode mutation won the version check. Discard this
            # stale snapshot and use the configuration it committed.
            db.session.rollback()
        configuration = ExtensionConfiguration.query.filter_by(
            user_id=g.api_user.id
        ).one()
    return configuration


def _bound_device():
    from safescroll.models import ExtensionDevice

    device_id = getattr(g.api_token, "extension_device_id", None)
    if not device_id:
        return None, None
    device = ExtensionDevice.query.filter_by(
        id=device_id, user_id=g.api_user.id
    ).first()
    if device is None or not device.is_connected:
        return None, error(
            "device_disconnected",
            "This extension device is no longer connected.",
            status=403,
        )
    return device, None


def _resolve_sync_device(cleaned: dict, configuration):
    from safescroll.models import ExtensionDevice

    identifier = cleaned["device_identifier"]
    bound_device, device_error = _bound_device()
    if device_error:
        return None, device_error
    if bound_device is not None:
        if bound_device.device_identifier != identifier:
            _record_rejected_sync(configuration, bound_device)
            current_app.logger.warning(
                "Rejected extension device mismatch for API token id=%s user_id=%s",
                g.api_token.id,
                g.api_user.id,
            )
            return None, error(
                "device_mismatch",
                "The bearer token is bound to a different extension device.",
                status=403,
            )
        return bound_device, None

    # Identifiers are never transferable between accounts. Return the same
    # generic response for an existing foreign identifier to avoid enumeration.
    device = ExtensionDevice.query.filter_by(
        user_id=g.api_user.id, device_identifier=identifier
    ).first()
    foreign_device = None
    if device is None:
        foreign_device = ExtensionDevice.query.filter(
            ExtensionDevice.device_identifier == identifier,
            ExtensionDevice.user_id != g.api_user.id,
        ).first()
    if foreign_device is not None:
        _record_rejected_sync(configuration, None)
        current_app.logger.warning(
            "Rejected cross-user extension identifier for API token id=%s user_id=%s",
            g.api_token.id,
            g.api_user.id,
        )
        return None, error(
            "device_not_available",
            "This extension device cannot be used with the current credential.",
            status=403,
        )
    if device is not None and not device.is_connected:
        return None, error(
            "device_disconnected",
            "This extension device is no longer connected.",
            status=403,
        )
    if device is None:
        device = ExtensionDevice(
            user_id=g.api_user.id,
            device_identifier=identifier,
            name=cleaned.get("device_name", "Chrome extension"),
            browser=cleaned.get("browser", "Chrome"),
            platform=cleaned.get("platform", "Unknown platform"),
        )
        db.session.add(device)
        db.session.flush()

    # Older databases/tokens remain usable until the schema migration adds the
    # binding column; current installations persist the association here.
    if hasattr(g.api_token, "extension_device_id"):
        g.api_token.extension_device_id = device.id
    return device, None


def _record_rejected_sync(configuration, device) -> None:
    from safescroll.models import ExtensionEvent, SyncLog

    db.session.add(
        SyncLog(
            user_id=g.api_user.id,
            extension_device_id=device.id if device else None,
            config_version=configuration.config_version,
            status="rejected",
            message="The extension sync request failed device verification.",
        )
    )
    db.session.add(
        ExtensionEvent(
            user_id=g.api_user.id,
            extension_device_id=device.id if device else None,
            event_type="sync",
            status="rejected",
            message="The extension sync request failed device verification.",
            extension_version=device.extension_version if device else None,
        )
    )
    db.session.commit()


def _record_request_error(message: str) -> None:
    """Persist authenticated sync errors without retaining request secrets."""

    from safescroll.models import ExtensionDevice, ExtensionEvent

    device = None
    device_id = getattr(g.api_token, "extension_device_id", None)
    if device_id:
        device = ExtensionDevice.query.filter_by(
            id=device_id, user_id=g.api_user.id
        ).first()
    db.session.add(
        ExtensionEvent(
            user_id=g.api_user.id,
            extension_device_id=device.id if device else None,
            event_type="sync",
            status="error",
            message=message,
            extension_version=device.extension_version if device else None,
        )
    )
    db.session.commit()


def _validate_sync_payload(payload: dict):
    errors: dict[str, list[str]] = {}
    cleaned = {}
    unknown = sorted(set(payload) - SYNC_FIELDS)
    if unknown:
        errors["unknown_fields"] = [
            f"Unsupported field: {field}" for field in unknown
        ]

    body_identifier = payload.get("device_identifier")
    header_identifier = request.headers.get("X-Device-ID")
    if body_identifier is not None and header_identifier:
        if str(body_identifier).strip() != header_identifier.strip():
            errors.setdefault("device_identifier", []).append(
                "The JSON and X-Device-ID values must match."
            )
    identifier = body_identifier if body_identifier is not None else header_identifier
    if not isinstance(identifier, str) or not DEVICE_IDENTIFIER.fullmatch(identifier.strip()):
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

    if "config_version" in payload:
        version = payload["config_version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            errors.setdefault("config_version", []).append(
                "Must be a nonnegative integer."
            )
        else:
            cleaned["config_version"] = version
    return cleaned, errors


def _configuration_payload(configuration, device) -> dict:
    modes = (
        ViewingMode.query.filter_by(user_id=g.api_user.id)
        .order_by(ViewingMode.is_active.desc(), ViewingMode.created_at.asc())
        .all()
    )
    active_mode = next((mode for mode in modes if mode.is_active), None)
    return {
        "configuration": {
            "config_version": configuration.config_version,
            "active_mode_id": active_mode.id if active_mode else None,
            "last_sync_time": (
                configuration.last_sync_at.isoformat()
                if configuration.last_sync_at
                else None
            ),
            "sync_status": configuration.sync_status,
        },
        "active_mode": _extension_mode(active_mode),
        "modes": [_extension_mode(mode) for mode in modes],
        "device": _extension_device(device),
    }


def _extension_mode(mode) -> dict | None:
    if mode is None:
        return None
    return {
        "id": mode.id,
        "name": mode.name,
        "icon": mode.icon,
        "color": mode.color,
        "description": mode.description,
        "preferred_categories": mode.preferred_categories,
        "blocked_categories": mode.blocked_categories,
        "preferred_keywords": mode.preferred_keywords,
        "blocked_keywords": mode.blocked_keywords,
        "strictness": mode.strictness,
        "is_protected": mode.is_protected,
        "is_active": mode.is_active,
        "schedule": mode.schedule,
        "updated_at": mode.updated_at.isoformat() if mode.updated_at else None,
    }


def _extension_device(device) -> dict | None:
    if device is None:
        return None
    return {
        "id": device.id,
        "device_identifier": device.device_identifier,
        "name": device.name,
        "browser": device.browser,
        "platform": device.platform,
        "extension_version": device.extension_version,
        "connected": device.is_connected,
        "last_seen_at": (
            device.last_seen_at.isoformat() if device.last_seen_at else None
        ),
        "last_sync_time": (
            device.last_sync_at.isoformat() if device.last_sync_at else None
        ),
    }


def _validation_error(details: dict):
    return error(
        "validation_error",
        "The extension sync payload is invalid.",
        status=422,
        details=details,
    )
