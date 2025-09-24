import socket
import sys
import threading
from typing import Any, Callable

import h2.connection
import h2.events
from h2.settings import SettingCodes
from h2.config import H2Configuration

from .const import SERVER_NAME
from .logger import debug_logger, error_logger, log_http
from .utils import Input
from .wsgi_typing import Environ, ExceptionInfo, WSGIApp


H2_CLIENT_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"


class ConnectionClosed(Exception):
    pass


class H2Protocol:
    def __init__(
        self,
        *,
        sock: socket.socket,
        peername: tuple[str, int],
        sockname: tuple[str, int],
        graceful_exit: threading.Event,
        url_scheme: str,
        script_name: str,
    ) -> None:
        self.s = sock
        self.peername = peername
        self.sockname = sockname
        self.graceful_exit = graceful_exit
        self.url_scheme = url_scheme
        self.script_name = script_name

        self.c = h2.connection.H2Connection(config=H2Configuration(client_side=False))
        self.send_lock = threading.Lock()

    def _send_outbound(self) -> None:
        with self.send_lock:
            data = self.c.data_to_send()
            if data:
                self.s.sendall(data)

    # No explicit preface discard: pass the preface to h2 via receive_data

    def _build_environ(self, headers: list[tuple[bytes, bytes]]) -> Environ:
        method = "GET"
        path = "/"
        query = ""
        http_scheme = self.url_scheme

        server_name, server_port = self.sockname
        remote_name, remote_port = self.peername

        environ: Environ = {
            "REQUEST_METHOD": method,
            "SCRIPT_NAME": self.script_name,
            "SERVER_NAME": server_name,
            "SERVER_PORT": str(server_port),
            "REMOTE_ADDR": remote_name,
            "REMOTE_PORT": str(remote_port),
            "REQUEST_URI": "",
            "PATH_INFO": "",
            "QUERY_STRING": "",
            "SERVER_PROTOCOL": "HTTP/2.0",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": http_scheme,
            # wsgi.input filled later
            "wsgi.input": Input(lambda: b""),
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": True,
            "wsgi.multiprocess": True,
            "wsgi.run_once": False,
        }

        for name, value in headers:
            n = name.decode("latin1")
            v = value.decode("latin1")
            if n == ":method":
                method = v
                environ["REQUEST_METHOD"] = method
            elif n == ":path":
                if "?" in v:
                    path, query = v.split("?", 1)
                else:
                    path, query = v, ""
                environ["REQUEST_URI"] = v
            elif n == ":scheme":
                http_scheme = v
                environ["wsgi.url_scheme"] = http_scheme
            elif n == "content-type":
                environ["CONTENT_TYPE"] = v
            elif n == "content-length":
                environ["CONTENT_LENGTH"] = v
            else:
                if not n.startswith(":"):
                    http_name = "HTTP_" + n.upper().replace("-", "_")
                    if http_name not in environ:
                        environ[http_name] = v
                    else:
                        environ[http_name] += "," + v

        # SCRIPT_NAME and PATH_INFO handling
        environ["SCRIPT_NAME"] = self.script_name
        if path == self.script_name:
            path_info = ""
        else:
            url_prefix_with_trailing_slash = self.script_name + "/"
            if path.startswith(url_prefix_with_trailing_slash):
                path_info = path[len(self.script_name) :]
            else:
                path_info = path
        environ["PATH_INFO"] = path_info
        environ["QUERY_STRING"] = query

        return environ  # type: ignore

    def _start_response_factory(
        self, stream_id: int
    ) -> Callable[[str, list[tuple[str, str]], ExceptionInfo | None], Callable[[bytes], Any]]:
        header_sent = {"value": False}
        response_buffer: dict[str, Any] = {"status": 200, "headers": []}

        def start_response(
            status: str,
            headers: list[tuple[str, str]],
            exc_info: ExceptionInfo | None = None,
        ) -> Callable[[bytes], Any]:
            if exc_info is not None and header_sent["value"]:
                raise exc_info[1].with_traceback(exc_info[2])
            if header_sent["value"]:
                raise RuntimeError("start_response() was already called")

            status_code_str, _ = status.split(" ", 1)
            if not status_code_str.isdigit():
                raise RuntimeError(f"Invalid status: {status}")
            response_buffer["status"] = int(status_code_str)

            # Normalize headers to lowercase, encode to bytes, add server
            norm_headers = [
                (k.lower().encode("latin1"), v.encode("latin1")) for k, v in headers
            ]
            norm_headers.append((b"server", SERVER_NAME))
            response_buffer["headers"] = norm_headers

            def write(chunk: bytes) -> None:
                # This write callable is rarely used; we'll send in the response loop.
                self._send_data_with_flow_control(stream_id, chunk, end_stream=False)

            return write

        def _send_headers_if_needed() -> None:
            if header_sent["value"]:
                return
            # Build HTTP/2 headers
            headers = [(b":status", str(response_buffer["status"]).encode("ascii"))]
            for k, v in response_buffer["headers"]:
                # Exclude hop-by-hop headers automatically ignored in h2
                if k in {b"connection", b"transfer-encoding"}:
                    continue
                headers.append((k, v))
            # debug
            print('[h2] sending headers', headers)
            with self.send_lock:
                self.c.send_headers(stream_id, headers, end_stream=False)
                data_to_send = self.c.data_to_send()
                if data_to_send:
                    self.s.sendall(data_to_send)
            header_sent["value"] = True

        # Attach helper to the instance for later use within call_wsgi
        start_response._send_headers_if_needed = _send_headers_if_needed  # type: ignore[attr-defined]
        start_response._response_buffer = response_buffer  # type: ignore[attr-defined]
        start_response._header_sent = header_sent  # type: ignore[attr-defined]
        return start_response

    def _send_data_with_flow_control(
        self, stream_id: int, data: bytes, *, end_stream: bool
    ) -> None:
        idx = 0
        while idx < len(data) or (len(data) == 0 and end_stream):
            window = min(
                self.c.local_flow_control_window(stream_id),
                self.c.max_outbound_frame_size,
            )
            if window <= 0:
                # Wait for window update by reading peer frames
                self._recv_and_handle_events(block_until_window_for=stream_id)
                continue

            to_send = data[idx : idx + window]
            idx += len(to_send)
            # Only mark end_stream if this is the last piece
            with self.send_lock:
                self.c.send_data(stream_id, to_send, end_stream=end_stream and idx >= len(data))
                outbound = self.c.data_to_send()
                if outbound:
                    self.s.sendall(outbound)
            if len(to_send) == 0:
                # End stream with empty data
                break

    def _recv_and_handle_events(self, *, block_until_window_for: int | None = None) -> None:
        # Minimal event handling to advance flow control; does not process new requests here.
        try:
            data = self.s.recv(65535)
        except socket.timeout:
            return
        if not data:
            raise ConnectionClosed
        for event in self.c.receive_data(data):
            debug_logger.debug("[h2] recv event from %s:%d: %r", *self.peername, event)
            if isinstance(event, h2.events.DataReceived):
                # Acknowledge any inbound data to release connection window
                self.c.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
            # Send any pending ACKs or settings
        self._send_outbound()

    def call_wsgi_on_stream(
        self,
        wsgi_app: WSGIApp,
        stream_id: int,
        headers: list[tuple[bytes, bytes]],
        *,
        initial_events: list[Any] | None = None,
    ) -> None:
        # Lazily read request body when the app asks for it
        pending_eom = {"value": False}

        def prime_events(evts: list[Any]) -> None:
            for event in evts:
                if isinstance(event, h2.events.DataReceived) and event.stream_id == stream_id:
                    # Stash into a simple buffer the next call will use
                    data_buffer.extend(event.data)
                    self.c.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                if isinstance(event, h2.events.StreamEnded) and event.stream_id == stream_id:
                    pending_eom["value"] = True
            self._send_outbound()

        data_buffer = bytearray()
        if initial_events:
            prime_events(initial_events)

        def receive_body() -> bytes:
            if pending_eom["value"] and not data_buffer:
                return b""
            if data_buffer:
                chunk = bytes(data_buffer)
                data_buffer.clear()
                return chunk
            while True:
                try:
                    data = self.s.recv(65535)
                except socket.timeout:
                    return b""
                if not data:
                    pending_eom["value"] = True
                    return b""
                for event in self.c.receive_data(data):
                    if isinstance(event, h2.events.DataReceived) and event.stream_id == stream_id:
                        self.c.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                        self._send_outbound()
                        return event.data
                    if isinstance(event, h2.events.StreamEnded) and event.stream_id == stream_id:
                        pending_eom["value"] = True
                        self._send_outbound()
                        return b""
                self._send_outbound()

        environ = self._build_environ(headers)
        environ["wsgi.input"] = Input(receive_body)

        start_response = self._start_response_factory(stream_id)
        iterable = None
        try:
            iterable = wsgi_app(environ, start_response)
            iterator = iter(iterable)

            try:
                first_chunk = next(iterator)
            except StopIteration:
                first_chunk = b""

            # Ensure headers are sent after start_response is called by the app
            start_response._send_headers_if_needed()  # type: ignore[attr-defined]

            status_code = start_response._response_buffer["status"]  # type: ignore[attr-defined]
            log_http(environ, int(status_code))

            if first_chunk:
                print('[h2] sending first chunk', len(first_chunk))
                self._send_data_with_flow_control(stream_id, first_chunk, end_stream=False)

            for chunk in iterator:
                if not chunk:
                    continue
                print('[h2] sending chunk', len(chunk))
                self._send_data_with_flow_control(stream_id, chunk, end_stream=False)

            # End stream
            print('[h2] end stream')
            self._send_data_with_flow_control(stream_id, b"", end_stream=True)
        except BaseException:
            error_logger.exception("Error while calling WSGI application", exc_info=sys.exc_info())
            # Send 500 if headers not sent
            headers = [
                (b":status", b"500"),
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", b"21"),
                (b"server", SERVER_NAME),
            ]
            try:
                self.c.send_headers(stream_id, headers, end_stream=False)
                self._send_data_with_flow_control(stream_id, b"Internal Server Error", end_stream=True)
                self._send_outbound()
            except Exception:
                pass
            log_http(environ, 500)
            raise
        finally:
            close = getattr(iterable, "close", None)
            if callable(close):
                close()


def http2_protocol(
    app: WSGIApp,
    sock: socket.socket,
    graceful_exit: threading.Event,
    *,
    url_scheme: str = "http",
    script_name: str = "",
) -> None:
    # Enter HTTP/2 (h2c) handler
    try:
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

        h = H2Protocol(
            sock=sock,
            peername=peername,
            sockname=sockname,
            graceful_exit=graceful_exit,
            url_scheme=url_scheme,
            script_name=script_name,
        )
    except Exception:
        import traceback
        traceback.print_exc()
        return

    # Send our initial SETTINGS; we'll pass the client's preface to receive_data
    h.c.initiate_connection()
    # Restrict to one concurrent stream to simplify processing
    h.c.update_settings({SettingCodes.MAX_CONCURRENT_STREAMS: 1})
    h._send_outbound()

    sock.settimeout(1)
    while not graceful_exit.is_set():
        try:
            data = sock.recv(65535)
            if not data:
                raise ConnectionClosed
            events = h.c.receive_data(data)
            # Group events per stream for any newly received requests.
            stream_order: list[int] = []
            stream_headers: dict[int, list[tuple[bytes, bytes]]] = {}
            stream_evmap: dict[int, list[Any]] = {}
            for ev in events:
                debug_logger.debug("[h2] recv event from %s:%d: %r", *h.peername, ev)
                if isinstance(ev, h2.events.RequestReceived):
                    stream_id = ev.stream_id
                    stream_order.append(stream_id)
                    stream_headers[stream_id] = ev.headers  # type: ignore[assignment]
                    stream_evmap.setdefault(stream_id, [])
                elif isinstance(ev, (h2.events.DataReceived, h2.events.StreamEnded)):
                    stream_evmap.setdefault(ev.stream_id, []).append(ev)
                elif isinstance(ev, h2.events.ConnectionTerminated):
                    raise ConnectionClosed
                elif isinstance(ev, h2.events.RemoteSettingsChanged):
                    # ignore
                    pass
                elif isinstance(ev, h2.events.SettingsAcknowledged):
                    # ignore
                    pass
                else:
                    # ignore other events
                    pass

            # Serve each new request detected in this batch synchronously.
            for sid in stream_order:
                h.call_wsgi_on_stream(app, sid, stream_headers[sid], initial_events=stream_evmap.get(sid, []))  # type: ignore[arg-type]

            h._send_outbound()
        except socket.timeout:
            continue
        except (ConnectionClosed, ConnectionError, OSError):
            debug_logger.debug("[h2] Connection closed by %s:%d", *h.peername)
            break
        except Exception:  # pragma: no cover
            import traceback

            traceback.print_exc()
            break

