"""Extension configuration lifecycle and automatic mode versioning.

The version bump is performed with ``config_version = config_version + 1``
inside the same database transaction as the mode mutation.  A user is bumped
at most once per transaction, even when one form replaces several category,
keyword, and schedule rows.
"""

from __future__ import annotations

from sqlalchemy import event, insert, select, update
from sqlalchemy.orm import Session

from safescroll.extensions import db
from safescroll.models import (
    ExtensionConfiguration,
    ModeCategory,
    ModeKeyword,
    ModeSchedule,
    User,
    ViewingMode,
)
from safescroll.utils import utcnow


_PENDING_USER_IDS = "safescroll_extension_config_pending_user_ids"
_PENDING_USERS = "safescroll_extension_config_pending_users"
_BUMPED_USER_IDS = "safescroll_extension_config_bumped_user_ids"


def get_or_create_extension_configuration(
    user_or_id: User | int,
) -> ExtensionConfiguration:
    """Return a user's configuration row without committing the caller's work."""

    user_id = user_or_id.id if isinstance(user_or_id, User) else int(user_or_id)
    configuration = ExtensionConfiguration.query.filter_by(user_id=user_id).first()
    if configuration is not None:
        return configuration

    active_mode_id = db.session.execute(
        select(ViewingMode.id).where(
            ViewingMode.user_id == user_id,
            ViewingMode.is_active.is_(True),
        )
    ).scalar_one_or_none()
    configuration = ExtensionConfiguration(
        user_id=user_id,
        config_version=1,
        active_mode_id=active_mode_id,
        sync_status="never_synced",
    )
    db.session.add(configuration)
    return configuration


def mark_extension_configuration_changed(user_or_id: User | int) -> None:
    """Mark a user for the automatic bump performed by the next ORM flush.

    Normal mode and nested configuration mutations are discovered
    automatically.  This hook is available for future configuration models
    that need to participate in the same transaction.
    """

    session = db.session()
    if isinstance(user_or_id, User):
        if user_or_id.id is None:
            session.info.setdefault(_PENDING_USERS, set()).add(user_or_id)
        else:
            session.info.setdefault(_PENDING_USER_IDS, set()).add(user_or_id.id)
    else:
        session.info.setdefault(_PENDING_USER_IDS, set()).add(int(user_or_id))


@event.listens_for(User, "after_insert")
def _create_configuration_for_new_user(_mapper, connection, user: User) -> None:
    """Give every new account a configuration row, including accounts with no modes."""

    now = utcnow()
    connection.execute(
        insert(ExtensionConfiguration.__table__).values(
            user_id=user.id,
            config_version=1,
            active_mode_id=None,
            sync_status="never_synced",
            created_at=now,
            updated_at=now,
        )
    )


def _user_for_config_object(obj):
    if isinstance(obj, ViewingMode):
        return obj.user_id, obj.user
    if isinstance(obj, (ModeCategory, ModeKeyword, ModeSchedule)):
        mode = obj.mode
        if mode is not None:
            return mode.user_id, mode.user
    return None, None


@event.listens_for(Session, "before_flush")
def _collect_changed_mode_owners(session: Session, _flush_context, _instances) -> None:
    user_ids = session.info.setdefault(_PENDING_USER_IDS, set())
    users = session.info.setdefault(_PENDING_USERS, set())

    deleted_user_ids = {
        user.id for user in session.deleted if isinstance(user, User) and user.id is not None
    }
    for obj in tuple(session.new) + tuple(session.dirty) + tuple(session.deleted):
        user_id, user = _user_for_config_object(obj)
        if user_id is not None and user_id not in deleted_user_ids:
            user_ids.add(user_id)
        elif user is not None and user not in session.deleted:
            users.add(user)


@event.listens_for(Session, "after_flush_postexec")
def _bump_changed_configurations(session: Session, _flush_context) -> None:
    user_ids = set(session.info.pop(_PENDING_USER_IDS, set()))
    pending_users = set(session.info.pop(_PENDING_USERS, set()))
    user_ids.update(user.id for user in pending_users if user.id is not None)

    bumped = session.info.setdefault(_BUMPED_USER_IDS, set())
    if not user_ids:
        return

    connection = session.connection()
    configuration_table = ExtensionConfiguration.__table__
    user_table = User.__table__
    mode_table = ViewingMode.__table__
    now = utcnow()

    for user_id in sorted(user_ids):
        # Do not recreate configuration rows while an account is being deleted.
        user_exists = connection.execute(
            select(user_table.c.id).where(user_table.c.id == user_id)
        ).scalar_one_or_none()
        if user_exists is None:
            continue

        active_mode_id = connection.execute(
            select(mode_table.c.id).where(
                mode_table.c.user_id == user_id,
                mode_table.c.is_active.is_(True),
            )
        ).scalar_one_or_none()
        values = {
            "active_mode_id": active_mode_id,
            "sync_status": "pending",
            "updated_at": now,
        }
        # Later flushes in the same transaction still need to refresh the
        # active-mode snapshot (for example, delete-active then choose a
        # replacement), but the externally visible version advances once.
        if user_id not in bumped:
            values["config_version"] = configuration_table.c.config_version + 1
        result = connection.execute(
            update(configuration_table)
            .where(configuration_table.c.user_id == user_id)
            .values(**values)
        )
        if result.rowcount == 0:
            connection.execute(
                insert(configuration_table).values(
                    user_id=user_id,
                    config_version=1,
                    active_mode_id=active_mode_id,
                    sync_status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
        bumped.add(user_id)

    # SQL expressions bypass ORM state bookkeeping. Expire any configuration
    # already present in this session so subsequent API serialization is fresh.
    for obj in tuple(session.identity_map.values()):
        if isinstance(obj, ExtensionConfiguration) and obj.user_id in user_ids:
            session.expire(obj)


def _clear_transaction_markers(session: Session) -> None:
    session.info.pop(_PENDING_USER_IDS, None)
    session.info.pop(_PENDING_USERS, None)
    session.info.pop(_BUMPED_USER_IDS, None)


@event.listens_for(Session, "after_commit")
def _clear_after_commit(session: Session) -> None:
    _clear_transaction_markers(session)


@event.listens_for(Session, "after_rollback")
def _clear_after_rollback(session: Session) -> None:
    _clear_transaction_markers(session)
