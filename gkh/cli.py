from __future__ import annotations

import sys

COMMANDS = ("install", "uninstall", "update", "status")


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "install"
    if command not in COMMANDS:
        sys.stderr.write("usage: install [%s]\n" % "|".join(COMMANDS))
        return 2
    print("%s: not implemented" % command)
    return 0
