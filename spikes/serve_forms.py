"""Serve the race's local pages until Ctrl+C, for trying `quiff do` and `quiff batch`
on a deterministic target (and for recording demos):

    http://127.0.0.1:8765/login   a login page (tomsmith / SuperSecretPassword!)
    http://127.0.0.1:8765/order   a pizza order form, like httpbin.org/forms/post

    uv run python spikes/serve_forms.py
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # spikes/

import race_local  # noqa: E402


def main() -> None:
    server = race_local.serve()
    print("Serving http://127.0.0.1:8765/login and http://127.0.0.1:8765/order — Ctrl+C to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
