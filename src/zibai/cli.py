import argparse
import dataclasses
import importlib
import ipaddress
import multiprocessing
import os
import signal
import socket
from functools import reduce
import threading
from typing import Any, Sequence

from .logger import logger
from .core import serve
from .multiprocess import multiprocess
from .wsgi_typing import WSGIApp


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
    uds_perms: int,
    dualstack_ipv6: bool,
    socket_type: int = socket.SOCK_STREAM,
) -> socket.socket:
    if value.startswith("unix:"):
        if not hasattr(socket, "AF_UNIX"):
            raise ValueError("UNIX sockets are not supported on this platform")

        path = value[5:]
        sock = socket.socket(socket.AF_UNIX, socket_type)  # type: ignore
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

    # Bind socket
    sock.bind((str(address), port))

    return sock


@dataclasses.dataclass
class Options:
    """
    Keep `Options` can be passed between processes.
    """

    app: str
    listen: str = "127.0.0.1:9000"
    subprocess: int = 0
    no_gevent: bool = False
    max_workers: int = 10
    watchfiles: str | None = None

    backlog: int | None = None
    dualstack_ipv6: bool = False
    unix_socket_perms: int = 0o600
    h11_max_incomplete_event_size: int | None = None
    max_request_pre_process: int | None = None

    # Server callback hooks
    before_serve: str | None = None
    before_graceful_exit: str | None = None
    before_died: str | None = None

    def __post_init__(self) -> None:
        """
        Check options. Do not do any side effects here.
        """
        if self.watchfiles is not None and self.subprocess <= 0:
            raise ValueError("Cannot watch files without subprocesses")


def parse_args(args: Sequence[str]) -> Options:
    fields = {field.name: field for field in dataclasses.fields(Options)}

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("app", help="WSGI app")
    parser.add_argument(
        "--listen",
        "-l",
        default=fields["listen"].default,
        help="listen address, HOST:PORT, unix:PATH",
    )
    parser.add_argument(
        "--backlog",
        type=int,
        help="listen backlog",
        required=False,
    )
    parser.add_argument(
        "--dualstack-ipv6",
        default=fields["dualstack_ipv6"].default,
        action="store_true",
        help="enable dualstack ipv6",
    )
    parser.add_argument(
        "--unix-socket-perms",
        default="600",
        help="unix socket permissions",
    )
    parser.add_argument(
        "--subprocess",
        "-p",
        default=fields["subprocess"].default,
        type=int,
        help="number of subprocesses",
    )
    parser.add_argument(
        "--watchfiles",
        help="watch files for changes and restart workers",
        required=False,
    )
    parser.add_argument(
        "--no-gevent",
        default=fields["no_gevent"].default,
        action="store_true",
        help="do not use gevent",
    )
    parser.add_argument(
        "--max-workers",
        "-w",
        default=fields["max_workers"].default,
        type=int,
        help="maximum number of threads or greenlets to use for handling requests",
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
    options = parser.parse_args(args)

    # Parse unix_socket_perms as an octal integer.
    options.unix_socket_perms = int(options.unix_socket_perms, base=8)

    # Check that platform supports dualstack ipv6.
    if options.dualstack_ipv6 and not socket.has_dualstack_ipv6():
        raise ValueError("Dualstack ipv6 is not supported on this platform")

    if options.watchfiles is not None:
        options.subprocess = max(options.subprocess, 1)

    return Options(**options.__dict__)


spawn = multiprocessing.get_context("spawn")


def get_app(string: str) -> Any:
    """
    Get WSGI app from import string.
    """
    return import_from_string(string)


def main(options: Options, is_main: bool = True) -> None:
    if not is_main and not options.no_gevent:
        try:
            import gevent
        except ImportError:
            logger.warning("gevent not found, using threading instead")
        else:
            import gevent.monkey

            gevent.monkey.patch_all()

    # Check that app is importable.
    get_app(options.app)
    # Check that bind socket can be created.
    create_bind_socket(
        options.listen,
        uds_perms=options.unix_socket_perms,
        dualstack_ipv6=options.dualstack_ipv6,
    ).close()

    if is_main and options.subprocess > 0:
        multiprocess(
            options.subprocess,
            lambda: spawn.Process(target=main, args=(options, False), daemon=True),
            options.watchfiles,
        )
        return

    if options.h11_max_incomplete_event_size is not None:
        # Set max_incomplete_event_size
        from . import h11

        h11.MAX_INCOMPLETE_EVENT_SIZE = options.h11_max_incomplete_event_size

    if options.before_serve is not None:
        before_serve_hook = import_from_string(options.before_serve)
    else:
        before_serve_hook = lambda: None

    if options.before_graceful_exit is not None:
        before_graceful_exit_hook = import_from_string(options.before_graceful_exit)
    else:
        before_graceful_exit_hook = lambda: None

    if options.before_died is not None:
        before_died_hook = import_from_string(options.before_died)
    else:
        before_died_hook = lambda: None

    application: WSGIApp = import_from_string(options.app)
    if options.max_request_pre_process is not None:
        from .middlewares.limit_request_count import LimitRequestCountMiddleware

        application = LimitRequestCountMiddleware(
            application, options.max_request_pre_process
        )

    graceful_exit = threading.Event()

    for sig in (
        signal.SIGINT,  # Sent by Ctrl+C.
        signal.SIGTERM  # Sent by `kill <pid>`. Not sent on Windows.
        if os.name != "nt"
        else signal.SIGBREAK,  # Sent by `Ctrl+Break` on Windows.
    ):
        signal.signal(
            sig,
            lambda sig, frame: (
                graceful_exit.set() if not graceful_exit.is_set() else exit(0)
            ),
        )

    serve(
        app=application,
        bind_socket=create_bind_socket(
            options.listen,
            uds_perms=options.unix_socket_perms,
            dualstack_ipv6=options.dualstack_ipv6,
        ),
        backlog=options.backlog,
        max_workers=options.max_workers,
        graceful_exit=graceful_exit,
        before_serve_hook=before_serve_hook,
        before_graceful_exit_hook=before_graceful_exit_hook,
        before_died_hook=before_died_hook,
    )
