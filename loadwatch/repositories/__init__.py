"""Репозитории — единственный слой, который пишет SQL."""

from loadwatch.repositories.user_repository import UserRepository

__all__ = ["UserRepository"]
