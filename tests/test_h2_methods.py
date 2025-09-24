import socket
import threading
import time

import h2.connection
import h2.events
import pytest

from zibai.core import serve


def method_app(environ, start_response):
    method = environ["REQUEST_METHOD"].upper()
    if method == "HEAD":
        start_response("200 OK", [("Content-Length", "12")])
        return [b""]
    if method == "OPTIONS":
        start_response(
            "204 No Content",
            [("Allow", "GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS"), ("Content-Length", "0")],
        )
        return [b""]
    if method in {"GET", "DELETE"}:
        start_response("200 OK", [("Content-Length", "0")])
        return [b""]
    if method in {"POST", "PUT", "PATCH"}:
        # echo
        start_response("200 OK", [])
        for chunk in environ["wsgi.input"]:
            if chunk:
                yield chunk
            else:
                return
    start_response("405 Method Not Allowed", [("Content-Length", "0")])
    return [b""]


@pytest.mark.parametrize("method,has_body,status", [
    ("GET", False, 200),
    ("HEAD", False, 200),
    ("OPTIONS", False, 204),
    ("DELETE", False, 200),
    ("POST", True, 200),
    ("PUT", True, 200),
    ("PATCH", True, 200),
])
def test_h2c_methods(socket_and_event, method, has_body, status):
    bind_socket, exit_event = socket_and_event
    bind_socket.listen()

    server_thread = threading.Thread(
        target=serve,
        kwargs=dict(
            app=method_app,
            bind_sockets=[bind_socket],
            max_workers=10,
            graceful_exit=exit_event,
        ),
        daemon=True,
    )
    server_thread.start()
    time.sleep(0.5)

    client_socket = socket.socket(bind_socket.family, bind_socket.type, bind_socket.proto)
    client_socket.connect(bind_socket.getsockname())
    with client_socket:
        conn = h2.connection.H2Connection()
        conn.initiate_connection()
        client_socket.sendall(conn.data_to_send())

        stream_id = conn.get_next_available_stream_id()
        headers = [
            (":method", method),
            (":authority", "example.com"),
            (":scheme", "http"),
            (":path", "/"),
        ]
        conn.send_headers(stream_id, headers, end_stream=not has_body)
        client_socket.sendall(conn.data_to_send())

        if has_body:
            body = b"abc123"
            conn.send_data(stream_id, body, end_stream=True)
            client_socket.sendall(conn.data_to_send())

        received = b""
        got_status = None
        while True:
            data = client_socket.recv(65535)
            assert data
            events = conn.receive_data(data)
            for event in events:
                if isinstance(event, h2.events.ResponseReceived):
                    for n, v in event.headers:
                        if n in (":status", b":status"):
                            got_status = int(v if isinstance(v, str) else v.decode("ascii"))
                if isinstance(event, h2.events.DataReceived):
                    received += event.data
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                if isinstance(event, h2.events.StreamEnded):
                    break
            client_socket.sendall(conn.data_to_send())
            if any(isinstance(e, h2.events.StreamEnded) for e in events):
                break

        assert got_status == status
        if has_body:
            assert received == body
        else:
            # For HEAD/GET/DELETE/OPTIONS no body expected per app
            assert received in (b"", b"")

