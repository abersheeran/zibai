import subprocess
import sys


def test_main():
    subprocess.check_call([sys.executable, "-m", "zibai", "--help"])
