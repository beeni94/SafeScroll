import smtplib
from email.message import EmailMessage

from flask import current_app


def send_password_reset_email(recipient: str, reset_url: str) -> None:
    subject = "Reset your SafeScroll password"
    body = (
        "A password reset was requested for your SafeScroll account.\n\n"
        f"Reset your password: {reset_url}\n\n"
        "This link expires in one hour. If you did not request it, you can ignore this email."
    )
    message_record = {
        "to": recipient,
        "subject": subject,
        "body": body,
        "reset_url": reset_url,
    }
    capture_for_development = (
        current_app.testing
        or current_app.config.get("MAIL_SUPPRESS_SEND")
        or current_app.config.get("APP_ENV") != "production"
    )
    if capture_for_development:
        current_app.extensions.setdefault("mail_outbox", []).append(message_record)

    if (
        current_app.testing
        or current_app.config.get("MAIL_SUPPRESS_SEND")
        or not current_app.config.get("MAIL_SERVER")
    ):
        current_app.logger.info("Password reset email queued for %s", recipient)
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = current_app.config["MAIL_DEFAULT_SENDER"]
    message["To"] = recipient
    message.set_content(body)

    server = smtplib.SMTP(
        current_app.config["MAIL_SERVER"], current_app.config["MAIL_PORT"], timeout=10
    )
    try:
        if current_app.config.get("MAIL_USE_TLS"):
            server.starttls()
        username = current_app.config.get("MAIL_USERNAME")
        if username:
            server.login(username, current_app.config.get("MAIL_PASSWORD") or "")
        server.send_message(message)
    finally:
        server.quit()
