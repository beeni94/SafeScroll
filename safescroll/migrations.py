"""Small, idempotent compatibility upgrades for pre-Sprint-2 SQLite data.

SafeScroll does not yet ship Alembic migrations.  The first Flask build stored
mode configuration as JSON in ``viewing_modes``; the normalized Sprint-2
schema uses ``modes`` plus child tables.  This helper copies legacy rows once
so an existing local account keeps its modes after upgrading.
"""

import json
import re
from datetime import datetime

from sqlalchemy import func, inspect, select, text

from safescroll.extensions import db
from safescroll.models import (
    Device,
    ExtensionConfiguration,
    ExtensionDevice,
    User,
    ViewingMode,
)
from safescroll.utils import utcnow


LEGACY_MODE_COLORS = {
    "study": "#14B8A6",
    "child": "#3B82F6",
    "fun": "#F59E0B",
    "islamic": "#8B5CF6",
}


def migrate_extension_schema() -> dict[str, int]:
    """Apply idempotent Sprint-3 extension compatibility upgrades.

    ``create_all`` creates the new tables but cannot add a column to an
    existing ``api_tokens`` table.  SQLite installations therefore receive a
    nullable device foreign key in place, preserving every issued token.  Any
    legacy extension-flavoured ``devices`` rows are copied to the dedicated
    extension table, and existing users receive one configuration row.
    """

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    result = {
        "token_columns_added": 0,
        "devices_migrated": 0,
        "configs_created": 0,
        "duplicate_devices_quarantined": 0,
    }

    if db.engine.dialect.name == "sqlite" and "api_tokens" in table_names:
        token_columns = {column["name"] for column in inspector.get_columns("api_tokens")}
        if "extension_device_id" not in token_columns:
            db.session.execute(
                text(
                    "ALTER TABLE api_tokens ADD COLUMN extension_device_id INTEGER "
                    "REFERENCES extension_devices(id) ON DELETE SET NULL"
                )
            )
            result["token_columns_added"] = 1
        # Also self-heal a partially applied upgrade where the nullable column
        # exists but its lookup index was never created.
        db.session.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_api_tokens_extension_device_id "
                "ON api_tokens (extension_device_id)"
            )
        )
        db.session.commit()

    if {"devices", "extension_devices"}.issubset(table_names):
        existing_identifiers = {
            identifier
            for (identifier,) in db.session.query(
                ExtensionDevice.device_identifier
            ).all()
        }
        legacy_devices = Device.query.filter_by(device_type="extension").all()
        for legacy in legacy_devices:
            identity = legacy.client_identifier
            if identity in existing_identifiers:
                continue
            db.session.add(
                ExtensionDevice(
                    user_id=legacy.user_id,
                    device_identifier=legacy.client_identifier,
                    name=legacy.name,
                    browser=legacy.browser,
                    platform=legacy.platform,
                    last_seen_at=legacy.last_seen_at,
                    last_sync_at=legacy.last_synced_at,
                    revoked_at=legacy.revoked_at,
                    created_at=legacy.created_at,
                    updated_at=legacy.updated_at,
                )
            )
            existing_identifiers.add(identity)
            result["devices_migrated"] += 1

    if "extension_devices" in table_names:
        # Early Sprint-3 builds scoped identifiers per user. Quarantine any
        # impossible cross-account duplicates before installing the global
        # unique index; bound credentials are revoked by ExtensionDevice.revoke.
        duplicate_identifiers = [
            identifier
            for (identifier,) in db.session.query(ExtensionDevice.device_identifier)
            .group_by(ExtensionDevice.device_identifier)
            .having(func.count(ExtensionDevice.id) > 1)
            .all()
        ]
        for identifier in duplicate_identifiers:
            duplicates = (
                ExtensionDevice.query.filter_by(device_identifier=identifier)
                .order_by(ExtensionDevice.id.asc())
                .all()
            )
            for duplicate in duplicates[1:]:
                duplicate.revoke()
                duplicate.device_identifier = (
                    f"quarantined-{duplicate.id}-{duplicate.user_id}"
                )
                result["duplicate_devices_quarantined"] += 1

    if {"users", "extension_configurations"}.issubset(table_names):
        configured_user_ids = {
            user_id
            for (user_id,) in db.session.query(ExtensionConfiguration.user_id).all()
        }
        for user_id, in db.session.query(User.id).all():
            if user_id in configured_user_ids:
                continue
            active_mode_id = db.session.execute(
                select(ViewingMode.id).where(
                    ViewingMode.user_id == user_id,
                    ViewingMode.is_active.is_(True),
                )
            ).scalar_one_or_none()
            db.session.add(
                ExtensionConfiguration(
                    user_id=user_id,
                    config_version=1,
                    active_mode_id=active_mode_id,
                    sync_status="never_synced",
                )
            )
            result["configs_created"] += 1

    if (
        result["devices_migrated"]
        or result["configs_created"]
        or result["duplicate_devices_quarantined"]
    ):
        db.session.commit()

    if db.engine.dialect.name == "sqlite" and "extension_devices" in table_names:
        db.session.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_extension_device_identifier "
                "ON extension_devices (device_identifier)"
            )
        )
        db.session.commit()
    return result


def migrate_legacy_viewing_modes() -> int:
    """Copy legacy ``viewing_modes`` rows into the normalized mode schema.

    The operation is deliberately idempotent: a row is skipped when the same
    user already owns a normalized mode with that name.  The legacy table is
    retained as a read-only fallback instead of being destructively dropped.
    """

    table_names = set(inspect(db.engine).get_table_names())
    if not {"viewing_modes", "modes"}.issubset(table_names):
        return 0

    rows = list(
        db.session.execute(text("SELECT * FROM viewing_modes ORDER BY id")).mappings()
    )
    migrated = 0
    mode_id_map: dict[tuple[int, int], int] = {}
    active_users = {
        user_id
        for (user_id,) in db.session.query(ViewingMode.user_id)
        .filter(ViewingMode.is_active.is_(True))
        .all()
    }
    occupied_ids = {mode_id for (mode_id,) in db.session.query(ViewingMode.id).all()}

    for row in rows:
        user_id = int(row["user_id"])
        name = str(row["name"]).strip()
        existing = ViewingMode.query.filter_by(user_id=user_id, name=name).first()
        legacy_id = int(row["id"])
        if existing is not None:
            mode_id_map[(user_id, legacy_id)] = existing.id
            continue

        requested_active = _as_bool(row.get("is_active"))
        is_active = requested_active and user_id not in active_users
        pin_hash = row.get("protection_pin_hash")
        mode = ViewingMode(
            user_id=user_id,
            name=name,
            icon=row.get("icon") or "🎯",
            color=_color(row.get("color"), name),
            description=row.get("description") or "",
            strictness=_strictness(row.get("strictness")),
            is_protected=_as_bool(row.get("is_protected")) and bool(pin_hash),
            protection_pin_hash=pin_hash,
            is_active=is_active,
            last_used_at=_datetime(row.get("last_used_at")) or (utcnow() if is_active else None),
            created_at=_datetime(row.get("created_at")) or utcnow(),
            updated_at=_datetime(row.get("updated_at")) or utcnow(),
        )
        if legacy_id not in occupied_ids:
            mode.id = legacy_id
            occupied_ids.add(legacy_id)

        mode.preferred_categories = _string_list(row.get("preferred_categories"))
        mode.blocked_categories = _string_list(row.get("blocked_categories"))
        mode.preferred_keywords = _string_list(row.get("preferred_keywords"))
        mode.blocked_keywords = _string_list(row.get("blocked_keywords"))
        schedule = _json_value(row.get("schedule"), {})
        try:
            mode.schedule = schedule if isinstance(schedule, (dict, list)) else {}
        except (KeyError, TypeError, ValueError):
            mode.schedule = {}

        db.session.add(mode)
        db.session.flush()
        mode_id_map[(user_id, legacy_id)] = mode.id
        if is_active:
            active_users.add(user_id)
        migrated += 1

    db.session.commit()
    _repair_daily_usage_mode_reference(mode_id_map)
    return migrated


def _repair_daily_usage_mode_reference(
    mode_id_map: dict[tuple[int, int], int],
) -> bool:
    """Point upgraded SQLite analytics rows at ``modes`` instead of the legacy table."""

    if db.engine.dialect.name != "sqlite":
        return False
    inspector = inspect(db.engine)
    if "daily_usage_stats" not in inspector.get_table_names():
        return False

    foreign_keys = inspector.get_foreign_keys("daily_usage_stats")
    needs_rebuild = any(
        foreign_key.get("referred_table") == "viewing_modes"
        for foreign_key in foreign_keys
    )
    remaps = [
        (user_id, legacy_id, normalized_id)
        for (user_id, legacy_id), normalized_id in mode_id_map.items()
        if legacy_id != normalized_id
    ]
    if not needs_rebuild:
        for user_id, legacy_id, normalized_id in remaps:
            db.session.execute(
                text(
                    "UPDATE daily_usage_stats SET mode_id = :normalized_id "
                    "WHERE user_id = :user_id AND mode_id = :legacy_id"
                ),
                {
                    "user_id": user_id,
                    "legacy_id": legacy_id,
                    "normalized_id": normalized_id,
                },
            )
        if remaps:
            db.session.commit()
        return False

    # SQLite cannot alter a foreign-key target in place. Rebuild this small
    # aggregate table with checks temporarily disabled, then validate it.
    db.session.remove()
    connection = db.engine.raw_connection()
    cursor = connection.cursor()
    try:
        connection.commit()
        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            "ALTER TABLE daily_usage_stats RENAME TO daily_usage_stats_legacy_fk"
        )
        for index_name in (
            "ix_daily_usage_stats_user_id",
            "ix_daily_usage_stats_mode_id",
            "ix_daily_usage_stats_date",
        ):
            cursor.execute(f'DROP INDEX IF EXISTS "{index_name}"')
        cursor.execute(
            """
            CREATE TABLE daily_usage_stats (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                mode_id INTEGER,
                date DATE NOT NULL,
                relevant_count INTEGER NOT NULL,
                filtered_count INTEGER NOT NULL,
                estimated_seconds_saved INTEGER NOT NULL,
                CONSTRAINT uq_daily_stat_user_mode_date
                    UNIQUE (user_id, mode_id, date),
                FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY(mode_id) REFERENCES modes (id) ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO daily_usage_stats (
                id, user_id, mode_id, date, relevant_count,
                filtered_count, estimated_seconds_saved
            )
            SELECT id, user_id, mode_id, date, relevant_count,
                   filtered_count, estimated_seconds_saved
            FROM daily_usage_stats_legacy_fk
            """
        )
        for user_id, legacy_id, normalized_id in remaps:
            cursor.execute(
                """
                UPDATE daily_usage_stats
                SET mode_id = ?
                WHERE user_id = ? AND mode_id = ?
                """,
                (normalized_id, user_id, legacy_id),
            )
        cursor.execute(
            """
            UPDATE daily_usage_stats
            SET mode_id = NULL
            WHERE mode_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM modes
                  WHERE modes.id = daily_usage_stats.mode_id
                    AND modes.user_id = daily_usage_stats.user_id
              )
            """
        )
        cursor.execute("DROP TABLE daily_usage_stats_legacy_fk")
        cursor.execute(
            "CREATE INDEX ix_daily_usage_stats_user_id ON daily_usage_stats (user_id)"
        )
        cursor.execute(
            "CREATE INDEX ix_daily_usage_stats_mode_id ON daily_usage_stats (mode_id)"
        )
        cursor.execute(
            "CREATE INDEX ix_daily_usage_stats_date ON daily_usage_stats (date)"
        )
        connection.commit()
        cursor.execute("PRAGMA foreign_keys = ON")
        violations = cursor.execute(
            "PRAGMA foreign_key_check(daily_usage_stats)"
        ).fetchall()
        if violations:
            raise RuntimeError("SQLite foreign-key validation failed after mode migration.")
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass
        cursor.close()
        connection.close()
    return True


def _json_value(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return default


def _string_list(value) -> list[str]:
    parsed = _json_value(value, [])
    if not isinstance(parsed, list):
        return []
    values = []
    seen = set()
    for raw_value in parsed:
        item = str(raw_value).strip()[:100]
        identity = item.casefold()
        if item and identity not in seen:
            values.append(item)
            seen.add(identity)
    return values


def _datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _strictness(value) -> int:
    try:
        return min(5, max(1, int(value)))
    except (TypeError, ValueError):
        return 3


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _color(value, mode_name: str) -> str:
    fallback = LEGACY_MODE_COLORS.get(mode_name.casefold(), "#6366F1")
    candidate = str(value or fallback).strip().upper()
    return candidate if re.fullmatch(r"#[0-9A-F]{6}", candidate) else fallback
