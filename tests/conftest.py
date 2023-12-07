import socket
import threading

import pytest

from .utils import create_ipv4_socket, create_ipv6_socket, create_unix_socket


@pytest.fixture(params=[create_ipv4_socket, create_ipv6_socket, create_unix_socket])
def bind_socket(request):
    if request.param is create_unix_socket:
        if not hasattr(socket, "AF_UNIX"):
            pytest.skip("AF_UNIX is not supported")

    with request.param() as sock:
        yield sock


@pytest.fixture
def exit_event():
    exit_event = threading.Event()
    try:
        yield exit_event
    finally:
        exit_event.set()
