"""Local dashboard for the document intake database.

    python dashboard.py            # then open http://127.0.0.1:8501

Standard library only. It reads the SQLite reporting views and renders plain HTML.
It listens on 127.0.0.1, so it is only reachable from this computer.
"""
import sqlite3
import sys
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from docintake import db
from docintake.ask import UnsafeQuery, ask
from docintake.backends import OllamaBackend

DB_PATH = Path(__file__).resolve().parent / "data" / "intake.db"
PORT = 8501
# Earlier attempts (v1 and v2 had bugs I fixed later; v3 is superseded by v4). They stay in the run history table
# but are hidden from the run picker so nobody opens an all-zero baseline by accident.
ARCHIVED_RUNS = ("[v1 ", "[v2 ", "[v3 ")

CSS = """
:root { --bg:#f6f5f1; --card:#fff; --ink:#1d2320; --muted:#667068; --line:#dcdad2;
        --ok:#2f7d4f; --flag:#b7791f; --fail:#b83a3a; --bar:#3f6b57; --bar2:#d9a441; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161a18; --card:#1f2522; --ink:#e8ece9; --muted:#97a39b; --line:#33403a;
          --ok:#58b57c; --flag:#e0a94a; --fail:#e06b6b; --bar:#5fa383; --bar2:#d9a441; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui, sans-serif; }
main { max-width:1000px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:24px; margin:0 0 4px; } h2 { font-size:17px; margin:0 0 12px; }
.sub { color:var(--muted); margin:0 0 20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:16px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:16px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; }
.stat b { display:block; font-size:28px; } .stat span { color:var(--muted); font-size:13px; }
.ok b { color:var(--ok); } .flagged b { color:var(--flag); } .failed b { color:var(--fail); }
table { width:100%; border-collapse:collapse; font-size:14px; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; } td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
.row { display:grid; grid-template-columns:200px 1fr 110px; gap:10px; align-items:center; margin:6px 0; font-size:14px; }
.track { height:14px; background:var(--line); border-radius:7px; overflow:hidden; display:flex; }
.track i { display:block; height:100%; } .a { background:var(--bar); } .b { background:var(--bar2); }
.legend { color:var(--muted); font-size:13px; margin-top:8px; }
.pill { padding:1px 8px; border-radius:10px; font-size:12px; color:#fff; }
.pill.flagged { background:var(--flag); } .pill.failed { background:var(--fail); }
a { color:var(--bar); } pre { white-space:pre-wrap; font-size:13px; background:var(--bg); padding:12px; border-radius:8px; }
select { font:inherit; padding:4px 8px; }
form { display:flex; gap:8px; } input.ask { flex:1; font:inherit; padding:8px 10px; border-radius:8px;
  border:1px solid var(--line); background:var(--bg); color:var(--ink); }
button { font:inherit; padding:8px 16px; border-radius:8px; border:0; background:var(--bar); color:#fff; cursor:pointer; }
.chip { display:inline-block; margin:2px 4px 2px 0; padding:2px 10px; border:1px solid var(--line); border-radius:12px;
  font-size:13px; text-decoration:none; } .err { color:var(--fail); } h3 { font-size:15px; margin:16px 0 6px; }
@media (max-width:640px) { .row { grid-template-columns:1fr; } }
"""


def money(value) -> str:
    return f"${value:,.2f}" if value is not None else ""


def pounds(value) -> str:
    return f"{value:,.0f} lb"


def bars(rows, label_key, a_key, b_key=None, fmt=money) -> str:
    if not rows:
        return "<p class=sub>Nothing to show yet.</p>"
    biggest = max((r[a_key] or 0) + ((r[b_key] or 0) if b_key else 0) for r in rows) or 1
    out = []
    for r in rows:
        a, b = r[a_key] or 0, (r[b_key] or 0) if b_key else 0
        segments = f'<i class=a style="width:{100 * a / biggest:.1f}%"></i>'
        if b_key:
            segments += f'<i class=b style="width:{100 * b / biggest:.1f}%"></i>'
        out.append(f'<div class=row><span>{escape(str(r[label_key]))}</span>'
                   f'<div class=track>{segments}</div><span class=n>{fmt(a + b)}</span></div>')
    return "".join(out)


def answer_card(model: str, question: str) -> str:
    """The 'ask a question' box, plus the answer if a question was submitted."""
    examples = ["Which vendor did we spend the most with?",
                "Which piece of equipment cost the most to repair?",
                "How many documents are in the review queue?"]
    chips = " ".join(f'<a class=chip href="/?model={quote(model)}&q={quote(e)}">{escape(e)}</a>' for e in examples)
    result = ""
    if question:
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                sql, rows = ask(conn, question, OllamaBackend(), model)
            finally:
                conn.close()
            if rows:
                head = "".join(f"<th>{escape(k)}" for k in rows[0].keys())
                body = "".join("<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in tuple(r)) + "</tr>" for r in rows)
                table = f"<table><tr>{head}</tr>{body}</table>"
            else:
                table = "<p class=sub>The query ran, and no rows matched.</p>"
            result = (f"<h3>Answer</h3>{table}"
                      f"<p class=legend>The model wrote this read-only SQL, and the dashboard ran it:</p><pre>{escape(sql)}</pre>")
        except UnsafeQuery as error:
            result = f"<p class=err>Blocked: {escape(str(error))}. Nothing was run.</p>"
        except Exception as error:  # model offline, malformed answer, or SQL the model got wrong
            result = f"<p class=err>Could not answer that: {escape(str(error))}</p>"
    return f"""<div class=card><h2>Ask a question about this run</h2>
<form onsubmit="this.querySelector('button').textContent='Thinking...'">
<input type=hidden name=model value="{escape(model)}">
<input class=ask name=q value="{escape(question)}" placeholder="e.g. Which customer did we ship the most weight to?" autocomplete=off>
<button>Ask</button></form>
<p class=legend>Try: {chips}</p>{result}</div>"""


def page(conn: sqlite3.Connection, model: str, question: str = "") -> str:
    q = lambda sql, *args: conn.execute(sql, args).fetchall()
    all_models = [r[0] for r in q("SELECT DISTINCT model FROM documents ORDER BY model")]
    if not all_models:
        return "<p>No data yet. Run <code>python -m docintake.pipeline</code> first.</p>"
    models = [m for m in all_models if not any(tag in m for tag in ARCHIVED_RUNS)] or all_models
    if model not in models:
        model = next((m for m in models if "holdout" not in m), models[0])
    history = q("SELECT * FROM v_run_history ORDER BY model")
    pct = lambda v: "" if v is None else f"{v:g}%"
    history_rows = "".join(
        f'<tr><td>{escape(r["model"])}</td><td class=n>{pct(r["dates_correct"])}</td>'
        f'<td class=n>{pct(r["line_items_correct"])}</td><td class=n>{pct(r["review_decision_correct"])}</td></tr>'
        for r in history)

    counts = {r["status"]: r["n"] for r in q(
        "SELECT status, COUNT(*) n FROM documents WHERE model = ? GROUP BY status", model)}
    total_docs = sum(counts.values())
    stats = "".join(
        f'<div class="stat {cls}"><b>{counts.get(key, 0)}</b><span>{label}</span></div>'
        for key, label, cls in (("ok", "extracted and checked", "ok"),
                                ("flagged", "flagged for review", "flagged"),
                                ("failed", "failed", "failed")))
    stats = f'<div class=stat><b>{total_docs}</b><span>documents</span></div>' + stats

    vendors = q("SELECT * FROM v_spend_by_vendor WHERE model = ? ORDER BY confirmed_spend + unreviewed_spend DESC", model)
    monthly = q("SELECT month, SUM(spend) spend FROM v_monthly_spend WHERE model = ? GROUP BY month ORDER BY month", model)
    equipment = q("SELECT * FROM v_equipment_cost WHERE model = ? ORDER BY repair_cost DESC", model)
    shipments = q("SELECT * FROM v_shipments_by_customer WHERE model = ? ORDER BY total_weight_lb DESC", model)
    review = q("SELECT * FROM v_review_queue WHERE model = ?", model)
    accuracy = q("SELECT * FROM v_accuracy_by_field WHERE model = ? ORDER BY percent_correct, field", model)

    review_rows = "".join(
        f'<tr><td><a href="/doc?model={escape(model)}&file={escape(r["source_file"])}">{escape(r["source_file"])}</a></td>'
        f'<td><span class="pill {r["status"]}">{r["status"]}</span></td>'
        f'<td>{escape(r["counterparty"] or "")}</td><td class=n>{money(r["total"])}</td>'
        f'<td>{escape(r["flags"])}</td></tr>' for r in review)
    accuracy_rows = "".join(
        f'<tr><td>{escape(r["field"])}</td><td class=n>{r["correct"]}/{r["documents"]}</td>'
        f'<td class=n>{r["percent_correct"]}%</td></tr>' for r in accuracy)

    options = "".join(f'<option{" selected" if m == model else ""}>{escape(m)}</option>' for m in models)
    return f"""
<h1>Document intake</h1>
<p class=sub>Fake lumber-mill paperwork, read by an LLM, checked by rules, stored in SQLite.
<form style="display:inline"><select name=model onchange="this.form.submit()">{options}</select></form></p>
<div class=stats>{stats}</div>
{answer_card(model, question)}
<div class=card><h2>Spend by vendor</h2>{bars(vendors, "counterparty", "confirmed_spend", "unreviewed_spend")}
<p class=legend>Green is spend from documents that passed every check. Amber is spend on flagged documents, not yet trusted.</p></div>
<div class=card><h2>Confirmed spend by month</h2>{bars(monthly, "month", "spend")}</div>
<div class=card><h2>Repair cost by equipment</h2>{bars(equipment, "equipment", "repair_cost")}</div>
<div class=card><h2>Shipped weight by customer</h2>{bars(shipments, "counterparty", "total_weight_lb", fmt=pounds)}</div>
<div class=card><h2>Review queue</h2>
<table><tr><th>File<th>Status<th>Counterparty<th class=n>Total<th>Why</tr>{review_rows or "<tr><td colspan=5>Nothing to review.</td></tr>"}</table></div>
<div class=card><h2>Accuracy against the answer key</h2>
{"<table><tr><th>Field<th class=n>Correct<th class=n>Rate</tr>" + accuracy_rows + "</table>" if accuracy_rows else "<p class=sub>Run <code>python -m docintake.evaluate</code> to fill this in.</p>"}</div>
<div class=card><h2>Run history</h2>
<p class=legend>Every attempt at reading the documents. v1 and v2 had bugs I later fixed. The "holdout" runs use different sets of 30 documents than the main set (holdout 2 was scored before I looked at it). v3 and v4 differ only by the grounding check.</p>
<table><tr><th>Run<th class=n>Dates right<th class=n>Line items right<th class=n>Right review decision</tr>{history_rows}</table></div>
"""


def doc_page(conn: sqlite3.Connection, model: str, file: str) -> str:
    row = conn.execute("SELECT * FROM documents WHERE model = ? AND source_file = ?", (model, file)).fetchone()
    if row is None:
        return "<p>Not found.</p>"
    items = conn.execute("SELECT * FROM line_items WHERE document_id = ? ORDER BY position", (row["id"],)).fetchall()
    item_rows = "".join(
        f'<tr><td>{escape(i["description"] or "")}</td><td class=n>{i["quantity"] or ""}</td>'
        f'<td class=n>{money(i["unit_price"])}</td><td class=n>{money(i["amount"])}</td>'
        f'<td class=n>{i["weight_lb"] or ""}</td></tr>' for i in items)
    return f"""
<p><a href="/?model={escape(model)}">&larr; back</a></p>
<h1>{escape(file)} <span class="pill {row['status']}">{row['status']}</span></h1>
<p class=sub>{escape(row['flags'] or 'No flags.')}</p>
<div class=card><h2>What the model extracted</h2>
<p>{escape(str(row['doc_type']))} {escape(str(row['doc_number']))} from {escape(str(row['counterparty']))} on {escape(str(row['doc_date']))}
 &middot; total {money(row['total'])}</p>
<table><tr><th>Description<th class=n>Qty<th class=n>Price<th class=n>Amount<th class=n>Weight</tr>{item_rows}</table></div>
<div class=card><h2>Text read from the PDF</h2><pre>{escape(row['raw_text'] or '')}</pre></div>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path not in ("/", "/doc"):
            self.send_error(404)
            return
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            body = (doc_page(conn, params.get("model", ""), params.get("file", ""))
                    if url.path == "/doc"
                    else page(conn, params.get("model", ""), params.get("q", "").strip()[:300]))
        finally:
            conn.close()
        html = (f'<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
                f'<title>Document intake</title><style>{CSS}</style><main>{body}</main>')
        data = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if not DB_PATH.exists():
        sys.exit("No database yet. Run: python -m docintake.pipeline")
    db.connect(DB_PATH).close()  # bring the views up to date; every page after this is read-only
    print(f"Dashboard at http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
