from copy import deepcopy
import time

from flask import flash, g, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from safescroll.extensions import db
from safescroll.forms import EmptyForm, ModeForm, ModeUnlockForm
from safescroll.models import ViewingMode
from safescroll.modes import bp
from safescroll.pin_security import (
    clear_pin_failures,
    pin_principal,
    pin_retry_after,
    record_pin_failure,
)
from safescroll.utils import comma_list, parse_comma_list, utcnow


def _owned_mode(mode_id: int) -> ViewingMode:
    return ViewingMode.query.filter_by(id=mode_id, user_id=current_user.id).first_or_404()


def _is_unlocked(mode: ViewingMode) -> bool:
    if not mode.is_protected:
        return True
    unlocked = session.get("mode_unlocks", {})
    return float(unlocked.get(str(mode.id), 0)) > time.time()


def _require_unlock(mode: ViewingMode, action: str):
    if _is_unlocked(mode):
        return None
    flash(f"Enter the protection PIN for {mode.name} to continue.", "warning")
    return redirect(url_for("modes.unlock_mode", mode_id=mode.id, next=action))


def _activate(mode: ViewingMode):
    ViewingMode.query.filter_by(user_id=current_user.id, is_active=True).update(
        {"is_active": False}, synchronize_session="fetch"
    )
    db.session.flush()
    mode.is_active = True
    mode.last_used_at = utcnow()
    db.session.commit()
    flash(f"{mode.name} is now active.", "success")


def _duplicate(source: ViewingMode):
    existing = {mode.name.casefold() for mode in current_user.modes}
    base = f"{source.name} copy"
    name = base
    suffix = 2
    while name.casefold() in existing:
        name = f"{base} {suffix}"
        suffix += 1
    clone = ViewingMode(
        user_id=current_user.id,
        name=name,
        icon=source.icon,
        color=source.color,
        description=source.description,
        preferred_categories=deepcopy(source.preferred_categories or []),
        blocked_categories=deepcopy(source.blocked_categories or []),
        preferred_keywords=deepcopy(source.preferred_keywords or []),
        blocked_keywords=deepcopy(source.blocked_keywords or []),
        strictness=source.strictness,
        schedule=deepcopy(source.schedule or {}),
        is_protected=False,
        is_active=False,
    )
    db.session.add(clone)
    db.session.commit()
    flash(f"Created {clone.name}.", "success")


def _delete(mode: ViewingMode):
    was_active = mode.is_active
    name = mode.name
    db.session.delete(mode)
    db.session.flush()
    if was_active:
        replacement = ViewingMode.query.filter_by(
            user_id=current_user.id, is_protected=False
        ).order_by(ViewingMode.created_at.asc()).first()
        if replacement:
            replacement.is_active = True
            replacement.last_used_at = utcnow()
    db.session.commit()
    unlocks = session.get("mode_unlocks", {})
    unlocks.pop(str(mode.id), None)
    session["mode_unlocks"] = unlocks
    flash(f"{name} was deleted.", "success")


def _apply_form(mode: ViewingMode, form: ModeForm) -> None:
    mode.name = form.name.data.strip()
    mode.icon = (form.icon.data or "").strip() or "🎯"
    color_field = getattr(form, "color", None)
    if color_field is not None:
        mode.color = color_field.data
    mode.description = (form.description.data or "").strip()
    mode.preferred_categories = parse_comma_list(form.preferred_categories.data)
    mode.blocked_categories = parse_comma_list(form.blocked_categories.data)
    mode.preferred_keywords = parse_comma_list(form.preferred_keywords.data)
    mode.blocked_keywords = parse_comma_list(form.blocked_keywords.data)
    mode.strictness = form.strictness.data
    if form.schedule_start.data and form.schedule_end.data:
        mode.schedule = {
            "days": form.schedule_days.data,
            "start": form.schedule_start.data.strftime("%H:%M"),
            "end": form.schedule_end.data.strftime("%H:%M"),
        }
    else:
        mode.schedule = {}
    mode.is_protected = bool(form.is_protected.data)
    if not mode.is_protected:
        mode.protection_pin_hash = None
    elif form.protection_pin.data:
        mode.set_protection_pin(form.protection_pin.data)


def _populate_form(form: ModeForm, mode: ViewingMode) -> None:
    form.name.data = mode.name
    form.icon.data = mode.icon
    color_field = getattr(form, "color", None)
    if color_field is not None:
        color_field.data = mode.color
    form.description.data = mode.description
    form.preferred_categories.data = comma_list(mode.preferred_categories)
    form.blocked_categories.data = comma_list(mode.blocked_categories)
    form.preferred_keywords.data = comma_list(mode.preferred_keywords)
    form.blocked_keywords.data = comma_list(mode.blocked_keywords)
    form.strictness.data = mode.strictness
    form.is_protected.data = mode.is_protected
    schedule = mode.schedule or {}
    form.schedule_days.data = schedule.get("days", [])
    if schedule.get("start"):
        from datetime import time

        form.schedule_start.data = time.fromisoformat(schedule["start"])
    if schedule.get("end"):
        from datetime import time

        form.schedule_end.data = time.fromisoformat(schedule["end"])


@bp.get("/modes")
@login_required
def list_modes():
    modes = ViewingMode.query.filter_by(user_id=current_user.id).order_by(
        ViewingMode.is_active.desc(), ViewingMode.created_at.asc()
    ).all()
    return render_template(
        "modes/list.html", modes=modes, action_form=EmptyForm(), active_mode=current_user.active_mode
    )


@bp.route("/modes/new", methods=["GET", "POST"])
@login_required
def create_mode():
    form = ModeForm()
    if form.validate_on_submit():
        if form.is_protected.data and not form.protection_pin.data:
            form.protection_pin.errors.append("Set a PIN for a protected mode.")
            return render_template(
                "modes/form.html",
                form=form,
                mode=None,
                editing=False,
                active_mode=current_user.active_mode,
            ), 400
        mode = ViewingMode(user_id=current_user.id, is_active=not bool(current_user.modes))
        _apply_form(mode, form)
        if mode.is_active:
            mode.last_used_at = utcnow()
        db.session.add(mode)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.name.errors.append("You already have a mode with this name.")
        else:
            flash(f"{mode.name} was created.", "success")
            return redirect(url_for("modes.list_modes"))
    return render_template(
        "modes/form.html", form=form, mode=None, editing=False, active_mode=current_user.active_mode
    )


@bp.route("/modes/<int:mode_id>/edit", methods=["GET", "POST"])
@login_required
def edit_mode(mode_id: int):
    mode = _owned_mode(mode_id)
    unlock_response = _require_unlock(mode, "edit")
    if unlock_response:
        return unlock_response
    form = ModeForm()
    if form.validate_on_submit():
        if form.is_protected.data and not (
            form.protection_pin.data or mode.protection_pin_hash
        ):
            form.protection_pin.errors.append("Set a PIN for a protected mode.")
            return render_template(
                "modes/form.html",
                form=form,
                mode=mode,
                editing=True,
                active_mode=current_user.active_mode,
            ), 400
        pin_changed = bool(form.protection_pin.data)
        _apply_form(mode, form)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.name.errors.append("You already have a mode with this name.")
        else:
            if pin_changed or not mode.is_protected:
                unlocks = session.get("mode_unlocks", {})
                unlocks.pop(str(mode.id), None)
                session["mode_unlocks"] = unlocks
            flash(f"{mode.name} was updated.", "success")
            return redirect(url_for("modes.list_modes"))
    elif not form.is_submitted():
        _populate_form(form, mode)
    return render_template(
        "modes/form.html", form=form, mode=mode, editing=True, active_mode=current_user.active_mode
    )


@bp.post("/modes/<int:mode_id>/activate")
@login_required
def activate_mode(mode_id: int):
    mode = _owned_mode(mode_id)
    unlock_response = _require_unlock(mode, "activate")
    if unlock_response:
        return unlock_response
    _activate(mode)
    return redirect(url_for("modes.list_modes"))


@bp.post("/modes/<int:mode_id>/duplicate")
@login_required
def duplicate_mode(mode_id: int):
    source = _owned_mode(mode_id)
    unlock_response = _require_unlock(source, "duplicate")
    if unlock_response:
        return unlock_response
    _duplicate(source)
    return redirect(url_for("modes.list_modes"))


@bp.post("/modes/<int:mode_id>/delete")
@login_required
def delete_mode(mode_id: int):
    mode = _owned_mode(mode_id)
    unlock_response = _require_unlock(mode, "delete")
    if unlock_response:
        return unlock_response
    _delete(mode)
    return redirect(url_for("modes.list_modes"))


@bp.route("/modes/<int:mode_id>/unlock", methods=["GET", "POST"])
@login_required
def unlock_mode(mode_id: int):
    mode = _owned_mode(mode_id)
    allowed_actions = {"edit", "activate", "duplicate", "delete", "list"}
    action = request.args.get("next", "list")
    if action not in allowed_actions:
        action = "list"
    if not mode.is_protected:
        return redirect(
            url_for("modes.edit_mode", mode_id=mode.id)
            if action == "edit"
            else url_for("modes.list_modes")
        )

    form = ModeUnlockForm()
    response_status = 200
    if form.validate_on_submit():
        managed_session = getattr(g, "current_user_session", None)
        principal = pin_principal(
            "web-session",
            managed_session.id if managed_session is not None else current_user.id,
        )
        retry_after = pin_retry_after(mode.id, principal)
        if retry_after is not None:
            form.pin.errors.append(
                f"Too many incorrect attempts. Try again in {retry_after} seconds."
            )
            response_status = 429
        elif mode.check_protection_pin(form.pin.data):
            clear_pin_failures(mode.id, principal)
            unlocks = session.get("mode_unlocks", {})
            unlocks[str(mode.id)] = time.time() + 5 * 60
            session["mode_unlocks"] = unlocks
            if action == "edit":
                return redirect(url_for("modes.edit_mode", mode_id=mode.id))
            if action == "activate":
                _activate(mode)
            elif action == "duplicate":
                _duplicate(mode)
            elif action == "delete":
                _delete(mode)
            return redirect(url_for("modes.list_modes"))
        else:
            retry_after = record_pin_failure(mode.id, principal)
            if retry_after is not None:
                form.pin.errors.append(
                    f"Too many incorrect attempts. Try again in {retry_after} seconds."
                )
                response_status = 429
            else:
                form.pin.errors.append("That protection PIN is incorrect.")
    return (
        render_template(
            "modes/unlock.html",
            form=form,
            mode=mode,
            action=action,
            active_mode=current_user.active_mode,
        ),
        response_status,
    )
