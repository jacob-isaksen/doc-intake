"""SQLite storage plus the reporting views the dashboard reads."""
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "intake.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    model         TEXT NOT NULL,
    source_file   TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('ok', 'flagged', 'failed')),
    flags         TEXT NOT NULL DEFAULT '',
    doc_type      TEXT,
    doc_number    TEXT,
    doc_date      TEXT,
    counterparty  TEXT,
    equipment     TEXT,
    subtotal      REAL,
    tax           REAL,
    total         REAL,
    total_weight_lb REAL,
    raw_text      TEXT,
    extracted_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (model, source_file)
);

CREATE TABLE IF NOT EXISTS line_items (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    description TEXT,
    quantity    REAL,
    unit_price  REAL,
    amount      REAL,
    weight_lb   REAL
);

CREATE TABLE IF NOT EXISTS eval_results (
    id          INTEGER PRIMARY KEY,
    model       TEXT NOT NULL,
    source_file TEXT NOT NULL,
    field       TEXT NOT NULL,
    expected    TEXT,
    actual      TEXT,
    correct     INTEGER NOT NULL,
    UNIQUE (model, source_file, field)
);

CREATE INDEX IF NOT EXISTS idx_documents_lookup ON documents (model, doc_type, doc_number, counterparty);

-- Money only counts as confirmed when the document passed every check.
CREATE VIEW IF NOT EXISTS v_spend_by_vendor AS
SELECT model,
       counterparty,
       COUNT(*)                                                         AS documents,
       ROUND(SUM(CASE WHEN status = 'ok'      THEN total ELSE 0 END), 2) AS confirmed_spend,
       ROUND(SUM(CASE WHEN status = 'flagged' THEN total ELSE 0 END), 2) AS unreviewed_spend
FROM documents
WHERE doc_type IN ('invoice', 'work_order') AND status IN ('ok', 'flagged')
GROUP BY model, counterparty;

CREATE VIEW IF NOT EXISTS v_monthly_spend AS
SELECT model,
       strftime('%Y-%m', doc_date) AS month,
       doc_type,
       ROUND(SUM(total), 2)        AS spend,
       COUNT(*)                    AS documents
FROM documents
WHERE doc_type IN ('invoice', 'work_order') AND status = 'ok' AND doc_date IS NOT NULL
GROUP BY model, month, doc_type;

CREATE VIEW IF NOT EXISTS v_equipment_cost AS
SELECT model,
       equipment,
       COUNT(*)             AS work_orders,
       ROUND(SUM(total), 2) AS repair_cost
FROM documents
WHERE doc_type = 'work_order' AND equipment IS NOT NULL AND status = 'ok'
GROUP BY model, equipment;

CREATE VIEW IF NOT EXISTS v_shipments_by_customer AS
SELECT model,
       counterparty,
       COUNT(*)                    AS shipments,
       ROUND(SUM(total_weight_lb)) AS total_weight_lb
FROM documents
WHERE doc_type = 'bill_of_lading' AND status = 'ok'
GROUP BY model, counterparty;

CREATE VIEW IF NOT EXISTS v_status_summary AS
SELECT model, doc_type, status, COUNT(*) AS documents
FROM documents
GROUP BY model, doc_type, status;

CREATE VIEW IF NOT EXISTS v_review_queue AS
SELECT model, source_file, status, flags, doc_type, doc_number, counterparty, doc_date, total
FROM documents
WHERE status != 'ok'
ORDER BY status DESC, source_file;

CREATE VIEW IF NOT EXISTS v_run_history AS
SELECT model,
       MAX(CASE WHEN field = 'doc_date'        THEN percent_correct END) AS dates_correct,
       MAX(CASE WHEN field = 'line_items'      THEN percent_correct END) AS line_items_correct,
       MAX(CASE WHEN field = 'review_decision' THEN percent_correct END) AS review_decision_correct
FROM v_accuracy_by_field
GROUP BY model;

CREATE VIEW IF NOT EXISTS v_accuracy_by_field AS
SELECT model,
       field,
       COUNT(*)                                   AS documents,
       SUM(correct)                               AS correct,
       ROUND(100.0 * SUM(correct) / COUNT(*), 1)  AS percent_correct
FROM eval_results
GROUP BY model, field;
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def canonical_counterparty(conn: sqlite3.Connection, model: str, name: str) -> str:
    """One spelling per company, so "KOOTENAI SAFETY SUPPLY" and "Kootenai Safety Supply" group together.

    Reuses the spelling already stored for this run when only the case differs.
    Otherwise an all-caps header (some invoice layouts print the vendor that way) is title-cased.
    """
    name = " ".join(name.split())
    row = conn.execute(
        "SELECT counterparty FROM documents WHERE model = ? AND lower(counterparty) = lower(?) "
        "ORDER BY counterparty = upper(counterparty), id LIMIT 1", (model, name)).fetchone()
    if row:
        return row["counterparty"]
    return name.title() if name.isupper() else name


def is_duplicate(conn: sqlite3.Connection, model: str, source_file: str, doc_type: str,
                 doc_number: str, counterparty: str) -> bool:
    """True if another file in this run already holds the same document."""
    row = conn.execute(
        "SELECT 1 FROM documents WHERE model = ? AND source_file != ? AND doc_type = ? "
        "AND doc_number = ? AND counterparty = ? LIMIT 1",
        (model, source_file, doc_type, doc_number, counterparty),
    ).fetchone()
    return row is not None


def save_document(conn: sqlite3.Connection, *, model: str, source_file: str, status: str,
                  flags: list[str], raw_text: str, doc=None) -> None:
    conn.execute("DELETE FROM documents WHERE model = ? AND source_file = ?", (model, source_file))
    values = dict(model=model, source_file=source_file, status=status,
                  flags="; ".join(flags), raw_text=raw_text)
    if doc is not None:
        values.update(
            doc_type=doc.doc_type, doc_number=doc.doc_number,
            doc_date=doc.doc_date.isoformat() if doc.doc_date else None,
            counterparty=doc.counterparty, equipment=doc.equipment,
            subtotal=doc.subtotal, tax=doc.tax, total=doc.total,
            total_weight_lb=doc.total_weight_lb,
        )
    columns = ", ".join(values)
    marks = ", ".join(f":{k}" for k in values)
    cursor = conn.execute(f"INSERT INTO documents ({columns}) VALUES ({marks})", values)
    if doc is not None:
        conn.executemany(
            "INSERT INTO line_items (document_id, position, description, quantity, unit_price, "
            "amount, weight_lb) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(cursor.lastrowid, n, i.description, i.quantity, i.unit_price, i.amount, i.weight_lb)
             for n, i in enumerate(doc.line_items, start=1)],
        )
    conn.commit()
