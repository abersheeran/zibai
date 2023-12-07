import os
import random

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
