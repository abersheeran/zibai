import socket
import threading
import time

import h11
import pytest
from zibai import serve


def hello_world_app(environ, start_response):
    start_response(
        "200 OK",
        [
            ("Content-type", "text/plain; charset=utf-8"),
            ("Content-Length", "12"),
        ],
    )
    return [b"Hello World!"]


@pytest.mark.parametrize("backlog", [10, None])
def test_hello_world_app(
    socket_and_event: tuple[socket.socket, threading.Event],
    backlog: int | None,
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
        client_connection = h11.Connection(h11.CLIENT)
        data = client_connection.send(
            h11.Request(method="GET", target="/", headers=[("Host", "example.com")])
        )
        assert data is not None
        client_socket.sendall(data)
        data = client_socket.recv(4096)
        client_connection.receive_data(data)
        event = client_connection.next_event()
        assert isinstance(event, h11.Response)
        assert event.status_code == 200
        assert event.headers == [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", b"12"),
            (b"server", "Zî Bái".encode("latin-1")),
        ]
