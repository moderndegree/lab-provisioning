"""Entry point. `python -m voice_gateway`, which is what the systemd unit runs."""

from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import load


def main() -> None:
    cfg = load()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    uvicorn.run(
        create_app(cfg),
        host=cfg.host,
        port=cfg.port,
        # journald already timestamps and levels everything; uvicorn's access
        # log would double every line for a service whose traffic is one long
        # websocket per client.
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
