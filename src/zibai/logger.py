import copy
import logging

logger = logging.getLogger("zibai")

debug_logger = logging.getLogger("zibai.debug")

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


LOGGING_CONFIG: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s %(levelname)s %(message)s",
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
        "error": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
    },
    "loggers": {
        "zibai": {"handlers": ["default"], "level": "INFO"},
        "zibai.debug": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "zibai.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "zibai.error": {"handlers": ["error"], "level": "ERROR", "propagate": False},
    },
}


def _merge_dict(base: dict, config: dict) -> dict:
    base = copy.deepcopy(base)  # deep copy

    for key, value in config.items():
        if key not in base:
            base[key] = value
        else:
            if isinstance(value, dict):
                base[key] = _merge_dict(base[key], value)
            else:
                base[key] = value
    return base


def load_config(config: dict) -> dict:
    return _merge_dict(LOGGING_CONFIG, config)
