import logging
from io import StringIO

from ov_mgn.logging import configure_logging, get_logger


def test_get_logger_keeps_package_module_name() -> None:
    logger = get_logger("ov_mgn.cli")

    assert logger.name == "ov_mgn.cli"


def test_get_logger_prefixes_short_name() -> None:
    logger = get_logger("docker")

    assert logger.name == "ov_mgn.docker"


def test_configure_logging_writes_to_stream() -> None:
    stream = StringIO()
    configure_logging("INFO", stream=stream)

    logging.getLogger("ov_mgn").info("ready")

    assert "INFO [ov_mgn] ready" in stream.getvalue()
