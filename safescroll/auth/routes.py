import secrets

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from safescroll.auth import bp
from safescroll.extensions import db
from safescroll.forms import (
    ForgotPasswordForm,
    LoginForm,
    RegistrationForm,
    ResetPasswordForm,
)
from safescroll.mailer import send_password_reset_email
from safescroll.models import Device, PasswordResetToken, User, UserSession
from safescroll.services import create_starter_modes
from safescroll.utils import (
    client_details,
    hash_token,
    is_safe_next_url,
    normalize_email,
    utcnow,
)


def _create_managed_session(user: User, remembered: bool) -> None:
    device_client_token = session.get("device_client_token") or secrets.token_urlsafe(24)
    client_identifier = hash_token(device_client_token)
    device_label, browser, platform = client_details(request.headers.get("User-Agent"))

    device = Device.query.filter_by(
        user_id=user.id, client_identifier=client_identifier
    ).first()
    if device is None:
        device = Device(
            user=user,
            name=device_label,
            device_type="web",
            client_identifier=client_identifier,
            browser=browser,
            platform=platform,
        )
        db.session.add(device)
    else:
        device.name = device_label
        device.browser = browser
        device.platform = platform
        device.revoked_at = None
        device.last_seen_at = utcnow()

    raw_token = secrets.token_urlsafe(32)
    lifetime = current_app.config[
        "REMEMBER_SESSION_LIFETIME" if remembered else "NORMAL_SESSION_LIFETIME"
    ]
    managed_session = UserSession(
        user=user,
        device=device,
        token_hash=hash_token(raw_token),
        device_label=device_label,
        browser=browser,
        platform=platform,
        ip_address=request.remote_addr,
        user_agent=(request.headers.get("User-Agent") or "")[:512],
        remembered=remembered,
        expires_at=utcnow() + lifetime,
    )
    db.session.add(managed_session)
    db.session.flush()

    session["auth_session_id"] = managed_session.id
    session["auth_session_token"] = raw_token
    session["device_client_token"] = device_client_token
    session.permanent = remembered


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = RegistrationForm()
    if form.validate_on_submit():
        try:
            email = normalize_email(form.email.data)
        except ValueError as exc:
            form.email.errors.append(str(exc))
        else:
            if User.query.filter_by(email=email).first():
                form.email.errors.append("An account with this email already exists.")
            else:
                user = User(full_name=form.full_name.data.strip(), email=email)
                user.set_password(form.password.data)
                db.session.add(user)
                create_starter_modes(user)
                db.session.commit()
                flash("Your account is ready. Please log in.", "success")
                return redirect(url_for("auth.login"))
    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        try:
            email = normalize_email(form.email.data)
        except ValueError:
            email = ""
        user = User.query.filter_by(email=email).first() if email else None
        if user and user.is_active and user.check_password(form.password.data):
            next_target = request.args.get("next")
            device_client_token = session.get("device_client_token")
            session.clear()
            if device_client_token:
                session["device_client_token"] = device_client_token
            login_user(user, remember=False, fresh=True)
            _create_managed_session(user, bool(form.remember_me.data))
            user.last_login_at = utcnow()
            db.session.commit()
            return redirect(
                next_target if is_safe_next_url(next_target) else url_for("dashboard.index")
            )
        flash("Invalid email or password.", "error")
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    device_client_token = session.get("device_client_token")
    managed_id = session.get("auth_session_id")
    if managed_id:
        managed = db.session.get(UserSession, managed_id)
        if managed and managed.user_id == current_user.id and managed.revoked_at is None:
            managed.revoked_at = utcnow()
            db.session.commit()
    logout_user()
    session.clear()
    if device_client_token:
        session["device_client_token"] = device_client_token
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        try:
            email = normalize_email(form.email.data)
        except ValueError:
            email = ""
        user = User.query.filter_by(email=email, is_active=True).first() if email else None
        if user:
            now = utcnow()
            PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).update(
                {"used_at": now}, synchronize_session=False
            )
            record, raw_token = PasswordResetToken.issue(
                user, current_app.config["RESET_TOKEN_LIFETIME"]
            )
            db.session.add(record)
            db.session.commit()
            reset_path = url_for("auth.reset_password", token=raw_token)
            reset_url = f"{current_app.config['APP_BASE_URL']}{reset_path}"
            try:
                send_password_reset_email(user.email, reset_url)
            except Exception:
                current_app.logger.exception("Unable to send password reset email")
        flash(
            "If an account matches that email, a reset link has been sent.",
            "success",
        )
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html", form=form)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    record = PasswordResetToken.from_raw_token(token)
    if record is None or not record.is_valid or not record.user.is_active:
        flash("That reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        now = utcnow()
        record.user.set_password(form.password.data)
        record.used_at = now
        UserSession.query.filter_by(user_id=record.user_id, revoked_at=None).update(
            {"revoked_at": now}, synchronize_session=False
        )
        PasswordResetToken.query.filter(
            PasswordResetToken.user_id == record.user_id,
            PasswordResetToken.id != record.id,
            PasswordResetToken.used_at.is_(None),
        ).update({"used_at": now}, synchronize_session=False)
        db.session.commit()
        logout_user()
        session.clear()
        flash("Your password has been reset. You can now log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", form=form, token=token)
