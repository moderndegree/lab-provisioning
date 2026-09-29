"""`python -m voice_chatter` — load the model, then serve /v1/audio/speech."""

from __future__ import annotations

import logging

import uvicorn

from .server import create_app, settings_from_env


def main() -> None:
    cfg = settings_from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
