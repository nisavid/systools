"""Command-line entry point for mlxctl."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mlxctl", description="Manage local MLX inference servers."
    )
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
