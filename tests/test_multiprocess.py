import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from zibai.multiprocess import MultiProcessManager, ProcessParameters

from .utils import new_console_in_windows


def while_true():
    signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))
    while True:
        time.sleep(1)


@new_console_in_windows
def test_multiprocess() -> None:
    """
    Ensure that the MultiProcessManager works as expected.
    """
    multi_process_manager = MultiProcessManager(
        2, ProcessParameters(while_true), join_timeout=5
    )
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(multi_process_manager.mainloop)
    time.sleep(1)
    multi_process_manager.should_exit.set()
    multi_process_manager.terminate_all_quickly()
    future.result()


@new_console_in_windows
def test_multiprocess_sigbreak() -> None:
    """
    Ensure that the SIGBREAK signal is handled as expected.
    """
    multi_process_manager = MultiProcessManager(
        2, ProcessParameters(while_true), join_timeout=5
    )
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(multi_process_manager.mainloop)
    time.sleep(1)
    multi_process_manager.should_exit.set()
    multi_process_manager.terminate_all()
    future.result()


@pytest.fixture
def multi_process_manager():
    multi_process_manager = MultiProcessManager(
        2, ProcessParameters(while_true), join_timeout=5
    )
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(multi_process_manager.mainloop)
    time.sleep(1)
    yield multi_process_manager
    multi_process_manager.should_exit.set()
    multi_process_manager.terminate_all_quickly()
    future.result()


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="platform unsupports SIGHUP")
def test_multiprocess_sighup(multi_process_manager: MultiProcessManager) -> None:
    """
    Ensure that the SIGHUP signal is handled as expected.
    """
    pids = [p.pid for p in multi_process_manager.processes]
    multi_process_manager.signal_queue.append(signal.SIGHUP)
    time.sleep(1)
    assert pids != [p.pid for p in multi_process_manager.processes]


@pytest.mark.skipif(
    not hasattr(signal, "SIGTTIN"), reason="platform unsupports SIGTTIN"
)
def test_multiprocess_sigttin(multi_process_manager: MultiProcessManager) -> None:
    """
    Ensure that the SIGTTIN signal is handled as expected.
    """
    multi_process_manager.signal_queue.append(signal.SIGTTIN)
    time.sleep(1)
    assert len(multi_process_manager.processes) == 3


@pytest.mark.skipif(
    not hasattr(signal, "SIGTTOU"), reason="platform unsupports SIGTTOU"
)
def test_multiprocess_sigttou(multi_process_manager: MultiProcessManager) -> None:
    """
    Ensure that the SIGTTOU signal is handled as expected.
    """
    multi_process_manager.signal_queue.append(signal.SIGTTOU)
    time.sleep(1)
    assert len(multi_process_manager.processes) == 1
    multi_process_manager.signal_queue.append(signal.SIGTTOU)
    time.sleep(1)
    assert len(multi_process_manager.processes) == 1
