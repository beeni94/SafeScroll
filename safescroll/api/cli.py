from datetime import timedelta

import click
from flask import current_app

from safescroll.api import bp
from safescroll.api.models import APIToken, TOKEN_PREFIX_LENGTH
from safescroll.extensions import db
from safescroll.models import User
from safescroll.utils import normalize_email, utcnow


@bp.cli.command("issue-token")
@click.option("--email", required=True, help="SafeScroll account email.")
@click.option("--label", default="Chrome extension", show_default=True)
@click.option("--days", type=click.IntRange(1, 365), default=None)
def issue_token_command(email: str, label: str, days: int | None):
    """Issue an API bearer token and display its raw value once."""

    try:
        normalized = normalize_email(email)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    user = User.query.filter_by(email=normalized, is_active=True).first()
    if user is None:
        raise click.ClickException("No active account matches that email.")

    lifetime = timedelta(days=days) if days else current_app.config["API_TOKEN_LIFETIME"]
    token, raw_token = APIToken.issue(user, lifetime=lifetime, label=label)
    db.session.add(token)
    db.session.commit()

    click.echo("API token created. Store it securely; it will not be shown again.")
    click.echo(f"Token: {raw_token}")
    click.echo(f"Prefix: {token.prefix}")
    click.echo(f"Expires: {token.expires_at.isoformat()}")


@bp.cli.command("revoke-token")
@click.option("--prefix", required=True, help="Token prefix shown at issuance.")
def revoke_token_command(prefix: str):
    """Revoke one API bearer token by its non-secret prefix."""

    prefix = prefix.strip()[:TOKEN_PREFIX_LENGTH]
    token = APIToken.query.filter_by(prefix=prefix).first()
    if token is None:
        raise click.ClickException("No API token matches that prefix.")
    if token.revoked_at is not None:
        raise click.ClickException("That API token is already revoked.")
    token.revoked_at = utcnow()
    db.session.commit()
    click.echo(f"Revoked API token {token.prefix}.")
