from datetime import timedelta

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from safescroll.api.models import APIToken, ApiToken
from safescroll.extensions import db
from safescroll.migrations import migrate_extension_schema
from safescroll.models import (
    Device,
    ExtensionConfiguration,
    ExtensionDevice,
    ExtensionEvent,
    ExtensionPairingToken,
    ModeSchedule,
    SyncLog,
    User,
    ViewingMode,
)
from safescroll.utils import hash_token, utcnow


def _create_user(email="extension-models@example.com"):
    user = User(full_name="Extension Models", email=email)
    user.set_password("CorrectHorse1!")
    db.session.add(user)
    db.session.commit()
    return user


def test_extension_schema_and_new_user_configuration(app):
    with app.app_context():
        user = _create_user()
        tables = set(inspect(db.engine).get_table_names())
        configuration = ExtensionConfiguration.query.filter_by(user_id=user.id).one()

        assert {
            "extension_devices",
            "extension_configurations",
            "extension_events",
            "extension_pairing_tokens",
            "sync_logs",
            "api_tokens",
        }.issubset(tables)
        assert configuration.config_version == 1
        assert configuration.active_mode_id is None
        assert configuration.sync_status == "never_synced"
        assert ApiToken is APIToken


def test_pairing_tokens_are_hashed_expiring_and_single_use(app):
    with app.app_context():
        user = _create_user("pairing-model@example.com")
        pairing, raw_token = ExtensionPairingToken.issue(
            user, lifetime=timedelta(minutes=5), created_ip="127.0.0.1"
        )
        device = ExtensionDevice(
            user_id=user.id,
            device_identifier="pairing-model-device",
            name="Pairing browser",
        )
        db.session.add_all([pairing, device])
        db.session.commit()

        assert pairing.token_hash == hash_token(raw_token)
        assert raw_token not in pairing.token_hash
        assert pairing.is_valid
        pairing.consume(device)
        db.session.add(
            ExtensionEvent(
                user_id=user.id,
                extension_device_id=device.id,
                event_type="pairing",
                status="connected",
            )
        )
        db.session.commit()

        assert not pairing.is_valid
        assert pairing.used_at is not None
        assert pairing.claimed_device_id == device.id
        assert user.extension_events.one().status == "connected"


def test_extension_device_binding_and_disconnect_revokes_tokens(app):
    with app.app_context():
        user = _create_user()
        device = ExtensionDevice(
            user_id=user.id,
            device_identifier="install-01",
            name="Work laptop",
            browser="Chrome 140",
            platform="Windows",
        )
        db.session.add(device)
        db.session.flush()
        token, raw_token = APIToken.issue(
            user,
            extension_device=device,
            lifetime=timedelta(days=30),
        )
        db.session.add(token)
        db.session.commit()

        assert APIToken.from_raw_token(raw_token).extension_device_id == device.id
        assert device.is_connected

        device.revoke()
        db.session.commit()

        assert not device.is_connected
        assert not token.is_valid


def test_bound_token_is_invalid_when_device_was_revoked_directly(app):
    with app.app_context():
        user = _create_user("direct-revoke@example.com")
        device = ExtensionDevice(
            user_id=user.id,
            device_identifier="direct-revoke-install",
            name="Direct revoke device",
        )
        db.session.add(device)
        db.session.flush()
        token, _raw = APIToken.issue(
            user, extension_device=device, lifetime=timedelta(days=1)
        )
        db.session.add(token)
        db.session.commit()

        device.revoked_at = utcnow()
        db.session.commit()

        assert not token.is_valid


def test_token_cannot_be_bound_to_another_users_device(app):
    with app.app_context():
        owner = _create_user("owner-device@example.com")
        stranger = _create_user("stranger-device@example.com")
        device = ExtensionDevice(
            user_id=stranger.id,
            device_identifier="stranger-install",
            name="Stranger device",
        )
        db.session.add(device)
        db.session.commit()

        with pytest.raises(ValueError, match="user's device"):
            APIToken.issue(
                owner,
                extension_device=device,
                lifetime=timedelta(days=1),
            )


def test_extension_identifier_cannot_be_claimed_by_two_users(app):
    with app.app_context():
        owner = _create_user("identifier-owner@example.com")
        stranger = _create_user("identifier-stranger@example.com")
        db.session.add(
            ExtensionDevice(
                user_id=owner.id,
                device_identifier="globally-unique-install",
                name="Owner browser",
            )
        )
        db.session.commit()

        db.session.add(
            ExtensionDevice(
                user_id=stranger.id,
                device_identifier="globally-unique-install",
                name="Stranger browser",
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_mode_configuration_changes_bump_version_once_per_transaction(app):
    with app.app_context():
        user = _create_user()
        configuration = ExtensionConfiguration.query.filter_by(user_id=user.id).one()
        initial_version = configuration.config_version

        mode = ViewingMode(user_id=user.id, name="Study", is_active=True)
        mode.preferred_categories = ["Education"]
        mode.preferred_keywords = ["tutorial"]
        mode.schedules = [
            ModeSchedule(day="mon", start_time="08:00", end_time="14:00")
        ]
        db.session.add(mode)
        db.session.commit()

        configuration = ExtensionConfiguration.query.filter_by(user_id=user.id).one()
        assert configuration.config_version == initial_version + 1
        assert configuration.active_mode_id == mode.id
        assert configuration.sync_status == "pending"

        before_nested_update = configuration.config_version
        mode.name = "Deep Study"
        mode.preferred_categories = ["Education", "Science"]
        mode.preferred_keywords = ["tutorial", "course"]
        mode.schedule = {
            "days": ["mon", "wed"],
            "start": "09:00",
            "end": "15:00",
        }
        db.session.commit()

        configuration = ExtensionConfiguration.query.filter_by(user_id=user.id).one()
        assert configuration.config_version == before_nested_update + 1

        second = ViewingMode(user_id=user.id, name="Fun")
        db.session.add(second)
        db.session.commit()
        before_activation = ExtensionConfiguration.query.filter_by(
            user_id=user.id
        ).one().config_version

        mode.is_active = False
        second.is_active = True
        db.session.commit()

        configuration = ExtensionConfiguration.query.filter_by(user_id=user.id).one()
        assert configuration.config_version == before_activation + 1
        assert configuration.active_mode_id == second.id

        before_delete = configuration.config_version
        db.session.delete(mode)
        db.session.commit()
        configuration = ExtensionConfiguration.query.filter_by(user_id=user.id).one()
        assert configuration.config_version == before_delete + 1
        assert configuration.active_mode_id == second.id


def test_version_bump_rolls_back_with_mode_mutation(app):
    with app.app_context():
        user = _create_user()
        mode = ViewingMode(user_id=user.id, name="Study", is_active=True)
        db.session.add(mode)
        db.session.commit()
        before = ExtensionConfiguration.query.filter_by(
            user_id=user.id
        ).one().config_version

        mode.strictness = 5
        db.session.flush()
        db.session.rollback()

        configuration = ExtensionConfiguration.query.filter_by(user_id=user.id).one()
        assert configuration.config_version == before
        assert db.session.get(ViewingMode, mode.id).strictness == 3


def test_active_delete_then_replacement_refreshes_snapshot_without_second_bump(app):
    with app.app_context():
        user = _create_user("replacement-snapshot@example.com")
        active = ViewingMode(user_id=user.id, name="Active", is_active=True)
        replacement = ViewingMode(user_id=user.id, name="Replacement")
        db.session.add_all([active, replacement])
        db.session.commit()
        before = ExtensionConfiguration.query.filter_by(
            user_id=user.id
        ).one().config_version

        db.session.delete(active)
        db.session.flush()
        replacement.is_active = True
        db.session.commit()

        configuration = ExtensionConfiguration.query.filter_by(user_id=user.id).one()
        assert configuration.config_version == before + 1
        assert configuration.active_mode_id == replacement.id


def test_sync_rows_are_user_owned_and_legacy_extension_devices_migrate_once(app):
    with app.app_context():
        user = _create_user()
        legacy = Device(
            user_id=user.id,
            name="Legacy extension",
            device_type="extension",
            platform="Linux",
            browser="Chrome",
            client_identifier="legacy-extension-01",
        )
        db.session.add(legacy)
        db.session.commit()

        first = migrate_extension_schema()
        second = migrate_extension_schema()
        extension_device = ExtensionDevice.query.filter_by(
            user_id=user.id,
            device_identifier="legacy-extension-01",
        ).one()
        configuration = ExtensionConfiguration.query.filter_by(user_id=user.id).one()
        log = SyncLog(
            user_id=user.id,
            extension_device_id=extension_device.id,
            config_version=configuration.config_version,
            status="success",
        )
        db.session.add(log)
        db.session.commit()

        assert first["devices_migrated"] == 1
        assert second["devices_migrated"] == 0
        assert log.user_id == user.id
        assert log.extension_device.user_id == user.id


def test_legacy_api_token_table_is_upgraded_without_losing_tokens(app):
    with app.app_context():
        user = _create_user("legacy-token@example.com")
        user_id = user.id
        raw_token = "ss_legacy_upgrade_token_value"
        now = utcnow()
        expires_at = now + timedelta(days=30)

        db.session.remove()
        connection = db.engine.raw_connection()
        cursor = connection.cursor()
        try:
            connection.commit()
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute("DROP TABLE api_tokens")
            cursor.execute(
                """
                CREATE TABLE api_tokens (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    label VARCHAR(120) NOT NULL,
                    token_hash VARCHAR(64) NOT NULL UNIQUE,
                    prefix VARCHAR(16) NOT NULL UNIQUE,
                    created_at DATETIME NOT NULL,
                    expires_at DATETIME NOT NULL,
                    revoked_at DATETIME,
                    last_used_at DATETIME
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO api_tokens (
                    id, user_id, label, token_hash, prefix,
                    created_at, expires_at, revoked_at, last_used_at
                ) VALUES (7, ?, 'Legacy extension', ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    user_id,
                    hash_token(raw_token),
                    raw_token[:16],
                    now.isoformat(" "),
                    expires_at.isoformat(" "),
                ),
            )
            connection.commit()
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()
            connection.close()

        result = migrate_extension_schema()
        columns = {
            column["name"] for column in inspect(db.engine).get_columns("api_tokens")
        }
        indexes = {
            index["name"] for index in inspect(db.engine).get_indexes("api_tokens")
        }
        token = APIToken.from_raw_token(raw_token)

        assert result["token_columns_added"] == 1
        assert "extension_device_id" in columns
        assert "ix_api_tokens_extension_device_id" in indexes
        assert token.id == 7
        assert token.user_id == user_id
        assert token.extension_device_id is None

        # A partially applied upgrade (column present, index absent) heals on
        # the next idempotent migration run without touching token data.
        db.session.execute(text("DROP INDEX ix_api_tokens_extension_device_id"))
        db.session.commit()
        migrate_extension_schema()
        healed_indexes = {
            index["name"] for index in inspect(db.engine).get_indexes("api_tokens")
        }
        assert "ix_api_tokens_extension_device_id" in healed_indexes
        assert APIToken.from_raw_token(raw_token).id == 7
