import socket
import threading
import time

import h2.connection
import h2.events

from zibai.core import serve


def streaming_echo_app(environ, start_response):
    start_response("200 OK", [])
    for chunk in environ["wsgi.input"]:
        if chunk:
            yield chunk
        else:
            return


def test_h2c_concurrent_streams(socket_and_event):
    bind_socket, exit_event = socket_and_event
    bind_socket.listen()

    server_thread = threading.Thread(
        target=serve,
        kwargs=dict(
            app=streaming_echo_app,
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

        # Open three streams interleaved
        s1, s2, s3 = conn.get_next_available_stream_id(), None, None
        conn.send_headers(s1, [(":method", "POST"), (":authority", "x"), (":scheme", "http"), (":path", "/")], end_stream=False)
        client_socket.sendall(conn.data_to_send())

        s2 = conn.get_next_available_stream_id()
        conn.send_headers(s2, [(":method", "POST"), (":authority", "x"), (":scheme", "http"), (":path", "/")], end_stream=False)
        client_socket.sendall(conn.data_to_send())

        # send partial bodies interleaved
        conn.send_data(s1, b"A" * 1024, end_stream=False)
        client_socket.sendall(conn.data_to_send())
        conn.send_data(s2, b"B" * 2048, end_stream=False)
        client_socket.sendall(conn.data_to_send())

        s3 = conn.get_next_available_stream_id()
        conn.send_headers(s3, [(":method", "POST"), (":authority", "x"), (":scheme", "http"), (":path", "/")], end_stream=False)
        client_socket.sendall(conn.data_to_send())
        conn.send_data(s3, b"C" * 512, end_stream=True)
        client_socket.sendall(conn.data_to_send())

        # finish s1 and s2
        conn.send_data(s1, b"A" * 512, end_stream=True)
        client_socket.sendall(conn.data_to_send())
        conn.send_data(s2, b"B" * 256, end_stream=True)
        client_socket.sendall(conn.data_to_send())

        results = {s1: b"", s2: b"", s3: b""}
        statuses = {}

        ended = set()
        while len(ended) < 3:
            data = client_socket.recv(65535)
            assert data
            events = conn.receive_data(data)
            for ev in events:
                if isinstance(ev, h2.events.ResponseReceived):
                    for n, v in ev.headers:
                        if n in (":status", b":status"):
                            statuses[ev.stream_id] = int(v if isinstance(v, str) else v.decode("ascii"))
                if isinstance(ev, h2.events.DataReceived):
                    results[ev.stream_id] += ev.data
                    conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                if isinstance(ev, h2.events.StreamEnded):
                    ended.add(ev.stream_id)
            client_socket.sendall(conn.data_to_send())

        assert statuses == {s1: 200, s2: 200, s3: 200}
        assert results[s1] == b"A" * (1024 + 512)
        assert results[s2] == b"B" * (2048 + 256)
        assert results[s3] == b"C" * 512

