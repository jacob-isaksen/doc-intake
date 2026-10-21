"""Pipeline behavior with a fake backend, so no model or network is needed."""
import json
from pathlib import Path

import pytest

from docintake import db
from docintake.evaluate import evaluate
from docintake.extract import ExtractionError, extract
from docintake.pipeline import process_file

GOOD = {
    "doc_type": "invoice", "doc_number": "INV-1", "doc_date": "2026-07-01",
    "counterparty": "Acme Saw",
    "line_items": [{"description": "Blade", "quantity": 2, "unit_price": 100.0, "amount": 200.0}],
    "subtotal": 200.0, "tax": 0.0, "total": 200.0,
}


DOCUMENT_TEXT = """Acme Saw
Invoice No.: INV-1
Date: 2026-07-01
Blade 2 $100.00 $200.00
Subtotal $200.00
Total $200.00"""


class FakeBackend:
    def __init__(self, *answers):
        self.name = "fake"
        self.answers = list(answers)
        self.calls = 0
        self.users = []

    def complete_json(self, system, user, schema):
        self.users.append(user)
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return answer if isinstance(answer, str) else json.dumps(answer)


@pytest.fixture
def conn():
    return db.connect(":memory:")


@pytest.fixture
def pdf_with_text(tmp_path, monkeypatch):
    """Skip real PDF parsing: pretend every file contains some text."""
    monkeypatch.setattr("docintake.pipeline.read_pdf_text", lambda path: DOCUMENT_TEXT)
    return tmp_path / "a.pdf"


def test_good_answer_is_saved_as_ok(conn, pdf_with_text):
    assert process_file(pdf_with_text, FakeBackend(GOOD), conn) == "ok"
    row = conn.execute("SELECT * FROM documents").fetchone()
    assert row["counterparty"] == "Acme Saw" and row["total"] == 200.0
    assert conn.execute("SELECT COUNT(*) FROM line_items").fetchone()[0] == 1


def test_bad_math_is_flagged_not_dropped(conn, pdf_with_text):
    assert process_file(pdf_with_text, FakeBackend({**GOOD, "total": 999.0}), conn) == "flagged"
    assert "total does not equal" in conn.execute("SELECT flags FROM documents").fetchone()["flags"]


def test_garbage_is_marked_failed_after_one_retry(conn, pdf_with_text):
    backend = FakeBackend("not json at all")
    assert process_file(pdf_with_text, backend, conn) == "failed"
    assert backend.calls == 3
    assert conn.execute("SELECT COUNT(*) FROM line_items").fetchone()[0] == 0


def test_retry_recovers_from_a_bad_first_answer(conn, pdf_with_text):
    assert process_file(pdf_with_text, FakeBackend("oops", GOOD), conn) == "ok"


def test_wrong_type_is_rejected_by_schema():  # noqa: E302
    with pytest.raises(ExtractionError):
        extract("text", FakeBackend({**GOOD, "total": "lots of money"}))


def test_unknown_document_type_is_rejected():
    with pytest.raises(ExtractionError):
        extract("text", FakeBackend({**GOOD, "doc_type": "receipt"}))


def test_empty_pdf_is_failed_without_calling_the_model(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("docintake.pipeline.read_pdf_text", lambda path: "")
    backend = FakeBackend(GOOD)
    assert process_file(tmp_path / "scan.pdf", backend, conn) == "failed"
    assert backend.calls == 0


def test_resent_document_is_flagged_as_duplicate(conn, pdf_with_text, tmp_path):
    assert process_file(tmp_path / "first.pdf", FakeBackend(GOOD), conn) == "ok"
    assert process_file(tmp_path / "second.pdf", FakeBackend(GOOD), conn) == "flagged"
    flags = conn.execute("SELECT flags FROM documents WHERE source_file = 'second.pdf'").fetchone()["flags"]
    assert "duplicate" in flags


def test_views_only_count_clean_spend_as_confirmed(conn, pdf_with_text, tmp_path):
    process_file(tmp_path / "a.pdf", FakeBackend(GOOD), conn)
    process_file(tmp_path / "b.pdf", FakeBackend({**GOOD, "doc_number": "INV-2", "total": 500.0}), conn)
    row = conn.execute("SELECT * FROM v_spend_by_vendor").fetchone()
    assert row["confirmed_spend"] == 200.0 and row["unreviewed_spend"] == 500.0


def test_evaluation_counts_a_silently_corrected_defect_as_a_miss(conn, tmp_path):
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir()
    expected = {**GOOD, "equipment": None, "total_weight_lb": None,
                "line_items": [{**GOOD["line_items"][0], "weight_lb": None}], "total": 999.0}
    (truth_dir / "a.json").write_text(json.dumps({"expected": expected, "expected_status": "flagged"}))
    # the model "fixed" the bad printed total to 200.0, so nothing was flagged
    db.save_document(conn, model="fake", source_file="a.pdf", status="ok", flags=[], raw_text="",
                     doc=extract("t", FakeBackend(GOOD), ground=False)[0])
    summary = evaluate(conn, "fake", truth_dir)
    assert summary["defective"] == 1 and summary["caught"] == 0
    total = conn.execute("SELECT correct FROM eval_results WHERE field = 'total'").fetchone()["correct"]
    assert total == 0


def test_all_caps_vendor_is_grouped_with_its_normal_spelling(conn, pdf_with_text, tmp_path):
    process_file(tmp_path / "a.pdf", FakeBackend({**GOOD, "counterparty": "Acme Saw"}), conn)
    process_file(tmp_path / "b.pdf", FakeBackend({**GOOD, "doc_number": "INV-2", "counterparty": "ACME SAW"}), conn)
    process_file(tmp_path / "c.pdf", FakeBackend({**GOOD, "doc_number": "INV-3", "counterparty": "BIRCH SUPPLY"}), conn)
    names = [r[0] for r in conn.execute("SELECT counterparty FROM documents ORDER BY id")]
    assert names == ["Acme Saw", "Acme Saw", "Birch Supply"]
