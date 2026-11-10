"""Run the Aio server: python -m aio_server"""

import logging

import uvicorn

from .api import create_app, engine_from
from .config import Settings

log = logging.getLogger("aio_server")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    settings = Settings.from_env()
    if settings.host not in ("127.0.0.1", "localhost", "::1") and not settings.auth:
        log.warning("Listening on %s without AIO_AUTH: anyone who can reach this port can use it", settings.host)
    engine = engine_from(settings)
    log.info("Locating ffmpeg (the first run may download a static build)")
    engine.ensure_ffmpeg()
    app = create_app(settings, engine=engine)
    print(f"\n  Aio is running at http://{settings.host}:{settings.port}\n")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
