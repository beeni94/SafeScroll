import os
from datetime import datetime

import click
from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from flask_login import current_user, logout_user
from flask_wtf.csrf import CSRFError

from safescroll.config import Config
from safescroll.extensions import csrf, db, login_manager
from safescroll.utils import hash_token, utcnow


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    os.makedirs(app.instance_path, exist_ok=True)
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        database_path = os.path.join(app.instance_path, "safescroll.db")
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{database_path}"

    if test_config:
        app.config.update(test_config)

    if (
        app.config.get("APP_ENV") == "production"
        and app.config.get("SECRET_KEY") == "safescroll-development-key-change-me"
    ):
        raise RuntimeError("Set a strong SECRET_KEY before running in production.")

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from safescroll.account import bp as account_bp
    from safescroll.api import bp as api_bp
    from safescroll.auth import bp as auth_bp
    from safescroll.dashboard import bp as dashboard_bp
    from safescroll.devices import bp as devices_bp
    from safescroll.modes import bp as modes_bp
    from safescroll.public import bp as public_bp
    # Registers transaction-aware extension configuration version hooks.
    from safescroll import extension_sync as _extension_sync  # noqa: F401

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(modes_bp)
    csrf.exempt(api_bp)
    app.register_blueprint(api_bp)
    # Keep the versioned Sprint 3 contract and expose the shorter extension
    # contract requested for Chrome clients. Both registrations share identical
    # bearer authentication, CORS, validation, and throttling hooks.
    app.register_blueprint(api_bp, url_prefix="/api", name="api_alias")

    _register_request_hooks(app)
    _register_error_handlers(app)
    _register_commands(app)

    if app.config.get("AUTO_CREATE_DATABASE"):
        with app.app_context():
            db.create_all()
            from safescroll.migrations import (
                migrate_extension_schema,
                migrate_legacy_viewing_modes,
            )

            migrated_modes = migrate_legacy_viewing_modes()
            migrate_extension_schema()
            if migrated_modes:
                app.logger.info(
                    "Migrated %s legacy viewing mode(s) to the normalized schema.",
                    migrated_modes,
                )

    @app.context_processor
    def inject_layout_context():
        active_mode = current_user.active_mode if current_user.is_authenticated else None
        return {"active_mode": active_mode, "current_year": datetime.now().year}

    return app


def _register_request_hooks(app: Flask) -> None:
    @app.before_request
    def validate_managed_session():
        if request.endpoint == "static":
            return None
        # API requests authenticate independently with a bearer token. Session
        # cookies must never authenticate mutations on the CSRF-exempt API.
        if request.path == "/api" or request.path.startswith("/api/"):
            return None
        if not current_user.is_authenticated:
            return None

        from safescroll.models import UserSession

        managed_id = session.get("auth_session_id")
        raw_token = session.get("auth_session_token")
        managed = db.session.get(UserSession, managed_id) if managed_id else None

        valid = bool(
            managed
            and raw_token
            and managed.user_id == current_user.id
            and managed.token_hash == hash_token(raw_token)
            and managed.is_valid
            and current_user.is_active
        )
        if not valid:
            device_token = session.get("device_client_token")
            logout_user()
            session.clear()
            if device_token:
                session["device_client_token"] = device_token
            flash("Your session has expired or was revoked. Please log in again.", "warning")
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

        g.current_user_session = managed
        touch_interval = app.config["SESSION_TOUCH_INTERVAL"]
        if utcnow() - managed.last_seen_at >= touch_interval:
            managed.last_seen_at = utcnow()
            if managed.device:
                managed.device.last_seen_at = managed.last_seen_at
            db.session.commit()
        return None

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if current_user.is_authenticated or request.endpoint in {
            "auth.reset_password",
            "account.export_data",
        }:
            response.headers["Cache-Control"] = "no-store, private"
        return response


def _register_error_handlers(app: Flask) -> None:
    def is_api_request() -> bool:
        return request.path == "/api" or request.path.startswith("/api/")

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        return render_template("errors/400.html", error=error), 400

    def handle_http_error(error, status: int):
        if is_api_request():
            from safescroll.api.responses import error as api_error

            codes = {400: "bad_request", 403: "forbidden", 404: "not_found"}
            return api_error(
                codes[status],
                "The requested API resource could not be processed."
                if status != 404
                else "The requested API endpoint does not exist.",
                status=status,
            )
        return render_template(f"errors/{status}.html", error=error), status

    for status in (400, 403, 404):
        app.register_error_handler(
            status,
            lambda error, code=status: handle_http_error(error, code),
        )

    @app.errorhandler(405)
    def handle_method_not_allowed(error):
        if is_api_request():
            from safescroll.api.responses import error as api_error

            methods = sorted(error.valid_methods or [])
            headers = {"Allow": ", ".join(methods)} if methods else None
            return api_error(
                "method_not_allowed",
                "This method is not allowed for the requested API endpoint.",
                status=405,
                details={"allowed_methods": methods} if methods else None,
                headers=headers,
            )
        return error

    @app.errorhandler(413)
    def handle_payload_too_large(error):
        if is_api_request():
            from safescroll.api.responses import error as api_error

            return api_error(
                "payload_too_large",
                "The API request body exceeds the allowed size.",
                status=413,
            )
        return error

    @app.errorhandler(500)
    def handle_server_error(error):
        db.session.rollback()
        if is_api_request():
            from safescroll.api.responses import error as api_error

            return api_error(
                "internal_server_error",
                "The API could not complete the request.",
                status=500,
            )
        return render_template("errors/500.html", error=error), 500


def _register_commands(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db_command():
        """Create any missing database tables."""
        db.create_all()
        from safescroll.migrations import (
            migrate_extension_schema,
            migrate_legacy_viewing_modes,
        )

        migrated_modes = migrate_legacy_viewing_modes()
        migrate_extension_schema()
        click.echo("SafeScroll database initialized.")
        if migrated_modes:
            click.echo(f"Migrated {migrated_modes} legacy viewing mode(s).")

    @app.cli.command("seed-demo")
    @click.option("--email", default="demo@safescroll.local", show_default=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def seed_demo_command(email: str, password: str):
        """Create a local demo user with starter modes."""
        from safescroll.models import User
        from safescroll.services import create_starter_modes
        from safescroll.utils import normalize_email, password_error

        normalized = normalize_email(email)
        if User.query.filter_by(email=normalized).first():
            raise click.ClickException("That email is already registered.")
        error = password_error(password)
        if error:
            raise click.ClickException(error)
        user = User(full_name="Demo User", email=normalized)
        user.set_password(password)
        db.session.add(user)
        create_starter_modes(user)
        db.session.commit()
        click.echo(f"Created {normalized}.")
