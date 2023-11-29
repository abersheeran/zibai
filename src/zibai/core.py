import atexit
import socket
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Any, Callable

from .wsgi_typing import WSGIApp

from .h11 import http11_protocol
from .logger import logger, debug_logger


def handle_connection(
    app: WSGIApp,
    s: socket.socket,
    address: tuple[str, int],
    graceful_exit: threading.Event,
) -> None:
    debug_logger.debug("Handling connection from %s:%d", *address[:2])
    with s:
        http11_protocol(app, s, graceful_exit)


def serve(
    *,
    app: Any,
    bind_socket: socket.socket,
    backlog: int | None,
    max_workers: int,
    graceful_exit: threading.Event,
    before_serve_hook: Callable[[], None] = lambda: None,
    before_graceful_exit_hook: Callable[[], None] = lambda: None,
    before_died_hook: Callable[[], None] = lambda: None,
) -> None:
    """
    Serve a WSGI application.
    """
    listen_address = bind_socket.getsockname()[:2]

    def _handle_exit_event() -> None:
        graceful_exit.wait()
        try:
            before_graceful_exit_hook()
        except Exception:
            logger.exception("Exception in `before_graceful_exit` callback")
        bind_socket.close()
        logger.info("Stopped listening on %s:%d", *listen_address)

    threading.Thread(target=_handle_exit_event, daemon=True).start()

    before_serve_hook()
    atexit.register(before_died_hook)

    with bind_socket, ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="zibai_worker"
    ) as executor:
        if backlog is not None:
            bind_socket.listen(backlog)
        else:
            bind_socket.listen()
        logger.info("Accepting request on %s:%d", *listen_address)

        while not graceful_exit.is_set():
            try:
                connection, address = bind_socket.accept()
            except OSError:  # bind_socket closed
                if not graceful_exit.is_set():
                    raise
            else:
                debug_logger.debug("Accepted connection from %s:%d", *address[:2])
                future = executor.submit(
                    handle_connection, app, connection, address, graceful_exit
                )
                # raise exception in main thread if exception in threadpool
                future.add_done_callback(lambda future: future.result())
