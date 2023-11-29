import os
import signal
import threading
from contextlib import nullcontext
from multiprocessing.context import SpawnProcess
from typing import Callable

from .logger import logger

UNIX_SIGNALS = {
    getattr(signal, f"SIG{x}"): x
    for x in "HUP QUIT TTIN TTOU USR1 USR2 WINCH".split()
    if hasattr(signal, f"SIG{x}")
}


class MultiProcessManager:
    def __init__(self, processes_num: int, create_process: Callable[[], SpawnProcess]):
        self.processes_num = processes_num
        self.create_process = create_process
        self.processes: list[SpawnProcess] = []
        self.signal_queue: list[int] = []
        for sig in UNIX_SIGNALS:
            signal.signal(sig, lambda sig, frame: self.signal_queue.append(sig))
        self.should_exit = threading.Event()
        for sig in (
            signal.SIGINT,  # Sent by Ctrl+C.
            signal.SIGTERM  # Sent by `kill <pid>`. Not sent on Windows.
            if os.name != "nt"
            else signal.SIGBREAK,  # Sent by `Ctrl+Break` on Windows.
        ):
            signal.signal(
                sig,
                lambda sig, frame: (
                    self.should_exit.set() if not self.should_exit.is_set() else exit(0)
                ),
            )

    def create_child(self) -> SpawnProcess:
        process = self.create_process()
        process.daemon = True
        self.processes.append(process)
        process.start()
        logger.info("Started child process [{}]".format(process.pid))
        return process

    def terminate_child(self, process: SpawnProcess) -> None:
        if process.exitcode is not None:
            return
        assert process.pid is not None
        if os.name == "nt":
            # Windows doesn't support SIGTERM.
            os.kill(process.pid, signal.CTRL_BREAK_EVENT)
        else:
            os.kill(process.pid, signal.SIGTERM)
        logger.info("Terminated child process [{}]".format(process.pid))

    def init_processes(self) -> None:
        for _ in range(self.processes_num):
            self.create_child()

    def terminate_all(self) -> None:
        for process in self.processes:
            self.terminate_child(process)

    def join_all(self) -> None:
        for process in self.processes:
            logger.info("Waiting for child process [{}]".format(process.pid))
            process.join()

    def restart_all(self) -> None:
        for idx, process in enumerate(tuple(self.processes)):
            self.terminate_child(process)
            del self.processes[idx]
            self.create_child()

    def mainloop(self) -> None:
        logger.info("Started parent process [{}]".format(os.getpid()))

        self.init_processes()

        while not self.should_exit.wait(0.5):
            self.handle_signals()
            self.keep_subprocess_alive()

        self.terminate_all()
        self.join_all()

        logger.info("Stopped parent process [{}]".format(os.getpid()))

    def keep_subprocess_alive(self) -> None:
        for idx, process in enumerate(tuple(self.processes)):
            if process.is_alive():
                continue

            self.terminate_child(process)
            logger.info("Child process [{}] died".format(process.pid))
            del self.processes[idx]
            self.create_child()

    def handle_signals(self) -> None:
        for sig in tuple(self.signal_queue):
            self.signal_queue.remove(sig)
            sig_name = UNIX_SIGNALS[sig]
            sig_handler = getattr(self, f"handle_{sig_name.lower()}", None)
            if sig_handler is not None:
                sig_handler()
            else:
                logger.info("Received signal {}".format(sig_name))

    def handle_hup(self) -> None:
        logger.info("Received SIGHUP, restarting processes")
        self.restart_all()

    def handle_ttin(self) -> None:
        logger.info("Received SIGTTIN, increasing processes")
        self.processes_num += 1
        self.create_child()

    def handle_ttou(self) -> None:
        logger.info("Received SIGTTOU, decreasing processes")
        if self.processes_num <= 1:
            logger.info("Cannot decrease processes any more")
            return
        self.processes_num -= 1
        self.terminate_child(self.processes.pop())


def multiprocess(
    processes_num: int,
    create_process: Callable[[], SpawnProcess],
    watchfiles: str | None,
) -> None:
    processes_manager = MultiProcessManager(processes_num, create_process)

    if watchfiles is not None:
        from .reloader import listen_for_changes

        contextmanager = listen_for_changes(watchfiles, processes_manager.restart_all)
    else:
        contextmanager = nullcontext()

    with contextmanager:
        processes_manager.mainloop()
