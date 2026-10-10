"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from loadwatch.models.user import User

__all__ = ["User"]
