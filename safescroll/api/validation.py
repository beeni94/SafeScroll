import re
from datetime import time

from flask import request

from safescroll.api.responses import error


LIST_FIELDS = {
    "preferred_categories",
    "blocked_categories",
    "preferred_keywords",
    "blocked_keywords",
}
MODE_FIELDS = {
    "name",
    "icon",
    "description",
    *LIST_FIELDS,
    "strictness",
    "schedule",
    "color",
    "is_protected",
    "protection_pin",
    "pin",
}
SCHEDULE_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
PIN = re.compile(r"^[0-9]{4,8}$")


def read_json(*, optional: bool = False):
    if optional and not request.data:
        return {}, None
    if not request.is_json:
        return None, error(
            "unsupported_media_type",
            "Use application/json for API request bodies.",
            status=415,
        )
    payload = request.get_json(silent=True)
    if payload is None:
        return None, error("invalid_json", "The JSON body is malformed.", status=400)
    if not isinstance(payload, dict):
        return None, error(
            "validation_error",
            "The request body must be a JSON object.",
            status=422,
            details={"body": ["Expected an object."]},
        )
    return payload, None


def validate_mode_payload(payload: dict, *, require_name: bool, creating: bool = False):
    errors: dict[str, list[str]] = {}
    cleaned: dict = {}
    unknown = sorted(set(payload) - MODE_FIELDS)
    if unknown:
        errors["unknown_fields"] = [f"Unsupported field: {field}" for field in unknown]

    if require_name and "name" not in payload:
        errors.setdefault("name", []).append("This field is required.")

    if "name" in payload:
        value = _string(payload["name"], "name", errors, min_length=2, max_length=80)
        if value is not None:
            cleaned["name"] = value

    if "icon" in payload:
        value = _string(payload["icon"], "icon", errors, min_length=1, max_length=16)
        if value is not None:
            cleaned["icon"] = value

    if "description" in payload:
        value = _string(
            payload["description"], "description", errors, min_length=0, max_length=500
        )
        if value is not None:
            cleaned["description"] = value

    for field in LIST_FIELDS:
        if field in payload:
            value = _string_list(payload[field], field, errors)
            if value is not None:
                cleaned[field] = value

    if "strictness" in payload:
        value = payload["strictness"]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            errors.setdefault("strictness", []).append("Must be an integer from 1 to 5.")
        else:
            cleaned["strictness"] = value

    if "schedule" in payload:
        value = _schedule(payload["schedule"], errors)
        if value is not None:
            cleaned["schedule"] = value

    if "color" in payload:
        value = payload["color"]
        if not isinstance(value, str) or not HEX_COLOR.fullmatch(value.strip()):
            errors.setdefault("color", []).append("Must be a six-digit hex color, such as #14b8a6.")
        else:
            cleaned["color"] = value.strip().lower()

    if "is_protected" in payload:
        if not isinstance(payload["is_protected"], bool):
            errors.setdefault("is_protected", []).append("Must be a boolean.")
        else:
            cleaned["is_protected"] = payload["is_protected"]

    for field in ("pin", "protection_pin"):
        if field in payload:
            value = payload[field]
            if not isinstance(value, str) or not PIN.fullmatch(value):
                errors.setdefault(field, []).append("Must contain 4 to 8 digits.")
            else:
                cleaned[field] = value

    if creating and cleaned.get("is_protected") and "protection_pin" not in cleaned:
        errors.setdefault("protection_pin", []).append(
            "A protection PIN is required for a protected mode."
        )
    if creating and "pin" in payload:
        errors.setdefault("pin", []).append(
            "A current PIN is not accepted when creating a mode."
        )
    if creating and "protection_pin" in payload and not cleaned.get("is_protected"):
        errors.setdefault("protection_pin", []).append(
            "Set is_protected to true when providing a protection PIN."
        )

    return cleaned, errors


def _string(value, field: str, errors: dict, *, min_length: int, max_length: int):
    if not isinstance(value, str):
        errors.setdefault(field, []).append("Must be a string.")
        return None
    value = value.strip()
    if len(value) < min_length:
        errors.setdefault(field, []).append(f"Must be at least {min_length} characters.")
    elif len(value) > max_length:
        errors.setdefault(field, []).append(f"Must be at most {max_length} characters.")
    else:
        return value
    return None


def _string_list(value, field: str, errors: dict):
    if not isinstance(value, list):
        errors.setdefault(field, []).append("Must be an array of strings.")
        return None
    if len(value) > 50:
        errors.setdefault(field, []).append("Must contain no more than 50 items.")
        return None
    result = []
    seen = set()
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.setdefault(field, []).append(f"Item {index} must be a string.")
            continue
        item = item.strip()
        if not item:
            errors.setdefault(field, []).append(f"Item {index} cannot be empty.")
        elif len(item) > 100:
            errors.setdefault(field, []).append(f"Item {index} must be at most 100 characters.")
        elif item.casefold() not in seen:
            result.append(item)
            seen.add(item.casefold())
    return result if field not in errors else None


def _schedule(value, errors: dict):
    # An explicit null/empty object clears an existing schedule. Any nonempty
    # schedule must be complete and valid.
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict):
        errors.setdefault("schedule", []).append("Must be an object.")
        return None
    unknown = sorted(set(value) - {"days", "start", "end"})
    if unknown:
        errors.setdefault("schedule", []).append(
            "Unsupported schedule fields: " + ", ".join(unknown) + "."
        )
    days = value.get("days")
    if not isinstance(days, list) or not days:
        errors.setdefault("schedule", []).append("Days must be a nonempty array.")
    elif any(not isinstance(day, str) or day not in SCHEDULE_DAYS for day in days):
        errors.setdefault("schedule", []).append(
            "Days may only contain mon, tue, wed, thu, fri, sat, or sun."
        )
    elif len(set(days)) != len(days):
        errors.setdefault("schedule", []).append("Days must not contain duplicates.")

    start_value = value.get("start")
    end_value = value.get("end")
    start = _clock_time(start_value)
    end = _clock_time(end_value)
    if start is None or end is None:
        errors.setdefault("schedule", []).append(
            "Start and end are required in 24-hour HH:MM format."
        )
    elif end <= start:
        errors.setdefault("schedule", []).append("End time must be later than start time.")

    if "schedule" in errors:
        return None
    return {
        "days": days,
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
    }


def _clock_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", value):
        return None
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.hour <= 23 else None
