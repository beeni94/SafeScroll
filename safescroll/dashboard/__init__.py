from flask import Blueprint


bp = Blueprint("dashboard", __name__)

from safescroll.dashboard import routes  # noqa: E402, F401
