import signal
import sys
import threading
import time

import pytest
from zibai.multiprocess import MultiProcessManager, ProcessParameters


def while_true():
    signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))
    while True:
        time.sleep(1)


def test_multiprocess() -> None:
    """
    Ensure that the MultiProcessManager works as expected.
    """
    supervisor = MultiProcessManager(2, ProcessParameters(while_true))
    threading.Thread(target=supervisor.mainloop, daemon=True).start()
    time.sleep(1)
    supervisor.should_exit.set()
    supervisor.terminate_all()
    supervisor.join_all()


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="platform unsupports SIGHUP")
def test_multiprocess_sighup() -> None:
    """
    Ensure that the SIGHUP signal is handled as expected.
    """
    supervisor = MultiProcessManager(2, ProcessParameters(while_true))
    threading.Thread(target=supervisor.mainloop, daemon=True).start()
    time.sleep(1)
    pids = [p.pid for p in supervisor.processes]
    supervisor.signal_queue.append(signal.SIGHUP)
    time.sleep(1)
    assert pids != [p.pid for p in supervisor.processes]
    supervisor.should_exit.set()
    supervisor.terminate_all()
    supervisor.join_all()


@pytest.mark.skipif(
    not hasattr(signal, "SIGTTIN"), reason="platform unsupports SIGTTIN"
)
def test_multiprocess_sigttin() -> None:
    """
    Ensure that the SIGTTIN signal is handled as expected.
    """
    supervisor = MultiProcessManager(2, ProcessParameters(while_true))
    threading.Thread(target=supervisor.mainloop, daemon=True).start()
    supervisor.signal_queue.append(signal.SIGTTIN)
    time.sleep(1)
    assert len(supervisor.processes) == 3
    supervisor.should_exit.set()
    supervisor.terminate_all()
    supervisor.join_all()


@pytest.mark.skipif(
    not hasattr(signal, "SIGTTOU"), reason="platform unsupports SIGTTOU"
)
def test_multiprocess_sigttou() -> None:
    """
    Ensure that the SIGTTOU signal is handled as expected.
    """
    supervisor = MultiProcessManager(2, ProcessParameters(while_true))
    threading.Thread(target=supervisor.mainloop, daemon=True).start()
    supervisor.signal_queue.append(signal.SIGTTOU)
    time.sleep(1)
    assert len(supervisor.processes) == 1
    supervisor.signal_queue.append(signal.SIGTTOU)
    time.sleep(1)
    assert len(supervisor.processes) == 1
    supervisor.should_exit.set()
    supervisor.terminate_all()
    supervisor.join_all()
