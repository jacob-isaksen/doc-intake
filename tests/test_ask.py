import json
import sqlite3

import pytest

from docintake import db
from docintake.ask import UnsafeQuery, ask, check_sql, sandbox


class SqlBackend:
    name = "fake"

    def __init__(self, sql):
        self.sql = sql

    def complete_json(self, system, user, schema):
        return json.dumps({"sql": self.sql})


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    for n, (vendor, total, status) in enumerate([("Acme", 100.0, "ok"), ("Acme", 50.0, "ok"), ("Birch", 400.0, "flagged")]):
        db.save_document(c, model="m", source_file=f"{n}.pdf", status=status,
                         flags=[] if status == "ok" else ["x"], raw_text="t",
                         doc=_doc(vendor, total, n))
    return c


def _doc(vendor, total, n):
    from docintake.schema import ExtractedDoc
    return ExtractedDoc(doc_type="invoice", doc_number=f"I{n}", counterparty=vendor, line_items=[], total=total)


def test_answers_a_question_with_a_select(conn):
    sql, rows = ask(conn, "top vendor?", SqlBackend("SELECT counterparty, confirmed_spend FROM spend_by_vendor ORDER BY confirmed_spend DESC"), "m")
    assert rows[0]["counterparty"] == "Acme" and rows[0]["confirmed_spend"] == 150.0


@pytest.mark.parametrize("sql", [
    "DELETE FROM documents",
    "DROP TABLE documents",
    "SELECT 1; DROP TABLE documents",
    "UPDATE documents SET total = 0",
    "SELECT 1 -- sneaky",
    "PRAGMA query_only = OFF",
])
def test_unsafe_sql_is_rejected_before_running(conn, sql):
    with pytest.raises(UnsafeQuery):
        check_sql(sql)


def test_database_refuses_writes_even_if_the_text_check_is_bypassed(conn):
    sandbox(conn, "m")
    with pytest.raises(sqlite3.Error):
        conn.execute("DELETE FROM documents")
    with pytest.raises(sqlite3.Error):
        conn.execute("CREATE TABLE evil (x)")
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3


def test_a_select_cannot_smuggle_in_a_write(conn):
    # a WITH ... DELETE is not a valid single SELECT; SQLite rejects it at prepare time
    with pytest.raises(sqlite3.Error):
        ask(conn, "x", SqlBackend("WITH t AS (SELECT 1) DELETE FROM documents"), "m")
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3


def test_works_through_a_read_only_connection_like_the_dashboard_uses(tmp_path):
    path = tmp_path / "x.db"
    c = db.connect(path)
    db.save_document(c, model="m", source_file="a.pdf", status="ok", flags=[], raw_text="t", doc=_doc("Acme", 100.0, 1))
    c.close()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    sql, rows = ask(ro, "top vendor?", SqlBackend("SELECT counterparty, confirmed_spend FROM spend_by_vendor"), "m")
    assert rows[0]["counterparty"] == "Acme"
    with pytest.raises(sqlite3.Error):
        ro.execute("DELETE FROM documents")
