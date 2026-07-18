import os
from datetime import timedelta

from dotenv import load_dotenv


load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    APP_ENV = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development"))
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    EXTENSION_INSTALL_URL = os.getenv("EXTENSION_INSTALL_URL")

    SECRET_KEY = os.getenv("SECRET_KEY", "safescroll-development-key-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AUTO_CREATE_DATABASE = _env_bool("AUTO_CREATE_DATABASE", APP_ENV != "production")

    SESSION_COOKIE_NAME = "safescroll_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", APP_ENV == "production")
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    NORMAL_SESSION_LIFETIME = timedelta(hours=12)
    REMEMBER_SESSION_LIFETIME = timedelta(days=30)
    SESSION_TOUCH_INTERVAL = timedelta(minutes=5)

    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    RESET_TOKEN_LIFETIME = timedelta(hours=1)
    API_TOKEN_LIFETIME = timedelta(days=int(os.getenv("API_TOKEN_LIFETIME_DAYS", "90")))
    PAIRING_TOKEN_LIFETIME = timedelta(
        minutes=max(1, int(os.getenv("PAIRING_TOKEN_LIFETIME_MINUTES", "5")))
    )
    PAIRING_RATE_LIMIT_PER_MINUTE = max(
        1, int(os.getenv("PAIRING_RATE_LIMIT_PER_MINUTE", "10"))
    )
    PAIRING_EXCHANGE_RATE_LIMIT_PER_MINUTE = max(
        1, int(os.getenv("PAIRING_EXCHANGE_RATE_LIMIT_PER_MINUTE", "30"))
    )
    API_CORS_ORIGINS = tuple(
        origin.strip().rstrip("/")
        for origin in os.getenv("API_CORS_ORIGINS", "").split(",")
        if origin.strip()
    )
    API_RATE_LIMIT_PER_MINUTE = max(
        1, int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "120"))
    )
    API_RATE_LIMIT_WINDOW_SECONDS = max(
        1, int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60"))
    )
    PIN_ATTEMPT_LIMIT = max(1, int(os.getenv("PIN_ATTEMPT_LIMIT", "5")))
    PIN_ATTEMPT_WINDOW = timedelta(
        minutes=max(1, int(os.getenv("PIN_ATTEMPT_WINDOW_MINUTES", "15")))
    )
    PIN_LOCKOUT = timedelta(
        minutes=max(1, int(os.getenv("PIN_LOCKOUT_MINUTES", "15")))
    )
    # Flask-WTF passes this value to itsdangerous as seconds.
    WTF_CSRF_TIME_LIMIT = 2 * 60 * 60

    MAIL_SERVER = os.getenv("MAIL_SERVER")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", True)
    MAIL_USERNAME = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.getenv(
        "MAIL_DEFAULT_SENDER", os.getenv("DEFAULT_SENDER", "noreply@safescroll.local")
    )
    MAIL_SUPPRESS_SEND = _env_bool("MAIL_SUPPRESS_SEND", False)

    MAX_CONTENT_LENGTH = 1 * 1024 * 1024
