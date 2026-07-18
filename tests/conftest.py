"""Shared pytest fixtures for the SafeScroll Flask application."""

import pytest

from safescroll import create_app
from safescroll.extensions import db


TEST_PASSWORD = "CorrectHorse1!"


@pytest.fixture
def app():
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "WTF_CSRF_ENABLED": False,
            "MAIL_SUPPRESS_SEND": True,
            "APP_BASE_URL": "http://localhost",
        }
    )

    with application.app_context():
        db.create_all()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


class AuthActions:
    """Small HTTP helper that deliberately goes through the real forms."""

    def __init__(self, client):
        self.client = client

    def register(
        self,
        *,
        full_name="Alice Example",
        email="alice@example.com",
        password=TEST_PASSWORD,
        confirm_password=None,
        follow_redirects=False,
    ):
        if confirm_password is None:
            confirm_password = password
        return self.client.post(
            "/register",
            data={
                "full_name": full_name,
                "email": email,
                "password": password,
                "confirm_password": confirm_password,
                "submit": "Create account",
            },
            follow_redirects=follow_redirects,
        )

    def login(
        self,
        *,
        email="alice@example.com",
        password=TEST_PASSWORD,
        remember_me=False,
        follow_redirects=False,
    ):
        data = {
            "email": email,
            "password": password,
            "submit": "Log in",
        }
        if remember_me:
            data["remember_me"] = "y"
        return self.client.post(
            "/login", data=data, follow_redirects=follow_redirects
        )

    def logout(self, *, follow_redirects=False):
        return self.client.post("/logout", follow_redirects=follow_redirects)


@pytest.fixture
def auth(client):
    return AuthActions(client)
