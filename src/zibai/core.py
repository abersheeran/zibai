import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Callable, Generator

from .h11 import http11_protocol
from .logger import debug_logger, logger
from .utils import unicode_to_wsgi
from .wsgi_typing import WSGIApp


def handle_connection(
    app: WSGIApp,
    s: socket.socket,
    address: tuple[str, int],
    graceful_exit: threading.Event,
    *,
    url_scheme: str = "http",
    script_name: str = "",
) -> None:
    debug_logger.debug("Handling connection from %s:%d", *address[:2])
    with s:
        try:
            http11_protocol(
                app,
                s,
                graceful_exit,
                url_scheme=url_scheme,
                script_name=script_name,
            )
        except ConnectionError:
            pass  # client closed connection, nothing to do


@contextmanager
def lifespan_hooks_context(
    before_serve_hook: Callable[[], None] = lambda: None,
    before_died_hook: Callable[[], None] = lambda: None,
) -> Generator[None, None, None]:
    """
    Context manager for lifespan hooks.
    """
    before_serve_hook()
    try:
        yield
    finally:
        before_died_hook()


def serve(
    *,
    app: Any,
    bind_socket: socket.socket,
    max_workers: int,
    graceful_exit: threading.Event,
    backlog: int | None = None,
    url_scheme: str = "http",
    script_name: str | None = None,
    before_serve_hook: Callable[[], None] = lambda: None,
    before_graceful_exit_hook: Callable[[], None] = lambda: None,
    before_died_hook: Callable[[], None] = lambda: None,
) -> None:
    """
    Serve a WSGI application.
    """
    if script_name is None:
        # If script_name is not specified, use the environment variable.
        script_name = unicode_to_wsgi(os.environ.get("SCRIPT_NAME", ""))

    listen_address = bind_socket.getsockname()[:2]

    def _handle_exit_event() -> None:
        graceful_exit.wait()
        try:
            before_graceful_exit_hook()
        except Exception:  # pragma: no cover
            logger.exception("Exception in `before_graceful_exit` callback")
        bind_socket.close()
        logger.info("Stopped listening on %s:%d", *listen_address)

    threading.Thread(target=_handle_exit_event, daemon=True).start()

    lifespan_hooks = lifespan_hooks_context(
        before_serve_hook=before_serve_hook, before_died_hook=before_died_hook
    )

    with lifespan_hooks, bind_socket, ThreadPoolExecutor(
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
                    raise  # pragma: no cover
            else:
                debug_logger.debug("Accepted connection from %s:%d", *address[:2])
                future = executor.submit(
                    handle_connection,
                    app,
                    connection,
                    address,
                    graceful_exit,
                    url_scheme=url_scheme,
                    script_name=script_name,
                )
                # raise exception in main thread if exception in threadpool
                future.add_done_callback(lambda future: future.result())
