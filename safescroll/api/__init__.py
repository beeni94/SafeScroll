from flask import Blueprint


bp = Blueprint("api", __name__, url_prefix="/api/v1")


# Importing models here ensures SQLAlchemy knows about the token table before
# create_all() runs. Routes and CLI commands attach themselves to the blueprint.
from safescroll.api import cli, extension, models, pairing, routes  # noqa: E402, F401
