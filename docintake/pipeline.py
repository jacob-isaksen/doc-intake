"""Run every PDF in a folder through extraction, validation, and storage.

    python -m docintake.pipeline                 # all PDFs in data/docs, using Ollama
    python -m docintake.pipeline --backend gemini
"""
import argparse
import sqlite3
import time
from pathlib import Path

from . import db
from .backends import Backend, GeminiBackend, OllamaBackend
from .extract import ExtractionError, extract, read_pdf_text
from .validate import check

DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"


def process_file(path: Path, backend: Backend, conn: sqlite3.Connection, ground: bool = True) -> str:
    """Process one PDF and return its status: ok, flagged, or failed."""
    text = read_pdf_text(path)
    if not text:
        db.save_document(conn, model=backend.name, source_file=path.name, status="failed",
                         flags=["no text layer (scanned image?)"], raw_text="")
        return "failed"

    try:
        doc, ungrounded = extract(text, backend, ground=ground)
    except ExtractionError as error:
        db.save_document(conn, model=backend.name, source_file=path.name, status="failed",
                         flags=[f"model output rejected: {error}"], raw_text=text)
        return "failed"

    doc = doc.model_copy(update={"counterparty": db.canonical_counterparty(conn, backend.name, doc.counterparty)})
    flags = check(doc) + [f"not found in the document: {item}" for item in ungrounded]
    if db.is_duplicate(conn, backend.name, path.name, doc.doc_type, doc.doc_number, doc.counterparty):
        flags.append("possible duplicate of an earlier document")
    status = "flagged" if flags else "ok"
    db.save_document(conn, model=backend.name, source_file=path.name, status=status,
                     flags=flags, raw_text=text, doc=doc)
    return status


def run(docs_dir: Path, backend: Backend, conn: sqlite3.Connection, ground: bool = True) -> dict[str, int]:
    counts = {"ok": 0, "flagged": 0, "failed": 0}
    files = sorted(docs_dir.glob("*.pdf"))
    for n, path in enumerate(files, start=1):
        started = time.time()
        status = process_file(path, backend, conn, ground)
        counts[status] += 1
        print(f"[{n:>2}/{len(files)}] {path.name:<28} {status:<8} {time.time() - started:4.1f}s")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["ollama", "gemini"], default="ollama")
    parser.add_argument("--model", help="override the backend's default model")
    parser.add_argument("--label", help="tag appended to the model name, e.g. 'prompt v2', to compare runs")
    parser.add_argument("--no-grounding", action="store_true", help="skip the grounding check (how v1 to v3 ran)")
    parser.add_argument("--docs", type=Path, default=DOCS_DIR)
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB)
    args = parser.parse_args()

    backend_class = {"ollama": OllamaBackend, "gemini": GeminiBackend}[args.backend]
    backend = backend_class(args.model) if args.model else backend_class()
    if args.label:
        backend.name = f"{backend.name} [{args.label}]"
    conn = db.connect(args.db)
    counts = run(args.docs, backend, conn, ground=not args.no_grounding)
    print(f"\n{backend.name}: {counts}")


if __name__ == "__main__":
    main()
