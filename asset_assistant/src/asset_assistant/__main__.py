"""The only supported launcher: one worker, loopback, no proxy headers."""

import uvicorn

from .app import create_app
from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port,
                workers=1, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()