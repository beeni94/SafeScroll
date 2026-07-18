import pytest

from safescroll.extensions import db
from safescroll.models import (
    ModeCategory,
    ModeKeyword,
    ModeSchedule,
    User,
    ViewingMode,
)


def _user() -> User:
    user = User(full_name="Mode Owner", email="modes@example.com")
    user.set_password("CorrectHorse1!")
    db.session.add(user)
    db.session.flush()
    return user


def test_mode_configuration_is_normalized_and_round_trips(app):
    with app.app_context():
        user = _user()
        mode = ViewingMode(
            user=user,
            name="Deep Work",
            color="#14b8a6",
            preferred_categories=["Python", " AI ", "python"],
            blocked_categories=["Gaming"],
            preferred_keywords=["tutorial", "course"],
            blocked_keywords=["prank"],
            schedule={
                "days": ["wed", "mon"],
                "start": "08:00",
                "end": "14:00",
            },
        )
        db.session.add(mode)
        db.session.commit()
        mode_id = mode.id

        db.session.expire_all()
        saved = db.session.get(ViewingMode, mode_id)

        assert ViewingMode.__tablename__ == "modes"
        assert ModeCategory.__tablename__ == "categories"
        assert ModeKeyword.__tablename__ == "keywords"
        assert ModeSchedule.__tablename__ == "mode_schedules"
        assert saved.color == "#14B8A6"
        assert saved.preferred_categories == ["Python", "AI"]
        assert saved.blocked_categories == ["Gaming"]
        assert saved.preferred_keywords == ["tutorial", "course"]
        assert saved.blocked_keywords == ["prank"]
        assert saved.schedule == {
            "days": ["mon", "wed"],
            "start": "08:00",
            "end": "14:00",
        }
        assert ModeCategory.query.filter_by(mode_id=mode_id).count() == 3
        assert ModeKeyword.query.filter_by(mode_id=mode_id).count() == 3
        assert ModeSchedule.query.filter_by(mode_id=mode_id).count() == 2

        exported = saved.export_dict()
        assert exported["color"] == "#14B8A6"
        assert exported["schedule"] == saved.schedule
        assert [row["day"] for row in exported["schedules"]] == ["mon", "wed"]
        assert "protection_pin_hash" not in exported


def test_replacing_configuration_or_deleting_mode_cascades_children(app):
    with app.app_context():
        user = _user()
        mode = ViewingMode(
            user=user,
            name="Replace Me",
            preferred_categories=["Python", "Science"],
            blocked_keywords=["spoiler"],
            schedule={"days": ["mon", "tue"], "start": "09:00", "end": "12:00"},
        )
        db.session.add(mode)
        db.session.commit()
        mode_id = mode.id

        mode.preferred_categories = ["Mathematics"]
        mode.blocked_keywords = []
        mode.schedule = {"days": ["fri"], "start": "10:00", "end": "11:00"}
        db.session.commit()

        assert ModeCategory.query.filter_by(mode_id=mode_id).count() == 1
        assert ModeKeyword.query.filter_by(mode_id=mode_id).count() == 0
        assert ModeSchedule.query.filter_by(mode_id=mode_id).count() == 1
        assert mode.preferred_categories == ["Mathematics"]
        assert mode.schedule["days"] == ["fri"]

        db.session.delete(mode)
        db.session.commit()
        assert ModeCategory.query.filter_by(mode_id=mode_id).count() == 0
        assert ModeKeyword.query.filter_by(mode_id=mode_id).count() == 0
        assert ModeSchedule.query.filter_by(mode_id=mode_id).count() == 0


def test_duplicate_copies_normalized_configuration(app, auth, client):
    auth.register()
    auth.login()
    with app.app_context():
        user = User.query.filter_by(email="alice@example.com").one()
        source = ViewingMode(
            user=user,
            name="Scheduled Focus",
            color="#3B82F6",
            preferred_categories=["Education"],
            blocked_categories=["Gaming"],
            preferred_keywords=["lecture"],
            blocked_keywords=["dance"],
            schedule={"days": ["mon", "thu"], "start": "08:00", "end": "14:00"},
        )
        db.session.add(source)
        db.session.commit()
        source_id = source.id

    response = client.post(f"/modes/{source_id}/duplicate")
    assert response.status_code == 302

    with app.app_context():
        clone = ViewingMode.query.filter_by(name="Scheduled Focus copy").one()
        assert clone.color == "#3B82F6"
        assert clone.preferred_categories == ["Education"]
        assert clone.blocked_categories == ["Gaming"]
        assert clone.preferred_keywords == ["lecture"]
        assert clone.blocked_keywords == ["dance"]
        assert clone.schedule == {
            "days": ["mon", "thu"],
            "start": "08:00",
            "end": "14:00",
        }
        assert clone.categories[0].id != db.session.get(ViewingMode, source_id).categories[0].id


@pytest.mark.parametrize("color", ["blue", "#12345", "#GGGGGG", "#12345678"])
def test_mode_rejects_invalid_colors(color):
    with pytest.raises(ValueError, match="hexadecimal"):
        ViewingMode(name="Invalid color", user_id=1, color=color)
