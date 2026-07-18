from flask import Blueprint


bp = Blueprint("modes", __name__)


from safescroll.modes import routes  # noqa: E402, F401
