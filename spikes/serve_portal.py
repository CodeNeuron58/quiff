"""A demo supplier portal for `quiff batch`: a purchase-order form and its confirmation page.

    uv run python spikes/serve_portal.py      # http://127.0.0.1:8766/orders/new

The company is made up and the portal runs only on your machine. It is the chore
in the README's GIF: the ten purchase orders in tasks/batch/purchase-orders.csv,
typed in by `quiff batch`.
"""

from __future__ import annotations

import html
import itertools
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

PORT = 8766
ITEMS = ["Basmati rice 25 kg", "Sunflower oil 15 L", "Toor dal 30 kg", "Sugar 50 kg",
         "Wheat flour 10 kg", "Assam tea 5 kg"]
SLOTS = ["Morning", "Afternoon", "Evening"]
FIELDS = [("store", "Store"), ("contact", "Contact"), ("phone", "Phone"), ("item", "Item"),
          ("quantity", "Quantity"), ("delivery", "Delivery"), ("notes", "Notes")]

_po_numbers = itertools.count(24017)
_po_lock = threading.Lock()

STYLE = """
:root { --ink:#1F2328; --muted:#5F6670; --line:#E2E5E9; --field:#CED3D9; --bg:#F4F5F7;
        --brand:#0E7C66; --brand-ink:#0A5E4D; --brand-soft:#DCEFEA; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.45 "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif; }
.top { height:56px; display:flex; align-items:center; gap:28px; padding:0 28px;
       background:#fff; border-bottom:1px solid var(--line); }
.brand { display:flex; align-items:center; gap:10px; font-size:16px; }
.mark { width:28px; height:28px; border-radius:7px; background:var(--brand); color:#fff;
        display:grid; place-items:center; font-weight:700; font-size:12px; letter-spacing:.02em; }
.demo { font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.06em;
        color:var(--muted); border:1px solid var(--line); border-radius:4px; padding:1px 6px; }
nav { display:flex; gap:4px; }
nav a { color:var(--muted); text-decoration:none; padding:6px 10px; border-radius:6px; font-size:14px; }
nav a.on { color:var(--ink); background:#EEF1F4; font-weight:600; }
.who { margin-left:auto; display:flex; align-items:center; gap:10px; color:var(--muted); font-size:13px; }
.avatar { width:30px; height:30px; border-radius:50%; background:var(--brand-soft); color:var(--brand-ink);
          display:grid; place-items:center; font-weight:700; font-size:12px; }
main { max-width:920px; margin:24px auto; padding:0 28px; }
.crumbs { color:var(--muted); font-size:13px; }
.crumbs span { margin:0 6px; color:#A0A6AE; }
h1 { font-size:24px; margin:4px 0 16px; font-weight:600; letter-spacing:-.01em; }
.card { background:#fff; border:1px solid var(--line); border-radius:10px; }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:16px 24px; padding:22px 24px 20px; }
.field { display:flex; flex-direction:column; gap:6px; min-width:0; }
fieldset.field { display:block; margin:0; padding:0; border:0; }
.wide { grid-column:1 / -1; }
label, legend { font-size:13px; font-weight:600; color:#3A4048; padding:0; }
legend { margin-bottom:6px; }
input[type=text], input[type=tel], input[type=number], select, textarea {
  font:inherit; color:var(--ink); height:38px; padding:0 11px; border:1px solid var(--field);
  border-radius:7px; background:#fff; width:100%; }
textarea { height:60px; padding:8px 11px; resize:none; }
input:focus, select:focus, textarea:focus { outline:none; border-color:var(--brand);
  box-shadow:0 0 0 3px rgba(14,124,102,.15); }
.radios { display:flex; gap:10px; }
.radios label { display:flex; align-items:center; gap:8px; height:38px; padding:0 14px;
  border:1px solid var(--field); border-radius:7px; font-weight:500; font-size:14px; color:var(--ink); }
.radios input { margin:0; accent-color:var(--brand); }
.foot { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:14px 24px;
  border-top:1px solid var(--line); background:#FAFBFC; border-radius:0 0 10px 10px;
  color:var(--muted); font-size:13px; }
button, .btn { font:inherit; font-weight:600; color:#fff; background:var(--brand); border:0;
  border-radius:7px; padding:9px 18px; cursor:pointer; text-decoration:none; display:inline-block; }
.done { padding:26px 28px 24px; max-width:640px; margin-top:12px; }
.ok { width:40px; height:40px; border-radius:50%; background:var(--brand-soft); display:grid; place-items:center; }
.done h1 { margin:14px 0 2px; }
.po { color:var(--muted); margin:0 0 16px; }
.summary { border-top:1px solid var(--line); padding-top:12px; margin-bottom:20px; }
.summary div { padding:4px 0; }
.k { display:inline-block; width:96px; color:var(--muted); }
"""


def _page(title: str, crumbs: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title} · Lantern Wholesale</title>
<style>{STYLE}</style></head>
<body>
<header class="top">
  <div class="brand"><span class="mark">LW</span><b>Lantern Wholesale</b><span class="demo">Demo</span></div>
  <nav><a class="on" href="#">Orders</a><a href="#">Catalogue</a><a href="#">Invoices</a><a href="#">Reports</a></nav>
  <div class="who"><span>ops@lantern.example</span><span class="avatar">OP</span></div>
</header>
<main>
  <div class="crumbs">Orders <span>/</span> {crumbs}</div>
{body}
</main>
</body></html>"""


def form_page() -> str:
    options = "".join(f"<option>{html.escape(item)}</option>" for item in ITEMS)
    slots = "".join(f'<label><input type="radio" name="delivery" value="{slot}"> {slot}</label>'
                    for slot in SLOTS)
    return _page("New purchase order", "New purchase order", f"""  <h1>New purchase order</h1>
  <form class="card" method="post" action="/orders/new" autocomplete="off">
    <div class="grid">
      <div class="field"><label for="store">Store name</label><input id="store" name="store" type="text"></div>
      <div class="field"><label for="contact">Contact person</label><input id="contact" name="contact" type="text"></div>
      <div class="field"><label for="phone">Phone</label><input id="phone" name="phone" type="tel"></div>
      <div class="field"><label for="item">Item</label>
        <select id="item" name="item"><option value="">Choose an item</option>{options}</select></div>
      <div class="field"><label for="quantity">Quantity</label><input id="quantity" name="quantity" type="number" min="1"></div>
      <fieldset class="field"><legend>Delivery slot</legend><div class="radios">{slots}</div></fieldset>
      <div class="field wide"><label for="notes">Notes</label><textarea id="notes" name="notes"></textarea></div>
    </div>
    <div class="foot"><span>Orders placed before 4 pm are delivered the next day.</span>
      <button type="submit">Submit order</button></div>
  </form>""")


def received_page(values: dict[str, str]) -> str:
    with _po_lock:
        po = f"PO-{next(_po_numbers)}"
    # one line per field, "Store: Sharma General Store", so `--check-text` can match it exactly
    lines = "".join(f'<div><span class="k">{label}:</span> <span>{html.escape(values.get(key, ""))}</span></div>'
                    for key, label in FIELDS)
    check = ('<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="#0E7C66" '
             'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 10.5l3.5 3.5 7.5-8"/></svg>')
    return _page("Order received", po, f"""  <div class="card done">
    <div class="ok">{check}</div>
    <h1>Order received</h1>
    <p class="po">{po} · We'll confirm the delivery slot by SMS.</p>
    <div class="summary">{lines}</div>
    <a class="btn" href="/orders/new">New purchase order</a>
  </div>""")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, body: str, code: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/orders"):
            self.send_response(302)
            self.send_header("Location", "/orders/new")
            self.end_headers()
        elif path == "/orders/new":
            self._send(form_page())
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._send("<h1>Not found</h1>", 404)

    def do_POST(self):
        if self.path.split("?")[0] != "/orders/new":
            self._send("<h1>Not found</h1>", 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        self._send(received_page({key: value[0].strip() for key, value in fields.items()}))


def serve(port: int = PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving http://127.0.0.1:{PORT}/orders/new (a demo supplier portal) — Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
