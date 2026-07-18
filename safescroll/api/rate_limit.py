"""Small per-token fixed-window limiter for the extension API.

The limiter deliberately keys requests by the stored token id instead of an IP
address. Chrome installations often share public addresses, while an API token
is already the security principal whose traffic we need to contain.

This in-process implementation is suitable for the current single-process
deployment. A multi-worker production deployment must replace it with a shared
backend (for example Redis) so every worker consumes the same allowance.
"""

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from flask import current_app


_request_times: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def fixed_window_rate_limit(
    bucket: str, key: str, *, limit: int, window: int
) -> int | None:
    """Return retry-after seconds for a namespaced fixed-window principal."""

    storage_key = f"{bucket}:{key}"
    now = monotonic()
    with _lock:
        requests = _request_times[storage_key]
        threshold = now - window
        while requests and requests[0] <= threshold:
            requests.popleft()
        if len(requests) >= limit:
            return max(1, int(window - (now - requests[0]) + 0.999))
        requests.append(now)
    return None


def token_rate_limit(token_key: str) -> int | None:
    """Return retry-after seconds when the token has exhausted its allowance."""

    limit = max(1, int(current_app.config.get("API_RATE_LIMIT_PER_MINUTE", 120)))
    window = max(1, int(current_app.config.get("API_RATE_LIMIT_WINDOW_SECONDS", 60)))
    return fixed_window_rate_limit(
        "api-token", token_key, limit=limit, window=window
    )
