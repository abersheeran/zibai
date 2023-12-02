import dataclasses
import socket
import sys
import threading
from typing import Any, Callable

import h11

from .utils import Input
from .logger import debug_logger, log_http, error_logger
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

    url_scheme: str = "http"
    script_name: str = ""

    # State variables
    response_buffer: tuple[str, list[tuple[str, str]]] | None = None
    header_sent: bool = False

    def get_next_event(self):
        while self.c.their_state is not h11.DONE:
            event = self.c.next_event()
            debug_logger.debug("Received event from %s:%d: %r", *self.peername, event)

            match event:
                case h11.NEED_DATA:
                    if self.c.they_are_waiting_for_100_continue:
                        self.send_with_event(
                            h11.InformationalResponse(headers=[], status_code=100)
                        )
                    self.c.receive_data(self.s.recv(MAX_INCOMPLETE_EVENT_SIZE))
                case h11.ConnectionClosed():
                    debug_logger.debug("Connection closed by %s:%d", *self.peername)
                    raise ConnectionClosed
                case _:
                    return event

        return h11.PAUSED

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
        self.response_buffer = (status_code, headers)

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
                    if name == "content-type":
                        environ["CONTENT_TYPE"] = value.decode("latin1")
                    elif name == "content-length":
                        environ["CONTENT_LENGTH"] = value.decode("latin1")
                    else:
                        environ[
                            "HTTP_" + name.upper().replace("-", "_")
                        ] = value.decode("latin1")

                remote_name, remote_port = self.peername
                environ["REMOTE_ADDR"] = remote_name
                environ["REMOTE_PORT"] = str(remote_port)

                return environ
            case _:
                raise RuntimeError(f"Unexpected event: {event}")

    def call_wsgi(self, wsgi_app: WSGIApp) -> None:
        environ = self.init_environ()
        try:
            iterable = wsgi_app(environ, self.start_response)
        except Exception:
            self.start_response(
                "500 Internal Server Error",
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", "21"),
                ],
            )
            self.send_with_event(h11.Data(data=b"Internal Server Error"))
            self.send_with_event(h11.EndOfMessage())

            error_logger.exception(
                "Error while calling WSGI application", exc_info=sys.exc_info()
            )
            log_http(environ, 500)
            raise

        try:
            iterator = iter(iterable)

            chunk = next(iterator)
            if self.response_buffer is None:
                raise RuntimeError("start_response() was not called")

            status_code = int(self.response_buffer[0].split(" ", 1)[0])
            self.send_with_event(
                h11.Response(status_code=status_code, headers=self.response_buffer[1])
            )
            self.header_sent = True

            log_http(environ, status_code)

            self.send_with_event(h11.Data(data=chunk))

            for chunk in iterator:
                self.send_with_event(h11.Data(data=chunk))

            self.send_with_event(h11.EndOfMessage())
        except Exception:
            if self.header_sent:
                raise

            self.start_response(
                "500 Internal Server Error",
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", "21"),
                ],
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
    peername = sock.getpeername()[:2]
    if isinstance(peername, str):
        peername = (peername, 0)
    sockname = sock.getsockname()[:2]
    if isinstance(sockname, str):
        sockname = (sockname, 0)

    h11_connection = h11.Connection(
        our_role=h11.SERVER, max_incomplete_event_size=MAX_INCOMPLETE_EVENT_SIZE
    )
    h = H11Protocol(
        c=h11_connection,
        s=sock,
        peername=peername,
        sockname=sockname,
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
                        h.c.start_next_cycle()
                        h.response_buffer = None
                        h.header_sent = False
                        debug_logger.debug("Start next cycle in %s:%d", *h.peername)
                        break
                    case h11.Data():  # unread request body
                        pass
                    case _:
                        raise RuntimeError(f"Unexpected event: {event}")
        except ConnectionClosed:
            debug_logger.debug("Connection closed by %s:%d", *h.peername)
            break
