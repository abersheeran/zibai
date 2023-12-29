import logging

logger = logging.getLogger("zibai")

debug_logger = logging.getLogger("zibai.debug")
debug_logger.setLevel(logging.INFO)  # Disable debug logging by default.

access_logger = logging.getLogger("zibai.access")

error_logger = logging.getLogger("zibai.error")


def log_http(environ, status_code) -> None:
    if status_code >= 500:
        error_logger.error(
            '"%s %s %s" %s',
            environ["REQUEST_METHOD"],
            environ["PATH_INFO"],
            environ["SERVER_PROTOCOL"],
            status_code,
            extra=environ,
            exc_info=True,
        )
    else:
        access_logger.info(
            '"%s %s %s" %s',
            environ["REQUEST_METHOD"],
            environ["PATH_INFO"],
            environ["SERVER_PROTOCOL"],
            status_code,
            extra=environ,
        )
