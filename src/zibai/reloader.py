import os
from contextlib import contextmanager
from typing import Any, Callable, Generator

import watchdog.events
from watchdog.observers import Observer

from .logger import logger


@contextmanager
def listen_for_changes(
    watchfiles: str, callback: Callable[[], Any]
) -> Generator[None, None, None]:
    def on_any_event(event: watchdog.events.FileSystemEvent) -> None:
        if event.event_type not in (
            watchdog.events.EVENT_TYPE_MOVED,
            watchdog.events.EVENT_TYPE_DELETED,
            watchdog.events.EVENT_TYPE_CREATED,
            watchdog.events.EVENT_TYPE_MODIFIED,
        ):
            return

        logger.info("Detected file change, reloading...")
        callback()

    event_handler = watchdog.events.PatternMatchingEventHandler(
        patterns=[pattern.strip() for pattern in watchfiles.split(";")],
        ignore_patterns=[
            "venv/*",
            ".venv/*",
            ".git/*",
            "__pycache__/*",
        ],
    )

    setattr(event_handler, "on_any_event", on_any_event)

    observer = Observer()

    path = os.getcwd()
    logger.info("Watching files in {}".format(path))
    observer.schedule(event_handler, path, recursive=True)

    observer.start()
    try:
        logger.debug("Started observer")
        yield
    finally:
        logger.debug("Stopping observer")
        observer.stop()
        observer.join()
