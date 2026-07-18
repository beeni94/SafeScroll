from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, logout_user

from safescroll.devices import bp
from safescroll.extensions import db
from safescroll.forms import EmptyForm
from safescroll.models import Device, UserSession
from safescroll.utils import utcnow


@bp.get("/devices")
@login_required
def list_devices():
    devices = (
        Device.query.filter_by(user_id=current_user.id)
        .order_by(Device.last_seen_at.desc())
        .all()
    )
    return render_template(
        "devices/list.html",
        devices=devices,
        action_form=EmptyForm(),
        active_mode=current_user.active_mode,
    )


@bp.post("/devices/<int:device_id>/revoke")
@login_required
def revoke_device(device_id: int):
    device = Device.query.filter_by(id=device_id, user_id=current_user.id).first_or_404()
    now = utcnow()
    device.revoked_at = now
    UserSession.query.filter_by(device_id=device.id, revoked_at=None).update(
        {"revoked_at": now}, synchronize_session=False
    )
    current_device_session = UserSession.query.filter_by(
        id=session.get("auth_session_id"), device_id=device.id
    ).first()
    db.session.commit()
    if current_device_session:
        device_client_token = session.get("device_client_token")
        logout_user()
        session.clear()
        if device_client_token:
            session["device_client_token"] = device_client_token
        flash("This device was revoked and signed out.", "success")
        return redirect(url_for("auth.login"))
    flash("Device access has been revoked.", "success")
    if request.args.get("next") == "extension":
        return redirect(url_for("dashboard.extension"))
    return redirect(url_for("devices.list_devices"))


@bp.post("/devices/<int:device_id>/remove")
@login_required
def remove_device(device_id: int):
    device = Device.query.filter_by(id=device_id, user_id=current_user.id).first_or_404()
    if device.revoked_at is None:
        flash("Revoke the device before removing it.", "warning")
        return redirect(url_for("devices.list_devices"))
    db.session.delete(device)
    db.session.commit()
    flash("Device removed from your account.", "success")
    return redirect(url_for("devices.list_devices"))
