from flask import Blueprint


bp = Blueprint("auth", __name__)

from safescroll.auth import routes  # noqa: E402, F401
