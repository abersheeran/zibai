import os
import random
import socket
import threading
import time

import pytest
from zibai import create_bind_socket


def create_ipv4_socket():
    for _ in range(10):
        port = random.randint(20000, 60000)
        try:
            return create_bind_socket(f"127.0.0.1:{port}")
        except IOError:
            continue


def create_ipv6_socket():
    for _ in range(10):
        port = random.randint(20000, 60000)
        try:
            return create_bind_socket(f"::1:{port}")
        except IOError:
            continue


def create_unix_socket():
    path = "/tmp/test-zibai-server.sock"
    if os.path.exists(path):
        os.remove(path)
    return create_bind_socket(f"unix:{path}")


@pytest.fixture(params=[create_ipv4_socket, create_ipv6_socket, create_unix_socket])
def bind_socket(request):
    if request.param is create_unix_socket:
        if not hasattr(socket, "AF_UNIX"):
            pytest.skip("AF_UNIX is not supported")

    with request.param() as sock:
        yield sock


@pytest.fixture
def socket_and_event(bind_socket):
    exit_event = threading.Event()
    try:
        yield bind_socket, exit_event
    finally:
        exit_event.set()
        time.sleep(0.15)
