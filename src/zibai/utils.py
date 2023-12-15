import sys
from typing import Callable, Generator

ENC, ESC = sys.getfilesystemencoding(), "surrogateescape"


def unicode_to_wsgi(u: str) -> str:
    """Convert an environment variable to a WSGI "bytes-as-unicode" string"""
    return u.encode(ENC, ESC).decode("iso-8859-1")


class Input:
    def __init__(self, receive: Callable[[], bytes]) -> None:
        self.buffer = bytearray()
        self.receive = receive
        self._has_more = True

    @property
    def has_more(self) -> bool:
        if self._has_more or self.buffer:
            return True
        return False

    def _receive_more_data(self) -> bytes:
        if not self._has_more:
            return b""
        data = self.receive()
        self._has_more = data != b""
        return data

    def read(self, size: int = -1) -> bytes:
        while size == -1 or size > len(self.buffer):
            self.buffer.extend(self._receive_more_data())
            if not self._has_more:
                break
        if size == -1:
            result = bytes(self.buffer)
            self.buffer.clear()
        else:
            result = bytes(self.buffer[:size])
            del self.buffer[:size]
        return result

    def readline(self, limit: int = -1) -> bytes:
        while True:
            lf_index = self.buffer.find(b"\n", 0, limit if limit > -1 else None)
            if lf_index != -1:
                result = bytes(self.buffer[: lf_index + 1])
                del self.buffer[: lf_index + 1]
                return result
            elif limit != -1:
                result = bytes(self.buffer[:limit])
                del self.buffer[:limit]
                return result
            if not self._has_more:
                break
            self.buffer.extend(self._receive_more_data())

        result = bytes(self.buffer)
        self.buffer.clear()
        return result

    def readlines(self, hint: int = -1) -> list[bytes]:
        if not self.has_more:
            return []
        if hint == -1:
            raw_data = self.read(-1)
            bytelist = raw_data.split(b"\n")
            if raw_data[-1] == 10:  # 10 -> b"\n"
                bytelist.pop(len(bytelist) - 1)
            return [line + b"\n" for line in bytelist]
        return [self.readline() for _ in range(hint)]

    def __iter__(self) -> Generator[bytes, None, None]:
        while self.has_more:
            yield self.readline()
