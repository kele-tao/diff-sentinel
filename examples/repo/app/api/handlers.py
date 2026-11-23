"""Request handlers that compose the service modules."""

from app.auth.session import refresh
from app.billing.charge import charge


def handle_refresh(request, now):
    """Handle POST /session/refresh."""
    session = refresh(request["session"], now)
    if session is None:
        return {"status": 401}
    return {"status": 200, "session": session}


def handle_charge(request, gateway):
    """Handle POST /orders/charge."""
    return {"status": 200, "result": charge(request["order"], gateway)}
