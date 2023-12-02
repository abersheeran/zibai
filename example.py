import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def app(environ, start_response):
    status = "200 OK"
    headers = [("Content-type", "text/plain; charset=utf-8"), ("Content-Length", "12")]
    start_response(status, headers)
    return [b"Hello World!"]


if __name__ == "__main__":
    import sys
    from zibai.cli import parse_args, main

    options = parse_args(["example:app"] + sys.argv[1:])
    main(options)
