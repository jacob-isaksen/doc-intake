"""Score a model's extractions against the hand-checkable ground truth.

    python -m docintake.evaluate --model ollama:qwen2.5:7b

Each document is checked field by field. The report also answers the question
that matters most for a human-in-the-loop tool: of the documents that really
were defective, how many did the system flag, and how many clean ones did it
flag by mistake?
"""
import argparse
import json
import sqlite3
from pathlib import Path

from . import db

TRUTH_DIR = Path(__file__).resolve().parent.parent / "data" / "truth"
MONEY_TOLERANCE = 0.005


def _same_number(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= MONEY_TOLERANCE


def _norm(text) -> str:
    """Case, spacing and trailing punctuation don't count as errors ("Inc" == "Inc.")."""
    return " ".join(str(text or "").casefold().replace(".", "").replace(",", "").split())


def _lines_match(expected: list[dict], actual: list[sqlite3.Row]) -> bool:
    if len(expected) != len(actual):
        return False
    for e, a in zip(expected, actual):
        if _norm(e["description"]) != _norm(a["description"]):
            return False
        for key in ("quantity", "unit_price", "amount", "weight_lb"):
            if not _same_number(e.get(key), a[key]):
                return False
    return True


def compare(truth: dict, row: sqlite3.Row | None, lines: list[sqlite3.Row]) -> dict[str, tuple]:
    """Return {field: (expected, actual, correct)} for one document."""
    exp = truth["expected"]
    failed = row is None or row["status"] == "failed"
    results = {}
    for field in ("doc_type", "doc_number", "doc_date", "equipment"):
        actual = None if failed else row[field]
        results[field] = (exp[field], actual, not failed and _norm(exp[field]) == _norm(actual))
    actual = None if failed else row["counterparty"]
    results["counterparty"] = (exp["counterparty"], actual, not failed and _norm(exp["counterparty"]) == _norm(actual))
    for field in ("subtotal", "tax", "total", "total_weight_lb"):
        actual = None if failed else row[field]
        results[field] = (exp[field], actual, not failed and _same_number(exp[field], actual))
    ok = not failed and _lines_match(exp["line_items"], lines)
    results["line_items"] = (f"{len(exp['line_items'])} lines", f"{len(lines)} lines", ok)
    actual_status = "failed" if row is None else row["status"]
    # A "failed" document is not silently wrong, it is routed to a human, so it counts as caught.
    caught = actual_status in ("flagged", "failed") if truth["expected_status"] == "flagged" else actual_status == "ok"
    results["review_decision"] = (truth["expected_status"], actual_status, caught)
    return results


def evaluate(conn: sqlite3.Connection, model: str, truth_dir: Path = TRUTH_DIR) -> dict:
    conn.execute("DELETE FROM eval_results WHERE model = ?", (model,))
    defective = caught = clean = false_alarms = 0
    for truth_path in sorted(truth_dir.glob("*.json")):
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        source = truth_path.stem + ".pdf"
        row = conn.execute("SELECT * FROM documents WHERE model = ? AND source_file = ?", (model, source)).fetchone()
        lines = []
        if row is not None:
            lines = conn.execute("SELECT * FROM line_items WHERE document_id = ? ORDER BY position", (row["id"],)).fetchall()
        for field, (expected, actual, correct) in compare(truth, row, lines).items():
            conn.execute(
                "INSERT INTO eval_results (model, source_file, field, expected, actual, correct) VALUES (?, ?, ?, ?, ?, ?)",
                (model, source, field, str(expected), str(actual), int(correct)))
        decision = conn.execute(
            "SELECT correct FROM eval_results WHERE model = ? AND source_file = ? AND field = 'review_decision'",
            (model, source)).fetchone()["correct"]
        if truth["expected_status"] == "flagged":
            defective += 1
            caught += decision
        else:
            clean += 1
            false_alarms += 1 - decision
    conn.commit()
    return {"defective": defective, "caught": caught, "clean": clean, "false_alarms": false_alarms}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="backend name as stored, e.g. ollama:qwen2.5:7b")
    parser.add_argument("--truth", type=Path, default=TRUTH_DIR, help="folder of answer-key JSON files")
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB)
    args = parser.parse_args()

    conn = db.connect(args.db)
    summary = evaluate(conn, args.model, args.truth)
    print(f"\nField accuracy for {args.model}\n")
    for row in conn.execute("SELECT field, correct, documents, percent_correct FROM v_accuracy_by_field "
                            "WHERE model = ? ORDER BY percent_correct, field", (args.model,)):
        print(f"  {row['field']:<16} {row['correct']:>2}/{row['documents']:<2}  {row['percent_correct']:>5.1f}%")
    print(f"\nDefective documents routed to a human: {summary['caught']}/{summary['defective']}")
    print(f"Clean documents flagged by mistake:    {summary['false_alarms']}/{summary['clean']}")


if __name__ == "__main__":
    main()
