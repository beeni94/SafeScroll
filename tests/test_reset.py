from datetime import timedelta
from urllib.parse import urlsplit

from safescroll.extensions import db
from safescroll.models import PasswordResetToken
from safescroll.utils import utcnow

TEST_PASSWORD = "CorrectHorse1!"
NEW_PASSWORD = "AnEvenStronger2!"


def request_reset(client, email):
    return client.post(
        "/forgot-password",
        data={"email": email, "submit": "Send reset link"},
        follow_redirects=True,
    )


def test_forgot_password_is_generic_and_only_queues_known_accounts(app, auth, client):
    auth.register()

    known = request_reset(client, "alice@example.com")
    unknown = request_reset(client, "nobody@example.com")

    assert known.status_code == 200
    assert unknown.status_code == 200
    assert b"if an account matches" in known.data.lower()
    assert b"if an account matches" in unknown.data.lower()
    assert b"alice@example.com" not in known.data.lower()
    assert b"nobody@example.com" not in unknown.data.lower()
    assert len(app.extensions["mail_outbox"]) == 1
    assert app.extensions["mail_outbox"][0]["to"] == "alice@example.com"


def test_valid_reset_changes_password_and_token_cannot_be_reused(app, auth, client):
    auth.register()
    request_reset(client, "alice@example.com")
    reset_url = app.extensions["mail_outbox"][-1]["reset_url"]
    reset_path = urlsplit(reset_url).path

    assert client.get(reset_path).status_code == 200
    changed = client.post(
        reset_path,
        data={
            "password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
            "submit": "Reset password",
        },
    )
    assert changed.status_code == 302

    assert auth.login(password=TEST_PASSWORD, follow_redirects=True).status_code == 200
    accepted = auth.login(password=NEW_PASSWORD)
    assert accepted.status_code == 302

    auth.logout()
    replayed = client.post(
        reset_path,
        data={
            "password": "AnotherStrong3!",
            "confirm_password": "AnotherStrong3!",
            "submit": "Reset password",
        },
    )
    assert replayed.status_code in (302, 400, 404)


def test_reset_rejects_tampered_token_and_password_mismatch(app, auth, client):
    auth.register()
    request_reset(client, "alice@example.com")
    reset_path = urlsplit(app.extensions["mail_outbox"][-1]["reset_url"]).path

    tampered = client.get(f"{reset_path}tampered", follow_redirects=True)
    mismatch = client.post(
        reset_path,
        data={
            "password": NEW_PASSWORD,
            "confirm_password": "DifferentStrong3!",
            "submit": "Reset password",
        },
        follow_redirects=True,
    )

    assert tampered.status_code == 200
    assert mismatch.status_code == 200
    assert auth.login(password=TEST_PASSWORD).status_code == 302


def test_expired_reset_token_is_rejected(app, auth, client):
    auth.register()
    request_reset(client, "alice@example.com")
    reset_path = urlsplit(app.extensions["mail_outbox"][-1]["reset_url"]).path
    raw_token = reset_path.rsplit("/", 1)[-1]

    with app.app_context():
        token_record = PasswordResetToken.from_raw_token(raw_token)
        token_record.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()

    response = client.get(reset_path)
    assert response.status_code == 302
