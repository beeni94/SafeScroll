from copy import deepcopy

from flask import g, request, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from safescroll.api import bp
from safescroll.api.auth import authenticate_request, configured_origins
from safescroll.api.responses import error, success
from safescroll.api.validation import read_json, validate_mode_payload
from safescroll.extensions import db
from safescroll.models import ViewingMode
from safescroll.pin_security import (
    clear_pin_failures,
    pin_principal,
    pin_retry_after,
    record_pin_failure,
)
from safescroll.utils import utcnow


MUTABLE_MODE_FIELDS = {
    "name",
    "icon",
    "description",
    "preferred_categories",
    "blocked_categories",
    "preferred_keywords",
    "blocked_keywords",
    "strictness",
    "schedule",
    "color",
    "is_protected",
}


@bp.before_request
def require_bearer_token():
    return authenticate_request()


@bp.after_request
def add_api_headers(response):
    response.headers["Cache-Control"] = "no-store"
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") in configured_origins():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, X-Device-ID"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PATCH, PUT, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Max-Age"] = "600"
        response.headers["Access-Control-Expose-Headers"] = "Location"
        response.vary.add("Origin")
    return response


@bp.get("/modes")
def list_modes():
    modes = (
        ViewingMode.query.filter_by(user_id=g.api_user.id)
        .order_by(ViewingMode.is_active.desc(), ViewingMode.created_at.asc())
        .all()
    )
    return success(
        {"modes": [_serialize_mode(mode) for mode in modes]},
        meta={"count": len(modes)},
    )


@bp.get("/modes/active")
def active_mode():
    mode = ViewingMode.query.filter_by(user_id=g.api_user.id, is_active=True).first()
    return success({"mode": _serialize_mode(mode) if mode else None})


@bp.post("/modes")
def create_mode():
    payload, response = read_json()
    if response:
        return response
    cleaned, errors = validate_mode_payload(payload, require_name=True, creating=True)
    if errors:
        return _validation_error(errors)
    if _name_exists(cleaned["name"]):
        return _name_conflict()

    mode = ViewingMode(
        user_id=g.api_user.id,
        name=cleaned["name"],
        icon=cleaned.get("icon", "🎯"),
        description=cleaned.get("description", ""),
        strictness=cleaned.get("strictness", 3),
        is_protected=cleaned.get("is_protected", False),
        is_active=not ViewingMode.query.filter_by(user_id=g.api_user.id).first(),
    )
    if mode.is_active:
        mode.last_used_at = utcnow()
    _apply_mode_values(mode, cleaned, creating=True)
    db.session.add(mode)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _name_conflict()
    except ValueError as exc:
        db.session.rollback()
        return _validation_error({"body": [str(exc)]})

    return success(
        {"mode": _serialize_mode(mode)},
        status=201,
        headers={"Location": url_for("api.get_mode", mode_id=mode.id)},
    )


@bp.get("/modes/<int:mode_id>")
def get_mode(mode_id: int):
    mode = _owned_mode(mode_id)
    if mode is None:
        return _mode_not_found()
    return success({"mode": _serialize_mode(mode)})


@bp.route("/modes/<int:mode_id>", methods=["PATCH", "PUT"])
def update_mode(mode_id: int):
    mode = _owned_mode(mode_id)
    if mode is None:
        return _mode_not_found()
    payload, response = read_json()
    if response:
        return response
    cleaned, errors = validate_mode_payload(
        payload,
        require_name=request.method == "PUT",
    )
    if errors:
        return _validation_error(errors)
    security_errors = _update_security_errors(mode, cleaned)
    if security_errors:
        return _validation_error(security_errors)
    authorization_error = _authorize_protected_mode(mode, cleaned)
    if authorization_error:
        return authorization_error

    changes = set(cleaned) & (MUTABLE_MODE_FIELDS | {"protection_pin"})
    if not changes:
        return _validation_error({"body": ["Provide at least one mode field to update."]})

    will_be_protected = cleaned.get("is_protected", mode.is_protected)
    if will_be_protected and not mode.is_protected and "protection_pin" not in cleaned:
        return _validation_error(
            {"protection_pin": ["A protection PIN is required when enabling protection."]}
        )
    if "name" in cleaned and _name_exists(cleaned["name"], excluding_id=mode.id):
        return _name_conflict()

    try:
        _apply_mode_values(mode, cleaned, creating=False)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _name_conflict()
    except ValueError as exc:
        db.session.rollback()
        return _validation_error({"body": [str(exc)]})
    return success({"mode": _serialize_mode(mode)})


@bp.delete("/modes/<int:mode_id>")
def delete_mode(mode_id: int):
    mode = _owned_mode(mode_id)
    if mode is None:
        return _mode_not_found()
    payload, response = read_json(optional=True)
    if response:
        return response
    action_errors = _action_payload_errors(payload)
    if action_errors:
        return _validation_error(action_errors)
    cleaned, errors = validate_mode_payload(payload, require_name=False)
    if errors:
        return _validation_error(errors)
    authorization_error = _authorize_protected_mode(mode, cleaned)
    if authorization_error:
        return authorization_error

    deleted_id = mode.id
    was_active = mode.is_active
    db.session.delete(mode)
    db.session.flush()
    replacement = None
    if was_active:
        replacement = (
            ViewingMode.query.filter_by(user_id=g.api_user.id, is_protected=False)
            .order_by(ViewingMode.created_at.asc())
            .first()
        )
        if replacement:
            replacement.is_active = True
            replacement.last_used_at = utcnow()
    db.session.commit()
    return success(
        {
            "deleted": True,
            "id": deleted_id,
            "active_mode_id": replacement.id if replacement else None,
        }
    )


@bp.post("/modes/<int:mode_id>/activate")
def activate_mode(mode_id: int):
    mode = _owned_mode(mode_id)
    if mode is None:
        return _mode_not_found()
    payload, response = read_json(optional=True)
    if response:
        return response
    action_errors = _action_payload_errors(payload)
    if action_errors:
        return _validation_error(action_errors)
    cleaned, errors = validate_mode_payload(payload, require_name=False)
    if errors:
        return _validation_error(errors)
    authorization_error = _authorize_protected_mode(mode, cleaned)
    if authorization_error:
        return authorization_error

    if not mode.is_active:
        ViewingMode.query.filter_by(user_id=g.api_user.id, is_active=True).update(
            {"is_active": False}, synchronize_session="fetch"
        )
        db.session.flush()
        mode.is_active = True
        mode.last_used_at = utcnow()
        db.session.commit()
    return success({"mode": _serialize_mode(mode)})


def _owned_mode(mode_id: int):
    return ViewingMode.query.filter_by(id=mode_id, user_id=g.api_user.id).first()


def _name_exists(name: str, *, excluding_id: int | None = None) -> bool:
    query = ViewingMode.query.filter(
        ViewingMode.user_id == g.api_user.id,
        func.lower(ViewingMode.name) == name.casefold(),
    )
    if excluding_id is not None:
        query = query.filter(ViewingMode.id != excluding_id)
    return query.first() is not None


def _authorize_protected_mode(mode: ViewingMode, cleaned: dict):
    if not mode.is_protected:
        return None
    principal = pin_principal("api-token", g.api_token.id)
    retry_after = pin_retry_after(mode.id, principal)
    if retry_after is not None:
        return _pin_rate_limited(retry_after)
    pin = cleaned.get("pin")
    if pin is None:
        return error(
            "protection_pin_required",
            "This mode requires its current protection PIN.",
            status=403,
        )
    if not mode.check_protection_pin(pin):
        retry_after = record_pin_failure(mode.id, principal)
        if retry_after is not None:
            return _pin_rate_limited(retry_after)
        return error(
            "invalid_protection_pin",
            "The protection PIN is incorrect.",
            status=403,
        )
    clear_pin_failures(mode.id, principal)
    return None


def _update_security_errors(mode: ViewingMode, cleaned: dict) -> dict:
    errors = {}
    resulting_protection = cleaned.get("is_protected", mode.is_protected)
    if "pin" in cleaned and not mode.is_protected:
        errors["pin"] = ["A current PIN is only valid for an already protected mode."]
    if "protection_pin" in cleaned and not resulting_protection:
        errors["protection_pin"] = [
            "A new protection PIN requires is_protected to remain enabled."
        ]
    return errors


def _action_payload_errors(payload: dict) -> dict:
    unsupported = sorted(set(payload) - {"pin"})
    if not unsupported:
        return {}
    return {
        "unknown_fields": [f"Unsupported field: {field}" for field in unsupported]
    }


def _apply_mode_values(mode: ViewingMode, cleaned: dict, *, creating: bool) -> None:
    defaults = {
        "preferred_categories": [],
        "blocked_categories": [],
        "preferred_keywords": [],
        "blocked_keywords": [],
        "schedule": {},
        "color": "#6366f1",
    }
    for field in MUTABLE_MODE_FIELDS - {"is_protected"}:
        if field in cleaned:
            setattr(mode, field, deepcopy(cleaned[field]))
        elif creating and field in defaults:
            setattr(mode, field, deepcopy(defaults[field]))

    resulting_protection = cleaned.get("is_protected", mode.is_protected)
    mode.is_protected = resulting_protection
    if not resulting_protection:
        mode.protection_pin_hash = None
    elif "protection_pin" in cleaned:
        mode.set_protection_pin(cleaned["protection_pin"])


def _serialize_mode(mode: ViewingMode) -> dict:
    data = mode.export_dict()
    data.setdefault("color", getattr(mode, "color", "#6366f1"))
    data.setdefault("created_at", mode.created_at.isoformat() if mode.created_at else None)
    data.setdefault("updated_at", mode.updated_at.isoformat() if mode.updated_at else None)
    return data


def _validation_error(details: dict):
    return error(
        "validation_error",
        "The mode payload is invalid.",
        status=422,
        details=details,
    )


def _name_conflict():
    return error(
        "mode_name_conflict",
        "A mode with this name already exists.",
        status=409,
        details={"name": ["Choose a unique mode name."]},
    )


def _mode_not_found():
    return error(
        "mode_not_found",
        "The requested mode does not exist.",
        status=404,
    )


def _pin_rate_limited(retry_after: int):
    return error(
        "pin_rate_limited",
        "Too many incorrect PIN attempts. Try again later.",
        status=429,
        details={"retry_after_seconds": retry_after},
        headers={"Retry-After": str(retry_after)},
    )
