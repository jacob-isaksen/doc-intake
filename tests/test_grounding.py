from datetime import date

from docintake.extract import extract
from docintake.grounding import find_ungrounded
from docintake.schema import ExtractedDoc, LineItem
from tests.test_pipeline import DOCUMENT_TEXT, GOOD, FakeBackend


def doc(**overrides) -> ExtractedDoc:
    base = dict(doc_type="invoice", doc_number="INV-1", doc_date=date(2026, 7, 1), counterparty="Acme Saw",
                line_items=[LineItem(description="Blade", quantity=2, unit_price=100.0, amount=200.0)],
                subtotal=200.0, tax=0.0, total=200.0)
    base.update(overrides)
    return ExtractedDoc(**base)


def test_values_that_are_in_the_text_are_grounded():
    assert find_ungrounded(doc(), DOCUMENT_TEXT) == []


def test_a_misread_digit_is_found_out():
    bad = doc(line_items=[LineItem(description="Blade", quantity=2, unit_price=100.0, amount=208.0)])
    assert find_ungrounded(bad, DOCUMENT_TEXT) == ["line 1 amount 208"]


def test_an_invented_date_is_found_out():
    assert find_ungrounded(doc(doc_date=date(2023, 11, 15)), DOCUMENT_TEXT) == ["date 2023-11-15"]


def test_a_date_is_found_in_any_common_spelling():
    for printed in ("Jul 1, 2026", "Jul 01, 2026", "07/01/2026", "7/1/2026"):
        text = f"Acme Saw INV-1 {printed} Blade 2 $100.00 $200.00"
        assert find_ungrounded(doc(), text) == []


def test_company_names_ignore_case_and_trailing_punctuation():
    assert find_ungrounded(doc(counterparty="ACME SAW INC."), "ACME SAW, INC Invoice INV-1 2026-07-01 Blade 2 $100.00 $200.00") == []


def test_numbers_with_thousands_separators_are_matched():
    text = "Acme Saw INV-1 2026-07-01 Blade 2 $1,500.00 $3,000.00 Total $3,000.00"
    items = [LineItem(description="Blade", quantity=2, unit_price=1500.0, amount=3000.0)]
    assert find_ungrounded(doc(line_items=items, subtotal=3000.0, total=3000.0), text) == []


def test_retry_points_the_model_at_what_was_wrong_and_takes_the_better_answer():
    wrong = {**GOOD, "total": 208.0, "subtotal": 208.0,
             "line_items": [{"description": "Blade", "quantity": 2, "unit_price": 100.0, "amount": 208.0}]}
    backend = FakeBackend(wrong, GOOD)
    result, issues = extract(DOCUMENT_TEXT, backend)
    assert result.total == 200.0 and issues == []
    assert backend.calls == 2
    assert "does not appear in the document" in backend.users[1] and "208" in backend.users[1]


def test_if_the_model_cannot_fix_it_the_ungrounded_values_are_reported():
    wrong = {**GOOD, "doc_date": "2023-11-15"}
    result, issues = extract(DOCUMENT_TEXT, FakeBackend(wrong))
    assert issues == ["date 2023-11-15"]


def test_a_clean_answer_costs_only_one_model_call():
    backend = FakeBackend(GOOD)
    extract(DOCUMENT_TEXT, backend)
    assert backend.calls == 1


def test_the_company_cannot_be_its_own_counterparty():
    us = {**GOOD, "counterparty": "Northern Pine Lumber Co."}
    backend = FakeBackend(us, GOOD)
    result, _ = extract(DOCUMENT_TEXT + "\nNorthern Pine Lumber Co.", backend)
    assert result.counterparty == "Acme Saw" and "which is us" in backend.users[1]
