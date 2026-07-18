from flask import Blueprint


bp = Blueprint("account", __name__)


from safescroll.account import routes  # noqa: E402, F401
