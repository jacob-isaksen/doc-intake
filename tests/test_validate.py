from datetime import date

from docintake.schema import ExtractedDoc, LineItem
from docintake.validate import check


def invoice(**overrides) -> ExtractedDoc:
    base = dict(
        doc_type="invoice", doc_number="INV-1", doc_date=date(2026, 7, 1), counterparty="Acme Saw",
        line_items=[
            LineItem(description="Blade", quantity=2, unit_price=100.0, amount=200.0),
            LineItem(description="Oil", quantity=1, unit_price=50.0, amount=50.0),
        ],
        subtotal=250.0, tax=15.0, total=265.0,
    )
    base.update(overrides)
    return ExtractedDoc(**base)


def test_clean_invoice_has_no_flags():
    assert check(invoice()) == []


def test_total_that_does_not_add_up_is_flagged():
    assert "total does not equal subtotal plus tax" in check(invoice(total=300.0))


def test_subtotal_that_does_not_match_lines_is_flagged():
    assert "subtotal does not equal sum of lines" in check(invoice(subtotal=260.0, total=275.0))


def test_line_math_is_checked():
    items = [LineItem(description="Blade", quantity=2, unit_price=100.0, amount=230.0)]
    flags = check(invoice(line_items=items, subtotal=230.0, tax=0.0, total=230.0))
    assert flags == ["line 1: quantity x price does not equal amount"]


def test_missing_date_is_flagged():
    assert "missing date" in check(invoice(doc_date=None))


def test_absurd_date_is_flagged():
    assert any("out of range" in f for f in check(invoice(doc_date=date(1999, 1, 1))))


def test_missing_total_is_flagged():
    assert "missing total" in check(invoice(total=None))


def test_one_cent_rounding_is_tolerated():
    assert check(invoice(total=265.01)) == []


def test_bill_of_lading_weight_check():
    items = [LineItem(description="2x4", quantity=2, weight_lb=1000.0),
             LineItem(description="2x6", quantity=1, weight_lb=500.0)]
    good = ExtractedDoc(doc_type="bill_of_lading", doc_number="B1", doc_date=date(2026, 7, 1),
                        counterparty="Mill Co", line_items=items, total_weight_lb=1500.0)
    bad = good.model_copy(update={"total_weight_lb": 1700.0})
    assert check(good) == []
    assert check(bad) == ["total weight does not equal sum of line weights"]


def test_our_own_company_as_counterparty_is_flagged():
    flags = check(invoice(counterparty="Northern Pine Lumber Co."))
    assert "counterparty is our own company" in flags
