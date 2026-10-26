"""Atomically replace the installed binary without following the destination."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def _refuse_symlink(destination: Path) -> int:
    print(
        f"Error: refusing to replace symlink at {destination}; remove an old "
        "source-checkout symlink manually after verifying its target, then retry.",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: replace_binary.py STAGED_EXECUTABLE DESTINATION", file=sys.stderr)
        return 2

    staged, destination = map(Path, sys.argv[1:])
    try:
        mode = destination.lstat().st_mode
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(mode):
            return _refuse_symlink(destination)
        if stat.S_ISDIR(mode):
            print(
                f"Error: refusing to replace directory at {destination}; choose another "
                "PREFIX or remove it manually after verifying its contents.",
                file=sys.stderr,
            )
            return 1
        elif not stat.S_ISREG(mode):
            print(
                f"Error: refusing to replace non-regular destination at {destination}; "
                "choose another PREFIX or move it manually after verifying it is safe.",
                file=sys.stderr,
            )
            return 1

    try:
        os.replace(staged, destination)
    except OSError as error:
        print(f"Error: cannot atomically replace {destination}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
