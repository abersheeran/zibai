import socket
import threading
import time

import h2.connection
import h2.events
import pytest

from zibai.core import serve


def hello_world_app(environ, start_response):
    start_response(
        "200 OK",
        [
            ("Content-type", "text/plain; charset=utf-8"),
            ("Content-Length", "12"),
        ],
    )
    return [b"Hello World!"]


def echo_app(environ, start_response):
    start_response("200 OK", [])
    # Stream back whatever the client sends
    for chunk in environ["wsgi.input"]:
        if chunk:
            yield chunk
        else:
            return


@pytest.mark.parametrize("backlog", [10, None])
def test_h2c_hello_world(
    socket_and_event: tuple[socket.socket, threading.Event], backlog: int | None
) -> None:
    bind_socket, exit_event = socket_and_event
    if backlog is None:
        bind_socket.listen()
    else:
        bind_socket.listen(backlog)

    server_thread = threading.Thread(
        target=serve,
        kwargs=dict(
            app=hello_world_app,
            bind_sockets=[bind_socket],
            max_workers=10,
            graceful_exit=exit_event,
        ),
        daemon=True,
    )
    server_thread.start()

    time.sleep(1)

    client_socket = socket.socket(
        bind_socket.family, bind_socket.type, bind_socket.proto
    )
    client_socket.connect(bind_socket.getsockname())
    with client_socket:
        conn = h2.connection.H2Connection()
        conn.initiate_connection()
        # Send client connection preface and initial settings
        data = conn.data_to_send()
        assert data.startswith(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")
        client_socket.sendall(data)

        # Open a stream with GET /
        stream_id = conn.get_next_available_stream_id()
        conn.send_headers(
            stream_id,
            [
                (":method", "GET"),
                (":authority", "example.com"),
                (":scheme", "http"),
                (":path", "/"),
            ],
            end_stream=True,
        )
        client_socket.sendall(conn.data_to_send())

        # Receive response
        body = b""
        status = None
        while True:
            data = client_socket.recv(65535)
            assert data
            events = conn.receive_data(data)
            for event in events:
                if isinstance(event, h2.events.ResponseReceived):
                    for n, v in event.headers:
                        if n in (":status", b":status"):
                            if isinstance(v, bytes):
                                status = int(v.decode("ascii"))
                            else:
                                status = int(v)
                if isinstance(event, h2.events.DataReceived):
                    body += event.data
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                if isinstance(event, h2.events.StreamEnded):
                    break
            client_socket.sendall(conn.data_to_send())
            if any(isinstance(e, h2.events.StreamEnded) for e in events):
                break

        assert status == 200
        assert body == b"Hello World!"


def test_h2c_post_echo(socket_and_event: tuple[socket.socket, threading.Event]) -> None:
    bind_socket, exit_event = socket_and_event
    bind_socket.listen()

    server_thread = threading.Thread(
        target=serve,
        kwargs=dict(
            app=echo_app,
            bind_sockets=[bind_socket],
            max_workers=10,
            graceful_exit=exit_event,
        ),
        daemon=True,
    )
    server_thread.start()

    time.sleep(1)

    client_socket = socket.socket(
        bind_socket.family, bind_socket.type, bind_socket.proto
    )
    client_socket.connect(bind_socket.getsockname())
    with client_socket:
        conn = h2.connection.H2Connection()
        conn.initiate_connection()
        client_socket.sendall(conn.data_to_send())

        stream_id = conn.get_next_available_stream_id()
        conn.send_headers(
            stream_id,
            [
                (":method", "POST"),
                (":authority", "example.com"),
                (":scheme", "http"),
                (":path", "/echo"),
                ("content-type", "text/plain"),
            ],
            end_stream=False,
        )
        payload = b"chunk1-" + b"x" * 1024 + b"-chunk2"
        # Send request body possibly split by flow control
        idx = 0
        while idx < len(payload):
            window = min(
                conn.local_flow_control_window(stream_id), conn.max_outbound_frame_size
            )
            if window == 0:
                # Need to read WINDOW_UPDATE
                data = client_socket.recv(65535)
                assert data
                events = conn.receive_data(data)
                client_socket.sendall(conn.data_to_send())
                continue
            to_send = payload[idx : idx + window]
            idx += len(to_send)
            conn.send_data(stream_id, to_send, end_stream=False)
            client_socket.sendall(conn.data_to_send())

        conn.end_stream(stream_id)
        client_socket.sendall(conn.data_to_send())

        # Receive echoed body
        received = b""
        status = None
        while True:
            data = client_socket.recv(65535)
            assert data
            events = conn.receive_data(data)
            for event in events:
                if isinstance(event, h2.events.ResponseReceived):
                    for n, v in event.headers:
                        if n in (":status", b":status"):
                            if isinstance(v, bytes):
                                status = int(v.decode("ascii"))
                            else:
                                status = int(v)
                if isinstance(event, h2.events.DataReceived):
                    received += event.data
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                if isinstance(event, h2.events.StreamEnded):
                    break
            client_socket.sendall(conn.data_to_send())
            if any(isinstance(e, h2.events.StreamEnded) for e in events):
                break

        assert status == 200
        assert received == payload

