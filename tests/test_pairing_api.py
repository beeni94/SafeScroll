from datetime import timedelta

from safescroll.api.models import APIToken
from safescroll.extensions import db
from safescroll.models import (
    ExtensionDevice,
    ExtensionEvent,
    ExtensionPairingToken,
    User,
)
from safescroll.utils import utcnow


def _exchange_payload(token, identifier="chrome-pairing-device-001"):
    return {
        "pairing_token": token,
        "device_identifier": identifier,
        "device_name": "Study laptop",
        "browser": "Chrome 140",
        "platform": "Windows",
        "extension_version": "1.0.0",
    }


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_pairing_requires_website_login_and_status_requires_bearer(client):
    pair = client.post("/api/extension/pair", json={})
    status = client.get("/api/extension/status")

    assert pair.status_code == 401
    assert pair.json["error"]["code"] == "authentication_required"
    assert status.status_code == 401
    assert status.json["error"]["code"] == "invalid_token"


def test_one_click_pair_exchange_status_and_disconnect(app, auth, client):
    auth.register()
    auth.login()

    started = client.post("/api/extension/pair", json={})
    assert started.status_code == 201
    raw_pairing_token = started.json["data"]["pairing_token"]
    assert raw_pairing_token.startswith("sp_")

    exchanged = client.post(
        "/api/extension/exchange",
        json=_exchange_payload(raw_pairing_token),
    )
    assert exchanged.status_code == 201
    access_token = exchanged.json["data"]["access_token"]
    assert access_token.startswith("ss_")
    assert exchanged.json["data"]["user"]["email"] == "alice@example.com"
    assert exchanged.json["data"]["device"]["extension_version"] == "1.0.0"

    reused = client.post(
        "/api/extension/exchange",
        json=_exchange_payload(raw_pairing_token),
    )
    assert reused.status_code == 401
    assert reused.json["error"]["code"] == "invalid_pairing_token"

    status = client.get("/api/extension/status", headers=_bearer(access_token))
    config = client.get("/api/extension/config", headers=_bearer(access_token))
    assert status.status_code == 200
    assert status.json["data"]["connected"] is True
    assert status.json["data"]["device"]["device_identifier"] == "chrome-pairing-device-001"
    assert config.status_code == 200
    assert config.json["data"]["active_mode"] is not None

    disconnected = client.post(
        "/api/extension/disconnect", json={}, headers=_bearer(access_token)
    )
    denied_after_disconnect = client.get(
        "/api/extension/status", headers=_bearer(access_token)
    )
    assert disconnected.status_code == 200
    assert disconnected.json["data"]["connected"] is False
    assert denied_after_disconnect.status_code == 401

    with app.app_context():
        pairing = ExtensionPairingToken.query.one()
        device = ExtensionDevice.query.one()
        token = APIToken.query.one()
        events = [event.status for event in ExtensionEvent.query.order_by(ExtensionEvent.id)]
        assert pairing.used_at is not None
        assert pairing.claimed_device_id == device.id
        assert not device.is_connected
        assert token.revoked_at is not None
        assert events == ["created", "connected", "rejected", "disconnected"]


def test_new_pairing_request_revokes_previous_pending_request(app, auth, client):
    auth.register(email="pending-pair@example.com")
    auth.login(email="pending-pair@example.com")

    first = client.post("/api/extension/pair", json={})
    second = client.post("/api/extension/pair", json={})

    assert first.status_code == second.status_code == 201
    with app.app_context():
        tokens = ExtensionPairingToken.query.order_by(ExtensionPairingToken.id).all()
        assert tokens[0].revoked_at is not None
        assert tokens[1].is_valid

    old_exchange = client.post(
        "/api/extension/exchange",
        json=_exchange_payload(first.json["data"]["pairing_token"]),
    )
    assert old_exchange.status_code == 401


def test_expired_pairing_token_is_rejected(app, auth, client):
    auth.register(email="expired-pair@example.com")
    auth.login(email="expired-pair@example.com")
    started = client.post("/api/extension/pair", json={})

    with app.app_context():
        pairing = ExtensionPairingToken.query.one()
        pairing.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()

    response = client.post(
        "/api/extension/exchange",
        json=_exchange_payload(started.json["data"]["pairing_token"]),
    )
    assert response.status_code == 401
    assert response.json["error"]["code"] == "invalid_pairing_token"


def test_pairing_cannot_claim_another_users_device(app, auth, client):
    auth.register(email="pair-owner@example.com")
    auth.login(email="pair-owner@example.com")
    started = client.post("/api/extension/pair", json={})

    with app.app_context():
        stranger = User(full_name="Stranger", email="pair-stranger@example.com")
        stranger.set_password("CorrectHorse1!")
        db.session.add(stranger)
        db.session.flush()
        db.session.add(
            ExtensionDevice(
                user_id=stranger.id,
                device_identifier="foreign-pairing-device",
                name="Foreign browser",
            )
        )
        db.session.commit()

    response = client.post(
        "/api/extension/exchange",
        json=_exchange_payload(
            started.json["data"]["pairing_token"],
            identifier="foreign-pairing-device",
        ),
    )
    assert response.status_code == 403
    assert response.json["error"]["code"] == "device_not_available"
    with app.app_context():
        owner = User.query.filter_by(email="pair-owner@example.com").one()
        assert owner.extension_devices.count() == 0
        assert owner.extension_pairing_tokens.one().is_valid
