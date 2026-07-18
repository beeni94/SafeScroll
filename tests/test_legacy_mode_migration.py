import json

from sqlalchemy import text

from safescroll.extensions import db
from safescroll.migrations import migrate_legacy_viewing_modes
from safescroll.models import (
    DailyUsageStat,
    ModeCategory,
    ModeKeyword,
    ModeSchedule,
    User,
    ViewingMode,
)


def test_legacy_viewing_modes_are_migrated_once(app):
    with app.app_context():
        user = User(full_name="Legacy User", email="legacy@example.com")
        user.set_password("CorrectHorse1!")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        db.session.execute(
            text(
                """
                CREATE TABLE viewing_modes (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    name VARCHAR(80) NOT NULL,
                    icon VARCHAR(16) NOT NULL,
                    description VARCHAR(500) NOT NULL,
                    preferred_categories JSON NOT NULL,
                    blocked_categories JSON NOT NULL,
                    preferred_keywords JSON NOT NULL,
                    blocked_keywords JSON NOT NULL,
                    strictness INTEGER NOT NULL,
                    schedule JSON NOT NULL,
                    is_protected BOOLEAN NOT NULL,
                    protection_pin_hash VARCHAR(255),
                    is_active BOOLEAN NOT NULL,
                    last_used_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        db.session.commit()

        # Reproduce the pre-normalization analytics foreign key. SQLite cannot
        # alter this constraint through create_all(), so the compatibility
        # migration must rebuild it without losing aggregate rows.
        db.session.remove()
        connection = db.engine.raw_connection()
        cursor = connection.cursor()
        try:
            connection.commit()
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute("DROP TABLE daily_usage_stats")
            cursor.execute(
                """
                CREATE TABLE daily_usage_stats (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    mode_id INTEGER REFERENCES viewing_modes(id) ON DELETE SET NULL,
                    date DATE NOT NULL,
                    relevant_count INTEGER NOT NULL,
                    filtered_count INTEGER NOT NULL,
                    estimated_seconds_saved INTEGER NOT NULL,
                    UNIQUE (user_id, mode_id, date)
                )
                """
            )
            connection.commit()
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()
            connection.close()

        db.session.execute(
            text(
                """
                INSERT INTO viewing_modes (
                    id, user_id, name, icon, description,
                    preferred_categories, blocked_categories,
                    preferred_keywords, blocked_keywords, strictness, schedule,
                    is_protected, protection_pin_hash, is_active,
                    last_used_at, created_at, updated_at
                ) VALUES (
                    41, :user_id, 'Study', '📚', 'Legacy focus mode',
                    :preferred_categories, :blocked_categories,
                    :preferred_keywords, :blocked_keywords, 4, :schedule,
                    0, NULL, 1, NULL,
                    '2026-01-01 09:00:00', '2026-01-01 09:00:00'
                )
                """
            ),
            {
                "user_id": user_id,
                "preferred_categories": json.dumps(["Education", "Python"]),
                "blocked_categories": json.dumps(["Gaming"]),
                "preferred_keywords": json.dumps(["tutorial"]),
                "blocked_keywords": json.dumps(["prank"]),
                "schedule": json.dumps(
                    {"days": ["mon", "wed"], "start": "08:00", "end": "14:00"}
                ),
            },
        )
        db.session.execute(
            text(
                """
                INSERT INTO daily_usage_stats (
                    id, user_id, mode_id, date, relevant_count,
                    filtered_count, estimated_seconds_saved
                ) VALUES (1, :user_id, 41, '2026-01-02', 12, 3, 45)
                """
            ),
            {"user_id": user_id},
        )
        db.session.commit()

        assert migrate_legacy_viewing_modes() == 1
        assert migrate_legacy_viewing_modes() == 0

        mode = ViewingMode.query.filter_by(user_id=user_id, name="Study").one()
        assert mode.id == 41
        assert mode.color == "#14B8A6"
        assert mode.is_active
        assert mode.preferred_categories == ["Education", "Python"]
        assert mode.blocked_categories == ["Gaming"]
        assert mode.preferred_keywords == ["tutorial"]
        assert mode.blocked_keywords == ["prank"]
        assert mode.schedule == {
            "days": ["mon", "wed"],
            "start": "08:00",
            "end": "14:00",
        }
        assert ModeCategory.query.filter_by(mode_id=mode.id).count() == 3
        assert ModeKeyword.query.filter_by(mode_id=mode.id).count() == 2
        assert ModeSchedule.query.filter_by(mode_id=mode.id).count() == 2
        stat = DailyUsageStat.query.one()
        assert stat.mode_id == mode.id
        foreign_key_targets = {
            row[2]
            for row in db.session.execute(
                text("PRAGMA foreign_key_list(daily_usage_stats)")
            )
        }
        assert "modes" in foreign_key_targets
        assert "viewing_modes" not in foreign_key_targets

        new_mode = ViewingMode(user_id=user_id, name="New normalized mode")
        db.session.add(new_mode)
        db.session.flush()
        db.session.add(
            DailyUsageStat(
                user_id=user_id,
                mode_id=new_mode.id,
                relevant_count=1,
                filtered_count=1,
            )
        )
        db.session.commit()
