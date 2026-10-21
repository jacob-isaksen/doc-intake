"""Ask a plain-English question about the extracted data.

    python -m docintake.ask "Which vendor did we spend the most with?"

The LLM writes a SQL query, and we run it. Model-written SQL is untrusted input,
so three independent guards stand between it and the database:

1. The query must be one SELECT statement (checked before it runs).
2. The connection is set to query_only, so SQLite itself refuses any write.
3. A SQLite authorizer rejects anything that is not reading data.

The model only sees the reporting views (for one chosen model run), not raw tables.
"""
import argparse
import json
import re
import sqlite3
from pathlib import Path

from . import db
from .backends import Backend, OllamaBackend

VIEWS = {
    "spend_by_vendor": ("v_spend_by_vendor", "counterparty, documents, confirmed_spend, unreviewed_spend"),
    "monthly_spend": ("v_monthly_spend", "month (YYYY-MM), doc_type, spend, documents"),
    "equipment_cost": ("v_equipment_cost", "equipment, work_orders, repair_cost"),
    "shipments_by_customer": ("v_shipments_by_customer", "counterparty, shipments, total_weight_lb"),
    "review_queue": ("v_review_queue", "source_file, status, flags, doc_type, doc_number, counterparty, doc_date, total"),
}
ROW_LIMIT = 50

SYSTEM_PROMPT = (
    "You write SQLite queries for a lumber mill's document database. "
    'Answer with JSON: {"sql": "<one SELECT statement>"}. '
    "Use only these views and columns:\n"
    + "\n".join(f"- {name}({columns})" for name, (_, columns) in VIEWS.items())
    + "\nWrite a single SELECT. No comments. No semicolons. Money is in dollars."
)


class UnsafeQuery(Exception):
    pass


def check_sql(sql: str) -> str:
    sql = sql.strip().rstrip(";").strip()
    if ";" in sql or "--" in sql or "/*" in sql:
        raise UnsafeQuery("only a single plain statement is allowed")
    if not re.match(r"(?is)^\s*(select|with)\b", sql):
        raise UnsafeQuery("only SELECT queries are allowed")
    return sql


def _allow_reads_only(action, *_):
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def sandbox(conn: sqlite3.Connection, model: str) -> sqlite3.Connection:
    """Give the model friendly per-run views, then lock the connection down."""
    quoted = model.replace("'", "''")
    for friendly, (real, _) in VIEWS.items():
        conn.execute(f"DROP VIEW IF EXISTS temp.{friendly}")
        conn.execute(f"CREATE TEMP VIEW {friendly} AS SELECT * FROM {real} WHERE model = '{quoted}'")
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(_allow_reads_only)
    return conn


def ask(conn: sqlite3.Connection, question: str, backend: Backend, model: str):
    """Return (sql, rows). Raises UnsafeQuery or sqlite3.Error if the model's SQL is bad."""
    schema = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}
    sql = check_sql(json.loads(backend.complete_json(SYSTEM_PROMPT, question, schema))["sql"])
    sandbox(conn, model)
    rows = conn.execute(sql).fetchmany(ROW_LIMIT)
    return sql, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--model", required=True, help="which stored run to ask about, e.g. 'ollama:qwen2.5:7b [v2 required fields]'")
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB)
    args = parser.parse_args()

    conn = db.connect(args.db)
    try:
        sql, rows = ask(conn, args.question, OllamaBackend(), args.model)
    except (UnsafeQuery, sqlite3.Error, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"Could not answer that safely: {error}")
    print(f"SQL the model wrote:\n  {sql}\n")
    for row in rows:
        print("  " + " | ".join(str(value) for value in tuple(row)))
    if not rows:
        print("  (no rows)")


if __name__ == "__main__":
    main()
