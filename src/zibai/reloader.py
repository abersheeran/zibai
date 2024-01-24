import os
import threading
from contextlib import contextmanager
from typing import Any, Callable, Generator

import watchdog.events
from watchdog.observers import Observer

from .logger import logger


@contextmanager
def listen_for_changes(
    watchfiles: str, callback: Callable[[], Any]
) -> Generator[None, None, None]:
    """
    When any of the files in `watchfiles` matches a change, call `callback`.
    """
    reloading_event = threading.Event()

    def run_callback(event: watchdog.events.FileSystemEvent) -> None:
        if not reloading_event.is_set():
            logger.info("Detected file change, reloading...")
            reloading_event.set()
            callback()
            reloading_event.clear()

    def on_any_event(event: watchdog.events.FileSystemEvent) -> None:
        if event.event_type not in (
            watchdog.events.EVENT_TYPE_MOVED,
            watchdog.events.EVENT_TYPE_DELETED,
            watchdog.events.EVENT_TYPE_CREATED,
            watchdog.events.EVENT_TYPE_MODIFIED,
        ):
            return

        threading.Thread(target=run_callback, args=(event,), daemon=True).start()

    event_handler = watchdog.events.PatternMatchingEventHandler(
        patterns=[pattern.strip() for pattern in watchfiles.split(";")],
    )

    setattr(event_handler, "on_any_event", on_any_event)

    observer = Observer()

    path = os.getcwd()
    logger.info("Watching files in {}".format(path))
    observer.schedule(event_handler, path, recursive=True)

    observer.start()
    try:
        yield
    finally:
        observer.stop()
        observer.join()
