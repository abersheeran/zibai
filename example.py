import logging

logger = logging.getLogger(__name__)


def app(environ, start_response):
    match environ["REQUEST_METHOD"]:
        case "POST":
            status = "200 OK"
            headers = []
            start_response(status, headers)
            for chunk in environ["wsgi.input"]:
                if chunk:
                    yield chunk
                else:
                    return
        case _:
            status = "200 OK"
            headers = [
                ("Content-type", "text/plain; charset=utf-8"),
                ("Content-Length", "12"),
            ]
            start_response(status, headers)
            yield b"Hello World!"


def before_serve_hook():
    logger.info("Starting server...")


def before_graceful_exit_hook():
    logger.info("Graceful exit...")


def before_died_hook():
    logger.info("Died...")


if __name__ == "__main__":
    import sys

    from zibai.cli import main, parse_args

    options = parse_args(["example:app"] + sys.argv[1:])
    main(options)
