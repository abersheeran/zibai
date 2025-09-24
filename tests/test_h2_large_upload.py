import os
import socket
import threading
import time

import h2.connection
import h2.events

from zibai.core import serve


def sink_app(environ, start_response):
    # Consume all bytes, then respond with length via header
    total = 0
    for chunk in environ["wsgi.input"]:
        if not chunk:
            break
        total += len(chunk)
    start_response("200 OK", [("X-Total-Length", str(total)), ("Content-Length", "0")])
    return [b""]


def test_h2c_large_upload(socket_and_event):
    bind_socket, exit_event = socket_and_event
    bind_socket.listen()

    server_thread = threading.Thread(
        target=serve,
        kwargs=dict(
            app=sink_app,
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
        conn.send_headers(stream_id, [(":method", "POST"), (":authority", "x"), (":scheme", "http"), (":path", "/")], end_stream=False)
        client_socket.sendall(conn.data_to_send())

        size = 16 * 1024 * 1024
        chunk = os.urandom(64 * 1024)
        sent = 0
        while sent < size:
            window = min(conn.local_flow_control_window(stream_id), conn.max_outbound_frame_size)
            if window == 0:
                data = client_socket.recv(65535)
                assert data
                events = conn.receive_data(data)
                client_socket.sendall(conn.data_to_send())
                continue
            to_send = chunk[:window]
            conn.send_data(stream_id, to_send, end_stream=False)
            client_socket.sendall(conn.data_to_send())
            sent += len(to_send)

        conn.end_stream(stream_id)
        client_socket.sendall(conn.data_to_send())

        got_status = None
        got_total = None
        while True:
            data = client_socket.recv(65535)
            assert data
            events = conn.receive_data(data)
            for ev in events:
                if isinstance(ev, h2.events.ResponseReceived):
                    for n, v in ev.headers:
                        if n in (":status", b":status"):
                            got_status = int(v if isinstance(v, str) else v.decode("ascii"))
                        if n in ("x-total-length", b"x-total-length"):
                            got_total = int(v if isinstance(v, str) else v.decode("ascii"))
                if isinstance(ev, h2.events.StreamEnded):
                    break
            client_socket.sendall(conn.data_to_send())
            if any(isinstance(e, h2.events.StreamEnded) for e in events):
                break

        assert got_status == 200
        assert got_total == size

