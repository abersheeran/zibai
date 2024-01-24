import dataclasses
import socket
import sys
import threading
from typing import Any, Callable

import h11

from .const import SERVER_NAME
from .logger import debug_logger, error_logger, log_http
from .utils import Input
from .wsgi_typing import Environ, ExceptionInfo, WSGIApp


class ConnectionClosed(Exception):
    """
    Received a ConnectionClosed event from h11.
    """


@dataclasses.dataclass
class H11Protocol:
    c: h11.Connection
    s: socket.socket
    peername: tuple[str, int]
    sockname: tuple[str, int]

    graceful_exit: threading.Event

    # WSGI variables
    url_scheme: str = "http"
    script_name: str = ""

    # State variables
    response_buffer: tuple[int, list[tuple[bytes, bytes]]] | None = None

    def __post_init__(self):
        self.s.settimeout(1)  # For graceful exit

    @property
    def header_sent(self) -> bool:
        return self.c.our_state is not h11.IDLE

    def get_next_event(self):
        if self.c.their_state is h11.DONE:
            return h11.PAUSED

        while True:
            if self.c.their_state is h11.IDLE and self.graceful_exit.is_set():
                raise ConnectionClosed

            event = self.c.next_event()
            debug_logger.debug("Received event from %s:%d: %r", *self.peername, event)

            match event:
                case h11.NEED_DATA:
                    if self.c.they_are_waiting_for_100_continue:
                        self.send_with_event(
                            h11.InformationalResponse(headers=[], status_code=100)
                        )
                    try:
                        self.c.receive_data(self.s.recv(MAX_INCOMPLETE_EVENT_SIZE))
                    except socket.timeout:
                        pass
                case h11.ConnectionClosed():
                    raise ConnectionClosed
                case _:
                    return event

    def send_with_event(self, event) -> None:
        data = self.c.send(event)
        assert data is not None
        self.s.sendall(data)
        debug_logger.debug("Sent event to %s:%d: %r", *self.peername, event)

    def read_request_body(self):
        event = self.get_next_event()
        match event:
            case h11.Data(data):
                return data
            case h11.EndOfMessage():
                return b""
            case _:
                return b""

    def start_response(
        self,
        status: str,
        headers: list[tuple[str, str]],
        exc_info: ExceptionInfo | None = None,
    ) -> Callable[[bytes], Any]:
        if exc_info is not None:
            try:
                if self.header_sent:
                    raise exc_info[1].with_traceback(exc_info[2])
            finally:
                del exc_info
        elif self.response_buffer is not None:
            raise RuntimeError("start_response() was already called")

        status_code, _ = status.split(" ", 1)
        if status_code.isdigit():
            status_code = int(status_code)
        else:
            raise RuntimeError(f"Invalid status: {status}")

        self.response_buffer = (
            status_code,
            [
                *(
                    (name.encode("latin1"), value.encode("latin1"))
                    for name, value in headers
                ),
                (b"Server", SERVER_NAME),
            ],
        )

        return self.s.sendall

    def init_environ(self) -> Environ:
        event = self.get_next_event()
        match event:
            case h11.Request(method, headers, target, http_version):
                request_uri = target.decode("latin-1")
                if "?" in request_uri:
                    path, query = request_uri.split("?", 1)
                else:
                    path, query = request_uri, ""

                server_name, server_port = self.sockname
                remote_name, remote_port = self.peername

                script_name = self.script_name
                if path == script_name:
                    path = ""
                else:
                    url_prefix_with_trailing_slash = script_name + "/"
                    if path.startswith(url_prefix_with_trailing_slash):
                        path = path[len(script_name) :]

                environ: Environ = {
                    "REQUEST_METHOD": method.decode("ascii"),
                    "SCRIPT_NAME": script_name,
                    "SERVER_NAME": server_name,
                    "SERVER_PORT": str(server_port),
                    "REMOTE_ADDR": remote_name,
                    "REMOTE_PORT": str(remote_port),
                    "REQUEST_URI": request_uri,
                    "PATH_INFO": path,
                    "QUERY_STRING": query,
                    "SERVER_PROTOCOL": f"HTTP/{http_version.decode('ascii')}",
                    "wsgi.version": (1, 0),
                    "wsgi.url_scheme": self.url_scheme,
                    "wsgi.input": Input(self.read_request_body),
                    "wsgi.errors": sys.stderr,
                    "wsgi.multithread": True,
                    "wsgi.multiprocess": True,
                    "wsgi.run_once": False,
                }

                for name, value in headers:
                    name = name.decode("latin1")
                    value = value.decode("latin1")
                    if name == "content-type":
                        environ["CONTENT_TYPE"] = value
                    elif name == "content-length":
                        environ["CONTENT_LENGTH"] = value
                    else:
                        http_name = "HTTP_" + name.upper().replace("-", "_")
                        if http_name not in environ:
                            environ[http_name] = value
                        else:
                            environ[http_name] += "," + value

                return environ  # type: ignore
            case _:
                raise RuntimeError(f"Unexpected event: {event}")

    def call_wsgi(self, wsgi_app: WSGIApp) -> None:
        environ = self.init_environ()
        iterable = None  # Just for finally block

        try:
            iterable = wsgi_app(environ, self.start_response)
            iterator = iter(iterable)

            chunk = next(iterator)
            if self.response_buffer is None:
                raise RuntimeError("start_response() was not called")

            status_code = self.response_buffer[0]
            self.send_with_event(
                h11.Response(status_code=status_code, headers=self.response_buffer[1])
            )

            log_http(environ, status_code)

            self.send_with_event(h11.Data(data=chunk))

            for chunk in iterator:
                self.send_with_event(h11.Data(data=chunk))

            self.send_with_event(h11.EndOfMessage())
        except Exception:
            if self.header_sent:
                raise

            self.send_with_event(
                h11.Response(
                    status_code=500,
                    headers=[
                        (b"Content-Type", b"text/plain; charset=utf-8"),
                        (b"Content-Length", b"21"),
                        (b"Server", SERVER_NAME),
                    ],
                )
            )
            self.send_with_event(h11.Data(data=b"Internal Server Error"))
            self.send_with_event(h11.EndOfMessage())

            error_logger.exception(
                "Error while calling WSGI application", exc_info=sys.exc_info()
            )
            log_http(environ, 500)
            raise
        finally:
            # Close the iterable if it has a close() method, per PEP 3333.
            close = getattr(iterable, "close", None)
            if callable(close):
                close()


MAX_INCOMPLETE_EVENT_SIZE = 16 * 1024


def http11_protocol(
    app: WSGIApp,
    sock: socket.socket,
    graceful_exit: threading.Event,
    *,
    url_scheme: str = "http",
    script_name: str = "",
) -> None:
    peername = sock.getpeername()
    if isinstance(peername, str):
        peername = (peername, 0)
    else:
        peername = peername[:2]
    sockname = sock.getsockname()
    if isinstance(sockname, str):
        sockname = (sockname, 0)
    else:
        sockname = sockname[:2]

    h11_connection = h11.Connection(
        our_role=h11.SERVER, max_incomplete_event_size=MAX_INCOMPLETE_EVENT_SIZE
    )
    h = H11Protocol(
        c=h11_connection,
        s=sock,
        peername=peername,
        sockname=sockname,
        graceful_exit=graceful_exit,
        url_scheme=url_scheme,
        script_name=script_name,
    )
    while not graceful_exit.is_set():
        try:
            h.call_wsgi(app)

            while True:
                event = h.get_next_event()
                match event:
                    case h11.EndOfMessage() | h11.PAUSED:
                        try:
                            h.c.start_next_cycle()
                        except h11.LocalProtocolError:
                            raise ConnectionClosed
                        h.response_buffer = None
                        debug_logger.debug("Start next cycle in %s:%d", *h.peername)
                        break
                    case h11.Data():  # unread request body
                        pass
                    case _:
                        raise RuntimeError(f"Unexpected event: {event}")
        except ConnectionClosed:
            debug_logger.debug("Connection closed by %s:%d", *h.peername)
            break
