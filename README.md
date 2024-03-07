# Zī Bái

> 中曲之山有兽焉，其状如马而白身黑尾，一角，虎牙爪，音如鼓音，其名曰駮，是食虎豹，可以御兵。

A modern high-performance pure-Python WSGI server. Can be launched using the command line or programmatically.

Correct handling of the HTTP protocol is ensured by [h11](https://github.com/python-hyper/h11). **Optional** [gevent](https://github.com/gevent/gevent).

- **Cross-platform multi-process management**. (You no longer have to worry about gunicorn not being available on Windows😀)
- Support IPv4, IPv6, Unix socket.
- Graceful restart. If code or configuration is updated, new workers will use them.
- Server event hooks. (If you want to do something extra at specific times 🙂)
- Clean and pure way of programming. Can be used any way you want.

Inspiration from [Uvicorn](https://github.com/encode/uvicorn), [GUnicorn](https://github.com/benoitc/gunicorn), [Waitress](https://github.com/Pylons/waitress), [runweb](https://github.com/abersheeran/runweb).

## Quick start

```bash
pip install zibai-server[gevent,reload]

# Then run your WSGI application like kui, django, flask, etc.
zibai example:app
```

Multiple processes:

```bash
zibai example:app -p 4
```

Auto reload in development:

```bash
zibai example:app --watchfiles "*.py;.env"
```

Use app factory:

```bash
zibai example:create_app --call
```

Use `--help` to see all available options.

```
usage: zibai [-h] [--call] [--listen LISTEN [LISTEN ...]] [--subprocess SUBPROCESS] [--no-gevent] [--max-workers MAX_WORKERS]
                   [--watchfiles WATCHFILES] [--backlog BACKLOG] [--dualstack-ipv6] [--unix-socket-perms UNIX_SOCKET_PERMS]
                   [--h11-max-incomplete-event-size H11_MAX_INCOMPLETE_EVENT_SIZE] [--max-request-pre-process MAX_REQUEST_PRE_PROCESS]
                   [--graceful-exit-timeout GRACEFUL_EXIT_TIMEOUT] [--url-scheme URL_SCHEME] [--url-prefix URL_PREFIX]
                   [--before-serve BEFORE_SERVE] [--before-graceful-exit BEFORE_GRACEFUL_EXIT] [--before-died BEFORE_DIED] [--no-access-log]
                   app

positional arguments:
  app                   WSGI app

options:
  -h, --help            show this help message and exit
  --call                use WSGI factory (default: False)
  --listen LISTEN [LISTEN ...], -l LISTEN [LISTEN ...]
                        listen address, HOST:PORT, unix:PATH (default: ['127.0.0.1:8000'])
  --subprocess SUBPROCESS, -p SUBPROCESS
                        number of subprocesses (default: 0)
  --no-gevent           do not use gevent (default: False)
  --max-workers MAX_WORKERS, -w MAX_WORKERS
                        maximum number of threads or greenlets to use for handling requests (default: 10)
  --watchfiles WATCHFILES
                        watch files for changes and restart workers (default: None)
  --backlog BACKLOG     listen backlog (default: None)
  --dualstack-ipv6      enable dualstack ipv6 (default: False)
  --unix-socket-perms UNIX_SOCKET_PERMS
                        unix socket permissions (default: 600)
  --h11-max-incomplete-event-size H11_MAX_INCOMPLETE_EVENT_SIZE
                        maximum number of bytes in an incomplete HTTP event (default: None)
  --max-request-pre-process MAX_REQUEST_PRE_PROCESS
                        maximum number of requests to process before killing the worker (default: None)
  --graceful-exit-timeout GRACEFUL_EXIT_TIMEOUT
                        graceful exit timeout (default: 10)
  --url-scheme URL_SCHEME
                        url scheme; will be passed to WSGI app as wsgi.url_scheme (default: http)
  --url-prefix URL_PREFIX
                        url prefix; will be passed to WSGI app as SCRIPT_NAME, if not specified, use environment variable SCRIPT_NAME (default:
                        None)
  --before-serve BEFORE_SERVE
                        callback to run before serving requests (default: None)
  --before-graceful-exit BEFORE_GRACEFUL_EXIT
                        callback to run before graceful exit (default: None)
  --before-died BEFORE_DIED
                        callback to run before exiting (default: None)
  --no-access-log       disable access log (default: False)
```

## Use programmatically

```python
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def app(environ, start_response):
    status = "200 OK"
    headers = [("Content-type", "text/plain; charset=utf-8"), ("Content-Length", "12")]
    start_response(status, headers)
    return [b"Hello World!"]


if __name__ == "__main__":
    import sys
    from zibai import parse_args, main

    options = parse_args(["example:app"] + sys.argv[1:])
    main(options)
```

`Options` consists of easily serializable types such as string, number, or None. So if you don't want to read and parse the configuration from the command line, you can also create `Options` yourself.

```python
from zibai import Options, main

options = Options(app="example:app")
main(options)
```

### Advanced usage

If `Options` cannot meet your customization needs, you can use the `serve` function directly.

```python
def app(environ, start_response):
    status = "200 OK"
    headers = [("Content-type", "text/plain; charset=utf-8"), ("Content-Length", "12")]
    start_response(status, headers)
    return [b"Hello World!"]


if __name__ == "__main__":
    import threading

    import zibai

    exit_event = threading.Event()

    zibai.serve(
        app=app,
        bind_socket=your_socket,
        backlog=None,
        max_workers=10,
        graceful_exit=exit_event,
        before_serve_hook=your_hook,
        before_graceful_exit_hook=your_hook,
        before_died_hook=your_hook,
    )
```

## Event hooks

The following hooks will be executed in each worker process:

- `before_serve` is called before serving requests.
- `before_graceful_exit` is called before graceful exit.
- `before_died` is called before exiting.

## Logging

Zī Bái uses the standard Python logging module. You can configure it as you like.

```python
# Process management, service startup or termination logs.
logger = logging.getLogger("zibai")
# Used for DEBUG http protocol errors, generally do not enable it.
debug_logger = logging.getLogger("zibai.debug")
# Access logs. Non-5xx type request logs will use this.
access_logger = logging.getLogger("zibai.access")
# Error logs. 5xx type request logs will use this.
error_logger = logging.getLogger("zibai.error")
```

You can configure the output format of `access_logger` and `error_logger` to access values in WSGI Environ.

```python
from zibai.logger import access_logger

formatter = logging.Formatter(
    "%(asctime)s [%(REMOTE_ADDR)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"
)
for handler in access_logger.handlers:
    handler.setFormatter(handler.formatter)
```

## Signals

Zī Bái will handle the following signals:

- `SIGINT`: Trigger quick exit (Just call `sys.exit(0)`). If subprocess is enabled, then the main process will wait for the subprocesses to exit quickly.
- `SIGTERM`: Trigger graceful exit. If subprocess is enabled, then the main process will wait for the subprocesses to exit gracefully.

There are also some signals that will only be processed by the main process when subprocess is enabled.

- `SIGBREAK`: Only available on Windows. Trigger graceful exit.
- `SIGHUP`: Work processeses are graceful restarted one after another. If you update the code, the new worker process will use the new code.
- `SIGTTIN`: Increase the number of worker processes by one.
- `SIGTTOU`: Decrease the number of worker processes by one.
