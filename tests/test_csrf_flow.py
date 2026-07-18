import re
from datetime import timedelta

from safescroll import create_app
from safescroll.api.models import APIToken
from safescroll.extensions import db
from safescroll.models import User

from conftest import TEST_PASSWORD


def _csrf_token(response) -> str:
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match, "Rendered form did not include a CSRF token"
    return match.group(1).decode()


def test_csrf_enabled_registration_login_and_logout_flow():
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "csrf-happy-path-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "WTF_CSRF_ENABLED": True,
            "MAIL_SUPPRESS_SEND": True,
        }
    )
    client = app.test_client()

    register_page = client.get("/register")
    registered = client.post(
        "/register",
        data={
            "csrf_token": _csrf_token(register_page),
            "full_name": "CSRF User",
            "email": "csrf@example.com",
            "password": TEST_PASSWORD,
            "confirm_password": TEST_PASSWORD,
            "submit": "Create account",
        },
    )
    assert registered.status_code == 302

    login_page = client.get("/login")
    logged_in = client.post(
        "/login",
        data={
            "csrf_token": _csrf_token(login_page),
            "email": "csrf@example.com",
            "password": TEST_PASSWORD,
            "submit": "Log in",
        },
    )
    assert logged_in.status_code == 302
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200

    with app.app_context():
        user = User.query.filter_by(email="csrf@example.com").one()
        token, raw_token = APIToken.issue(user, lifetime=timedelta(days=1))
        db.session.add(token)
        db.session.commit()

    # The bearer-authenticated API is intentionally CSRF-exempt, while the
    # browser session alone still cannot authenticate it.
    api_sync = client.post(
        "/api/extension/sync",
        json={"device_identifier": "csrf-extension-device"},
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert api_sync.status_code == 200
    assert client.get("/api/modes").status_code == 401

    # Pairing is the one API operation authorized by the signed-in website
    # session, so it performs its own explicit CSRF validation.
    pairing_without_csrf = client.post("/api/extension/pair", json={})
    pairing_with_csrf = client.post(
        "/api/extension/pair",
        json={},
        headers={"X-CSRFToken": _csrf_token(dashboard)},
    )
    assert pairing_without_csrf.status_code == 400
    assert pairing_without_csrf.json["error"]["code"] == "invalid_csrf_token"
    assert pairing_with_csrf.status_code == 201

    logged_out = client.post(
        "/logout", data={"csrf_token": _csrf_token(dashboard)}
    )
    assert logged_out.status_code == 302
    assert client.get("/dashboard").status_code == 302

    with app.app_context():
        db.session.remove()
        db.drop_all()
