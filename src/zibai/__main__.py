import sys

from .cli import main, parse_args

main(parse_args(sys.argv[1:]))
