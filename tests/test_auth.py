from urllib.parse import urlsplit

from werkzeug.security import check_password_hash

from safescroll import create_app
from safescroll.extensions import db
from safescroll.models import User


TEST_PASSWORD = "CorrectHorse1!"


def path_from_location(response):
    return urlsplit(response.headers["Location"]).path


def test_registration_normalizes_email_and_hashes_password(app, auth):
    response = auth.register(email="Alice@Example.COM")

    assert response.status_code == 302
    with app.app_context():
        user = db.session.execute(db.select(User)).scalar_one()
        assert user.email == "alice@example.com"
        assert user.password_hash != TEST_PASSWORD
        assert check_password_hash(user.password_hash, TEST_PASSWORD)


def test_registration_rejects_duplicate_email_case_insensitively(app, auth):
    assert auth.register(email="alice@example.com").status_code == 302
    response = auth.register(
        full_name="Second Alice", email="ALICE@example.com", follow_redirects=True
    )

    assert response.status_code == 200
    with app.app_context():
        assert len(db.session.execute(db.select(User)).scalars().all()) == 1


def test_registration_rejects_invalid_email_weak_password_and_mismatch(app, auth):
    invalid_email = auth.register(email="not-an-email", follow_redirects=True)
    weak_password = auth.register(
        email="weak@example.com", password="password", follow_redirects=True
    )
    mismatch = auth.register(
        email="mismatch@example.com",
        confirm_password="DifferentPassword1!",
        follow_redirects=True,
    )

    assert invalid_email.status_code == 200
    assert weak_password.status_code == 200
    assert mismatch.status_code == 200
    with app.app_context():
        assert db.session.execute(db.select(User)).scalars().all() == []


def test_login_uses_real_credentials_and_logout_ends_access(auth, client):
    auth.register()

    rejected = auth.login(password="WrongPassword1!", follow_redirects=True)
    assert rejected.status_code == 200
    assert client.get("/dashboard").status_code == 302

    accepted = auth.login()
    assert accepted.status_code == 302
    assert path_from_location(accepted) == "/dashboard"
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert b"Alice" in dashboard.data

    logged_out = auth.logout()
    assert logged_out.status_code == 302
    assert client.get("/dashboard").status_code == 302


def test_remember_me_sets_a_persistent_session_cookie(auth):
    auth.register()
    response = auth.login(remember_me=True)

    cookies = response.headers.getlist("Set-Cookie")
    assert any(
        cookie.startswith("safescroll_session=")
        and ("Expires=" in cookie or "Max-Age=" in cookie)
        for cookie in cookies
    )


def test_login_rejects_an_external_next_redirect(auth, client):
    auth.register()
    response = client.post(
        "/login?next=https://attacker.example/phish",
        data={
            "email": "alice@example.com",
            "password": TEST_PASSWORD,
            "submit": "Log in",
        },
    )

    assert response.status_code == 302
    assert not response.headers["Location"].startswith("https://attacker.example")


def test_login_accepts_a_local_next_redirect(auth, client):
    auth.register()
    response = client.post(
        "/login?next=/modes",
        data={
            "email": "alice@example.com",
            "password": TEST_PASSWORD,
            "submit": "Log in",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/modes")


def test_csrf_rejects_a_post_without_a_token():
    csrf_app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "csrf-test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "WTF_CSRF_ENABLED": True,
            "MAIL_SUPPRESS_SEND": True,
        }
    )

    with csrf_app.app_context():
        db.create_all()
    try:
        response = csrf_app.test_client().post(
            "/login",
            data={"email": "alice@example.com", "password": TEST_PASSWORD},
        )
        assert response.status_code == 400
    finally:
        with csrf_app.app_context():
            db.session.remove()
            db.drop_all()
