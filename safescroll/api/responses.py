from flask import jsonify


def success(data, *, status: int = 200, meta: dict | None = None, headers: dict | None = None):
    payload = {"ok": True, "data": data}
    if meta is not None:
        payload["meta"] = meta
    response = jsonify(payload)
    response.status_code = status
    if headers:
        response.headers.update(headers)
    return response


def error(
    code: str,
    message: str,
    *,
    status: int,
    details: dict | None = None,
    headers: dict | None = None,
):
    item = {"code": code, "message": message}
    if details:
        item["details"] = details
    response = jsonify({"ok": False, "error": item})
    response.status_code = status
    if headers:
        response.headers.update(headers)
    return response
