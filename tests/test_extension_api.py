from datetime import timedelta

import safescroll.api.extension as extension_api
from safescroll.api.models import APIToken
from safescroll.extensions import db
from safescroll.models import (
    ExtensionConfiguration,
    ExtensionDevice,
    SyncLog,
    User,
    ViewingMode,
)
from safescroll.utils import utcnow


def _identity(app, *, email="extension-api@example.com", mode_names=("Study",)):
    with app.app_context():
        user = User(full_name="Extension API User", email=email)
        user.set_password("CorrectHorse1!")
        db.session.add(user)
        db.session.flush()
        modes = []
        for index, name in enumerate(mode_names):
            mode = ViewingMode(
                user_id=user.id,
                name=name,
                icon="book",
                color="#14B8A6",
                description=f"{name} description",
                strictness=index + 3,
                is_active=index == 0,
                last_used_at=utcnow() if index == 0 else None,
            )
            mode.preferred_categories = ["Education"]
            mode.blocked_categories = ["Gaming"]
            mode.preferred_keywords = ["tutorial"]
            mode.blocked_keywords = ["prank"]
            mode.schedule = {
                "days": ["mon", "wed"],
                "start": "08:00",
                "end": "14:00",
            }
            db.session.add(mode)
            modes.append(mode)
        token, raw = APIToken.issue(user, lifetime=timedelta(days=30))
        db.session.add(token)
        db.session.commit()
        return {
            "user_id": user.id,
            "mode_ids": [mode.id for mode in modes],
            "token_id": token.id,
            "raw": raw,
        }


def _headers(identity, **extra):
    return {"Authorization": f"Bearer {identity['raw']}", **extra}


def test_short_api_aliases_require_auth_and_return_complete_owned_modes(app, client):
    owner = _identity(app, mode_names=("Study", "Fun"))
    stranger = _identity(
        app, email="other-extension@example.com", mode_names=("Private",)
    )

    unauthorized = client.get("/api/modes")
    listed = client.get("/api/modes", headers=_headers(owner))
    active = client.get("/api/modes/active", headers=_headers(owner))
    hidden = client.get(
        f"/api/modes/{stranger['mode_ids'][0]}", headers=_headers(owner)
    )
    missing = client.get("/api/modes/999999", headers=_headers(owner))

    assert unauthorized.status_code == 401
    assert unauthorized.json["error"]["code"] == "invalid_token"
    assert listed.status_code == 200
    assert [mode["name"] for mode in listed.json["data"]["modes"]] == [
        "Study",
        "Fun",
    ]
    mode = active.json["data"]["mode"]
    assert mode["name"] == "Study"
    assert mode["preferred_categories"] == ["Education"]
    assert mode["blocked_categories"] == ["Gaming"]
    assert mode["preferred_keywords"] == ["tutorial"]
    assert mode["blocked_keywords"] == ["prank"]
    assert mode["strictness"] == 3
    assert mode["is_protected"] is False
    assert mode["schedule"]["days"] == ["mon", "wed"]
    serialized = str(active.json).lower()
    assert "password" not in serialized
    assert "protection_pin_hash" not in serialized
    assert "token_hash" not in serialized
    assert hidden.status_code == 404
    assert hidden.json["error"]["code"] == "mode_not_found"
    assert missing.status_code == 404
    assert missing.json["error"]["code"] == "mode_not_found"


def test_short_activation_alias_keeps_exactly_one_owned_mode_active(app, client):
    identity = _identity(app, mode_names=("Study", "Fun"))

    activated = client.post(
        f"/api/modes/{identity['mode_ids'][1]}/activate",
        headers=_headers(identity),
    )

    assert activated.status_code == 200
    assert activated.json["data"]["mode"]["name"] == "Fun"
    with app.app_context():
        active = ViewingMode.query.filter_by(
            user_id=identity["user_id"], is_active=True
        ).all()
        assert [mode.id for mode in active] == [identity["mode_ids"][1]]
        configuration = ExtensionConfiguration.query.filter_by(
            user_id=identity["user_id"]
        ).one()
        assert configuration.active_mode_id == identity["mode_ids"][1]
        assert configuration.config_version >= 2


def test_extension_sync_binds_device_and_returns_versioned_configuration(app, client):
    identity = _identity(app, mode_names=("Study", "Fun"))
    payload = {
        "device_identifier": "chrome-install-123456",
        "device_name": "My Chrome",
        "browser": "Chrome 126",
        "platform": "Windows",
        "extension_version": "1.2.0",
        "config_version": 0,
    }

    synced = client.post(
        "/api/extension/sync", json=payload, headers=_headers(identity)
    )
    configured = client.get("/api/extension/config", headers=_headers(identity))

    assert synced.status_code == 200
    assert synced.json["data"]["update_required"] is True
    assert synced.json["data"]["configuration"]["active_mode_id"] == identity[
        "mode_ids"
    ][0]
    assert synced.json["data"]["configuration"]["sync_status"] == "update_required"
    assert synced.json["data"]["active_mode"]["name"] == "Study"
    assert synced.json["data"]["device"]["device_identifier"] == payload[
        "device_identifier"
    ]
    assert configured.status_code == 200
    assert configured.json["data"]["device"]["name"] == "My Chrome"

    with app.app_context():
        token = db.session.get(APIToken, identity["token_id"])
        device = ExtensionDevice.query.filter_by(user_id=identity["user_id"]).one()
        log = SyncLog.query.filter_by(user_id=identity["user_id"]).one()
        configuration = ExtensionConfiguration.query.filter_by(
            user_id=identity["user_id"]
        ).one()
        assert token.extension_device_id == device.id
        assert device.last_sync_at is not None
        assert device.last_seen_at == device.last_sync_at
        assert device.extension_version == "1.2.0"
        assert configuration.last_sync_at is not None
        assert log.extension_device_id == device.id
        assert log.status == "update_required"


def test_sync_marks_configuration_synced_when_versions_match(app, client):
    identity = _identity(app)
    first = client.post(
        "/api/extension/sync",
        json={"device_identifier": "chrome-install-version-match", "config_version": 0},
        headers=_headers(identity),
    )
    server_version = first.json["data"]["configuration"]["config_version"]

    matched = client.post(
        "/api/extension/sync",
        json={
            "device_identifier": "chrome-install-version-match",
            "config_version": server_version,
        },
        headers=_headers(identity),
    )

    assert matched.status_code == 200
    assert matched.json["data"]["update_required"] is False
    assert matched.json["data"]["configuration"]["sync_status"] == "synced"
    with app.app_context():
        statuses = [
            log.status
            for log in SyncLog.query.filter_by(user_id=identity["user_id"])
            .order_by(SyncLog.id)
            .all()
        ]
        assert statuses == ["update_required", "synced"]


def test_config_read_repairs_stale_active_snapshot_and_advances_version(app, client):
    identity = _identity(app, mode_names=("Study", "Fun"))
    with app.app_context():
        configuration = ExtensionConfiguration.query.filter_by(
            user_id=identity["user_id"]
        ).one()
        previous_version = configuration.config_version
        configuration.active_mode_id = None
        db.session.commit()

    response = client.get("/api/extension/config", headers=_headers(identity))

    assert response.status_code == 200
    assert response.json["data"]["configuration"]["active_mode_id"] == identity[
        "mode_ids"
    ][0]
    assert response.json["data"]["configuration"]["config_version"] == (
        previous_version + 1
    )
    with app.app_context():
        repaired = ExtensionConfiguration.query.filter_by(
            user_id=identity["user_id"]
        ).one()
        assert repaired.active_mode_id == identity["mode_ids"][0]
        assert repaired.sync_status == "pending"


def test_sync_rolls_back_when_configuration_changes_during_compare(
    app, client, monkeypatch
):
    identity = _identity(app)
    monkeypatch.setattr(
        extension_api,
        "_conditionally_store_sync_state",
        lambda *args, **kwargs: False,
    )

    response = client.post(
        "/api/extension/sync",
        json={
            "device_identifier": "chrome-install-version-race",
            "config_version": 1,
        },
        headers=_headers(identity),
    )

    assert response.status_code == 409
    assert response.json["error"]["code"] == "configuration_changed"
    with app.app_context():
        assert ExtensionDevice.query.filter_by(user_id=identity["user_id"]).count() == 0
        assert SyncLog.query.filter_by(user_id=identity["user_id"]).count() == 0
        assert db.session.get(APIToken, identity["token_id"]).extension_device_id is None


def test_sync_requires_valid_json_and_consistent_device_identity(app, client):
    identity = _identity(app)

    wrong_type = client.post(
        "/api/extension/sync",
        data="not-json",
        content_type="text/plain",
        headers=_headers(identity),
    )
    invalid = client.post(
        "/api/extension/sync",
        json={"device_identifier": "short", "unexpected": True},
        headers=_headers(identity),
    )
    mismatch = client.post(
        "/api/extension/sync",
        json={"device_identifier": "chrome-install-123456"},
        headers=_headers(identity, **{"X-Device-ID": "chrome-install-999999"}),
    )

    assert wrong_type.status_code == 415
    assert invalid.status_code == 422
    assert "device_identifier" in invalid.json["error"]["details"]
    assert "unknown_fields" in invalid.json["error"]["details"]
    assert mismatch.status_code == 422


def test_bound_token_rejects_device_mismatch_and_logs_it(app, client):
    identity = _identity(app)
    first = client.post(
        "/api/extension/sync",
        json={"device_identifier": "chrome-install-primary"},
        headers=_headers(identity),
    )
    mismatch = client.post(
        "/api/extension/sync",
        json={"device_identifier": "chrome-install-secondary"},
        headers=_headers(identity),
    )

    assert first.status_code == 200
    assert mismatch.status_code == 403
    assert mismatch.json["error"]["code"] == "device_mismatch"
    with app.app_context():
        logs = SyncLog.query.filter_by(user_id=identity["user_id"]).all()
        assert [log.status for log in logs] == ["update_required", "rejected"]
        assert ExtensionDevice.query.filter_by(user_id=identity["user_id"]).count() == 1


def test_extension_device_identifier_cannot_cross_users(app, client):
    owner = _identity(app, email="device-owner@example.com")
    stranger = _identity(app, email="device-stranger@example.com")
    identifier = "chrome-install-user-owned"

    assert client.post(
        "/api/extension/sync",
        json={"device_identifier": identifier},
        headers=_headers(owner),
    ).status_code == 200
    rejected = client.post(
        "/api/extension/sync",
        json={"device_identifier": identifier},
        headers=_headers(stranger),
    )

    assert rejected.status_code == 403
    assert rejected.json["error"]["code"] == "device_not_available"
    with app.app_context():
        assert ExtensionDevice.query.filter_by(device_identifier=identifier).count() == 1


def test_extension_alias_rejects_revoked_expired_tokens_and_revoked_device(app, client):
    revoked = _identity(app, email="revoked-short@example.com")
    expired = _identity(app, email="expired-short@example.com")
    disconnected = _identity(app, email="disconnected@example.com")
    client.post(
        "/api/extension/sync",
        json={"device_identifier": "chrome-install-disconnect"},
        headers=_headers(disconnected),
    )
    with app.app_context():
        db.session.get(APIToken, revoked["token_id"]).revoke()
        db.session.get(APIToken, expired["token_id"]).expires_at = utcnow() - timedelta(
            seconds=1
        )
        device = ExtensionDevice.query.filter_by(user_id=disconnected["user_id"]).one()
        # Simulate an administrative disconnect without using revoke(), so this
        # explicitly exercises the API's device check as well as token checks.
        device.revoked_at = utcnow()
        db.session.commit()

    assert client.get("/api/modes", headers=_headers(revoked)).status_code == 401
    assert client.get("/api/modes", headers=_headers(expired)).status_code == 401
    denied = client.get("/api/extension/config", headers=_headers(disconnected))
    assert denied.status_code == 401
    assert denied.json["error"]["code"] == "invalid_token"


def test_api_rate_limit_is_per_token_and_applies_to_both_prefixes(app, client):
    limited = _identity(app, email="limited@example.com")
    other = _identity(app, email="unlimited@example.com")
    app.config["API_RATE_LIMIT_PER_MINUTE"] = 2
    app.config["API_RATE_LIMIT_WINDOW_SECONDS"] = 60

    first = client.get("/api/modes", headers=_headers(limited))
    second = client.get("/api/v1/modes", headers=_headers(limited))
    blocked = client.get("/api/modes/active", headers=_headers(limited))
    separate = client.get("/api/modes", headers=_headers(other))

    assert first.status_code == second.status_code == 200
    assert blocked.status_code == 429
    assert blocked.json["error"]["code"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) > 0
    assert separate.status_code == 200


def test_short_api_unknown_routes_use_secure_json_errors(client):
    missing = client.get("/api/not-a-real-endpoint")
    wrong_method = client.delete("/api/modes")

    assert missing.status_code == 404
    assert missing.is_json
    assert missing.json["error"]["code"] == "not_found"
    assert wrong_method.status_code == 405
    assert wrong_method.is_json
    assert wrong_method.json["error"]["code"] == "method_not_allowed"
