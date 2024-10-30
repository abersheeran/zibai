import os
import queue
import selectors
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from contextlib import contextmanager
from types import TracebackType
from typing import Any, Callable, Generator, Protocol
from typing import cast as typing_cast

from .h11 import http11_protocol
from .logger import debug_logger, logger
from .utils import unicode_to_wsgi
from .wsgi_typing import WSGIApp


class ThreadPoolExecutor(_ThreadPoolExecutor):
    def __init__(
        self,
        max_workers: int | None = None,
        thread_name_prefix: str = "",
        initializer: Callable[..., object] | None = None,
        initargs: tuple[Any, ...] = (),
        *,
        join_timeout: float = 5,
    ) -> None:
        super().__init__(max_workers, thread_name_prefix, initializer, initargs)
        self._join_timeout = join_timeout

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._shutdown_lock:
            self._shutdown = True
            # Drain all work items from the queue, and then cancel their
            # associated futures.
            while True:
                try:
                    work_item = self._work_queue.get_nowait()
                except queue.Empty:
                    break
                if work_item is not None:
                    work_item.future.cancel()

            # Send a wake-up to prevent threads calling
            # _work_queue.get(block=True) from permanently blocking.
            self._work_queue.put(None)  # type: ignore

        for t in self._threads:
            t.join(self._join_timeout)


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


class ContextManager(Protocol):
    def __enter__(self) -> Any: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> bool | None: ...


@contextmanager
def quickly_exit_manager(
    *contextmangers: ContextManager, quickly_exit: threading.Event
) -> Generator[None, None, None]:
    for cm in contextmangers:
        cm.__enter__()
    try:
        yield
    finally:
        if not quickly_exit.is_set():
            for cm in contextmangers:
                cm.__exit__(*sys.exc_info())


def serve(
    *,
    app: Any,
    bind_sockets: list[socket.socket],
    max_workers: int,
    graceful_exit: threading.Event,
    graceful_exit_timeout: float = 10,
    quickly_exit: threading.Event = threading.Event(),
    url_scheme: str = "http",
    script_name: str | None = None,
    before_serve_hook: Callable[[], None] = lambda: None,
    before_graceful_exit_hook: Callable[[], None] = lambda: None,
    before_died_hook: Callable[[], None] = lambda: None,
    socket_timeout: float = 5,
) -> None:
    """
    Serve a WSGI application.
    """
    if script_name is None:
        # If script_name is not specified, use the environment variable.
        script_name = unicode_to_wsgi(os.environ.get("SCRIPT_NAME", ""))

    def _handle_exit_event() -> None:
        graceful_exit.wait()
        try:
            before_graceful_exit_hook()
        except Exception:  # pragma: no cover
            logger.exception("Exception in `before_graceful_exit` callback")

    threading.Thread(target=_handle_exit_event, daemon=True).start()

    lifespan_hooks = lifespan_hooks_context(
        before_serve_hook=before_serve_hook, before_died_hook=before_died_hook
    )
    executor = ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="zibai_worker",
        join_timeout=graceful_exit_timeout,
    )
    selector = selectors.DefaultSelector()

    connections: set[socket.socket] = set()

    with lifespan_hooks, selector, executor:
        for sock in bind_sockets:
            selector.register(sock, selectors.EVENT_READ)

        while not graceful_exit.is_set():
            events = selector.select(timeout=0.1)
            if not events:
                continue

            for key, _ in events:
                try:
                    sock: socket.socket = typing_cast(socket.socket, key.fileobj)
                    connection, address = sock.accept()
                    debug_logger.debug("Accepted connection from %s:%d", *address[:2])
                except (BlockingIOError, ConnectionError):
                    continue
                else:
                    executor.submit(
                        handle_connection,
                        app,
                        connection,
                        address,
                        graceful_exit,
                        socket_timeout,
                        connections,
                        url_scheme=url_scheme,
                        script_name=script_name,
                    )

        if quickly_exit.is_set():
            # Close all connections, forcefully
            debug_logger.debug("Closing all connections")
            for connection in tuple(connections):
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
            debug_logger.debug("Closed all connections")


def handle_connection(
    app: WSGIApp,
    connection: socket.socket,
    address: tuple[str, int],
    graceful_exit: threading.Event,
    socket_timeout: float,
    connections: set[socket.socket],
    *,
    url_scheme: str = "http",
    script_name: str = "",
) -> None:
    connection.settimeout(socket_timeout)
    debug_logger.debug("Handling connection from %s:%d", *address[:2])
    with connection:
        try:
            connections.add(connection)

            http11_protocol(
                app,
                connection,
                graceful_exit,
                url_scheme=url_scheme,
                script_name=script_name,
            )
        except ConnectionError:
            pass  # client closed connection, nothing to do
        finally:
            connections.discard(connection)
