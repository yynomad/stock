#!/usr/bin/env python3
"""Compatibility entry point for the installed stockman command."""


def main(argv=None):
    try:
        from stockman.cli import main as cli_main
    except ImportError:
        from pathlib import Path
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
        from stockman.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
