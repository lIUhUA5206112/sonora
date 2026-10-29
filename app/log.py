"""Tiny append-only log for troubleshooting (data/sonora.log)."""

from __future__ import annotations

import time

from . import config


def write(message: str) -> None:
    try:
        config.DATA.mkdir(parents=True, exist_ok=True)
        with open(config.DATA / "sonora.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass
