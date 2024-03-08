import functools
import os
from typing import Any, Callable


def new_console_in_windows(test_function: Callable[[], Any]):
    if os.name != "nt":
        return test_function

    @functools.wraps(test_function)
    def new_function():
        import subprocess
        import sys

        return subprocess.check_call(
            [
                sys.executable,
                "-c",
                f"from {test_function.__module__} import {test_function.__name__}; {test_function.__name__}.__wrapped__()",
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    return new_function
