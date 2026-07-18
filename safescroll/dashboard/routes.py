from flask import current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from safescroll.dashboard import bp
from safescroll.extensions import db
from safescroll import models
from safescroll.services import analytics_data, dashboard_data


@bp.route("/dashboard")
@login_required
def index():
    return render_template("dashboard/index.html", **dashboard_data(current_user))


@bp.route("/analytics")
@login_required
def analytics():
    return render_template("dashboard/analytics.html", **analytics_data(current_user))


@bp.route("/extension")
@login_required
def extension():
    """Render extension connections without exposing bearer credentials."""
    extension_device_model = getattr(models, "ExtensionDevice", None)
    extension_configuration_model = getattr(models, "ExtensionConfiguration", None)

    devices = []
    using_legacy_devices = False
    if extension_device_model is not None:
        devices = (
            extension_device_model.query.filter_by(user_id=current_user.id)
            .order_by(
                extension_device_model.last_sync_at.desc(),
                extension_device_model.last_seen_at.desc(),
            )
            .all()
        )

    # Keep pre-migration extension records visible until a new device checks in.
    if not devices:
        legacy_device_model = getattr(models, "Device", None)
        if legacy_device_model is not None:
            devices = (
                legacy_device_model.query.filter_by(
                    user_id=current_user.id, device_type="extension"
                )
                .order_by(legacy_device_model.last_seen_at.desc())
                .all()
            )
            using_legacy_devices = bool(devices)

    connected_devices = [device for device in devices if device.is_connected]
    connected_device = connected_devices[0] if connected_devices else None
    configuration = None
    if extension_configuration_model is not None:
        configuration = extension_configuration_model.query.filter_by(
            user_id=current_user.id
        ).first()

    sync_candidates = [
        getattr(device, "last_sync_at", None)
        or getattr(device, "last_synced_at", None)
        for device in devices
    ]
    last_sync_at = max((value for value in sync_candidates if value), default=None)
    if configuration is not None:
        configuration_sync_at = getattr(configuration, "last_sync_at", None)
        if configuration_sync_at and (
            last_sync_at is None or configuration_sync_at > last_sync_at
        ):
            last_sync_at = configuration_sync_at

    return render_template(
        "dashboard/extension.html",
        devices=devices,
        connected_devices=connected_devices,
        connected_device=connected_device,
        using_legacy_devices=using_legacy_devices,
        extension_configuration=configuration,
        last_sync_at=last_sync_at,
        active_mode=current_user.active_mode,
        extension_install_url=current_app.config.get("EXTENSION_INSTALL_URL"),
    )


@bp.post("/extension/devices/<int:device_id>/disconnect")
@login_required
def disconnect_extension_device(device_id: int):
    """Revoke an extension device and every API token bound to it."""
    extension_device_model = getattr(models, "ExtensionDevice", None)
    if extension_device_model is None:
        return redirect(url_for("dashboard.extension"))

    device = extension_device_model.query.filter_by(
        id=device_id, user_id=current_user.id
    ).first_or_404()

    if device.is_connected:
        device.revoke()
        legacy_device = models.Device.query.filter_by(
            user_id=current_user.id,
            device_type="extension",
            client_identifier=device.device_identifier,
        ).first()
        if legacy_device is not None and legacy_device.revoked_at is None:
            legacy_device.revoked_at = device.revoked_at

        configuration = models.ExtensionConfiguration.query.filter_by(
            user_id=current_user.id
        ).first()
        remaining_connected = models.ExtensionDevice.query.filter(
            models.ExtensionDevice.user_id == current_user.id,
            models.ExtensionDevice.id != device.id,
            models.ExtensionDevice.revoked_at.is_(None),
        ).first()
        if configuration is not None and remaining_connected is None:
            configuration.sync_status = "disconnected"
        if configuration is not None:
            db.session.add(
                models.SyncLog(
                    user_id=current_user.id,
                    extension_device_id=device.id,
                    config_version=configuration.config_version,
                    status="disconnected",
                    message="The extension was disconnected from the website.",
                )
            )
        db.session.add(
            models.ExtensionEvent(
                user_id=current_user.id,
                extension_device_id=device.id,
                event_type="connection",
                status="disconnected",
                message="The extension was disconnected from the website.",
                extension_version=device.extension_version,
            )
        )
        db.session.commit()
        flash(f"{device.name} was disconnected from SafeScroll.", "success")
    else:
        flash(f"{device.name} is already disconnected.", "info")

    return redirect(url_for("dashboard.extension"))
