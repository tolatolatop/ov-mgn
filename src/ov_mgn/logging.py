import logging
import sys
from typing import TextIO

from ov_mgn.config import LogLevel

LOGGER_NAME = "ov_mgn"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: LogLevel = "INFO", stream: TextIO | None = None) -> None:
    """Configure application logging once for CLI execution."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    root_logger = logging.getLogger(LOGGER_NAME)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
    root_logger.propagate = False


def get_logger(name: str | None = None) -> logging.Logger:
    if not name:
        return logging.getLogger(LOGGER_NAME)
    if name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}."):
        return logging.getLogger(name)

    return logging.getLogger(f"{LOGGER_NAME}.{name}")
