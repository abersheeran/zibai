import os
import sys

from .cli import main, parse_args


def command_line() -> None:
    sys.path.insert(0, os.getcwd())
    main(parse_args(sys.argv[1:]))


if __name__ == "__main__":
    command_line()
