import os
import signal
import dataclasses

from ..wsgi_typing import WSGIApp, Environ, StartResponse, IterableChunks


@dataclasses.dataclass
class LimitRequestCountMiddleware:
    app: WSGIApp
    max_request_pre_process: int
    request_count: int = 0

    def __call__(
        self, environ: Environ, start_response: StartResponse
    ) -> IterableChunks:
        yield from self.app(environ, start_response)

        if self.max_request_pre_process is not None:
            self.request_count += 1
            if self.request_count >= self.max_request_pre_process:
                if os.name == "nt":
                    os.kill(os.getpid(), signal.CTRL_BREAK_EVENT)
                else:
                    os.kill(os.getpid(), signal.SIGTERM)
