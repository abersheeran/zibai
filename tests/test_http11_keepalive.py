import socket
import threading
import time

import h11
import pytest

from zibai.core import serve


def simple_app(environ, start_response):
	status = "200 OK"
	headers = [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", "2")]
	start_response(status, headers)
	return [b"OK"]


def expect_app(environ, start_response):
	# consume body if any
	_ = environ["wsgi.input"].read()
	start_response("200 OK", [("Content-Length", "0")])
	return [b""]


@pytest.mark.parametrize("backlog", [10, None])
@pytest.mark.parametrize("keepalive_timeout", [0.5, 1.0])
def test_http11_keepalive_timeout(socket_and_event, backlog, keepalive_timeout):
	bind_socket, exit_event = socket_and_event
	if backlog is None:
		bind_socket.listen()
	else:
		bind_socket.listen(backlog)

	server_thread = threading.Thread(
		target=serve,
		kwargs=dict(
			app=simple_app,
			bind_sockets=[bind_socket],
			max_workers=10,
			graceful_exit=exit_event,
			keepalive_timeout=keepalive_timeout,
		),
		daemon=True,
	)
	server_thread.start()

	time.sleep(0.2)

	client_socket = socket.socket(bind_socket.family, bind_socket.type, bind_socket.proto)
	client_socket.connect(bind_socket.getsockname())
	with client_socket:
		conn = h11.Connection(h11.CLIENT)
		# First request
		data = conn.send(h11.Request(method="GET", target="/", headers=[("Host", "example.com")]))
		client_socket.sendall(data)
		data = client_socket.recv(4096)
		conn.receive_data(data)
		# Read response headers
		evt = conn.next_event()
		assert isinstance(evt, h11.Response)
		# Drain body and EOM so the server enters IDLE keepalive state
		body_received = False
		while True:
			evt = conn.next_event()
			if evt is h11.NEED_DATA:
				chunk = client_socket.recv(4096)
				conn.receive_data(chunk)
				continue
			if isinstance(evt, h11.Data):
				body_received = True
				continue
			if isinstance(evt, h11.EndOfMessage):
				break
		# Ensure we did receive the body
		assert body_received
		# Idle beyond keepalive: expect close
		time.sleep(keepalive_timeout + 0.3)
		# The server may have closed; any recv should return b"" or raise
		try:
			client_socket.settimeout(0.5)
			data = client_socket.recv(1)
			assert data == b""
		except socket.timeout:
			pytest.fail("connection not closed after keepalive timeout")


@pytest.mark.parametrize("backlog", [10])
def test_http11_expect_100_continue(socket_and_event, backlog):
	bind_socket, exit_event = socket_and_event
	bind_socket.listen(backlog)

	server_thread = threading.Thread(
		target=serve,
		kwargs=dict(
			app=expect_app,
			bind_sockets=[bind_socket],
			max_workers=10,
			graceful_exit=exit_event,
			keepalive_timeout=1.0,
		),
		daemon=True,
	)
	server_thread.start()

	time.sleep(0.2)

	client_socket = socket.socket(bind_socket.family, bind_socket.type, bind_socket.proto)
	client_socket.connect(bind_socket.getsockname())
	with client_socket:
		conn = h11.Connection(h11.CLIENT)
		headers = [("Host", "example.com"), ("Expect", "100-continue"), ("Content-Length", "4")]
		client_socket.sendall(conn.send(h11.Request(method="POST", target="/", headers=headers)))
		# We expect informational 100 Continue from server
		resp = client_socket.recv(4096)
		conn.receive_data(resp)
		event = conn.next_event()
		assert isinstance(event, h11.InformationalResponse) and event.status_code == 100
		# Now send body
		client_socket.sendall(conn.send(h11.Data(data=b"TEST")))
		client_socket.sendall(conn.send(h11.EndOfMessage()))
		# Read final response
		resp2 = client_socket.recv(4096)
		conn.receive_data(resp2)
		final = conn.next_event()
		assert isinstance(final, h11.Response) and final.status_code == 200