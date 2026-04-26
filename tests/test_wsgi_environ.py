import socket
import threading
import time

import h11

from zibai.core import serve


def script_app(environ, start_response):
	status = "200 OK"
	headers = [("Content-Type", "text/plain; charset=utf-8")]
	start_response(status, headers)
	body = f"SCRIPT_NAME={environ['SCRIPT_NAME']},PATH_INFO={environ['PATH_INFO']}".encode()
	return [body]


def _client_request(bind_socket, target):
	client_socket = socket.socket(bind_socket.family, bind_socket.type, bind_socket.proto)
	client_socket.connect(bind_socket.getsockname())
	with client_socket:
		conn = h11.Connection(h11.CLIENT)
		client_socket.sendall(conn.send(h11.Request(method="GET", target=target, headers=[("Host", "example.com")])) )
		data = client_socket.recv(4096)
		conn.receive_data(data)
		resp = conn.next_event()
		assert isinstance(resp, h11.Response) and resp.status_code == 200
		body = b""
		while True:
			evt = conn.next_event()
			if evt is h11.NEED_DATA:
				chunk = client_socket.recv(4096)
				conn.receive_data(chunk)
				continue
			if isinstance(evt, h11.Data):
				body += evt.data
				continue
			if isinstance(evt, h11.EndOfMessage):
				break
		return body


def test_wsgi_script_name_root(socket_and_event):
	bind_socket, exit_event = socket_and_event
	bind_socket.listen()
	server_thread = threading.Thread(
		target=serve,
		kwargs=dict(
			app=script_app,
			bind_sockets=[bind_socket],
			max_workers=10,
			graceful_exit=exit_event,
			script_name="",
			keepalive_timeout=1.0,
		),
		daemon=True,
	)
	server_thread.start()
	time.sleep(0.2)
	body = _client_request(bind_socket, "/foo/bar?x=1")
	assert body == b"SCRIPT_NAME=,PATH_INFO=/foo/bar"


def test_wsgi_script_name_prefix(socket_and_event):
	bind_socket, exit_event = socket_and_event
	bind_socket.listen()
	server_thread = threading.Thread(
		target=serve,
		kwargs=dict(
			app=script_app,
			bind_sockets=[bind_socket],
			max_workers=10,
			graceful_exit=exit_event,
			script_name="/api",
			keepalive_timeout=1.0,
		),
		daemon=True,
	)
	server_thread.start()
	time.sleep(0.2)
	# exact prefix
	body = _client_request(bind_socket, "/api")
	assert body == b"SCRIPT_NAME=/api,PATH_INFO="
	# nested path
	body2 = _client_request(bind_socket, "/api/v1/items")
	assert body2 == b"SCRIPT_NAME=/api,PATH_INFO=/v1/items"