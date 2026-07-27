from __future__ import annotations

import logging
import sys

import uvicorn

from .app import create_app
from .config import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not settings.device_tokens:
        logging.error("DEVICE_TOKENS is empty; configure at least one device_id:token pair")
        sys.exit(2)

    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.listen_host,
        port=settings.listen_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
