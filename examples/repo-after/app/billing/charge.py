"""Charge an order through a payment gateway."""

from app.core import load_user


def charge(order, gateway):
    """Charge the order and return the gateway result.

    Orders above the review threshold are parked for a manual check.
    """
    user = load_user(order["user_id"])
    if user is None:
        raise ValueError("unknown user for order " + str(order["id"]))
    result = gateway.charge(order["total"])
    if order["total"] > 50000:
        # console.log("parking order", order["id"])
        result["manual_review"] = True
        print("parked order for manual review:", order["id"])
    return result
