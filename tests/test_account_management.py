from datetime import timedelta

from safescroll.extensions import db
from safescroll.models import PasswordResetToken, User, UserSession, ViewingMode
from safescroll.utils import hash_token, utcnow

from conftest import TEST_PASSWORD


def test_profile_update_is_normalized_and_persisted(app, auth, client):
    auth.register()
    auth.login()

    response = client.post(
        "/profile",
        data={
            "full_name": "Alice Updated",
            "email": "  Updated@Example.COM  ",
            "timezone": "Asia/Karachi",
            "submit": "Save profile",
        },
    )

    assert response.status_code == 302
    with app.app_context():
        user = db.session.execute(db.select(User)).scalar_one()
        assert user.full_name == "Alice Updated"
        assert user.email == "updated@example.com"
        assert user.timezone == "Asia/Karachi"


def test_password_change_revokes_other_sessions_and_reset_tokens(app, auth, client):
    auth.register()
    auth.login()

    with app.app_context():
        user = User.query.filter_by(email="alice@example.com").one()
        other = UserSession(
            user_id=user.id,
            token_hash=hash_token("other-session"),
            device_label="Other browser",
            expires_at=utcnow() + timedelta(hours=1),
        )
        token, _raw = PasswordResetToken.issue(user, timedelta(hours=1))
        db.session.add_all([other, token])
        db.session.commit()
        other_id, token_id = other.id, token.id

    response = client.post(
        "/security/password",
        data={
            "current_password": TEST_PASSWORD,
            "new_password": "NewCorrectHorse2!",
            "confirm_password": "NewCorrectHorse2!",
            "submit": "Update password",
        },
    )

    assert response.status_code == 302
    assert client.get("/dashboard").status_code == 200
    with app.app_context():
        assert db.session.get(UserSession, other_id).revoked_at is not None
        assert db.session.get(PasswordResetToken, token_id).used_at is not None
        assert User.query.filter_by(email="alice@example.com").one().check_password(
            "NewCorrectHorse2!"
        )


def test_revoked_current_session_is_rejected_on_next_request(app, auth, client):
    auth.register()
    auth.login()
    with client.session_transaction() as browser_session:
        managed_id = browser_session["auth_session_id"]

    with app.app_context():
        db.session.get(UserSession, managed_id).revoked_at = utcnow()
        db.session.commit()

    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_mode_crud_stays_user_owned_and_preserves_one_active_mode(app, auth, client):
    auth.register()
    auth.login()

    created = client.post(
        "/modes/new",
        data={
            "name": "Deep Work",
            "icon": "🧠",
            "description": "Focus time",
            "preferred_categories": "Python\nAI, Education",
            "blocked_categories": "Music, Pranks",
            "strictness": "5",
            "submit": "Save mode",
        },
    )
    assert created.status_code == 302

    with app.app_context():
        user = User.query.filter_by(email="alice@example.com").one()
        mode = ViewingMode.query.filter_by(user_id=user.id, name="Deep Work").one()
        assert mode.preferred_categories == ["Python", "AI", "Education"]
        mode_id = mode.id

    assert client.post(f"/modes/{mode_id}/activate").status_code == 302
    assert client.post(f"/modes/{mode_id}/duplicate").status_code == 302

    with app.app_context():
        user = User.query.filter_by(email="alice@example.com").one()
        active_modes = ViewingMode.query.filter_by(user_id=user.id, is_active=True).all()
        assert [mode.id for mode in active_modes] == [mode_id]
        assert ViewingMode.query.filter_by(user_id=user.id, name="Deep Work copy").one()

    assert client.post(f"/modes/{mode_id}/delete").status_code == 302
    with app.app_context():
        user = User.query.filter_by(email="alice@example.com").one()
        assert ViewingMode.query.filter_by(user_id=user.id, is_active=True).count() == 1


def test_account_deletion_removes_user_data(app, auth, client):
    auth.register()
    auth.login()

    response = client.post(
        "/account/delete",
        data={
            "password": TEST_PASSWORD,
            "confirmation": "DELETE",
            "submit": "Delete account",
        },
    )

    assert response.status_code == 302
    assert client.get("/dashboard").status_code == 302
    with app.app_context():
        assert User.query.count() == 0
        assert ViewingMode.query.count() == 0


def test_protected_mode_requires_pin_before_activation(app, auth, client):
    auth.register()
    auth.login()
    created = client.post(
        "/modes/new",
        data={
            "name": "Parent Guard",
            "icon": "🔒",
            "strictness": "5",
            "is_protected": "y",
            "protection_pin": "2468",
            "submit": "Save mode",
        },
    )
    assert created.status_code == 302
    with app.app_context():
        mode = ViewingMode.query.filter_by(name="Parent Guard").one()
        mode_id = mode.id
        assert mode.protection_pin_hash != "2468"

    locked = client.post(f"/modes/{mode_id}/activate")
    assert locked.status_code == 302
    assert f"/modes/{mode_id}/unlock" in locked.headers["Location"]

    wrong = client.post(
        f"/modes/{mode_id}/unlock?next=activate",
        data={"pin": "0000", "submit": "Unlock mode"},
    )
    assert wrong.status_code == 200

    unlocked = client.post(
        f"/modes/{mode_id}/unlock?next=activate",
        data={"pin": "2468", "submit": "Unlock mode"},
    )
    assert unlocked.status_code == 302
    with app.app_context():
        assert db.session.get(ViewingMode, mode_id).is_active is True


def test_protected_mode_cannot_be_created_without_pin(app, auth, client):
    auth.register()
    auth.login()
    response = client.post(
        "/modes/new",
        data={
            "name": "Missing PIN",
            "icon": "🔒",
            "strictness": "4",
            "is_protected": "y",
            "submit": "Save mode",
        },
    )
    assert response.status_code == 400
    with app.app_context():
        assert ViewingMode.query.filter_by(name="Missing PIN").first() is None


def test_deleting_active_mode_does_not_auto_activate_protected_mode(app, auth, client):
    auth.register()
    auth.login()
    with app.app_context():
        user = User.query.filter_by(email="alice@example.com").one()
        active = user.active_mode
        for mode in user.modes:
            if mode.id != active.id:
                mode.is_protected = True
                mode.set_protection_pin("2468")
        db.session.commit()
        active_id = active.id

    response = client.post(f"/modes/{active_id}/delete")

    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email="alice@example.com").one()
        assert user.active_mode is None


def test_browser_pin_attempts_are_rate_limited(app, auth, client):
    auth.register()
    auth.login()
    with app.app_context():
        user = User.query.filter_by(email="alice@example.com").one()
        mode = next(mode for mode in user.modes if not mode.is_active)
        mode.is_protected = True
        mode.set_protection_pin("2468")
        db.session.commit()
        mode_id = mode.id

    responses = [
        client.post(
            f"/modes/{mode_id}/unlock?next=activate",
            data={"pin": "1111", "submit": "Unlock mode"},
        )
        for _ in range(app.config["PIN_ATTEMPT_LIMIT"])
    ]
    correct_while_locked = client.post(
        f"/modes/{mode_id}/unlock?next=activate",
        data={"pin": "2468", "submit": "Unlock mode"},
    )

    assert all(response.status_code == 200 for response in responses[:-1])
    assert responses[-1].status_code == 429
    assert correct_while_locked.status_code == 429
    assert b"Too many incorrect attempts" in correct_while_locked.data
