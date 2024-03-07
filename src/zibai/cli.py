import argparse
import dataclasses
import importlib
import ipaddress
import logging
import multiprocessing
import os
import signal
import socket
import sys
import threading
from functools import reduce
from typing import Any, Callable, Sequence

from .core import serve
from .logger import logger
from .multiprocess import ProcessParameters, multiprocess
from .wsgi_typing import WSGIApp


@dataclasses.dataclass
class Options:
    """
    Keep `Options` can be passed between processes.
    """

    app: str
    call: bool = False
    listen: list[str] = dataclasses.field(default_factory=lambda: ["127.0.0.1:8000"])
    subprocess: int = 0
    no_gevent: bool = False
    max_workers: int = 10
    watchfiles: str | None = None

    backlog: int | None = None
    dualstack_ipv6: bool = False
    unix_socket_perms: int = 0o600
    h11_max_incomplete_event_size: int | None = None
    max_request_pre_process: int | None = None
    graceful_exit_timeout: float = 10

    # WSGI environment settings
    url_scheme: str = "http"
    url_prefix: str | None = None

    # Server callback hooks
    before_serve: str | None = None
    before_graceful_exit: str | None = None
    before_died: str | None = None

    # Logging
    no_access_log: bool = False

    # After __post_init__
    sockets: list[socket.socket] = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        if self.watchfiles is not None and self.subprocess <= 0:
            raise ValueError("Cannot watch files without subprocesses")

        if self.dualstack_ipv6 and not socket.has_dualstack_ipv6():
            raise ValueError("Dualstack ipv6 is not supported on this platform")

        self.init_sockets()

    @classmethod
    def default_value(cls, field_name: str) -> Any:
        fields = {field.name: field for field in dataclasses.fields(Options)}
        default = fields[field_name].default
        default_factory = fields[field_name].default_factory
        if default is dataclasses.MISSING and default_factory is dataclasses.MISSING:
            raise ValueError(f"Field {field_name} has no default value")
        if default_factory is not dataclasses.MISSING:
            return default_factory()
        return default

    def init_sockets(self) -> None:
        self.sockets = []

        for listen in self.listen:
            sock = create_bind_socket(
                listen,
                uds_perms=self.unix_socket_perms,
                dualstack_ipv6=self.dualstack_ipv6,
            )
            if self.backlog is not None:
                sock.listen(self.backlog)
            else:
                sock.listen()
            self.sockets.append(sock)

    def get_application(self) -> WSGIApp:
        app = import_from_string(self.app)
        if self.call:
            app = app()
        return app

    def get_before_serve_hook(self) -> Callable[[], None]:
        if self.before_serve is not None:
            return import_from_string(self.before_serve)
        else:
            return lambda: None

    def get_before_graceful_exit_hook(self) -> Callable[[], None]:
        if self.before_graceful_exit is not None:
            return import_from_string(self.before_graceful_exit)
        else:
            return lambda: None

    def get_before_died_hook(self) -> Callable[[], None]:
        if self.before_died is not None:
            return import_from_string(self.before_died)
        else:
            return lambda: None

    def configure_logging(self) -> None:
        if self.no_access_log:
            logging.getLogger("zibai.access").setLevel(logging.WARNING)

        # Set default logging format.
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
        )


def import_from_string(import_str: str) -> Any:
    module_str, _, attrs_str = import_str.partition(":")
    if not module_str or not attrs_str:
        raise ValueError(
            f'Import string "{import_str}" must be in format "<module>:<attribute>".'
        )

    return reduce(getattr, attrs_str.split("."), importlib.import_module(module_str))


def create_bind_socket(
    value: str,
    *,
    uds_perms: int = Options.default_value("unix_socket_perms"),
    dualstack_ipv6: bool = Options.default_value("dualstack_ipv6"),
    socket_type: int = socket.SOCK_STREAM,
) -> socket.socket:
    if value.startswith("unix:"):
        if not hasattr(socket, "AF_UNIX"):
            raise ValueError("UNIX sockets are not supported on this platform")

        path = value[5:]
        sock = socket.socket(socket.AF_UNIX, socket_type)  # type: ignore
        if os.path.exists(path):
            os.unlink(path)
        sock.bind(path)

        os.chmod(path, uds_perms)
        return sock

    if ":" not in value:
        raise ValueError("Bind must be of the form: HOST:PORT")

    host, port = value.rsplit(":", 1)

    try:
        port = int(port)
    except ValueError:
        raise ValueError("Bind port must be an integer")

    if not 0 < port < 65536:
        raise ValueError("Bind port must be between 0 and 65536")

    if host == "":
        if dualstack_ipv6:
            host = "::"
        else:
            host = "0.0.0.0"

    address = ipaddress.ip_address(host)
    sock = socket.socket(
        socket.AF_INET if address.version == 4 else socket.AF_INET6,
        socket_type,
    )

    # Set socket options
    if dualstack_ipv6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    if os.name != "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    else:  # In windows, SO_REUSEPORT is not available
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    sock.bind((str(address), port))

    return sock


def parse_args(args: Sequence[str]) -> Options:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    # Please keep the order of arguments like `Options`.
    parser.add_argument("app", help="WSGI app")
    parser.add_argument(
        "--call",
        help="use WSGI factory",
        default=Options.default_value("call"),
        action="store_true",
    )
    parser.add_argument(
        "--listen",
        "-l",
        default=Options.default_value("listen"),
        nargs="+",
        help="listen address, HOST:PORT, unix:PATH",
    )
    parser.add_argument(
        "--subprocess",
        "-p",
        default=Options.default_value("subprocess"),
        type=int,
        help="number of subprocesses",
    )
    parser.add_argument(
        "--no-gevent",
        default=Options.default_value("no_gevent"),
        action="store_true",
        help="do not use gevent",
    )
    parser.add_argument(
        "--max-workers",
        "-w",
        default=Options.default_value("max_workers"),
        type=int,
        help="maximum number of threads or greenlets to use for handling requests",
    )
    parser.add_argument(
        "--watchfiles",
        help="watch files for changes and restart workers",
        required=False,
    )
    parser.add_argument(
        "--backlog",
        type=int,
        help="listen backlog",
        required=False,
    )
    parser.add_argument(
        "--dualstack-ipv6",
        default=Options.default_value("dualstack_ipv6"),
        action="store_true",
        help="enable dualstack ipv6",
    )
    parser.add_argument(
        "--unix-socket-perms",
        default="600",
        help="unix socket permissions",
    )
    parser.add_argument(
        "--h11-max-incomplete-event-size",
        type=int,
        help="maximum number of bytes in an incomplete HTTP event",
        required=False,
    )
    parser.add_argument(
        "--max-request-pre-process",
        type=int,
        help="maximum number of requests to process before killing the worker",
        required=False,
    )
    parser.add_argument(
        "--graceful-exit-timeout",
        default=Options.default_value("graceful_exit_timeout"),
        type=float,
        help="graceful exit timeout",
    )
    parser.add_argument(
        "--url-scheme",
        default=Options.default_value("url_scheme"),
        help="url scheme; will be passed to WSGI app as wsgi.url_scheme",
    )
    parser.add_argument(
        "--url-prefix",
        help="url prefix; will be passed to WSGI app as SCRIPT_NAME, "
        "if not specified, use environment variable SCRIPT_NAME",
        required=False,
    )
    parser.add_argument(
        "--before-serve",
        help="callback to run before serving requests",
        required=False,
    )
    parser.add_argument(
        "--before-graceful-exit",
        help="callback to run before graceful exit",
        required=False,
    )
    parser.add_argument(
        "--before-died",
        help="callback to run before exiting",
        required=False,
    )
    parser.add_argument(
        "--no-access-log",
        default=Options.default_value("no_access_log"),
        action="store_true",
        help="disable access log",
    )
    options = parser.parse_args(args)

    # Parse unix_socket_perms as an octal integer.
    options.unix_socket_perms = int(options.unix_socket_perms, base=8)

    # When watchfiles is specified, subprocess must be greater than 0.
    if options.watchfiles is not None:
        options.subprocess = max(options.subprocess, 1)

    return Options(**options.__dict__)


spawn = multiprocessing.get_context("spawn")


def main(options: Options, *, is_main: bool = True) -> None:
    """
    Main entrypoint for running Zī Bái.
    """
    options.configure_logging()

    if is_main:
        for sock in options.sockets:
            sockname = sock.getsockname()
            if isinstance(sockname, str):
                logger.info("Listening on %s", sockname)
            else:
                logger.info("Listening on %s:%d", *sockname[:2])

    if not options.no_gevent and (options.subprocess == 0 or not is_main):
        # Single process mode or worker process with gevent.
        try:
            import gevent
        except ImportError:
            logger.warning("gevent not found, using threading instead")
        else:
            import gevent.monkey

            gevent.monkey.patch_all()
            logger.info("Using gevent for worker pool")

    # Before use multiprocessing, we need to call `get_application` to make sure
    # the application can be imported correctly.
    application = options.get_application()

    if is_main and options.subprocess > 0:
        multiprocess(
            options.subprocess,
            ProcessParameters(main, options, is_main=False),
            options.watchfiles,
            options.graceful_exit_timeout,
        )
        return

    if options.h11_max_incomplete_event_size is not None:
        # Set max_incomplete_event_size
        from . import h11

        h11.MAX_INCOMPLETE_EVENT_SIZE = options.h11_max_incomplete_event_size

    if options.max_request_pre_process is not None:
        from .middlewares.limit_request_count import LimitRequestCountMiddleware

        application = LimitRequestCountMiddleware(
            application, options.max_request_pre_process
        )

    graceful_exit = threading.Event()

    def handle_int(sig, frame) -> None:
        logger.info("Received SIGINT, qucikly exiting")
        graceful_exit.set()
        sys.exit(0)

    def handle_term(sig, frame) -> None:
        if graceful_exit.is_set():
            logger.info("Received second SIGTERM, quickly exiting")
            sys.exit(0)
        logger.info("Received SIGTERM, gracefully exiting")
        graceful_exit.set()

    signal.signal(signal.SIGINT, handle_int)
    signal.signal(signal.SIGTERM, handle_term)

    if is_main:
        logger.info("Run in single process mode [%d]", os.getpid())

    serve(
        app=application,
        bind_sockets=options.sockets,
        max_workers=options.max_workers,
        graceful_exit=graceful_exit,
        url_scheme=options.url_scheme,
        script_name=options.url_prefix,
        before_serve_hook=options.get_before_serve_hook(),
        before_graceful_exit_hook=options.get_before_graceful_exit_hook(),
        before_died_hook=options.get_before_died_hook(),
    )
