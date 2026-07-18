from flask import Blueprint


bp = Blueprint("devices", __name__)


from safescroll.devices import routes  # noqa: E402, F401
