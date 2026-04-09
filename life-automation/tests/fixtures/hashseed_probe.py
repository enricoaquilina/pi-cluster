"""Helper used by test_conftest_runner to verify PYTHONHASHSEED propagation.

Prints the hash of a well-known string. With PYTHONHASHSEED=random this
value varies across invocations; with PYTHONHASHSEED=0 it's stable.
"""
import sys

if __name__ == "__main__":
    print(hash("conftest-runner-probe"))
    sys.exit(0)
