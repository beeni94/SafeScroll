import secrets
from datetime import timedelta

from safescroll.extensions import db
from safescroll.utils import hash_token, utcnow


TOKEN_MARKER = "ss_"
TOKEN_PREFIX_LENGTH = 16


class APIToken(db.Model):
    """Revocable bearer credential for extension/API access.

    Only a SHA-256 digest is persisted. The raw credential is returned once by
    :meth:`issue` and cannot be recovered from the database.
    """

    __tablename__ = "api_tokens"

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
    label = db.Column(db.String(120), nullable=False, default="Chrome extension")
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    prefix = db.Column(db.String(TOKEN_PREFIX_LENGTH), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    revoked_at = db.Column(db.DateTime, index=True)
    last_used_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="api_tokens")
    extension_device = db.relationship(
        "ExtensionDevice", back_populates="api_tokens"
    )

    @classmethod
    def issue(
        cls,
        user,
        *,
        lifetime: timedelta,
        label: str = "Chrome extension",
        extension_device=None,
    ) -> tuple["APIToken", str]:
        if lifetime.total_seconds() <= 0:
            raise ValueError("Token lifetime must be positive.")
        raw_token = f"{TOKEN_MARKER}{secrets.token_urlsafe(32)}"
        if extension_device is not None and extension_device.user_id != user.id:
            raise ValueError("An API token can only be bound to its user's device.")
        record = cls(
            user_id=user.id,
            extension_device=extension_device,
            label=(label or "Chrome extension").strip()[:120],
            token_hash=hash_token(raw_token),
            prefix=raw_token[:TOKEN_PREFIX_LENGTH],
            expires_at=utcnow() + lifetime,
        )
        return record, raw_token

    @classmethod
    def from_raw_token(cls, raw_token: str):
        if not isinstance(raw_token, str) or not raw_token.startswith(TOKEN_MARKER):
            return None
        return cls.query.filter_by(token_hash=hash_token(raw_token)).first()

    @property
    def is_valid(self) -> bool:
        device_is_valid = (
            self.extension_device is None or self.extension_device.is_connected
        )
        return (
            self.revoked_at is None
            and self.expires_at > utcnow()
            and device_is_valid
        )

    def revoke(self) -> None:
        if self.revoked_at is None:
            self.revoked_at = utcnow()

    def export_dict(self) -> dict:
        return {
            "id": self.id,
            "extension_device_id": self.extension_device_id,
            "label": self.label,
            "prefix": self.prefix,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "revoked": self.revoked_at is not None,
        }


# PEP-8-friendly name for new code while retaining the established APIToken
# import used by the Sprint-2 API and existing databases/tests.
ApiToken = APIToken
