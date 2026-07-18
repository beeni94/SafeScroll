from urllib.parse import urlsplit

import pytest

from safescroll.extensions import db
from safescroll.models import User, ViewingMode


TEST_PASSWORD = "CorrectHorse1!"


def path_from_location(response):
    return urlsplit(response.headers["Location"]).path


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/analytics",
        "/extension",
        "/profile",
        "/security",
        "/devices",
        "/modes",
        "/modes/new",
    ],
)
def test_private_pages_redirect_anonymous_visitors_to_login(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert path_from_location(response) == "/login"


def test_dashboard_uses_the_authenticated_users_data(auth, client):
    auth.register(full_name="Zaphod Unique Dashboard Name")
    auth.login()

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert b"Zaphod" in response.data
    assert b"Beenish" not in response.data


def test_dashboard_escapes_profile_content(auth, client):
    auth.register(full_name="<script>alert(1)</script>")
    auth.login()

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert b"<script>alert(1)</script>" not in response.data
    assert b"&lt;script&gt;" in response.data


def test_dashboard_does_not_render_another_users_modes(app, client):
    with app.app_context():
        alice = User(full_name="Alice Owner", email="alice@example.com")
        alice.set_password(TEST_PASSWORD)
        bob = User(full_name="Bob Owner", email="bob@example.com")
        bob.set_password(TEST_PASSWORD)
        db.session.add_all([alice, bob])
        db.session.flush()
        db.session.add_all(
            [
                ViewingMode(
                    user_id=alice.id, name="Alice Only Mode", is_active=True
                ),
                ViewingMode(user_id=bob.id, name="Bob Secret Mode", is_active=True),
            ]
        )
        db.session.commit()

    logged_in = client.post(
        "/login",
        data={
            "email": "alice@example.com",
            "password": TEST_PASSWORD,
            "submit": "Log in",
        },
    )
    assert logged_in.status_code == 302

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"Alice Only Mode" in response.data
    assert b"Bob Secret Mode" not in response.data


@pytest.mark.parametrize(
    "path",
    ["/", "/privacy", "/terms", "/contact", "/register", "/login", "/forgot-password"],
)
def test_public_pages_render_complete_html(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert b"<!doctype html>" in response.data.lower()


def test_all_authenticated_pages_render_with_real_context(app, auth, client):
    auth.register()
    auth.login()
    with app.app_context():
        mode_id = ViewingMode.query.filter_by(name="Study").one().id

    paths = [
        "/dashboard",
        "/analytics",
        "/extension",
        "/profile",
        "/security",
        "/devices",
        "/modes",
        "/modes/new",
        f"/modes/{mode_id}/edit",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"<!doctype html>" in response.data.lower(), path
