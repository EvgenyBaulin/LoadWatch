"""``python -m loadwatch.digest``: отправить сводки тем, у кого наступило выбранное время."""

import sys

from loadwatch.config import get_settings
from loadwatch.db import create_db_engine, create_session_factory
from loadwatch.logging_setup import configure_logging
from loadwatch.notifications import NotifierRegistry
from loadwatch.services.channel_service import ChannelService
from loadwatch.services.digest_service import DigestService


def main() -> int:
    """Точка входа."""
    settings = get_settings()
    configure_logging(settings)
    engine = create_db_engine(settings)
    try:
        with create_session_factory(engine)() as session:
            channels = ChannelService(session, settings, NotifierRegistry(settings))
            DigestService(session, settings, channels).send_due()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
