from flask import flash, jsonify, redirect, render_template, session, url_for
from flask_login import current_user, login_required, logout_user

from safescroll.account import bp
from safescroll.extensions import db
from safescroll.forms import ChangePasswordForm, DeleteAccountForm, EmptyForm, ProfileForm
from safescroll.models import PasswordResetToken, User, UserSession
from safescroll.utils import normalize_email, utcnow


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm(obj=current_user)
    if form.validate_on_submit():
        try:
            email = normalize_email(form.email.data)
        except ValueError as exc:
            form.email.errors.append(str(exc))
        else:
            duplicate = User.query.filter(
                User.id != current_user.id, User.email == email
            ).first()
            if duplicate:
                form.email.errors.append("An account with this email already exists.")
            else:
                email_changed = current_user.email != email
                current_user.full_name = form.full_name.data.strip()
                current_user.email = email
                current_user.timezone = form.timezone.data.strip()
                if email_changed:
                    current_user.email_verified = False
                    PasswordResetToken.query.filter_by(
                        user_id=current_user.id, used_at=None
                    ).update({"used_at": utcnow()}, synchronize_session=False)
                db.session.commit()
                flash("Your profile has been updated.", "success")
                return redirect(url_for("account.profile"))
    return render_template("account/profile.html", form=form, active_mode=current_user.active_mode)


@bp.get("/security")
@login_required
def security():
    sessions = (
        UserSession.query.filter_by(user_id=current_user.id, revoked_at=None)
        .filter(UserSession.expires_at > utcnow())
        .order_by(UserSession.last_seen_at.desc())
        .all()
    )
    return render_template(
        "account/security.html",
        change_password_form=ChangePasswordForm(),
        delete_account_form=DeleteAccountForm(),
        action_form=EmptyForm(),
        sessions=sessions,
        current_session_id=session.get("auth_session_id"),
        active_mode=current_user.active_mode,
    )


@bp.post("/security/password")
@login_required
def change_password():
    form = ChangePasswordForm()
    if not form.validate_on_submit() or not current_user.check_password(form.current_password.data):
        if form.current_password.data and not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Current password is incorrect.")
        sessions = (
            UserSession.query.filter_by(user_id=current_user.id, revoked_at=None)
            .filter(UserSession.expires_at > utcnow())
            .order_by(UserSession.last_seen_at.desc())
            .all()
        )
        return render_template(
            "account/security.html",
            change_password_form=form,
            delete_account_form=DeleteAccountForm(),
            action_form=EmptyForm(),
            sessions=sessions,
            current_session_id=session.get("auth_session_id"),
            active_mode=current_user.active_mode,
        ), 400

    now = utcnow()
    current_id = session.get("auth_session_id")
    current_user.set_password(form.new_password.data)
    UserSession.query.filter(
        UserSession.user_id == current_user.id,
        UserSession.revoked_at.is_(None),
        UserSession.id != current_id,
    ).update({"revoked_at": now}, synchronize_session=False)
    PasswordResetToken.query.filter_by(
        user_id=current_user.id, used_at=None
    ).update({"used_at": now}, synchronize_session=False)
    db.session.commit()
    flash("Password updated. Other signed-in sessions were revoked.", "success")
    return redirect(url_for("account.security"))


@bp.post("/security/sessions/<int:session_id>/revoke")
@login_required
def revoke_session(session_id: int):
    managed = UserSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    if managed.id == session.get("auth_session_id"):
        flash("Use Log out to end your current session.", "warning")
    elif managed.revoked_at is None:
        managed.revoked_at = utcnow()
        db.session.commit()
        flash("The session has been revoked.", "success")
    return redirect(url_for("account.security"))


@bp.post("/security/sessions/revoke-others")
@login_required
def revoke_other_sessions():
    UserSession.query.filter(
        UserSession.user_id == current_user.id,
        UserSession.revoked_at.is_(None),
        UserSession.id != session.get("auth_session_id"),
    ).update({"revoked_at": utcnow()}, synchronize_session=False)
    db.session.commit()
    flash("All other sessions have been logged out.", "success")
    return redirect(url_for("account.security"))


@bp.get("/account/export")
@login_required
def export_data():
    response = jsonify(current_user.export_dict())
    response.headers["Content-Disposition"] = "attachment; filename=safescroll-data.json"
    return response


@bp.post("/account/delete")
@bp.post("/profile/delete")
@login_required
def delete_account():
    form = DeleteAccountForm()
    if not form.validate_on_submit() or form.confirmation.data != "DELETE" or not current_user.check_password(form.password.data):
        if form.confirmation.data != "DELETE":
            form.confirmation.errors.append("Type DELETE exactly to confirm.")
        if form.password.data and not current_user.check_password(form.password.data):
            form.password.errors.append("Password is incorrect.")
        sessions = (
            UserSession.query.filter_by(user_id=current_user.id, revoked_at=None)
            .filter(UserSession.expires_at > utcnow())
            .all()
        )
        return render_template(
            "account/security.html",
            change_password_form=ChangePasswordForm(),
            delete_account_form=form,
            action_form=EmptyForm(),
            sessions=sessions,
            current_session_id=session.get("auth_session_id"),
            active_mode=current_user.active_mode,
        ), 400

    user = db.session.get(User, current_user.id)
    logout_user()
    session.clear()
    db.session.delete(user)
    db.session.commit()
    flash("Your SafeScroll account has been permanently deleted.", "success")
    return redirect(url_for("public.home"))
