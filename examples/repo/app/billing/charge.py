"""Charge an order through a payment gateway."""

from app.core import load_user


def charge(order, gateway):
    """Charge the order and return the gateway result."""
    user = load_user(order["user_id"])
    if user is None:
        raise ValueError("unknown user for order " + str(order["id"]))
    return gateway.charge(order["total"])
