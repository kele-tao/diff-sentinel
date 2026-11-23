"""Initial schema."""

revision = "0001"


def upgrade(op):
    op.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
