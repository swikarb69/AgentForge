"""Database access module."""


def get_db_connection() -> str:
    """Return database connection string."""
    return "sqlite:///:memory:"
