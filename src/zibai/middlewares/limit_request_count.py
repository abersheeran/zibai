import dataclasses
import signal

from ..wsgi_typing import Environ, IterableChunks, StartResponse, WSGIApp


@dataclasses.dataclass
class LimitRequestCountMiddleware:
    app: WSGIApp
    max_request_pre_process: int
    request_count: int = 0

    def __call__(
        self, environ: Environ, start_response: StartResponse
    ) -> IterableChunks:
        yield from self.app(environ, start_response)

        self.request_count += 1
        if self.request_count >= self.max_request_pre_process:
            signal.raise_signal(signal.SIGTERM)
