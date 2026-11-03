"""Quiff's side of the on-screen overlay.

The overlay itself is a small Electron app in `overlay/` (HTML/CSS: the status
pill, ghost cursor, highlight box and approval edge glow). This module starts
it, then sends it one JSON line per event over a local socket. The app's window
is click-through, always on top and never focused, so the apps Quiff drives
behave exactly as without it; screen recorders capture it.

If the overlay app dies, the run carries on without it.
"""

from __future__ import annotations

import ctypes
import json
import os
import secrets
import socket
import subprocess
import threading
from pathlib import Path

from . import Rect, Show, with_context

EMERGE_S, GLIDE_S, FADE_S = 0.24, 0.22, 0.27  # cursor travel times (v4 spec)


class OverlayError(RuntimeError):
    pass


def app_dir() -> Path:
    """The Electron app: QUIFF_OVERLAY_DIR, or overlay/ in the source checkout."""
    configured = os.environ.get("QUIFF_OVERLAY_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "overlay"


def _child_env() -> dict[str, str]:
    # VS Code's terminals set this, and it turns Electron into plain Node.
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    return env


def electron_exe(app: Path) -> Path:
    package = app / "node_modules" / "electron"
    if not package.is_dir():
        raise OverlayError(f"the overlay isn't installed yet: run `npm install` in {app}")
    path_txt = package / "path.txt"
    exe = package / "dist" / (path_txt.read_text().strip() if path_txt.exists() else "electron.exe")
    if not exe.exists():
        # npm sometimes skips Electron's download step; run it once
        subprocess.run(["node", str(package / "install.js")], cwd=package, env=_child_env(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if not exe.exists():
        raise OverlayError(f"Electron's binary is missing: run `node install.js` in {package}")
    return exe


def make_dpi_aware() -> None:
    """Read real screen pixels: element rects and window rects then share one
    coordinate space, which the overlay converts to its own layout units."""
    if not hasattr(ctypes, "windll"):
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


class Overlay(Show):
    """Starts the overlay app and sends it Quiff's events. Safe to call from any thread."""

    active = True

    def __init__(self, *, reduced_motion: bool | None = None, edge_glow: bool = True,
                 timeout: float = 30.0, scale: float = 1.0) -> None:
        make_dpi_aware()
        app = app_dir()
        exe = electron_exe(app)
        token = secrets.token_hex(16)
        server = socket.create_server(("127.0.0.1", 0))
        server.settimeout(timeout)
        port = server.getsockname()[1]
        self._process = subprocess.Popen(
            [str(exe), str(app), f"--quiff-port={port}", f"--quiff-token={token}"],
            env=_child_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            self._sock = self._accept(server, token, timeout)
        except Exception:
            self._process.kill()
            raise
        finally:
            server.close()
        self._lock = threading.Lock()
        self._cursor_out = False
        self.reduced_motion = bool(reduced_motion)
        self.calls_offset = 0  # model calls made before the loop started (the plan)
        config: dict = {"t": "config", "edgeGlow": edge_glow, "scale": float(scale)}
        if reduced_motion is not None:
            config["reducedMotion"] = reduced_motion
        self._send(config)

    def _accept(self, server: socket.socket, token: str, timeout: float) -> socket.socket:
        try:
            while True:
                conn, _ = server.accept()
                conn.settimeout(timeout)
                line = b""
                while not line.endswith(b"\n") and len(line) < 256:
                    chunk = conn.recv(256)
                    if not chunk:
                        break
                    line += chunk
                if line.strip() == f"ready {token}".encode():
                    conn.settimeout(5.0)
                    return conn
                conn.close()  # not our overlay
        except socket.timeout:
            raise OverlayError("the overlay app did not start in time") from None

    def _send(self, message: dict) -> None:
        data = (json.dumps(message) + "\n").encode()
        with self._lock:
            if self._sock is None:
                return
            try:
                self._sock.sendall(data)
            except OSError:
                self._sock = None  # the overlay is gone; the run goes on without it

    # ---------------------------------------------------------------- Show API

    def state(self, kind: str, line: str, guard: str = "") -> None:
        if kind == "yourturn":
            self._cursor_out = False  # the cursor goes back into the pill
        self._send({"t": "state", "kind": kind, "line": line, "guard": guard})

    def target(self, rect: Rect, chip: str, kind: str = "working", ripple: bool = True) -> float:
        first = not self._cursor_out
        self._cursor_out = True
        self._send({"t": "target", "rect": list(rect), "chip": chip, "kind": kind,
                    "ripple": ripple})
        if self.reduced_motion:
            return FADE_S
        return EMERGE_S if first else GLIDE_S

    def clear_target(self) -> None:
        self._send({"t": "clear"})

    def meter(self, text: str, calls: int) -> None:
        self._send({"t": "meter", "text": with_context(self.context, text),
                    "calls": calls + self.calls_offset})

    def close(self) -> None:
        self._send({"t": "close"})
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
        with self._lock:
            if self._sock is not None:
                self._sock.close()
                self._sock = None
