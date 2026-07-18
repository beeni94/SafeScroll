from datetime import timedelta
import re

from safescroll.api.models import APIToken
from safescroll.extensions import db
from safescroll.models import User, ViewingMode
from safescroll.utils import utcnow


def _identity(app, *, email="extension@example.com", mode_name=None, protected=False):
    with app.app_context():
        user = User(full_name="Extension User", email=email)
        user.set_password("CorrectHorse1!")
        db.session.add(user)
        db.session.flush()
        mode_id = None
        if mode_name:
            mode = ViewingMode(
                user_id=user.id,
                name=mode_name,
                icon="📚",
                color="#14B8A6",
                strictness=3,
                is_active=True,
                is_protected=protected,
                last_used_at=utcnow(),
            )
            mode.preferred_categories = ["Education"]
            if protected:
                mode.set_protection_pin("2468")
            db.session.add(mode)
            db.session.flush()
            mode_id = mode.id
        token, raw = APIToken.issue(
            user,
            lifetime=timedelta(days=30),
            label="Test extension",
        )
        db.session.add(token)
        db.session.commit()
        return {"user_id": user.id, "mode_id": mode_id, "token_id": token.id, "raw": raw}


def _headers(identity, **extra):
    return {"Authorization": f"Bearer {identity['raw']}", **extra}


def test_api_requires_bearer_even_with_authenticated_session(app, client, auth):
    auth.register()
    auth.login()

    response = client.get("/api/v1/modes")

    assert response.status_code == 401
    assert response.is_json
    assert response.json["ok"] is False
    assert response.json["error"]["code"] == "invalid_token"
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


def test_token_is_hashed_and_last_used_is_recorded(app, client):
    identity = _identity(app)
    with app.app_context():
        token = db.session.get(APIToken, identity["token_id"])
        assert token.token_hash != identity["raw"]
        assert token.prefix == identity["raw"][:16]
        assert token.last_used_at is None

    response = client.get("/api/v1/modes", headers=_headers(identity))

    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(APIToken, identity["token_id"]).last_used_at is not None


def test_list_and_get_never_cross_user_ownership(app, client):
    owner = _identity(app, email="owner@example.com", mode_name="Owner mode")
    stranger = _identity(app, email="stranger@example.com", mode_name="Stranger mode")

    listed = client.get("/api/v1/modes", headers=_headers(owner))
    hidden = client.get(
        f"/api/v1/modes/{stranger['mode_id']}", headers=_headers(owner)
    )

    assert listed.status_code == 200
    assert [item["name"] for item in listed.json["data"]["modes"]] == ["Owner mode"]
    assert hidden.status_code == 404
    assert hidden.json["error"]["code"] == "mode_not_found"


def test_activation_never_crosses_user_ownership_and_handles_missing_ids(app, client):
    owner = _identity(app, email="activation-owner@example.com", mode_name="Owner mode")
    stranger = _identity(
        app, email="activation-stranger@example.com", mode_name="Stranger mode"
    )

    hidden = client.post(
        f"/api/modes/{stranger['mode_id']}/activate",
        headers=_headers(owner),
    )
    missing = client.post("/api/modes/999999/activate", headers=_headers(owner))

    assert hidden.status_code == 404
    assert hidden.json["error"]["code"] == "mode_not_found"
    assert missing.status_code == 404
    assert missing.json["error"]["code"] == "mode_not_found"
    with app.app_context():
        assert db.session.get(ViewingMode, owner["mode_id"]).is_active is True
        assert db.session.get(ViewingMode, stranger["mode_id"]).is_active is True


def test_create_mode_normalizes_payload_and_marks_first_mode_active(app, client):
    identity = _identity(app)
    payload = {
        "name": "  Deep Work  ",
        "icon": "🎯",
        "color": "#14b8a6",
        "description": "  Focused learning  ",
        "preferred_categories": ["Python", " python ", "AI"],
        "blocked_categories": ["Music"],
        "preferred_keywords": ["tutorial"],
        "blocked_keywords": ["dance"],
        "strictness": 4,
        "schedule": {"days": ["mon", "wed"], "start": "08:00", "end": "14:00"},
    }

    response = client.post(
        "/api/v1/modes", json=payload, headers=_headers(identity)
    )

    assert response.status_code == 201
    mode = response.json["data"]["mode"]
    assert mode["name"] == "Deep Work"
    assert mode["color"] == "#14B8A6"
    assert mode["preferred_categories"] == ["Python", "AI"]
    assert mode["schedule"]["days"] == ["mon", "wed"]
    assert mode["is_active"] is True
    assert mode["last_used_at"] is not None
    assert response.headers["Location"].endswith(f"/api/v1/modes/{mode['id']}")


def test_json_validation_rejects_unknown_fields_and_invalid_schedules(app, client):
    identity = _identity(app)
    response = client.post(
        "/api/v1/modes",
        json={
            "name": "Study",
            "is_active": True,
            "schedule": {"days": [], "start": "17:00", "end": "09:00"},
        },
        headers=_headers(identity),
    )

    assert response.status_code == 422
    details = response.json["error"]["details"]
    assert "unknown_fields" in details
    assert "schedule" in details


def test_create_rejects_contextless_pin_fields(app, client):
    identity = _identity(app)

    new_pin_without_protection = client.post(
        "/api/v1/modes",
        json={"name": "Unsafe", "protection_pin": "2468"},
        headers=_headers(identity),
    )
    current_pin_on_create = client.post(
        "/api/v1/modes",
        json={"name": "Also unsafe", "pin": "2468"},
        headers=_headers(identity),
    )

    assert new_pin_without_protection.status_code == 422
    assert "protection_pin" in new_pin_without_protection.json["error"]["details"]
    assert current_pin_on_create.status_code == 422
    assert "pin" in current_pin_on_create.json["error"]["details"]


def test_patch_can_clear_schedule(app, client):
    identity = _identity(app, mode_name="Scheduled")
    with app.app_context():
        mode = db.session.get(ViewingMode, identity["mode_id"])
        mode.schedule = {"days": ["mon"], "start": "08:00", "end": "10:00"}
        db.session.commit()

    response = client.patch(
        f"/api/v1/modes/{identity['mode_id']}",
        json={"schedule": None},
        headers=_headers(identity),
    )

    assert response.status_code == 200
    assert response.json["data"]["mode"]["schedule"] == {}


def test_protected_mode_requires_current_pin_for_update_and_activation(app, client):
    identity = _identity(app, mode_name="Child", protected=True)

    missing = client.patch(
        f"/api/v1/modes/{identity['mode_id']}",
        json={"strictness": 5},
        headers=_headers(identity),
    )
    wrong = client.post(
        f"/api/v1/modes/{identity['mode_id']}/activate",
        json={"pin": "1111"},
        headers=_headers(identity),
    )
    updated = client.patch(
        f"/api/v1/modes/{identity['mode_id']}",
        json={"strictness": 5, "pin": "2468"},
        headers=_headers(identity),
    )

    assert missing.status_code == 403
    assert missing.json["error"]["code"] == "protection_pin_required"
    assert wrong.status_code == 403
    assert wrong.json["error"]["code"] == "invalid_protection_pin"
    assert updated.status_code == 200
    assert updated.json["data"]["mode"]["strictness"] == 5


def test_protected_mode_pin_attempts_are_rate_limited(app, client):
    identity = _identity(app, mode_name="Locked child", protected=True)

    responses = [
        client.patch(
            f"/api/v1/modes/{identity['mode_id']}",
            json={"strictness": 5, "pin": "1111"},
            headers=_headers(identity),
        )
        for _ in range(app.config["PIN_ATTEMPT_LIMIT"])
    ]
    still_locked = client.patch(
        f"/api/v1/modes/{identity['mode_id']}",
        json={"strictness": 5, "pin": "2468"},
        headers=_headers(identity),
    )

    assert all(response.status_code == 403 for response in responses[:-1])
    assert responses[-1].status_code == 429
    assert responses[-1].json["error"]["code"] == "pin_rate_limited"
    assert int(responses[-1].headers["Retry-After"]) > 0
    assert still_locked.status_code == 429


def test_deleting_active_mode_activates_owned_replacement(app, client):
    identity = _identity(app, mode_name="First")
    created = client.post(
        "/api/v1/modes", json={"name": "Second"}, headers=_headers(identity)
    )
    second_id = created.json["data"]["mode"]["id"]

    deleted = client.delete(
        f"/api/v1/modes/{identity['mode_id']}", headers=_headers(identity)
    )
    active = client.get("/api/v1/modes/active", headers=_headers(identity))

    assert deleted.status_code == 200
    assert deleted.json["data"]["active_mode_id"] == second_id
    assert active.json["data"]["mode"]["id"] == second_id


def test_deleting_active_mode_does_not_bypass_replacement_pin(app, client):
    identity = _identity(app, mode_name="Active")
    protected = client.post(
        "/api/v1/modes",
        json={
            "name": "Protected replacement",
            "is_protected": True,
            "protection_pin": "2468",
        },
        headers=_headers(identity),
    ).json["data"]["mode"]

    deleted = client.delete(
        f"/api/v1/modes/{identity['mode_id']}", headers=_headers(identity)
    )
    active = client.get("/api/v1/modes/active", headers=_headers(identity))

    assert deleted.status_code == 200
    assert deleted.json["data"]["active_mode_id"] is None
    assert active.json["data"]["mode"] is None
    assert protected["is_active"] is False


def test_revoked_and_expired_tokens_are_rejected(app, client):
    revoked = _identity(app, email="revoked@example.com")
    expired = _identity(app, email="expired@example.com")
    with app.app_context():
        db.session.get(APIToken, revoked["token_id"]).revoke()
        db.session.get(APIToken, expired["token_id"]).expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()

    assert client.get("/api/v1/modes", headers=_headers(revoked)).status_code == 401
    assert client.get("/api/v1/modes", headers=_headers(expired)).status_code == 401


def test_cors_uses_exact_allowlist_and_rejects_other_origins(app, client):
    identity = _identity(app)
    allowed = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    app.config["API_CORS_ORIGINS"] = (allowed,)

    good = client.get(
        "/api/v1/modes", headers=_headers(identity, Origin=allowed)
    )
    bad = client.get(
        "/api/v1/modes",
        headers=_headers(identity, Origin="https://attacker.example"),
    )
    preflight = client.options(
        "/api/extension/sync",
        headers={
            "Origin": allowed,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, X-Device-ID",
        },
    )

    assert good.status_code == 200
    assert good.headers["Access-Control-Allow-Origin"] == allowed
    assert "X-Device-ID" in good.headers["Access-Control-Allow-Headers"]
    assert preflight.status_code == 200
    assert preflight.headers["Access-Control-Allow-Origin"] == allowed
    assert "X-Device-ID" in preflight.headers["Access-Control-Allow-Headers"]
    assert bad.status_code == 403
    assert "Access-Control-Allow-Origin" not in bad.headers


def test_api_404_and_405_use_json_errors(client):
    missing = client.get("/api/v1/not-real")
    wrong_method = client.delete("/api/v1/modes")

    assert missing.status_code == 404
    assert missing.is_json
    assert missing.json["error"]["code"] == "not_found"
    assert wrong_method.status_code == 405
    assert wrong_method.is_json
    assert wrong_method.json["error"]["code"] == "method_not_allowed"


def test_oversized_api_payload_uses_json_error(app, client):
    identity = _identity(app)
    app.config["MAX_CONTENT_LENGTH"] = 64

    response = client.post(
        "/api/v1/modes",
        data='{"name":"' + ("x" * 200) + '"}',
        content_type="application/json",
        headers=_headers(identity),
    )

    assert response.status_code == 413
    assert response.is_json
    assert response.json["error"]["code"] == "payload_too_large"


def test_cli_issues_raw_token_once_and_can_revoke_it(app):
    with app.app_context():
        user = User(full_name="CLI User", email="cli@example.com")
        user.set_password("CorrectHorse1!")
        db.session.add(user)
        db.session.commit()

    runner = app.test_cli_runner()
    issued = runner.invoke(
        args=["api", "issue-token", "--email", "cli@example.com", "--days", "7"]
    )
    assert issued.exit_code == 0, issued.output
    raw = re.search(r"^Token: (ss_\S+)$", issued.output, re.MULTILINE).group(1)
    prefix = raw[:16]
    with app.app_context():
        record = APIToken.query.filter_by(prefix=prefix).one()
        assert record.token_hash != raw

    revoked = runner.invoke(args=["api", "revoke-token", "--prefix", prefix])
    assert revoked.exit_code == 0, revoked.output
    with app.app_context():
        assert APIToken.query.filter_by(prefix=prefix).one().revoked_at is not None
