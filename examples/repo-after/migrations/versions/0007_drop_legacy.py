"""Drop the legacy email column and add a risk score."""

revision = "0007"


def upgrade(op):
    op.execute("ALTER TABLE users DROP COLUMN legacy_email")
    op.execute("ALTER TABLE orders ADD COLUMN risk_score INTEGER NOT NULL")
    op.execute("UPDATE orders SET risk_score = 0")
