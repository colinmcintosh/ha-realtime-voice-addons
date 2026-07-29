from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)
    if not settings.device_tokens:
        # Not fatal: devices are normally enrolled from the pairing UI, which
        # stores only a hash of the token it mints. Refusing to start here would
        # mean there was no way to reach the UI that hands out the first one.
        logger.info(
            "DEVICE_TOKENS is empty; enrol devices from the pairing UI "
            "(configured tokens remain supported)"
        )

    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.listen_host,
        port=settings.listen_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
