from datetime import timedelta

from safescroll.api.models import APIToken
from safescroll.extensions import db
from safescroll.models import (
    Device,
    ExtensionConfiguration,
    ExtensionDevice,
    SyncLog,
    User,
)
from safescroll.utils import utcnow


def test_extension_page_has_complete_disconnected_state(app, auth, client):
    auth.register()
    auth.login()

    response = client.get("/extension")

    assert response.status_code == 200
    assert b"Not connected" in response.data
    assert b"Connect extension" in response.data
    assert b"Installation instructions" in response.data
    assert b"data-extension-pair-button" in response.data
    assert b"single-use pairing request" in response.data
    assert b"extension.js" in response.data
    with app.app_context():
        configuration = ExtensionConfiguration.query.one()
        assert f"v{configuration.config_version}".encode() in response.data


def test_extension_page_lists_sync_metadata_without_credentials(app, auth, client):
    auth.register()
    auth.login()
    with app.app_context():
        user = User.query.filter_by(email="alice@example.com").one()
        device = ExtensionDevice(
            user_id=user.id,
            device_identifier="page-device-identifier",
            name="Study laptop",
            browser="Chrome 140",
            platform="Windows",
            extension_version="1.2.0",
            last_sync_at=utcnow(),
        )
        db.session.add(device)
        db.session.flush()
        token, raw_token = APIToken.issue(
            user,
            extension_device=device,
            lifetime=timedelta(days=30),
        )
        db.session.add(token)
        db.session.commit()

    response = client.get("/extension")

    assert response.status_code == 200
    assert b"Connected" in response.data
    assert b"Study laptop" in response.data
    assert b"Chrome 140" in response.data
    assert b"Extension 1.2.0" in response.data
    assert b"Disconnect" in response.data
    assert raw_token.encode() not in response.data
    assert b"token_hash" not in response.data
    assert b"password_hash" not in response.data


def test_disconnect_extension_device_is_owned_and_revokes_bound_tokens(
    app, auth, client
):
    auth.register()
    auth.login()
    with app.app_context():
        owner = User.query.filter_by(email="alice@example.com").one()
        stranger = User(full_name="Other User", email="other-page@example.com")
        stranger.set_password("CorrectHorse1!")
        db.session.add(stranger)
        db.session.flush()
        owned_device = ExtensionDevice(
            user_id=owner.id,
            device_identifier="owned-page-device",
            name="Owned Chrome",
        )
        foreign_device = ExtensionDevice(
            user_id=stranger.id,
            device_identifier="foreign-page-device",
            name="Foreign Chrome",
        )
        db.session.add_all([owned_device, foreign_device])
        db.session.flush()
        token, _raw = APIToken.issue(
            owner,
            extension_device=owned_device,
            lifetime=timedelta(days=30),
        )
        db.session.add(token)
        db.session.commit()
        owned_id = owned_device.id
        foreign_id = foreign_device.id
        token_id = token.id

    denied = client.post(f"/extension/devices/{foreign_id}/disconnect")
    disconnected = client.post(f"/extension/devices/{owned_id}/disconnect")

    assert denied.status_code == 404
    assert disconnected.status_code == 302
    with app.app_context():
        assert not db.session.get(ExtensionDevice, owned_id).is_connected
        assert db.session.get(APIToken, token_id).revoked_at is not None
        assert db.session.get(ExtensionDevice, foreign_id).is_connected
        assert ExtensionConfiguration.query.filter_by(
            user_id=db.session.get(ExtensionDevice, owned_id).user_id
        ).one().sync_status == "disconnected"
        assert SyncLog.query.filter_by(
            extension_device_id=owned_id, status="disconnected"
        ).count() == 1


def test_legacy_extension_disconnect_returns_to_extension_page(app, auth, client):
    auth.register()
    auth.login()
    with app.app_context():
        user = User.query.filter_by(email="alice@example.com").one()
        legacy = Device(
            user_id=user.id,
            name="Legacy Chrome",
            device_type="extension",
            platform="Windows",
            browser="Chrome",
            client_identifier="legacy-page-extension",
        )
        db.session.add(legacy)
        db.session.commit()
        legacy_id = legacy.id

    page = client.get("/extension")
    disconnected = client.post(
        f"/devices/{legacy_id}/revoke?next=extension"
    )

    assert b"Legacy Chrome" in page.data
    assert disconnected.status_code == 302
    assert disconnected.headers["Location"].endswith("/extension")
    with app.app_context():
        assert db.session.get(Device, legacy_id).revoked_at is not None
