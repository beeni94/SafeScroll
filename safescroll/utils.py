import hashlib
import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

from email_validator import EmailNotValidError, validate_email
from flask import request


PASSWORD_PATTERN = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,128}$"
)


def utcnow() -> datetime:
    """Return naive UTC for consistent SQLite storage and comparison."""
    return datetime.now(UTC).replace(tzinfo=None)


def relative_time(value: datetime | None) -> str:
    if value is None:
        return "never"
    seconds = max(0, int((utcnow() - value).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    days = hours // 24
    if days < 7:
        return f"{days} day{'s' if days != 1 else ''} ago"
    return value.strftime("%b %d, %Y")


def normalize_email(value: str) -> str:
    try:
        normalized = validate_email(
            (value or "").strip(), check_deliverability=False
        ).normalized
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc
    return normalized.lower()


def password_error(password: str) -> str | None:
    if not PASSWORD_PATTERN.fullmatch(password or ""):
        return (
            "Use 8-128 characters with uppercase, lowercase, a number, "
            "and a symbol."
        )
    return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_safe_next_url(target: str | None) -> bool:
    if not target:
        return False
    host_url = urlsplit(request.host_url)
    candidate = urlsplit(urljoin(request.host_url, target))
    return candidate.scheme in {"http", "https"} and candidate.netloc == host_url.netloc


def parse_comma_list(value: str | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in re.split(r"[,\r\n]+", value or ""):
        cleaned = " ".join(item.strip().split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def comma_list(value) -> str:
    return ", ".join(value or [])


def client_details(user_agent: str | None) -> tuple[str, str, str]:
    value = user_agent or ""
    lowered = value.lower()

    if "edg/" in lowered:
        browser = "Edge"
    elif "chrome/" in lowered:
        browser = "Chrome"
    elif "firefox/" in lowered:
        browser = "Firefox"
    elif "safari/" in lowered:
        browser = "Safari"
    else:
        browser = "Browser"

    if "windows" in lowered:
        platform = "Windows"
    elif "android" in lowered:
        platform = "Android"
    elif "iphone" in lowered or "ipad" in lowered:
        platform = "iOS"
    elif "mac os" in lowered or "macintosh" in lowered:
        platform = "macOS"
    elif "linux" in lowered:
        platform = "Linux"
    else:
        platform = "Unknown platform"

    return f"{browser} on {platform}", browser, platform
