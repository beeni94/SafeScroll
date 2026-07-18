from datetime import timedelta

import pytest

from safescroll.extensions import db
from safescroll.models import Device, User, UserSession, ViewingMode
from safescroll.utils import hash_token, utcnow

TEST_PASSWORD = "CorrectHorse1!"


@pytest.fixture
def two_users(app):
    with app.app_context():
        alice = User(full_name="Alice Owner", email="alice@example.com")
        alice.set_password(TEST_PASSWORD)
        bob = User(full_name="Bob Intruder", email="bob@example.com")
        bob.set_password(TEST_PASSWORD)
        db.session.add_all([alice, bob])
        db.session.commit()
        return alice.id, bob.id


def log_in(client, email):
    response = client.post(
        "/login",
        data={
            "email": email,
            "password": TEST_PASSWORD,
            "submit": "Log in",
        },
    )
    assert response.status_code == 302


def test_user_cannot_revoke_another_users_device(app, two_users):
    alice_id, _bob_id = two_users
    with app.app_context():
        device = Device(
            user_id=alice_id,
            name="Alice Chrome",
            client_identifier="alice-device-id",
        )
        db.session.add(device)
        db.session.commit()
        device_id = device.id

    bob_client = app.test_client()
    log_in(bob_client, "bob@example.com")
    forbidden = bob_client.post(f"/devices/{device_id}/revoke")
    assert forbidden.status_code == 404

    with app.app_context():
        assert db.session.get(Device, device_id).revoked_at is None

    alice_client = app.test_client()
    log_in(alice_client, "alice@example.com")
    allowed = alice_client.post(f"/devices/{device_id}/revoke")
    assert allowed.status_code == 302
    with app.app_context():
        assert db.session.get(Device, device_id).revoked_at is not None


def test_user_cannot_revoke_another_users_session(app, two_users):
    alice_id, _bob_id = two_users
    with app.app_context():
        other_session = UserSession(
            user_id=alice_id,
            token_hash=hash_token("alice-other-session-token"),
            device_label="Alice other browser",
            expires_at=utcnow() + timedelta(hours=1),
        )
        db.session.add(other_session)
        db.session.commit()
        session_id = other_session.id

    bob_client = app.test_client()
    log_in(bob_client, "bob@example.com")
    forbidden = bob_client.post(f"/security/sessions/{session_id}/revoke")
    assert forbidden.status_code == 404

    with app.app_context():
        assert db.session.get(UserSession, session_id).revoked_at is None

    alice_client = app.test_client()
    log_in(alice_client, "alice@example.com")
    allowed = alice_client.post(f"/security/sessions/{session_id}/revoke")
    assert allowed.status_code == 302
    with app.app_context():
        assert db.session.get(UserSession, session_id).revoked_at is not None


def test_revoke_other_sessions_never_revokes_another_users_session(app, two_users):
    alice_id, bob_id = two_users
    with app.app_context():
        alice_other = UserSession(
            user_id=alice_id,
            token_hash=hash_token("alice-bulk-target"),
            device_label="Alice tablet",
            expires_at=utcnow() + timedelta(hours=1),
        )
        bob_other = UserSession(
            user_id=bob_id,
            token_hash=hash_token("bob-must-survive"),
            device_label="Bob tablet",
            expires_at=utcnow() + timedelta(hours=1),
        )
        db.session.add_all([alice_other, bob_other])
        db.session.commit()
        alice_session_id = alice_other.id
        bob_session_id = bob_other.id

    alice_client = app.test_client()
    log_in(alice_client, "alice@example.com")
    response = alice_client.post("/security/sessions/revoke-others")
    assert response.status_code == 302

    with app.app_context():
        assert db.session.get(UserSession, alice_session_id).revoked_at is not None
        assert db.session.get(UserSession, bob_session_id).revoked_at is None


def test_user_cannot_activate_another_users_mode(app, two_users):
    alice_id, _bob_id = two_users
    with app.app_context():
        mode = ViewingMode(
            user_id=alice_id,
            name="Alice Private Study",
            preferred_categories=["Python"],
        )
        db.session.add(mode)
        db.session.commit()
        mode_id = mode.id

    bob_client = app.test_client()
    log_in(bob_client, "bob@example.com")
    forbidden = bob_client.post(f"/modes/{mode_id}/activate")
    assert forbidden.status_code == 404

    with app.app_context():
        assert db.session.get(ViewingMode, mode_id).is_active is False

    alice_client = app.test_client()
    log_in(alice_client, "alice@example.com")
    allowed = alice_client.post(f"/modes/{mode_id}/activate")
    assert allowed.status_code == 302
    with app.app_context():
        assert db.session.get(ViewingMode, mode_id).is_active is True


@pytest.mark.parametrize(
    "path",
    [
        "/logout",
        "/security/password",
        "/security/sessions/revoke-others",
        "/profile/delete",
        "/devices/1/revoke",
        "/modes/1/activate",
    ],
)
def test_state_changing_routes_require_authentication(client, path):
    response = client.post(path)

    assert response.status_code == 302
