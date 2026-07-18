import secrets
import re
from datetime import date, time, timedelta

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy.orm import validates
from werkzeug.security import check_password_hash, generate_password_hash

from safescroll.extensions import db
from safescroll.utils import hash_token, relative_time, utcnow


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    timezone = db.Column(db.String(64), nullable=False, default="UTC")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    last_login_at = db.Column(db.DateTime)
    password_changed_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    modes = db.relationship(
        "ViewingMode", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    devices = db.relationship(
        "Device", back_populates="user", cascade="all, delete-orphan", lazy="dynamic"
    )
    sessions = db.relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan", lazy="dynamic"
    )
    daily_stats = db.relationship(
        "DailyUsageStat", back_populates="user", cascade="all, delete-orphan", lazy="dynamic"
    )
    reset_tokens = db.relationship(
        "PasswordResetToken",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    extension_devices = db.relationship(
        "ExtensionDevice",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    extension_configuration = db.relationship(
        "ExtensionConfiguration",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    api_tokens = db.relationship(
        "APIToken",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    sync_logs = db.relationship(
        "SyncLog",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    extension_pairing_tokens = db.relationship(
        "ExtensionPairingToken",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    extension_events = db.relationship(
        "ExtensionEvent",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="scrypt")
        self.password_changed_at = utcnow()

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def active_mode(self):
        return next((mode for mode in self.modes if mode.is_active), None)

    def export_dict(self) -> dict:
        return {
            "profile": {
                "full_name": self.full_name,
                "email": self.email,
                "timezone": self.timezone,
                "created_at": self.created_at.isoformat() if self.created_at else None,
            },
            "modes": [mode.export_dict() for mode in self.modes],
            "devices": [device.export_dict() for device in self.devices.all()],
            "activity": [stat.export_dict() for stat in self.daily_stats.all()],
        }

    @property
    def created_at_label(self) -> str:
        return self.created_at.strftime("%b %Y") if self.created_at else "Recently"


class ViewingMode(TimestampMixin, db.Model):
    __tablename__ = "modes"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_mode_user_name"),
        CheckConstraint("strictness >= 1 AND strictness <= 5", name="ck_mode_strictness"),
        Index(
            "uq_mode_one_active_per_user",
            "user_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = db.Column(db.String(80), nullable=False)
    color = db.Column(db.String(7), nullable=False, default="#6366F1")
    icon = db.Column(db.String(16), nullable=False, default="🎯")
    description = db.Column(db.String(500), nullable=False, default="")
    strictness = db.Column(db.Integer, nullable=False, default=3)
    is_protected = db.Column(db.Boolean, nullable=False, default=False)
    protection_pin_hash = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=False, index=True)
    last_used_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="modes")
    daily_stats = db.relationship("DailyUsageStat", back_populates="mode")
    categories = db.relationship(
        "ModeCategory",
        back_populates="mode",
        cascade="all, delete-orphan",
        order_by="ModeCategory.position, ModeCategory.id",
        lazy="selectin",
    )
    keywords = db.relationship(
        "ModeKeyword",
        back_populates="mode",
        cascade="all, delete-orphan",
        order_by="ModeKeyword.position, ModeKeyword.id",
        lazy="selectin",
    )
    schedules = db.relationship(
        "ModeSchedule",
        back_populates="mode",
        cascade="all, delete-orphan",
        order_by="ModeSchedule.day_order, ModeSchedule.id",
        lazy="selectin",
    )
    pin_attempts = db.relationship(
        "ModePINAttempt",
        back_populates="mode",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    @validates("color")
    def validate_color(self, _key: str, value: str | None) -> str | None:
        """Store optional colors in a predictable API-safe ``#RRGGBB`` form."""
        if value is None or not str(value).strip():
            return "#6366F1"
        normalized = str(value).strip().upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", normalized):
            raise ValueError("Mode color must be a six-digit hexadecimal color.")
        return normalized

    @validates("icon")
    def validate_icon(self, _key: str, value: str | None) -> str:
        normalized = str(value or "").strip() or "🎯"
        if len(normalized) > 16:
            raise ValueError("Mode icons must contain at most 16 characters.")
        return normalized

    @staticmethod
    def _normalized_values(values) -> list[str]:
        """Trim and case-insensitively deduplicate category/keyword values."""
        if values is None:
            return []
        if isinstance(values, str):
            values = [values]
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            value = str(raw_value).strip()
            identity = value.casefold()
            if not value or identity in seen:
                continue
            seen.add(identity)
            normalized.append(value)
        return normalized

    def _category_values(self, category_type: str) -> list[str]:
        return [entry.name for entry in self.categories if entry.category_type == category_type]

    def _set_category_values(self, category_type: str, values) -> None:
        retained = [
            entry for entry in self.categories if entry.category_type != category_type
        ]
        existing = {
            entry.name.casefold(): entry
            for entry in self.categories
            if entry.category_type == category_type
        }
        selected = []
        for position, value in enumerate(self._normalized_values(values)):
            entry = existing.pop(value.casefold(), None)
            if entry is None:
                entry = ModeCategory(category_type=category_type, name=value)
            else:
                entry.name = value
            entry.position = position
            selected.append(entry)
        self.categories = retained + selected

    @property
    def preferred_categories(self) -> list[str]:
        return self._category_values("preferred")

    @preferred_categories.setter
    def preferred_categories(self, values) -> None:
        self._set_category_values("preferred", values)

    @property
    def blocked_categories(self) -> list[str]:
        return self._category_values("blocked")

    @blocked_categories.setter
    def blocked_categories(self, values) -> None:
        self._set_category_values("blocked", values)

    def _keyword_values(self, keyword_type: str) -> list[str]:
        return [entry.value for entry in self.keywords if entry.keyword_type == keyword_type]

    def _set_keyword_values(self, keyword_type: str, values) -> None:
        retained = [entry for entry in self.keywords if entry.keyword_type != keyword_type]
        existing = {
            entry.value.casefold(): entry
            for entry in self.keywords
            if entry.keyword_type == keyword_type
        }
        selected = []
        for position, value in enumerate(self._normalized_values(values)):
            entry = existing.pop(value.casefold(), None)
            if entry is None:
                entry = ModeKeyword(keyword_type=keyword_type, value=value)
            else:
                entry.value = value
            entry.position = position
            selected.append(entry)
        self.keywords = retained + selected

    @property
    def preferred_keywords(self) -> list[str]:
        return self._keyword_values("preferred")

    @preferred_keywords.setter
    def preferred_keywords(self, values) -> None:
        self._set_keyword_values("preferred", values)

    @property
    def blocked_keywords(self) -> list[str]:
        return self._keyword_values("blocked")

    @blocked_keywords.setter
    def blocked_keywords(self, values) -> None:
        self._set_keyword_values("blocked", values)

    @property
    def schedule(self) -> dict:
        if not self.schedules:
            return {}
        first = self.schedules[0]
        return {
            "days": [entry.day for entry in self.schedules],
            "start": first.start_time.strftime("%H:%M"),
            "end": first.end_time.strftime("%H:%M"),
        }

    @schedule.setter
    def schedule(self, value) -> None:
        if not value:
            self.schedules = []
            return

        if isinstance(value, dict):
            days = value.get("days", [value["day"]] if value.get("day") else [])
            if isinstance(days, str):
                days = [days]
            rows = [
                {"day": day, "start": value.get("start"), "end": value.get("end")}
                for day in days
            ]
        elif isinstance(value, (list, tuple)):
            rows = list(value)
        else:
            raise ValueError("Mode schedule must be a schedule object or list of rows.")

        existing = {entry.day: entry for entry in self.schedules}
        schedules = []
        seen_days: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Each mode schedule row must be an object.")
            day = str(row.get("day", "")).strip().lower()
            if not day or day in seen_days:
                continue
            start_time = ModeSchedule.coerce_time(row.get("start"))
            end_time = ModeSchedule.coerce_time(row.get("end"))
            entry = existing.pop(day, None)
            if entry is None:
                entry = ModeSchedule(day=day)
            entry.start_time = start_time
            entry.end_time = end_time
            entry.day_order = ModeSchedule.DAY_ORDER.get(day, -1)
            schedules.append(entry)
            seen_days.add(day)
        self.schedules = schedules

    def set_protection_pin(self, pin: str | None) -> None:
        if pin:
            self.protection_pin_hash = generate_password_hash(pin, method="scrypt")

    def check_protection_pin(self, pin: str) -> bool:
        return bool(self.protection_pin_hash) and check_password_hash(
            self.protection_pin_hash, pin
        )

    def export_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "color": self.color,
            "description": self.description,
            "preferred_categories": self.preferred_categories,
            "blocked_categories": self.blocked_categories,
            "preferred_keywords": self.preferred_keywords,
            "blocked_keywords": self.blocked_keywords,
            "strictness": self.strictness,
            "schedule": self.schedule,
            "schedules": [entry.export_dict() for entry in self.schedules],
            "is_protected": self.is_protected,
            "is_active": self.is_active,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ModeCategory(db.Model):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint(
            "mode_id", "category_type", "name", name="uq_mode_category_type_name"
        ),
        CheckConstraint(
            "category_type IN ('preferred', 'blocked')",
            name="ck_mode_category_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    mode_id = db.Column(
        db.Integer,
        db.ForeignKey("modes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_type = db.Column(db.String(16), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)

    mode = db.relationship("ViewingMode", back_populates="categories")

    @validates("category_type")
    def validate_category_type(self, _key: str, value: str) -> str:
        if value not in {"preferred", "blocked"}:
            raise ValueError("Category type must be preferred or blocked.")
        return value

    @validates("name")
    def validate_name(self, _key: str, value: str) -> str:
        normalized = str(value).strip()
        if not normalized or len(normalized) > 100:
            raise ValueError("Category names must contain between 1 and 100 characters.")
        return normalized

    def export_dict(self) -> dict:
        return {"id": self.id, "type": self.category_type, "name": self.name}


class ModeKeyword(db.Model):
    __tablename__ = "keywords"
    __table_args__ = (
        UniqueConstraint(
            "mode_id", "keyword_type", "value", name="uq_mode_keyword_type_value"
        ),
        CheckConstraint(
            "keyword_type IN ('preferred', 'blocked')",
            name="ck_mode_keyword_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    mode_id = db.Column(
        db.Integer,
        db.ForeignKey("modes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    keyword_type = db.Column(db.String(16), nullable=False)
    value = db.Column(db.String(100), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)

    mode = db.relationship("ViewingMode", back_populates="keywords")

    @validates("keyword_type")
    def validate_keyword_type(self, _key: str, value: str) -> str:
        if value not in {"preferred", "blocked"}:
            raise ValueError("Keyword type must be preferred or blocked.")
        return value

    @validates("value")
    def validate_value(self, _key: str, value: str) -> str:
        normalized = str(value).strip()
        if not normalized or len(normalized) > 100:
            raise ValueError("Keywords must contain between 1 and 100 characters.")
        return normalized

    def export_dict(self) -> dict:
        return {"id": self.id, "type": self.keyword_type, "value": self.value}


class ModeSchedule(db.Model):
    __tablename__ = "mode_schedules"
    __table_args__ = (
        UniqueConstraint("mode_id", "day", name="uq_mode_schedule_day"),
        CheckConstraint(
            "day IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')",
            name="ck_mode_schedule_day",
        ),
    )

    DAY_ORDER = {
        day: position
        for position, day in enumerate(
            ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        )
    }

    id = db.Column(db.Integer, primary_key=True)
    mode_id = db.Column(
        db.Integer,
        db.ForeignKey("modes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day = db.Column(db.String(3), nullable=False)
    day_order = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

    mode = db.relationship("ViewingMode", back_populates="schedules")

    def __init__(self, **kwargs):
        if "day" in kwargs and "day_order" not in kwargs:
            day = str(kwargs["day"]).strip().lower()
            kwargs["day"] = day
            kwargs["day_order"] = self.DAY_ORDER.get(day, -1)
        super().__init__(**kwargs)

    @staticmethod
    def coerce_time(value) -> time:
        if isinstance(value, time):
            return value.replace(second=0, microsecond=0)
        if isinstance(value, str):
            try:
                return time.fromisoformat(value).replace(second=0, microsecond=0)
            except ValueError as error:
                raise ValueError("Schedule times must use HH:MM format.") from error
        raise ValueError("Schedule times are required.")

    @validates("day")
    def validate_day(self, _key: str, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in self.DAY_ORDER:
            raise ValueError("Schedule day must be mon, tue, wed, thu, fri, sat, or sun.")
        self.day_order = self.DAY_ORDER[normalized]
        return normalized

    @validates("start_time", "end_time")
    def validate_time(self, _key: str, value) -> time:
        return self.coerce_time(value)

    def export_dict(self) -> dict:
        return {
            "id": self.id,
            "day": self.day,
            "start": self.start_time.strftime("%H:%M"),
            "end": self.end_time.strftime("%H:%M"),
        }


class ModePINAttempt(db.Model):
    """Short-lived PIN failure state for one mode and authenticated principal."""

    __tablename__ = "mode_pin_attempts"
    __table_args__ = (
        UniqueConstraint("mode_id", "principal_key", name="uq_mode_pin_principal"),
    )

    id = db.Column(db.Integer, primary_key=True)
    mode_id = db.Column(
        db.Integer,
        db.ForeignKey("modes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    principal_key = db.Column(db.String(64), nullable=False)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    locked_until = db.Column(db.DateTime)

    mode = db.relationship("ViewingMode", back_populates="pin_attempts")


class Device(TimestampMixin, db.Model):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("user_id", "client_identifier", name="uq_device_user_client"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = db.Column(db.String(120), nullable=False)
    device_type = db.Column(db.String(32), nullable=False, default="browser")
    platform = db.Column(db.String(80), nullable=False, default="Unknown platform")
    browser = db.Column(db.String(80), nullable=False, default="Browser")
    client_identifier = db.Column(db.String(128), nullable=False)
    credential_hash = db.Column(db.String(64), unique=True)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_synced_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="devices")
    sessions = db.relationship("UserSession", back_populates="device")

    @property
    def is_connected(self) -> bool:
        return self.revoked_at is None

    @property
    def is_active(self) -> bool:
        return self.is_connected

    @property
    def last_seen_label(self) -> str:
        return relative_time(self.last_seen_at)

    @property
    def created_at_label(self) -> str:
        return self.created_at.strftime("%b %d, %Y") if self.created_at else "recently"

    def export_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "device_type": self.device_type,
            "platform": self.platform,
            "browser": self.browser,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "revoked": self.revoked_at is not None,
        }


class ExtensionDevice(TimestampMixin, db.Model):
    """A Chrome extension installation authorized for one SafeScroll user.

    Browser-login devices remain in :class:`Device`; keeping extension
    installations separate lets API credentials and sync history be revoked
    without invalidating the user's website sessions.
    """

    __tablename__ = "extension_devices"
    __table_args__ = (
        # An installation identifier cannot be claimed by two accounts, even
        # when first-sync requests arrive in different application workers.
        Index(
            "uq_extension_device_identifier",
            "device_identifier",
            unique=True,
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_identifier = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(120), nullable=False, default="Chrome extension")
    browser = db.Column(db.String(80), nullable=False, default="Chrome")
    platform = db.Column(db.String(80), nullable=False, default="Unknown platform")
    extension_version = db.Column(db.String(32))
    last_seen_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_sync_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime, index=True)

    user = db.relationship("User", back_populates="extension_devices")
    api_tokens = db.relationship(
        "APIToken", back_populates="extension_device", lazy="selectin"
    )
    sync_logs = db.relationship(
        "SyncLog", back_populates="extension_device", lazy="dynamic"
    )
    extension_events = db.relationship(
        "ExtensionEvent", back_populates="extension_device", lazy="dynamic"
    )

    @validates("device_identifier")
    def validate_device_identifier(self, _key: str, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized or len(normalized) > 128:
            raise ValueError(
                "Extension device identifiers must contain between 1 and 128 characters."
            )
        return normalized

    @property
    def is_connected(self) -> bool:
        return self.revoked_at is None

    def revoke(self) -> None:
        """Disconnect this installation and revoke every credential bound to it."""

        if self.revoked_at is None:
            self.revoked_at = utcnow()
        for token in self.api_tokens:
            token.revoke()

    def export_dict(self) -> dict:
        return {
            "id": self.id,
            "device_identifier": self.device_identifier,
            "name": self.name,
            "browser": self.browser,
            "platform": self.platform,
            "extension_version": self.extension_version,
            "last_seen_at": self.last_seen_at.isoformat()
            if self.last_seen_at
            else None,
            "last_sync_at": self.last_sync_at.isoformat()
            if self.last_sync_at
            else None,
            "revoked": self.revoked_at is not None,
            "is_connected": self.is_connected,
        }


class ExtensionConfiguration(TimestampMixin, db.Model):
    """Versioned extension configuration state for exactly one user."""

    __tablename__ = "extension_configurations"
    __table_args__ = (
        CheckConstraint(
            "config_version >= 1", name="ck_extension_configuration_version"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    config_version = db.Column(db.Integer, nullable=False, default=1)
    active_mode_id = db.Column(
        db.Integer,
        db.ForeignKey("modes.id", ondelete="SET NULL"),
        index=True,
    )
    last_sync_at = db.Column(db.DateTime)
    sync_status = db.Column(db.String(32), nullable=False, default="never_synced")

    user = db.relationship("User", back_populates="extension_configuration")
    active_mode = db.relationship("ViewingMode", foreign_keys=[active_mode_id])

    def export_dict(self) -> dict:
        return {
            "config_version": self.config_version,
            "active_mode_id": self.active_mode_id,
            "last_sync_at": self.last_sync_at.isoformat()
            if self.last_sync_at
            else None,
            "sync_status": self.sync_status,
        }


class SyncLog(db.Model):
    """Immutable audit row for an extension configuration sync attempt."""

    __tablename__ = "sync_logs"
    __table_args__ = (
        CheckConstraint("config_version >= 1", name="ck_sync_log_version"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    extension_device_id = db.Column(
        db.Integer,
        db.ForeignKey("extension_devices.id", ondelete="SET NULL"),
        index=True,
    )
    config_version = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(32), nullable=False)
    message = db.Column(db.String(500))
    synced_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User", back_populates="sync_logs")
    extension_device = db.relationship("ExtensionDevice", back_populates="sync_logs")

    def export_dict(self) -> dict:
        return {
            "id": self.id,
            "extension_device_id": self.extension_device_id,
            "config_version": self.config_version,
            "status": self.status,
            "message": self.message,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
        }


class ExtensionPairingToken(db.Model):
    """Short-lived, single-use credential for website-to-extension pairing."""

    __tablename__ = "extension_pairing_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    claimed_device_id = db.Column(
        db.Integer,
        db.ForeignKey("extension_devices.id", ondelete="SET NULL"),
        index=True,
    )
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    prefix = db.Column(db.String(16), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime, index=True)
    revoked_at = db.Column(db.DateTime, index=True)
    created_ip = db.Column(db.String(45))

    user = db.relationship("User", back_populates="extension_pairing_tokens")
    claimed_device = db.relationship("ExtensionDevice", foreign_keys=[claimed_device_id])

    @classmethod
    def issue(cls, user, *, lifetime: timedelta, created_ip: str | None = None):
        if lifetime.total_seconds() <= 0:
            raise ValueError("Pairing-token lifetime must be positive.")
        raw_token = f"sp_{secrets.token_urlsafe(32)}"
        record = cls(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            prefix=raw_token[:16],
            expires_at=utcnow() + lifetime,
            created_ip=(created_ip or "")[:45] or None,
        )
        return record, raw_token

    @classmethod
    def from_raw_token(cls, raw_token: str):
        if not isinstance(raw_token, str) or not raw_token.startswith("sp_"):
            return None
        return cls.query.filter_by(token_hash=hash_token(raw_token)).first()

    @property
    def is_valid(self) -> bool:
        return (
            self.used_at is None
            and self.revoked_at is None
            and self.expires_at > utcnow()
        )

    def consume(self, device) -> None:
        if not self.is_valid:
            raise ValueError("This pairing token is no longer valid.")
        if device.user_id != self.user_id:
            raise ValueError("A pairing token can only claim its user's device.")
        self.claimed_device_id = device.id
        self.used_at = utcnow()

    def revoke(self) -> None:
        if self.used_at is None and self.revoked_at is None:
            self.revoked_at = utcnow()

    def export_dict(self) -> dict:
        return {
            "id": self.id,
            "prefix": self.prefix,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "used_at": self.used_at.isoformat() if self.used_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "claimed_device_id": self.claimed_device_id,
        }


class ExtensionEvent(db.Model):
    """User-owned audit history for pairing, connection, and sync activity."""

    __tablename__ = "extension_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    extension_device_id = db.Column(
        db.Integer,
        db.ForeignKey("extension_devices.id", ondelete="SET NULL"),
        index=True,
    )
    event_type = db.Column(db.String(40), nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False)
    message = db.Column(db.String(500))
    extension_version = db.Column(db.String(32))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    user = db.relationship("User", back_populates="extension_events")
    extension_device = db.relationship(
        "ExtensionDevice", back_populates="extension_events"
    )

    def export_dict(self) -> dict:
        return {
            "id": self.id,
            "extension_device_id": self.extension_device_id,
            "event_type": self.event_type,
            "status": self.status,
            "message": self.message,
            "extension_version": self.extension_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserSession(db.Model):
    __tablename__ = "user_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id = db.Column(
        db.Integer, db.ForeignKey("devices.id", ondelete="SET NULL"), index=True
    )
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    device_label = db.Column(db.String(120), nullable=False)
    browser = db.Column(db.String(80), nullable=False, default="Browser")
    platform = db.Column(db.String(80), nullable=False, default="Unknown platform")
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(512))
    remembered = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="sessions")
    device = db.relationship("Device", back_populates="sessions")

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > utcnow()

    @property
    def device_name(self) -> str:
        return self.device_label

    @property
    def device_type(self) -> str:
        return "mobile" if self.platform in {"Android", "iOS"} else "browser"

    @property
    def last_seen_label(self) -> str:
        return relative_time(self.last_seen_at)


class DailyUsageStat(db.Model):
    __tablename__ = "daily_usage_stats"
    __table_args__ = (
        UniqueConstraint("user_id", "mode_id", "date", name="uq_daily_stat_user_mode_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode_id = db.Column(
        db.Integer, db.ForeignKey("modes.id", ondelete="SET NULL"), index=True
    )
    date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    relevant_count = db.Column(db.Integer, nullable=False, default=0)
    filtered_count = db.Column(db.Integer, nullable=False, default=0)
    estimated_seconds_saved = db.Column(db.Integer, nullable=False, default=0)

    user = db.relationship("User", back_populates="daily_stats")
    mode = db.relationship("ViewingMode", back_populates="daily_stats")

    def export_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "mode_id": self.mode_id,
            "relevant_count": self.relevant_count,
            "filtered_count": self.filtered_count,
            "estimated_seconds_saved": self.estimated_seconds_saved,
        }


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="reset_tokens")

    @classmethod
    def issue(cls, user: User, lifetime: timedelta) -> tuple["PasswordResetToken", str]:
        raw_token = secrets.token_urlsafe(32)
        record = cls(
            user=user,
            token_hash=hash_token(raw_token),
            expires_at=utcnow() + lifetime,
        )
        return record, raw_token

    @classmethod
    def from_raw_token(cls, raw_token: str):
        return cls.query.filter_by(token_hash=hash_token(raw_token)).first()

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > utcnow()


class ContactMessage(db.Model):
    __tablename__ = "contact_messages"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(24), nullable=False, default="new")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
