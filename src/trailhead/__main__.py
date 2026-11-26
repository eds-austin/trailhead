"""Console entry point: `trailhead` / `python -m trailhead`."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trailhead")
    parser.add_argument("--gallery", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--db", help="Path to the database file (default: user data dir)")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    from .app import run

    return run(args, [sys.argv[0], *qt_args])


if __name__ == "__main__":
    raise SystemExit(main())
