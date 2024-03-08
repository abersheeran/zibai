import os
import signal
import threading
import time
from contextlib import nullcontext
from multiprocessing import Pipe, get_context
from multiprocessing.context import SpawnProcess
from typing import Any, Callable, ParamSpec

from .logger import logger

get_context("spawn").allow_connection_pickling()

UNIX_SIGNALS = {
    getattr(signal, f"SIG{x}"): x
    for x in "HUP QUIT TTIN TTOU USR1 USR2 WINCH".split()
    if hasattr(signal, f"SIG{x}")
}

P = ParamSpec("P")


class ProcessParameters:
    def __init__(
        self,
        f: Callable[P, Any],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        self.f = f
        self.args = args
        self.kwargs = kwargs


class Process:
    def __init__(self, parameters: ProcessParameters) -> None:
        self.parameters = parameters
        self.parent_conn, self.child_conn = Pipe()
        self.process = SpawnProcess(target=self.target)

    def ping(self, timeout: float = 5) -> bool:
        try:
            self.parent_conn.send(b"ping")
            if self.parent_conn.poll(timeout):
                self.parent_conn.recv()
                return True
        except IOError:
            pass
        return False

    def pong(self) -> None:
        self.child_conn.recv()
        self.child_conn.send(b"pong")

    def always_pong(self) -> None:
        while True:
            self.pong()

    def target(self) -> Any:
        if os.name == "nt":
            # Windows doesn't support SIGTERM, so we use SIGBREAK instead.
            # And then we raise SIGTERM when SIGBREAK is received.
            # https://learn.microsoft.com/zh-cn/cpp/c-runtime-library/reference/signal?view=msvc-170
            signal.signal(
                signal.SIGBREAK, lambda sig, frame: signal.raise_signal(signal.SIGTERM)
            )

        threading.Thread(target=self.always_pong, daemon=True).start()
        return self.parameters.f(*self.parameters.args, **self.parameters.kwargs)

    def is_alive(self, timeout: float = 5) -> bool:
        return self.process.is_alive() and self.ping(timeout)

    def start(self) -> None:
        self.process.start()
        logger.info("Started child process [{}]".format(self.process.pid))

    def terminate(self) -> None:
        if self.process.exitcode is not None:
            return
        assert self.process.pid is not None
        if os.name == "nt":
            # Windows doesn't support SIGTERM.
            # So send SIGBREAK, and then in process raise SIGTERM.
            os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
        else:
            os.kill(self.process.pid, signal.SIGTERM)
        logger.info("Terminated child process [{}]".format(self.process.pid))

        self.parent_conn.close()
        self.child_conn.close()

    def terminate_quickly(self) -> None:
        if self.process.exitcode is not None:
            return
        assert self.process.pid is not None
        if os.name == "nt":
            os.kill(self.process.pid, signal.CTRL_C_EVENT)
        else:
            os.kill(self.process.pid, signal.SIGINT)
        logger.info("Terminated quickly child process [{}]".format(self.process.pid))

        self.parent_conn.close()
        self.child_conn.close()

    def kill(self) -> None:
        # In Windows, the method will call `TerminateProcess` to kill the process.
        # In Unix, the method will send SIGKILL to the process.
        self.process.kill()

    def join(self, timeout: float | None = None) -> None:
        logger.info("Waiting for child process [{}]".format(self.process.pid))
        self.process.join(timeout)
        # Timeout, kill the process
        while self.process.exitcode is None:
            self.process.kill()
            self.process.join(1)

    @property
    def pid(self) -> int | None:
        return self.process.pid


class MultiProcessManager:
    def __init__(
        self,
        processes_num: int,
        process_parameters: ProcessParameters,
        join_timeout: float | None = None,
    ):
        self.join_timeout = join_timeout
        self.processes_num = processes_num
        self.process_parameters = process_parameters
        self.processes: list[Process] = []

        self.should_exit = threading.Event()
        self.reload_lock = threading.Lock()

        self.signal_queue: list[int] = []
        for sig in UNIX_SIGNALS:
            signal.signal(sig, lambda sig, frame: self.signal_queue.append(sig))

        # Sent by Ctrl+C.
        signal.signal(signal.SIGINT, lambda sig, frame: self.handle_int())
        # Sent by `kill <pid>`. Not sent on Windows.
        signal.signal(signal.SIGTERM, lambda sig, frame: self.handle_term())
        if os.name == "nt":
            # Sent by `Ctrl+Break` on Windows.
            signal.signal(signal.SIGBREAK, lambda sig, frame: self.handle_break())

    def init_processes(self) -> None:
        for _ in range(self.processes_num):
            process = Process(self.process_parameters)
            process.start()
            self.processes.append(process)

    def terminate_all(self) -> None:
        for process in self.processes:
            process.terminate()

    def terminate_all_quickly(self) -> None:
        for process in self.processes:
            process.terminate_quickly()

    def join_all(self) -> None:
        for process in self.processes:
            process.join(self.join_timeout)

    def restart_all(self) -> None:
        for idx, process in enumerate(tuple(self.processes)):
            process.terminate()
            process.join()
            new_process = Process(self.process_parameters)
            new_process.start()
            self.processes[idx] = new_process

    def on_watchfiles_reload(self) -> None:
        with self.reload_lock:
            self.terminate_all_quickly()
            self.join_all()
            time.sleep(1)  # Wait for the Ctrl+C signal to be handled
            # Because in Windows, the Ctrl+C signal always send to main process.

    def mainloop(self) -> None:
        logger.info("Started parent process [{}]".format(os.getpid()))

        self.init_processes()

        while not self.should_exit.wait(0.5):
            self.handle_signals()
            self.keep_subprocess_alive()

        self.join_all()

        logger.info("Stopped parent process [{}]".format(os.getpid()))

    def keep_subprocess_alive(self) -> None:
        if self.should_exit.is_set():
            return  # parent process is exiting, no need to keep subprocess alive

        for idx, process in enumerate(tuple(self.processes)):
            if process.is_alive():
                continue

            process.kill()  # process is hung, kill it
            process.join(1)

            if self.should_exit.is_set():
                return

            logger.info("Child process [{}] died".format(process.pid))
            del self.processes[idx]
            process = Process(self.process_parameters)
            process.start()
            self.processes.append(process)

    def handle_signals(self) -> None:
        for sig in tuple(self.signal_queue):
            self.signal_queue.remove(sig)
            sig_name = UNIX_SIGNALS[sig]
            sig_handler = getattr(self, f"handle_{sig_name.lower()}", None)
            if sig_handler is not None:
                sig_handler()
            else:
                logger.info(f"Received signal [{sig_name}], but nothing to do")

    def handle_int(self) -> None:
        if self.reload_lock.locked():
            return
        logger.info("Received SIGINT, quickly exiting")
        self.should_exit.set()
        # On Windows Ctrl+C is automatically sent to all child processes.
        if os.name != "nt":
            self.terminate_all_quickly()

    def handle_term(self) -> None:
        logger.info("Received SIGTERM, exiting")
        self.should_exit.set()
        self.terminate_all()

    def handle_break(self) -> None:
        logger.info("Received SIGBREAK, exiting")
        self.should_exit.set()
        # On Windows, Ctrl+Break is automatically sent to all child processes.
        # So, we don't need to terminate all child processes here.

    def handle_hup(self) -> None:
        logger.info("Received SIGHUP, restarting processes")
        self.restart_all()

    def handle_ttin(self) -> None:
        logger.info("Received SIGTTIN, increasing processes")
        self.processes_num += 1
        process = Process(self.process_parameters)
        process.start()
        self.processes.append(process)

    def handle_ttou(self) -> None:
        logger.info("Received SIGTTOU, decreasing processes")
        if self.processes_num <= 1:
            logger.info("Cannot decrease processes any more")
            return
        self.processes_num -= 1
        process = self.processes.pop()
        process.terminate()
        process.join()


def multiprocess(
    processes_num: int,
    process_parameters: ProcessParameters,
    watchfiles: str | None,
    join_timeout: float | None = None,
) -> None:
    processes_manager = MultiProcessManager(
        processes_num, process_parameters, join_timeout
    )

    if watchfiles is not None:
        from .reloader import listen_for_changes

        contextmanager = listen_for_changes(
            watchfiles, processes_manager.on_watchfiles_reload
        )
    else:
        contextmanager = nullcontext()

    with contextmanager:
        processes_manager.mainloop()
