from flask import current_app, g, request
from flask_login import current_user

from safescroll.api.models import APIToken
from safescroll.api.rate_limit import token_rate_limit
from safescroll.api.responses import error
from safescroll.extensions import db
from safescroll.models import User
from safescroll.utils import utcnow


def configured_origins() -> set[str]:
    configured = current_app.config.get("API_CORS_ORIGINS", ())
    if isinstance(configured, str):
        configured = configured.split(",")
    return {str(origin).strip().rstrip("/") for origin in configured if str(origin).strip()}


def origin_is_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    normalized = origin.rstrip("/")
    same_origin = request.host_url.rstrip("/")
    return normalized == same_origin or normalized in configured_origins()


def _view_name() -> str:
    return (request.endpoint or "").rsplit(".", 1)[-1]


def authenticate_request():
    """Authenticate every non-preflight API request using only a bearer token."""

    origin = request.headers.get("Origin")
    if not origin_is_allowed(origin):
        return error(
            "origin_not_allowed",
            "This request origin is not allowed.",
            status=403,
        )

    if request.method == "OPTIONS":
        return None

    # Pairing starts from an authenticated website session and completes with
    # the short-lived single-use credential itself. These two views perform
    # their own purpose-built authentication and throttling.
    if _view_name() == "exchange_extension_pairing":
        return None
    if _view_name() == "begin_extension_pairing":
        if current_user.is_authenticated and current_user.is_active:
            return None
        return error(
            "authentication_required",
            "Sign in to the SafeScroll website before pairing an extension.",
            status=401,
        )

    authorization = request.headers.get("Authorization", "")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return _unauthorized("A bearer token is required.")

    raw_token = parts[1]
    if len(raw_token) > 512:
        return _unauthorized("The bearer token is invalid or expired.")

    token = APIToken.from_raw_token(raw_token)
    user = db.session.get(User, token.user_id) if token else None
    if token is None or not token.is_valid or user is None or not user.is_active:
        return _unauthorized("The bearer token is invalid or expired.")

    retry_after = token_rate_limit(token.token_hash)
    if retry_after is not None:
        return error(
            "rate_limited",
            "Too many API requests. Try again later.",
            status=429,
            details={"retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )

    token.last_used_at = utcnow()
    db.session.commit()
    g.api_token = token
    g.api_user = user
    return None


def _unauthorized(message: str):
    return error(
        "invalid_token",
        message,
        status=401,
        headers={
            "WWW-Authenticate": 'Bearer realm="SafeScroll API", error="invalid_token"'
        },
    )
