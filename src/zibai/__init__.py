from .logger import logger

try:
    import gevent
except ImportError:
    USIING_GEVENT = False
    logger.warning("gevent not found, using threading instead")
else:
    import gevent.monkey

    gevent.monkey.patch_all()
    USIING_GEVENT = True


from .core import serve
from .multiprocess import MultiProcessManager

__all__ = ["serve", "MultiProcessManager"]
