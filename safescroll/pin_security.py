"""Persistent throttling for protected-mode PIN verification."""

import math

from flask import current_app

from safescroll.extensions import db
from safescroll.models import ModePINAttempt
from safescroll.utils import hash_token, utcnow


def pin_principal(kind: str, identifier) -> str:
    """Return a non-reversible, fixed-length key for a session or API token."""

    return hash_token(f"{kind}:{identifier}")


def pin_retry_after(mode_id: int, principal_key: str) -> int | None:
    attempt = _attempt(mode_id, principal_key)
    if attempt is None or attempt.locked_until is None:
        return None
    now = utcnow()
    if attempt.locked_until <= now:
        db.session.delete(attempt)
        db.session.commit()
        return None
    return max(1, math.ceil((attempt.locked_until - now).total_seconds()))


def record_pin_failure(mode_id: int, principal_key: str) -> int | None:
    now = utcnow()
    attempt = _attempt(mode_id, principal_key)
    window = current_app.config["PIN_ATTEMPT_WINDOW"]
    limit = current_app.config["PIN_ATTEMPT_LIMIT"]
    if attempt is None:
        attempt = ModePINAttempt(
            mode_id=mode_id,
            principal_key=principal_key,
            failed_count=0,
            window_started_at=now,
        )
        db.session.add(attempt)
    elif now - attempt.window_started_at >= window:
        attempt.failed_count = 0
        attempt.window_started_at = now
        attempt.locked_until = None

    attempt.failed_count += 1
    if attempt.failed_count >= limit:
        attempt.locked_until = now + current_app.config["PIN_LOCKOUT"]
    db.session.commit()
    return pin_retry_after(mode_id, principal_key)


def clear_pin_failures(mode_id: int, principal_key: str) -> None:
    attempt = _attempt(mode_id, principal_key)
    if attempt is not None:
        db.session.delete(attempt)
        db.session.commit()


def _attempt(mode_id: int, principal_key: str):
    return ModePINAttempt.query.filter_by(
        mode_id=mode_id,
        principal_key=principal_key,
    ).first()
