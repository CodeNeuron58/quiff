"""The Quiff app: a local page to teach Quiff a chore and run it again, no terminal.

    uv run quiff app          # or double-click Quiff.cmd

It serves one page on 127.0.0.1 only. Every API call must carry the token in
the address the app opens, and the Host header must be the app's own, so no
other website can start a run on this computer. Runs are the real `quiff`
CLI (see runner.py); chores and uploaded spreadsheets stay in runs/app/.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import runner

PAGE = Path(__file__).with_name("index.html")
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'none'")


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, token: str | None = None) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.token = token or secrets.token_urlsafe(18)
        self.run: runner.Run | None = None
        self.lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.token}"


class Handler(BaseHTTPRequestHandler):
    server: AppServer

    def log_message(self, *args) -> None:  # keep the console for what matters
        pass

    # -- plumbing ----------------------------------------------------------------
    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if kind.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _own_host(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _allowed(self) -> bool:
        """API calls: our own Host (no DNS rebinding), our token, no foreign Origin."""
        if not self._own_host():
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return False
        sent = self.headers.get("X-Quiff-Token", "")
        return hmac.compare_digest(sent.encode(), self.server.token.encode())

    def _body(self, limit: int = 2_100_000) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("That request is too big.")
        return self.rfile.read(length) if length else b""

    # -- routes ------------------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            if not self._own_host():
                return self._send(403, b"Forbidden", "text/plain")
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/favicon.ico":
            return self._send(204, b"", "text/plain")
        if not url.path.startswith("/api/"):
            return self._send(404, b"Not found", "text/plain")
        if not self._allowed():
            return self._json(403, {"error": "Not allowed."})
        query = parse_qs(url.query)
        if url.path == "/api/info":
            return self._json(200, info())
        if url.path == "/api/chores":
            return self._json(200, {"chores": chores_for_page()})
        if url.path == "/api/run":
            run = self.server.run
            if run is None:
                return self._json(200, {"state": "idle"})
            since = (query.get("since") or ["0"])[0]
            since = int(since) if since.isdigit() else 0
            return self._json(200, run.snapshot(since))
        return self._json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if not url.path.startswith("/api/") or not self._allowed():
            return self._json(403, {"error": "Not allowed."})
        try:
            if url.path == "/api/upload":
                name = parse_qs(url.query).get("name", ["rows.csv"])[0]
                return self._json(200, runner.save_upload(name, self._body()))
            data = json.loads(self._body(200_000) or b"{}")
            if url.path == "/api/run":
                return self._json(200, self._start(data))
            if url.path == "/api/answer":
                run = self.server.run
                ok = bool(run and run.answer(str(data.get("answer", ""))))
                return self._json(200 if ok else 409, {"ok": ok})
            if url.path == "/api/stop":
                if self.server.run:
                    self.server.run.stop()
                return self._json(200, {"ok": True})
            if url.path == "/api/forget":
                chores = [c for c in runner.load_chores() if c.id != data.get("id")]
                runner.save_chores(chores)
                return self._json(200, {"ok": True})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        return self._json(404, {"error": "Not found."})

    def _start(self, data: dict) -> dict:
        with self.server.lock:
            current = self.server.run
            if current and current.exit_code is None:
                raise ValueError("Quiff is already doing a chore: wait for it, or stop it first.")
            show = bool(data.get("show", True)) and overlay_ready()
            csv_path = ""
            if data.get("mode") == "replay":
                chore = runner.find_chore(str(data.get("id", "")))
                if chore is None:
                    raise ValueError("That chore isn't saved any more.")
                mode = "replay"
                if chore.kind == "sheet":
                    csv_path = str(_uploaded(data.get("csv")) if data.get("csv") else chore.csv)
                    sheet = runner.read_sheet(Path(csv_path))
                    if sheet["columns"] != chore.columns:
                        raise ValueError("This chore was taught with the columns "
                                         + ", ".join(chore.columns) + ". The new file has "
                                         + ", ".join(sheet["columns"]) + ".")
            else:
                if not has_key():
                    raise ValueError("Teaching needs a Meta API key: put META_API_KEY=... in .env "
                                     "next to the README, then open Quiff again.")
                sheet = data.get("sheet") or None
                if sheet:
                    sheet = runner.read_sheet(_uploaded(sheet.get("path")))
                where = data.get("where") or {}
                chore = runner.new_chore(str(data.get("sentence", "")), str(where.get("type", "url")),
                                         str(where.get("value", "")), sheet,
                                         bool(data.get("check_first", True)))
                mode = "teach"
                csv_path = chore.csv
            total = runner.read_sheet(Path(csv_path))["rows"] if chore.kind == "sheet" else 0
            command = runner.command_for(chore, csv_path=csv_path, show=show, replay=mode == "replay")
            self.server.run = runner.Run(chore, mode, command, total_rows=total, csv_path=csv_path)
            return {"ok": True, "id": self.server.run.id, "show": show}


def _uploaded(path) -> Path:
    """A spreadsheet the page names: only one the app itself saved in runs/app/uploads/."""
    candidate = Path(str(path or "")).resolve()
    if not candidate.is_relative_to(runner.UPLOADS.resolve()) or not candidate.is_file():
        raise ValueError("Pick the spreadsheet again.")
    return candidate


# ---------------------------------------------------------------- facts for the page

def has_key() -> bool:
    return bool(os.environ.get("META_API_KEY"))


def overlay_ready() -> bool:
    try:
        from quiff.show.overlay import app_dir
        return (app_dir() / "node_modules" / "electron").is_dir()
    except Exception:
        return False


def info() -> dict:
    return {"has_key": has_key(), "overlay": overlay_ready(), "windows": os.name == "nt",
            "apps": runner.APPS}


def chores_for_page() -> list[dict]:
    from quiff.recall import EXAMPLE_FLOWS_DIR

    saved = [runner.asdict(c) for c in runner.load_chores()]
    for chore in saved:
        chore["csv_name"] = Path(chore["csv"]).name.split("-", 2)[-1] if chore.get("csv") else ""
    if any(EXAMPLE_FLOWS_DIR.glob("b05*.json")):
        saved.append(runner.asdict(runner.example_chore()))
    return saved


def serve(port: int = 8770, open_browser: bool = True) -> None:
    from quiff.models.meta import load_env

    load_env()
    server = AppServer(port)
    print(f"Quiff is open at {server.url}")
    print("Keep this window open while you use Quiff. Close it, or press Ctrl+C, to quit.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(server.url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server.run and server.run.exit_code is None:
            server.run.stop()
        server.server_close()
