"""Command-line entry point, run as `lesto` or `python -m lesto`."""

from lesto import __version__


def main() -> None:
    print(f"lesto {__version__}")
